"""Multi-layer tests for :mod:`tradinglab.gui.chart_renderer`.

The :class:`ChartRenderer` owns panel state + reusable rendering
helpers extracted from the monolithic ChartApp. It's pure matplotlib
(Agg backend works headlessly) and has no Tk surface, so we can drive
the public API directly without booting an app shell.

Targeted methods:
* ``reset_slot_artists`` — torn-down state shape.
* ``display_candles_for`` — HA on/off branching.
* ``key_bar_hollow_indices_for`` — feature gate + computation.
* ``ha_flat_overlay_for`` — gating + dark-mode color resolution.
* ``repaint_visible_slot_glyphs`` — fall-back to render_fallback when
  panel_state is empty.
* ``autoscale_slot_y`` — ylim fit + no-op branches.
* ``refresh_view_after_tick`` / ``refresh_view_after_append`` —
  viewport preservation + autoscale orchestration.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pytest

from tradinglab.gui.chart_renderer import ChartRenderer
from tradinglab.models import Candle


def _candle(i: int, *, gap: bool = False) -> Candle:
    ts = _dt.datetime(2025, 5, 1) + _dt.timedelta(days=i)
    if gap:
        return Candle.gap(ts)
    return Candle(
        date=ts, open=100 + i, high=101 + i, low=99 + i, close=100.5 + i,
        volume=1_000_000, session="regular",
    )


def _candles(n: int) -> list[Candle]:
    return [_candle(i) for i in range(n)]


# ---------------------------------------------------------------------------
# 1. ChartRenderer construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_default_state_empty(self):
        r = ChartRenderer()
        assert r.panel_state == {}
        assert len(r.ax_candle_map) == 0
        assert r.blit_bg is None


# ---------------------------------------------------------------------------
# 2. reset_slot_artists
# ---------------------------------------------------------------------------


class TestResetSlotArtists:
    def test_missing_slot_is_noop(self):
        r = ChartRenderer()
        # No raise.
        r.reset_slot_artists("primary")

    def test_clears_artist_keys(self):
        r = ChartRenderer()
        r.panel_state["primary"] = {
            "price_wicks": None,
            "price_bodies": None,
            "vol_bars": None,
            "price_shades": [],
            "vol_shades": [],
            "event_artists": [],
            "event_hit_meta": [],
            "event_badge_tooltip": "tip",
            "vol_tod_artists": [],
            "vol_tod_patches": [],
            "ind_state": None,
        }
        r.reset_slot_artists("primary")
        ps = r.panel_state["primary"]
        for key in ("price_wicks", "price_bodies", "vol_bars"):
            assert ps[key] is None
        assert ps["price_shades"] == []
        assert ps["event_artists"] == []
        assert ps["event_hit_meta"] == []
        assert ps["event_badge_tooltip"] == ""

    def test_clears_indicator_state_if_present(self):
        r = ChartRenderer()

        class _IndState:
            def __init__(self):
                self.cleared = False

            def clear(self):
                self.cleared = True

        ind_state = _IndState()
        r.panel_state["primary"] = {
            "price_wicks": None, "price_bodies": None, "vol_bars": None,
            "price_shades": [], "vol_shades": [], "event_artists": [],
            "event_hit_meta": [], "vol_tod_artists": [],
            "vol_tod_patches": [], "ind_state": ind_state,
        }
        r.reset_slot_artists("primary")
        assert ind_state.cleared is True


# ---------------------------------------------------------------------------
# 3. display_candles_for
# ---------------------------------------------------------------------------


class TestDisplayCandlesFor:
    def test_ha_off_passes_through(self):
        r = ChartRenderer()
        c = _candles(5)
        assert r.display_candles_for(c, ha_on=False) is c

    def test_empty_passes_through(self):
        r = ChartRenderer()
        assert r.display_candles_for([], ha_on=True) == []

    def test_ha_on_returns_transformed(self):
        r = ChartRenderer()
        c = _candles(5)
        out = r.display_candles_for(c, ha_on=True)
        # HA candles are different objects, same length.
        assert out is not c
        assert len(out) == 5


# ---------------------------------------------------------------------------
# 4. key_bar_hollow_indices_for
# ---------------------------------------------------------------------------


class TestKeyBarHollowIndicesFor:
    def test_disabled_returns_none(self):
        r = ChartRenderer()
        assert r.key_bar_hollow_indices_for(
            _candles(20), highlight_key_bars_on=False,
        ) is None

    def test_empty_returns_none(self):
        r = ChartRenderer()
        assert r.key_bar_hollow_indices_for(
            [], highlight_key_bars_on=True,
        ) is None

    def test_enabled_returns_set(self):
        r = ChartRenderer()
        out = r.key_bar_hollow_indices_for(
            _candles(50), highlight_key_bars_on=True,
        )
        # Result is a set of int (possibly empty for the synthetic series).
        assert isinstance(out, set)
        assert all(isinstance(i, int) for i in out)


# ---------------------------------------------------------------------------
# 5. ha_flat_overlay_for
# ---------------------------------------------------------------------------


class TestHaFlatOverlayFor:
    def test_gate_off_returns_none(self):
        r = ChartRenderer()
        c = _candles(5)
        assert r.ha_flat_overlay_for(
            c, highlight_ha_flat_on=False, ha_on=True, dark_mode=False,
        ) is None

    def test_ha_off_returns_none(self):
        r = ChartRenderer()
        c = _candles(5)
        assert r.ha_flat_overlay_for(
            c, highlight_ha_flat_on=True, ha_on=False, dark_mode=False,
        ) is None

    def test_empty_returns_none(self):
        r = ChartRenderer()
        assert r.ha_flat_overlay_for(
            [], highlight_ha_flat_on=True, ha_on=True, dark_mode=False,
        ) is None

    def test_returned_shape_when_flat_bars_exist(self):
        r = ChartRenderer()
        # Synthetic ascending closes ⇒ unlikely to produce flat bars; the
        # method may return None for this series. Both outcomes are valid.
        c = _candles(50)
        out = r.ha_flat_overlay_for(
            c, highlight_ha_flat_on=True, ha_on=True, dark_mode=False,
        )
        if out is not None:
            # Shape contract.
            assert "bull_indices" in out
            assert "bear_indices" in out
            assert "bull_color" in out
            assert "bear_color" in out
            assert out["bull_hatch"] == "xxx"
            assert out["bear_hatch"] == "xxx"


# ---------------------------------------------------------------------------
# 6. repaint_visible_slot_glyphs
# ---------------------------------------------------------------------------


class TestRepaintVisibleSlotGlyphs:
    def test_no_panel_state_invokes_fallback(self):
        r = ChartRenderer()
        called: list[bool] = []
        r.repaint_visible_slot_glyphs(
            draw_slice=lambda *a, **k: None,
            render_fallback=lambda: called.append(True),
        )
        assert called == [True]

    def test_calls_draw_slice_per_slot(self):
        r = ChartRenderer()
        r.panel_state["primary"] = {"render_start": 5, "render_end": 10}
        r.panel_state["compare"] = {"render_start": 0, "render_end": 3}
        calls: list[tuple] = []
        r.repaint_visible_slot_glyphs(
            draw_slice=lambda slot, rs, re_: calls.append((slot, rs, re_)),
            render_fallback=lambda: pytest.fail("fallback should NOT fire"),
        )
        assert set(calls) == {("primary", 5, 10), ("compare", 0, 3)}

    def test_skips_invalid_range(self):
        r = ChartRenderer()
        r.panel_state["primary"] = {"render_start": 10, "render_end": 10}
        called_draw: list = []
        called_fallback: list = []
        r.repaint_visible_slot_glyphs(
            draw_slice=lambda *a, **k: called_draw.append(a),
            render_fallback=lambda: called_fallback.append(True),
        )
        # No slot had a valid range ⇒ fallback fires.
        assert called_draw == []
        assert called_fallback == [True]

    def test_draw_slice_exception_falls_through(self):
        r = ChartRenderer()
        r.panel_state["primary"] = {"render_start": 0, "render_end": 5}

        def _boom(slot, rs, re_):
            raise RuntimeError("draw failed")

        called_fallback: list = []
        r.repaint_visible_slot_glyphs(
            draw_slice=_boom,
            render_fallback=lambda: called_fallback.append(True),
        )
        # All slots failed ⇒ fallback fires.
        assert called_fallback == [True]


# ---------------------------------------------------------------------------
# 7. autoscale_slot_y
# ---------------------------------------------------------------------------


class TestAutoscaleSlotY:
    def test_missing_slot_is_noop(self):
        r = ChartRenderer()
        # Doesn't raise.
        r.autoscale_slot_y(
            "primary", 0, 10, series_getter=lambda c: None, log_price_on=False,
        )

    def test_empty_candles_is_noop(self):
        r = ChartRenderer()
        fig = plt.figure()
        price_ax = fig.add_subplot(211)
        vol_ax = fig.add_subplot(212)
        r.panel_state["primary"] = {
            "candles": [], "price_ax": price_ax, "vol_ax": vol_ax,
        }
        r.autoscale_slot_y(
            "primary", 0, 0, series_getter=lambda c: None, log_price_on=False,
        )
        plt.close(fig)

    def test_fits_ylim_when_candles_present(self):
        r = ChartRenderer()
        fig = plt.figure()
        price_ax = fig.add_subplot(211)
        vol_ax = fig.add_subplot(212)
        candles = _candles(30)
        r.panel_state["primary"] = {
            "candles": candles, "price_ax": price_ax, "vol_ax": vol_ax,
        }
        # Series getter returns a SeriesArrays-like with .high/.low/.volume.
        from types import SimpleNamespace

        import numpy as np

        def _getter(c):
            return SimpleNamespace(
                high=np.array([cd.high for cd in c]),
                low=np.array([cd.low for cd in c]),
                close=np.array([cd.close for cd in c]),
                open=np.array([cd.open for cd in c]),
                volume=np.array([cd.volume for cd in c], dtype=float),
            )

        r.autoscale_slot_y(
            "primary", 0, 10, series_getter=_getter, log_price_on=False,
        )
        # ylim was set; sanity-check shape.
        lo, hi = price_ax.get_ylim()
        assert hi > lo
        plt.close(fig)


# ---------------------------------------------------------------------------
# 8. refresh_view_after_tick
# ---------------------------------------------------------------------------


class TestRefreshViewAfterTick:
    def test_missing_slot_is_noop(self):
        r = ChartRenderer()
        # All callbacks must not be invoked.
        called: list[str] = []
        r.refresh_view_after_tick(
            "primary",
            apply_tick_to_artists=lambda s: called.append("apply") or True,
            draw_slice=lambda *a: called.append("draw"),
            autoscale_slot_y=lambda *a: called.append("autoscale"),
            autoscale_indicator_panes=lambda s: called.append("ind"),
            canvas_draw_idle=lambda: called.append("draw_idle"),
        )
        assert called == []

    def test_normal_tick_calls_apply_and_autoscale(self):
        r = ChartRenderer()
        fig = plt.figure()
        ax = fig.add_subplot()
        ax.set_xlim(0, 9)
        candles = _candles(10)
        r.panel_state["primary"] = {
            "candles": candles, "price_ax": ax,
            "render_start": 0, "render_end": 10,
        }
        called: list[str] = []
        r.refresh_view_after_tick(
            "primary",
            apply_tick_to_artists=lambda s: called.append("apply") or True,
            draw_slice=lambda *a: called.append("draw"),
            autoscale_slot_y=lambda *a: called.append("autoscale"),
            autoscale_indicator_panes=lambda s: called.append("ind"),
            canvas_draw_idle=lambda: called.append("draw_idle"),
        )
        # apply ran successfully ⇒ no draw_slice fallback.
        assert "apply" in called
        assert "draw" not in called
        assert "autoscale" in called
        assert "ind" in called
        assert "draw_idle" in called
        plt.close(fig)

    def test_apply_false_falls_back_to_draw_slice(self):
        r = ChartRenderer()
        fig = plt.figure()
        ax = fig.add_subplot()
        ax.set_xlim(0, 9)
        candles = _candles(10)
        r.panel_state["primary"] = {
            "candles": candles, "price_ax": ax,
            "render_start": 0, "render_end": 10,
        }
        called: list[str] = []
        r.refresh_view_after_tick(
            "primary",
            apply_tick_to_artists=lambda s: False,  # apply rejected
            draw_slice=lambda *a: called.append("draw"),
            autoscale_slot_y=lambda *a: called.append("autoscale"),
            autoscale_indicator_panes=lambda s: called.append("ind"),
            canvas_draw_idle=lambda: called.append("draw_idle"),
        )
        assert "draw" in called
        plt.close(fig)

    def test_render_end_mismatch_takes_autoscale_only_branch(self):
        r = ChartRenderer()
        fig = plt.figure()
        ax = fig.add_subplot()
        ax.set_xlim(0, 9)
        candles = _candles(15)  # n=15 but render_end=10 ⇒ mismatch
        r.panel_state["primary"] = {
            "candles": candles, "price_ax": ax,
            "render_start": 0, "render_end": 10,
        }
        called: list[str] = []
        r.refresh_view_after_tick(
            "primary",
            apply_tick_to_artists=lambda s: called.append("apply") or True,
            draw_slice=lambda *a: called.append("draw"),
            autoscale_slot_y=lambda *a: called.append("autoscale"),
            autoscale_indicator_panes=lambda s: called.append("ind"),
            canvas_draw_idle=lambda: called.append("draw_idle"),
        )
        # mismatch branch: autoscale only.
        assert "apply" not in called
        assert "draw" not in called
        assert "autoscale" in called
        plt.close(fig)


# ---------------------------------------------------------------------------
# 9. refresh_view_after_append
# ---------------------------------------------------------------------------


class TestRefreshViewAfterAppend:
    def test_missing_slot_is_noop(self):
        r = ChartRenderer()
        r.refresh_view_after_append(
            "primary",
            ensure_rendered_for_view=lambda s: None,
            autoscale_slot_y=lambda *a: None,
            autoscale_indicator_panes=lambda s: None,
            canvas_draw_idle=lambda: None,
        )

    def test_empty_candles_is_noop(self):
        r = ChartRenderer()
        fig = plt.figure()
        ax = fig.add_subplot()
        r.panel_state["primary"] = {"candles": [], "price_ax": ax}
        called: list[str] = []
        r.refresh_view_after_append(
            "primary",
            ensure_rendered_for_view=lambda s: called.append("ensure"),
            autoscale_slot_y=lambda *a: called.append("autoscale"),
            autoscale_indicator_panes=lambda s: called.append("ind"),
            canvas_draw_idle=lambda: called.append("draw"),
        )
        assert called == []
        plt.close(fig)

    def test_sandbox_xlim_applied_when_provided(self):
        r = ChartRenderer()
        fig = plt.figure()
        ax = fig.add_subplot()
        candles = _candles(20)
        r.panel_state["primary"] = {"candles": candles, "price_ax": ax}
        r.refresh_view_after_append(
            "primary",
            ensure_rendered_for_view=lambda s: None,
            autoscale_slot_y=lambda *a: None,
            autoscale_indicator_panes=lambda s: None,
            canvas_draw_idle=lambda: None,
            sandbox_full_session_xlim=(-0.5, 99.5),
        )
        lo, hi = ax.get_xlim()
        assert lo == pytest.approx(-0.5)
        assert hi == pytest.approx(99.5)
        plt.close(fig)

    def test_glued_right_edge_slides_xlim(self):
        r = ChartRenderer()
        fig = plt.figure()
        ax = fig.add_subplot()
        # n=20, prev xlim ends near n-2=18 so it's "glued".
        ax.set_xlim(10.0, 18.5)
        candles = _candles(20)
        r.panel_state["primary"] = {"candles": candles, "price_ax": ax}
        called: list[str] = []
        r.refresh_view_after_append(
            "primary",
            ensure_rendered_for_view=lambda s: called.append("ensure"),
            autoscale_slot_y=lambda *a: called.append("autoscale"),
            autoscale_indicator_panes=lambda s: called.append("ind"),
            canvas_draw_idle=lambda: called.append("draw"),
        )
        lo, hi = ax.get_xlim()
        # New hi = (n-1) + 0.5 = 19.5; width = 8.5; new lo = 11.
        assert hi == pytest.approx(19.5)
        assert lo == pytest.approx(11.0)
        # All callbacks fired.
        assert "ensure" in called
        assert "autoscale" in called
        assert "draw" in called
        plt.close(fig)

    def test_not_glued_preserves_xlim(self):
        r = ChartRenderer()
        fig = plt.figure()
        ax = fig.add_subplot()
        # n=50, prev xlim is at the far left (not glued).
        ax.set_xlim(0.0, 10.0)
        candles = _candles(50)
        r.panel_state["primary"] = {"candles": candles, "price_ax": ax}
        r.refresh_view_after_append(
            "primary",
            ensure_rendered_for_view=lambda s: None,
            autoscale_slot_y=lambda *a: None,
            autoscale_indicator_panes=lambda s: None,
            canvas_draw_idle=lambda: None,
        )
        lo, hi = ax.get_xlim()
        assert (lo, hi) == (0.0, 10.0)
        plt.close(fig)


# ---------------------------------------------------------------------------
# 10. ensure_rendered_for_view (refill guard)
# ---------------------------------------------------------------------------


class TestEnsureRenderedForView:
    def test_missing_slot_is_noop(self):
        r = ChartRenderer()
        r.ensure_rendered_for_view(
            "primary",
            draw_slice=lambda *a: pytest.fail("draw should NOT fire"),
            min_render_candles=10,
            max_render_candles=200,
            render_buffer_multiplier=2,
        )

    def test_empty_candles_is_noop(self):
        r = ChartRenderer()
        fig = plt.figure()
        ax = fig.add_subplot()
        r.panel_state["primary"] = {
            "candles": [], "price_ax": ax,
            "render_start": 0, "render_end": 0, "offset": 0,
        }
        called: list = []
        r.ensure_rendered_for_view(
            "primary",
            draw_slice=lambda *a: called.append(a),
            min_render_candles=10, max_render_candles=200,
            render_buffer_multiplier=2,
        )
        assert called == []
        plt.close(fig)

    def test_full_render_returns_early(self):
        r = ChartRenderer()
        fig = plt.figure()
        ax = fig.add_subplot()
        ax.set_xlim(0, 49)
        candles = _candles(50)
        r.panel_state["primary"] = {
            "candles": candles, "price_ax": ax,
            "render_start": 0, "render_end": 50, "offset": 0,
        }
        called: list = []
        r.ensure_rendered_for_view(
            "primary",
            draw_slice=lambda *a: called.append(a),
            min_render_candles=10, max_render_candles=200,
            render_buffer_multiplier=2,
        )
        assert called == []
        plt.close(fig)

    def test_xlim_in_safe_zone_returns_early(self):
        r = ChartRenderer()
        fig = plt.figure()
        ax = fig.add_subplot()
        # candles=200, rendered slice = [50, 150], view well inside.
        ax.set_xlim(80.0, 120.0)
        candles = _candles(200)
        r.panel_state["primary"] = {
            "candles": candles, "price_ax": ax,
            "render_start": 50, "render_end": 150, "offset": 0,
        }
        called: list = []
        r.ensure_rendered_for_view(
            "primary",
            draw_slice=lambda *a: called.append(a),
            min_render_candles=10, max_render_candles=400,
            render_buffer_multiplier=2,
        )
        assert called == []
        plt.close(fig)

    def test_xlim_crosses_left_edge_triggers_redraw(self):
        r = ChartRenderer()
        fig = plt.figure()
        ax = fig.add_subplot()
        # candles=200, rendered [50, 150]. View slides left to [20, 90] —
        # buffer = max(1, 100/4)=25. safe_left would need lo>=75, but 20<75.
        ax.set_xlim(20.0, 90.0)
        candles = _candles(200)
        r.panel_state["primary"] = {
            "candles": candles, "price_ax": ax,
            "render_start": 50, "render_end": 150, "offset": 0,
        }
        called: list = []
        r.ensure_rendered_for_view(
            "primary",
            draw_slice=lambda *a: called.append(a),
            min_render_candles=10, max_render_candles=400,
            render_buffer_multiplier=2,
        )
        assert called and called[0][0] == "primary"
        plt.close(fig)

    def test_inverted_xlim_is_noop(self):
        r = ChartRenderer()
        fig = plt.figure()
        ax = fig.add_subplot()
        ax.set_xlim(50.0, 10.0)  # inverted — hi <= lo after clamping.
        candles = _candles(60)
        r.panel_state["primary"] = {
            "candles": candles, "price_ax": ax,
            "render_start": 10, "render_end": 40, "offset": 0,
        }
        called: list = []
        r.ensure_rendered_for_view(
            "primary",
            draw_slice=lambda *a: called.append(a),
            min_render_candles=5, max_render_candles=400,
            render_buffer_multiplier=2,
        )
        assert called == []
        plt.close(fig)


# ---------------------------------------------------------------------------
# 11. apply_tick_to_artists
# ---------------------------------------------------------------------------


def _build_tick_panel_state(n: int = 10):
    """Construct a renderer + panel_state with REAL artists for tick tests.

    Uses the production ``rendering`` helpers to build collections shaped
    exactly like the renderer expects, so ``apply_tick_to_artists`` can
    mutate them in place without tripping the early-out guards.
    """
    from matplotlib.collections import LineCollection, PolyCollection
    fig = plt.figure()
    price_ax = fig.add_subplot(211)
    vol_ax = fig.add_subplot(212)
    candles = _candles(n)
    body_half = 0.4
    from tradinglab.rendering import bar_geometry, vol_geometry

    wick_segs = []
    body_verts_all = []
    body_colors = []
    vol_verts_all = []
    vol_colors = []
    for i, c in enumerate(candles):
        wseg, bverts, bcolor = bar_geometry(c, i, body_half=body_half)
        vverts, vcolor = vol_geometry(c, i, body_half=body_half)
        wick_segs.append(wseg)
        body_verts_all.append(bverts)
        body_colors.append(bcolor)
        vol_verts_all.append(vverts)
        vol_colors.append(vcolor)

    wicks = LineCollection(wick_segs, colors=body_colors)
    price_ax.add_collection(wicks)
    wicks._sc_segments = wick_segs
    wicks._sc_colors = list(body_colors)
    wicks._sc_src_indices = list(range(n))

    bodies = PolyCollection(body_verts_all, facecolors=body_colors,
                            edgecolors=body_colors)
    price_ax.add_collection(bodies)
    bodies._sc_verts = body_verts_all
    bodies._sc_colors = list(body_colors)
    bodies._sc_src_indices = list(range(n))

    vol_bars = PolyCollection(vol_verts_all, facecolors=vol_colors,
                              edgecolors=vol_colors)
    vol_ax.add_collection(vol_bars)
    vol_bars._sc_verts = vol_verts_all
    vol_bars._sc_colors = list(vol_colors)
    vol_bars._sc_src_indices = list(range(n))

    r = ChartRenderer()
    r.panel_state["primary"] = {
        "candles": candles, "price_ax": price_ax, "vol_ax": vol_ax,
        "render_start": 0, "render_end": n, "offset": 0,
        "price_wicks": wicks, "price_bodies": bodies, "vol_bars": vol_bars,
        "body_half": body_half,
    }
    return r, fig


class TestApplyTickToArtists:
    def test_missing_slot_returns_false(self):
        r = ChartRenderer()
        assert r.apply_tick_to_artists(
            "primary", ha_on=False, highlight_key_bars_on=False,
            render_indicators=lambda s: None,
        ) is False

    def test_ha_on_returns_false(self):
        r, fig = _build_tick_panel_state()
        assert r.apply_tick_to_artists(
            "primary", ha_on=True, highlight_key_bars_on=False,
            render_indicators=lambda s: None,
        ) is False
        plt.close(fig)

    def test_key_bars_on_returns_false(self):
        r, fig = _build_tick_panel_state()
        assert r.apply_tick_to_artists(
            "primary", ha_on=False, highlight_key_bars_on=True,
            render_indicators=lambda s: None,
        ) is False
        plt.close(fig)

    def test_empty_candles_returns_false(self):
        r = ChartRenderer()
        r.panel_state["primary"] = {
            "candles": [], "render_end": 0,
            "price_wicks": object(), "price_bodies": object(),
            "vol_bars": object(),
        }
        assert r.apply_tick_to_artists(
            "primary", ha_on=False, highlight_key_bars_on=False,
            render_indicators=lambda s: None,
        ) is False

    def test_render_end_mismatch_returns_false(self):
        r, fig = _build_tick_panel_state(n=10)
        r.panel_state["primary"]["render_end"] = 9  # mismatch with n=10.
        assert r.apply_tick_to_artists(
            "primary", ha_on=False, highlight_key_bars_on=False,
            render_indicators=lambda s: None,
        ) is False
        plt.close(fig)

    def test_gap_last_candle_returns_false(self):
        r, fig = _build_tick_panel_state(n=10)
        cs = list(r.panel_state["primary"]["candles"])
        cs[-1] = _candle(99, gap=True)
        r.panel_state["primary"]["candles"] = cs
        assert r.apply_tick_to_artists(
            "primary", ha_on=False, highlight_key_bars_on=False,
            render_indicators=lambda s: None,
        ) is False
        plt.close(fig)

    def test_missing_wicks_returns_false(self):
        r, fig = _build_tick_panel_state(n=10)
        r.panel_state["primary"]["price_wicks"] = None
        assert r.apply_tick_to_artists(
            "primary", ha_on=False, highlight_key_bars_on=False,
            render_indicators=lambda s: None,
        ) is False
        plt.close(fig)

    def test_src_indices_mismatch_returns_false(self):
        r, fig = _build_tick_panel_state(n=10)
        # Last src index doesn't match n-1.
        r.panel_state["primary"]["price_wicks"]._sc_src_indices[-1] = 99
        assert r.apply_tick_to_artists(
            "primary", ha_on=False, highlight_key_bars_on=False,
            render_indicators=lambda s: None,
        ) is False
        plt.close(fig)

    def test_happy_path_mutates_and_returns_true(self):
        r, fig = _build_tick_panel_state(n=10)
        cs = r.panel_state["primary"]["candles"]
        # Bump the last candle's price/volume.
        last = cs[-1]
        new_last = Candle(
            date=last.date, open=last.open, high=last.high + 5,
            low=last.low, close=last.close + 4, volume=last.volume * 2,
            session="regular",
        )
        cs[-1] = new_last
        called: list[str] = []
        ok = r.apply_tick_to_artists(
            "primary", ha_on=False, highlight_key_bars_on=False,
            render_indicators=lambda s: called.append(s),
        )
        assert ok is True
        assert called == ["primary"]
        plt.close(fig)

    def test_render_indicators_exception_still_returns_true(self):
        r, fig = _build_tick_panel_state(n=10)
        def _boom(slot):
            raise RuntimeError("render fail")
        ok = r.apply_tick_to_artists(
            "primary", ha_on=False, highlight_key_bars_on=False,
            render_indicators=_boom,
        )
        assert ok is True
        plt.close(fig)


# ---------------------------------------------------------------------------
# 12. render_event_glyphs_for_slot — guards
# ---------------------------------------------------------------------------


class TestRenderEventGlyphsForSlot:
    def test_missing_slot_is_noop(self):
        r = ChartRenderer()
        r.render_event_glyphs_for_slot(
            "primary",
            get_events_view=lambda s: pytest.fail("should NOT fire"),
            theme={}, sandbox_blind=False,
        )

    def test_no_price_ax_is_noop(self):
        r = ChartRenderer()
        r.panel_state["primary"] = {"candles": _candles(5), "price_ax": None}
        r.render_event_glyphs_for_slot(
            "primary",
            get_events_view=lambda s: pytest.fail("should NOT fire"),
            theme={}, sandbox_blind=False,
        )

    def test_empty_candles_is_noop(self):
        r = ChartRenderer()
        fig = plt.figure()
        ax = fig.add_subplot()
        r.panel_state["primary"] = {"candles": [], "price_ax": ax}
        r.render_event_glyphs_for_slot(
            "primary",
            get_events_view=lambda s: pytest.fail("should NOT fire"),
            theme={}, sandbox_blind=False,
        )
        plt.close(fig)

    def test_no_view_returns_early(self):
        r = ChartRenderer()
        fig = plt.figure()
        ax = fig.add_subplot()
        r.panel_state["primary"] = {
            "candles": _candles(10), "price_ax": ax, "offset": 0,
        }
        # get_events_view returns None ⇒ early return before any glyph work.
        r.render_event_glyphs_for_slot(
            "primary",
            get_events_view=lambda s: None,
            theme={}, sandbox_blind=False,
        )
        plt.close(fig)
