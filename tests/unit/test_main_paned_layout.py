"""Unit tests for :func:`tradinglab.constants.compute_main_paned_sashes`.

The helper resolves the cumulative sash x-positions of
``app._main_paned`` for both the 2-pane (ChartStack hidden) and
3-pane (ChartStack visible) layouts. The headline invariant — and the
reason the helper exists — is that the *notebook* (right-side
watchlist / OHLC / scanner / sandbox / entries / exits tab strip) has
the same absolute width in both modes. Toggling ChartStack only steals
``CHARTSTACK_PANE_STARTUP_WIDTH_PX`` from the chart, not the notebook.

The chart pane and chartstack pane have defensive ``chart_min_px``
floors so a narrow window degrades gracefully (chart stays usable;
notebook gives up width first).
"""
from __future__ import annotations

import pytest

from tradinglab.constants import (
    CHART_PANE_STARTUP_RATIO,
    CHARTSTACK_PANE_STARTUP_WIDTH_PX,
    compute_main_paned_sashes,
)

# ---------------------------------------------------------------------------
# 2-pane (ChartStack off)
# ---------------------------------------------------------------------------


class TestTwoPaneLayout:
    def test_returns_single_sash(self) -> None:
        sashes = compute_main_paned_sashes(1280, chartstack_visible=False)
        assert len(sashes) == 1

    def test_chart_claims_startup_ratio_on_wide_window(self) -> None:
        """On a 2000-px window, chart gets the golden-major (~61.8 %)
        and notebook the golden-minor (~38.2 %).

        We pick a wide window so the ``notebook_min_px`` clamp doesn't
        kick in — the math is the pure ratio. Notebook width is
        computed as ``main_w - int(main_w * CHART_PANE_STARTUP_RATIO)``
        rather than ``int(main_w * (1 - ratio))`` to dodge float
        precision artefacts.
        """
        main_w = 2000
        sashes = compute_main_paned_sashes(main_w, chartstack_visible=False)
        chart_w = sashes[0]
        notebook_w = main_w - chart_w
        expected_chart = int(main_w * CHART_PANE_STARTUP_RATIO)
        expected_notebook = main_w - expected_chart
        assert chart_w == expected_chart
        assert notebook_w == expected_notebook

    def test_notebook_min_clamp_on_narrow_window(self) -> None:
        """At 700 px, the golden-minor (~38.2 %) = ~268 px — below
        ``notebook_min_px`` default of 280. The clamp pins notebook at
        280."""
        sashes = compute_main_paned_sashes(700, chartstack_visible=False)
        chart_w = sashes[0]
        assert 700 - chart_w == 280


# ---------------------------------------------------------------------------
# 3-pane (ChartStack on)
# ---------------------------------------------------------------------------


class TestThreePaneLayout:
    def test_returns_two_sashes(self) -> None:
        sashes = compute_main_paned_sashes(1600, chartstack_visible=True)
        assert len(sashes) == 2

    def test_first_sash_is_chartstack_width(self) -> None:
        """The leftmost sash position equals the ChartStack column
        width — that's where the ``[chartstack | chart]`` boundary
        lives."""
        sashes = compute_main_paned_sashes(1600, chartstack_visible=True)
        assert sashes[0] == CHARTSTACK_PANE_STARTUP_WIDTH_PX

    def test_notebook_matches_two_pane_notebook(self) -> None:
        """Headline invariant: notebook width is identical whether
        ChartStack is visible or not."""
        for main_w in (1400, 1920, 2560, 3840):
            off = compute_main_paned_sashes(main_w, chartstack_visible=False)
            on = compute_main_paned_sashes(main_w, chartstack_visible=True)
            notebook_off = main_w - off[0]
            notebook_on = main_w - on[1]
            assert notebook_off == notebook_on, (
                f"main_w={main_w}: 2-pane nb={notebook_off}, "
                f"3-pane nb={notebook_on}")

    def test_chart_shrinks_by_exactly_chartstack_width(self) -> None:
        """The headline user-facing promise: 'shrinking the main chart
        only by a little bit to accommodate the chartstack'.

        The 'little bit' is exactly the chartstack column width.
        """
        for main_w in (1400, 1920, 2560, 3840):
            off = compute_main_paned_sashes(main_w, chartstack_visible=False)
            on = compute_main_paned_sashes(main_w, chartstack_visible=True)
            chart_off = off[0]
            chart_on = on[1] - on[0]
            delta = chart_off - chart_on
            assert delta == CHARTSTACK_PANE_STARTUP_WIDTH_PX, (
                f"main_w={main_w}: chart shrank by {delta}px "
                f"(expected {CHARTSTACK_PANE_STARTUP_WIDTH_PX})")

    def test_sash_positions_are_cumulative(self) -> None:
        """``ttk.PanedWindow.sashpos`` takes cumulative x-pixels, not
        per-pane widths. The second sash must be strictly greater than
        the first."""
        sashes = compute_main_paned_sashes(1600, chartstack_visible=True)
        assert sashes[0] < sashes[1]


# ---------------------------------------------------------------------------
# Defensive clamps
# ---------------------------------------------------------------------------


class TestDefensiveClamps:
    def test_narrow_window_floors_chart(self) -> None:
        """At 500 px with ChartStack on (CS=220, nb_min=280, chart_min=200),
        the math gives chart = 500 - 220 - 280 = 0. The floor kicks in:
        chart = 200, notebook = max(0, 500 - 220 - 200) = 80."""
        sashes = compute_main_paned_sashes(500, chartstack_visible=True)
        assert sashes[0] == CHARTSTACK_PANE_STARTUP_WIDTH_PX
        chart_w = sashes[1] - sashes[0]
        assert chart_w == 200

    def test_extreme_narrow_window_does_not_raise(self) -> None:
        """100-px window is absurd but the helper must not blow up."""
        sashes = compute_main_paned_sashes(100, chartstack_visible=True)
        assert len(sashes) == 2

    def test_custom_min_widths_honored(self) -> None:
        """``notebook_min_px`` and ``chart_min_px`` are overridable."""
        sashes = compute_main_paned_sashes(
            1000, chartstack_visible=False, notebook_min_px=400)
        chart_w = sashes[0]
        assert 1000 - chart_w == 400


# ---------------------------------------------------------------------------
# Concrete examples (regression pinning)
# ---------------------------------------------------------------------------


class TestConcreteExamples:
    """Pin the math at a handful of common window sizes so a future
    accidental change to the ratio or the chartstack width trips
    these assertions instead of only surfacing on launch."""

    @pytest.mark.parametrize(
        "main_w,expected_2pane,expected_3pane",
        [
            # 1080p typical: 1920 wide. chart=int(1920*0.618034)=1186.
            (1920, [1186], [220, 1186]),  # nb=734 in both cases.
            # 1440p wide: 2560. chart=int(2560*0.618034)=1582.
            (2560, [1582], [220, 1582]),  # nb=978 in both cases.
            # 4K: 3840. chart=int(3840*0.618034)=2373.
            (3840, [2373], [220, 2373]),  # nb=1467 in both cases.
        ],
    )
    def test_pinned_sash_positions(self, main_w, expected_2pane, expected_3pane):
        assert compute_main_paned_sashes(
            main_w, chartstack_visible=False) == expected_2pane
        assert compute_main_paned_sashes(
            main_w, chartstack_visible=True) == expected_3pane
