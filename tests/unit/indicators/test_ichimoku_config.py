from __future__ import annotations

from tradinglab import constants
from tradinglab.gui.indicator_dialog import IndicatorDialog
from tradinglab.indicators.base import LineStyle, factory_is_available_for
from tradinglab.indicators.config import IndicatorConfig, IndicatorManager
from tradinglab.indicators.ichimoku import IchimokuCloud


def _config() -> IndicatorConfig:
    return IndicatorConfig(
        kind_id="ichimoku",
        display_name="Ichimoku",
        params={
            "conversion_period": 9,
            "base_period": 26,
            "span_b_period": 52,
            "displacement": 26,
        },
        fill_visibility={"cloud": False},
        scopes=frozenset({"main", "compare"}),
    )


def test_fill_visibility_round_trips_without_entering_params() -> None:
    original = _config()
    payload = original.to_dict()
    assert payload["fill_visibility"] == {"cloud": False}
    assert "cloud" not in payload["params"]

    restored = IndicatorConfig.from_dict(payload)
    assert restored.fill_visibility == {"cloud": False}
    assert restored.params == original.params


def test_missing_fill_visibility_is_backward_compatible() -> None:
    payload = _config().to_dict()
    payload.pop("fill_visibility")
    assert IndicatorConfig.from_dict(payload).fill_visibility == {}


def test_symbol_availability_filters_dynamically_without_mutation() -> None:
    manager = IndicatorManager()
    cfg = manager.add(_config())
    assert manager.applicable("main", "1d", symbol="AAPL") == [cfg]
    assert manager.applicable("main", "1d", symbol="^VIX/15.87") == [cfg]
    assert manager.applicable("main", "1d", symbol="AMD/NVDA") == []
    assert cfg.visible is True
    assert cfg.scopes == frozenset({"main", "compare"})


def test_factory_availability_combines_interval_and_symbol_hooks() -> None:
    assert factory_is_available_for(
        IchimokuCloud, "1d", {}, symbol="AAPL",
    ).ok
    result = factory_is_available_for(
        IchimokuCloud, "1d", {}, symbol="AMD/NVDA",
    )
    assert result.ok is False
    assert "approximate" in result.reason


def test_dialog_preserves_visible_override_for_hidden_default() -> None:
    overrides = IndicatorDialog._visibility_overrides_for_kind(
        "ichimoku",
        {"chikou_source": LineStyle(visible=True)},
    )
    assert overrides == {"chikou_source": True}


def test_dialog_senkou_defaults_read_live_semantic_palette(monkeypatch) -> None:
    monkeypatch.setattr(constants, "BULL_COLOR", "#e69f00")
    monkeypatch.setattr(constants, "BEAR_COLOR", "#56b4e9")
    styles = IndicatorDialog._default_style_for_kind("ichimoku")
    assert styles["senkou_span_a_raw"].color == "#e69f00"
    assert styles["senkou_span_b_raw"].color == "#56b4e9"
