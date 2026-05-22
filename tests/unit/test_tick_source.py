"""Tests for the tick-source plumbing in :mod:`scanner.tick_source`.

Phase 2 of the live-tick slice. Validates:

* :class:`PollingTickSource` calls ``fetch_fn`` at the configured
  cadence on a daemon thread and dispatches ticks to subscribers.
* ``start`` / ``stop`` are idempotent; ``stop`` joins cleanly.
* Subscriber exceptions are isolated — the source thread continues.
* :class:`QueuedTickSource` buffers ticks across the thread boundary
  in FIFO order; bounded queues drop oldest on overflow.
* End-to-end: PollingTickSource → QueuedTickSource → ScanRunner.run()
  produces sane results from a fake fetch.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import List

import tradinglab.indicators  # noqa: F401
from tradinglab.models import Candle
from tradinglab.scanner.model import (
    OP_GT,
    Condition,
    FieldRef,
    Group,
    ScanDefinition,
    UniverseFilter,
)
from tradinglab.scanner.runner import ScanRunner
from tradinglab.scanner.tick_source import (
    PollingTickSource,
    QueuedTickSource,
    Tick,
)


def _candles(n: int, *, start_close: float = 100.0) -> list[Candle]:
    base = datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc)
    out: list[Candle] = []
    for i in range(n):
        c = start_close + i
        out.append(Candle(
            date=base + timedelta(minutes=i),
            open=c - 0.5, high=c + 1.0, low=c - 1.0, close=c,
            volume=1000.0 + i, session="regular",
        ))
    return out


# -- PollingTickSource --------------------------------------------------------


def test_polling_calls_fetch_fn_and_dispatches():
    calls = {"n": 0}
    def fetch(symbols):
        calls["n"] += 1
        return {sym: _candles(3) for sym in symbols}

    received: list[Tick] = []
    src = PollingTickSource(fetch, ["AAA", "BBB"], interval_s=0.02)
    src.subscribe(received.append)
    src.start()
    try:
        # Wait for at least 2 ticks.
        deadline = time.time() + 2.0
        while len(received) < 2 and time.time() < deadline:
            time.sleep(0.01)
    finally:
        src.stop()

    assert len(received) >= 2
    assert calls["n"] >= 2
    # Tick payload sanity.
    t = received[0]
    assert t.tick_id == 1
    assert set(t.candles_by_symbol.keys()) == {"AAA", "BBB"}
    assert t.forming is False
    # Monotonic tick IDs.
    ids = [t.tick_id for t in received]
    assert ids == sorted(ids) == list(range(1, len(received) + 1))


def test_polling_start_stop_idempotent():
    src = PollingTickSource(lambda syms: {}, ["AAA"], interval_s=0.05)
    src.start()
    src.start()  # second start must be no-op, not crash
    src.stop()
    src.stop()  # second stop must be no-op
    # Restart is allowed.
    src.start()
    src.stop()


def test_polling_isolates_subscriber_exception():
    """A throwing subscriber must not kill the source thread."""
    received: list[Tick] = []
    def bad(_t: Tick) -> None:
        raise RuntimeError("boom")

    def fetch(symbols):
        return {sym: _candles(2) for sym in symbols}

    src = PollingTickSource(fetch, ["AAA"], interval_s=0.02)
    src.subscribe(bad)
    src.subscribe(received.append)
    src.start()
    try:
        deadline = time.time() + 1.5
        while len(received) < 3 and time.time() < deadline:
            time.sleep(0.01)
    finally:
        src.stop()
    assert len(received) >= 3, f"good subscriber should still get ticks; got {len(received)}"


def test_polling_isolates_fetch_exception():
    """fetch_fn raising on one tick must not kill the loop; later ticks succeed."""
    state = {"calls": 0}
    def fetch(symbols):
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("first call boom")
        return {sym: _candles(2) for sym in symbols}

    received: list[Tick] = []
    src = PollingTickSource(fetch, ["AAA"], interval_s=0.02)
    src.subscribe(received.append)
    src.start()
    try:
        deadline = time.time() + 1.5
        while len(received) < 2 and time.time() < deadline:
            time.sleep(0.01)
    finally:
        src.stop()
    # First call raised → no tick dispatched. Subsequent calls dispatch normally.
    assert len(received) >= 2
    assert state["calls"] >= 3


def test_polling_latest_candles_snapshot():
    """``latest_candles_by_symbol`` reflects the most recent successful fetch."""
    def fetch(symbols):
        return {sym: _candles(4) for sym in symbols}
    src = PollingTickSource(fetch, ["AAA"], interval_s=0.02)
    src.start()
    try:
        deadline = time.time() + 1.0
        while not src.latest_candles_by_symbol() and time.time() < deadline:
            time.sleep(0.01)
    finally:
        src.stop()
    snap = src.latest_candles_by_symbol()
    assert "AAA" in snap
    assert len(snap["AAA"]) == 4


# -- QueuedTickSource ---------------------------------------------------------


class _ManualSource:
    """Test double for TickSource — emit ticks on demand from caller's thread."""
    def __init__(self):
        self._subs = []
        self._latest = {}
        self.started = False
        self.stopped = False

    def start(self) -> None: self.started = True
    def stop(self) -> None: self.stopped = True
    def subscribe(self, cb): self._subs.append(cb)
    def latest_candles_by_symbol(self): return dict(self._latest)
    def emit(self, tick: Tick):
        self._latest = dict(tick.candles_by_symbol)
        for cb in self._subs:
            cb(tick)


def _mk_tick(tick_id: int) -> Tick:
    return Tick(
        tick_id=tick_id,
        candles_by_symbol={"AAA": _candles(3)},
        forming=False,
        timestamp=datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc),
    )


def test_queued_drain_all_returns_fifo():
    upstream = _ManualSource()
    q = QueuedTickSource(upstream)
    upstream.emit(_mk_tick(1))
    upstream.emit(_mk_tick(2))
    upstream.emit(_mk_tick(3))
    drained = q.drain_all()
    assert [t.tick_id for t in drained] == [1, 2, 3]
    # Empty after drain.
    assert q.drain_all() == []


def test_queued_drain_one_at_a_time():
    upstream = _ManualSource()
    q = QueuedTickSource(upstream)
    upstream.emit(_mk_tick(1))
    upstream.emit(_mk_tick(2))
    t1 = q.drain()
    t2 = q.drain()
    assert t1 is not None and t1.tick_id == 1
    assert t2 is not None and t2.tick_id == 2
    assert q.drain() is None


def test_queued_bounded_drops_oldest_on_overflow():
    upstream = _ManualSource()
    q = QueuedTickSource(upstream, maxsize=2)
    upstream.emit(_mk_tick(1))
    upstream.emit(_mk_tick(2))
    upstream.emit(_mk_tick(3))  # forces drop of tick 1
    drained = q.drain_all()
    assert [t.tick_id for t in drained] == [2, 3]
    assert q.dropped == 1


def test_queued_lifecycle_forwards_to_upstream():
    upstream = _ManualSource()
    q = QueuedTickSource(upstream)
    q.start()
    assert upstream.started
    q.stop()
    assert upstream.stopped


# -- end-to-end ---------------------------------------------------------------


def test_end_to_end_polling_to_runner():
    """PollingTickSource → QueuedTickSource → ScanRunner.run() pipeline."""
    def fetch(symbols):
        return {sym: _candles(5) for sym in symbols}

    src = PollingTickSource(fetch, ["AAA", "BBB"], interval_s=0.02)
    q = QueuedTickSource(src)
    runner = ScanRunner()
    scan = ScanDefinition(
        name="close_gt", primary_interval="1m",
        universe_filter=UniverseFilter.all(),
        root=Group(combinator="and", children=[
            Condition(left=FieldRef.builtin("close"), op=OP_GT,
                      params={"right": FieldRef.literal(102.0)},
                      interval="1m"),
        ]),
    )
    try:
        q.start()
        deadline = time.time() + 2.0
        ticks: list[Tick] = []
        while not ticks and time.time() < deadline:
            ticks = q.drain_all()
            if not ticks:
                time.sleep(0.01)
        assert ticks, "no ticks received from polling source"

        # Drive the runner from one tick.
        t = ticks[0]
        results = runner.run(
            scans=[scan],
            candles_by_symbol=t.candles_by_symbol,
            interval="1m",
            tick_id=t.tick_id,
            timestamp=t.timestamp,
            last_bar_forming=t.forming,
        )
        sr = results[scan.id]
        # Last close = 104, both symbols match.
        assert {r.symbol for r in sr.matched_rows()} == {"AAA", "BBB"}
        assert all(r.is_forming is False for r in sr.rows)
    finally:
        q.stop()
        runner.shutdown()
