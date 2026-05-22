"""Tests for :class:`ScanRunner` persistent-memo lifecycle and stats.

Validates the streaming-foundation slice's runner-level guarantees:

* fingerprint-based reconciliation (build, reuse, append, rebuild);
* stale-symbol eviction;
* per-symbol task granularity (one ``Bars`` view shared across scans);
* the ``stats()`` counters track the right transitions.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

import tradinglab.indicators  # noqa: F401  -- registers indicators
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

# --- helpers ---------------------------------------------------------------


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


def _scan_gt(threshold: float, *, name: str = "s") -> ScanDefinition:
    return ScanDefinition(
        name=name,
        primary_interval="1m",
        universe_filter=UniverseFilter.all(),
        root=Group(combinator="and", children=[
            Condition(left=FieldRef.builtin("close"), op=OP_GT,
                      params={"right": FieldRef.literal(threshold)},
                      interval="1m"),
        ]),
    )


# --- build / reuse ---------------------------------------------------------


def test_first_run_builds_state_for_each_symbol():
    runner = ScanRunner(max_workers=1)
    try:
        scan = _scan_gt(50.0)
        cs = {"AAPL": _candles(10), "MSFT": _candles(10)}
        runner.run([scan], cs, interval="1m", tick_id=1)
        s = runner.stats()
        assert s["buffer_rebuilds"] == 2
        assert s["memo_builds"]     == 2
        assert s["memo_reuses"]     == 0
    finally:
        runner.shutdown()


def test_second_run_same_state_reuses_memo():
    """Same list object, no growth, no fingerprint change → reuse."""
    runner = ScanRunner(max_workers=1)
    try:
        scan = _scan_gt(50.0)
        aapl = _candles(10)
        cs = {"AAPL": aapl}
        runner.run([scan], cs, interval="1m", tick_id=1)
        runner.run([scan], cs, interval="1m", tick_id=2)
        s = runner.stats()
        assert s["buffer_rebuilds"] == 1
        assert s["memo_builds"]     == 1
        assert s["memo_reuses"]     == 1
    finally:
        runner.shutdown()


def test_appending_to_same_list_uses_buffer_append():
    """Same list object, length grows by 1 → append path."""
    runner = ScanRunner(max_workers=1)
    try:
        scan = _scan_gt(50.0)
        aapl: list[Candle] = _candles(10)
        cs = {"AAPL": aapl}
        runner.run([scan], cs, interval="1m", tick_id=1)
        # Mutate the same list (sandbox pattern).
        aapl.append(_candles(11)[-1])
        runner.run([scan], cs, interval="1m", tick_id=2)
        s = runner.stats()
        assert s["buffer_appends"]  == 1
        assert s["buffer_rebuilds"] == 1
        # Append path no longer rebuilds the memo: incremental advance
        # (or per-key drop+recompute) replaces the old fresh-memo path.
        # The scan here uses only a builtin (``close``), so nothing is
        # cached and the per-key advance loop is a no-op — memo_builds
        # stays at 1 (the initial cold build).
        assert s["memo_builds"]     == 1
        assert s["memo_reuses"]     == 0
    finally:
        runner.shutdown()


def test_replacing_list_object_rebuilds():
    """Different list object (id changes) → full rebuild even if content matches."""
    runner = ScanRunner(max_workers=1)
    try:
        scan = _scan_gt(50.0)
        first = _candles(10)
        runner.run([scan], {"AAPL": first}, interval="1m", tick_id=1)
        # Different list object but same content.
        second = list(first)
        assert second is not first
        runner.run([scan], {"AAPL": second}, interval="1m", tick_id=2)
        s = runner.stats()
        assert s["buffer_rebuilds"] == 2
        assert s["memo_builds"]     == 2
        assert s["memo_reuses"]     == 0
    finally:
        runner.shutdown()


def test_last_bar_mutation_same_length_rebuilds():
    """Forming-bar style: same list object, same length, last close changed.
    Fingerprint differs → full rebuild (memo cannot be reused safely)."""
    runner = ScanRunner(max_workers=1)
    try:
        scan = _scan_gt(50.0)
        cs_list: list[Candle] = _candles(10)
        runner.run([scan], {"AAPL": cs_list}, interval="1m", tick_id=1)
        # Replace last bar with a same-length but different-close one.
        last = cs_list[-1]
        cs_list[-1] = Candle(
            date=last.date, open=last.open, high=last.high, low=last.low,
            close=last.close + 5.0, volume=last.volume, session=last.session,
        )
        runner.run([scan], {"AAPL": cs_list}, interval="1m", tick_id=2)
        s = runner.stats()
        # First run rebuilt; second run also rebuilt due to fingerprint mismatch.
        assert s["buffer_rebuilds"] == 2
        assert s["memo_builds"]     == 2
        assert s["memo_reuses"]     == 0
        assert s["buffer_appends"]  == 0
    finally:
        runner.shutdown()


def test_stale_symbol_evicted_when_dropped_from_universe():
    runner = ScanRunner(max_workers=1)
    try:
        scan = _scan_gt(50.0)
        runner.run([scan], {"AAPL": _candles(5), "MSFT": _candles(5)},
                   interval="1m", tick_id=1)
        # Drop MSFT from the universe.
        runner.run([scan], {"AAPL": _candles(5)},
                   interval="1m", tick_id=2)
        # No more cached state for MSFT.
        assert "MSFT" not in runner._states
        assert "AAPL" in runner._states
        assert runner.stats()["stale_evictions"] >= 1
    finally:
        runner.shutdown()


def test_invalidate_drops_state_for_symbol():
    runner = ScanRunner(max_workers=1)
    try:
        scan = _scan_gt(50.0)
        runner.run([scan], {"AAPL": _candles(5)}, interval="1m", tick_id=1)
        assert "AAPL" in runner._states
        runner.invalidate("AAPL")
        assert "AAPL" not in runner._states
    finally:
        runner.shutdown()


def test_invalidate_all_clears_states_keeps_history():
    runner = ScanRunner(max_workers=1)
    try:
        scan = _scan_gt(50.0)
        runner.run([scan], {"AAPL": _candles(5)}, interval="1m", tick_id=1)
        h_before = runner.history_for(scan.id)
        runner.invalidate_all()
        assert runner._states == {}
        # History survived.
        assert runner.history_for(scan.id) is h_before
    finally:
        runner.shutdown()


# --- per-symbol task granularity ------------------------------------------


def test_multi_scan_single_symbol_shares_one_bars_view():
    """Two scans on the same symbol should run against the SAME ``Bars``
    object (per-symbol task granularity guarantees this)."""
    runner = ScanRunner(max_workers=1)
    try:
        scan_a = _scan_gt(50.0, name="a")
        scan_b = _scan_gt(60.0, name="b")
        runner.run([scan_a, scan_b], {"AAPL": _candles(10)},
                   interval="1m", tick_id=1)
        # One memo build for AAPL despite two scans.
        s = runner.stats()
        assert s["memo_builds"]     == 1
        assert s["buffer_rebuilds"] == 1
    finally:
        runner.shutdown()


def test_stats_independent_copies():
    """``stats()`` must return a fresh copy so callers can't poke
    internals."""
    runner = ScanRunner(max_workers=1)
    try:
        scan = _scan_gt(50.0)
        runner.run([scan], {"AAPL": _candles(5)}, interval="1m", tick_id=1)
        snap1 = runner.stats()
        snap1["memo_builds"] = 999
        snap2 = runner.stats()
        assert snap2["memo_builds"] == 1
    finally:
        runner.shutdown()


# --- result correctness preserved across the rewrite ----------------------


def test_run_returns_one_result_per_scan_with_stable_symbol_order():
    runner = ScanRunner(max_workers=2)
    try:
        scan = _scan_gt(105.0)  # close[i] = 100 + i ; matches at i=6..
        cs = {
            "AAPL": _candles(10, start_close=100.0),
            "MSFT": _candles(10, start_close=100.0),
            "GOOG": _candles(10, start_close=100.0),
        }
        results = runner.run([scan], cs, interval="1m", tick_id=1)
        assert set(results) == {scan.id}
        rows = results[scan.id].rows
        # 3 symbols, alphabetical order.
        assert [r.symbol for r in rows] == ["AAPL", "GOOG", "MSFT"]
        for r in rows:
            assert r.matched is True  # last close = 109 > 105
    finally:
        runner.shutdown()
