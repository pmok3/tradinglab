"""Tests for the per-BarsNp ``_days_for`` cache backing ``_today_mask``.

Pins the perf fix that retired the O(N) ``astype("datetime64[D]")``
recompute previously done per call by ``_today_mask`` /
``_b_bars_since_open``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from tradinglab.models import Candle
from tradinglab.scanner.fields import BarsNp, _days_cache, _days_for, _today_mask


def _make_candles(n: int, *, start: datetime, interval_min: int = 5,
                  session: str = "regular") -> list[Candle]:
    out: list[Candle] = []
    for i in range(n):
        ts = start + timedelta(minutes=i * interval_min)
        c = 100.0 + i
        out.append(Candle(date=ts, open=c - 0.5, high=c + 1.0,
                          low=c - 1.0, close=c, volume=1000 + i,
                          session=session))
    return out


# ---------------------------------------------------------------------------
# Correctness — _today_mask must behave exactly like the pre-cache version
# ---------------------------------------------------------------------------


def _reference_mask(b: BarsNp, i: int) -> np.ndarray | None:
    """Pre-cache reference implementation (recomputes astype each call)."""
    if i < 0 or i >= b.timestamps.size:
        return None
    today = b.timestamps[i].astype("datetime64[D]")
    days = b.timestamps.astype("datetime64[D]")
    mask = days == today
    mask[i + 1:] = False
    return mask


def test_today_mask_single_day_matches_reference():
    candles = _make_candles(8, start=datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc))
    b = BarsNp.from_candles(candles)
    for i in range(len(b)):
        np.testing.assert_array_equal(_today_mask(b, i), _reference_mask(b, i))


def test_today_mask_multi_day_matches_reference():
    # 3 days × 5 bars/day spaced 5 hours apart (forces day rollover).
    candles = _make_candles(15, start=datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc),
                            interval_min=5 * 60)
    b = BarsNp.from_candles(candles)
    for i in range(len(b)):
        np.testing.assert_array_equal(_today_mask(b, i), _reference_mask(b, i))


def test_today_mask_empty_bars_returns_none():
    b = BarsNp.from_candles([])
    assert _today_mask(b, 0) is None
    assert _days_for(b).size == 0


def test_today_mask_last_bar_of_multi_day():
    candles = _make_candles(15, start=datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc),
                            interval_min=5 * 60)
    b = BarsNp.from_candles(candles)
    last = len(b) - 1
    mask = _today_mask(b, last)
    ref = _reference_mask(b, last)
    np.testing.assert_array_equal(mask, ref)
    # No look-ahead past last (trivially true at last index).
    assert mask.sum() >= 1


def test_today_mask_out_of_bounds_returns_none():
    b = BarsNp.from_candles(_make_candles(3, start=datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc)))
    assert _today_mask(b, -1) is None
    assert _today_mask(b, 99) is None


# ---------------------------------------------------------------------------
# Cache mechanics
# ---------------------------------------------------------------------------


def test_days_for_cache_hit_returns_same_object():
    """Two calls on the same BarsNp must return the IDENTICAL ndarray."""
    _days_cache.clear()
    b = BarsNp.from_candles(_make_candles(10, start=datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc)))
    a1 = _days_for(b)
    a2 = _days_for(b)
    assert a1 is a2


def test_days_for_cache_miss_different_bars_objects():
    """Different BarsNp instances must produce different cached arrays."""
    _days_cache.clear()
    b1 = BarsNp.from_candles(_make_candles(10, start=datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc)))
    b2 = BarsNp.from_candles(_make_candles(10, start=datetime(2026, 5, 5, 9, 30, tzinfo=timezone.utc)))
    a1 = _days_for(b1)
    a2 = _days_for(b2)
    assert a1 is not a2
    # And contents differ (different start dates).
    assert a1[0] != a2[0]


def test_days_for_content_correct():
    """Cached array must equal astype('datetime64[D]') of timestamps."""
    _days_cache.clear()
    candles = _make_candles(12, start=datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc),
                            interval_min=4 * 60)
    b = BarsNp.from_candles(candles)
    expected = b.timestamps.astype("datetime64[D]")
    np.testing.assert_array_equal(_days_for(b), expected)
