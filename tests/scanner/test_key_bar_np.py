"""Equivalence + caching tests for the BarsNp-native key-bar entry point.

Locks in two properties:

1. ``compute_key_bar_arrays_np(BarsNp.from_candles(c))`` produces results
   numerically identical to ``compute_key_bar_arrays(c)`` on both
   intraday and daily fixtures (so swapping in the np-native path inside
   the scanner cache cannot change scan output).
2. The shared :class:`BarsKeyedCache` correctly memoizes per-snapshot
   and evicts under LRU pressure.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List

import numpy as np
import pytest

from tradinglab.core.key_bar import (
    KEY_BAR_UNKNOWN,
    compute_key_bar_arrays,
    compute_key_bar_arrays_np,
)
from tradinglab.models import Candle
from tradinglab.scanner._bars_cache import BarsKeyedCache
from tradinglab.scanner.fields import BarsNp


def _intraday_candles(n: int, *, start_hour: int = 9) -> List[Candle]:
    base = datetime(2024, 1, 8, start_hour, 30)
    out: List[Candle] = []
    price = 100.0
    for i in range(n):
        ts = base + timedelta(minutes=5 * i)
        o = price
        c = o + ((-1) ** i) * 0.4 + (i % 7) * 0.05
        h = max(o, c) + 0.2
        lo = min(o, c) - 0.2
        out.append(Candle(date=ts, open=o, high=h, low=lo, close=c,
                          volume=10_000 + (i * 137 % 5000),
                          session="regular"))
        price = c
    return out


def _daily_candles(n: int) -> List[Candle]:
    base = datetime(2023, 1, 3)
    out: List[Candle] = []
    price = 50.0
    for i in range(n):
        ts = base + timedelta(days=i)
        o = price
        c = o + ((-1) ** i) * 0.6 + (i % 5) * 0.1
        h = max(o, c) + 0.3
        lo = min(o, c) - 0.3
        out.append(Candle(date=ts, open=o, high=h, low=lo, close=c,
                          volume=1_000_000 + (i * 4_321 % 50_000),
                          session="regular"))
        price = c
    return out


def _arrays_equal(a: np.ndarray, b: np.ndarray) -> None:
    assert a.shape == b.shape
    assert a.dtype == b.dtype
    if np.issubdtype(a.dtype, np.floating):
        # NaN-aware comparison
        nan_mask_a = np.isnan(a); nan_mask_b = np.isnan(b)
        assert np.array_equal(nan_mask_a, nan_mask_b)
        assert np.allclose(a[~nan_mask_a], b[~nan_mask_b], atol=1e-12, rtol=0.0)
    else:
        assert np.array_equal(a, b)


def _assert_kb_equal(x, y) -> None:
    _arrays_equal(x.signed, y.signed)
    _arrays_equal(x.bars_since_bull, y.bars_since_bull)
    _arrays_equal(x.bars_since_bear, y.bars_since_bear)
    _arrays_equal(x.last_bull_high, y.last_bull_high)
    _arrays_equal(x.last_bull_low, y.last_bull_low)
    _arrays_equal(x.last_bear_high, y.last_bear_high)
    _arrays_equal(x.last_bear_low, y.last_bear_low)


def test_kb_np_matches_candle_path_intraday() -> None:
    candles = _intraday_candles(80)
    expected = compute_key_bar_arrays(candles)
    got = compute_key_bar_arrays_np(BarsNp.from_candles(candles))
    _assert_kb_equal(got, expected)


def test_kb_np_matches_candle_path_daily() -> None:
    candles = _daily_candles(60)
    expected = compute_key_bar_arrays(candles)
    got = compute_key_bar_arrays_np(BarsNp.from_candles(candles))
    _assert_kb_equal(got, expected)


def test_kb_np_empty() -> None:
    empty = BarsNp.from_candles([])
    out = compute_key_bar_arrays_np(empty)
    assert len(out) == 0


# ---------------------------------------------------------------------------
# BarsKeyedCache primitive
# ---------------------------------------------------------------------------


def test_bars_keyed_cache_memoizes() -> None:
    cache: BarsKeyedCache[int] = BarsKeyedCache(max_size=4)
    bars = BarsNp.from_candles(_daily_candles(5))
    calls = {"n": 0}

    def compute(_b: object) -> int:
        calls["n"] += 1
        return 42

    a = cache.get_or_compute(bars, compute)
    b = cache.get_or_compute(bars, compute)
    assert a == 42 and b == 42
    assert calls["n"] == 1


def test_bars_keyed_cache_extra_key_disambiguates() -> None:
    cache: BarsKeyedCache[int] = BarsKeyedCache(max_size=4)
    bars = BarsNp.from_candles(_daily_candles(5))
    calls = {"n": 0}

    def compute(_b: object) -> int:
        calls["n"] += 1
        return calls["n"]

    v1 = cache.get_or_compute(bars, compute, extra_key=("a",))
    v2 = cache.get_or_compute(bars, compute, extra_key=("b",))
    v1b = cache.get_or_compute(bars, compute, extra_key=("a",))
    assert v1 == 1 and v2 == 2 and v1b == 1
    assert calls["n"] == 2


def test_bars_keyed_cache_lru_eviction() -> None:
    cache: BarsKeyedCache[int] = BarsKeyedCache(max_size=2)
    snaps = [BarsNp.from_candles(_daily_candles(3 + i)) for i in range(3)]
    for i, b in enumerate(snaps):
        cache.get_or_compute(b, lambda _b, k=i: k)
    assert len(cache) == 2
    # Re-touching the most-recent two confirms they're still resident
    # without re-computation
    seen = []
    cache.get_or_compute(snaps[2], lambda _b: (_ for _ in ()).throw(AssertionError("recomputed snap[2]")))
    cache.get_or_compute(snaps[1], lambda _b: (_ for _ in ()).throw(AssertionError("recomputed snap[1]")))
    seen.append("ok")
    assert seen == ["ok"]


def test_bars_keyed_cache_id_recycle_guard() -> None:
    """A new BarsNp at a recycled id must not return the stale value."""
    cache: BarsKeyedCache[int] = BarsKeyedCache(max_size=2)
    b1 = BarsNp.from_candles(_daily_candles(3))
    cache.get_or_compute(b1, lambda _b: 111)
    saved_id = id(b1)
    del b1
    # Force allocation churn until we get a recycled id (best-effort; this
    # almost always hits on CPython within a few iterations).
    for _ in range(64):
        b2 = BarsNp.from_candles(_daily_candles(3))
        if id(b2) == saved_id:
            break
    out = cache.get_or_compute(b2, lambda _b: 222)
    assert out == 222
