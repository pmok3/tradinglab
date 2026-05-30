"""Unit tests for the pure in-readout overlay legend row builder.

These exercise :func:`gui.readout_legend.build_overlay_legend_rows`
without Tk / matplotlib — it must be a pure function of the indicator
manager + theme.
"""

from __future__ import annotations

from tradinglab.gui.readout_legend import (
    ReadoutLegendRow,
    build_overlay_legend_rows,
)
from tradinglab.indicators.base import LineStyle
from tradinglab.indicators.config import IndicatorConfig, IndicatorManager


def _sma(visible: bool = True, color: str = "#ff8800") -> IndicatorConfig:
    return IndicatorConfig(
        kind_id="sma",
        display_name="SMA(20)",
        params={"length": 20},
        style={"sma": LineStyle(color=color, width=1.2, visible=True)},
        intervals=(),
        scopes=frozenset({"main"}),
        visible=visible,
    )


def _ema(visible: bool = True) -> IndicatorConfig:
    return IndicatorConfig(
        kind_id="ema",
        display_name="EMA(50)",
        params={"length": 50},
        style={"ema": LineStyle(color="#00aaff", width=1.2, visible=True)},
        intervals=(),
        scopes=frozenset({"main"}),
        visible=visible,
    )


def _bbands(visible: bool = True) -> IndicatorConfig:
    return IndicatorConfig(
        kind_id="bbands",
        display_name="BB(20,2)",
        params={"length": 20, "mult": 2.0},
        style={},
        intervals=(),
        scopes=frozenset({"main"}),
        visible=visible,
    )


def test_empty_manager_yields_no_rows():
    rows = build_overlay_legend_rows(IndicatorManager(), "main", "1d")
    assert rows == []


def test_single_output_row_uses_display_name_and_color():
    m = IndicatorManager()
    cfg = m.add(_sma(color="#ff8800"))
    rows = build_overlay_legend_rows(m, "main", "1d")
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, ReadoutLegendRow)
    assert row.config_id == cfg.id
    assert row.label == "SMA(20)"
    assert row.color == "#ff8800"
    assert row.visible is True


def test_two_overlays_in_insertion_order():
    m = IndicatorManager()
    c1 = m.add(_sma())
    c2 = m.add(_ema())
    rows = build_overlay_legend_rows(m, "main", "1d")
    assert [r.config_id for r in rows] == [c1.id, c2.id]
    assert [r.label for r in rows] == ["SMA(20)", "EMA(50)"]


def test_multi_output_expands_one_row_per_key_with_qualifier():
    m = IndicatorManager()
    cfg = m.add(_bbands())
    rows = build_overlay_legend_rows(m, "main", "1d")
    # Bollinger Bands has >1 default_style output → one row per key,
    # each label prefixed with the display name.
    assert len(rows) > 1
    assert all(r.config_id == cfg.id for r in rows)
    assert all(r.label.startswith("BB(20,2) ") for r in rows)
    keys = {r.output_key for r in rows}
    assert len(keys) == len(rows)  # distinct output keys


def test_hidden_config_is_included_but_marked_not_visible():
    m = IndicatorManager()
    m.add(_sma(visible=False))
    rows = build_overlay_legend_rows(m, "main", "1d")
    assert len(rows) == 1
    assert rows[0].visible is False


def test_scope_filter_excludes_other_scope():
    m = IndicatorManager()
    m.add(_sma())  # scope main only
    assert build_overlay_legend_rows(m, "compare", "1d") == []
    assert len(build_overlay_legend_rows(m, "main", "1d")) == 1


def test_color_falls_back_to_theme_text_when_unstyled():
    m = IndicatorManager()
    # bbands cfg has empty style override; if the factory exposes a
    # default colour we use it, otherwise the theme text colour. Either
    # way the colour must be a non-empty string.
    m.add(_bbands())
    rows = build_overlay_legend_rows(m, "main", "1d", theme_text="#abcabc")
    assert rows
    assert all(isinstance(r.color, str) and r.color for r in rows)
