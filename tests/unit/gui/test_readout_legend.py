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
    # Single-output indicators now show "DisplayName" as the row label;
    # if the factory's params_schema has a length param, the formatter
    # parenthesises it. ``SMA`` ships with kind_id="sma" + a length
    # param, so the label is "SMA(20)" — matching the display_name set
    # by the indicator class.
    assert row.label == "SMA(20)"
    # As of the ``legend-condensation`` sprint, single-output rows
    # carry one OverlaySegment. The colour lives on the segment.
    assert len(row.outputs) == 1
    assert row.outputs[0].output_key == "sma"
    assert row.outputs[0].color == "#ff8800"
    assert row.visible is True


def test_two_overlays_in_insertion_order():
    m = IndicatorManager()
    c1 = m.add(_sma())
    c2 = m.add(_ema())
    rows = build_overlay_legend_rows(m, "main", "1d")
    assert [r.config_id for r in rows] == [c1.id, c2.id]
    assert [r.label for r in rows] == ["SMA(20)", "EMA(50)"]


def test_multi_output_collapses_to_one_row_with_per_output_segments():
    """Multi-output indicators now collapse to ONE row with per-band segments.

    Pre-condensation, this test asserted ``len(rows) > 1`` (one row
    per band) with each row's label prefixed with the display name.
    The new shape is ``one row + len(row.outputs) == n_bands`` so the
    legend renders ``BB(20,2) upper <v1> middle <v2> lower <v3>`` on
    a single line.
    """
    m = IndicatorManager()
    cfg = m.add(_bbands())
    rows = build_overlay_legend_rows(m, "main", "1d")
    assert len(rows) == 1
    row = rows[0]
    assert row.config_id == cfg.id
    # Bollinger declares the top-down order via effective_output_keys.
    keys = [seg.output_key for seg in row.outputs]
    assert keys == ["upper", "middle", "lower"]
    # Per-band labels carry the band name for the legend renderer.
    labels = [seg.key_label for seg in row.outputs]
    assert labels == ["upper", "middle", "lower"]


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
    # way every output segment's colour must be a non-empty string.
    m.add(_bbands())
    rows = build_overlay_legend_rows(m, "main", "1d", theme_text="#abcabc")
    assert rows
    for row in rows:
        for seg in row.outputs:
            assert isinstance(seg.color, str) and seg.color
