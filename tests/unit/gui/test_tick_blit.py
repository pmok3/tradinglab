"""Live-tick blit fast path (cluster 1).

Covers the renderer's blit-vs-draw_idle decision (``refresh_view_after_tick``
+ ``_snapshot_slot_limits``) with a bare ``ChartRenderer`` + stub callbacks,
and the app-side ``_paint_tick_frame`` / artist-collection helpers against a
real headless ``ChartApp``.
"""
from __future__ import annotations

import os

import pytest

# Force headless matplotlib before importing it.
os.environ.setdefault("MPLBACKEND", "Agg")

import matplotlib.pyplot as plt  # noqa: E402

from tradinglab.gui.chart_renderer import ChartRenderer  # noqa: E402

# ---------------------------------------------------------------------------
# Renderer decision logic (bare ChartRenderer, stub callbacks)
# ---------------------------------------------------------------------------


def _renderer_with_slot(n: int = 20):
    r = ChartRenderer()
    fig, (ax_p, ax_v) = plt.subplots(2, 1)
    ax_p.set_xlim(0, n)
    ax_p.set_ylim(0, 100)
    ax_v.set_xlim(0, n)
    ax_v.set_ylim(0, 10)
    r.panel_state["primary"] = {
        "price_ax": ax_p,
        "volume_ax": ax_v,
        "indicator_axes": [],
        "render_start": 0,
        "render_end": n,
        "candles": list(range(n)),
    }
    return r, fig, ax_p, ax_v


def test_blits_when_limits_unchanged():
    r, fig, ax_p, _ = _renderer_with_slot()
    calls = {"draw_idle": 0, "blit": 0}

    def blit_tick_frame(_slot):
        calls["blit"] += 1
        return True

    try:
        r.refresh_view_after_tick(
            "primary",
            apply_tick_to_artists=lambda _s: True,
            draw_slice=lambda *a: None,
            autoscale_slot_y=lambda *a: None,  # leaves limits unchanged
            autoscale_indicator_panes=lambda _s: None,
            canvas_draw_idle=lambda: calls.__setitem__("draw_idle", calls["draw_idle"] + 1),
            blit_tick_frame=blit_tick_frame,
        )
    finally:
        plt.close(fig)
    assert calls["blit"] == 1
    assert calls["draw_idle"] == 0


def test_draws_when_ylim_changes():
    r, fig, ax_p, _ = _renderer_with_slot()
    calls = {"draw_idle": 0, "blit": 0}

    def autoscale(_slot, _lo, _hi):
        ax_p.set_ylim(0, 200)  # move the limit -> blit ineligible

    try:
        r.refresh_view_after_tick(
            "primary",
            apply_tick_to_artists=lambda _s: True,
            draw_slice=lambda *a: None,
            autoscale_slot_y=autoscale,
            autoscale_indicator_panes=lambda _s: None,
            canvas_draw_idle=lambda: calls.__setitem__("draw_idle", calls["draw_idle"] + 1),
            blit_tick_frame=lambda _s: calls.__setitem__("blit", calls["blit"] + 1) or True,
        )
    finally:
        plt.close(fig)
    assert calls["blit"] == 0
    assert calls["draw_idle"] == 1


def test_draws_when_apply_tick_fails():
    r, fig, ax_p, _ = _renderer_with_slot()
    calls = {"draw_idle": 0, "blit": 0, "draw_slice": 0}
    try:
        r.refresh_view_after_tick(
            "primary",
            apply_tick_to_artists=lambda _s: False,  # HA / highlight rebuild
            draw_slice=lambda *a: calls.__setitem__("draw_slice", calls["draw_slice"] + 1),
            autoscale_slot_y=lambda *a: None,
            autoscale_indicator_panes=lambda _s: None,
            canvas_draw_idle=lambda: calls.__setitem__("draw_idle", calls["draw_idle"] + 1),
            blit_tick_frame=lambda _s: calls.__setitem__("blit", calls["blit"] + 1) or True,
        )
    finally:
        plt.close(fig)
    assert calls["draw_slice"] == 1
    assert calls["blit"] == 0
    assert calls["draw_idle"] == 1


def test_no_blit_callback_falls_back_to_draw_idle():
    r, fig, _ax, _ = _renderer_with_slot()
    calls = {"draw_idle": 0}
    try:
        r.refresh_view_after_tick(
            "primary",
            apply_tick_to_artists=lambda _s: True,
            draw_slice=lambda *a: None,
            autoscale_slot_y=lambda *a: None,
            autoscale_indicator_panes=lambda _s: None,
            canvas_draw_idle=lambda: calls.__setitem__("draw_idle", calls["draw_idle"] + 1),
            blit_tick_frame=None,
        )
    finally:
        plt.close(fig)
    assert calls["draw_idle"] == 1


def test_snapshot_slot_limits_detects_change():
    r, fig, ax_p, _ = _renderer_with_slot()
    try:
        ps = r.panel_state["primary"]
        a = r._snapshot_slot_limits(ps)
        b = r._snapshot_slot_limits(ps)
        assert a == b
        ax_p.set_ylim(0, 999)
        c = r._snapshot_slot_limits(ps)
        assert c != a
    finally:
        plt.close(fig)


# ---------------------------------------------------------------------------
# App-side paint frame + artist collection (real headless ChartApp)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rendered_app():
    from tests.smoke._helpers import _pump, _stub_yfinance

    _stub_yfinance()
    from tradinglab.app import ChartApp

    a = ChartApp()
    try:
        a.geometry("900x650-3000-3000")
    except Exception:
        pass
    try:
        a.withdraw()
    except Exception:
        pass
    _pump(a, 0.3)
    try:
        a._render()
    except Exception:
        pass
    _pump(a, 0.2)
    yield a
    try:
        a._on_close()
    except Exception:
        pass


def test_collect_excludes_overlay_artists(rendered_app):
    app = rendered_app
    arts = app._collect_tick_blit_artists()
    assert arts, "expected at least the candle artists"
    collected = {id(a) for _ax, a in arts}
    overlay_ids = app._overlay_artist_ids()
    # No overlay artist may appear in the data set.
    assert collected.isdisjoint(overlay_ids)
    # The primary candle collections must be present.
    ps = app._panel_state.get("primary") or {}
    for key in ("price_wicks", "price_bodies"):
        art = ps.get(key)
        if art is not None:
            assert id(art) in collected


def test_paint_tick_frame_seeds_then_reuses_background(rendered_app):
    app = rendered_app
    app._tick_blit_bg = None
    real_draw = app._canvas.draw
    counter = {"n": 0}

    def counting_draw(*a, **k):
        counter["n"] += 1
        return real_draw(*a, **k)

    app._canvas.draw = counting_draw  # type: ignore[assignment]
    try:
        ok1 = app._paint_tick_frame("primary")
        assert ok1 is True
        assert app._tick_blit_bg is not None
        assert counter["n"] == 1  # one hidden capture draw
        ok2 = app._paint_tick_frame("primary")
        assert ok2 is True
        assert counter["n"] == 1  # reused — no recapture
        # Invalidating the hover background invalidates the tick bg too.
        app._blit_bg = None
        assert app._tick_blit_bg is None
        ok3 = app._paint_tick_frame("primary")
        assert ok3 is True
        assert counter["n"] == 2  # recaptured after invalidation
    finally:
        app._canvas.draw = real_draw  # type: ignore[assignment]


def test_paint_tick_frame_restores_artist_visibility(rendered_app):
    app = rendered_app
    app._tick_blit_bg = None
    arts = app._collect_tick_blit_artists()
    before = [(a, a.get_visible()) for _ax, a in arts]
    app._paint_tick_frame("primary")
    for a, vis in before:
        assert a.get_visible() == vis


def test_tick_blit_fires_end_to_end(rendered_app):
    """A real ``_refresh_view_after_tick`` on an eligible chart must blit,
    NOT fall back to ``canvas.draw_idle()`` (the silent-regression guard)."""
    app = rendered_app
    # Eligibility: in-place tick mutation must succeed (right-edge, non-gap,
    # no HA/highlight). Pre-flight via the same predicate the renderer uses.
    if not app._apply_tick_to_artists("primary"):
        pytest.skip("tick fast path not eligible in this render state")

    app._tick_blit_bg = None
    app._tick_blit_fires = 0
    real_draw_idle = app._canvas.draw_idle
    di = {"n": 0}

    def counting_draw_idle(*a, **k):
        di["n"] += 1
        return real_draw_idle(*a, **k)

    app._canvas.draw_idle = counting_draw_idle  # type: ignore[assignment]
    try:
        app._refresh_view_after_tick("primary")
    finally:
        app._canvas.draw_idle = real_draw_idle  # type: ignore[assignment]

    assert app._tick_blit_fires >= 1, "blit fast path did not fire"
    assert di["n"] == 0, "fell back to draw_idle despite stable limits"

