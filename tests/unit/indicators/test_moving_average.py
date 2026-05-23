"""Tests for the consolidated :class:`MovingAverage` indicator.

Covers compute parity with the legacy ``SMA`` / ``EMA`` classes, the
new ``source`` parameter, the persisted-config migration via
``migrate_kind_id`` / ``IndicatorConfig.from_dict``, the type-derived
default colours, and the per-session "last used MA type" memory on the
dialog.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from tradinglab.core.bars import Bars
from tradinglab.indicators import (
    EMA,
    INDICATORS,
    SMA,
    MovingAverage,
    factory_by_kind_id,
)
from tradinglab.indicators.base import (
    _KIND_ID_MIGRATIONS,
    _LEGACY_MA_OUTPUT_KEYS,
    migrate_kind_id,
)
from tradinglab.indicators.config import IndicatorConfig
from tradinglab.indicators.moving_averages import (
    _DEFAULT_COLOR_BY_MA,
    SOURCE_TYPES,
)
from tradinglab.models import Candle

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_candles(n: int, *, seed: int = 7) -> list[Candle]:
    import datetime as _dt
    rng = np.random.default_rng(seed)
    closes = 100.0 + np.cumsum(rng.normal(0, 0.5, size=n))
    opens = closes + rng.normal(0, 0.1, size=n)
    highs = np.maximum(opens, closes) + rng.uniform(0.05, 0.30, size=n)
    lows = np.minimum(opens, closes) - rng.uniform(0.05, 0.30, size=n)
    base = _dt.datetime(2025, 1, 1, 9, 30)
    out: list[Candle] = []
    for i in range(n):
        out.append(Candle(
            date=base + _dt.timedelta(minutes=i),
            open=float(opens[i]), high=float(highs[i]),
            low=float(lows[i]), close=float(closes[i]),
            volume=int(1_000 + rng.integers(0, 5_000)),
        ))
    return out


def _bars(n: int = 60) -> tuple[Bars, list[Candle]]:
    cs = _make_candles(n)
    return Bars.from_candles(cs), cs


# ---------------------------------------------------------------------------
# Registration / discovery
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_registered_under_moving_average_name(self):
        assert "Moving Average" in INDICATORS
        assert INDICATORS["Moving Average"] is MovingAverage

    def test_legacy_sma_ema_not_in_menu_registry(self):
        # Legacy classes are intentionally excluded from the Add menu.
        assert "SMA" not in INDICATORS
        assert "EMA" not in INDICATORS

    def test_kind_id_ma_resolves_to_moving_average(self):
        pair = factory_by_kind_id("ma")
        assert pair is not None
        name, cls = pair
        assert name == "Moving Average"
        assert cls is MovingAverage

    def test_legacy_sma_ema_kind_ids_still_resolvable(self):
        # In-memory configs created via IndicatorConfig(kind_id="sma", ...)
        # without going through from_dict must still find a factory.
        pair_sma = factory_by_kind_id("sma")
        pair_ema = factory_by_kind_id("ema")
        assert pair_sma is not None and pair_sma[1] is SMA
        assert pair_ema is not None and pair_ema[1] is EMA


# ---------------------------------------------------------------------------
# Compute parity & sources
# ---------------------------------------------------------------------------


class TestCompute:
    def test_sma_matches_legacy(self):
        bars, _ = _bars(60)
        legacy = SMA(20).compute_arr(bars)["sma"]
        new = MovingAverage(20, "SMA", "Close").compute_arr(bars)["ma"]
        np.testing.assert_allclose(legacy, new, equal_nan=True)

    def test_ema_matches_legacy(self):
        bars, _ = _bars(60)
        legacy = EMA(20).compute_arr(bars)["ema"]
        new = MovingAverage(20, "EMA", "Close").compute_arr(bars)["ma"]
        np.testing.assert_allclose(legacy, new, equal_nan=True)

    def test_all_four_types_produce_finite_tail(self):
        bars, _ = _bars(80)
        for t in ("SMA", "EMA", "WMA", "RMA"):
            out = MovingAverage(20, t, "Close").compute_arr(bars)["ma"]
            assert out.shape == (80,)
            assert np.isfinite(out[-1]), f"{t} tail not finite"

    @pytest.mark.parametrize("source", list(SOURCE_TYPES))
    def test_every_source_runs_and_is_finite(self, source):
        bars, _ = _bars(80)
        out = MovingAverage(20, "SMA", source).compute_arr(bars)["ma"]
        assert np.isfinite(out[-1]), f"source={source} tail not finite"

    def test_source_hl2_equals_manual_midpoint(self):
        bars, _ = _bars(40)
        manual = ((bars.high + bars.low) / 2.0)
        # SMA on HL2 should equal the trailing mean of the manual array
        ma = MovingAverage(20, "SMA", "HL2").compute_arr(bars)["ma"]
        # Last bar's MA(20) on HL2 = mean of last 20 manual values
        np.testing.assert_allclose(ma[-1], manual[-20:].mean())

    def test_source_ohlc4_equals_manual(self):
        bars, _ = _bars(40)
        manual = (bars.open + bars.high + bars.low + bars.close) / 4.0
        ma = MovingAverage(20, "SMA", "OHLC4").compute_arr(bars)["ma"]
        np.testing.assert_allclose(ma[-1], manual[-20:].mean())


# ---------------------------------------------------------------------------
# Construction validation
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_zero_length_rejected(self):
        with pytest.raises(ValueError):
            MovingAverage(0, "SMA", "Close")

    def test_negative_length_rejected(self):
        with pytest.raises(ValueError):
            MovingAverage(-5, "SMA", "Close")

    def test_unknown_ma_type_rejected(self):
        with pytest.raises(ValueError):
            MovingAverage(20, "ZZZ", "Close")

    def test_unknown_source_rejected(self):
        with pytest.raises(ValueError):
            MovingAverage(20, "SMA", "Tertiary")

    def test_ma_type_case_insensitive(self):
        # Normalisation: 'sma' / 'Sma' should canonicalise to 'SMA'.
        ma = MovingAverage(20, "sma", "Close")
        assert ma.ma_type == "SMA"

    def test_source_case_insensitive(self):
        ma = MovingAverage(20, "SMA", "close")
        assert ma.source == "Close"


# ---------------------------------------------------------------------------
# Legend / display name
# ---------------------------------------------------------------------------


class TestLegendLabel:
    def test_close_source_omits_source_tag(self):
        assert MovingAverage(20, "SMA", "Close").name == "SMA(20)"
        assert MovingAverage(9, "EMA", "Close").name == "EMA(9)"

    def test_non_close_source_includes_tag(self):
        assert MovingAverage(20, "SMA", "HLC3").name == "SMA(20,HLC3)"
        assert MovingAverage(50, "WMA", "OHLC4").name == "WMA(50,OHLC4)"


# ---------------------------------------------------------------------------
# Default colors
# ---------------------------------------------------------------------------


class TestDefaultColors:
    def test_each_type_has_unique_default_color(self):
        colors = [_DEFAULT_COLOR_BY_MA[t] for t in ("SMA", "EMA", "WMA", "RMA")]
        assert len(set(colors)) == 4

    def test_sma_default_is_legacy_blue(self):
        assert _DEFAULT_COLOR_BY_MA["SMA"] == "#1f77b4"
        ma = MovingAverage(20, "SMA", "Close")
        assert ma.style_overrides["ma"].color == "#1f77b4"

    def test_ema_default_is_legacy_orange(self):
        assert _DEFAULT_COLOR_BY_MA["EMA"] == "#ff7f0e"
        ma = MovingAverage(20, "EMA", "Close")
        assert ma.style_overrides["ma"].color == "#ff7f0e"


# ---------------------------------------------------------------------------
# Migration: kind_id rewrite + style key remap
# ---------------------------------------------------------------------------


class TestMigration:
    def test_kind_id_migrations_contain_sma_and_ema(self):
        assert "sma" in _KIND_ID_MIGRATIONS
        assert "ema" in _KIND_ID_MIGRATIONS

    def test_legacy_output_key_map_contains_sma_and_ema(self):
        assert _LEGACY_MA_OUTPUT_KEYS == {"sma": "sma", "ema": "ema"}

    def test_migrate_sma_injects_ma_type(self):
        # The SMA → MA migration is chart-only (scanner FieldRefs
        # keep ``id="sma"``); pass ``include_chart_only=True`` to
        # exercise the chart hydration path that IndicatorConfig.from_dict
        # uses.
        new_kind, new_params = migrate_kind_id(
            "sma", {"length": 50}, include_chart_only=True)
        assert new_kind == "ma"
        assert new_params["ma_type"] == "SMA"
        assert new_params["length"] == 50

    def test_migrate_ema_injects_ma_type(self):
        new_kind, new_params = migrate_kind_id(
            "ema", {"length": 9}, include_chart_only=True)
        assert new_kind == "ma"
        assert new_params["ma_type"] == "EMA"
        assert new_params["length"] == 9

    def test_user_supplied_ma_type_overrides_default(self):
        new_kind, new_params = migrate_kind_id(
            "sma", {"length": 20, "ma_type": "WMA"},
            include_chart_only=True)
        assert new_params["ma_type"] == "WMA"

    def test_scanner_default_keeps_legacy_sma_ema_kind_ids(self):
        # The scanner / FieldRef hydration path uses the default
        # ``include_chart_only=False``; SMA/EMA stay as legacy field
        # ids so the scanner allowlist can still resolve them.
        sk, _ = migrate_kind_id("sma", {"length": 50})
        ek, _ = migrate_kind_id("ema", {"length": 9})
        assert sk == "sma"
        assert ek == "ema"

    def test_from_dict_remaps_legacy_sma_style_key(self):
        cfg = IndicatorConfig.from_dict({
            "kind_id": "sma",
            "params": {"length": 20},
            "style": {
                "sma": {"color": "#cc0000", "width": 2.0, "visible": True},
            },
        })
        assert cfg.kind_id == "ma"
        assert cfg.params["ma_type"] == "SMA"
        assert "ma" in cfg.style
        assert cfg.style["ma"].color == "#cc0000"
        assert cfg.style["ma"].width == pytest.approx(2.0)

    def test_from_dict_remaps_legacy_ema_style_key(self):
        cfg = IndicatorConfig.from_dict({
            "kind_id": "ema",
            "params": {"length": 9},
            "style": {
                "ema": {"color": "#0066cc", "width": 1.5, "visible": False},
            },
        })
        assert cfg.kind_id == "ma"
        assert cfg.params["ma_type"] == "EMA"
        assert "ma" in cfg.style
        assert cfg.style["ma"].color == "#0066cc"
        assert cfg.style["ma"].visible is False

    def test_from_dict_migrated_config_is_known(self):
        # Migrated config must not be flagged ``unknown`` — the
        # render layer skips unknown configs.
        cfg = IndicatorConfig.from_dict({
            "kind_id": "sma",
            "params": {"length": 20},
        })
        assert cfg.unknown is False

    def test_round_trip_preserves_color(self):
        # Persist a legacy SMA config, round-trip through serialization
        # twice — color must survive.
        cfg1 = IndicatorConfig.from_dict({
            "kind_id": "sma",
            "params": {"length": 20},
            "style": {
                "sma": {"color": "#abcdef", "width": 1.4, "visible": True},
            },
        })
        cfg2 = IndicatorConfig.from_dict(cfg1.to_dict())
        assert cfg2.kind_id == "ma"
        assert cfg2.style["ma"].color == "#abcdef"


# ---------------------------------------------------------------------------
# Incremental protocol
# ---------------------------------------------------------------------------


class TestIncremental:
    def test_sma_close_inc_matches_full_compute(self):
        bars30, cs30 = _bars(30)
        ma = MovingAverage(10, "SMA", "Close")
        state = ma.inc_init(bars30)
        # Extend by 5 bars and inc_step.
        bars35, _ = _bars(35)
        # Re-seed with the same RNG so first 30 bars are identical.
        # Easier: just use the original 30 bars + new candles appended.
        cs35 = cs30 + _make_candles(5, seed=99)
        bars_new = Bars.from_candles(cs35)
        new_state = ma.inc_step(state, bars_new, prev_len=30)
        full = ma.compute_arr(bars_new)["ma"]
        np.testing.assert_allclose(new_state["output"]["ma"], full, equal_nan=True)

    def test_ema_close_inc_matches_full_compute(self):
        bars30, cs30 = _bars(30)
        ma = MovingAverage(10, "EMA", "Close")
        state = ma.inc_init(bars30)
        cs35 = cs30 + _make_candles(5, seed=99)
        bars_new = Bars.from_candles(cs35)
        new_state = ma.inc_step(state, bars_new, prev_len=30)
        full = ma.compute_arr(bars_new)["ma"]
        np.testing.assert_allclose(new_state["output"]["ma"], full, equal_nan=True)

    def test_wma_raises_to_force_full_recompute(self):
        bars, _ = _bars(30)
        ma = MovingAverage(10, "WMA", "Close")
        with pytest.raises(ValueError):
            ma.inc_init(bars)

    def test_non_close_source_raises(self):
        bars, _ = _bars(30)
        ma = MovingAverage(10, "SMA", "HL2")
        with pytest.raises(ValueError):
            ma.inc_init(bars)


# ---------------------------------------------------------------------------
# Per-session last-used MA type memory
# ---------------------------------------------------------------------------


class TestLastUsedTypeMemory:
    def test_class_attr_default_is_sma(self):
        # Lazy-import to avoid pulling Tk into module collection.
        from tradinglab.gui.indicator_dialog import IndicatorDialog
        # The class attribute must exist and start as "SMA" so first-run
        # behaviour matches the schema default.
        assert IndicatorDialog._last_used_ma_type == "SMA"

    def test_class_attr_is_classvar_not_instance(self):
        # If someone accidentally moves the attribute to __init__,
        # the memory is lost on every dialog open. Guard against
        # that by asserting the attribute exists on the class itself.
        from tradinglab.gui.indicator_dialog import IndicatorDialog
        assert "_last_used_ma_type" in IndicatorDialog.__dict__
