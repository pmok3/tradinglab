"""ScanRunner ↔ BarsRegistry integration tests.

Verifies the opt-in registry path on :class:`ScanRunner`: that
``bars_registry=`` produces the same results as the local-state path
on a single-interval scan, that lazy-load skipping is graceful, that
adding a symbol mid-stream "lights up" on the next ``run()``, that
memos are shared across scans via the registry, and that mixed-mode
construction is safe.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List

import pytest

import tradinglab.indicators  # noqa: F401  (registers indicators)
from tradinglab.core.bars_registry import BarsRegistry
from tradinglab.data.multi_interval_cache import MultiIntervalCache
from tradinglab.models import Candle
from tradinglab.scanner.model import (
    Condition,
    FieldRef,
    Group,
    OP_GT,
    ScanDefinition,
    UniverseFilter,
)
from tradinglab.scanner.runner import ScanRunner


# --- helpers ---------------------------------------------------------------


def _candles(closes: List[float],
             start: datetime = datetime(2026, 5, 4, 9, 30),
             interval_min: int = 5) -> List[Candle]:
    out = []
    for i, c in enumerate(closes):
        out.append(Candle(
            date=start + timedelta(minutes=i * interval_min),
            open=c - 0.5, high=c + 1.0, low=c - 1.0, close=c,
            volume=1000 + i, session="regular",
        ))
    return out


def _scan_close_gt(threshold: float, *, name: str = "test",
                   interval: str = "5m") -> ScanDefinition:
    return ScanDefinition(
        name=name,
        primary_interval=interval,
        universe_filter=UniverseFilter.all(),
        root=Group(combinator="and", children=[
            Condition(left=FieldRef.builtin("close"), op=OP_GT,
                      params={"right": FieldRef.literal(threshold)},
                      interval=interval),
        ]),
    )


def _make_registry_with(symbol: str, candles: List[Candle], interval: str = "5m") -> BarsRegistry:
    cache = MultiIntervalCache()
    cache.set_bars(symbol, interval, candles)
    return BarsRegistry(cache)


# --- equivalence -----------------------------------------------------------


def test_registry_runner_matches_local_runner_results():
    """Same scan, same candles, both runner modes — same matched/values."""
    aapl = _candles([100.0, 105.0, 108.0, 110.0])
    msft = _candles([90.0, 95.0, 99.0, 101.0])
    scan = _scan_close_gt(105.0)

    # Local-state path.
    runner_local = ScanRunner()
    try:
        local_results = runner_local.run(
            [scan], {"AAPL": aapl, "MSFT": msft},
            interval="5m", tick_id=1,
        )
    finally:
        runner_local.shutdown()

    # Registry path.
    cache = MultiIntervalCache()
    cache.set_bars("AAPL", "5m", aapl)
    cache.set_bars("MSFT", "5m", msft)
    reg = BarsRegistry(cache)
    runner_reg = ScanRunner(bars_registry=reg)
    try:
        reg_results = runner_reg.run(
            [scan], {"AAPL": aapl, "MSFT": msft},  # values ignored in registry mode
            interval="5m", tick_id=1,
        )
    finally:
        runner_reg.shutdown()

    local_rows = {r.symbol: r for r in local_results[scan.id].rows}
    reg_rows = {r.symbol: r for r in reg_results[scan.id].rows}
    assert set(local_rows) == set(reg_rows) == {"AAPL", "MSFT"}
    for sym in ("AAPL", "MSFT"):
        assert local_rows[sym].matched == reg_rows[sym].matched
        # Per-condition LHS values agree.
        assert local_rows[sym].values == reg_rows[sym].values


def test_symbol_not_in_registry_is_skipped_no_crash():
    """Universe lists a symbol the registry hasn't loaded → no row, no crash."""
    aapl = _candles([100.0, 105.0, 108.0, 110.0])
    reg = _make_registry_with("AAPL", aapl)  # MSFT NOT loaded
    scan = _scan_close_gt(105.0)
    runner = ScanRunner(bars_registry=reg)
    try:
        results = runner.run(
            [scan], {"AAPL": aapl, "MSFT": []},  # MSFT in universe via dict key
            interval="5m", tick_id=1,
        )
    finally:
        runner.shutdown()
    rows = results[scan.id].rows
    syms = {r.symbol for r in rows}
    # AAPL evaluated, MSFT silently skipped (no row).
    assert syms == {"AAPL"}
    # Stat counter notes the skip.
    assert runner.stats()["registry_skips"] == 1


def test_symbol_added_mid_stream_appears_next_run():
    """Adding a symbol to the registry between runs lights it up next tick."""
    aapl = _candles([100.0, 105.0, 108.0, 110.0])
    msft = _candles([90.0, 95.0, 100.0, 102.0])
    cache = MultiIntervalCache()
    cache.set_bars("AAPL", "5m", aapl)  # MSFT not yet loaded
    reg = BarsRegistry(cache)
    scan = _scan_close_gt(99.0)
    runner = ScanRunner(bars_registry=reg)
    try:
        r1 = runner.run([scan], {"AAPL": aapl, "MSFT": []},
                        interval="5m", tick_id=1)
        # MSFT skipped on tick 1.
        assert {r.symbol for r in r1[scan.id].rows} == {"AAPL"}

        # Lazy-load completes — backfill MSFT into cache.
        cache.set_bars("MSFT", "5m", msft)

        r2 = runner.run([scan], {"AAPL": aapl, "MSFT": []},
                        interval="5m", tick_id=2)
        # MSFT now appears.
        assert {r.symbol for r in r2[scan.id].rows} == {"AAPL", "MSFT"}
    finally:
        runner.shutdown()


def test_multi_scan_single_symbol_shares_memo_via_registry():
    """Two scans on the same symbol/interval → one memo build via the registry."""
    aapl = _candles([100.0, 105.0, 108.0, 110.0])
    reg = _make_registry_with("AAPL", aapl)
    scan_a = _scan_close_gt(100.0, name="A")
    scan_b = _scan_close_gt(50.0, name="B")
    runner = ScanRunner(bars_registry=reg)
    try:
        runner.run([scan_a, scan_b], {"AAPL": aapl},
                   interval="5m", tick_id=1)
    finally:
        runner.shutdown()
    s = reg.stats()
    # Only one memo was built for the (AAPL, 5m) key, even though
    # the runner's per-symbol task evaluates both scans.
    assert s["memos_rebuilt"] == 1
    # And exactly one view was vended (per-tick / per-symbol).
    assert s["views_built"] == 1


def test_switching_from_no_registry_to_real_registry_doesnt_crash():
    """Constructing first without then with a registry both work."""
    aapl = _candles([100.0, 105.0, 108.0, 110.0])
    scan = _scan_close_gt(100.0)

    # Without registry.
    r0 = ScanRunner()
    try:
        out0 = r0.run([scan], {"AAPL": aapl}, interval="5m", tick_id=1)
        assert {r.symbol for r in out0[scan.id].rows} == {"AAPL"}
    finally:
        r0.shutdown()

    # With registry.
    reg = _make_registry_with("AAPL", aapl)
    r1 = ScanRunner(bars_registry=reg)
    try:
        out1 = r1.run([scan], {"AAPL": aapl}, interval="5m", tick_id=2)
        assert {r.symbol for r in out1[scan.id].rows} == {"AAPL"}
    finally:
        r1.shutdown()


def test_scan_with_mixed_interval_condition_does_not_blow_up():
    """A scan with a 5m base + a 1d condition runs cleanly under the registry."""
    aapl_5m = _candles([100.0, 105.0, 108.0, 110.0])
    aapl_1d = _candles([95.0, 100.0, 102.0])
    cache = MultiIntervalCache()
    cache.set_bars("AAPL", "5m", aapl_5m)
    cache.set_bars("AAPL", "1d", aapl_1d)
    reg = BarsRegistry(cache)

    scan = ScanDefinition(
        name="mixed",
        primary_interval="5m",
        universe_filter=UniverseFilter.all(),
        root=Group(combinator="and", children=[
            Condition(left=FieldRef.builtin("close"), op=OP_GT,
                      params={"right": FieldRef.literal(50.0)},
                      interval="5m"),
            Condition(left=FieldRef.builtin("close"), op=OP_GT,
                      params={"right": FieldRef.literal(50.0)},
                      interval="1d"),
        ]),
    )
    runner = ScanRunner(bars_registry=reg)
    try:
        # Should not raise NotImplementedError nor crash.
        results = runner.run([scan], {"AAPL": aapl_5m},
                             interval="5m", tick_id=1)
    finally:
        runner.shutdown()
    rows = results[scan.id].rows
    assert len(rows) == 1
    assert rows[0].symbol == "AAPL"
    # Both conditions resolve True: 5m close=110>50 AND 1d close=102>50.
    assert rows[0].matched is True
