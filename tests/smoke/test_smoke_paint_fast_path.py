"""Stage 1+4: topology-preserving fast-path dispatch + correctness.

``ChartApp._render`` has a fast path (``_paint_topology_preserve``, **ON by
default** as of the Stage 4 roll-out) that reuses the existing axes instead of
``figure.clear()`` when the topology key is unchanged. These checks pin:

* flag OFF → the fast path NEVER fires (legacy rebuild behavior);
* flag ON + unchanged topology → the fast path fires AND reuses the same Axes
  objects while re-pointing candles + rebuilding the data artists;
* a topology change (interval) → falls back to the slow rebuild;
* any fast-path exception → falls back to the slow rebuild (no crash);
* the shipped default (no env/settings override) fires the fast path.

Uses the shared session ``app`` fixture (avoids the multi-Tk-root collision
the smoke conftest guards against). An autouse guard save/restores the flag
around every test so the rest of the smoke session is unaffected.
"""
from __future__ import annotations

import os
import types

import pytest

from tests.smoke._helpers import _pump


@pytest.fixture(autouse=True)
def _restore_paint_flag(app):
    """Save/restore the flag around each test so these checks don't change the
    session-wide default (now ON) seen by other smoke modules."""
    saved = app._paint_topology_preserve
    yield
    app._paint_topology_preserve = saved


def _establish_topology(app):
    """Render once via the slow path so ``_last_topology_key`` is set for the
    current (compare/interval/pane) topology, with the one-shot xlim signals
    cleared so the next render is fast-path eligible."""
    app._paint_topology_preserve = False
    app._preserve_xlim_on_render = False
    app._slide_xlim_to_right_edge = False
    app._preserve_xlim_by_time_on_render = False
    app._render()
    _pump(app, 0.05)


def test_flag_off_never_fast_paths(app):
    _establish_topology(app)
    f0 = app._render_topology_preserved_fires
    app._render()
    _pump(app, 0.05)
    app._render()
    _pump(app, 0.05)
    assert app._render_topology_preserved_fires == f0


def test_default_is_on_and_fires_fast_path(app):
    """Stage 4 roll-out: the fast path is ON by default — with NO flag
    manipulation, an unchanged-topology re-render takes the fast path.

    Skipped when an env override is active (the session app's flag was forced
    by `TRADINGLAB_PAINT_TOPOLOGY_PRESERVE`), since this guards the *unset*
    shipped default specifically.
    """
    if os.environ.get("TRADINGLAB_PAINT_TOPOLOGY_PRESERVE") is not None:
        pytest.skip("env override active; this guard validates the unset default")
    assert app._paint_topology_preserve is True  # shipped default is ON
    app._preserve_xlim_on_render = False
    app._slide_xlim_to_right_edge = False
    app._preserve_xlim_by_time_on_render = False
    app._render()  # establish the topology key (flag left at its default)
    _pump(app, 0.05)
    f0 = app._render_topology_preserved_fires
    app._preserve_xlim_on_render = False
    app._render()  # same topology → default fast path
    _pump(app, 0.05)
    assert app._render_topology_preserved_fires == f0 + 1


def test_fast_path_fires_and_reuses_axes(app):
    _establish_topology(app)
    ps = app._panel_state["primary"]
    ax_p_before = ps["price_ax"]
    ax_v_before = ps["vol_ax"]
    f0 = app._render_topology_preserved_fires

    app._paint_topology_preserve = True
    app._preserve_xlim_on_render = False
    app._render()
    _pump(app, 0.05)

    # Fast path fired exactly once.
    assert app._render_topology_preserved_fires == f0 + 1
    # Same Axes objects reused — no figure.clear()/add_subplot.
    assert app._panel_state["primary"]["price_ax"] is ax_p_before
    assert app._panel_state["primary"]["vol_ax"] is ax_v_before
    # Data artists were rebuilt in place + candles re-pointed.
    assert app._panel_state["primary"]["price_bodies"] is not None
    assert app._panel_state["primary"]["candles"] is app._primary
    # xlim is well-formed.
    lo, hi = ax_p_before.get_xlim()
    assert hi > lo


def test_topology_change_falls_back_to_slow(app):
    _establish_topology(app)
    f0 = app._render_topology_preserved_fires
    saved_iv = app.interval_var.get()
    app._paint_topology_preserve = True
    try:
        new_iv = "5m" if saved_iv != "5m" else "1d"
        app.interval_var.set(new_iv)  # interval ∈ topology key → key changes
        app._preserve_xlim_on_render = False
        app._render()
        _pump(app, 0.05)
        # Topology differed → fast path must NOT have fired.
        assert app._render_topology_preserved_fires == f0
    finally:
        app.interval_var.set(saved_iv)
        app._paint_topology_preserve = False
        app._render()  # restore a consistent slow render for later tests
        _pump(app, 0.05)


def test_fast_path_error_falls_back_to_slow(app):
    _establish_topology(app)
    f0 = app._render_topology_preserved_fires
    app._paint_topology_preserve = True

    def _boom(self, **kwargs):
        raise RuntimeError("induced fast-path failure")

    app._render_topology_preserved = types.MethodType(_boom, app)
    try:
        app._preserve_xlim_on_render = False
        app._render()  # dispatch tries fast path → raises → caught → slow path
        _pump(app, 0.05)
    finally:
        del app._render_topology_preserved
        app._paint_topology_preserve = False

    # The failed attempt was not counted as a success...
    assert app._render_topology_preserved_fires == f0
    # ...and the slow-path fallback produced a valid render.
    assert app._panel_state["primary"]["price_bodies"] is not None


def test_dispatch_falls_back_on_topology_key_mismatch(app):
    """ANY topology change shifts ``_compute_topology_key`` (compare toggle,
    interval, indicator add/remove/reorder, drilldown — each pinned at the unit
    level in ``test_paint_topology_key.py``). The dispatch compares the live
    key against ``_last_topology_key`` and must fall back on a mismatch — pinned
    here by forcing a stale key, which stands in for any transition."""
    _establish_topology(app)
    f0 = app._render_topology_preserved_fires
    app._paint_topology_preserve = True
    app._last_topology_key = ("stale", "topology", "key")
    app._preserve_xlim_on_render = False
    try:
        app._render()
        _pump(app, 0.05)
        assert app._render_topology_preserved_fires == f0  # mismatch → slow
    finally:
        app._paint_topology_preserve = False


def test_preserve_xlim_excludes_fast_path(app):
    """Drill-down / preserve renders read ``_panel_state`` with load-bearing
    axes-lifecycle assumptions — the fast path is excluded even when the
    topology key matches."""
    _establish_topology(app)
    f0 = app._render_topology_preserved_fires
    app._paint_topology_preserve = True
    app._preserve_xlim_on_render = True  # excludes the fast path
    try:
        app._render()
        _pump(app, 0.05)
        assert app._render_topology_preserved_fires == f0  # slow path
    finally:
        app._preserve_xlim_on_render = False
        app._paint_topology_preserve = False


def test_slide_to_right_excludes_fast_path(app):
    """The right-edge slide signal (poll-tick glued-to-right) carries an xlim
    transform the fast path doesn't replicate — excluded even on a key match."""
    _establish_topology(app)
    f0 = app._render_topology_preserved_fires
    app._paint_topology_preserve = True
    app._preserve_xlim_on_render = False
    app._slide_xlim_to_right_edge = True  # excludes the fast path
    try:
        app._render()
        _pump(app, 0.05)
        assert app._render_topology_preserved_fires == f0  # slow path
    finally:
        app._slide_xlim_to_right_edge = False
        app._paint_topology_preserve = False


def test_fast_path_handles_time_remap(app):
    """``preserve_by_time`` (ticker-switch window preservation) IS fast-path
    eligible — the fast path replicates the calendar-window remap rather than
    falling back. Pan to a sub-window, set the signal, fast-render, and confirm
    the fast path fired and kept a well-formed window."""
    _establish_topology(app)
    n = len(app._primary)
    if n < 20:
        pytest.skip("need >= 20 primary bars to exercise a sub-window remap")
    app._panel_state["primary"]["price_ax"].set_xlim(5.0, 15.0)
    app._paint_topology_preserve = True
    app._preserve_xlim_on_render = False
    app._preserve_xlim_by_time_on_render = True
    f0 = app._render_topology_preserved_fires
    try:
        app._render()
        _pump(app, 0.05)
        # Time-remap is handled IN the fast path (not a fallback trigger).
        assert app._render_topology_preserved_fires == f0 + 1
        lo, hi = app._panel_state["primary"]["price_ax"].get_xlim()
        assert hi > lo
    finally:
        app._preserve_xlim_by_time_on_render = False
        app._paint_topology_preserve = False
