"""Integration tests for ``gui.live_price_overlay`` — real matplotlib Agg axes.

Validates that ``LivePriceOverlay`` actually attaches the expected
``Line2D`` + ``Text`` artists to the axes, mutates them in place on
``update_in_place``, and honours the design contract (color, linestyle,
zorder) when used the way ``ChartApp._redraw_live_price_overlay`` will
use it.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # noqa: E402

from matplotlib.figure import Figure  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.text import Text  # noqa: E402

import pytest

from tradinglab.gui.live_price_overlay import (
    LIVE_PRICE_LINESTYLE,
    LIVE_PRICE_LABEL_ZORDER,
    LIVE_PRICE_ZORDER,
    LivePriceOverlay,
    format_price,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def axes_pair():
    fig = Figure()
    ax_primary = fig.add_subplot(2, 1, 1)
    ax_compare = fig.add_subplot(2, 1, 2)
    yield ax_primary, ax_compare


# ---------------------------------------------------------------------------
# redraw — happy path
# ---------------------------------------------------------------------------


class TestRedraw:

    def test_redraw_attaches_line_and_label_to_axes(self, axes_pair):
        ax_p, ax_c = axes_pair
        ov = LivePriceOverlay()
        ov.redraw(
            ax_by_slot={"primary": ax_p, "compare": ax_c},
            price_by_slot={"primary": 100.0, "compare": 50.0},
            color="#abcdef",
        )
        entry_p = ov.get_artists("primary")
        entry_c = ov.get_artists("compare")
        assert entry_p is not None
        assert entry_c is not None
        line_p, label_p = entry_p
        assert isinstance(line_p, Line2D)
        assert isinstance(label_p, Text)
        # The line lives on the right axes.
        assert line_p in ax_p.lines
        line_c, _ = entry_c
        assert line_c in ax_c.lines

    def test_redraw_uses_passed_color(self, axes_pair):
        ax_p, _ = axes_pair
        ov = LivePriceOverlay()
        ov.redraw(
            ax_by_slot={"primary": ax_p},
            price_by_slot={"primary": 100.0},
            color="#112233",
        )
        line, label = ov.get_artists("primary")
        # mpl normalises colors; just check the string passed through
        # is what the line is using (as a tuple or hex).
        assert line.get_color() == "#112233"
        if label is not None:
            assert label.get_color() == "#112233"

    def test_redraw_uses_dotted_linestyle(self, axes_pair):
        ax_p, _ = axes_pair
        ov = LivePriceOverlay()
        ov.redraw(
            ax_by_slot={"primary": ax_p},
            price_by_slot={"primary": 100.0},
            color="#888",
        )
        line, _ = ov.get_artists("primary")
        # mpl resolves the dash-tuple linestyle into its dash sequence;
        # we just assert the dashes are nonzero (not a solid line).
        dashes = line.get_linestyle()
        # Matplotlib stores tuple linestyles intact when set via the
        # constructor — confirm we passed the right value.
        assert dashes == LIVE_PRICE_LINESTYLE or dashes != "-"

    def test_redraw_label_text_uses_format_price(self, axes_pair):
        ax_p, _ = axes_pair
        ov = LivePriceOverlay()
        ov.redraw(
            ax_by_slot={"primary": ax_p},
            price_by_slot={"primary": 1234.5678},
            color="#888",
        )
        _, label = ov.get_artists("primary")
        assert label is not None
        # Leading space, then formatted price.
        assert label.get_text() == " " + format_price(1234.5678)

    def test_redraw_zorder_below_overlays(self, axes_pair):
        ax_p, _ = axes_pair
        ov = LivePriceOverlay()
        ov.redraw(
            ax_by_slot={"primary": ax_p},
            price_by_slot={"primary": 100.0},
            color="#888",
        )
        line, label = ov.get_artists("primary")
        assert line.get_zorder() == LIVE_PRICE_ZORDER
        if label is not None:
            assert label.get_zorder() == LIVE_PRICE_LABEL_ZORDER

    def test_redraw_clears_previous_pass(self, axes_pair):
        ax_p, _ = axes_pair
        ov = LivePriceOverlay()
        ov.redraw(
            ax_by_slot={"primary": ax_p},
            price_by_slot={"primary": 100.0},
            color="#888",
        )
        line1, _ = ov.get_artists("primary")
        ov.redraw(
            ax_by_slot={"primary": ax_p},
            price_by_slot={"primary": 110.0},
            color="#888",
        )
        line2, _ = ov.get_artists("primary")
        assert line1 is not line2  # python ref cleared
        # And the y-data of the new line is at the new price.
        assert tuple(line2.get_ydata()) == (110.0, 110.0)


# ---------------------------------------------------------------------------
# redraw — gating / safe noops
# ---------------------------------------------------------------------------


class TestRedrawGating:

    def test_disabled_overlay_draws_nothing(self, axes_pair):
        ax_p, _ = axes_pair
        ov = LivePriceOverlay(enabled=False)
        ov.redraw(
            ax_by_slot={"primary": ax_p},
            price_by_slot={"primary": 100.0},
            color="#888",
        )
        assert ov.slot_count == 0
        assert len(ax_p.lines) == 0

    def test_none_price_skips_slot(self, axes_pair):
        ax_p, ax_c = axes_pair
        ov = LivePriceOverlay()
        ov.redraw(
            ax_by_slot={"primary": ax_p, "compare": ax_c},
            price_by_slot={"primary": 100.0, "compare": None},
            color="#888",
        )
        assert ov.get_artists("primary") is not None
        assert ov.get_artists("compare") is None

    def test_nan_price_skips_slot(self, axes_pair):
        ax_p, _ = axes_pair
        ov = LivePriceOverlay()
        ov.redraw(
            ax_by_slot={"primary": ax_p},
            price_by_slot={"primary": float("nan")},
            color="#888",
        )
        assert ov.get_artists("primary") is None

    def test_none_axis_skips_slot(self):
        ov = LivePriceOverlay()
        ov.redraw(
            ax_by_slot={"primary": None},
            price_by_slot={"primary": 100.0},
            color="#888",
        )
        assert ov.get_artists("primary") is None


# ---------------------------------------------------------------------------
# update_in_place
# ---------------------------------------------------------------------------


class TestUpdateInPlace:

    def test_update_mutates_line_ydata(self, axes_pair):
        ax_p, _ = axes_pair
        ov = LivePriceOverlay()
        ov.redraw(
            ax_by_slot={"primary": ax_p},
            price_by_slot={"primary": 100.0},
            color="#888",
        )
        line, _ = ov.get_artists("primary")
        ok = ov.update_in_place("primary", 105.5)
        assert ok is True
        ys = tuple(line.get_ydata())
        assert ys == (105.5, 105.5)

    def test_update_mutates_label_text_and_position(self, axes_pair):
        ax_p, _ = axes_pair
        ov = LivePriceOverlay()
        ov.redraw(
            ax_by_slot={"primary": ax_p},
            price_by_slot={"primary": 100.0},
            color="#888",
        )
        _, label = ov.get_artists("primary")
        assert label is not None
        ov.update_in_place("primary", 105.5)
        # Position y has moved to 105.5 in data coords.
        assert label.get_position()[1] == 105.5
        # And the text updated to the new formatted price.
        assert label.get_text() == " " + format_price(105.5)

    def test_update_unknown_slot_returns_false(self, axes_pair):
        ov = LivePriceOverlay()
        # Never redrew — no artist exists.
        assert ov.update_in_place("primary", 100.0) is False

    def test_update_with_none_returns_false(self, axes_pair):
        ax_p, _ = axes_pair
        ov = LivePriceOverlay()
        ov.redraw(
            ax_by_slot={"primary": ax_p},
            price_by_slot={"primary": 100.0},
            color="#888",
        )
        assert ov.update_in_place("primary", None) is False

    def test_update_with_nan_returns_false(self, axes_pair):
        ax_p, _ = axes_pair
        ov = LivePriceOverlay()
        ov.redraw(
            ax_by_slot={"primary": ax_p},
            price_by_slot={"primary": 100.0},
            color="#888",
        )
        line, _ = ov.get_artists("primary")
        ok = ov.update_in_place("primary", float("nan"))
        assert ok is False
        # Original price unchanged.
        assert tuple(line.get_ydata()) == (100.0, 100.0)

    def test_update_after_disable_returns_false(self, axes_pair):
        ax_p, _ = axes_pair
        ov = LivePriceOverlay()
        ov.redraw(
            ax_by_slot={"primary": ax_p},
            price_by_slot={"primary": 100.0},
            color="#888",
        )
        ov.set_enabled(False)
        assert ov.update_in_place("primary", 200.0) is False


# ---------------------------------------------------------------------------
# enable / clear
# ---------------------------------------------------------------------------


class TestLifecycle:

    def test_set_enabled_false_clears_artist_map(self, axes_pair):
        ax_p, _ = axes_pair
        ov = LivePriceOverlay()
        ov.redraw(
            ax_by_slot={"primary": ax_p},
            price_by_slot={"primary": 100.0},
            color="#888",
        )
        assert ov.slot_count == 1
        ov.set_enabled(False)
        assert ov.slot_count == 0

    def test_clear_drops_refs(self, axes_pair):
        ax_p, _ = axes_pair
        ov = LivePriceOverlay()
        ov.redraw(
            ax_by_slot={"primary": ax_p},
            price_by_slot={"primary": 100.0},
            color="#888",
        )
        ov.clear()
        assert ov.slot_count == 0

    def test_close_alias_clears_refs(self, axes_pair):
        ax_p, _ = axes_pair
        ov = LivePriceOverlay()
        ov.redraw(
            ax_by_slot={"primary": ax_p},
            price_by_slot={"primary": 100.0},
            color="#888",
        )
        ov.close()
        assert ov.slot_count == 0


# ---------------------------------------------------------------------------
# Boxed badge — TradingView-style label matching cursor crosshair
# ---------------------------------------------------------------------------


class TestBoxedBadge:
    """When `label_bg` / `label_fg` / `label_edge` are passed,
    the label should render as an `Annotation` with a round bbox
    patch — matching the cursor crosshair price label style in
    `gui.interaction._build_hover_artists`."""

    def test_redraw_with_box_colors_yields_annotation_with_bbox(self, axes_pair):
        from matplotlib.text import Annotation
        ax_p, _ = axes_pair
        ov = LivePriceOverlay()
        ov.redraw(
            ax_by_slot={"primary": ax_p},
            price_by_slot={"primary": 100.0},
            color="#888888",
            label_bg="#ffffff",
            label_fg="#111111",
            label_edge="#666666",
        )
        line, label = ov.get_artists("primary")
        assert isinstance(line, Line2D)
        assert isinstance(label, Annotation)
        bbox = label.get_bbox_patch()
        assert bbox is not None, "boxed badge must have a bbox patch"

    def test_box_colors_applied_to_bbox_facecolor_and_edgecolor(self, axes_pair):
        ax_p, _ = axes_pair
        ov = LivePriceOverlay()
        ov.redraw(
            ax_by_slot={"primary": ax_p},
            price_by_slot={"primary": 100.0},
            color="#888888",
            label_bg="#ff00ff",
            label_fg="#00ff00",
            label_edge="#0000ff",
        )
        _, label = ov.get_artists("primary")
        bbox = label.get_bbox_patch()
        from matplotlib.colors import to_hex
        assert to_hex(bbox.get_facecolor()) == "#ff00ff"
        assert to_hex(bbox.get_edgecolor()) == "#0000ff"
        assert to_hex(label.get_color()) == "#00ff00"

    def test_line_color_uses_color_param_not_label_fg(self, axes_pair):
        ax_p, _ = axes_pair
        ov = LivePriceOverlay()
        ov.redraw(
            ax_by_slot={"primary": ax_p},
            price_by_slot={"primary": 100.0},
            color="#888888",
            label_bg="#ffffff",
            label_fg="#111111",
            label_edge="#666666",
        )
        line, _ = ov.get_artists("primary")
        from matplotlib.colors import to_hex
        assert to_hex(line.get_color()) == "#888888"

    def test_legacy_call_without_box_yields_plain_text_no_bbox(self, axes_pair):
        from matplotlib.text import Text, Annotation
        ax_p, _ = axes_pair
        ov = LivePriceOverlay()
        ov.redraw(
            ax_by_slot={"primary": ax_p},
            price_by_slot={"primary": 100.0},
            color="#888888",
        )
        _, label = ov.get_artists("primary")
        assert isinstance(label, Text)
        assert not isinstance(label, Annotation)

    def test_update_in_place_moves_annotation_xy_anchor(self, axes_pair):
        ax_p, _ = axes_pair
        ov = LivePriceOverlay()
        ov.redraw(
            ax_by_slot={"primary": ax_p},
            price_by_slot={"primary": 100.0},
            color="#888888",
            label_bg="#fff", label_fg="#000", label_edge="#888",
        )
        _, label = ov.get_artists("primary")
        # Initial xy anchor is at price=100.
        assert label.xy[1] == 100.0
        ok = ov.update_in_place("primary", 105.5)
        assert ok is True
        # xy anchor should now sit at the new price.
        assert label.xy[1] == 105.5

    def test_update_in_place_refreshes_text_without_leading_space(self, axes_pair):
        ax_p, _ = axes_pair
        ov = LivePriceOverlay()
        ov.redraw(
            ax_by_slot={"primary": ax_p},
            price_by_slot={"primary": 100.0},
            color="#888",
            label_bg="#fff", label_fg="#000", label_edge="#888",
        )
        ov.update_in_place("primary", 123.45)
        _, label = ov.get_artists("primary")
        # The annotate path renders the price WITHOUT a leading space
        # because the bbox provides its own padding; the legacy text
        # path uses a leading space to fake padding.
        assert label.get_text() == "123.45"


class TestApplyTheme:
    """`apply_theme` recolours existing artists in place so theme
    toggles don't have to wait for the next `_render` to reach the
    live-price overlay."""

    def test_apply_theme_recolors_line(self, axes_pair):
        ax_p, _ = axes_pair
        ov = LivePriceOverlay()
        ov.redraw(
            ax_by_slot={"primary": ax_p},
            price_by_slot={"primary": 100.0},
            color="#111111",
            label_bg="#ffffff", label_fg="#111111", label_edge="#888888",
        )
        ov.apply_theme(
            line_color="#dcdcdc",
            label_bg="#2b2b2b",
            label_fg="#dcdcdc",
            label_edge="#666666",
        )
        line, label = ov.get_artists("primary")
        from matplotlib.colors import to_hex
        assert to_hex(line.get_color()) == "#dcdcdc"
        bbox = label.get_bbox_patch()
        assert to_hex(bbox.get_facecolor()) == "#2b2b2b"
        assert to_hex(bbox.get_edgecolor()) == "#666666"
        assert to_hex(label.get_color()) == "#dcdcdc"

    def test_apply_theme_with_no_artists_is_noop(self):
        ov = LivePriceOverlay()
        # Should not raise.
        ov.apply_theme(
            line_color="#fff", label_bg="#000", label_fg="#fff", label_edge="#888",
        )
