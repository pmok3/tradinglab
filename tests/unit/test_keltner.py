"""Unit tests for :class:`KeltnerChannels`.

Covers:

* Shape + warmup NaN regions for both ``method="atr"`` and
  ``method="original"``.
* ``upper >= middle >= lower`` invariant at every defined index.
* Modern formula parity against an inline reference:
  ``middle = apply_ma(ma_type, close, length)``;
  ``upper/lower = middle ± multiplier * apply_ma(atr_ma_type, TR,
  atr_length)`` with ``TR`` from :func:`indicators.wilder.true_range`.
* Original formula parity: ``middle = ma((H+L+C)/3, length)``;
  ``upper/lower = middle ± multiplier * ma(H-L, length)``.
* Kernel variation: SMA / EMA / WMA / RMA on both axes.
* Multiplier scaling: doubling `multiplier` doubles the half-width.
* Method-aware ma_type default sentinel: ``KC()`` → EMA centerline,
  ``KC(method="original")`` → SMA centerline.
* Constructor validation: every documented invalid combination
  raises ``ValueError`` before any mutation.
* Persistence round-trip via :class:`IndicatorConfig`.
"""

from __future__ import annotations

import datetime as dt
from typing import List

import numpy as np
import pytest

from tradinglab.core.bars import Bars
from tradinglab.indicators import INDICATORS, KeltnerChannels
from tradinglab.indicators.base import factory_by_kind_id
from tradinglab.indicators.config import IndicatorConfig
from tradinglab.indicators.ma_kernels import apply_ma
from tradinglab.indicators.wilder import true_range
from tradinglab.models import Candle


def _mk_candles(n: int, seed: float = 0.0) -> List[Candle]:
    """Deterministic walk with non-trivial range — exercises TR fully."""
    start = dt.datetime(2024, 1, 2, 9, 30, tzinfo=dt.timezone.utc)
    rng = np.random.default_rng(int(seed * 1000) or 7)
    out: List[Candle] = []
    base = 100.0
    for i in range(n):
        drift = 0.05 * i + seed
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


def test_registered_and_factory_by_kind_id() -> None:
    assert "Keltner Channels" in INDICATORS
    pair = factory_by_kind_id("keltner")
    assert pair is not None
    name, fac = pair
    assert name == "Keltner Channels"
    assert fac is KeltnerChannels


def test_default_constructor_yields_modern_ema_kc() -> None:
    kc = KeltnerChannels()
    assert kc.method == "atr"
    assert kc.ma_type == "EMA"
    assert kc.atr_ma_type == "RMA"
    assert kc.length == 20
    assert kc.atr_length == 10
    assert kc.multiplier == 2.0
    assert kc.name == "KC(20,2)"
    assert kc.overlay is True


def test_default_constructor_yields_original_sma_kc_when_method_original() -> None:
    kc = KeltnerChannels(method="original")
    assert kc.method == "original"
    # Sentinel resolution picks SMA for original method.
    assert kc.ma_type == "SMA"
    assert kc.name == "KC-Orig(20,2)"


def test_explicit_ma_type_overrides_method_default() -> None:
    kc = KeltnerChannels(method="original", ma_type="EMA")
    assert kc.ma_type == "EMA"
    assert kc.name == "KC-Orig(20,2,EMA)"


def test_name_tag_encoding() -> None:
    # Atr/RMA defaults — silent.
    assert KeltnerChannels().name == "KC(20,2)"
    # Only centerline differs.
    assert KeltnerChannels(ma_type="SMA").name == "KC(20,2,SMA)"
    # Both kernels differ — spelled out.
    assert KeltnerChannels(ma_type="SMA", atr_ma_type="EMA").name == "KC(20,2,SMA/EMA)"
    # Atr kernel differs, centerline default — tag still spells out both.
    assert KeltnerChannels(atr_ma_type="SMA").name == "KC(20,2,EMA/SMA)"
    # Non-default atr_length.
    assert KeltnerChannels(atr_length=14).name == "KC(20,2,σ=14)"
    # Non-default length / multiplier.
    assert KeltnerChannels(length=14, multiplier=1.5).name == "KC(14,1.5)"


def test_modern_method_shapes_and_warmup() -> None:
    n = 60
    candles = _mk_candles(n)
    kc = KeltnerChannels(length=20, atr_length=10)
    out = kc.compute(candles)
    assert set(out.keys()) == {"middle", "upper", "lower"}
    for arr in out.values():
        assert arr.shape == (n,)
    warmup = max(20, 10 + 1)
    for k in ("upper", "lower"):
        assert np.all(np.isnan(out[k][: warmup - 1])), f"{k} warmup wrong"
        assert np.isfinite(out[k][warmup - 1])


def test_original_method_shapes_and_warmup() -> None:
    n = 60
    candles = _mk_candles(n, seed=1.0)
    kc = KeltnerChannels(method="original", length=20, ma_type="SMA")
    out = kc.compute(candles)
    for arr in out.values():
        assert arr.shape == (n,)
    warmup = 20
    for k in ("middle", "upper", "lower"):
        assert np.all(np.isnan(out[k][: warmup - 1])), f"orig {k} warmup wrong"
        assert np.isfinite(out[k][warmup - 1])


@pytest.mark.parametrize("method,ma_type", [
    ("atr", "SMA"),
    ("atr", "EMA"),
    ("atr", "WMA"),
    ("atr", "RMA"),
    ("original", "SMA"),
    ("original", "EMA"),
    ("original", "WMA"),
    ("original", "RMA"),
])
def test_upper_middle_lower_ordering(method: str, ma_type: str) -> None:
    candles = _mk_candles(80, seed=2.0)
    kc = KeltnerChannels(method=method, ma_type=ma_type)
    out = kc.compute(candles)
    mid, upper, lower = out["middle"], out["upper"], out["lower"]
    ok = np.isfinite(mid) & np.isfinite(upper) & np.isfinite(lower)
    assert ok.any(), f"no defined indices for {method}/{ma_type}"
    assert np.all(upper[ok] >= mid[ok] - 1e-12), \
        f"upper < middle for {method}/{ma_type}"
    assert np.all(mid[ok] >= lower[ok] - 1e-12), \
        f"middle < lower for {method}/{ma_type}"


def test_modern_method_matches_reference_formula() -> None:
    """Direct parity check: compute_arr equals the textbook formula."""
    candles = _mk_candles(70, seed=3.5)
    bars = Bars.from_candles(candles)
    kc = KeltnerChannels(length=20, atr_length=10, ma_type="EMA",
                          atr_ma_type="RMA", multiplier=2.0)
    out = kc.compute_arr(bars)

    # Reference: build the centerline + ATR by hand from the same
    # ma_kernels primitives.
    mid_ref = apply_ma("EMA", bars.close, 20)
    tr_ref = true_range(bars.high, bars.low, bars.close)
    atr_ref = apply_ma("RMA", tr_ref, 10)
    upper_ref = mid_ref + 2.0 * atr_ref
    lower_ref = mid_ref - 2.0 * atr_ref

    np.testing.assert_array_equal(out["middle"], mid_ref)
    np.testing.assert_allclose(out["upper"], upper_ref, equal_nan=True)
    np.testing.assert_allclose(out["lower"], lower_ref, equal_nan=True)


def test_original_method_matches_reference_formula() -> None:
    candles = _mk_candles(70, seed=4.5)
    bars = Bars.from_candles(candles)
    kc = KeltnerChannels(method="original", length=20, ma_type="SMA",
                          multiplier=2.0)
    out = kc.compute_arr(bars)

    typical = (bars.high + bars.low + bars.close) / 3.0
    rng = bars.high - bars.low
    mid_ref = apply_ma("SMA", typical, 20)
    band_ref = apply_ma("SMA", rng, 20)
    upper_ref = mid_ref + 2.0 * band_ref
    lower_ref = mid_ref - 2.0 * band_ref

    np.testing.assert_allclose(out["middle"], mid_ref, equal_nan=True)
    np.testing.assert_allclose(out["upper"], upper_ref, equal_nan=True)
    np.testing.assert_allclose(out["lower"], lower_ref, equal_nan=True)


def test_multiplier_scales_half_width_linearly() -> None:
    candles = _mk_candles(60, seed=5.0)
    kc1 = KeltnerChannels(multiplier=1.0)
    kc2 = KeltnerChannels(multiplier=2.0)
    o1 = kc1.compute(candles)
    o2 = kc2.compute(candles)
    # Centerlines are identical (multiplier doesn't affect mid).
    np.testing.assert_allclose(o1["middle"], o2["middle"], equal_nan=True)
    # Half-widths scale by 2x.
    hw1 = o1["upper"] - o1["middle"]
    hw2 = o2["upper"] - o2["middle"]
    ok = np.isfinite(hw1) & np.isfinite(hw2)
    np.testing.assert_allclose(hw2[ok], 2.0 * hw1[ok])


def test_atr_length_changes_modern_but_not_original() -> None:
    candles = _mk_candles(70, seed=6.0)
    # Modern: atr_length affects the bands.
    m_a = KeltnerChannels(atr_length=10).compute(candles)
    m_b = KeltnerChannels(atr_length=20).compute(candles)
    diff = m_a["upper"] - m_b["upper"]
    ok = np.isfinite(diff)
    assert ok.any()
    assert not np.allclose(diff[ok], 0.0), "atr_length must affect bands in modern method"

    # Original: atr_length is inert.
    o_a = KeltnerChannels(method="original", atr_length=10).compute(candles)
    o_b = KeltnerChannels(method="original", atr_length=20).compute(candles)
    np.testing.assert_allclose(o_a["upper"], o_b["upper"], equal_nan=True)
    np.testing.assert_allclose(o_a["lower"], o_b["lower"], equal_nan=True)
    np.testing.assert_allclose(o_a["middle"], o_b["middle"], equal_nan=True)


@pytest.mark.parametrize("kwargs", [
    {"length": 1},
    {"length": 0},
    {"multiplier": 0},
    {"multiplier": -1.0},
    {"atr_length": 1},
    {"ma_type": "garbage"},
    {"atr_ma_type": "garbage"},
    {"method": "weird"},
])
def test_constructor_validation(kwargs) -> None:
    with pytest.raises(ValueError):
        KeltnerChannels(**kwargs)


def test_empty_candles_returns_empty_arrays() -> None:
    kc = KeltnerChannels()
    out = kc.compute([])
    for k in ("middle", "upper", "lower"):
        assert out[k].shape == (0,)


def test_indicator_config_round_trip_modern() -> None:
    cfg = IndicatorConfig.from_dict({
        "kind_id": "keltner",
        "params": {"length": 14, "multiplier": 1.5, "atr_length": 14,
                    "ma_type": "EMA", "atr_ma_type": "RMA",
                    "method": "atr"},
    })
    assert cfg.kind_id == "keltner"
    assert not cfg.unknown
    inst = cfg.make_indicator()
    assert isinstance(inst, KeltnerChannels)
    assert inst.length == 14
    assert inst.multiplier == 1.5
    assert inst.atr_length == 14
    assert inst.ma_type == "EMA"
    assert inst.method == "atr"
    # Round-trip through to_dict.
    d = cfg.to_dict()
    assert d["kind_id"] == "keltner"
    assert d["params"]["length"] == 14


def test_indicator_config_round_trip_original() -> None:
    cfg = IndicatorConfig.from_dict({
        "kind_id": "keltner",
        "params": {"length": 20, "multiplier": 2.0,
                    "ma_type": "SMA", "method": "original"},
    })
    inst = cfg.make_indicator()
    assert isinstance(inst, KeltnerChannels)
    assert inst.method == "original"
    assert inst.ma_type == "SMA"
