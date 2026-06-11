"""Legend condensation — one row per indicator + per-output visibility.

Replaces the historical "one row per output key" expansion with a
single consolidated row per indicator config. AVWAP with bands=off
now shows just `AVWAP(...)` instead of 5 rows; Bollinger Bands shows
`BB(20) upper <v1> middle <v2> lower <v3>` on one line with each
band's value in its own colour.

Two new mechanisms drive the visibility set:

1. Indicator-side `effective_output_keys(params)` — multi-output
   indicators that emit all-NaN for "off" bands (AVWAP) declare which
   outputs are actually rendered given their current params.
2. Per-output `LineStyle.visible` on the config — a user-flippable
   per-band toggle in the per-indicator dialog. Hidden outputs vanish
   from the legend, the same way hidden indicator configs do today.

The new `build_overlay_legend_rows` returns
:class:`ReadoutLegendRow` (now richer: per-output segments) — one per
indicator config, not one per output. The renderer in
`gui/interaction.py` consumes the segments and builds an `HPacker`
with one `TextArea` per token (label / value / spacer) so each
band's value can be coloured independently.
"""
from __future__ import annotations

import pytest


def _ema(length: int):
    """Build an EMA indicator instance + IndicatorConfig for it."""
    from tradinglab.indicators.config import IndicatorConfig

    return IndicatorConfig(
        id=1,
        kind_id="ema",
        kind_version=1,
        display_name="EMA",
        params={"length": length},
        style={},
    )


class TestEffectiveOutputKeysAVWAP:
    """AVWAP must declare which outputs are visible per `bands` param."""

    def test_avwap_bands_off_only_one_output(self):
        from tradinglab.indicators.avwap import AnchoredVWAP

        assert AnchoredVWAP.effective_output_keys({"bands": "off"}) == ("avwap",)

    def test_avwap_bands_one_sigma_three_outputs(self):
        from tradinglab.indicators.avwap import AnchoredVWAP

        out = AnchoredVWAP.effective_output_keys({"bands": "1σ"})
        # Upper / lower flank a central avwap line — order is
        # top-down on chart: upper, avwap, lower.
        assert out == ("upper1", "avwap", "lower1")

    def test_avwap_bands_two_sigma_three_outputs(self):
        from tradinglab.indicators.avwap import AnchoredVWAP

        assert AnchoredVWAP.effective_output_keys({"bands": "2σ"}) == (
            "upper2", "avwap", "lower2",
        )

    def test_avwap_bands_both_five_outputs(self):
        from tradinglab.indicators.avwap import AnchoredVWAP

        # Top-down: upper2, upper1, avwap, lower1, lower2.
        assert AnchoredVWAP.effective_output_keys({"bands": "both"}) == (
            "upper2", "upper1", "avwap", "lower1", "lower2",
        )

    def test_avwap_empty_params_defaults_to_off(self):
        from tradinglab.indicators.avwap import AnchoredVWAP

        # Same default as the schema (bands="off").
        assert AnchoredVWAP.effective_output_keys({}) == ("avwap",)


class TestEffectiveOutputKeysBollinger:
    """Bollinger declares its outputs in canonical visual top-down order."""

    def test_bb_top_down_order(self):
        from tradinglab.indicators.bollinger import BollingerBands

        assert BollingerBands.effective_output_keys({"length": 20}) == (
            "upper", "middle", "lower",
        )


class TestEffectiveOutputKeysDefault:
    """Indicators that don't override fall back to default_style key order."""

    def test_ema_single_output(self):
        from tradinglab.indicators.moving_averages import EMA

        assert EMA.effective_output_keys({"length": 20}) == ("ema",)

    def test_sma_single_output(self):
        from tradinglab.indicators.moving_averages import SMA

        assert SMA.effective_output_keys({"length": 50}) == ("sma",)


class TestFormatIndicatorLabel:
    """The consolidated-row prefix shows `Name(param1, name2=val2, ...)`."""

    def test_ema_with_length(self):
        from tradinglab.gui.readout_legend import format_indicator_label

        cfg = _ema(20)
        assert format_indicator_label(cfg) == "EMA(20)"

    def test_avwap_with_defaults(self):
        from tradinglab.gui.readout_legend import format_indicator_label
        from tradinglab.indicators.config import IndicatorConfig

        cfg = IndicatorConfig(
            id=1,
            kind_id="avwap",
            kind_version=1,
            display_name="AVWAP",
            params={"anchor_ts": "", "price_source": "typical", "bands": "off"},
            style={},
        )
        # Audit ``avwap-anchor-only-label``: the only "important" param
        # for AVWAP is its anchor; price_source / bands are noise. Blank
        # anchor (uninitialised) → bare ``AVWAP`` with no parens.
        assert format_indicator_label(cfg) == "AVWAP"

    def test_avwap_with_intraday_anchor(self):
        from tradinglab.gui.readout_legend import format_indicator_label
        from tradinglab.indicators.config import IndicatorConfig

        cfg = IndicatorConfig(
            id=1,
            kind_id="avwap",
            kind_version=1,
            display_name="AVWAP",
            params={
                "anchor_ts": "2025-09-15T09:30:00",
                "anchor_shared": True,
                "shared_anchor_ts": "2025-09-15T09:30:00",
                "price_source": "typical",
                "bands": "off",
            },
            style={},
        )
        # ISO-8601 anchor formatted human-readably: ``T`` → space and
        # the trailing ``:00`` seconds dropped. Shown only in shared mode
        # (the prefix is symbol-agnostic). Audit ``avwap-anchor-only-label``.
        assert format_indicator_label(cfg) == "AVWAP(2025-09-15 09:30)"

    def test_avwap_anchor_drops_seconds_only_when_zero(self):
        from tradinglab.gui.readout_legend import format_indicator_label
        from tradinglab.indicators.config import IndicatorConfig

        cfg = IndicatorConfig(
            id=1, kind_id="avwap", kind_version=1, display_name="AVWAP",
            params={"anchor_ts": "2025-09-15T09:31:45", "anchor_shared": True,
                    "shared_anchor_ts": "2025-09-15T09:31:45",
                    "price_source": "typical", "bands": "off"},
            style={},
        )
        # Non-zero seconds should be preserved (it's a precise anchor).
        assert format_indicator_label(cfg) == "AVWAP(2025-09-15 09:31:45)"

    def test_avwap_with_date_only_anchor(self):
        from tradinglab.gui.readout_legend import format_indicator_label
        from tradinglab.indicators.config import IndicatorConfig

        cfg = IndicatorConfig(
            id=1, kind_id="avwap", kind_version=1, display_name="AVWAP",
            params={"anchor_ts": "2025-09-15", "anchor_shared": True,
                    "shared_anchor_ts": "2025-09-15",
                    "price_source": "typical", "bands": "off"},
            style={},
        )
        # Daily/weekly/monthly anchors are date-only strings; pass through.
        assert format_indicator_label(cfg) == "AVWAP(2025-09-15)"

    def test_avwap_bands_param_never_appears_in_label(self):
        from tradinglab.gui.readout_legend import format_indicator_label
        from tradinglab.indicators.config import IndicatorConfig

        for bands in ("off", "1σ", "2σ", "both"):
            cfg = IndicatorConfig(
                id=1, kind_id="avwap", kind_version=1, display_name="AVWAP",
                params={"anchor_ts": "2025-09-15T09:30:00",
                        "price_source": "typical", "bands": bands},
                style={},
            )
            label = format_indicator_label(cfg)
            assert "bands" not in label, f"bands leaked into AVWAP label for {bands!r}"
            assert "typical" not in label, "price_source leaked into AVWAP label"

    def test_bollinger_with_length_and_stddev(self):
        from tradinglab.gui.readout_legend import format_indicator_label
        from tradinglab.indicators.config import IndicatorConfig

        cfg = IndicatorConfig(
            id=1,
            kind_id="bbands",
            kind_version=1,
            display_name="Bollinger Bands",
            params={"length": 20, "num_std": 2.0},
            style={},
        )
        out = format_indicator_label(cfg)
        # Must include the indicator name + both params.
        assert out.startswith("Bollinger Bands(")
        assert "20" in out
        assert "2" in out

    def test_unknown_kind_id_falls_back_to_display_name(self):
        from tradinglab.gui.readout_legend import format_indicator_label
        from tradinglab.indicators.config import IndicatorConfig

        cfg = IndicatorConfig(
            id=1,
            kind_id="not-a-real-kind",
            kind_version=1,
            display_name="Mystery",
            params={"foo": 1},
            style={},
        )
        # No factory → bare display name (no params can be ordered).
        out = format_indicator_label(cfg)
        assert "Mystery" in out


class TestBuildOverlayLegendRowsConsolidation:
    """`build_overlay_legend_rows` emits ONE row per indicator config."""

    def _stub_manager(self, configs):
        """Build a minimal IndicatorManager-shaped object.

        ``collect_overlay_configs`` calls ``manager.list()``; we also
        set ``scopes`` + ``intervals`` on each cfg so the scope/interval
        filter doesn't drop them.
        """
        class _Stub:
            def __init__(self, _configs):
                self._configs = list(_configs)
                # Decorate each cfg with the scope/interval surface
                # ``collect_overlay_configs`` filters by.
                for c in self._configs:
                    if not getattr(c, "scopes", None):
                        c.scopes = frozenset({"main"})
                    if not hasattr(c, "intervals") or c.intervals is None:
                        c.intervals = ()
            def list(self):
                return list(self._configs)
            def configs_for(self, scope, interval, *, visible_only=False):
                return [c for c in self._configs
                        if (not visible_only or c.visible)]
        return _Stub(configs)

    def test_avwap_bands_off_emits_one_row_with_one_segment(self):
        from tradinglab.gui.readout_legend import build_overlay_legend_rows
        from tradinglab.indicators.config import IndicatorConfig

        cfg = IndicatorConfig(
            id=1, kind_id="avwap", kind_version=1, display_name="AVWAP",
            params={"price_source": "typical", "bands": "off"},
            style={},
        )
        mgr = self._stub_manager([cfg])
        rows = build_overlay_legend_rows(mgr, "main", "1d")
        assert len(rows) == 1
        row = rows[0]
        assert row.config_id == 1
        # Exactly one output segment — only "avwap" is visible.
        assert len(row.outputs) == 1
        assert row.outputs[0].output_key == "avwap"

    def test_avwap_bands_both_emits_one_row_with_five_segments(self):
        from tradinglab.gui.readout_legend import build_overlay_legend_rows
        from tradinglab.indicators.config import IndicatorConfig

        cfg = IndicatorConfig(
            id=1, kind_id="avwap", kind_version=1, display_name="AVWAP",
            params={"price_source": "typical", "bands": "both"},
            style={},
        )
        mgr = self._stub_manager([cfg])
        rows = build_overlay_legend_rows(mgr, "main", "1d")
        assert len(rows) == 1
        # 5 segments — top-down visual order.
        keys = [seg.output_key for seg in rows[0].outputs]
        assert keys == ["upper2", "upper1", "avwap", "lower1", "lower2"]

    def test_bb_emits_one_row_with_three_segments_top_down(self):
        from tradinglab.gui.readout_legend import build_overlay_legend_rows
        from tradinglab.indicators.config import IndicatorConfig

        cfg = IndicatorConfig(
            id=1, kind_id="bbands", kind_version=1, display_name="BB",
            params={"length": 20, "num_std": 2.0},
            style={},
        )
        mgr = self._stub_manager([cfg])
        rows = build_overlay_legend_rows(mgr, "main", "1d")
        assert len(rows) == 1
        keys = [seg.output_key for seg in rows[0].outputs]
        assert keys == ["upper", "middle", "lower"]

    def test_per_output_visible_false_hides_segment(self):
        """When `cfg.style[key].visible = False`, the segment must vanish.

        This is the new "user-flip individual band" behaviour the
        sprint wires up.
        """
        from tradinglab.gui.readout_legend import build_overlay_legend_rows
        from tradinglab.indicators.base import LineStyle
        from tradinglab.indicators.config import IndicatorConfig

        cfg = IndicatorConfig(
            id=1, kind_id="bbands", kind_version=1, display_name="BB",
            params={"length": 20, "num_std": 2.0},
            style={
                "upper": LineStyle(color="#ff0000", visible=True),
                "middle": LineStyle(color="#00ff00", visible=False),
                "lower": LineStyle(color="#0000ff", visible=True),
            },
        )
        mgr = self._stub_manager([cfg])
        rows = build_overlay_legend_rows(mgr, "main", "1d")
        assert len(rows) == 1
        keys = [seg.output_key for seg in rows[0].outputs]
        # middle hidden — only upper + lower remain.
        assert keys == ["upper", "lower"]

    def test_per_output_segment_carries_its_color(self):
        from tradinglab.gui.readout_legend import build_overlay_legend_rows
        from tradinglab.indicators.base import LineStyle
        from tradinglab.indicators.config import IndicatorConfig

        cfg = IndicatorConfig(
            id=1, kind_id="bbands", kind_version=1, display_name="BB",
            params={"length": 20, "num_std": 2.0},
            style={
                "upper": LineStyle(color="#ff0000"),
                "middle": LineStyle(color="#00ff00"),
                "lower": LineStyle(color="#0000ff"),
            },
        )
        mgr = self._stub_manager([cfg])
        rows = build_overlay_legend_rows(mgr, "main", "1d")
        colors_by_key = {seg.output_key: seg.color for seg in rows[0].outputs}
        assert colors_by_key["upper"] == "#ff0000"
        assert colors_by_key["middle"] == "#00ff00"
        assert colors_by_key["lower"] == "#0000ff"

    def test_single_output_row_label_is_just_indicator_name(self):
        from tradinglab.gui.readout_legend import build_overlay_legend_rows

        mgr = self._stub_manager([_ema(20)])
        rows = build_overlay_legend_rows(mgr, "main", "1d")
        assert len(rows) == 1
        # The single output's key-label should be empty (or just the
        # indicator's parenthesised label — no "ema" suffix duplication).
        assert rows[0].label == "EMA(20)"
        assert len(rows[0].outputs) == 1
        # For single-output indicators the per-output prefix
        # (the band name) should be empty — the row reads simply
        # "EMA(20) <value>".
        assert rows[0].outputs[0].key_label == ""

    def test_multi_output_segments_have_band_labels(self):
        from tradinglab.gui.readout_legend import build_overlay_legend_rows
        from tradinglab.indicators.config import IndicatorConfig

        cfg = IndicatorConfig(
            id=1, kind_id="bbands", kind_version=1, display_name="BB",
            params={"length": 20, "num_stddev": 2.0},
            style={},
        )
        mgr = self._stub_manager([cfg])
        rows = build_overlay_legend_rows(mgr, "main", "1d")
        labels_by_key = {seg.output_key: seg.key_label for seg in rows[0].outputs}
        assert labels_by_key["upper"] == "upper"
        assert labels_by_key["middle"] == "middle"
        assert labels_by_key["lower"] == "lower"


class TestHiddenConfigStillIncluded:
    """Hidden indicator configs stay in the legend so the user can re-enable."""

    def _stub_manager(self, configs):
        class _Stub:
            def __init__(self, _configs):
                self._configs = list(_configs)
                for c in self._configs:
                    if not getattr(c, "scopes", None):
                        c.scopes = frozenset({"main"})
                    if not hasattr(c, "intervals") or c.intervals is None:
                        c.intervals = ()
            def list(self):
                return list(self._configs)
            def configs_for(self, scope, interval, *, visible_only=False):
                return [c for c in self._configs
                        if (not visible_only or c.visible)]
        return _Stub(configs)

    def test_hidden_indicator_row_emitted_with_visible_false(self):
        from tradinglab.gui.readout_legend import build_overlay_legend_rows
        from tradinglab.indicators.config import IndicatorConfig

        cfg = IndicatorConfig(
            id=1, kind_id="bbands", kind_version=1, display_name="BB",
            params={"length": 20, "num_std": 2.0},
            style={},
            visible=False,
        )
        mgr = self._stub_manager([cfg])
        rows = build_overlay_legend_rows(mgr, "main", "1d")
        assert len(rows) == 1
        assert rows[0].visible is False
