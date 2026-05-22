"""Unit tests for :mod:`tradinglab.core.chandelier_math`."""
from __future__ import annotations

import numpy as np
import pytest

from tradinglab.core.chandelier_math import (
    compute_atr,
    compute_chandelier_long,
    compute_chandelier_short,
    rolling_highest_high_since,
    rolling_lowest_low_since,
)

# ---------------------------------------------------------------------------
# rolling_highest_high_since / rolling_lowest_low_since
# ---------------------------------------------------------------------------


class TestRollingExtremum:
    def test_indicator_mode_warmup_is_nan(self):
        highs = np.array([10.0, 11.0, 12.0, 13.0], dtype=np.float64)
        out = rolling_highest_high_since(highs, lookback=3, anchor_idx=None)
        assert np.isnan(out[0]) and np.isnan(out[1])
        assert out[2] == 12.0
        assert out[3] == 13.0

    def test_indicator_mode_window_size(self):
        highs = np.array([5.0, 1.0, 1.0, 1.0, 1.0], dtype=np.float64)
        out = rolling_highest_high_since(highs, lookback=3, anchor_idx=None)
        # Once we leave the 5.0 behind, the rolling-high drops to 1.0.
        assert out[2] == 5.0
        assert out[3] == 1.0
        assert out[4] == 1.0

    def test_anchor_mode_seeds_at_anchor(self):
        highs = np.array([100.0, 99.0, 98.0, 102.0, 103.0, 101.0], dtype=np.float64)
        out = rolling_highest_high_since(highs, lookback=4, anchor_idx=2)
        # Before anchor: NaN
        assert np.isnan(out[0]) and np.isnan(out[1])
        # At anchor: value seeded from bar[anchor]
        assert out[2] == 98.0
        # Expanding forward but never reaching back before anchor
        assert out[3] == 102.0
        assert out[4] == 103.0
        # Stays at the high until we exceed it (anchored high is 103)
        assert out[5] == 103.0

    def test_anchor_mode_lookback_caps_window(self):
        # Highs increase then drop; with lookback=2, the window should not
        # include bars older than (i - lookback + 1) once the cap kicks in.
        highs = np.array([100.0, 105.0, 110.0, 90.0, 80.0], dtype=np.float64)
        out = rolling_highest_high_since(highs, lookback=2, anchor_idx=0)
        # i=0: window = [100]; out=100
        # i=1: window = [100,105]; out=105
        # i=2: window = [105,110]; out=110 (anchor max(0,1)=1)
        # i=3: window = [110,90]; out=110
        # i=4: window = [90,80]; out=90 (cap excludes anchor's prior high)
        assert out[0] == 100.0
        assert out[1] == 105.0
        assert out[2] == 110.0
        assert out[3] == 110.0
        assert out[4] == 90.0

    def test_lookup_low_mirror(self):
        lows = np.array([10.0, 9.0, 8.0, 12.0, 13.0], dtype=np.float64)
        out = rolling_lowest_low_since(lows, lookback=3, anchor_idx=2)
        assert np.isnan(out[0]) and np.isnan(out[1])
        assert out[2] == 8.0
        assert out[3] == 8.0
        assert out[4] == 8.0

    def test_zero_lookback_returns_all_nan(self):
        highs = np.array([1.0, 2.0, 3.0], dtype=np.float64)
        out = rolling_highest_high_since(highs, lookback=0, anchor_idx=None)
        assert np.all(np.isnan(out))

    def test_anchor_outside_bounds_returns_all_nan(self):
        highs = np.array([1.0, 2.0, 3.0], dtype=np.float64)
        out_neg = rolling_highest_high_since(highs, lookback=2, anchor_idx=-1)
        out_off = rolling_highest_high_since(highs, lookback=2, anchor_idx=10)
        assert np.all(np.isnan(out_neg))
        assert np.all(np.isnan(out_off))


# ---------------------------------------------------------------------------
# compute_atr
# ---------------------------------------------------------------------------


class TestComputeATR:
    def test_atr_warmup_nan_then_finite(self):
        n = 30
        rng = np.random.default_rng(42)
        closes = 100.0 + np.cumsum(rng.normal(0, 1, n))
        highs = closes + 0.5
        lows = closes - 0.5
        atr = compute_atr(highs, lows, closes, atr_period=10, ma_type="RMA")
        # bar 0 has no prior close so TR is NaN-ish; the kernel needs
        # atr_period valid TR bars to warm up.
        assert np.isnan(atr[0])
        assert np.isfinite(atr[-1])

    def test_atr_rejects_unknown_ma_type(self):
        highs = np.array([1.0, 2.0]); lows = np.array([0.5, 1.5]); closes = np.array([1.0, 2.0])
        with pytest.raises(ValueError):
            compute_atr(highs, lows, closes, atr_period=2, ma_type="NOPE")

    def test_atr_rejects_short_period(self):
        highs = np.array([1.0, 2.0]); lows = np.array([0.5, 1.5]); closes = np.array([1.0, 2.0])
        with pytest.raises(ValueError):
            compute_atr(highs, lows, closes, atr_period=1, ma_type="RMA")

    def test_atr_kernel_variants_all_run(self):
        n = 50
        rng = np.random.default_rng(0)
        closes = 100.0 + np.cumsum(rng.normal(0, 1, n))
        highs = closes + 0.5
        lows = closes - 0.5
        for k in ("RMA", "SMA", "EMA", "WMA"):
            atr = compute_atr(highs, lows, closes, atr_period=10, ma_type=k)
            assert atr.shape == (n,)
            assert np.isfinite(atr[-1]), f"kernel={k}"


# ---------------------------------------------------------------------------
# compute_chandelier_long / short
# ---------------------------------------------------------------------------


def _bars(n, seed=0):
    rng = np.random.default_rng(seed)
    closes = 100.0 + np.cumsum(rng.normal(0, 0.5, n))
    highs = closes + rng.uniform(0.1, 1.0, n)
    lows = closes - rng.uniform(0.1, 1.0, n)
    return highs, lows, closes


class TestChandelierLong:
    def test_long_stop_below_high(self):
        highs, lows, closes = _bars(60)
        atr = compute_atr(highs, lows, closes, atr_period=10, ma_type="RMA")
        stops, _final = compute_chandelier_long(
            highs, atr, lookback=10, multiplier=3.0, anchor_idx=None,
        )
        # At every finite index, the ratcheted stop must sit below the
        # running max of highs seen so far (the rolling-high that fed
        # the stop). Stops never exceed an actual recent high — they're
        # an ATR offset *below* the rolling extremum.
        finite = np.isfinite(stops) & np.isfinite(atr)
        assert finite.any()
        running_max = np.maximum.accumulate(highs)
        for i in np.where(finite)[0]:
            assert stops[i] < running_max[i]

    def test_long_ratchet_never_decreases(self):
        # Make highs go up then back down so the raw stop would fall;
        # ratchet must hold the previous max.
        n = 40
        highs = np.linspace(100, 120, n // 2).tolist() + np.linspace(120, 105, n // 2).tolist()
        highs = np.array(highs)
        lows = highs - 1.0
        closes = (highs + lows) / 2.0
        atr = compute_atr(highs, lows, closes, atr_period=5, ma_type="RMA")
        stops, _ = compute_chandelier_long(
            highs, atr, lookback=5, multiplier=2.0, anchor_idx=0,
        )
        # The ratcheted series must be non-decreasing where finite.
        finite_vals = stops[np.isfinite(stops)]
        assert finite_vals.size >= 2
        diffs = np.diff(finite_vals)
        assert np.all(diffs >= -1e-9), f"ratchet violated: diffs={diffs}"

    def test_long_anchor_no_pre_entry_bars(self):
        highs = np.array([200.0, 150.0, 100.0, 101.0, 102.0], dtype=np.float64)
        lows = highs - 1.0
        closes = (highs + lows) / 2.0
        atr = compute_atr(highs, lows, closes, atr_period=2, ma_type="RMA")
        stops, _ = compute_chandelier_long(
            highs, atr, lookback=10, multiplier=1.0, anchor_idx=2,
        )
        # Pre-anchor bars must be NaN
        assert np.isnan(stops[0]) and np.isnan(stops[1])
        # Anchored region only sees bars >= idx 2; the 200.0 spike is
        # excluded from the rolling-high.
        for i in (2, 3, 4):
            if np.isfinite(stops[i]):
                assert stops[i] < 105.0  # well below the pre-anchor spike

    def test_long_final_ratchet_returned(self):
        highs, lows, closes = _bars(40)
        atr = compute_atr(highs, lows, closes, atr_period=10, ma_type="RMA")
        stops, final = compute_chandelier_long(
            highs, atr, lookback=10, multiplier=3.0, anchor_idx=None,
        )
        finite = stops[np.isfinite(stops)]
        if finite.size:
            assert final == pytest.approx(finite[-1])

    def test_long_invalid_lookback(self):
        h = np.array([1.0, 2.0]); a = np.array([0.5, 0.5])
        with pytest.raises(ValueError):
            compute_chandelier_long(h, a, lookback=0, multiplier=1.0)

    def test_long_invalid_multiplier(self):
        h = np.array([1.0, 2.0]); a = np.array([0.5, 0.5])
        with pytest.raises(ValueError):
            compute_chandelier_long(h, a, lookback=1, multiplier=-1.0)

    def test_long_shape_mismatch(self):
        h = np.array([1.0, 2.0]); a = np.array([0.5])
        with pytest.raises(ValueError):
            compute_chandelier_long(h, a, lookback=1, multiplier=1.0)


class TestChandelierShort:
    def test_short_stop_above_low(self):
        highs, lows, closes = _bars(60, seed=1)
        atr = compute_atr(highs, lows, closes, atr_period=10, ma_type="RMA")
        stops, _ = compute_chandelier_short(
            lows, atr, lookback=10, multiplier=3.0, anchor_idx=None,
        )
        # Ratcheted short stop must sit above the running min of lows.
        finite = np.isfinite(stops) & np.isfinite(atr)
        running_min = np.minimum.accumulate(lows)
        for i in np.where(finite)[0]:
            assert stops[i] > running_min[i]

    def test_short_ratchet_never_increases(self):
        # Lows fall then rise — ratchet (min) must hold the low watermark.
        n = 40
        lows = np.linspace(100, 80, n // 2).tolist() + np.linspace(80, 95, n // 2).tolist()
        lows = np.array(lows)
        highs = lows + 1.0
        closes = (highs + lows) / 2.0
        atr = compute_atr(highs, lows, closes, atr_period=5, ma_type="RMA")
        stops, _ = compute_chandelier_short(
            lows, atr, lookback=5, multiplier=2.0, anchor_idx=0,
        )
        finite_vals = stops[np.isfinite(stops)]
        assert finite_vals.size >= 2
        diffs = np.diff(finite_vals)
        # Non-increasing
        assert np.all(diffs <= 1e-9), f"short ratchet violated: diffs={diffs}"

    def test_ratchet_prev_seeds_continuation(self):
        # Reasonable check that ratchet_prev seeds the running max for long.
        highs = np.array([100, 101, 102, 103, 104], dtype=np.float64)
        lows = highs - 1.0
        closes = (highs + lows) / 2.0
        atr = compute_atr(highs, lows, closes, atr_period=2, ma_type="RMA")
        # Without seed
        stops1, _final1 = compute_chandelier_long(
            highs, atr, lookback=2, multiplier=1.0, anchor_idx=0,
        )
        # With seed at a very high value — ratchet pins it.
        stops2, _final2 = compute_chandelier_long(
            highs, atr, lookback=2, multiplier=1.0, anchor_idx=0,
            ratchet_prev=10_000.0,
        )
        # All finite outputs of stops2 should be >= 10000 (ratchet held).
        f2 = stops2[np.isfinite(stops2)]
        assert np.all(f2 >= 10_000.0)
