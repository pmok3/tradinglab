"""Shared-pane multi-config consumers: autoscale union, log scale,
per-config clickable labels, and multi-config hover.

When two indicator configs share one lower pane (e.g. RVOL Cumulative +
RVOL ToD, both `pane_group="rvol"`), the render layer stores BOTH, but
three consumers historically collapsed to `config[0]`: y-autoscale (clip
one), hover readout (show one), and label-click-to-modify (open the
first). These pin the fixes. See `indicators/render.spec.md`,
`gui/interaction.spec.md`, `gui/chart_renderer.spec.md`.
"""
from __future__ import annotations

from types import SimpleNamespace

import matplotlib
import numpy as np

matplotlib.use("Agg")
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from tradinglab.indicators import render as R


def _fresh_pane():
    fig = Figure(figsize=(8, 2), dpi=100)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    return fig, ax


# --- lines_by_pane_axes ----------------------------------------------------

def test_lines_by_pane_axes_groups_shared_axes():
    _fig, ax = _fresh_pane()
    (l1,) = ax.plot([0, 1, 2], [1, 2, 3])
    (l2,) = ax.plot([0, 1, 2], [1, 8, 2])
    state = SimpleNamespace(
        panes={1: ax, 2: ax}, pane_lines={1: {"rvol": l1}, 2: {"rvol": l2}},
    )
    grouped = R.lines_by_pane_axes(state)
    assert len(grouped) == 1, "two cfg_ids on one axes → one bucket"
    ax_obj, lines = grouped[0]
    assert ax_obj is ax
    assert set(lines) == {l1, l2}


def test_lines_by_pane_axes_distinct_axes_stay_separate():
    _fig1, ax1 = _fresh_pane()
    _fig2, ax2 = _fresh_pane()
    (l1,) = ax1.plot([0, 1], [1, 2])
    (l2,) = ax2.plot([0, 1], [3, 4])
    state = SimpleNamespace(
        panes={1: ax1, 2: ax2}, pane_lines={1: {"a": l1}, 2: {"b": l2}},
    )
    grouped = R.lines_by_pane_axes(state)
    assert len(grouped) == 2


# --- autoscale: union (linear) + log-aware ---------------------------------

def test_autoscale_union_fits_the_taller_series():
    _fig, ax = _fresh_pane()
    (l_cum,) = ax.plot(range(5), [1.0, 1.2, 0.9, 1.1, 1.0])
    (l_tod,) = ax.plot(range(5), [1.0, 8.0, 1.0, 6.0, 1.0])  # spiky
    R.autoscale_pane_y(ax, [l_cum, l_tod], 0, 5)
    lo, hi = ax.get_ylim()
    assert hi >= 8.0, "shared-pane fit must include the ToD spike (8x)"
    assert lo <= 0.9, "and still include the Cumulative trough"


def test_autoscale_log_axis_positive_limits():
    _fig, ax = _fresh_pane()
    ax.set_yscale("log")
    # Include a zero (warmup / no-volume bar) which is invalid on log.
    (ln,) = ax.plot(range(5), [0.0, 1.0, 2.0, 8.0, 1.5])
    R.autoscale_pane_y(ax, [ln], 0, 5)
    lo, hi = ax.get_ylim()
    assert lo > 0.0, "log axis lower bound must be strictly positive"
    assert hi >= 8.0


# --- per-config clickable labels -------------------------------------------

def _cfg(cid, name):
    return SimpleNamespace(id=cid, display_name=name, kind_id="rvol",
                           visible=True, params={})


def test_render_pane_labels_one_pickable_artist_per_config():
    _fig, ax = _fresh_pane()
    cfgs = [_cfg(1, "RVOL Cum(20)"), _cfg(2, "RVOL ToD(20)")]
    R._render_pane_labels(ax, cfgs, scope="main")
    artists = ax._sc_pane_label_artists
    # Two name artists (each carrying ONE config id) — no "•" spacers anymore
    # (each name is followed by its own inline value slot instead).
    name_artists = [a for a in artists if getattr(a, "_sc_pane_label_config_ids", ())]
    assert len(name_artists) == 2
    assert len(artists) == 2, "only name artists are stored (no bullet spacers)"
    ids = {a._sc_pane_label_config_ids for a in name_artists}
    assert ids == {(1,), (2,)}, "each name artist carries exactly its own id"
    # A value slot (x-position) is reserved per config, right after its name,
    # laid out left-to-right so value[1] sits before name[2].
    x_by_cid = ax._sc_pane_value_x_by_cid
    assert set(x_by_cid) == {1, 2}, "one reserved value x-position per config"
    assert x_by_cid[1] < x_by_cid[2], "values laid out left-to-right after names"
    # Back-compat singular points at the first name artist.
    assert ax._sc_pane_label_artist is name_artists[0]


def test_render_pane_labels_empty_clears():
    _fig, ax = _fresh_pane()
    R._render_pane_labels(ax, [_cfg(1, "RVOL Cum(20)")], scope="main")
    assert ax._sc_pane_label_artists
    R._render_pane_labels(ax, [], scope="main")
    assert ax._sc_pane_label_artists == []
    assert ax._sc_pane_label_artist is None


def test_pane_label_hit_targets_the_clicked_config():
    """_pane_indicator_label_hit returns the config of the name under the
    cursor, not config[0]."""
    from tradinglab.gui.interaction import InteractionMixin

    _fig, ax = _fresh_pane()
    cfgs = [_cfg(10, "RVOL Cum(20)"), _cfg(20, "RVOL ToD(20)")]
    R._render_pane_labels(ax, cfgs, scope="main")
    _fig.canvas.draw()  # realise extents

    name_artists = [
        a for a in ax._sc_pane_label_artists
        if getattr(a, "_sc_pane_label_config_ids", ())
    ]
    second = next(a for a in name_artists if a._sc_pane_label_config_ids == (20,))
    bbox = second.get_window_extent(_fig.canvas.get_renderer())
    # Synthetic event over the SECOND name (ToD).
    ev = SimpleNamespace(
        inaxes=ax, x=(bbox.x0 + bbox.x1) / 2.0, y=(bbox.y0 + bbox.y1) / 2.0,
    )
    app = SimpleNamespace(_canvas=_fig.canvas)
    label, cid = InteractionMixin._pane_indicator_label_hit(app, ev)
    assert cid == 20, "click on the ToD name must target the ToD config"


def test_pane_label_hit_skips_spacers_and_misses():
    from tradinglab.gui.interaction import InteractionMixin

    _fig, ax = _fresh_pane()
    R._render_pane_labels(ax, [_cfg(1, "RVOL Cum(20)")], scope="main")
    _fig.canvas.draw()
    # Click far outside any label.
    ev = SimpleNamespace(inaxes=ax, x=5.0, y=5.0)
    app = SimpleNamespace(_canvas=_fig.canvas)
    label, cid = InteractionMixin._pane_indicator_label_hit(app, ev)
    assert label is None and cid is None


# --- hover enumerates every config on a shared pane ------------------------

def test_indicator_lines_at_enumerates_all_shared_configs():
    from tradinglab.gui.interaction import InteractionMixin

    _fig, ax = _fresh_pane()
    (l_cum,) = ax.plot(range(5), [1.0, 1.5, 2.0, 1.0, 1.2])
    (l_tod,) = ax.plot(range(5), [1.0, 8.0, 1.0, 3.0, 1.0])
    state = SimpleNamespace(
        overlay_lines={},
        panes={10: ax, 20: ax},
        pane_lines={10: {"rvol": l_cum}, 20: {"rvol": l_tod}},
    )
    mgr = SimpleNamespace(get=lambda cid: {
        10: SimpleNamespace(display_name="RVOL Cum(20)", kind_id="rvol", visible=True),
        20: SimpleNamespace(display_name="RVOL ToD(20)", kind_id="rvol", visible=True),
    }[cid])
    app = SimpleNamespace(
        _indicator_manager=mgr,
        _find_indicator_panel_for_axes=lambda a: ({"ind_state": state}, "indicator"),
        _line_value_at=lambda ln, idx: float(ln.get_ydata()[idx]),
    )
    out = InteractionMixin._indicator_lines_at(app, ax, idx=1)
    joined = " | ".join(out)
    assert "RVOL Cum(20)" in joined, "hover must include Cumulative"
    assert "RVOL ToD(20)" in joined, "hover must include ToD (the second config)"
    assert "8.00" in joined, "ToD's value at the cursor bar must show"
