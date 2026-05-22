"""Tests for tradinglab.core.heikin_ashi."""

from __future__ import annotations

import math

import numpy as np
import pytest

from tradinglab.core.heikin_ashi import ha_arrays


def _arr(*xs) -> np.ndarray:
    return np.asarray(xs, dtype=np.float64)


def test_empty_inputs_return_four_empty_arrays():
    o, h, l, c = ha_arrays(_arr(), _arr(), _arr(), _arr())
    assert o.size == h.size == l.size == c.size == 0


def test_single_bar_seed_uses_open_close_average():
    # HA_Open seed = (O+C)/2; HA_Close = (O+H+L+C)/4
    # HA_High = max(H, HA_Open, HA_Close); HA_Low = min(L, HA_Open, HA_Close)
    ha_o, ha_h, ha_l, ha_c = ha_arrays(
        _arr(10.0), _arr(12.0), _arr(9.0), _arr(11.0),
    )
    assert ha_o[0] == pytest.approx((10.0 + 11.0) / 2.0)        # 10.5
    assert ha_c[0] == pytest.approx((10.0 + 12.0 + 9.0 + 11.0) / 4.0)  # 10.5
    assert ha_h[0] == pytest.approx(max(12.0, 10.5, 10.5))
    assert ha_l[0] == pytest.approx(min(9.0,  10.5, 10.5))


def test_two_bar_recurrence():
    o = _arr(10.0, 11.0)
    h = _arr(12.0, 13.0)
    l = _arr(9.0,  10.0)
    c = _arr(11.0, 12.0)
    ha_o, ha_h, ha_l, ha_c = ha_arrays(o, h, l, c)
    # Bar 0: seed
    assert ha_o[0] == pytest.approx(10.5)
    assert ha_c[0] == pytest.approx(10.5)
    # Bar 1: HA_Open = (HA_Open[0] + HA_Close[0]) / 2
    assert ha_o[1] == pytest.approx((10.5 + 10.5) / 2.0)
    # HA_Close = (11+13+10+12)/4 = 11.5
    assert ha_c[1] == pytest.approx(11.5)


def test_monotonic_up_produces_flat_bottom():
    # In a strong uptrend, HA_Low usually equals HA_Open (no lower wick).
    n = 20
    o = np.linspace(100.0, 119.0, n)
    c = o + 1.0
    h = c + 0.5
    l = o - 0.5
    ha_o, ha_h, ha_l, ha_c = ha_arrays(o, h, l, c)
    # After a few bars, HA_Open should equal HA_Low (flat-bottom) on most bars.
    flat_bottoms = np.sum(np.isclose(ha_l[3:], ha_o[3:], atol=1e-9))
    assert flat_bottoms >= (n - 3) * 0.7  # at least 70% of mature bars


def test_monotonic_down_produces_flat_top():
    n = 20
    o = np.linspace(120.0, 101.0, n)
    c = o - 1.0
    h = o + 0.5
    l = c - 0.5
    ha_o, ha_h, ha_l, ha_c = ha_arrays(o, h, l, c)
    flat_tops = np.sum(np.isclose(ha_h[3:], ha_o[3:], atol=1e-9))
    assert flat_tops >= (n - 3) * 0.7


def test_nan_gap_reseeds_recurrence():
    # Bar 1 is a gap (NaN OHLC); bar 2 should re-seed from its own O/C.
    nan = math.nan
    o = _arr(10.0, nan, 20.0, 21.0)
    h = _arr(11.0, nan, 22.0, 23.0)
    l = _arr(9.0,  nan, 19.0, 20.0)
    c = _arr(10.5, nan, 21.0, 22.0)
    ha_o, ha_h, ha_l, ha_c = ha_arrays(o, h, l, c)
    assert math.isnan(ha_c[1])
    # Bar 2 must re-seed (NOT propagate NaN forever):
    assert not math.isnan(ha_o[2])
    assert ha_o[2] == pytest.approx((20.0 + 21.0) / 2.0)
    # Bar 3 then continues the normal recurrence from bar 2:
    assert ha_o[3] == pytest.approx((ha_o[2] + ha_c[2]) / 2.0)


def test_mismatched_lengths_raises():
    with pytest.raises(ValueError):
        ha_arrays(_arr(1.0, 2.0), _arr(1.0), _arr(1.0, 2.0), _arr(1.0, 2.0))


def test_high_low_clamp_with_extreme_real_wick():
    # If real H/L exceed HA body, HA_High/Low must include the real wick.
    o = _arr(100.0, 101.0)
    h = _arr(105.0, 110.0)  # tall upper wick on bar 1
    l = _arr(99.0,   95.0)  # tall lower wick on bar 1
    c = _arr(101.0, 102.0)
    ha_o, ha_h, ha_l, ha_c = ha_arrays(o, h, l, c)
    assert ha_h[1] >= 110.0 - 1e-9
    assert ha_l[1] <= 95.0  + 1e-9


def test_outputs_are_float64_and_correct_length():
    n = 50
    rng = np.random.default_rng(seed=42)
    o = rng.uniform(100, 200, n)
    h = o + rng.uniform(0, 5, n)
    l = o - rng.uniform(0, 5, n)
    c = o + rng.uniform(-3, 3, n)
    ha = ha_arrays(o, h, l, c)
    for arr in ha:
        assert arr.dtype == np.float64
        assert arr.shape == (n,)
    ha_o, ha_h, ha_l, ha_c = ha
    # Invariant: HA_High >= max(HA_Open, HA_Close); HA_Low <= min.
    assert np.all(ha_h >= np.maximum(ha_o, ha_c) - 1e-9)
    assert np.all(ha_l <= np.minimum(ha_o, ha_c) + 1e-9)
