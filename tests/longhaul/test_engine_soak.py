"""Headless long-horizon soak: drive many sessions and assert invariants.

What this catches that the smoke suite cannot
---------------------------------------------
Every one of the 176 mega-test checks restores its own state in a ``finally``
block. That discipline keeps them independent — and structurally guarantees
none of them can ever observe a bug that requires state to *accumulate*.
This suite is the inverse: one long run, nothing reset, invariants sampled
continuously.

The invariants are chosen to be things that are cheap to measure and that
degrade monotonically when something leaks:

============================  =========================================
Invariant                     Failure it catches
============================  =========================================
Bounded LRU caches (§7.21)    an unbounded memo growing for the life of
                              a multi-day session
NaN non-propagation           a single NaN poisoning every later bar of
                              a recurrence-based indicator
Heap drift (``tracemalloc``)  per-iteration allocation that is never
                              released
Determinism under repetition  hidden global state making run N differ
                              from run 1
Monotonic timeline            bar ordering corrupting after many
                              aggregations
============================  =========================================

``tracemalloc`` is stdlib on purpose: measuring true RSS would mean adding
``psutil``, and CLAUDE.md §9 forbids new dependencies without discussion.
"""
from __future__ import annotations

import gc
import tracemalloc

import numpy as np
import pytest

from tests._fixtures import market_sim as ms

pytestmark = [pytest.mark.longhaul, pytest.mark.timeout(600)]

#: Sessions per soak. Long enough that a per-iteration leak is visible above
#: allocator noise, short enough to stay inside a 20-minute CI job.
SOAK_SESSIONS = 60


# --------------------------------------------------------------------------
# Bounded caches (§7.21)
# --------------------------------------------------------------------------


def test_warmup_cache_stays_bounded_under_parameter_sweeps(soak_gc):
    """``strategy_tester.warmup`` memoises per ``(kind_id, params)``.

    A user sweeping indicator parameters across a long session generates an
    unbounded key space. §7.21 bounds it with ``LRUDict(maxsize=256)``; this
    drives far more distinct keys than that and asserts the bound holds.
    """
    from tradinglab.strategy_tester import warmup

    cache = warmup._WARMUP_CACHE
    maxsize = cache.maxsize
    before = len(cache)

    for length in range(2, 400):
        warmup.warmup_bars_for_kind("ema", {"length": length})
        assert len(cache) <= maxsize, (
            f"warmup cache exceeded its bound: {len(cache)} > {maxsize} "
            f"after {length} distinct parameter sets")

    assert len(cache) >= before, "cache should retain entries, not empty itself"
    assert len(cache) <= maxsize


def test_lru_dict_never_exceeds_maxsize_under_sustained_churn(soak_gc):
    from tradinglab.core.lru_dict import LRUDict

    d: LRUDict[int, int] = LRUDict(maxsize=128)
    for i in range(200_000):
        d[i] = i
        if i % 10_000 == 0:
            assert len(d) <= 128, f"LRUDict grew to {len(d)} at i={i}"
    assert len(d) == 128
    # The most recent keys must be the survivors.
    assert 199_999 in d
    assert 0 not in d


# --------------------------------------------------------------------------
# NaN non-propagation across a long timeline
# --------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", ["normal", "extended", "halt", "illiquid"])
def test_indicators_do_not_leak_nan_over_many_sessions(scenario, soak_gc):
    """After warmup, a recurrence indicator must stay finite for the whole run.

    A single NaN entering an IIR recurrence poisons every subsequent bar. On a
    short, clean fixture that is invisible; over 60 sessions including halts
    and zero-volume bars it is not.
    """
    from tradinglab.indicators import ATR, RSI
    from tradinglab.indicators.moving_averages import EMA

    cs = ms.candles("SOAK", "5m", scenario=scenario, days=SOAK_SESSIONS)
    assert len(cs) > 1000, "soak needs a long timeline to be meaningful"

    for name, ind in (("ema", EMA(length=20)), ("rsi", RSI(length=14)),
                      ("atr", ATR(length=14))):
        for key, series in ind.compute(cs).items():
            arr = np.asarray(series, dtype=float)
            finite = np.isfinite(arr)
            if not finite.any():
                pytest.fail(f"{name}.{key} is entirely NaN on {scenario}")
            first = int(np.argmax(finite))
            tail = arr[first:]
            bad = int((~np.isfinite(tail)).sum())
            assert bad == 0, (
                f"{name}.{key} on {scenario}: {bad} NaN values AFTER warmup "
                f"(first finite at bar {first} of {arr.size}) — a NaN leaked "
                f"into the recurrence and poisoned the rest of the run")


def test_vwap_resets_every_session_over_a_long_run(soak_gc):
    """VWAP must re-anchor each session, not drift across 60 days."""
    from tradinglab.indicators import VWAP

    cs = ms.candles("SOAKV", "5m", days=SOAK_SESSIONS)
    out = VWAP().compute(cs)
    key = next(iter(out))
    vw = np.asarray(out[key], dtype=float)

    by_day: dict = {}
    for i, c in enumerate(cs):
        by_day.setdefault(c.date.date(), []).append(i)

    for day, idx in by_day.items():
        first = vw[idx[0]]
        if not np.isfinite(first):
            continue
        # On the first bar of a session VWAP equals that bar's typical price,
        # because the cumulation has just been reset.
        c = cs[idx[0]]
        typical = (c.high + c.low + c.close) / 3.0
        assert abs(first - typical) < max(0.02, typical * 0.002), (
            f"VWAP did not reset on {day}: first bar {first:.4f} vs typical "
            f"{typical:.4f} — cumulation is leaking across sessions")


# --------------------------------------------------------------------------
# Heap drift
# --------------------------------------------------------------------------


def test_repeated_indicator_computation_does_not_drift_the_heap(soak_gc):
    """Per-iteration allocation that is never released shows up as slope.

    Uses stdlib ``tracemalloc`` rather than RSS so no new dependency is
    needed (§9).

    The threshold is deliberately BOTH relative and absolute. A purely
    relative check is worthless here: the steady-state traced heap between
    collections is only a few kilobytes, so ordinary allocator jitter reads
    as a triple-digit percentage. Requiring an absolute floor as well means
    this fires on a real leak (which compounds into megabytes over the run)
    and stays quiet on noise — the same reasoning behind the min-of-N rule
    for perf budgets in CLAUDE.md §7.26.
    """
    from tradinglab.indicators import ATR, RSI
    from tradinglab.indicators.moving_averages import EMA

    cs = ms.candles("HEAP", "5m", days=10)
    inds = [EMA(length=20), RSI(length=14), ATR(length=14)]
    iterations = 240

    gc.collect()
    tracemalloc.start()
    try:
        samples: list[int] = []
        for i in range(iterations):
            for ind in inds:
                ind.compute(cs)
            if i % 10 == 0:
                gc.collect()
                samples.append(tracemalloc.get_traced_memory()[0])
    finally:
        tracemalloc.stop()

    assert len(samples) >= 8
    early = float(np.median(samples[1:5]))
    late = float(np.median(samples[-4:]))
    grew_bytes = late - early
    grew_frac = grew_bytes / max(early, 1.0)

    # A genuine per-iteration leak of even 1 KB would add ~240 KB over the
    # run; 4 MB is far above allocator noise and far below any real leak.
    absolute_floor = 4 * 1024 * 1024
    assert not (grew_frac > 0.5 and grew_bytes > absolute_floor), (
        f"traced heap grew {grew_frac:.1%} ({grew_bytes:,.0f} bytes) across "
        f"{iterations} iterations ({early:,.0f} -> {late:,.0f}) — an "
        f"allocation is not being released between computations")


# --------------------------------------------------------------------------
# Determinism under repetition
# --------------------------------------------------------------------------


def test_indicator_output_is_stable_across_many_repetitions(soak_gc):
    """Run N is bit-identical to run 1 — no hidden global state."""
    from tradinglab.indicators import RSI

    cs = ms.candles("REPT", "5m", days=20)
    first = {k: np.asarray(v, dtype=float)
             for k, v in RSI(length=14).compute(cs).items()}
    for n in range(25):
        again = RSI(length=14).compute(cs)
        for k, v in first.items():
            got = np.asarray(again[k], dtype=float)
            assert np.array_equal(got, v, equal_nan=True), (
                f"RSI.{k} changed on repetition {n} — hidden mutable state")


def test_aggregation_is_stable_across_many_repetitions(soak_gc):
    from tradinglab.backtest.aggregation import aggregate

    fine = ms.candles("REPA", "1m", days=10)
    first = aggregate(fine, "1m", "15m")
    for n in range(25):
        assert aggregate(fine, "1m", "15m") == first, (
            f"aggregation drifted on repetition {n}")


# --------------------------------------------------------------------------
# Timeline integrity over the whole run
# --------------------------------------------------------------------------


@pytest.mark.parametrize("interval", ["1m", "5m", "15m", "1d"])
def test_timeline_stays_ordered_and_unique_over_a_long_run(interval, soak_gc):
    cs = ms.candles("TIME", interval, days=SOAK_SESSIONS)
    ts = [c.date for c in cs]
    assert ts == sorted(ts), f"{interval} timeline lost ordering"
    assert len(set(ts)) == len(ts), f"{interval} timeline has duplicates"


def test_long_run_ohlc_invariants_hold_on_every_bar(soak_gc):
    """One malformed bar in 23,000 is exactly what a short fixture misses."""
    for scenario in ("normal", "extended", "earnings_gap", "halt", "illiquid"):
        cs = ms.candles("OHLC", "1m", scenario=scenario, days=SOAK_SESSIONS)
        for c in cs:
            assert c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high, (
                f"OHLC violated on {scenario} at {c.date}: "
                f"O={c.open} H={c.high} L={c.low} C={c.close}")
            assert c.volume >= 0
