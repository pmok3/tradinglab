"""Tests for Prior Day H/L/C indicator."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from tradinglab.core.bars import Bars
from tradinglab.indicators.prior_day import PriorDayHLC
from tradinglab.models import Candle


def _make_intraday_candles(
    days: int = 2,
    bars_per_day: int = 5,
    base_price: float = 100.0,
) -> list[Candle]:
    """Build a synthetic intraday candle series spanning multiple days."""
    candles = []
    base = datetime(2024, 3, 4, 9, 30)  # Monday
    for d in range(days):
        day_start = base + timedelta(days=d)
        for b in range(bars_per_day):
            dt = day_start + timedelta(minutes=b * 5)
            # Each day's prices shift up slightly.
            p = base_price + d * 10 + b
            candles.append(Candle(
                date=dt,
                open=p,
                high=p + 2.0,
                low=p - 1.0,
                close=p + 0.5,
                volume=1000.0 + b * 100,
                session="regular",
            ))
    return candles


class TestPriorDayHLC:

    def test_basic_two_days(self):
        """With 2 days of data, day 2 bars show day 1's H/L/C."""
        candles = _make_intraday_candles(days=2, bars_per_day=5)
        ind = PriorDayHLC()
        bars = Bars.from_candles(candles)
        result = ind.compute_arr(bars)

        # Day 1 bars: indices 0-4. Day 2 bars: indices 5-9.
        pdh = result["prior_day_high"]
        pdl = result["prior_day_low"]
        pdc = result["prior_day_close"]

        # Day 1 bars should be NaN (no prior day).
        assert np.all(np.isnan(pdh[:5]))
        assert np.all(np.isnan(pdl[:5]))
        assert np.all(np.isnan(pdc[:5]))

        # Day 2 bars should have day 1's values.
        assert np.all(np.isfinite(pdh[5:10]))
        assert np.all(np.isfinite(pdl[5:10]))
        assert np.all(np.isfinite(pdc[5:10]))

        expected_high = 106.0
        expected_low = 99.0
        expected_close = 104.5

        np.testing.assert_allclose(pdh[5:10], expected_high)
        np.testing.assert_allclose(pdl[5:10], expected_low)
        np.testing.assert_allclose(pdc[5:10], expected_close)

    def test_three_days_rolling(self):
        """With 3 days, day 2 uses day 1, day 3 uses day 2.
        Last bar of day 2 is NaN (line break to prevent vertical connector)."""
        candles = _make_intraday_candles(days=3, bars_per_day=3)
        ind = PriorDayHLC()
        result = ind.compute_arr(Bars.from_candles(candles))

        pdh = result["prior_day_high"]
        # Day 1 (0-2): NaN
        assert np.all(np.isnan(pdh[:3]))
        # Day 2 (3-5): first two bars finite, last bar NaN (line break)
        assert np.all(np.isfinite(pdh[3:5]))
        assert np.isnan(pdh[5]), "Last bar of day 2 should be NaN (line break)"
        # Day 3 (6-8): first bar finite (starts the new level)
        assert np.isfinite(pdh[6]), "First bar of day 3 should have a value"
        assert np.all(np.isfinite(pdh[6:9]))
        # Day 2 and day 3 should have different values (prices shift +10/day)
        assert pdh[3] != pdh[6]

    def test_single_day_all_nan(self):
        """With only one day of data, everything is NaN."""
        candles = _make_intraday_candles(days=1, bars_per_day=5)
        ind = PriorDayHLC()
        result = ind.compute_arr(Bars.from_candles(candles))
        assert np.all(np.isnan(result["prior_day_high"]))
        assert np.all(np.isnan(result["prior_day_low"]))
        assert np.all(np.isnan(result["prior_day_close"]))

    def test_empty_input(self):
        """Empty input returns empty arrays."""
        ind = PriorDayHLC()
        result = ind.compute_arr(Bars.from_candles([]))
        assert result["prior_day_high"].size == 0
        assert result["prior_day_low"].size == 0
        assert result["prior_day_close"].size == 0

    def test_daily_bars_all_nan(self):
        """Daily bars return all NaN (indicator is intraday only)."""
        candles = []
        base = datetime(2024, 3, 4)
        for d in range(5):
            dt = base + timedelta(days=d)
            candles.append(Candle(
                date=dt, open=100, high=105, low=95,
                close=102, volume=1000, session="regular",
            ))
        ind = PriorDayHLC()
        result = ind.compute_arr(Bars.from_candles(candles))
        assert np.all(np.isnan(result["prior_day_high"]))

    def test_constant_across_session(self):
        """All bars in a session have the same PDH/PDL/PDC value."""
        candles = _make_intraday_candles(days=2, bars_per_day=10)
        ind = PriorDayHLC()
        result = ind.compute_arr(Bars.from_candles(candles))
        pdh = result["prior_day_high"]
        day2_vals = pdh[10:20]
        finite = day2_vals[np.isfinite(day2_vals)]
        assert finite.size > 0
        np.testing.assert_allclose(finite, finite[0])

    def test_extended_hours_excluded(self):
        """Pre/post market bars are excluded from prior day calculation."""
        base = datetime(2024, 3, 4, 9, 30)
        candles = []
        for b in range(3):
            candles.append(Candle(
                date=base + timedelta(minutes=b * 5),
                open=100, high=105, low=95, close=102,
                volume=1000, session="regular",
            ))
        candles.append(Candle(
            date=base + timedelta(hours=-3),
            open=50, high=200, low=20, close=180,
            volume=500, session="pre",
        ))
        day2 = base + timedelta(days=1)
        for b in range(3):
            candles.append(Candle(
                date=day2 + timedelta(minutes=b * 5),
                open=110, high=115, low=108, close=112,
                volume=1000, session="regular",
            ))
        candles.sort(key=lambda c: c.date)
        ind = PriorDayHLC()
        result = ind.compute_arr(Bars.from_candles(candles))
        pdh = result["prior_day_high"]
        day2_pdh = pdh[np.isfinite(pdh)]
        if day2_pdh.size > 0:
            assert day2_pdh[0] == pytest.approx(105.0)

    def test_compute_candle_api(self):
        """The candle-list API works the same as compute_arr."""
        candles = _make_intraday_candles(days=2, bars_per_day=5)
        ind = PriorDayHLC()
        result = ind.compute(candles)
        assert "prior_day_high" in result
        assert "prior_day_low" in result
        assert "prior_day_close" in result
        assert result["prior_day_high"].size == len(candles)

    def test_availability(self):
        """Indicator is available on intraday, not on daily."""
        assert PriorDayHLC.is_available_for("5m").ok
        assert PriorDayHLC.is_available_for("1m").ok
        assert PriorDayHLC.is_available_for("1h").ok
        assert not PriorDayHLC.is_available_for("1d").ok
        assert not PriorDayHLC.is_available_for("1wk").ok

    def test_kind_id(self):
        assert PriorDayHLC.kind_id == "prior_day_hlc"

    def test_overlay(self):
        assert PriorDayHLC.overlay is True

    def test_output_keys_match_style(self):
        """Output keys must match default_style keys."""
        ind = PriorDayHLC()
        candles = _make_intraday_candles(days=2, bars_per_day=3)
        result = ind.compute_arr(Bars.from_candles(candles))
        assert set(result.keys()) == set(PriorDayHLC.default_style.keys())

    # ---- Toggle tests ----

    def test_disable_high(self):
        """show_high=False leaves prior_day_high as all NaN."""
        candles = _make_intraday_candles(days=2, bars_per_day=5)
        ind = PriorDayHLC(show_high=False)
        result = ind.compute_arr(Bars.from_candles(candles))
        assert np.all(np.isnan(result["prior_day_high"]))
        assert np.any(np.isfinite(result["prior_day_low"]))
        assert np.any(np.isfinite(result["prior_day_close"]))

    def test_disable_low(self):
        """show_low=False leaves prior_day_low as all NaN."""
        candles = _make_intraday_candles(days=2, bars_per_day=5)
        ind = PriorDayHLC(show_low=False)
        result = ind.compute_arr(Bars.from_candles(candles))
        assert np.any(np.isfinite(result["prior_day_high"]))
        assert np.all(np.isnan(result["prior_day_low"]))
        assert np.any(np.isfinite(result["prior_day_close"]))

    def test_disable_close(self):
        """show_close=False leaves prior_day_close as all NaN."""
        candles = _make_intraday_candles(days=2, bars_per_day=5)
        ind = PriorDayHLC(show_close=False)
        result = ind.compute_arr(Bars.from_candles(candles))
        assert np.any(np.isfinite(result["prior_day_high"]))
        assert np.any(np.isfinite(result["prior_day_low"]))
        assert np.all(np.isnan(result["prior_day_close"]))

    def test_disable_all(self):
        """All toggles off = all NaN."""
        candles = _make_intraday_candles(days=2, bars_per_day=5)
        ind = PriorDayHLC(show_high=False, show_low=False, show_close=False)
        result = ind.compute_arr(Bars.from_candles(candles))
        assert np.all(np.isnan(result["prior_day_high"]))
        assert np.all(np.isnan(result["prior_day_low"]))
        assert np.all(np.isnan(result["prior_day_close"]))

    def test_name_reflects_toggles(self):
        """The name string reflects which levels are enabled."""
        assert "H" in PriorDayHLC(show_high=True, show_low=False, show_close=False).name
        assert "L" in PriorDayHLC(show_high=False, show_low=True, show_close=False).name
        assert "C" in PriorDayHLC(show_high=False, show_low=False, show_close=True).name
        full = PriorDayHLC()
        assert "H" in full.name and "L" in full.name and "C" in full.name

    def test_no_vertical_connector_between_days(self):
        """The last bar of each prior session should be NaN so
        matplotlib breaks the line instead of drawing a vertical
        connector between days with different prior-day levels."""
        candles = _make_intraday_candles(days=3, bars_per_day=4)
        ind = PriorDayHLC()
        result = ind.compute_arr(Bars.from_candles(candles))
        pdh = result["prior_day_high"]
        # Day 2 (4-7): last bar (idx 7) should be NaN (line break)
        assert np.isnan(pdh[7]), "Last bar of day 2 should be NaN (line break)"
        # Day 3 (8-11): first bar (idx 8) should be finite (starts new level)
        assert np.isfinite(pdh[8]), "First bar of day 3 should have a value"


class TestLegendLabels:
    """Chart-legend prefix + per-output band labels (surgical-fix sprint)."""

    def test_legend_label_has_no_params_suffix(self):
        """The legend prefix is the clean display name — no boolean
        toggle suffix like ``(True, show_low=True, show_close=True)``."""
        out = PriorDayHLC.legend_label("Prior Day H/L/C", {
            "show_high": True, "show_low": True, "show_close": True,
        })
        assert out == "Prior Day H/L/C"
        assert "(" not in out

    def test_legend_label_passthrough_partial_name(self):
        """A partial display name (e.g. close disabled) is preserved."""
        assert PriorDayHLC.legend_label("Prior Day H/L", {}) == "Prior Day H/L"

    def test_legend_label_blank_falls_back(self):
        assert PriorDayHLC.legend_label("", {}) == "Prior Day H/L/C"

    def test_output_key_label_abbreviates(self):
        assert PriorDayHLC.output_key_label("prior_day_high") == "pd_high"
        assert PriorDayHLC.output_key_label("prior_day_low") == "pd_low"
        assert PriorDayHLC.output_key_label("prior_day_close") == "pd_close"

    def test_output_key_label_unknown_passthrough(self):
        assert PriorDayHLC.output_key_label("foo") == "foo"

    def test_format_indicator_label_integration(self):
        """End-to-end through the readout-legend formatter: no params."""
        from tradinglab.gui.readout_legend import (
            _key_label_for,
            format_indicator_label,
        )
        from tradinglab.indicators.config import IndicatorConfig

        cfg = IndicatorConfig(
            kind_id="prior_day_hlc",
            display_name="Prior Day H/L/C",
            params={"show_high": True, "show_low": True, "show_close": True},
        )
        assert format_indicator_label(cfg) == "Prior Day H/L/C"
        assert _key_label_for(cfg, "prior_day_high") == "pd_high"
        assert _key_label_for(cfg, "prior_day_close") == "pd_close"


class TestEffectiveOutputKeys:
    """A deselected level must drop out of the visible output set so it
    does not appear on the chart (legend / per-output bookkeeping)."""

    def test_all_enabled_returns_all_three(self):
        keys = PriorDayHLC.effective_output_keys(
            {"show_high": True, "show_low": True, "show_close": True})
        assert keys == ("prior_day_high", "prior_day_low", "prior_day_close")

    def test_close_disabled_drops_pd_close(self):
        keys = PriorDayHLC.effective_output_keys(
            {"show_high": True, "show_low": True, "show_close": False})
        assert "prior_day_close" not in keys
        assert keys == ("prior_day_high", "prior_day_low")

    def test_only_close_enabled(self):
        keys = PriorDayHLC.effective_output_keys(
            {"show_high": False, "show_low": False, "show_close": True})
        assert keys == ("prior_day_close",)

    def test_all_disabled_returns_empty(self):
        keys = PriorDayHLC.effective_output_keys(
            {"show_high": False, "show_low": False, "show_close": False})
        assert keys == ()

    def test_missing_params_default_to_enabled(self):
        # Back-compat: a persisted config without the show_* keys keeps
        # all three levels (the schema defaults are True).
        assert PriorDayHLC.effective_output_keys({}) == (
            "prior_day_high", "prior_day_low", "prior_day_close")

    def test_legend_excludes_deselected_close(self):
        """End-to-end: the readout legend must not surface pd_close when
        show_close is off (the reported bug)."""
        from tradinglab.gui.readout_legend import _effective_output_keys_for
        from tradinglab.indicators.config import IndicatorConfig

        cfg = IndicatorConfig(
            kind_id="prior_day_hlc",
            display_name="Prior Day H/L",
            params={"show_high": True, "show_low": True, "show_close": False},
        )
        keys = _effective_output_keys_for(cfg)
        assert "prior_day_close" not in keys
        assert set(keys) == {"prior_day_high", "prior_day_low"}
