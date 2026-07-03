"""Tests for RSI oversold / overbought reference bands.

RSI gained two horizontal reference bands (default oversold 30 /
overbought 70) drawn as dotted axhlines in its pane, user-configurable
and render-only (they never affect the RSI output value). Mirrors the
LRSI band pattern (`reference_levels` instance attribute) plus the new
per-indicator `reference_line_style` render hook.
"""
from __future__ import annotations

import numpy as np
import pytest

from tradinglab.core.bars import Bars
from tradinglab.indicators import render as R
from tradinglab.indicators.config import IndicatorConfig
from tradinglab.indicators.rsi import RSI
from tradinglab.indicators.smi import SMI
from tradinglab.models import Candle


def _bars(closes: list[float]) -> Bars:
    from datetime import datetime, timedelta

    base = datetime(2024, 3, 4, 9, 30)
    candles = [
        Candle(
            date=base + timedelta(minutes=i),
            open=c, high=c + 1.0, low=c - 1.0, close=c,
            volume=1000.0, session="regular",
        )
        for i, c in enumerate(closes)
    ]
    return Bars.from_candles(candles)


# ---------------------------------------------------------------------------
# Defaults + params
# ---------------------------------------------------------------------------


def test_default_length_is_14():
    assert RSI().length == 14


def test_default_bands_are_30_70():
    assert RSI().reference_levels == (30.0, 70.0)


def test_custom_bands():
    assert RSI(oversold=25, overbought=75).reference_levels == (25.0, 75.0)


def test_show_reference_lines_false_hides_bands():
    assert RSI(show_reference_lines=False).reference_levels == ()


def test_class_reference_levels_default_empty():
    # Static introspection (no instance) reports no levels.
    assert RSI.reference_levels == ()


def test_band_params_in_schema():
    names = {p.name for p in RSI.params_schema}
    assert {"length", "oversold", "overbought", "show_reference_lines"} <= names


def test_trigger_relevant_params_is_length_only():
    # Band params are render-only → hidden from the trigger form.
    assert RSI.TRIGGER_RELEVANT_PARAMS == ("length",)


def test_factory_callable_with_no_args():
    # Persisted-config rehydrate path: RSI() must not raise.
    assert RSI().reference_levels == (30.0, 70.0)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_oversold_not_less_than_overbought_raises():
    with pytest.raises(ValueError):
        RSI(oversold=70, overbought=30)
    with pytest.raises(ValueError):
        RSI(oversold=50, overbought=50)


def test_band_out_of_range_raises():
    with pytest.raises(ValueError):
        RSI(oversold=-1, overbought=70)
    with pytest.raises(ValueError):
        RSI(oversold=30, overbought=101)


def test_length_below_two_raises():
    with pytest.raises(ValueError):
        RSI(length=1)


# ---------------------------------------------------------------------------
# Render-only contract: bands never change the RSI value
# ---------------------------------------------------------------------------


def test_band_params_do_not_change_compute():
    closes = [100.0 + np.sin(i / 3.0) * 5 + i * 0.1 for i in range(60)]
    bars = _bars(closes)
    a = RSI(length=14).compute_arr(bars)["rsi"]
    b = RSI(length=14, oversold=10, overbought=90,
            show_reference_lines=False).compute_arr(bars)["rsi"]
    np.testing.assert_array_equal(a, b)


# ---------------------------------------------------------------------------
# Dotted line style + render resolution
# ---------------------------------------------------------------------------


def test_reference_line_style_is_dotted():
    assert RSI.reference_line_style == ":"


def test_render_resolves_dotted_style_for_rsi():
    assert R._resolve_reference_line_style(RSI) == ":"


def test_render_style_falls_back_to_dashed_for_other_oscillators():
    # SMI (and every other oscillator) has no reference_line_style →
    # the render layer defaults to dashed.
    assert R._resolve_reference_line_style(SMI) == "--"


def test_render_resolves_instance_levels_from_config():
    cfg = IndicatorConfig(
        kind_id="rsi",
        params={"length": 14, "oversold": 20, "overbought": 80},
    )
    levels = R._resolve_reference_levels(cfg, RSI)
    assert tuple(levels) == (20.0, 80.0)


def test_render_resolves_empty_when_bands_hidden():
    cfg = IndicatorConfig(
        kind_id="rsi",
        params={"length": 14, "show_reference_lines": False},
    )
    assert R._resolve_reference_levels(cfg, RSI) == ()
