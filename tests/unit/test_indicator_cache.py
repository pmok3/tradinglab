"""Unit tests for the Tier-3 perf upgrades to :class:`IndicatorCache`.

Covers:

* **Fingerprint fallback** (perf-2): a fresh list with byte-identical
  OHLCV content hits the cache and returns the previously-computed
  result. A length-changed copy misses (fingerprint includes ``n``).
* **Bars-view length-mismatch eviction** (perf-1 prerequisite):
  ``bars_for`` returns a stale view only when the cached candles
  IS the same list AND lengths match. Grown lists evict.
* **Incremental hook** (perf-1): :meth:`get_or_compute_incremental`
  returns cached arrays on same-id-equal-len, calls ``inc_step`` on
  same-id-grow (result byte-equal / np.allclose to full recompute),
  and falls through to full recompute on shrink or different
  content. Verified for SMA (byte-equal) and EMA (np.allclose).
* **invalidate_for_candles** drops both stores so a later get with
  the same content does NOT hit via fingerprint either.
"""

from __future__ import annotations

import datetime as dt
from typing import List

import numpy as np
import pytest

from tradinglab.indicators.cache import (
    IndicatorCache,
    _candles_fingerprint,
    config_hash,
)
from tradinglab.indicators.moving_averages import EMA, SMA
from tradinglab.core.bars import Bars
from tradinglab.models import Candle


def _mk_candles(n: int, seed: float = 0.0) -> List[Candle]:
    """Deterministic walk; closes at +seed per bar for parity tests."""
    start = dt.datetime(2024, 1, 2, 9, 30, tzinfo=dt.timezone.utc)
    out: List[Candle] = []
    base = 100.0
    for i in range(n):
        op = base + i * 0.1 + seed
        cl = op + 0.5
        hi = max(op, cl) + 0.5
        lo = min(op, cl) - 0.5
        out.append(
            Candle(
                date=start + dt.timedelta(minutes=5 * i),
                open=op, high=hi, low=lo, close=cl,
                volume=1000, session="regular",
            )
        )
    return out


def test_fingerprint_fallback_hits_on_same_content() -> None:
    candles = _mk_candles(60)
    cache = IndicatorCache(capacity=8)
    h = config_hash("sma", {"length": 20})
    sma = SMA(length=20)
    bars = Bars.from_candles(candles)
    r1 = cache.get_or_compute_incremental(candles, h, sma, bars)
    fresh = list(candles)  # new list id, same Candle objects
    r2 = cache.get(fresh, h)
    assert r2 is not None, "fingerprint fallback must hit on same content"
    assert r2 is r1, "should return the same result object"
    # The hit should also re-key under the new id so a 2nd lookup
    # takes the id-fast-path (no fingerprint scan).
    r3 = cache.get(fresh, h)
    assert r3 is r1


def test_fingerprint_misses_on_length_change() -> None:
    candles = _mk_candles(60)
    cache = IndicatorCache(capacity=8)
    h = config_hash("sma", {"length": 20})
    cache.get_or_compute_incremental(
        candles, h, SMA(length=20), Bars.from_candles(candles)
    )
    shorter = candles[:-1]  # different length, different fingerprint
    assert cache.get(shorter, h) is None


def test_fingerprint_helper_handles_empty_and_none() -> None:
    assert _candles_fingerprint(None) is None
    assert _candles_fingerprint([]) is None


def test_bars_for_evicts_on_length_mismatch() -> None:
    candles = _mk_candles(30)
    cache = IndicatorCache(capacity=4)
    bars = cache.bars_for(candles)
    assert len(bars) == 30
    # 2nd call with same list — same view returned (memoized).
    assert cache.bars_for(candles) is bars
    # Grow the list in place — bars_for must NOT return the stale
    # N-element view; it must rebuild against the new length.
    last = candles[-1]
    candles.append(
        Candle(
            date=last.date + dt.timedelta(minutes=5),
            open=last.close, high=last.close + 0.5,
            low=last.close - 0.5, close=last.close + 0.2,
            volume=1000, session="regular",
        )
    )
    bars2 = cache.bars_for(candles)
    assert bars2 is not bars, "stale view must be evicted on length change"
    assert len(bars2) == 31


def test_get_or_compute_incremental_returns_cached_on_equal_len() -> None:
    candles = _mk_candles(60)
    cache = IndicatorCache(capacity=8)
    h = config_hash("sma", {"length": 20})
    sma = SMA(length=20)
    bars = Bars.from_candles(candles)
    r1 = cache.get_or_compute_incremental(candles, h, sma, bars)
    r2 = cache.get_or_compute_incremental(candles, h, sma, bars)
    assert r2 is r1, "same id + equal len must return cached object"


def test_get_or_compute_incremental_sma_growth_matches_full() -> None:
    candles = _mk_candles(60)
    cache = IndicatorCache(capacity=8)
    h = config_hash("sma", {"length": 20})
    sma = SMA(length=20)
    bars = Bars.from_candles(candles)
    cache.get_or_compute_incremental(candles, h, sma, bars)
    # Grow the list in place by 5 bars.
    for k in range(5):
        last = candles[-1]
        candles.append(
            Candle(
                date=last.date + dt.timedelta(minutes=5),
                open=last.close, high=last.close + 0.5,
                low=last.close - 0.5, close=last.close + 0.1,
                volume=1000, session="regular",
            )
        )
    bars2 = Bars.from_candles(candles)
    inc = cache.get_or_compute_incremental(candles, h, sma, bars2)
    full = SMA(length=20).compute(candles)
    # SMA is exact arithmetic — byte-equal.
    assert np.array_equal(inc["sma"], full["sma"], equal_nan=True)


def test_get_or_compute_incremental_ema_growth_matches_full() -> None:
    candles = _mk_candles(60)
    cache = IndicatorCache(capacity=8)
    h = config_hash("ema", {"length": 12})
    ema = EMA(length=12)
    bars = Bars.from_candles(candles)
    cache.get_or_compute_incremental(candles, h, ema, bars)
    for k in range(8):
        last = candles[-1]
        candles.append(
            Candle(
                date=last.date + dt.timedelta(minutes=5),
                open=last.close, high=last.close + 0.7,
                low=last.close - 0.4, close=last.close + 0.3,
                volume=1500, session="regular",
            )
        )
    bars2 = Bars.from_candles(candles)
    inc = cache.get_or_compute_incremental(candles, h, ema, bars2)
    full = EMA(length=12).compute(candles)
    assert np.allclose(inc["ema"], full["ema"], equal_nan=True, atol=1e-10)


def test_get_or_compute_incremental_falls_through_on_shrink() -> None:
    candles = _mk_candles(60)
    cache = IndicatorCache(capacity=8)
    h = config_hash("sma", {"length": 20})
    sma = SMA(length=20)
    bars = Bars.from_candles(candles)
    cache.get_or_compute_incremental(candles, h, sma, bars)
    # Shrink in place — incremental hook can't go backwards, full
    # recompute is required.
    del candles[-5:]
    bars2 = Bars.from_candles(candles)
    inc = cache.get_or_compute_incremental(candles, h, sma, bars2)
    full = SMA(length=20).compute(candles)
    assert np.array_equal(inc["sma"], full["sma"], equal_nan=True)


def test_invalidate_for_candles_clears_both_stores() -> None:
    candles = _mk_candles(40)
    cache = IndicatorCache(capacity=8)
    h = config_hash("sma", {"length": 10})
    cache.get_or_compute_incremental(
        candles, h, SMA(length=10), Bars.from_candles(candles)
    )
    assert cache.get(candles, h) is not None
    cache.invalidate_for_candles(candles)
    assert cache.get(candles, h) is None
    # Fingerprint must also be gone — a fresh list with same content
    # MUST NOT hit (else invalidate is incomplete).
    fresh = list(candles)
    assert cache.get(fresh, h) is None
