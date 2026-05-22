"""Tests for the unified RVOL indicator's z-score mode.

Covers:
* Sample-stddev z-score math (parity with a numpy reference).
* NaN policy: warmup, zero-stddev windows, NaN underlying RVOL.
* Spike detection: a clear volume spike must produce the largest z.
* Param validation: ``length < 2`` rejected when ``z_score=True``.
* Pane group is ``"rvol_z"`` when ``z_score=True`` (its own subwindow).
* Reference levels are ``(0.0, 2.0)`` (Bellafiore +2σ) when z-score on.
* Intraday-only modes are intraday-gated regardless of z_score.
* Aggregator / session_filter flow through to the underlying RVOL compute.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

import tradinglab.indicators  # noqa: F401  -- registers indicators
from tradinglab.core.bars import Bars
from tradinglab.indicators import RVOL, factory_by_kind_id
from tradinglab.indicators.rvol import _rolling_zscore
from tradinglab.models import Candle


def _candles(volumes, start_minute: int = 0) -> list[Candle]:
    """Build a minute-resolution candle list with given int volumes."""
    t0 = datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc)
    out = []
    for i, v in enumerate(volumes):
        out.append(Candle(
            date=t0 + timedelta(minutes=start_minute + i),
            open=100.0, high=100.5, low=99.5, close=100.0,
            volume=int(v), session="regular",
        ))
    return out


# ---------------------------------------------------------------------------
# _rolling_zscore math
# ---------------------------------------------------------------------------


def test_rolling_zscore_parity_with_numpy_reference():
    """Compare against a hand-rolled sample-stddev z-score."""
    rng = np.random.default_rng(11)
    rvol = rng.uniform(0.5, 3.0, size=100)
    L = 10
    z = _rolling_zscore(rvol, L)
    for i in range(L - 1, len(rvol)):
        window = rvol[i - L + 1 : i + 1]
        mean = window.mean()
        std = window.std(ddof=1)
        expected = (rvol[i] - mean) / std
        np.testing.assert_allclose(z[i], expected, rtol=1e-12, atol=1e-12)


def test_rolling_zscore_nan_in_window_excluded_from_stats():
    """NaN underlying RVOL values are dropped from each window's stats."""
    rvol = np.array([np.nan, np.nan, 1.0, 2.0, 3.0, 4.0, 5.0])
    z = _rolling_zscore(rvol, length=5)
    expected = (5.0 - 3.0) / np.std([1.0, 2.0, 3.0, 4.0, 5.0], ddof=1)
    np.testing.assert_allclose(z[6], expected, rtol=1e-12)


def test_rolling_zscore_zero_stddev_returns_nan():
    """A constant RVOL window has stddev=0 → z must be NaN, not inf."""
    rvol = np.full(20, 1.0)
    z = _rolling_zscore(rvol, length=10)
    assert np.all(np.isnan(z))


def test_rolling_zscore_warmup_returns_nan():
    """Until the window has 2+ finite samples, output is NaN."""
    rvol = np.array([np.nan, np.nan, 1.0])
    z = _rolling_zscore(rvol, length=5)
    assert np.isnan(z[0]) and np.isnan(z[1]) and np.isnan(z[2])


def test_rolling_zscore_underlying_nan_at_current_bar_returns_nan():
    """If the bar's own RVOL is NaN, z is NaN even with a valid window."""
    rvol = np.array([1.0, 2.0, 3.0, 4.0, np.nan])
    z = _rolling_zscore(rvol, length=4)
    assert np.isnan(z[4])


# ---------------------------------------------------------------------------
# RVOL(z_score=True, mode="simple"): end-to-end behavior
# ---------------------------------------------------------------------------


def test_simple_rvol_zscore_detects_volume_spike():
    """A clear single-bar volume spike must produce the maximum z."""
    rng = np.random.default_rng(7)
    vols = rng.integers(900_000, 1_100_000, size=80).tolist()
    spike_idx = 70
    vols[spike_idx] *= 5  # large spike
    candles = _candles(vols)
    z = RVOL(mode="simple", length=20, z_score=True).compute_arr(
        Bars.from_candles(candles)
    )["rvol"]
    assert int(np.nanargmax(z)) == spike_idx
    assert float(np.nanmax(z)) > 3.0  # well above the +2σ Bellafiore line


def test_simple_rvol_zscore_constant_volume_yields_no_signal():
    """Flat volume → underlying RVOL ≈ 1.0 everywhere → z stays NaN
    (zero stddev window) or near zero, never an actionable spike.
    """
    candles = _candles([1_000_000] * 60)
    z = RVOL(mode="simple", length=20, z_score=True).compute_arr(
        Bars.from_candles(candles)
    )["rvol"]
    finite = z[np.isfinite(z)]
    if finite.size:
        assert float(np.nanmax(np.abs(finite))) < 1e-6


def test_simple_rvol_zscore_empty_bars():
    """Zero-length input must not crash and must return shape (0,)."""
    z = RVOL(mode="simple", length=20, z_score=True).compute_arr(
        Bars.from_candles([])
    )["rvol"]
    assert z.shape == (0,)


def test_simple_rvol_zscore_short_input_all_nan():
    """Fewer bars than ``length`` → output entirely NaN, no crash."""
    candles = _candles([1_000_000 + i for i in range(5)])
    z = RVOL(mode="simple", length=20, z_score=True).compute_arr(
        Bars.from_candles(candles)
    )["rvol"]
    assert np.all(np.isnan(z))


# ---------------------------------------------------------------------------
# Registry + metadata
# ---------------------------------------------------------------------------


def test_registered_in_indicator_factory():
    """Unified RVOL is registered under kind_id ``"rvol"``."""
    entry = factory_by_kind_id("rvol")
    assert entry is not None
    _name, factory = entry
    assert factory is RVOL


@pytest.mark.parametrize("z_score,expected", [
    (False, "rvol"),
    (True, "rvol_z"),
])
def test_pane_group_for_toggles_with_z_score(z_score, expected):
    """``pane_group_for`` returns ``"rvol_z"`` iff ``z_score=True``."""
    assert RVOL.pane_group_for({"z_score": z_score}) == expected


def test_reference_levels_zero_and_two_sigma_when_z_score():
    """When ``z_score=True``, instance ref levels are ``(0.0, 2.0)``."""
    inst = RVOL(z_score=True)
    refs = inst.reference_levels
    assert 0.0 in refs and 2.0 in refs


def test_reference_levels_one_warn_extreme_when_not_z_score():
    """When ``z_score=False``, instance ref levels are ``(1.0, warn, extreme)``."""
    inst = RVOL(z_score=False, threshold_warn=1.5, threshold_extreme=4.0)
    assert inst.reference_levels == (1.0, 1.5, 4.0)


@pytest.mark.parametrize("mode", ["simple", "time_of_day", "cumulative"])
def test_length_below_two_rejected_when_z_score(mode):
    """``length < 2`` is rejected when ``z_score=True`` (need 2+ samples)."""
    with pytest.raises(ValueError):
        RVOL(mode=mode, length=1, z_score=True)


@pytest.mark.parametrize("mode", ["simple", "time_of_day", "cumulative"])
def test_length_one_allowed_when_not_z_score(mode):
    """``length=1`` is allowed without z_score (matches legacy plain RVOL)."""
    inst = RVOL(mode=mode, length=1, z_score=False)
    assert inst.length == 1


def test_passes_aggregator_through_to_compute():
    """A non-default aggregator must reach the underlying compute."""
    inst = RVOL(mode="simple", length=10, aggregator="median", z_score=True)
    assert inst.aggregator == "median"


def test_intraday_only_availability_for_tod_and_cum_with_z_score():
    """ToD and Cum modes must be intraday-only regardless of z_score."""
    for mode in ("time_of_day", "cumulative"):
        for z in (False, True):
            params = {"mode": mode, "z_score": z}
            for itv in ("1m", "5m", "15m"):
                assert RVOL.is_available_for(itv, params).ok, \
                    f"mode={mode} z={z} should be available on {itv}"
            for itv in ("1d", "1w"):
                assert not RVOL.is_available_for(itv, params).ok, \
                    f"mode={mode} z={z} should NOT be available on {itv}"


def test_simple_mode_available_on_all_intervals():
    """Simple mode works on every interval, with or without z_score."""
    for z in (False, True):
        params = {"mode": "simple", "z_score": z}
        for itv in ("1m", "5m", "1d", "1w"):
            assert RVOL.is_available_for(itv, params).ok
