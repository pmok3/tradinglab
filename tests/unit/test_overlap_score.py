"""Tests for Overlap Score Inverted indicator."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from tradinglab.core.bars import Bars
from tradinglab.indicators.overlap_score import OverlapScoreInverted
from tradinglab.models import Candle


def _make_candles(prices: list[tuple[float, float]], base_date=None) -> list[Candle]:
    """Build candles from (low, high) pairs."""
    base = base_date or datetime(2024, 3, 4, 9, 30)
    return [
        Candle(date=base + timedelta(minutes=5 * i),
               open=lo, high=hi, low=lo, close=hi,
               volume=1000, session="regular")
        for i, (lo, hi) in enumerate(prices)
    ]


class TestOverlapScoreInverted:

    def test_full_overlap_gives_zero(self):
        """A bar entirely inside prior bars' ranges → OSI = 0."""
        # 10 bars all at [100, 110], then one more at [102, 108] (inside)
        prices = [(100.0, 110.0)] * 11 + [(102.0, 108.0)]
        candles = _make_candles(prices)
        ind = OverlapScoreInverted(lookback=10)
        result = ind.compute_arr(Bars.from_candles(candles))
        osi = result["osi"]
        # Last bar is fully inside every prior bar
        assert osi[-1] == pytest.approx(0.0, abs=0.1)

    def test_zero_overlap_gives_hundred(self):
        """A bar with no overlap with any prior bar → OSI = 100."""
        # 10 bars at [100, 110], then one at [200, 210]
        prices = [(100.0, 110.0)] * 11 + [(200.0, 210.0)]
        candles = _make_candles(prices)
        ind = OverlapScoreInverted(lookback=10)
        result = ind.compute_arr(Bars.from_candles(candles))
        osi = result["osi"]
        assert osi[-1] == pytest.approx(100.0, abs=0.1)

    def test_partial_overlap(self):
        """A bar that partially overlaps should give a value between 0 and 100."""
        # Prior bars at [100, 110], current bar at [105, 115]
        # Overlap with each prior = min(110,115) - max(100,105) = 110-105 = 5
        # Current range = 10, overlap fraction = 0.5
        # So OSI ≈ 50%
        prices = [(100.0, 110.0)] * 11 + [(105.0, 115.0)]
        candles = _make_candles(prices)
        ind = OverlapScoreInverted(lookback=10)
        result = ind.compute_arr(Bars.from_candles(candles))
        osi = result["osi"]
        assert 40.0 < osi[-1] < 60.0

    def test_warmup_is_nan(self):
        """First `lookback` bars should be NaN."""
        prices = [(100.0, 110.0)] * 20
        candles = _make_candles(prices)
        ind = OverlapScoreInverted(lookback=10)
        result = ind.compute_arr(Bars.from_candles(candles))
        osi = result["osi"]
        assert np.all(np.isnan(osi[:10]))
        assert np.all(np.isfinite(osi[10:]))

    def test_output_range(self):
        """All finite values should be in [0, 100]."""
        prices = [(90 + i, 100 + i) for i in range(50)]
        candles = _make_candles(prices)
        ind = OverlapScoreInverted(lookback=10)
        result = ind.compute_arr(Bars.from_candles(candles))
        osi = result["osi"]
        finite = osi[np.isfinite(osi)]
        assert np.all(finite >= 0.0)
        assert np.all(finite <= 100.0)

    def test_empty_input(self):
        """Empty input returns empty array."""
        ind = OverlapScoreInverted()
        result = ind.compute_arr(Bars.from_candles([]))
        assert result["osi"].size == 0

    def test_recency_weighting(self):
        """Recent bars should matter more than distant bars."""
        # 9 bars at [100,110], then 1 bar at [200,210] (recent, no overlap),
        # then current at [100,110]. The distant bars overlap fully,
        # but the most recent bar (200-210) has zero overlap.
        # With recency weighting, OSI should be higher than a flat average.
        prices = [(100.0, 110.0)] * 9 + [(200.0, 210.0)] + [(100.0, 110.0)]
        candles = _make_candles(prices)
        ind = OverlapScoreInverted(lookback=10)
        result = ind.compute_arr(Bars.from_candles(candles))
        osi = result["osi"]
        # Most recent prior bar has zero overlap → boosts OSI
        # If weights were flat, OSI ≈ 10%. With recency weighting, higher.
        assert osi[-1] > 15.0

    def test_doji_handling(self):
        """A doji bar (high == low) should not crash (floored range)."""
        prices = [(100.0, 110.0)] * 11 + [(105.0, 105.0)]  # doji
        candles = _make_candles(prices)
        ind = OverlapScoreInverted(lookback=10)
        result = ind.compute_arr(Bars.from_candles(candles))
        osi = result["osi"]
        assert np.isfinite(osi[-1])

    def test_output_key_matches_style(self):
        """Output keys must match default_style keys."""
        ind = OverlapScoreInverted()
        prices = [(100.0, 110.0)] * 15
        result = ind.compute_arr(Bars.from_candles(_make_candles(prices)))
        assert set(result.keys()) == set(OverlapScoreInverted.default_style.keys())

    def test_kind_id(self):
        assert OverlapScoreInverted.kind_id == "overlap_score_inv"

    def test_overlay_false(self):
        assert OverlapScoreInverted.overlay is False

    def test_reference_levels(self):
        assert 20.0 in OverlapScoreInverted.reference_levels
        assert 80.0 in OverlapScoreInverted.reference_levels

    def test_name(self):
        ind = OverlapScoreInverted(lookback=14)
        assert ind.name == "Overlap(14)"

    def test_compute_candle_api(self):
        """The candle-list API works."""
        prices = [(100.0, 110.0)] * 15
        candles = _make_candles(prices)
        ind = OverlapScoreInverted()
        result = ind.compute(candles)
        assert "osi" in result
        assert result["osi"].size == len(candles)

    def test_trending_gives_high_score(self):
        """Steadily trending bars should produce high OSI (new territory)."""
        # Each bar steps up by 5 — minimal overlap with prior bars
        prices = [(50 + i * 5, 60 + i * 5) for i in range(20)]
        candles = _make_candles(prices)
        ind = OverlapScoreInverted(lookback=5)
        result = ind.compute_arr(Bars.from_candles(candles))
        osi = result["osi"]
        # Later bars should have high OSI (mostly new territory)
        assert np.nanmean(osi[10:]) > 50.0

    def test_consolidation_gives_low_score(self):
        """Bars chopping in the same range should produce low OSI."""
        # All bars in [100, 110]
        prices = [(100.0, 110.0)] * 20
        candles = _make_candles(prices)
        ind = OverlapScoreInverted(lookback=10)
        result = ind.compute_arr(Bars.from_candles(candles))
        osi = result["osi"]
        finite = osi[np.isfinite(osi)]
        assert np.all(finite < 5.0)


def _reference_osi(highs: np.ndarray, lows: np.ndarray, L: int) -> np.ndarray:
    """Hand-rolled Python-loop OSI used as a golden reference.

    Mirrors the original ``compute_arr`` implementation byte-for-byte so
    the vectorized version can be regression-tested against it.
    """
    n = len(highs)
    out = np.full(n, np.nan, dtype=np.float64)
    if n <= L:
        return out
    alpha = max(0.01, 1.0 - 5.0 / (L + 1.0))
    raw_w = np.power(alpha, np.arange(L, dtype=np.float64))
    norm_w = raw_w / raw_w.sum()
    for i in range(L, n):
        hi_i = float(highs[i])
        lo_i = float(lows[i])
        current_range = max(hi_i - lo_i, 0.01)
        weighted = 0.0
        for k in range(L):
            j = i - k - 1
            top = min(hi_i, float(highs[j]))
            bot = max(lo_i, float(lows[j]))
            overlap = max(0.0, top - bot)
            weighted += norm_w[k] * (overlap / current_range)
        out[i] = (1.0 - weighted) * 100.0
    return out


class TestOverlapScoreVectorized:
    """Vectorized implementation must match the Python reference exactly."""

    @pytest.mark.parametrize("lookback", [2, 5, 10, 25, 50])
    def test_matches_reference_random_walk(self, lookback):
        rng = np.random.default_rng(20240501 + lookback)
        n = 500
        base = 100.0 + np.cumsum(rng.normal(0.0, 0.4, n))
        highs = base + rng.uniform(0.1, 1.0, n)
        lows = base - rng.uniform(0.1, 1.0, n)
        candles = [
            Candle(
                date=datetime(2024, 3, 4, 9, 30) + timedelta(minutes=5 * i),
                open=float(lows[i]),
                high=float(highs[i]),
                low=float(lows[i]),
                close=float(highs[i]),
                volume=1000,
                session="regular",
            )
            for i in range(n)
        ]
        bars = Bars.from_candles(candles)
        got = OverlapScoreInverted(lookback=lookback).compute_arr(bars)["osi"]
        want = _reference_osi(bars.high, bars.low, lookback)
        np.testing.assert_allclose(got, want, rtol=1e-12, atol=1e-12,
                                   equal_nan=True)

    def test_matches_reference_with_dojis(self):
        """Bars with high == low (range floored) still match the reference."""
        prices = [(100.0, 110.0), (105.0, 105.0), (102.0, 108.0)] * 7
        candles = _make_candles(prices)
        bars = Bars.from_candles(candles)
        got = OverlapScoreInverted(lookback=10).compute_arr(bars)["osi"]
        want = _reference_osi(bars.high, bars.low, 10)
        np.testing.assert_allclose(got, want, rtol=1e-12, atol=1e-12,
                                   equal_nan=True)

    def test_short_input_returns_all_nan(self):
        """n <= lookback short-circuits to an all-NaN output."""
        candles = _make_candles([(100.0, 110.0)] * 5)
        result = OverlapScoreInverted(lookback=10).compute_arr(
            Bars.from_candles(candles))
        osi = result["osi"]
        assert osi.size == 5
        assert np.all(np.isnan(osi))
