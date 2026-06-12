"""Centered-ratio y-axis for the RVOL / RRVOL ratio panes.

User-confirmed design: the ratio pane (``z_score=False``) defaults to a
piecewise FuncScale that pins **0 to the bottom, the 1.0 "average" baseline to
the vertical center, and the visible max to the top**, with a 5× top floor so
the 2×/5× decision bands stay put in calm windows. ``log`` and ``linear`` are
one click away; the z-score pane is never centered.

Pins:
- param model: ``axis_mode`` choice (default ``centered``) replaces the legacy
  ``log_scale`` bool, which is still accepted and mapped to ``log``;
- shared-pane mode resolution with ``log > centered > linear`` precedence and a
  z-score carve-out;
- centered FuncScale anchors (0 bottom / 1.0 center / max top) and the 5× floor;
- dynamic ``top`` (pan/zoom/stream updates the attr + ylim, not the scale);
- dark-mode major tick labels follow the theme on a centered axis.
"""
from __future__ import annotations

from types import SimpleNamespace

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402

from tradinglab.indicators import render as R  # noqa: E402
from tradinglab.indicators.rrvol import RRVOL  # noqa: E402
from tradinglab.indicators.rvol import AXIS_MODES, RVOL, resolve_axis_mode  # noqa: E402
from tradinglab.rendering import setup_indicator_pane_axes, style_axes  # noqa: E402


# --------------------------------------------------------------------------- #
# Param model
# --------------------------------------------------------------------------- #
class TestParamModel:
    def test_rvol_default_is_centered(self):
        assert RVOL().axis_mode == "centered"
        assert RRVOL().axis_mode == "centered"

    def test_schema_swapped_log_scale_for_axis_mode(self):
        names = {p.name for p in RVOL.params_schema}
        assert "axis_mode" in names
        assert "log_scale" not in names
        rr_names = {p.name for p in RRVOL.params_schema}
        assert "axis_mode" in rr_names

    def test_axis_mode_default_and_choices(self):
        pdef = next(p for p in RVOL.params_schema if p.name == "axis_mode")
        assert pdef.default == "centered"
        assert tuple(pdef.choices) == AXIS_MODES == ("centered", "linear", "log")

    def test_legacy_log_scale_kwarg_maps_to_log(self):
        # Persisted pre-axis_mode configs construct via factory(**params).
        assert RVOL(log_scale=True).axis_mode == "log"
        assert RVOL(log_scale=True).log_scale is True

    def test_explicit_modes_round_trip(self):
        assert RVOL(axis_mode="linear").axis_mode == "linear"
        assert RVOL(axis_mode="log").axis_mode == "log"
        assert RRVOL(axis_mode="log").axis_mode == "log"

    def test_unknown_axis_mode_falls_back_to_centered(self):
        assert RVOL(axis_mode="bogus").axis_mode == "centered"

    def test_resolve_axis_mode_helper(self):
        assert resolve_axis_mode() == "centered"
        assert resolve_axis_mode("centered", log_scale=True) == "log"
        assert resolve_axis_mode("linear", log_scale=True) == "linear"  # explicit wins
        assert resolve_axis_mode("log") == "log"

    def test_axis_mode_excluded_from_trigger_relevant_params(self):
        # View-only: must not leak into scanner/entries/exits forms.
        assert "axis_mode" not in RVOL.TRIGGER_RELEVANT_PARAMS
        assert "axis_mode" not in RRVOL.TRIGGER_RELEVANT_PARAMS


# --------------------------------------------------------------------------- #
# Pane-mode resolution (shared panes + z-score carve-out + legacy)
# --------------------------------------------------------------------------- #
def _cfg(visible=True, **params):
    return SimpleNamespace(visible=visible, params=params)


class TestResolvePaneAxisMode:
    def test_config_axis_mode_default_and_legacy(self):
        assert R._config_axis_mode({}) == "centered"
        assert R._config_axis_mode({"axis_mode": "linear"}) == "linear"
        assert R._config_axis_mode({"log_scale": True}) == "log"
        # Explicit axis_mode beats a stale legacy log_scale.
        assert R._config_axis_mode({"axis_mode": "centered", "log_scale": True}) == "centered"

    def test_single_centered_default(self):
        assert R._resolve_pane_axis_mode([_cfg(mode="simple")]) == "centered"

    def test_log_wins_on_shared_pane(self):
        # Matches the legacy ``any(log_scale)`` shared-pane semantics.
        group = [_cfg(mode="simple"), _cfg(log_scale=True)]
        assert R._resolve_pane_axis_mode(group) == "log"

    def test_zscore_pane_stays_linear(self):
        assert R._resolve_pane_axis_mode([_cfg(z_score=True)]) == "linear"
        # A z-score config never drags a shared pane to centered/log.
        assert R._resolve_pane_axis_mode([_cfg(z_score=True, axis_mode="log")]) == "linear"

    def test_all_explicit_linear(self):
        assert R._resolve_pane_axis_mode([_cfg(axis_mode="linear")]) == "linear"

    def test_invisible_configs_ignored(self):
        assert R._resolve_pane_axis_mode([_cfg(visible=False, log_scale=True)]) == "linear"


# --------------------------------------------------------------------------- #
# Centered scale application + autoscale anchors
# --------------------------------------------------------------------------- #
def _fresh_pane():
    fig = plt.figure(figsize=(6, 1.2))
    FigureCanvasAgg(fig)
    ax = fig.add_subplot()
    setup_indicator_pane_axes(ax)
    return fig, ax


def _frac(ax, value):
    """Axes-fraction height (0=bottom, 1=top) of a data ``value``."""
    ax.figure.canvas.draw()
    disp = ax.transData.transform((0, value))[1]
    a0 = ax.transAxes.transform((0, 0))[1]
    a1 = ax.transAxes.transform((0, 1))[1]
    return (disp - a0) / (a1 - a0)


class TestCenteredScale:
    def test_apply_sets_function_scale_and_tag(self):
        fig, ax = _fresh_pane()
        try:
            R._apply_pane_axis_scale(ax, "centered")
            assert ax.get_yscale() == "function"
            assert ax._sc_axis_mode == "centered"
            assert isinstance(ax.yaxis.get_major_locator(), R._CenteredRatioLocator)
            assert ax.yaxis.get_label_position() == "right"
        finally:
            plt.close(fig)

    def test_anchors_calm_window_uses_5x_floor(self):
        fig, ax = _fresh_pane()
        try:
            R._apply_pane_axis_scale(ax, "centered")
            ln, = ax.plot(np.arange(30), np.linspace(0.6, 3.0, 30))  # max 3.0 < floor
            R.autoscale_pane_y(ax, [ln], 0, 30)
            assert ax._sc_centered_top == 5.0  # floored
            assert ax.get_ylim() == (0.0, 5.0)
            assert abs(_frac(ax, 0.0) - 0.0) < 1e-6
            assert abs(_frac(ax, 1.0) - 0.5) < 1e-6
            assert abs(_frac(ax, 5.0) - 1.0) < 1e-6
        finally:
            plt.close(fig)

    def test_anchors_spike_window_tracks_max(self):
        fig, ax = _fresh_pane()
        try:
            R._apply_pane_axis_scale(ax, "centered")
            ln, = ax.plot(np.arange(30), np.linspace(0.6, 12.0, 30))
            R.autoscale_pane_y(ax, [ln], 0, 30)
            assert ax._sc_centered_top == 12.0
            assert abs(_frac(ax, 0.0) - 0.0) < 1e-6
            assert abs(_frac(ax, 1.0) - 0.5) < 1e-6  # 1.0 STILL centered
            assert abs(_frac(ax, 12.0) - 1.0) < 1e-6
        finally:
            plt.close(fig)

    def test_top_is_dynamic_without_resetting_scale(self):
        """Pan/zoom/stream updates the attr + ylim only — scale set once."""
        fig, ax = _fresh_pane()
        try:
            R._apply_pane_axis_scale(ax, "centered")
            ln, = ax.plot(np.arange(30), np.linspace(0.6, 3.0, 30))
            R.autoscale_pane_y(ax, [ln], 0, 30)
            mid_calm = _frac(ax, 3.0)
            # Simulate a spike entering the window (no re-apply of the scale).
            ln.set_data(np.arange(30), np.linspace(0.6, 12.0, 30))
            R.autoscale_pane_y(ax, [ln], 0, 30)
            mid_spike = _frac(ax, 3.0)
            assert mid_calm > mid_spike  # 3.0 compresses downward as top grows
            assert abs(_frac(ax, 1.0) - 0.5) < 1e-6  # center invariant
        finally:
            plt.close(fig)

    def test_untagged_axis_keeps_linear_autoscale(self):
        """Regression: panes without the centered tag use the prior pad rule."""
        fig = plt.figure()
        ax = fig.add_subplot()
        try:
            ln, = ax.plot(np.arange(20), np.arange(20) * 10.0)
            R.autoscale_pane_y(ax, [ln], 0, 5)  # window 0..40
            ylo, yhi = ax.get_ylim()
            assert ylo < 0  # additive 5% pad below
            assert 38.0 < yhi < 45.0
        finally:
            plt.close(fig)


# --------------------------------------------------------------------------- #
# Locator density + dark-mode theming
# --------------------------------------------------------------------------- #
class TestLocatorAndTheme:
    def test_locator_positions_calm_and_spike(self):
        fig, ax = _fresh_pane()
        try:
            loc = R._CenteredRatioLocator(ax)
            ax._sc_centered_top = 5.0
            ax.figure.canvas.draw()
            calm = loc()
            assert 0.0 in calm and 1.0 in calm and 5.0 in calm
            ax._sc_centered_top = 12.0
            ax.figure.canvas.draw()
            spike = loc()
            assert {0.0, 1.0, 12.0}.issubset(set(spike))
            assert max(spike) == 12.0
        finally:
            plt.close(fig)

    def test_tiny_pane_keeps_minimum_anchors(self):
        fig, ax = _fresh_pane()
        try:
            loc = R._CenteredRatioLocator(ax, min_label_px=28)
            ax._sc_centered_top = 12.0
            # Force a very short pane: ~40px → cap 3 → only 0/1/top.
            ax.figure.set_size_inches(6, 0.45)
            ax.figure.canvas.draw()
            pos = loc()
            assert 0.0 in pos and 1.0 in pos and 12.0 in pos
            assert len(pos) >= 3
        finally:
            plt.close(fig)

    def test_centered_major_labels_follow_dark_theme(self):
        dark = {"ax_bg": "#1e1e1e", "text": "#e0e0e0", "spine": "#888", "grid": "#444"}
        fig, ax = _fresh_pane()
        try:
            style_axes(ax, dark)  # stores which="both" tick color kwargs
            R._apply_pane_axis_scale(ax, "centered")  # set_yscale must not undo it
            ax._sc_centered_top = 8.0
            ax.plot(np.arange(10), np.linspace(0.5, 8.0, 10))
            ax.set_ylim(0, 8.0)
            fig.canvas.draw()
            labels = [
                (t.label2.get_color() if t.label2.get_text() else t.label1.get_color())
                for t in ax.yaxis.get_major_ticks()
                if (t.label2.get_text() or t.label1.get_text()).strip()
            ]
            assert labels, "expected centered-axis tick labels"
            for c in labels:
                assert c == dark["text"], f"centered tick label not themed: {c!r}"
        finally:
            plt.close(fig)
