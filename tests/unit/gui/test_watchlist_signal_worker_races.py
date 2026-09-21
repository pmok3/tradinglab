"""Watchlist signal worker races: snapshot-at-handoff + Tk-thread apply.

Regression suite for the P0 "watchlist worker races shared GUI/cache
data":

1. ``_signal_bars`` returned the LIVE ``_full_cache`` list to the signal
   evaluator running off-thread; the Tk-thread streaming path appends to
   that list in place, so the evaluator could observe torn state (bars
   from two different stream appends mixed in one evaluation) or raise
   ``IndexError``. It now returns ``list(bars)`` — the same
   snapshot-at-handoff the sandbox path already did with
   ``list(visible)``.
2. ``_compute_watchlist_signals`` mutated the shared
   ``_watchlist_snapshot`` (``setdefault`` + in-place ``_sig`` dict
   update) from the worker while the Tk thread reads it in
   ``_populate_watchlist_tab`` / ``_watchlist_cell_text``. It now posts
   ``("watchlist_signals", {sym: cells})`` on ``_worker_inbox``; the
   Tk-thread drain applies it atomically via ``_apply_watchlist_signals``.

The tests below capture the downstream order effects: a stream mutation
landing between worker start and result delivery must not corrupt the
delivered result, and the worker must never mutate the shared snapshot
in place.

See ``gui/watchlist_tab.spec.md`` and ``gui/polling.spec.md``.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Any

import pytest

from tradinglab.gui import polling as _polling
from tradinglab.gui.watchlist_tab import WatchlistTabMixin
from tradinglab.models import Candle
from tradinglab.watchlists.signals import ColumnValue


def _bars(state: int, n: int = 40) -> list[Candle]:
    """40 bars whose closes identify ``state``: ``1000*state + i``."""
    import datetime as _dt

    out = []
    base = _dt.datetime(2026, 6, 10, 14, 0, tzinfo=_dt.timezone.utc)
    for i in range(n):
        px = 1000.0 * state + i
        out.append(Candle(date=base + _dt.timedelta(minutes=5 * i),
                          open=px, high=px + 0.5, low=px - 0.5,
                          close=px, volume=1000))
    return out


def _state_closes(state: int, n: int = 40) -> tuple[float, ...]:
    return tuple(1000.0 * state + i for i in range(n))


class _Var:
    def __init__(self, v: str) -> None:
        self._v = v

    def get(self) -> str:
        return self._v


class _App(WatchlistTabMixin):
    """Minimal harness satisfying the mixin attribute contract."""

    def __init__(self, *, tickers: list[str]) -> None:
        self._full_cache: dict[tuple, list[Candle]] = {}
        self._watchlist_snapshot: dict[str, dict[str, Any]] = {}
        self._events_cache: dict[str, Any] = {}
        self._worker_inbox: queue.Queue = queue.Queue()
        self.source_var = _Var("yfinance")
        self.interval_var = _Var("5m")
        self._sandbox = None
        self._tickers = list(tickers)
        self._watchlist_signals_inflight = False
        self.refresh_calls = 0

    # -- mixin seams -------------------------------------------------
    def _pinned_ticker_union(self) -> list[str]:
        return list(self._tickers)

    def _is_sandbox_active(self) -> bool:
        return False

    def _queue_watchlist_snapshot_refresh(self) -> None:
        self.refresh_calls += 1

    def _cache_is_stale(self, cached, itv) -> bool:  # noqa: ARG002
        return False

    # -- test drivers ------------------------------------------------
    def inbox_items(self) -> list[tuple[str, Any]]:
        out = []
        try:
            while True:
                out.append(self._worker_inbox.get_nowait())
        except queue.Empty:
            pass
        return out


class _RecordingEvaluator:
    """Stand-in for WatchlistSignalEvaluator.

    Records the exact bar closes handed to it per symbol and returns a
    cell carrying the last close. ``on_snapshot`` / ``on_iterated``
    hooks let tests interleave stream mutations deterministically.
    """

    def __init__(self, *, bars_provider, source,  # noqa: ANN001
                 on_snapshot=None, on_iterated=None,  # noqa: ANN001
                 iter_delay: float = 0.0005) -> None:
        self._bars_provider = bars_provider
        self._source = source
        self._on_snapshot = on_snapshot
        self._on_iterated = on_iterated
        self._iter_delay = iter_delay
        self.seen: list[tuple[float, ...]] = []

    def evaluate(self, symbols, columns):  # noqa: ANN001, ANN202
        out: dict[str, dict[str, ColumnValue]] = {}
        for sym in symbols:
            candles = self._bars_provider(self._source, sym, "5m")
            if self._on_snapshot is not None:
                self._on_snapshot()
            closes = []
            for c in candles:
                closes.append(c.close)
                if self._iter_delay:
                    time.sleep(self._iter_delay)
            if self._on_iterated is not None:
                self._on_iterated(tuple(closes))
            self.seen.append(tuple(closes))
            last = closes[-1] if closes else None
            out[sym] = {"sig1": ColumnValue(last, f"{last}", "ok")}
        return out


@pytest.fixture()
def _patch_evaluator(monkeypatch):
    """Install the recording evaluator; return its factory kwargs hook."""
    hooks: dict[str, Any] = {}

    def _factory(*, bars_provider, source):
        return _RecordingEvaluator(bars_provider=bars_provider,
                                   source=source, **hooks)

    monkeypatch.setattr(
        "tradinglab.watchlists.signals.WatchlistSignalEvaluator", _factory)
    return hooks


def _run_worker(app: _App) -> None:
    """Run ``_compute_watchlist_signals`` on a real worker thread."""
    errors: list[BaseException] = []

    def _target() -> None:
        try:
            app._compute_watchlist_signals(["AMD"], [], "yfinance")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=_target)
    t.start()
    t.join(timeout=30.0)
    assert not t.is_alive(), "signal worker hung"
    assert not errors, f"worker raised: {errors!r}"


# ---------------------------------------------------------------------------
# 1. The worker receives a snapshot, never the live list
# ---------------------------------------------------------------------------


def test_signal_bars_returns_a_copy_of_the_cached_list():
    """``_signal_bars`` must not hand the live ``_full_cache`` list out."""
    app = _App(tickers=["AMD"])
    live = _bars(0)
    app._full_cache[("yfinance", "AMD", "5m")] = live

    bars = app._signal_bars("yfinance", "AMD", "5m")

    assert bars is not live
    assert [c.close for c in bars] == list(_state_closes(0))


def test_worker_evaluation_sees_exactly_one_cache_state(_patch_evaluator):
    """Churn the live list while the worker evaluates.

    The evaluator's slow iteration widens any race window: with the old
    live-list hand-off it would observe a mixture of two states (or
    raise); with snapshot-at-handoff it must see exactly one complete
    state — never a mix.
    """
    app = _App(tickers=["AMD"])
    live = _bars(0)
    app._full_cache[("yfinance", "AMD", "5m")] = live
    exact = {_state_closes(k) for k in range(50)}

    stop = threading.Event()

    def _churn() -> None:
        # In-place replace, mirroring how streaming mutates the cached
        # list without swapping the object identity.
        k = 1
        while not stop.is_set():
            live[:] = _bars(k % 50)
            k += 1

    churn = threading.Thread(target=_churn)
    churn.start()
    try:
        _run_worker(app)
    finally:
        stop.set()
        churn.join(timeout=30.0)

    # The recording evaluator instance is cached on the app; fetch it.
    ev = app._watchlist_signal_evaluator
    assert len(ev.seen) == 1, f"expected one evaluation, saw {len(ev.seen)}"
    assert ev.seen[0] in exact, (
        "worker observed a torn mix of cache states "
        f"(first/last closes: {ev.seen[0][:3]}…{ev.seen[0][-3:]})"
    )


# ---------------------------------------------------------------------------
# 2. The shared snapshot is only ever touched on the Tk thread
# ---------------------------------------------------------------------------


def test_worker_never_mutates_the_shared_snapshot(_patch_evaluator):
    """Pre-existing ``_sig`` dict must be the identical, untouched object
    after the worker run; results travel via the inbox instead."""
    app = _App(tickers=["AMD"])
    app._full_cache[("yfinance", "AMD", "5m")] = _bars(0)
    old_sig = {"old_col": ColumnValue(1.0, "1.0", "ok")}
    app._watchlist_snapshot["AMD"] = {"last": 100.0, "_sig": old_sig}

    _run_worker(app)

    snap = app._watchlist_snapshot["AMD"]
    assert snap["_sig"] is old_sig, "worker mutated the shared _sig dict"
    assert snap["_sig"] == {"old_col": old_sig["old_col"]}
    assert snap["last"] == 100.0

    kinds = [kind for kind, _payload in app.inbox_items()]
    assert "watchlist_signals" in kinds
    assert "refresh" in kinds


def test_tk_thread_apply_merges_cells_atomically(_patch_evaluator):
    """Draining the inbox payload on the Tk thread merges, preserving
    pre-existing cells and other snapshot keys."""
    app = _App(tickers=["AMD"])
    app._full_cache[("yfinance", "AMD", "5m")] = _bars(0)
    old_cell = ColumnValue(1.0, "1.0", "ok")
    app._watchlist_snapshot["AMD"] = {"last": 100.0,
                                      "_sig": {"old_col": old_cell}}

    _run_worker(app)
    payloads = [p for k, p in app.inbox_items() if k == "watchlist_signals"]
    assert len(payloads) == 1

    # Tk-thread drain:
    app._apply_watchlist_signals(payloads[0])

    sig = app._watchlist_snapshot["AMD"]["_sig"]
    assert sig["old_col"] is old_cell
    assert sig["sig1"].raw == _state_closes(0)[-1]
    assert app._watchlist_snapshot["AMD"]["last"] == 100.0


def test_apply_creates_sig_dict_for_new_tickers():
    """Tickers with no snapshot entry yet get one on the Tk thread."""
    app = _App(tickers=["AMD"])
    cell = ColumnValue(5.0, "5.0", "ok")

    app._apply_watchlist_signals({"AMD": {"sig1": cell}})

    assert app._watchlist_snapshot["AMD"]["_sig"] == {"sig1": cell}


# ---------------------------------------------------------------------------
# 3. End-to-end: worker → inbox → real Tk-thread drain → snapshot
# ---------------------------------------------------------------------------


class _DrainHarness(WatchlistTabMixin, _polling.PollingMixin):
    """Combined harness wiring the real ``_drain_worker_inbox`` to the
    real ``_apply_watchlist_signals`` (no Tk interpreter)."""

    def __init__(self) -> None:
        self._worker_inbox: queue.Queue = queue.Queue()
        self._after_jobs: set[str] = set()
        self._watchlist_snapshot: dict[str, dict[str, Any]] = {}
        self._full_cache: dict[tuple, list[Candle]] = {}
        self._sandbox = None
        self._watchlist_signals_inflight = False
        self.refresh_calls = 0
        self.after_calls: list[tuple[int, Any]] = []

    def _is_sandbox_active(self) -> bool:
        return False

    def _schedule_watchlist_tab_refresh(self) -> None:
        self.refresh_calls += 1

    def after(self, delay_ms: int, fn):  # noqa: ANN001, ANN202
        self.after_calls.append((delay_ms, fn))
        return f"job{len(self.after_calls)}"

    def after_cancel(self, jid: str) -> None:  # noqa: ARG002
        pass


def test_end_to_end_worker_inbox_drain_applies_signals(_patch_evaluator):
    """Downstream order: the worker's posted payload survives the real
    drain and lands in the snapshot on the Tk thread, followed by the
    refresh the worker's ``finally`` queued."""
    h = _DrainHarness()
    h._full_cache[("yfinance", "AMD", "5m")] = _bars(0)

    errors: list[BaseException] = []

    def _target() -> None:
        try:
            h._compute_watchlist_signals(["AMD"], [], "yfinance")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=_target)
    t.start()
    t.join(timeout=30.0)
    assert not t.is_alive()
    assert not errors

    # Tk-thread drain (main thread here).
    h._drain_worker_inbox()

    sig = h._watchlist_snapshot["AMD"]["_sig"]
    assert sig["sig1"].raw == _state_closes(0)[-1]
    assert h.refresh_calls == 1
    # Drain re-armed itself for the next tick.
    assert h.after_calls and h.after_calls[-1][0] == 80


# ---------------------------------------------------------------------------
# 4. Ordering: a stream mutation between handoff and delivery is harmless
# ---------------------------------------------------------------------------


def test_stream_mutation_after_handoff_does_not_corrupt_result(
    _patch_evaluator,
):
    """The delivered result is anchored to the handoff snapshot.

    The worker snapshots bars, the stream then appends newer bars to the
    live list, and only then does the worker finish iterating. The
    posted cells must reflect the snapshot (state 0's last close), not
    the post-mutation tail.
    """
    app = _App(tickers=["AMD"])
    live = _bars(0)
    app._full_cache[("yfinance", "AMD", "5m")] = live

    snapshotted = threading.Event()
    proceed = threading.Event()
    _patch_evaluator["on_snapshot"] = lambda: (
        snapshotted.set(), proceed.wait(timeout=30.0),
    )

    errors: list[BaseException] = []

    def _target() -> None:
        try:
            app._compute_watchlist_signals(["AMD"], [], "yfinance")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=_target)
    t.start()
    try:
        assert snapshotted.wait(timeout=30.0), "worker never snapshotted"
        # Stream mutation lands between handoff and delivery.
        live.extend(_bars(1))
        proceed.set()
        t.join(timeout=30.0)
    finally:
        proceed.set()
        t.join(timeout=30.0)
    assert not t.is_alive()
    assert not errors

    payloads = [p for k, p in app.inbox_items() if k == "watchlist_signals"]
    assert len(payloads) == 1
    # State 0's last close (39.0), NOT the appended state-1 tail (1039.0).
    assert payloads[0]["AMD"]["sig1"].raw == _state_closes(0)[-1] == 39.0
    assert payloads[0]["AMD"]["sig1"].raw != _state_closes(1)[-1]
