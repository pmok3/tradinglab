"""Unit tests for :class:`ChandelierStops` indicator class."""
from __future__ import annotations

import datetime as dt
from typing import List

import numpy as np
import pytest

from tradinglab.core.bars import Bars
from tradinglab.indicators import INDICATORS, ChandelierStops
from tradinglab.indicators.base import factory_by_kind_id
from tradinglab.indicators.config import IndicatorConfig
from tradinglab.models import Candle


def _mk_candles(n: int, seed: int = 7) -> list[Candle]:
    start = dt.datetime(2024, 1, 2, 9, 30, tzinfo=dt.timezone.utc)
    rng = np.random.default_rng(seed)
    out: list[Candle] = []
    base = 100.0
    for i in range(n):
        drift = 0.05 * i
        o = base + drift + 0.3 * np.sin(i * 0.4)
        c = o + 0.6 * np.cos(i * 0.3) + 0.05 * rng.standard_normal()
        h = max(o, c) + abs(0.6 * np.sin(i * 0.7)) + 0.1
        l = min(o, c) - abs(0.5 * np.cos(i * 0.5)) - 0.1
        out.append(
            Candle(
                date=start + dt.timedelta(minutes=5 * i),
                open=float(o), high=float(h), low=float(l), close=float(c),
                volume=1000, session="regular",
            )
        )
    return out


# ---------------------------------------------------------------------------
# Registration / factory
# ---------------------------------------------------------------------------


def test_registered_and_factory_by_kind_id() -> None:
    assert "Chandelier Stops" in INDICATORS
    pair = factory_by_kind_id("chandelier")
    assert pair is not None
    name, fac = pair
    assert name == "Chandelier Stops"
    assert fac is ChandelierStops


def test_class_overlay_and_output_kinds() -> None:
    assert ChandelierStops.overlay is True
    # Both outputs must request the stair-step render path.
    assert ChandelierStops.output_kinds == {
        "long_stop": "stair_line",
        "short_stop": "stair_line",
    }


# ---------------------------------------------------------------------------
# Construction / defaults
# ---------------------------------------------------------------------------


def test_default_construction() -> None:
    c = ChandelierStops()
    assert c.lookback == 22
    assert c.atr_period == 22
    assert c.multiplier == 3.0
    assert c.ma_type == "RMA"
    # Compact name format for defaults.
    assert c.name == "CHAND(22,22,3)"


def test_name_includes_ma_type_when_non_default() -> None:
    assert ChandelierStops(ma_type="SMA").name == "CHAND(22,22,3,SMA)"
    assert ChandelierStops(ma_type="EMA").name == "CHAND(22,22,3,EMA)"
    # Non-default lookback / atr_period / mult also encoded.
    assert ChandelierStops(lookback=10, atr_period=14, multiplier=2.5).name == "CHAND(10,14,2.5)"


@pytest.mark.parametrize("ma", ["RMA", "SMA", "EMA", "WMA"])
def test_ma_type_variants_accepted(ma: str) -> None:
    c = ChandelierStops(ma_type=ma)
    assert c.ma_type == ma


def test_ma_type_case_insensitive() -> None:
    c = ChandelierStops(ma_type="ema")
    assert c.ma_type == "EMA"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kwargs", [
    {"lookback": 0},
    {"lookback": -1},
    {"atr_period": 1},
    {"atr_period": -2},
    {"multiplier": 0.0},
    {"multiplier": 0.4},  # below MIN
    {"multiplier": 8.1},  # above MAX
    {"ma_type": "NOPE"},
    {"ma_type": ""},
])
def test_constructor_validation_rejects_bad_params(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        ChandelierStops(**kwargs)


# ---------------------------------------------------------------------------
# compute / compute_arr
# ---------------------------------------------------------------------------


def test_compute_returns_both_outputs_with_correct_shape() -> None:
    n = 60
    candles = _mk_candles(n)
    out = ChandelierStops().compute(candles)
    assert set(out.keys()) == {"long_stop", "short_stop"}
    for arr in out.values():
        assert arr.shape == (n,)


def test_warmup_is_nan_until_atr_warms() -> None:
    n = 60
    candles = _mk_candles(n)
    c = ChandelierStops(lookback=5, atr_period=10, multiplier=2.0)
    out = c.compute(candles)
    # NaN warmup at index 0..atr_period-1 at minimum
    assert np.all(np.isnan(out["long_stop"][:c.atr_period - 1]))
    # Eventually finite
    assert np.isfinite(out["long_stop"][-1])
    assert np.isfinite(out["short_stop"][-1])


def test_long_stop_ratchet_never_descends() -> None:
    n = 80
    candles = _mk_candles(n)
    out = ChandelierStops(lookback=10, atr_period=10, multiplier=3.0).compute(candles)
    finite = out["long_stop"][np.isfinite(out["long_stop"])]
    diffs = np.diff(finite)
    assert np.all(diffs >= -1e-9), f"long ratchet violated: {diffs}"


def test_short_stop_ratchet_never_rises() -> None:
    n = 80
    candles = _mk_candles(n, seed=11)
    out = ChandelierStops(lookback=10, atr_period=10, multiplier=3.0).compute(candles)
    finite = out["short_stop"][np.isfinite(out["short_stop"])]
    diffs = np.diff(finite)
    assert np.all(diffs <= 1e-9), f"short ratchet violated: {diffs}"


def test_long_stop_below_running_high() -> None:
    n = 60
    candles = _mk_candles(n)
    out = ChandelierStops(lookback=10, atr_period=10, multiplier=3.0).compute(candles)
    highs = Bars.from_candles(candles).high
    running_max = np.maximum.accumulate(highs)
    long = out["long_stop"]
    for i in np.where(np.isfinite(long))[0]:
        assert long[i] < running_max[i]


def test_short_stop_above_running_low() -> None:
    n = 60
    candles = _mk_candles(n)
    out = ChandelierStops(lookback=10, atr_period=10, multiplier=3.0).compute(candles)
    lows = Bars.from_candles(candles).low
    running_min = np.minimum.accumulate(lows)
    short = out["short_stop"]
    for i in np.where(np.isfinite(short))[0]:
        assert short[i] > running_min[i]


def test_multiplier_scales_distance_from_extremum() -> None:
    """Doubling the multiplier should roughly double the long-stop's
    distance below the running rolling-high (where ATR has warmed)."""
    n = 80
    candles = _mk_candles(n, seed=21)
    out_lo = ChandelierStops(lookback=10, atr_period=10, multiplier=1.0).compute(candles)
    out_hi = ChandelierStops(lookback=10, atr_period=10, multiplier=2.0).compute(candles)
    highs = Bars.from_candles(candles).high
    # Compare the last finite bar's distance
    long_lo = out_lo["long_stop"]
    long_hi = out_hi["long_stop"]
    i = n - 1
    while i >= 0 and not (np.isfinite(long_lo[i]) and np.isfinite(long_hi[i])):
        i -= 1
    assert i > 0
    # Hi-mult stop must be lower (further from high) than lo-mult.
    assert long_hi[i] < long_lo[i]


def test_empty_bars_returns_empty_arrays() -> None:
    out = ChandelierStops().compute([])
    assert out["long_stop"].shape == (0,)
    assert out["short_stop"].shape == (0,)


# ---------------------------------------------------------------------------
# Config round-trip
# ---------------------------------------------------------------------------


def test_indicator_config_round_trip() -> None:
    cfg = IndicatorConfig(
        kind_id="chandelier",
        kind_version=1,
        display_name="Chandelier Stops",
        params={"lookback": 14, "atr_period": 10, "multiplier": 2.5, "ma_type": "EMA"},
    )
    d = cfg.to_dict()
    loaded = IndicatorConfig.from_dict(d)
    assert loaded.kind_id == "chandelier"
    assert loaded.params == cfg.params
