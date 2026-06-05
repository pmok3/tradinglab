"""Unit tests for :func:`tradinglab.constants.compute_toggle_sashes`.

Audit ``chartstack-toggle-preserves-notebook``: when ChartStack is
toggled on/off the watchlist (notebook) column must NOT move — only
the chart pane resizes to make room for (or reclaim space from) the
ChartStack column on the left.

The previous toggle path fed ``compute_main_paned_sashes`` a stale
``_initial_geometry`` width and recomputed the notebook width from a
ratio. On a window that had been resized / maximised since launch,
the stale width produced sash positions that left the notebook
filling ~half the screen. The fix: capture the live chart|notebook
boundary before the toggle and pin it across the mutation.

``compute_toggle_sashes(main_w, notebook_left_x, *, chartstack_visible)``
is the pure helper that turns the captured boundary into sash
positions that hold it fixed.
"""
from __future__ import annotations

import pytest

from tradinglab.constants import (
    CHARTSTACK_PANE_STARTUP_WIDTH_PX,
    compute_toggle_sashes,
)

# ---------------------------------------------------------------------------
# Toggle ON (2-pane → 3-pane)
# ---------------------------------------------------------------------------


class TestToggleOn:
    def test_returns_two_sashes(self) -> None:
        out = compute_toggle_sashes(1920, 1186, chartstack_visible=True)
        assert len(out) == 2

    def test_first_sash_is_chartstack_width(self) -> None:
        out = compute_toggle_sashes(1920, 1186, chartstack_visible=True)
        assert out[0] == CHARTSTACK_PANE_STARTUP_WIDTH_PX

    def test_notebook_boundary_preserved_exactly(self) -> None:
        """The chart|notebook boundary (rightmost sash) is held at
        exactly the captured position — the watchlist does not move."""
        out = compute_toggle_sashes(1920, 1186, chartstack_visible=True)
        assert out[1] == 1186

    def test_chart_shrinks_from_left_by_chartstack_width(self) -> None:
        """Before: chart spanned [0, 1186] = 1186 px.
        After: chart spans [220, 1186] = 966 px — lost exactly the
        ChartStack column width, all from the left."""
        out = compute_toggle_sashes(1920, 1186, chartstack_visible=True)
        chart_w = out[1] - out[0]
        assert chart_w == 1186 - CHARTSTACK_PANE_STARTUP_WIDTH_PX


# ---------------------------------------------------------------------------
# Toggle OFF (3-pane → 2-pane)
# ---------------------------------------------------------------------------


class TestToggleOff:
    def test_returns_single_sash(self) -> None:
        out = compute_toggle_sashes(1920, 1186, chartstack_visible=False)
        assert out == [1186]

    def test_notebook_boundary_preserved_exactly(self) -> None:
        out = compute_toggle_sashes(1920, 1186, chartstack_visible=False)
        assert out[-1] == 1186

    def test_chart_grows_to_reclaim_chartstack_width(self) -> None:
        """Before: chart spanned [220, 1186] = 966 px.
        After: chart spans [0, 1186] = 1186 px — reclaimed exactly the
        ChartStack column width."""
        out = compute_toggle_sashes(1920, 1186, chartstack_visible=False)
        assert out[0] == 1186


# ---------------------------------------------------------------------------
# Round-trip invariant (the headline user promise)
# ---------------------------------------------------------------------------


class TestRoundTripInvariant:
    @pytest.mark.parametrize("main_w", [1280, 1920, 2560, 3840])
    @pytest.mark.parametrize("boundary", [600, 800, 1186, 1582, 2373])
    def test_notebook_boundary_identical_on_and_off(
        self, main_w: int, boundary: int
    ) -> None:
        """For any window width + boundary, the notebook's left edge
        is identical whether ChartStack is on or off. This is the
        whole point of the helper."""
        if boundary >= main_w:
            pytest.skip("boundary beyond window — covered by clamp tests")
        on = compute_toggle_sashes(main_w, boundary, chartstack_visible=True)
        off = compute_toggle_sashes(main_w, boundary, chartstack_visible=False)
        assert on[-1] == off[-1] == boundary

    def test_uses_actual_width_not_a_recomputed_ratio(self) -> None:
        """Regression for the reported bug: a window maximised to
        2560 px with the notebook boundary at 1582 must keep the
        notebook at 2560-1582 = 978 px after toggle-on — NOT snap it
        to ~half the screen because of a stale 1280-px startup width.

        The helper only sees the (correct) live width + boundary, so
        it cannot reproduce the stale-width bug. This test pins that
        the boundary the caller passes is honoured verbatim."""
        out = compute_toggle_sashes(2560, 1582, chartstack_visible=True)
        notebook_w = 2560 - out[-1]
        assert notebook_w == 978


# ---------------------------------------------------------------------------
# Defensive clamps
# ---------------------------------------------------------------------------


class TestDefensiveClamps:
    def test_toggle_on_narrow_chart_nudges_boundary_right(self) -> None:
        """If holding the boundary would crush the chart below the
        min, nudge the boundary right just enough to keep the chart
        usable. boundary=300, CS=220 → chart=80 < 200 → nudge to
        220+200=420."""
        out = compute_toggle_sashes(2000, 300, chartstack_visible=True)
        assert out[0] == CHARTSTACK_PANE_STARTUP_WIDTH_PX
        chart_w = out[1] - out[0]
        assert chart_w >= 200

    def test_toggle_off_narrow_chart_floors_boundary(self) -> None:
        out = compute_toggle_sashes(2000, 50, chartstack_visible=False)
        assert out[0] >= 200

    def test_boundary_capped_at_main_w(self) -> None:
        """A boundary beyond the window right edge is capped so the
        notebook can't be pushed off-screen."""
        out = compute_toggle_sashes(1000, 5000, chartstack_visible=False)
        assert out[-1] <= 1000

    def test_zero_main_w_does_not_cap(self) -> None:
        """When the live width is unknown (0 — widget not realised),
        the helper skips the cap rather than collapsing the boundary
        to 0; the caller falls back to the ratio path in that case."""
        out = compute_toggle_sashes(0, 1186, chartstack_visible=False)
        assert out == [1186]

    def test_custom_chartstack_width_and_chart_min(self) -> None:
        out = compute_toggle_sashes(
            2000, 1000, chartstack_visible=True,
            chartstack_w=300, chart_min_px=150,
        )
        assert out[0] == 300
        assert out[1] == 1000
