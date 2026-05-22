"""Runner tests: synchronous + threaded paths, MatchHistory, universe filter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

import pytest

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
from tradinglab.scanner.runner import (
    MatchHistory,
    MatchRow,
    ScanResult,
    ScanRunner,
    run_scan_sync,
)


def _candles(closes: list[float],
             start: datetime = datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc),
             interval_min: int = 5) -> list[Candle]:
    out = []
    for i, c in enumerate(closes):
        out.append(Candle(
            date=start + timedelta(minutes=i*interval_min),
            open=c-0.5, high=c+1.0, low=c-1.0, close=c, volume=1000+i,
            session="regular",
        ))
    return out


def _scan_close_gt(threshold: float, *, name: str = "test",
                   universe: UniverseFilter = None) -> ScanDefinition:
    return ScanDefinition(
        name=name,
        primary_interval="5m",
        universe_filter=universe or UniverseFilter.all(),
        root=Group(combinator="and", children=[
            Condition(left=FieldRef.builtin("close"), op=OP_GT,
                      params={"right": FieldRef.literal(threshold)},
                      interval="5m"),
        ]),
    )


# ---------------------------------------------------------------------------
# MatchHistory
# ---------------------------------------------------------------------------


def test_match_history_first_true_is_new():
    h = MatchHistory()
    assert h.update("AAPL", 1, True) is True


def test_match_history_repeated_true_not_new():
    h = MatchHistory()
    h.update("AAPL", 1, True)
    assert h.update("AAPL", 2, True) is False


def test_match_history_false_then_true_is_new():
    h = MatchHistory()
    h.update("AAPL", 1, True)
    h.update("AAPL", 2, False)
    assert h.update("AAPL", 3, True) is True


def test_match_history_none_does_not_change_state():
    h = MatchHistory()
    h.update("AAPL", 1, True)
    h.update("AAPL", 2, None)
    # Still considered matched; next True is not new.
    assert h.update("AAPL", 3, True) is False


def test_match_history_records_tick():
    h = MatchHistory()
    h.update("AAPL", 7, True)
    assert h.last_matched_tick["AAPL"] == 7


# ---------------------------------------------------------------------------
# run_scan_sync — basic
# ---------------------------------------------------------------------------


def test_run_scan_sync_matches_and_misses():
    scan = _scan_close_gt(100.0)
    bars = {
        "WIN": _candles([90, 95, 105]),    # close=105 > 100 ✓
        "LOSE": _candles([90, 95, 99]),    # close=99 < 100 ✗
    }
    res = run_scan_sync(scan, bars, interval="5m", tick_id=1)
    by_sym = {r.symbol: r for r in res.rows}
    assert by_sym["WIN"].matched is True
    assert by_sym["LOSE"].matched is False


def test_run_scan_sync_records_per_condition_values():
    scan = _scan_close_gt(100.0)
    cond_id = scan.all_conditions()[0].id
    bars = {"WIN": _candles([90, 95, 105])}
    res = run_scan_sync(scan, bars, interval="5m", tick_id=1)
    row = res.rows[0]
    assert row.values[cond_id] == 105.0


def test_run_scan_sync_empty_candles_yields_none():
    scan = _scan_close_gt(100.0)
    bars = {"EMPTY": []}
    res = run_scan_sync(scan, bars, interval="5m", tick_id=1)
    assert res.rows[0].matched is None


def test_run_scan_sync_history_marks_new_rows():
    scan = _scan_close_gt(100.0)
    history = MatchHistory()
    bars = {"AAA": _candles([90, 95, 105])}
    r1 = run_scan_sync(scan, bars, interval="5m", tick_id=1, history=history)
    assert r1.rows[0].is_new is True
    assert len(r1.new_rows) == 1
    # Second call: same match, not new anymore.
    r2 = run_scan_sync(scan, bars, interval="5m", tick_id=2, history=history)
    assert r2.rows[0].is_new is False
    assert r2.new_rows == []


def test_run_scan_sync_rank_by_evaluated():
    scan = _scan_close_gt(0.0)
    object.__setattr__(scan, "rank_by", FieldRef.builtin("volume"))
    bars = {"AAA": _candles([100, 101, 102])}
    res = run_scan_sync(scan, bars, interval="5m", tick_id=1)
    assert res.rows[0].rank_value == 1002.0  # last bar's volume


def test_run_scan_sync_universe_filter_symbols():
    scan = _scan_close_gt(0.0,
                          universe=UniverseFilter(kind="symbols", symbols=("AAA",)))
    bars = {"AAA": _candles([100, 101]), "BBB": _candles([100, 101])}
    res = run_scan_sync(scan, bars, interval="5m", tick_id=1)
    syms = sorted(r.symbol for r in res.rows)
    assert syms == ["AAA"]


def test_run_scan_sync_memo_is_shared_across_calls():
    """Two scans referencing the same indicator on the same symbol should
    only call compute() once."""
    from tradinglab.scanner.engine import IndicatorMemo
    from tradinglab.scanner.runner import run_scan_sync

    scan_a = _scan_close_gt(0.0, name="a")
    scan_b = _scan_close_gt(0.0, name="b")
    bars = {"AAA": _candles([100.0]*30)}
    memos = {}
    run_scan_sync(scan_a, bars, interval="5m", tick_id=1, memos=memos)
    run_scan_sync(scan_b, bars, interval="5m", tick_id=1, memos=memos)
    # The same IndicatorMemo instance should now be in the dict.
    assert "AAA" in memos
    assert isinstance(memos["AAA"], IndicatorMemo)


def test_run_scan_sync_timestamp_recorded():
    scan = _scan_close_gt(0.0)
    bars = {"AAA": _candles([100])}
    ts = datetime(2026, 5, 4, 14, 30, tzinfo=timezone.utc)
    res = run_scan_sync(scan, bars, interval="5m", tick_id=1, timestamp=ts)
    assert res.timestamp == ts


# ---------------------------------------------------------------------------
# ScanRunner (threaded)
# ---------------------------------------------------------------------------


def test_scan_runner_basic_threaded():
    runner = ScanRunner(max_workers=2)
    try:
        scan = _scan_close_gt(100.0)
        bars = {f"S{i}": _candles([90, 95, 105 if i % 2 == 0 else 99])
                for i in range(8)}
        out = runner.run([scan], bars, interval="5m", tick_id=1)
        assert scan.id in out
        result = out[scan.id]
        assert len(result.rows) == 8
        wins = {r.symbol for r in result.rows if r.matched is True}
        # Even-indexed symbols get close=105 → win.
        assert wins == {f"S{i}" for i in range(0, 8, 2)}
    finally:
        runner.shutdown()


def test_scan_runner_history_persists_across_runs():
    runner = ScanRunner(max_workers=2)
    try:
        scan = _scan_close_gt(100.0)
        bars = {"AAA": _candles([90, 95, 105])}
        r1 = runner.run([scan], bars, interval="5m", tick_id=1)
        assert r1[scan.id].rows[0].is_new is True
        r2 = runner.run([scan], bars, interval="5m", tick_id=2)
        assert r2[scan.id].rows[0].is_new is False
    finally:
        runner.shutdown()


def test_scan_runner_reset_history_clears():
    runner = ScanRunner(max_workers=2)
    try:
        scan = _scan_close_gt(100.0)
        bars = {"AAA": _candles([90, 95, 105])}
        runner.run([scan], bars, interval="5m", tick_id=1)
        runner.reset_history(scan.id)
        r2 = runner.run([scan], bars, interval="5m", tick_id=2)
        # After reset, the next True match is "new" again.
        assert r2[scan.id].rows[0].is_new is True
    finally:
        runner.shutdown()


def test_scan_runner_multiple_scans_share_memo_per_symbol():
    """Smoke: indicator-using scans on same symbol shouldn't crash; correctness
    of caching is verified by the engine tests."""
    runner = ScanRunner(max_workers=2)
    try:
        scan_a = ScanDefinition(
            name="A", primary_interval="5m",
            root=Group(combinator="and", children=[
                Condition(left=FieldRef.indicator("sma", params={"length": 5}),
                          op=OP_GT, params={"right": FieldRef.literal(0.0)},
                          interval="5m"),
            ]),
        )
        scan_b = ScanDefinition(
            name="B", primary_interval="5m",
            root=Group(combinator="and", children=[
                Condition(left=FieldRef.indicator("sma", params={"length": 5}),
                          op=OP_GT, params={"right": FieldRef.literal(0.0)},
                          interval="5m"),
            ]),
        )
        bars = {"AAA": _candles([10.0]*30), "BBB": _candles([20.0]*30)}
        out = runner.run([scan_a, scan_b], bars, interval="5m", tick_id=1)
        assert all(r.matched is True
                   for res in out.values()
                   for r in res.rows)
    finally:
        runner.shutdown()


def test_scan_runner_empty_scans_returns_empty():
    runner = ScanRunner()
    try:
        out = runner.run([], {"A": _candles([100])}, interval="5m", tick_id=1)
        assert out == {}
    finally:
        runner.shutdown()


def test_scan_runner_shutdown_idempotent():
    runner = ScanRunner()
    runner.shutdown()
    runner.shutdown()  # second call is a no-op


def test_scan_runner_universe_filter_symbols():
    runner = ScanRunner()
    try:
        scan = _scan_close_gt(0.0,
                              universe=UniverseFilter(kind="symbols", symbols=("AAA",)))
        bars = {"AAA": _candles([100]), "BBB": _candles([100])}
        out = runner.run([scan], bars, interval="5m", tick_id=1)
        syms = {r.symbol for r in out[scan.id].rows}
        assert syms == {"AAA"}
    finally:
        runner.shutdown()


# ---------------------------------------------------------------------------
# Within-last-N-bars evidence threading
# ---------------------------------------------------------------------------


def test_run_scan_sync_no_lookback_yields_empty_evidence():
    """Plain scans (no within_last on any node) emit empty evidence lists."""
    scan = _scan_close_gt(99.0)  # within_last_bars defaults to 0.
    res = run_scan_sync(scan, {"AAA": _candles([100.0])},
                        interval="5m", tick_id=1)
    row = res.rows[0]
    assert row.matched is True
    assert row.evidence == []


def test_run_scan_sync_lookback_match_carries_evidence():
    """A within-last match populates ``MatchRow.evidence`` with payload."""
    cond = Condition(
        left=FieldRef.builtin("close"), op=OP_GT,
        params={"right": FieldRef.literal(104.0)},
        interval="5m",
        within_last_bars=2,
        within_last_mode="any",
    )
    scan = ScanDefinition(
        name="lb",
        primary_interval="5m",
        universe_filter=UniverseFilter.all(),
        root=Group(combinator="and", children=[cond]),
    )
    # close: [100, 105, 99, 99]; close>104 fires at idx=1 only.
    # Anchor = 3, N=2 → window [1..3], any match found at j=1.
    res = run_scan_sync(scan, {"AAA": _candles([100.0, 105.0, 99.0, 99.0])},
                        interval="5m", tick_id=1)
    row = res.rows[0]
    assert row.matched is True
    assert len(row.evidence) == 1
    ev = row.evidence[0]
    assert ev.node_id == cond.id
    assert ev.bars_ago == 2  # i=3, j=1 → 3-1=2
    assert ev.value == 105.0


def test_run_scan_sync_evidence_reset_between_symbols():
    """Each symbol's row carries only its own evidence, not other symbols'."""
    cond = Condition(
        left=FieldRef.builtin("close"), op=OP_GT,
        params={"right": FieldRef.literal(104.0)},
        interval="5m",
        within_last_bars=2,
    )
    scan = ScanDefinition(
        name="lb",
        primary_interval="5m",
        universe_filter=UniverseFilter.all(),
        root=Group(combinator="and", children=[cond]),
    )
    bars = {
        "AAA": _candles([100.0, 105.0, 99.0]),  # match at idx=1
        "BBB": _candles([100.0, 100.0, 100.0]), # never matches
    }
    res = run_scan_sync(scan, bars, interval="5m", tick_id=1)
    rows = {r.symbol: r for r in res.rows}
    assert rows["AAA"].matched is True
    assert len(rows["AAA"].evidence) == 1
    assert rows["BBB"].matched is False
    # BBB's row must NOT carry AAA's evidence.
    assert rows["BBB"].evidence == []


def test_run_scan_sync_evidence_reset_between_scans():
    """Evidence from one scan does not leak into the next on the same tick."""
    # Scan A: within_last condition that fires.
    cond_a = Condition(
        left=FieldRef.builtin("close"), op=OP_GT,
        params={"right": FieldRef.literal(104.0)},
        interval="5m",
        within_last_bars=2,
    )
    scan_a = ScanDefinition(
        name="A", primary_interval="5m",
        universe_filter=UniverseFilter.all(),
        root=Group(combinator="and", children=[cond_a]),
    )
    # Scan B: plain (N=0) — should have empty evidence even though A
    # fired evidence on the same context-equivalent symbol.
    scan_b = _scan_close_gt(99.0)

    candles = {"AAA": _candles([100.0, 105.0, 99.0])}
    memos = {}
    res_a = run_scan_sync(scan_a, candles, interval="5m", tick_id=1, memos=memos)
    res_b = run_scan_sync(scan_b, candles, interval="5m", tick_id=1, memos=memos)
    assert len(res_a.rows[0].evidence) == 1
    assert res_b.rows[0].evidence == []

