"""Meta-tests: indicators use vectorized + incremental implementations where
possible.

Three guards, all registry-driven so a NEW indicator is forced to make a
conscious choice:

1. ``test_incremental_protocol_parity`` — for EVERY registered indicator that
   declares ``inc_init`` + ``inc_step``, drive a growth sequence exactly the
   way ``IndicatorCache`` does (try ``inc_step``; on raise, fall back to a full
   ``compute_arr``) and assert the running result matches a from-scratch full
   compute at every length. Catches an incremental implementation that drifts
   from its own ``compute_arr``.

2. ``test_incremental_coverage_is_classified`` — every registered indicator is
   in ``_INCREMENTAL_EXPECTED`` (True = should support the protocol, with a
   rationale for the False entries), and reality (``hasattr inc_step``) matches.
   Adding an indicator fails this until it is classified.

3. ``test_compute_arr_has_no_unjustified_per_bar_loop`` — each indicator's
   ``compute_arr`` body is numpy-vectorized; any ``for``-loop must be declared
   in ``_LOOP_ALLOWLIST`` with the reason it cannot vectorize.
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from tradinglab.core.bars import Bars
from tradinglab.indicators import base as ind_base
from tradinglab.models import Candle

_ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Shared fixture: several weekdays of intraday RTH bars (ET-aligned,
# session="regular") so session/daily-reset/VWAP indicators all hydrate.
# ---------------------------------------------------------------------------

def _rth_intraday_candles(days: int = 5, per_day: int = 78, seed: int = 7) -> list[Candle]:
    rng = np.random.default_rng(seed)
    out: list[Candle] = []
    price = 100.0
    day = datetime(2024, 6, 3, 9, 30, tzinfo=_ET)  # Monday
    for d in range(days):
        start = day + timedelta(days=d)
        # skip weekends
        while start.weekday() >= 5:
            start = start + timedelta(days=1)
        t = start
        for _ in range(per_day):
            o = price
            c = price + float(rng.normal(0, 0.6))
            hi = max(o, c) + abs(float(rng.normal(0, 0.25)))
            lo = min(o, c) - abs(float(rng.normal(0, 0.25)))
            out.append(Candle(date=t, open=o, high=hi, low=lo, close=c,
                              volume=1000 + int(rng.integers(0, 500)),
                              session="regular"))
            price = c
            t = t + timedelta(minutes=5)
    return out


_CANDLES = _rth_intraday_candles()


def _bars(candles) -> Bars:
    return Bars.from_candles(candles)


def _all_kind_ids() -> list[str]:
    """Every kind_id known to the registry (built-ins + legacy SMA/EMA)."""
    return sorted(ind_base._BY_KIND_ID.keys())


def _instantiate(kid: str):
    entry = ind_base.factory_by_kind_id(kid)
    if entry is None:
        return None
    _name, factory = entry
    try:
        return factory()
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# 1. Incremental-protocol parity (cache-faithful)
# ---------------------------------------------------------------------------

def _incremental_kind_ids() -> list[str]:
    out = []
    for kid in _all_kind_ids():
        inst = _instantiate(kid)
        if inst is not None and callable(getattr(inst, "inc_init", None)) \
                and callable(getattr(inst, "inc_step", None)):
            out.append(kid)
    return out


@pytest.mark.parametrize("kid", _incremental_kind_ids())
def test_incremental_protocol_parity(kid):
    inst = _instantiate(kid)
    assert inst is not None
    candles = _CANDLES
    n_total = len(candles)
    warmup = int(getattr(inst, "warmup_bars", 0) or 0)
    init_len = min(max(warmup + 5, 60), n_total - 30)
    assert init_len < n_total

    state = inst.inc_init(_bars(candles[:init_len]))
    assert isinstance(state, dict) and isinstance(state.get("output"), dict)
    cur = init_len
    successes = 0
    # Step in small chunks (mix of single + multi-bar appends) to exercise
    # both paths, mimicking IndicatorCache.get_or_compute_incremental.
    targets = list(range(init_len + 1, n_total + 1, 7))
    if targets[-1] != n_total:
        targets.append(n_total)
    for target in targets:
        try:
            new_state = inst.inc_step(state, _bars(candles[:target]), prev_len=cur)
            assert isinstance(new_state, dict) and isinstance(new_state.get("output"), dict)
            state = new_state
            successes += 1
        except Exception:
            # Cache contract: inc_step may refuse → caller does a full compute.
            state = inst.inc_init(_bars(candles[:target]))
        cur = target
        full = inst.compute_arr(_bars(candles[:target]))
        for key, ref in full.items():
            got = state["output"].get(key)
            assert got is not None, f"{kid}: missing output key {key!r}"
            np.testing.assert_allclose(
                got, ref, rtol=1e-6, atol=1e-6, equal_nan=True,
                err_msg=f"{kid}: inc output drifted from compute_arr['{key}'] at len={target}",
            )
    # A declared-incremental indicator must take the fast path at least
    # sometimes on this clean intraday data (else it's effectively dead).
    assert successes >= 1, f"{kid}: inc_step never succeeded — fast path is dead"


# ---------------------------------------------------------------------------
# 2. Incremental coverage classification
# ---------------------------------------------------------------------------

#: Expected incremental-protocol support per kind_id. True = inc_init/inc_step
#: implemented; False = intentionally NOT incremental (rationale required).
_INCREMENTAL_EXPECTED: dict[str, bool] = {
    # --- incremental (recurrence / cumulative / rolling) ---
    "sma": True,
    "ema": True,
    "ma": True,         # unified MovingAverage (SMA/EMA on Close)
    "rsi": True,        # Wilder recurrence
    "atr": True,        # Wilder recurrence (rolling RMA form)
    "macd": True,       # chained EMAs
    "bbands": True,     # SMA + rolling sums
    "adx": True,        # chained Wilder recurrences
    "vwap": True,       # session-cumulative
    # --- NOT incremental (rationale) ---
    # Keltner: EMA basis is incremental but the ATR band uses the same
    #   rolling machinery; deferred (compound, lower priority).
    "keltner": False,
    # Anchored VWAP: anchor can move arbitrarily (user re-anchors) → the
    #   cumulative window is not append-only. Welford from a fixed anchor
    #   is a future inc candidate (compute #6).
    "avwap": False,
    # SMI: double-smoothed stochastic; vectorized but the inc state is a
    #   2-stage EMA cascade — deferred.
    "smi": False,
    # Laguerre RSI: 4-stage Laguerre filter cascade — deferred.
    "lrsi": False,
    # RVOL / RRVOL: session-bucketed / cross-symbol relative volume; the
    #   time-of-day buckets are not a simple append recurrence.
    "rvol": False,
    "rrvol": False,
    # Chandelier: rolling HH/LL ratchet + ATR; window-extrema is not a
    #   first-order recurrence.
    "chandelier": False,
    # Prior Day H/L/C: step function keyed on the prior completed session;
    #   trivially cheap, no per-tick recompute pressure.
    "prior_day_hlc": False,
    # Overlap Score: windowed overlap statistic — deferred.
    "overlap_score_inv": False,
}


def test_incremental_coverage_is_classified():
    known = set(_all_kind_ids())
    classified = set(_INCREMENTAL_EXPECTED.keys())
    missing = known - classified
    assert not missing, (
        f"new indicator(s) not classified in _INCREMENTAL_EXPECTED: {sorted(missing)} — "
        "add the kind_id with True (implement inc_init/inc_step) or False (+ rationale)."
    )
    stale = classified - known
    assert not stale, f"_INCREMENTAL_EXPECTED references unknown kind_id(s): {sorted(stale)}"
    mismatches = []
    for kid, expected in _INCREMENTAL_EXPECTED.items():
        inst = _instantiate(kid)
        if inst is None:
            continue
        actual = callable(getattr(inst, "inc_init", None)) and callable(
            getattr(inst, "inc_step", None))
        if actual != expected:
            mismatches.append(f"{kid}: expected inc={expected}, actual inc={actual}")
    assert not mismatches, "incremental coverage mismatch:\n" + "\n".join(mismatches)


# ---------------------------------------------------------------------------
# 3. Vectorization: no un-justified per-bar loop in compute_arr
# ---------------------------------------------------------------------------

#: kind_id → reason its ``compute_arr`` contains a ``for``-loop that genuinely
#: cannot vectorize over the bar dimension. New looping indicators must justify
#: themselves here. (This checks ``compute_arr`` itself; O(#days) helper loops
#: like RVOL/ATR time-of-day bucketing live outside ``compute_arr`` and are not
#: flagged.)
_LOOP_ALLOWLIST: dict[str, str] = {
    "vwap": "per-session cumulative loop is O(#days), not O(#bars)",
    "avwap": "anchored running-Welford variance is a sequential recurrence",
    "prior_day_hlc": "per-day group loop is O(#days)",
}


def _compute_arr_has_for_loop(inst) -> bool:
    fn = getattr(type(inst), "compute_arr", None)
    if fn is None:
        return False
    import textwrap
    try:
        src = textwrap.dedent(inspect.getsource(fn))
    except (OSError, TypeError):
        return False
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.AsyncFor)):
            return True
    return False


def test_compute_arr_has_no_unjustified_per_bar_loop():
    offenders = []
    for kid in _all_kind_ids():
        inst = _instantiate(kid)
        if inst is None:
            continue
        if _compute_arr_has_for_loop(inst) and kid not in _LOOP_ALLOWLIST:
            offenders.append(kid)
    assert not offenders, (
        "compute_arr contains a per-bar Python for-loop (vectorize it, or add to "
        f"_LOOP_ALLOWLIST with a reason): {sorted(offenders)}"
    )
