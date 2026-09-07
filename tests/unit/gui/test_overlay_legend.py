"""Tests for the retired legend module's compatibility enumeration helper."""

from __future__ import annotations

import pytest

pytest.importorskip("tkinter")

from tradinglab.gui.overlay_legend import collect_overlay_configs
from tradinglab.indicators.base import LineStyle
from tradinglab.indicators.config import IndicatorConfig, IndicatorManager


@pytest.fixture()
def manager() -> IndicatorManager:
    return IndicatorManager()


def _cfg(
    kind_id: str,
    *,
    scope: str = "main",
    intervals: tuple[str, ...] = (),
    visible: bool = True,
) -> IndicatorConfig:
    return IndicatorConfig(
        kind_id=kind_id,
        display_name=kind_id.upper(),
        params={"length": 20},
        style={kind_id: LineStyle(color="#ff8800", width=1.2, visible=True)},
        intervals=intervals,
        scopes=frozenset({scope}),
        visible=visible,
    )


def test_collect_overlay_configs_includes_hidden(manager):
    visible = _cfg("sma")
    hidden = _cfg("ema", visible=False)
    manager.add(visible)
    manager.add(hidden)

    out = collect_overlay_configs(manager, "main", "1d")

    assert {cfg.id for cfg in out} == {visible.id, hidden.id}


def test_collect_overlay_configs_filters_scope_and_interval(manager):
    main = _cfg("sma")
    compare = _cfg("ema", scope="compare")
    minutes = _cfg("sma", intervals=("5m",))
    manager.add(main)
    manager.add(compare)
    manager.add(minutes)

    assert {cfg.id for cfg in collect_overlay_configs(manager, "main", "1d")} == {
        main.id
    }
    assert {cfg.id for cfg in collect_overlay_configs(manager, "main", "5m")} == {
        main.id,
        minutes.id,
    }


def test_collect_overlay_configs_excludes_non_overlay_kinds(manager):
    rsi = _cfg("rsi")
    manager.add(rsi)

    assert rsi.id not in {
        cfg.id for cfg in collect_overlay_configs(manager, "main", "1d")
    }


def test_overlay_legend_module_no_longer_exports_runtime_widget():
    import tradinglab.gui.overlay_legend as module

    assert not hasattr(module, "OverlayLegend")
