"""Unit tests for :class:`MACD`.

Covers:

* Registry presence + ``factory_by_kind_id``.
* Default constructor + name encoding (default suppression).
* Shape + warmup NaN regions (``slow-1`` for macd, ``slow+signal-2``
  for signal/histogram).
* Reference-formula parity: ``macd = fast_MA - slow_MA``,
  ``signal = MA(macd)``, ``histogram = macd - signal``.
* Source variants: ``close`` / ``hl2`` / ``hlc3`` / ``ohlc4``.
* Kernel variants: ``SMA`` / ``EMA`` / ``WMA`` / ``RMA``.
* Histogram classification helper (4-class momentum scheme).
* Constructor validation: every documented invalid combination
  raises ``ValueError`` before any mutation.
* Persistence round-trip via :class:`IndicatorConfig`.
* Empty-input safety.
"""

from __future__ import annotations

import datetime as dt
from typing import List

import numpy as np
import pytest

from tradinglab.core.bars import Bars
from tradinglab.indicators import INDICATORS, MACD
from tradinglab.indicators.base import factory_by_kind_id
from tradinglab.indicators.config import IndicatorConfig
from tradinglab.indicators.ma_kernels import apply_ma
from tradinglab.indicators.macd import classify_histogram
from tradinglab.models import Candle


def _mk_candles(n: int, seed: float = 0.0) -> List[Candle]:
    """Deterministic OHLC walk with a sinusoid + linear drift."""
    start = dt.datetime(2024, 1, 2, 9, 30, tzinfo=dt.timezone.utc)
    rng = np.random.default_rng(int(seed * 1000) or 11)
    out: List[Candle] = []
    for i in range(n):
        drift = 0.05 * i + seed
        base = 100.0 + drift + 5.0 * np.sin(i * 0.18)
        o = base + 0.05 * rng.standard_normal()
        c = base + 0.10 * np.cos(i * 0.12) + 0.05 * rng.standard_normal()
        h = max(o, c) + abs(0.4 * np.sin(i * 0.27)) + 0.05
        l = min(o, c) - abs(0.3 * np.cos(i * 0.21)) - 0.05
        out.append(
            Candle(
                date=start + dt.timedelta(minutes=5 * i),
                open=float(o), high=float(h), low=float(l), close=float(c),
                volume=1000, session="regular",
            )
        )
    return out


def test_registered_and_factory_by_kind_id() -> None:
    assert "MACD" in INDICATORS
    pair = factory_by_kind_id("macd")
    assert pair is not None
    name, fac = pair
    assert name == "MACD"
    assert fac is MACD


def test_class_metadata() -> None:
    assert MACD.kind_id == "macd"
    assert MACD.overlay is False
    assert MACD.pane_group == "macd"
    assert 0.0 in MACD.reference_levels
    # Histogram is the only non-line output.
    assert MACD.output_kinds["macd"] == "line"
    assert MACD.output_kinds["signal"] == "line"
    assert MACD.output_kinds["histogram"] == "histogram"
    assert len(MACD.histogram_palette) == 4


def test_params_schema_choices() -> None:
    schema = {p.name: p for p in MACD.params_schema}
    assert set(schema) == {
        "fast_length", "slow_length", "signal_length", "ma_type", "source",
    }
    assert schema["fast_length"].default == 12
    assert schema["slow_length"].default == 26
    assert schema["signal_length"].default == 9
    assert schema["ma_type"].default == "EMA"
    assert set(schema["ma_type"].choices) == {"SMA", "EMA", "WMA", "RMA"}
    assert schema["source"].default == "close"
    assert set(schema["source"].choices) == {"close", "hl2", "hlc3", "ohlc4"}


def test_default_constructor() -> None:
    m = MACD()
    assert m.fast_length == 12
    assert m.slow_length == 26
    assert m.signal_length == 9
    assert m.ma_type == "EMA"
    assert m.source == "close"
    assert m.name == "MACD(12,26,9)"


def test_name_encoding_non_default_ma_type() -> None:
    assert MACD(ma_type="SMA").name == "MACD(12,26,9,SMA)"
    assert MACD(ma_type="WMA").name == "MACD(12,26,9,WMA)"
    assert MACD(ma_type="RMA").name == "MACD(12,26,9,RMA)"


def test_name_encoding_non_default_source() -> None:
    assert MACD(source="hl2").name == "MACD(12,26,9,hl2)"
    assert MACD(source="hlc3").name == "MACD(12,26,9,hlc3)"
    assert MACD(source="ohlc4").name == "MACD(12,26,9,ohlc4)"


def test_name_encoding_both_overrides() -> None:
    assert MACD(ma_type="SMA", source="hl2").name == "MACD(12,26,9,SMA,hl2)"


def test_name_encoding_custom_lengths() -> None:
    assert MACD(fast_length=5, slow_length=35, signal_length=5).name == \
        "MACD(5,35,5)"


def test_compute_output_shape_and_keys() -> None:
    candles = _mk_candles(200)
    out = MACD().compute(candles)
    assert set(out) == {"macd", "signal", "histogram"}
    assert all(v.shape == (200,) for v in out.values())


def test_warmup_nan_regions_ema() -> None:
    """EMA kernel: macd NaN until slow-1, signal/hist until slow+sig-2."""
    candles = _mk_candles(200)
    out = MACD().compute(candles)  # 12/26/9 EMA
    # MACD line: first valid index = slow_length - 1 = 25.
    macd_first = int(np.argmax(np.isfinite(out["macd"])))
    assert macd_first == 25
    assert np.all(np.isnan(out["macd"][:25]))
    assert np.all(np.isfinite(out["macd"][25:]))
    # Signal: 25 + signal_length - 1 = 33.
    sig_first = int(np.argmax(np.isfinite(out["signal"])))
    assert sig_first == 33
    assert np.all(np.isnan(out["signal"][:33]))
    assert np.all(np.isfinite(out["signal"][33:]))
    # Histogram inherits signal warmup.
    hist_first = int(np.argmax(np.isfinite(out["histogram"])))
    assert hist_first == 33


def test_warmup_nan_regions_sma() -> None:
    candles = _mk_candles(120)
    out = MACD(ma_type="SMA").compute(candles)
    macd_first = int(np.argmax(np.isfinite(out["macd"])))
    assert macd_first == 25  # slow-1
    sig_first = int(np.argmax(np.isfinite(out["signal"])))
    assert sig_first == 33  # slow + signal - 2


def test_warmup_short_series_returns_all_nan() -> None:
    candles = _mk_candles(5)  # < slow_length
    out = MACD().compute(candles)
    assert np.all(np.isnan(out["macd"]))
    assert np.all(np.isnan(out["signal"]))
    assert np.all(np.isnan(out["histogram"]))


def test_reference_formula_parity_ema_close() -> None:
    candles = _mk_candles(300)
    bars = Bars.from_candles(candles)
    out = MACD().compute_arr(bars)
    expected_fast = apply_ma("EMA", bars.close.astype(np.float64), 12)
    expected_slow = apply_ma("EMA", bars.close.astype(np.float64), 26)
    expected_macd = expected_fast - expected_slow
    expected_signal = apply_ma("EMA", expected_macd, 9)
    expected_hist = expected_macd - expected_signal
    np.testing.assert_allclose(
        out["macd"], expected_macd, equal_nan=True, rtol=0, atol=1e-12,
    )
    np.testing.assert_allclose(
        out["signal"], expected_signal, equal_nan=True, rtol=0, atol=1e-12,
    )
    np.testing.assert_allclose(
        out["histogram"], expected_hist, equal_nan=True, rtol=0, atol=1e-12,
    )


def test_histogram_is_macd_minus_signal() -> None:
    candles = _mk_candles(250)
    for ma in ("SMA", "EMA", "WMA", "RMA"):
        out = MACD(ma_type=ma).compute(candles)
        finite = np.isfinite(out["histogram"])
        np.testing.assert_allclose(
            out["histogram"][finite],
            out["macd"][finite] - out["signal"][finite],
            rtol=0, atol=1e-12,
        )


def test_source_hl2() -> None:
    candles = _mk_candles(200)
    bars = Bars.from_candles(candles)
    out = MACD(source="hl2").compute_arr(bars)
    hl2 = ((bars.high + bars.low) / 2.0).astype(np.float64)
    expected_macd = apply_ma("EMA", hl2, 12) - apply_ma("EMA", hl2, 26)
    np.testing.assert_allclose(
        out["macd"], expected_macd, equal_nan=True, rtol=0, atol=1e-12,
    )


def test_source_hlc3() -> None:
    candles = _mk_candles(200)
    bars = Bars.from_candles(candles)
    out = MACD(source="hlc3").compute_arr(bars)
    hlc3 = ((bars.high + bars.low + bars.close) / 3.0).astype(np.float64)
    expected_macd = apply_ma("EMA", hlc3, 12) - apply_ma("EMA", hlc3, 26)
    np.testing.assert_allclose(
        out["macd"], expected_macd, equal_nan=True, rtol=0, atol=1e-12,
    )


def test_source_ohlc4() -> None:
    candles = _mk_candles(200)
    bars = Bars.from_candles(candles)
    out = MACD(source="ohlc4").compute_arr(bars)
    ohlc4 = ((bars.open + bars.high + bars.low + bars.close) / 4.0).astype(
        np.float64,
    )
    expected_macd = apply_ma("EMA", ohlc4, 12) - apply_ma("EMA", ohlc4, 26)
    np.testing.assert_allclose(
        out["macd"], expected_macd, equal_nan=True, rtol=0, atol=1e-12,
    )


def test_different_sources_produce_different_macd() -> None:
    candles = _mk_candles(200)
    a = MACD(source="close").compute(candles)["macd"]
    b = MACD(source="hl2").compute(candles)["macd"]
    finite = np.isfinite(a) & np.isfinite(b)
    # The sinusoidal price's H/L straddle close, so hl2 ≠ close.
    assert not np.allclose(a[finite], b[finite])


def test_kernel_variants_all_compute() -> None:
    candles = _mk_candles(200)
    results = {ma: MACD(ma_type=ma).compute(candles) for ma in
               ("SMA", "EMA", "WMA", "RMA")}
    for ma, out in results.items():
        finite = np.isfinite(out["macd"])
        assert finite.any(), f"{ma} produced no finite MACD values"
        finite_sig = np.isfinite(out["signal"])
        assert finite_sig.any(), f"{ma} produced no finite signal values"


def test_constructor_validation_fast_length() -> None:
    with pytest.raises(ValueError, match="fast_length"):
        MACD(fast_length=1)
    with pytest.raises(ValueError, match="fast_length"):
        MACD(fast_length=0)


def test_constructor_validation_slow_length() -> None:
    with pytest.raises(ValueError, match="slow_length"):
        MACD(slow_length=1)
    with pytest.raises(ValueError, match="slow_length"):
        MACD(fast_length=12, slow_length=0)


def test_constructor_validation_signal_length() -> None:
    with pytest.raises(ValueError, match="signal_length"):
        MACD(signal_length=1)
    with pytest.raises(ValueError, match="signal_length"):
        MACD(signal_length=0)


def test_constructor_validation_slow_greater_than_fast() -> None:
    with pytest.raises(ValueError, match="slow_length must be > fast_length"):
        MACD(fast_length=26, slow_length=12)
    with pytest.raises(ValueError, match="slow_length must be > fast_length"):
        MACD(fast_length=12, slow_length=12)  # equal also rejected


def test_constructor_validation_ma_type() -> None:
    with pytest.raises(ValueError, match="ma_type"):
        MACD(ma_type="bogus")


def test_constructor_validation_source() -> None:
    with pytest.raises(ValueError, match="source"):
        MACD(source="bogus")


def test_classify_histogram_four_classes() -> None:
    # Series exercises every class:
    # 1.0, 2.0 → above 0, rising (class 0)
    # 2.0, 1.5 → above 0, falling (class 1)
    # 1.5, -0.5 → falling (still falling; below 0 → class 3)
    # -0.5, -1.0 → below 0, falling (class 3)
    # -1.0, -0.5 → below 0, rising (class 2)
    # -0.5, 0.5 → rising; above 0 → class 0
    hist = np.array([1.0, 2.0, 1.5, -0.5, -1.0, -0.5, 0.5])
    classes = classify_histogram(hist)
    # First bar: above 0 → treated as "rising" → class 0.
    assert classes[0] == 0
    # 1.0 → 2.0 rising above → 0
    assert classes[1] == 0
    # 2.0 → 1.5 falling above → 1
    assert classes[2] == 1
    # 1.5 → -0.5: dropped below 0, falling → 3
    assert classes[3] == 3
    # -0.5 → -1.0: below 0, falling → 3
    assert classes[4] == 3
    # -1.0 → -0.5: below 0, rising → 2
    assert classes[5] == 2
    # -0.5 → 0.5: above 0, rising → 0
    assert classes[6] == 0


def test_classify_histogram_skips_leading_nan() -> None:
    hist = np.array([np.nan, np.nan, 1.0, 2.0, 1.5])
    classes = classify_histogram(hist)
    assert int(classes[0]) == -1
    assert int(classes[1]) == -1
    assert int(classes[2]) == 0  # first finite → "rising"
    assert int(classes[3]) == 0  # 1.0 → 2.0 rising
    assert int(classes[4]) == 1  # 2.0 → 1.5 falling


def test_classify_histogram_empty() -> None:
    out = classify_histogram(np.array([], dtype=float))
    assert out.shape == (0,)


def test_persistence_round_trip_defaults() -> None:
    cfg = IndicatorConfig.from_dict({"kind_id": "macd", "params": {}})
    inst = cfg.make_indicator()
    assert isinstance(inst, MACD)
    assert inst.fast_length == 12
    assert inst.slow_length == 26
    assert inst.signal_length == 9
    assert inst.ma_type == "EMA"
    assert inst.source == "close"
    d = cfg.to_dict()
    assert d["kind_id"] == "macd"


def test_persistence_round_trip_custom_params() -> None:
    cfg = IndicatorConfig.from_dict({
        "kind_id": "macd",
        "params": {
            "fast_length": 8, "slow_length": 21, "signal_length": 5,
            "ma_type": "SMA", "source": "hl2",
        },
    })
    inst = cfg.make_indicator()
    assert isinstance(inst, MACD)
    assert inst.fast_length == 8
    assert inst.slow_length == 21
    assert inst.signal_length == 5
    assert inst.ma_type == "SMA"
    assert inst.source == "hl2"
    # Round-trip via to_dict / from_dict.
    cfg2 = IndicatorConfig.from_dict(cfg.to_dict())
    inst2 = cfg2.make_indicator()
    assert inst2.fast_length == 8
    assert inst2.source == "hl2"


def test_pane_group_via_config_resolver() -> None:
    from tradinglab.indicators.config import effective_pane_group
    cfg = IndicatorConfig.from_dict({"kind_id": "macd", "params": {}})
    assert effective_pane_group(cfg) == "macd"


def test_compute_handles_empty_candles() -> None:
    out = MACD().compute([])
    assert out["macd"].shape == (0,)
    assert out["signal"].shape == (0,)
    assert out["histogram"].shape == (0,)
