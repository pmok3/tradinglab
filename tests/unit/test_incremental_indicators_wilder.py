"""Incremental-protocol parity for the Wilder-family indicators (RSI, ATR).

compute #3: a closed-bar append extends these O(k) via ``inc_step`` instead
of a full O(N) recompute. The incremental path must match the full
``compute_arr`` (within float64 round-off — the kernel is causal so the
cached prefix is exact; only the appended bars differ). Mirrors the SMA/EMA
parity tests in ``test_incremental_indicators.py``.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from tradinglab.core.bars import Bars
from tradinglab.indicators.atr import ATR
from tradinglab.indicators.cache import IndicatorCache
from tradinglab.indicators.rsi import RSI
from tradinglab.models import Candle

_ET = ZoneInfo("America/New_York")


def _random_walk(n: int, *, seed: int) -> list[Candle]:
    rng = np.random.default_rng(seed)
    base = datetime(2024, 6, 3, 9, 30, tzinfo=_ET)
    out: list[Candle] = []
    price = 100.0
    for i in range(n):
        o = price
        c = price + float(rng.normal(0, 1.0))
        hi = max(o, c) + abs(float(rng.normal(0, 0.4)))
        lo = min(o, c) - abs(float(rng.normal(0, 0.4)))
        out.append(Candle(date=base + timedelta(minutes=i), open=o, high=hi,
                          low=lo, close=c, volume=1000 + i, session="regular"))
        price = c
    return out


def _bars(candles: list[Candle]) -> Bars:
    return Bars.from_candles(candles)


def _key(ind) -> str:
    return "rsi" if isinstance(ind, RSI) else "atr"


def _single_append_parity(ind, candles, init_len, *, rtol=1e-9, atol=1e-9):
    key = _key(ind)
    full = ind.compute_arr(_bars(candles))[key]
    state = ind.inc_init(_bars(candles[:init_len]))
    assert state.get("seeded") is True
    cur = init_len
    for target in range(init_len + 1, len(candles) + 1):
        state = ind.inc_step(state, _bars(candles[:target]), prev_len=cur)
        cur = target
    inc = state["output"][key]
    np.testing.assert_allclose(inc, full, rtol=rtol, atol=atol, equal_nan=True)


def _multi_append_parity(ind, candles, init_len, chunks, *, rtol=1e-9, atol=1e-9):
    key = _key(ind)
    full = ind.compute_arr(_bars(candles))[key]
    state = ind.inc_init(_bars(candles[:init_len]))
    cur = init_len
    for target in chunks:
        state = ind.inc_step(state, _bars(candles[:target]), prev_len=cur)
        cur = target
    inc = state["output"][key]
    np.testing.assert_allclose(inc[:cur], full[:cur], rtol=rtol, atol=atol, equal_nan=True)


# --- RSI -------------------------------------------------------------------

@pytest.mark.parametrize("length,seed", [(14, 1), (7, 2), (21, 3)])
def test_rsi_single_append_parity(length, seed):
    _single_append_parity(RSI(length=length), _random_walk(200, seed=seed),
                           init_len=4 * length + 10)


def test_rsi_multi_bar_catchup():
    _multi_append_parity(RSI(length=14), _random_walk(220, seed=4),
                         init_len=80, chunks=[120, 175, 220])


def test_rsi_inc_init_output_matches_compute_arr():
    candles = _random_walk(150, seed=5)
    ind = RSI(length=14)
    st = ind.inc_init(_bars(candles))
    np.testing.assert_array_equal(st["output"]["rsi"], ind.compute_arr(_bars(candles))["rsi"])


def test_rsi_inc_step_rejects_non_growth():
    candles = _random_walk(100, seed=6)
    ind = RSI(length=14)
    st = ind.inc_init(_bars(candles[:80]))
    with pytest.raises(ValueError):
        ind.inc_step(st, _bars(candles[:80]), prev_len=80)


def test_rsi_unseeded_inc_step_raises():
    # init below the seed window → seeded False → inc_step defers to full.
    candles = _random_walk(40, seed=7)
    ind = RSI(length=14)
    st = ind.inc_init(_bars(candles[:10]))
    assert st.get("seeded") is False
    with pytest.raises(ValueError):
        ind.inc_step(st, _bars(candles[:20]), prev_len=10)


# --- ATR -------------------------------------------------------------------

@pytest.mark.parametrize("length,seed", [(14, 11), (10, 12), (20, 13)])
def test_atr_single_append_parity(length, seed):
    _single_append_parity(ATR(length=length), _random_walk(200, seed=seed),
                           init_len=4 * length + 10)


def test_atr_multi_bar_catchup():
    _multi_append_parity(ATR(length=14), _random_walk(220, seed=14),
                         init_len=80, chunks=[130, 200, 220])


def test_atr_inc_init_output_matches_compute_arr():
    candles = _random_walk(150, seed=15)
    ind = ATR(length=14)
    st = ind.inc_init(_bars(candles))
    np.testing.assert_array_equal(st["output"]["atr"], ind.compute_arr(_bars(candles))["atr"])


def test_atr_non_rma_config_defers_to_full():
    # SMA-smoothed ATR is not incremental → inc_init unseeded, inc_step raises.
    candles = _random_walk(120, seed=16)
    ind = ATR(length=14, ma_type="SMA")
    st = ind.inc_init(_bars(candles[:80]))
    assert st.get("seeded") is False
    with pytest.raises(ValueError):
        ind.inc_step(st, _bars(candles[:100]), prev_len=80)


def test_atr_tod_mode_defers_to_full():
    candles = _random_walk(120, seed=17)
    ind = ATR(length=20, mode="tod")
    st = ind.inc_init(_bars(candles[:90]))
    assert st.get("seeded") is False
    with pytest.raises(ValueError):
        ind.inc_step(st, _bars(candles[:110]), prev_len=90)


# --- end-to-end through the IndicatorCache ---------------------------------

@pytest.mark.parametrize("ind_factory", [lambda: RSI(length=14), lambda: ATR(length=14)])
def test_cache_incremental_matches_full_rebuild(ind_factory):
    candles = _random_walk(180, seed=21)
    ind = ind_factory()
    key = _key(ind)
    cache = IndicatorCache()
    hkey = "h"
    # Prime at 120 bars, then grow to 150 then 180 through the cache's
    # incremental hook; compare to a from-scratch full compute at 180.
    growing = list(candles[:120])
    cache.get_or_compute_incremental(growing, hkey, ind, _bars(growing))
    for n in (150, 180):
        growing.extend(candles[len(growing):n])
        res = cache.get_or_compute_incremental(growing, hkey, ind, _bars(growing))
    full = ind.compute_arr(_bars(candles))[key]
    np.testing.assert_allclose(res[key], full, rtol=1e-9, atol=1e-9, equal_nan=True)
