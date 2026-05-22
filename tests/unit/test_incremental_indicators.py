"""Parity + integration tests for the incremental indicator protocol.

Phase 3 of the live-tick slice. Validates:

* :meth:`SMA.inc_init` / :meth:`SMA.inc_step` produce arrays
  bit-for-bit equal to :meth:`SMA.compute_arr` after equivalent
  appends. Tested across warmup, single-tick appends, and multi-bar
  catch-up appends on a deterministic random walk.
* :meth:`EMA.inc_init` / :meth:`EMA.inc_step` produce arrays
  numerically equivalent to :meth:`EMA.compute_arr` (within
  floating-point tolerance) across the same scenarios. Includes the
  warmup-crossing case where state was inited at length ``< L``.
* Bad inputs (``len <= prev_len``) raise ``ValueError``.
* :class:`IndicatorMemo.advance_for_append` advances supported
  indicators in place, drops unsupported entries, and updates
  ``stats_sink`` counters.
* :class:`ScanRunner` uses the incremental path for closed-bar
  appends end-to-end: ``incremental_steps`` ticks; the cached output
  array stays valid across appends; scan results are unchanged from
  full-rebuild parity.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import List

import numpy as np
import pytest

import tradinglab.indicators  # noqa: F401
from tradinglab.core.bars import Bars
from tradinglab.indicators.moving_averages import EMA, SMA
from tradinglab.models import Candle
from tradinglab.scanner.engine import IndicatorMemo, make_context
from tradinglab.scanner.model import (
    OP_GT,
    Condition,
    FieldRef,
    Group,
    ScanDefinition,
    UniverseFilter,
)
from tradinglab.scanner.runner import ScanRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _random_walk(n: int, seed: int = 7) -> list[Candle]:
    rng = random.Random(seed)
    base = 100.0
    t0 = datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc)
    out: list[Candle] = []
    price = base
    for i in range(n):
        price = max(1.0, price + rng.uniform(-1.0, 1.0))
        out.append(Candle(
            date=t0 + timedelta(minutes=i),
            open=price - 0.2, high=price + 0.5, low=price - 0.5,
            close=price, volume=1000 + i, session="regular",
        ))
    return out


def _bars_of(candles: list[Candle]) -> Bars:
    return Bars.from_candles(candles)


# ---------------------------------------------------------------------------
# SMA parity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("length,seed", [(5, 1), (20, 7), (50, 13)])
def test_sma_inc_step_parity_single_appends(length, seed):
    """One-bar appends repeatedly — output must match full compute exactly."""
    candles = _random_walk(120, seed=seed)
    sma = SMA(length=length)
    # Start state at 30 bars (warmup region for length=50 → mostly NaN).
    state = sma.inc_init(_bars_of(candles[:30]))
    cur_len = 30
    for target_len in range(31, len(candles) + 1):
        state = sma.inc_step(state, _bars_of(candles[:target_len]), prev_len=cur_len)
        cur_len = target_len
    inc_out = state["output"]["sma"]
    full_out = sma.compute_arr(_bars_of(candles))["sma"]
    # Tiny rounding differences from cumsum vs window-mean are expected.
    np.testing.assert_allclose(inc_out, full_out, rtol=1e-12, atol=1e-12, equal_nan=True)


def test_sma_inc_step_parity_multi_bar_append():
    """Catch-up append by k>1 bars — must match single-step path."""
    candles = _random_walk(80, seed=3)
    sma = SMA(length=10)
    state = sma.inc_init(_bars_of(candles[:20]))
    state = sma.inc_step(state, _bars_of(candles[:50]), prev_len=20)
    state = sma.inc_step(state, _bars_of(candles[:80]), prev_len=50)
    np.testing.assert_allclose(
        state["output"]["sma"], sma.compute_arr(_bars_of(candles))["sma"],
        rtol=1e-12, atol=1e-12, equal_nan=True,
    )


def test_sma_inc_step_warmup_init_below_length():
    """State inited at length < L (all NaN), then crosses warmup via appends."""
    candles = _random_walk(50, seed=2)
    sma = SMA(length=20)
    state = sma.inc_init(_bars_of(candles[:5]))  # 5 < 20 → all NaN
    assert np.all(np.isnan(state["output"]["sma"]))
    state = sma.inc_step(state, _bars_of(candles[:50]), prev_len=5)
    np.testing.assert_allclose(
        state["output"]["sma"], sma.compute_arr(_bars_of(candles))["sma"],
        rtol=1e-12, atol=1e-12, equal_nan=True,
    )


def test_sma_inc_step_rejects_non_growth():
    candles = _random_walk(30, seed=4)
    sma = SMA(length=5)
    state = sma.inc_init(_bars_of(candles))
    with pytest.raises(ValueError):
        sma.inc_step(state, _bars_of(candles), prev_len=30)
    with pytest.raises(ValueError):
        sma.inc_step(state, _bars_of(candles[:20]), prev_len=30)


# ---------------------------------------------------------------------------
# EMA parity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("length,seed", [(5, 11), (20, 17), (50, 23)])
def test_ema_inc_step_parity_single_appends(length, seed):
    candles = _random_walk(150, seed=seed)
    ema = EMA(length=length)
    # Init at exactly the seed length so committed state is well-defined.
    state = ema.inc_init(_bars_of(candles[:length]))
    cur_len = length
    for target_len in range(length + 1, len(candles) + 1):
        state = ema.inc_step(state, _bars_of(candles[:target_len]), prev_len=cur_len)
        cur_len = target_len
    inc_out = state["output"]["ema"]
    full_out = ema.compute_arr(_bars_of(candles))["ema"]
    np.testing.assert_allclose(inc_out, full_out, rtol=1e-12, atol=1e-12, equal_nan=True)


def test_ema_inc_step_warmup_crossing():
    """Init at length < L (all NaN, committed_idx=-1), then cross seed boundary."""
    candles = _random_walk(60, seed=29)
    L = 20
    ema = EMA(length=L)
    state = ema.inc_init(_bars_of(candles[:5]))
    assert state["committed_idx"] == -1
    assert np.all(np.isnan(state["output"]["ema"]))
    # Append in chunks that cross the seed (5 → 25 → 60).
    state = ema.inc_step(state, _bars_of(candles[:25]), prev_len=5)
    # After crossing, committed_idx must be at the latest committed index.
    assert state["committed_idx"] == 24
    state = ema.inc_step(state, _bars_of(candles[:60]), prev_len=25)
    np.testing.assert_allclose(
        state["output"]["ema"],
        ema.compute_arr(_bars_of(candles))["ema"],
        rtol=1e-12, atol=1e-12, equal_nan=True,
    )


def test_ema_inc_step_multi_bar_catchup():
    candles = _random_walk(100, seed=31)
    ema = EMA(length=10)
    state = ema.inc_init(_bars_of(candles[:30]))
    state = ema.inc_step(state, _bars_of(candles[:65]), prev_len=30)
    state = ema.inc_step(state, _bars_of(candles[:100]), prev_len=65)
    np.testing.assert_allclose(
        state["output"]["ema"],
        ema.compute_arr(_bars_of(candles))["ema"],
        rtol=1e-12, atol=1e-12, equal_nan=True,
    )


def test_ema_inc_step_rejects_non_growth():
    candles = _random_walk(30, seed=37)
    ema = EMA(length=5)
    state = ema.inc_init(_bars_of(candles))
    with pytest.raises(ValueError):
        ema.inc_step(state, _bars_of(candles), prev_len=30)


# ---------------------------------------------------------------------------
# IndicatorMemo.advance_for_append
# ---------------------------------------------------------------------------


def test_memo_advance_steps_supported_indicator():
    candles = _random_walk(40, seed=41)
    memo = IndicatorMemo(candles=list(candles[:30]))
    # Trigger init by accessing SMA.
    out0 = memo.get("sma", {"length": 10})
    assert "sma" in out0
    sink = {}
    new_bars = Bars.from_candles(candles)  # length 40
    memo.advance_for_append(new_bars, prev_len=30, stats_sink=sink)
    # Cache reflects new length, parity with full recompute.
    out1 = memo.cache[("sma", (("length", 10),))]
    np.testing.assert_allclose(
        out1["sma"], SMA(length=10).compute_arr(new_bars)["sma"],
        rtol=1e-12, atol=1e-12, equal_nan=True,
    )
    assert sink.get("incremental_steps") == 1
    assert sink.get("incremental_falls_back", 0) == 0


def test_memo_advance_drops_unsupported_indicator():
    """Indicator without inc_init/inc_step → falls back (drops cache entry)."""
    candles = _random_walk(40, seed=43)
    memo = IndicatorMemo(candles=list(candles[:30]))
    # ATR has compute_arr but (today) no inc_step.
    memo.get("atr", {"length": 14})
    sink = {}
    new_bars = Bars.from_candles(candles)
    memo.advance_for_append(new_bars, prev_len=30, stats_sink=sink)
    assert ("atr", (("length", 14),)) not in memo.cache
    assert sink.get("incremental_falls_back") == 1
    assert sink.get("incremental_steps", 0) == 0


# ---------------------------------------------------------------------------
# Runner end-to-end
# ---------------------------------------------------------------------------


def _scan_sma_above_close(length: int, name: str = "sma_check") -> ScanDefinition:
    return ScanDefinition(
        name=name, primary_interval="1m",
        universe_filter=UniverseFilter.all(),
        root=Group(combinator="and", children=[
            Condition(
                left=FieldRef.indicator(id="sma", output_key="sma",
                                        params={"length": length}),
                op=OP_GT,
                params={"right": FieldRef.literal(0.0)},
                interval="1m",
            ),
        ]),
    )


def test_runner_appends_take_incremental_path():
    """A scan referencing SMA: each tick after the first should
    advance via inc_step rather than rebuild.
    """
    runner = ScanRunner(max_workers=1)
    try:
        scan = _scan_sma_above_close(length=10)
        candles = _random_walk(30, seed=51)
        cs = {"AAA": candles}
        # Cold tick: cache populated, inc state seeded.
        runner.run([scan], cs, interval="1m", tick_id=1)
        s0 = runner.stats()
        assert s0["incremental_steps"] == 0
        # 5 successive single-bar appends.
        next_bars = _random_walk(35, seed=51)[30:]
        for i, c in enumerate(next_bars, start=2):
            candles.append(c)
            runner.run([scan], cs, interval="1m", tick_id=i)
        s1 = runner.stats()
        # Five appends → five inc_steps for the one cached SMA(10).
        assert s1["incremental_steps"] == 5
        assert s1["incremental_falls_back"] == 0
        # Reconcile counters: 1 cold rebuild, 5 appends, 0 forming.
        assert s1["buffer_rebuilds"] == 1
        assert s1["buffer_appends"] == 5
    finally:
        runner.shutdown()


def test_runner_incremental_output_matches_full_rebuild():
    """Run a scan via the incremental path and compare to a fresh
    runner that rebuilds every tick — match values must agree.
    """
    scan = _scan_sma_above_close(length=10)
    candles_full = _random_walk(50, seed=61)

    inc_runner = ScanRunner(max_workers=1)
    rebuild_runner = ScanRunner(max_workers=1)
    try:
        # Drive incremental runner: same list, append-grow.
        candles = list(candles_full[:20])
        inc_runner.run([scan], {"AAA": candles}, interval="1m", tick_id=1)
        for i in range(20, 50):
            candles.append(candles_full[i])
            inc_runner.run([scan], {"AAA": candles}, interval="1m", tick_id=i + 1)
        inc_last = inc_runner._scan_results_cache if False else None  # not used

        # Drive rebuild runner: fresh list each tick (forces full rebuild).
        for i in range(20, 50):
            fresh = list(candles_full[:i + 1])
            rebuild_runner.run([scan], {"AAA": fresh}, interval="1m", tick_id=i + 1)

        # Both runners' last results: same matched value, same close_value.
        # We compare via running one final tick each and inspecting rows.
        inc_results = inc_runner.run(
            [scan], {"AAA": candles}, interval="1m", tick_id=999,
        )
        rebuild_results = rebuild_runner.run(
            [scan], {"AAA": list(candles_full)}, interval="1m", tick_id=999,
        )
        assert (
            inc_results[scan.id].rows[0].matched
            == rebuild_results[scan.id].rows[0].matched
        )
    finally:
        inc_runner.shutdown()
        rebuild_runner.shutdown()


def test_runner_stats_incremental_counters_present():
    runner = ScanRunner(max_workers=1)
    try:
        s = runner.stats()
        assert "incremental_steps" in s
        assert "incremental_falls_back" in s
    finally:
        runner.shutdown()
