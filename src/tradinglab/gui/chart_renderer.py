"""Rendering helpers extracted from :mod:`tradinglab.app`."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from typing import Any

import numpy as np
from matplotlib.colors import to_rgba

from .. import constants as _constants
from ..core.viewport import compute_render_range as _compute_render_range
from ..core.viewport import y_limits_for_slice as _y_limits_for_slice
from ..indicators import render as _ind_render
from ..models import Candle
from ..rendering import (
    bar_geometry,
    vol_geometry,
)
from ..rendering import (
    safe_remove as _safe_remove,
)


class ChartRenderer:
    """Own panel render state and reusable rendering helpers."""

    def __init__(self) -> None:
        self.panel_state: dict[str, dict[str, Any]] = {}
        self.ax_candle_map: OrderedDict[Any, tuple[list[Candle], str, int]] = OrderedDict()
        self.blit_bg = None
        # Data-less background snapshot for the live-tick blit fast path
        # (gui/interaction.py:_paint_tick_frame). Captured with the candle/
        # volume/indicator/live-price artists hidden so they can be redrawn
        # ghost-free on top each tick. Invalidated whenever ``blit_bg`` is.
        self.tick_blit_bg = None

    def reset_slot_artists(self, slot: str) -> None:
        """Remove the candle/volume/shading artists held by ``panel_state[slot]``."""
        ps = self.panel_state.get(slot)
        if not ps:
            return
        bodies = ps.get("price_bodies")
        if bodies is not None:
            for art in list(getattr(bodies, "_sc_flat_hatch_collections", []) or []):
                _safe_remove(art)
        for key in ("price_wicks", "price_bodies", "vol_bars"):
            _safe_remove(ps.get(key))
            ps[key] = None
        for art in list(ps.get("price_shades", [])):
            _safe_remove(art)
        ps["price_shades"] = []
        for art in list(ps.get("vol_shades", [])):
            _safe_remove(art)
        ps["vol_shades"] = []
        try:
            from .events_overlay import clear_event_glyph_artists

            clear_event_glyph_artists(list(ps.get("event_artists", []) or []))
        except Exception:  # noqa: BLE001
            pass
        ps["event_artists"] = []
        ps["event_hit_meta"] = []
        ps["event_badge_tooltip"] = ""
        try:
            from .volume_tod_overlay import clear_volume_tod_artists

            clear_volume_tod_artists(list(ps.get("vol_tod_artists", []) or []))
        except Exception:  # noqa: BLE001
            pass
        ps["vol_tod_artists"] = []
        ps["vol_tod_patches"] = []
        ind_state = ps.get("ind_state")
        if ind_state is not None:
            try:
                ind_state.clear()
            except Exception:  # noqa: BLE001
                pass

    def display_candles_for(self, candles, *, ha_on: bool):
        """Return the candle list to use for glyph drawing in this slot."""
        if not ha_on or not candles:
            return candles
        try:
            from ..core.heikin_ashi import heikin_ashi_candles

            return heikin_ashi_candles(candles)
        except Exception:  # noqa: BLE001
            return candles

    def key_bar_hollow_indices_for(
        self,
        candles: list[Candle],
        *,
        highlight_key_bars_on: bool,
    ) -> set | None:
        """Return the set of global candle indices that qualify as key bars."""
        if not highlight_key_bars_on or not candles:
            return None
        try:
            from ..core.key_bar import KEY_BAR_BEAR, KEY_BAR_BULL, compute_key_bar_arrays

            res = compute_key_bar_arrays(candles)
            mask = (res.signed == KEY_BAR_BULL) | (res.signed == KEY_BAR_BEAR)
            return set(int(i) for i in mask.nonzero()[0])
        except Exception:  # noqa: BLE001
            return None

    def ha_flat_overlay_for(
        self,
        candles: list[Candle],
        *,
        highlight_ha_flat_on: bool,
        ha_on: bool,
        dark_mode: bool,
    ) -> dict[str, object] | None:
        """Return a hatched-overlay descriptor for HA flat-top/-bottom bars."""
        if not (highlight_ha_flat_on and ha_on) or not candles:
            return None
        try:
            from ..core.ha_flat import compute_ha_flat_arrays
            from ..rendering import brighter_shade, darker_shade

            res = compute_ha_flat_arrays(candles)
            bull_any = bool(res.bull_flat_bottom.any())
            bear_any = bool(res.bear_flat_top.any())
            if not (bull_any or bear_any):
                return None
            if dark_mode:
                bull_hatch_color = brighter_shade(to_rgba(_constants.BULL_COLOR), dark_mode=True)
                bear_hatch_color = brighter_shade(to_rgba(_constants.BEAR_COLOR), dark_mode=True)
            else:
                bull_hatch_color = darker_shade(to_rgba(_constants.BULL_COLOR), dark_mode=False)
                bear_hatch_color = darker_shade(to_rgba(_constants.BEAR_COLOR), dark_mode=False)
            return {
                "bull_indices": frozenset(int(i) for i in res.bull_flat_bottom.nonzero()[0]),
                "bear_indices": frozenset(int(i) for i in res.bear_flat_top.nonzero()[0]),
                "bull_color": bull_hatch_color,
                "bear_color": bear_hatch_color,
                "bull_hatch": "xxx",
                "bear_hatch": "xxx",
            }
        except Exception:  # noqa: BLE001
            return None

    def repaint_visible_slot_glyphs(
        self,
        *,
        draw_slice: Callable[[str, int, int], None],
        render_fallback: Callable[[], None],
    ) -> None:
        """Repaint candle/volume glyphs in each live slot without a topology rebuild."""
        if not self.panel_state:
            render_fallback()
            return
        repainted_any = False
        for slot, ps in list(self.panel_state.items()):
            try:
                rs = int(ps.get("render_start", 0))
                re_ = int(ps.get("render_end", 0))
            except Exception:  # noqa: BLE001
                continue
            if re_ <= rs:
                continue
            try:
                draw_slice(slot, rs, re_)
                repainted_any = True
            except Exception:  # noqa: BLE001
                continue
        if not repainted_any:
            try:
                render_fallback()
            except Exception:  # noqa: BLE001
                pass

    def autoscale_slot_y(
        self,
        slot: str,
        lo: int,
        hi: int,
        *,
        series_getter: Callable[[list[Candle]], Any],
        log_price_on: bool,
    ) -> None:
        """Fit price + volume Y to ``candles[lo:hi]`` for ``slot``."""
        ps = self.panel_state.get(slot)
        if not ps:
            return
        candles = ps["candles"]
        if not candles:
            return
        sa = series_getter(candles)
        try:
            price_ax = ps["price_ax"]
            use_log = price_ax.get_yscale() == "log" or bool(log_price_on)
            ylim = _y_limits_for_slice(sa, "price", lo, hi, log=use_log)
            if ylim is not None:
                price_ax.set_ylim(*ylim)
            vlim = _y_limits_for_slice(sa, "volume", lo, hi)
            if vlim is not None:
                ps["vol_ax"].set_ylim(*vlim)
        except Exception:  # noqa: BLE001
            pass

    def ensure_rendered_for_view(
        self,
        slot: str,
        *,
        draw_slice: Callable[[str, int, int], None],
        min_render_candles: int,
        max_render_candles: int,
        render_buffer_multiplier: int,
    ) -> None:
        """Refill ``slot``'s slice if the visible X range exits the safe zone."""
        ps = self.panel_state.get(slot)
        if not ps:
            return
        candles = ps["candles"]
        n = len(candles)
        if n == 0:
            return
        rs, re_ = int(ps.get("render_start", 0)), int(ps.get("render_end", 0))
        if rs == 0 and re_ == n:
            return
        try:
            lo_f, hi_f = ps["price_ax"].get_xlim()
        except Exception:  # noqa: BLE001
            return
        offset = ps.get("offset", 0)
        lo = max(0, int(np.floor(lo_f - offset)))
        hi = min(n, int(np.ceil(hi_f - offset)))
        if hi <= lo:
            return
        buffer = max(1, (re_ - rs) // (render_buffer_multiplier * 2))
        at_left_edge = rs == 0
        at_right_edge = re_ == n
        safe_left = at_left_edge or lo >= rs + buffer
        safe_right = at_right_edge or hi <= re_ - buffer
        if safe_left and safe_right:
            return
        new_start, new_end = _compute_render_range(
            lo, hi, n, min_render_candles, max_render_candles,
        )
        if new_start == rs and new_end == re_:
            return
        draw_slice(slot, new_start, new_end)

    def apply_tick_to_artists(
        self,
        slot: str,
        *,
        ha_on: bool,
        highlight_key_bars_on: bool,
        render_indicators: Callable[[str], None],
    ) -> bool:
        """Mutate the rightmost candle/volume artist in place for a streaming tick."""
        ps = self.panel_state.get(slot)
        if not ps:
            return False
        if ha_on or highlight_key_bars_on:
            return False
        candles = ps.get("candles") or []
        n = len(candles)
        if n == 0:
            return False
        if int(ps.get("render_end", 0)) != n:
            return False
        last = candles[n - 1]
        if last.is_gap:
            return False
        wicks = ps.get("price_wicks")
        bodies = ps.get("price_bodies")
        vol_bars = ps.get("vol_bars")
        if wicks is None or bodies is None or vol_bars is None:
            return False
        for art in (wicks, bodies, vol_bars):
            src = getattr(art, "_sc_src_indices", None)
            if not src or src[-1] != n - 1:
                return False
        try:
            offset = int(ps.get("offset", 0))
            x = (n - 1) + offset
            body_half = ps.get("body_half")
            wick_seg, body_verts, body_color = bar_geometry(last, x, body_half=body_half)
            vol_verts, vol_color = vol_geometry(last, x, body_half=body_half)
            wicks._sc_segments[-1] = wick_seg
            wicks._sc_colors[-1] = body_color
            bodies._sc_verts[-1] = body_verts
            vol_bars._sc_verts[-1] = vol_verts
            vol_bars._sc_colors[-1] = vol_color
            wicks.set_segments(wicks._sc_segments)
            wicks.set_color(wicks._sc_colors)
            bodies.set_verts(bodies._sc_verts)
            bodies.set_facecolors(bodies._sc_colors)
            bodies.set_edgecolors(bodies._sc_colors)
            vol_bars.set_verts(vol_bars._sc_verts)
            vol_bars.set_facecolors(vol_bars._sc_colors)
            vol_bars.set_edgecolors(vol_bars._sc_colors)
        except Exception:  # noqa: BLE001
            return False
        try:
            render_indicators(slot)
        except Exception:  # noqa: BLE001
            pass
        return True

    def refresh_view_after_tick(
        self,
        slot: str = "primary",
        *,
        apply_tick_to_artists: Callable[[str], bool],
        draw_slice: Callable[[str, int, int], None],
        autoscale_slot_y: Callable[[str, int, int], None],
        autoscale_indicator_panes: Callable[[str], None],
        canvas_draw_idle: Callable[[], None],
        blit_tick_frame: Callable[[str], bool] | None = None,
    ) -> None:
        """Mutate in-place for a tick or fall back to a slice rebuild.

        When the in-place mutation succeeds AND the autoscale did not move
        any axis limit (the common case: the forming bar ticks within the
        current view), repaint via ``blit_tick_frame`` — a ~5x cheaper
        blit of the data artists onto a cached data-less background —
        instead of a full ``canvas.draw_idle()``. Any limit change, an
        HA/highlight rebuild, or a blit failure falls back to the full
        redraw (which also invalidates the blit background via the
        draw_event handler).
        """
        ps = self.panel_state.get(slot)
        if not ps:
            return
        rs = int(ps.get("render_start", 0))
        re_ = int(ps.get("render_end", 0))
        n = len(ps.get("candles") or [])
        if re_ != n:
            try:
                lo_f, hi_f = ps["price_ax"].get_xlim()
                lo = max(0, int(np.floor(lo_f)))
                hi = min(n, int(np.ceil(hi_f)))
                autoscale_slot_y(slot, lo, hi)
                autoscale_indicator_panes(slot)
                canvas_draw_idle()
            except Exception:  # noqa: BLE001
                pass
            return
        lims_before = self._snapshot_slot_limits(ps)
        tick_ok = apply_tick_to_artists(slot)
        if not tick_ok:
            draw_slice(slot, rs, re_)
        try:
            lo_f, hi_f = ps["price_ax"].get_xlim()
            lo = max(0, int(np.floor(lo_f)))
            hi = min(len(ps["candles"]), int(np.ceil(hi_f)))
            autoscale_slot_y(slot, lo, hi)
            autoscale_indicator_panes(slot)
            lims_after = self._snapshot_slot_limits(ps)
            if (
                tick_ok
                and blit_tick_frame is not None
                and lims_after == lims_before
                and blit_tick_frame(slot)
            ):
                return
            canvas_draw_idle()
        except Exception:  # noqa: BLE001
            pass

    def _snapshot_slot_limits(self, ps: dict[str, Any]) -> tuple:
        """Hashable ``(xlim, ylim)`` snapshot of a slot's axes.

        Equal between two ticks iff no axis (price / volume / indicator
        panes) moved — the precondition for a ghost-free tick blit against
        a cached decorations-only background.
        """
        axes = []
        for key in ("price_ax", "volume_ax"):
            ax = ps.get(key)
            if ax is not None:
                axes.append(ax)
        for ax in ps.get("indicator_axes", []) or []:
            if ax is not None:
                axes.append(ax)
        snap = []
        for ax in axes:
            try:
                snap.append((tuple(ax.get_xlim()), tuple(ax.get_ylim())))
            except Exception:  # noqa: BLE001
                snap.append(None)
        return tuple(snap)


    def refresh_view_after_append(
        self,
        slot: str = "primary",
        *,
        ensure_rendered_for_view: Callable[[str], None],
        autoscale_slot_y: Callable[[str, int, int], None],
        autoscale_indicator_panes: Callable[[str], None],
        canvas_draw_idle: Callable[[], None],
        sandbox_full_session_xlim: tuple[float, float] | None = None,
    ) -> None:
        """After a rollover appended a bar, preserve the viewport and refit Y."""
        ps = self.panel_state.get(slot)
        if not ps:
            return
        candles = ps["candles"]
        n = len(candles)
        if n == 0:
            return
        ax_p = ps["price_ax"]
        if sandbox_full_session_xlim is not None:
            try:
                ax_p.set_xlim(*sandbox_full_session_xlim)
            except Exception:  # noqa: BLE001
                pass
        else:
            try:
                lo_f, hi_f = ax_p.get_xlim()
            except Exception:  # noqa: BLE001
                return
            glued = (n - 2) >= lo_f and (n - 2) <= hi_f + 0.6
            if glued:
                width = hi_f - lo_f
                new_hi = (n - 1) + 0.5
                new_lo = new_hi - width
                try:
                    ax_p.set_xlim(new_lo, new_hi)
                except Exception:  # noqa: BLE001
                    pass
        ensure_rendered_for_view(slot)
        try:
            lo_f2, hi_f2 = ax_p.get_xlim()
            lo = max(0, int(np.floor(lo_f2)))
            hi = min(n, int(np.ceil(hi_f2)))
            autoscale_slot_y(slot, lo, hi)
            autoscale_indicator_panes(slot)
            canvas_draw_idle()
        except Exception:  # noqa: BLE001
            pass

    def render_indicators_for_slot(
        self,
        slot: str,
        *,
        interval: str,
        source: str,
        slot_symbol: str,
        indicator_manager: Any,
        indicator_cache: Any,
        warn: Callable[[str], None] | None = None,
    ) -> None:
        """Compute + draw every applicable indicator into one slot."""
        ps = self.panel_state.get(slot)
        if not ps:
            return
        candles = ps.get("candles") or []
        if not candles:
            return
        ax_p = ps.get("price_ax")
        if ax_p is None:
            return
        ind_axes = list(ps.get("ind_axes") or [])
        scope = str(ps.get("ind_scope") or "main")
        state = ps.get("ind_state")
        if state is None:
            state = _ind_render.PanelIndicatorState()
            ps["ind_state"] = state
        try:
            from ..core.render_context import render_context as _rctx

            with _rctx(
                interval=interval,
                source=source,
                primary_symbol=slot_symbol,
            ):
                _ind_render.render_for_slot(
                    price_ax=ax_p,
                    pane_axes=ind_axes,
                    candles=candles,
                    offset=int(ps.get("offset", 0)),
                    manager=indicator_manager,
                    cache=indicator_cache,
                    interval=interval,
                    scope=scope,
                    state=state,
                )
        except Exception as e:  # noqa: BLE001
            if warn is not None:
                try:
                    warn(f"Indicator render error in {slot}: {e}")
                except Exception:  # noqa: BLE001
                    pass
        self.autoscale_indicator_panes_for_slot(slot)

    def autoscale_indicator_panes_for_slot(self, slot: str) -> None:
        """Re-fit each indicator pane's Y axis to its visible window."""
        ps = self.panel_state.get(slot)
        if not ps:
            return
        state = ps.get("ind_state")
        if state is None:
            return
        ax_p = ps.get("price_ax")
        if ax_p is None:
            return
        candles = ps.get("candles") or []
        try:
            n = len(candles)
            lo_f, hi_f = ax_p.get_xlim()
            offset = int(ps.get("offset", 0))
            lo = max(0, int(np.floor(lo_f - offset)))
            hi = min(n, int(np.ceil(hi_f - offset)))
        except Exception:  # noqa: BLE001
            return
        for cfg_id, lower_ax in getattr(state, "panes", {}).items():
            lines = getattr(state, "pane_lines", {}).get(cfg_id, {}).values()
            try:
                _ind_render.autoscale_pane_y(lower_ax, lines, lo, hi)
            except Exception:  # noqa: BLE001
                pass

    def render_event_glyphs_for_slot(
        self,
        slot: str,
        *,
        get_events_view: Callable[[str], Any],
        theme: dict[str, str],
        sandbox_blind: bool,
    ) -> None:
        """Project the slot's gated EventsView into bottom-of-pane glyphs."""
        ps = self.panel_state.get(slot)
        if not ps:
            return
        ax_p = ps.get("price_ax")
        candles = ps.get("candles") or []
        if ax_p is None or not candles:
            return
        view = get_events_view(slot)
        if view is None:
            return
        try:
            from .. import defaults as _defaults_mod
            from ..events.render import build_event_glyphs
            from .events_overlay import draw_event_glyphs
        except ImportError:
            return
        try:
            show_earnings = bool(_defaults_mod.get("show_earnings"))
            show_dividends = bool(_defaults_mod.get("show_dividends"))
            show_upcoming = bool(_defaults_mod.get("show_upcoming_events"))
        except Exception:  # noqa: BLE001
            show_earnings = True
            show_dividends = True
            show_upcoming = True
        try:
            glyphs = build_event_glyphs(view, candles, blind=bool(sandbox_blind))
        except Exception:  # noqa: BLE001
            return
        if not glyphs:
            return
        offset = int(ps.get("offset", 0))
        try:
            payload = draw_event_glyphs(
                ax_p,
                glyphs,
                offset=offset,
                theme=theme,
                show_earnings=show_earnings,
                show_dividends=show_dividends,
                show_upcoming=show_upcoming,
            )
        except Exception:  # noqa: BLE001
            return
        ps["event_artists"] = list(payload.artists)
        ps["event_hit_meta"] = list(payload.hit_meta)
        ps["event_badge_tooltip"] = str(payload.forward_badge_tooltip or "")

    def render_volume_tod_for_slot(
        self,
        slot: str,
        *,
        interval: str,
        get_intraday: Callable[[str], list[Candle]],
        now_ms_for_slot: Callable[[str], int | None],
        is_sandbox_active: Callable[[], bool],
        suppress_volume_fill: Callable[[str, dict[int, bool]], None],
        theme: dict[str, str],
        dark_mode: bool,
    ) -> None:
        """Paint the time-of-day shading overlay on the slot's volume bars."""
        try:
            from .. import defaults as _defaults_mod
        except ImportError:
            return
        try:
            if not bool(_defaults_mod.get("volume_tod_enabled")):
                return
        except Exception:  # noqa: BLE001
            return
        if interval != "1d":
            return
        ps = self.panel_state.get(slot)
        if not ps:
            return
        ax_v = ps.get("vol_ax")
        candles = ps.get("candles") or []
        if ax_v is None or not candles:
            return
        render_start = int(ps.get("render_start", 0) or 0)
        render_end = int(ps.get("render_end", 0) or 0)
        if render_end <= render_start:
            return
        intraday = get_intraday(slot)
        try:
            now_ms = now_ms_for_slot(slot)
        except Exception:  # noqa: BLE001
            return
        if now_ms is None:
            return
        try:
            rth_only = bool(_defaults_mod.get("volume_tod_rth_only"))
        except Exception:  # noqa: BLE001
            rth_only = True
        try:
            median_lookback = int(
                _defaults_mod.get("volume_tod_median_lookback_days") or 20,
            )
        except Exception:  # noqa: BLE001
            median_lookback = 20
        try:
            from .volume_tod_overlay import (
                compute_volume_tod_patches,
                draw_volume_tod_patches,
                patches_should_suppress_default_fill,
            )
        except ImportError:
            return
        try:
            sb_active = bool(is_sandbox_active())
        except Exception:  # noqa: BLE001
            sb_active = False
        try:
            patches = compute_volume_tod_patches(
                candles,
                list(intraday or []),
                now_ms=int(now_ms),
                slice_start=render_start,
                slice_end=render_end,
                rth_only=rth_only,
                median_lookback_days=median_lookback,
                sandbox_active=sb_active,
            )
        except Exception:  # noqa: BLE001
            return
        if not patches:
            return
        try:
            suppress = patches_should_suppress_default_fill(patches)
            suppress_volume_fill(slot, suppress)
        except Exception:  # noqa: BLE001
            pass
        offset = int(ps.get("offset", 0))
        try:
            payload = draw_volume_tod_patches(
                ax_v,
                patches,
                offset=offset,
                theme=theme,
                dark_mode=dark_mode,
                show_median_tick=True,
            )
        except Exception:  # noqa: BLE001
            return
        ps["vol_tod_artists"] = list(payload.artists)
        ps["vol_tod_patches"] = list(payload.patches)

    def suppress_default_volume_fill(
        self,
        slot: str,
        suppress_indices: dict[int, bool],
    ) -> None:
        """Hide the default volume-bar fill for indices the overlay covers."""
        ps = self.panel_state.get(slot)
        if not ps:
            return
        bars = ps.get("vol_bars")
        if bars is None:
            return
        colors = list(getattr(bars, "_sc_colors", []) or [])
        src_indices = list(getattr(bars, "_sc_src_indices", []) or [])
        if not colors or len(colors) != len(src_indices):
            return
        new_colors = []
        changed = False
        transparent = (0.0, 0.0, 0.0, 0.0)
        for color, idx in zip(colors, src_indices, strict=False):
            if suppress_indices.get(int(idx)):
                new_colors.append(transparent)
                changed = True
            else:
                new_colors.append(color)
        if not changed:
            return
        try:
            bars.set_facecolors(new_colors)
            bars.set_edgecolors(new_colors)
        except Exception:  # noqa: BLE001
            pass
