"""Interaction mixin for :class:`tradinglab.app.ChartApp`.

Per the decomposition rubber-duck review this is intentionally ONE mixin:
pan + zoom + hover + crosshair + click-to-type + their overlay artists
are a single subsystem — they share the ``_blit_bg`` background, the
animated artists, the pixel-cache, and the matplotlib event wiring. They
cannot be split without re-coupling via a web of cross-calls.

State (``_pan_state``, ``_zoom_state``, ``_drag_press``, ``_blit_bg``,
``_hover_ann``, ``_crosshair_artists``, ``_typing_target``,
``_typing_buffer``, ``_typing_preview_artists``, ``_last_cursor_px``,
``_last_clicked_slot``, etc.) is initialised in ``ChartApp.__init__``;
the mixin only provides behaviour.

Mixin rules: no ``__init__``, no cooperative ``super()``, no name
collisions. No back-import of ``tradinglab.app``.
"""

from __future__ import annotations

import tkinter as tk
from typing import Any

import numpy as np

from .. import constants as _constants
from ..core.viewport import y_limits_for_slice as _y_limits_for_slice
from ..formatting import fmt_volume

_PAN_REDRAW_INTERVAL_MS = 16
# H2: coalesce hover dispatches to ~60 Hz. mpl motion_notify can fire
# 100+/sec under fast dragging; without throttling, the per-event blit
# (restore_region + draw_artist + canvas.blit) dominates CPU and starves
# stream-tick / pan redraws on slower machines. User-overridable via
# settings.json["hover_throttle_ms"] (see defaults.py).
from .. import defaults as _defaults  # noqa: E402

_HOVER_THROTTLE_MS = _defaults.get("hover_throttle_ms")


class InteractionMixin:
    """Pan, zoom, hover tooltip, crosshair, and click-to-type behaviour."""

    # ---- matplotlib event handlers (spec §6.4, §11, §12) --------------
    #
    # Canonical handlers that do real work are implemented below in the
    # "pan + zoom + hover + crosshair + click-to-type" sections. Event
    # wiring happens in _build_ui so the chart is reactive on first render.

    def _on_button_press(self, event) -> None:
        """Dispatch a canvas button_press to pan/zoom/click-to-type starters."""
        if event.inaxes is None or event.button not in (1, 3):
            return
        # Anchored-VWAP "Pick Anchor" mode: while armed, intercept ALL
        # left-clicks before pan/zoom/dblclick dispatch so a missed
        # candle hit can't start a pan and a dblclick can't drill down.
        # The handler itself decides whether to consume + clear or to
        # stay armed for a retry.
        if (event.button == 1
                and getattr(self, "_anchor_pick_state", None)
                and self._handle_anchor_pick_click(event)):
            self._pan_state = None
            self._drag_press = None
            return
        if (event.button in (1, 3)
                and self._maybe_handle_pane_indicator_label_click(event)):
            self._pan_state = None
            self._drag_press = None
            return
        # In-readout overlay legend rows: B3 (or B1 double-click) on a
        # row opens the per-indicator edit dialog / context menu, mirroring
        # the pane-indicator-label affordance. Gated before pan/zoom so a
        # click on the legend never starts a pan.
        if (event.button in (1, 3)
                and self._maybe_handle_readout_legend_click(event)):
            self._pan_state = None
            self._drag_press = None
            return
        # Drawings: B1 double-click on a horizontal line opens the
        # per-line edit dialog. Gated BEFORE the drilldown check so
        # a line drawn over a candle wins (drill-down only takes
        # over when the dblclick misses every line). Mirror of the
        # legend-row dblclick → per-indicator-popup pattern.
        if (event.button == 1 and getattr(event, "dblclick", False)
                and self._maybe_handle_drawing_dblclick(event)):
            self._pan_state = None
            self._drag_press = None
            return
        # Double-click on a 1d candle drills down into 5m for that day
        # (drill-down feature). Handle BEFORE pan_begin so we don't leave
        # _pan_state armed when the second click arrives.
        if (event.button == 1 and getattr(event, "dblclick", False)
                and self._maybe_handle_dblclick_drilldown(event)):
            # Cancel any pan that the *first* click of the double-click
            # may have armed; we just teleported to a different view.
            self._pan_state = None
            self._drag_press = None
            return
        # Drawings: B1 single-click on a horizontal line starts a
        # drag-to-move gesture. Intercept BEFORE pan_begin so the
        # chart doesn't pan while the user is dragging a line.
        if event.button == 1 and self._maybe_begin_drawing_drag(event):
            self._drag_press = None
            return
        # Remember the press location so button_release can distinguish
        # click vs drag and so pan/zoom can start (spec §12, §6.4).
        self._drag_press = {
            "x": event.x, "y": event.y,
            "xdata": event.xdata, "ydata": event.ydata,
            "ax": event.inaxes, "button": event.button,
            "moved": False,
        }
        if event.button == 1:
            self._pan_begin(event)
        elif event.button == 3:
            self._zoom_begin(event)
        # Give the canvas keyboard focus so click-to-type keystrokes arrive.
        try:
            self._canvas.get_tk_widget().focus_set()
        except Exception:  # noqa: BLE001
            pass

    def _maybe_handle_pane_indicator_label_click(self, event) -> bool:
        """Open pane-indicator settings when the in-pane label is clicked."""
        label, config_id = self._pane_indicator_label_hit(event)
        if label is None or config_id is None:
            return False
        slot_key = self._slot_key_for_axes(getattr(event, "inaxes", None))
        if event.button == 1:
            opener = getattr(self, "_open_per_indicator_dialog", None)
            if callable(opener):
                try:
                    opener(int(config_id), slot_key)
                except Exception:  # noqa: BLE001
                    pass
            return True
        if event.button == 3:
            show = getattr(self, "_show_legend_context_menu", None)
            if callable(show):
                x_root, y_root = self._event_root_xy(event)
                try:
                    show(int(config_id), slot_key, x_root, y_root)
                except Exception:  # noqa: BLE001
                    pass
            return True
        return False

    def _pane_indicator_label_hit(self, event):
        ax = getattr(event, "inaxes", None)
        if ax is None:
            return None, None
        label = getattr(ax, "_sc_pane_label_artist", None)
        if label is None:
            return None, None
        try:
            if not label.get_visible():
                return None, None
        except Exception:  # noqa: BLE001
            return None, None
        config_ids = tuple(getattr(label, "_sc_pane_label_config_ids", ()) or ())
        if not config_ids:
            return None, None
        hit = False
        try:
            hit = bool(label.contains(event)[0])
        except Exception:  # noqa: BLE001
            hit = False
        if not hit:
            try:
                renderer = self._canvas.get_renderer()
                bbox = label.get_window_extent(renderer)
                hit = bool(bbox.contains(float(event.x), float(event.y)))
            except Exception:  # noqa: BLE001
                hit = False
        if not hit:
            return None, None
        try:
            config_id = int(config_ids[0])
        except (TypeError, ValueError):
            return None, None
        return label, config_id

    def _maybe_handle_readout_legend_click(self, event) -> bool:
        """Open per-indicator edit/menu when an in-readout legend row is clicked.

        Mirrors :meth:`_maybe_handle_pane_indicator_label_click` but
        hit-tests the per-overlay ``TextArea`` rows stacked under the
        OHLCV strip. B1 → per-indicator edit dialog; B3 → the full
        legend context menu (Edit / Change Colour / Duplicate /
        Hide↔Show / Remove). Returns True when a row was hit so the
        caller can suppress pan/zoom.
        """
        config_id = self._readout_legend_row_hit(event)
        if config_id is None:
            return False
        slot_key = self._slot_key_for_axes(getattr(event, "inaxes", None))
        if event.button == 1:
            opener = getattr(self, "_open_per_indicator_dialog", None)
            if callable(opener):
                try:
                    opener(int(config_id), slot_key)
                except Exception:  # noqa: BLE001
                    pass
            return True
        if event.button == 3:
            show = getattr(self, "_show_legend_context_menu", None)
            if callable(show):
                x_root, y_root = self._event_root_xy(event)
                try:
                    show(int(config_id), slot_key, x_root, y_root)
                except Exception:  # noqa: BLE001
                    pass
            return True
        return False

    def _readout_legend_row_hit(self, event):
        """Return the ``config_id`` of the legend row under ``event``, or None.

        Each readout box stashes ``_ind_rows`` (built in
        :meth:`_build_readout_indicator_rows`); every row is now an
        ``HPacker`` (one indicator config, many output segments).
        The HPacker's window extent covers the whole row, so a pixel
        hit anywhere on the row maps to the indicator's config_id.

        Audit ``legend-condensation``.
        """
        ax = getattr(event, "inaxes", None)
        if ax is None:
            return None
        box = getattr(self, "_readout_artists", {}).get(ax)
        if box is None or not box.get_visible():
            return None
        rows = getattr(box, "_ind_rows", None)
        if not rows:
            return None
        try:
            renderer = self._canvas.get_renderer()
        except Exception:  # noqa: BLE001
            return None
        for meta in rows:
            container = meta.get("container")
            if container is None:
                continue
            try:
                bbox = container.get_window_extent(renderer)
                if bbox.contains(float(event.x), float(event.y)):
                    return int(meta.get("config_id"))
            except Exception:  # noqa: BLE001
                continue
        return None

    def _slot_key_for_axes(self, ax) -> str | None:
        if ax is None:
            return None
        for slot_key, ps in getattr(self, "_panel_state", {}).items():
            if ps.get("price_ax") is ax or ps.get("vol_ax") is ax:
                return str(slot_key)
            try:
                if ax in (ps.get("ind_axes") or ()):  # indicator panes
                    return str(slot_key)
            except Exception:  # noqa: BLE001
                pass
        return None

    def _event_root_xy(self, event) -> tuple[int, int]:
        gui_event = getattr(event, "guiEvent", None)
        try:
            return int(gui_event.x_root), int(gui_event.y_root)
        except Exception:  # noqa: BLE001
            pass
        try:
            widget = self._canvas.get_tk_widget()
            return (
                int(widget.winfo_rootx()) + int(event.x),
                int(widget.winfo_rooty()) + int(event.y),
            )
        except Exception:  # noqa: BLE001
            return (
                int(getattr(event, "x", 0) or 0),
                int(getattr(event, "y", 0) or 0),
            )

    def _maybe_handle_dblclick_drilldown(self, event) -> bool:
        """Return True if a 1d→5m drill-down zoom was kicked off.

        Gates: must be on 1d interval; click must land on either the
        **primary** or **compare** panel's price or volume axes; must
        hit a real (non-gap) candle near its center column. If the 5m
        cache for the primary ticker doesn't cover the clicked day, the
        call is a no-op (per spec: missing companion-prefetch data ⇒
        no drill-down).

        Compare-panel clicks are accepted because the four axes share
        an x-axis (matplotlib ``sharex``) — zooming primary's xlim
        propagates to compare automatically. The clicked day is taken
        from the panel that was clicked; thanks to pairing, the same
        index in the primary panel maps to the same calendar date.

        **HA mode robustness**: the day is taken from the slot's raw
        ``_panel_state[slot]['candles']`` list (which is always the raw
        OHLC list, even under HA display). The legacy ``_ax_candle_map``
        lookup is kept as a fast path; on a miss the function falls back
        to ``_panel_state`` so any stale-map / partial-render edge case
        (e.g. a click that lands mid-re-render after an HA toggle) still
        resolves cleanly. Even though HA preserves ``date`` / ``is_gap``,
        sourcing from ``_panel_state`` first eliminates any sensitivity
        to map-rebuild timing.
        """
        if self.interval_var.get() != "1d":
            return False
        ax = event.inaxes
        if ax is None or event.xdata is None:
            return False
        # Locate the slot whose axes received the click. Iterating
        # _panel_state keeps this future-proof if more panels are added.
        hit_slot = None
        hit_ps = None
        for slot, ps in self._panel_state.items():
            if ax is ps.get("price_ax") or ax is ps.get("vol_ax"):
                hit_slot = slot
                hit_ps = ps
                break
        if hit_slot is None or hit_ps is None:
            return False
        # Prefer the slot's raw candles (always the true OHLC list, even
        # in HA mode). Fall back to ``_ax_candle_map`` for offset only —
        # the map is keyed by axes identity and may be transiently stale
        # right after an HA toggle's ``_render`` if a click is dispatched
        # before the topology rebuild commits.
        candles = hit_ps.get("candles") or []
        entry = self._ax_candle_map.get(ax)
        offset = entry[2] if entry is not None else 0
        if not candles:
            return False
        idx = int(round(event.xdata - offset))
        if idx < 0 or idx >= len(candles):
            return False
        # Accept any click within the bar's full half-column (±0.5 of bar
        # center) — this is the entire visual column the bar owns, so a
        # click on the body, the wick, or even empty space directly above
        # or below the candle drills into it. Bodies are rendered with
        # half-width 0.3 (``rendering._BODY_HALF``); the original ±0.3
        # tolerance equalled the body bounds exactly, which felt flaky at
        # body edges due to float precision and made the wick feel like
        # the only reliable target. ±0.5 = snap-to-nearest, no dead zones
        # between bars.
        if abs(event.xdata - (idx + offset)) > 0.5:
            return False
        c = candles[idx]
        if getattr(c, "is_gap", False):
            return False
        try:
            day = c.date.date()
        except Exception:  # noqa: BLE001
            return False
        return self._zoom_5m_for_date(day)

    def _on_button_release(self, event) -> None:
        # Drawing drag release takes priority — commit the new price.
        if event.button == 1 and self._maybe_end_drawing_drag(event):
            self._drag_press = None
            return
        press = getattr(self, "_drag_press", None)
        self._drag_press = None
        if event.button == 1:
            self._pan_end(event)
        elif event.button == 3:
            self._zoom_end(event)
        if press is None:
            return
        # Click-to-type detection: release near press with same axes = click.
        dx = (event.x or 0) - (press.get("x") or 0)
        dy = (event.y or 0) - (press.get("y") or 0)
        is_click = (dx * dx + dy * dy) < 9
        same_ax = press["ax"] is event.inaxes and event.inaxes is not None
        if event.button == 1 and is_click and same_ax:
            self._begin_click_to_type(event.inaxes)
        elif event.button == 3 and is_click and same_ax:
            # Drawings: B3 click-no-drag pops the per-line context
            # menu when the release was on a drawing, otherwise the
            # chart-canvas context menu. ``_zoom_end`` already removed
            # the rubber-band rectangle (the data-coord-delta check
            # there short-circuits a no-drag B3). Dragged B3 falls
            # through to the normal rubber-band zoom path.
            try:
                self._maybe_handle_b3_click_menu(event)
            except Exception:  # noqa: BLE001
                pass

    def _on_mouse_move(self, event) -> None:
        # Drawing drag motion takes priority — move the line preview.
        if getattr(self, "_drawing_drag_state", None) is not None:
            self._drawing_drag_motion(event)
            return
        # Pan drag takes priority; no hover during pan.
        if self._pan_state is not None:
            self._pan_drag(event)
            return
        if self._zoom_state is not None:
            self._zoom_drag(event)
            return
        # Cache pixel position for crosshair revival after re-render (§11.4).
        if event.inaxes is not None and event.x is not None and event.y is not None:
            self._last_cursor_px = (int(event.x), int(event.y))
        else:
            self._last_cursor_px = None
        # H2: coalesce hover dispatch to ~60 Hz. Stash the latest event
        # and schedule (or piggy-back on) a single pending dispatch.
        # Intermediate motion events drop their work; only the most
        # recent event drives the next blit.
        self._hover_pending_event = event
        if self._hover_throttle_job is None:
            try:
                self._hover_throttle_job = self._track_after(
                    _HOVER_THROTTLE_MS, self._run_throttled_hover)
            except Exception:  # noqa: BLE001
                # Fallback: dispatch synchronously if scheduling fails.
                self._dispatch_hover(event)
                self._hover_pending_event = None

    def _run_throttled_hover(self) -> None:
        """Drain the latest pending hover event scheduled by H2 throttle."""
        self._hover_throttle_job = None
        ev = self._hover_pending_event
        self._hover_pending_event = None
        if ev is None:
            return
        try:
            self._dispatch_hover(ev)
        except Exception:  # noqa: BLE001
            pass

    def _on_draw_event(self, _event) -> None:
        """Capture blit background after every matplotlib full-redraw (§11.2)."""
        # Live-tick blit fast path: while seeding ``_tick_blit_bg`` we issue
        # a full ``canvas.draw()`` with the data artists hidden. That draw
        # fires this handler; suppress it so the data-less snapshot does not
        # become ``_blit_bg`` (which would make hover/pan lose the candles).
        if getattr(self, "_suspend_draw_capture", False):
            return
        try:
            canvas = self._canvas
            self._blit_bg = canvas.copy_from_bbox(self._figure.bbox)
        except Exception:  # noqa: BLE001
            self._blit_bg = None
            return
        # A genuine full redraw repainted the axes decorations, so the
        # data-less tick-blit snapshot is now stale. Drop it; the next
        # eligible tick re-seeds it lazily.
        self._tick_blit_bg = None
        # Composite always-on overlays (data readout, plus any revived
        # crosshair / hover) on top of the freshly-captured background
        # so they don't disappear after a full redraw. Safe: blit() does
        # not re-trigger a draw_event.
        try:
            self._blit_overlays()
        except Exception:  # noqa: BLE001
            pass
        # Reposition the per-slot overlay legends (big-bet item #9,
        # rev 2). Each legend follows its price axes' top-left so the
        # strip stays anchored below the OHLCV readout through resizes,
        # compare-toggles, and theme switches. The first paint sees a
        # 1-pixel canvas — the legend silently no-ops then and the next
        # draw_event (after the layout settles) snaps it into place.
        reposition = getattr(self, "_reposition_overlay_legends", None)
        if callable(reposition):
            try:
                reposition()
            except Exception:  # noqa: BLE001
                pass

    def _on_key_press(self, event) -> None:
        """Accumulate keystrokes for click-to-type (§12).

        Space is reserved as the watchlist-cycle hotkey but is handled
        exclusively by the app-level ``bind_all("<KeyPress-space>")``
        registration (see ``ChartApp._on_global_space``) — that binding
        also overrides the class-level <space> on Treeview / TButton /
        Button so the cycle works regardless of which widget has focus.
        Handling space here too would cause a double-cycle when the
        canvas has focus (widget-level ``<Key>`` fires, then ``"all"``
        tag fires for the same event). So this handler intentionally
        ignores space.
        """
        keysym = getattr(event, "keysym", "") or ""
        ch = getattr(event, "char", "") or ""
        # Filter bare modifier keys.
        if keysym in ("Alt_L", "Alt_R", "Control_L", "Control_R",
                       "Shift_L", "Shift_R", "Meta_L", "Meta_R",
                       "Super_L", "Super_R", "Caps_Lock", "Num_Lock"):
            return
        # Filter modifier combos reported by matplotlib's event.key
        # (works in source installs; may be empty in frozen builds).
        mpl_key = getattr(event, "key", None) or ""
        if mpl_key and ("+" in mpl_key or mpl_key in (
                "alt", "ctrl", "shift", "control", "meta")):
            return
        if keysym == "space":
            return
        if self._typing_target is None:
            # Starting typing with no chart clicked defaults to primary.
            # Tickers are letters (plus dot/dash for class shares like
            # BRK.B). Digits are intentionally ignored so a stray
            # numeric keypress doesn't start a phantom "1" / "23" symbol
            # buffer over the chart.
            if ch and (ch.isalpha() or ch in "._-"):
                self._typing_target = self._last_clicked_slot or "primary"
                self._typing_buffer = ""
            else:
                return
        if keysym == "Return":
            self._commit_click_to_type()
        elif keysym == "Escape":
            self._cancel_click_to_type()
        elif keysym == "BackSpace":
            self._typing_buffer = self._typing_buffer[:-1]
            self._refresh_typing_preview()
        elif ch and (ch.isalpha() or ch in "._-"):
            self._typing_buffer += ch.upper()
            self._refresh_typing_preview()
    # ---- pan + zoom (spec §6.4) ---------------------------------------
    def _pan_setup_blit(self) -> None:
        """Mark all data artists animated, force a clean draw, snapshot bg.

        Idempotent: safe to call multiple times during a pan (e.g. after a
        slice refill swaps artists). Tears down any previous animated
        flags before re-marking.

        H3: fingerprints the figure's artist topology by id() and skips
        the full ``canvas.draw()`` + bg snapshot when the topology hasn't
        changed since the last setup AND the cached ``_pan_bg`` is still
        valid. Pan-begin without intervening renders is the common hit
        (e.g. start-pan, release, start-pan again).
        """
        # Build a fingerprint of the current artist topology in one walk.
        fp_ids: list[int] = []
        artists: list[object] = []
        for axx in self._figure.axes:
            for coll in list(axx.collections):
                fp_ids.append(id(coll))
                artists.append(coll)
            for line in list(axx.lines):
                if line.get_visible():
                    fp_ids.append(id(line))
                    artists.append(line)
            for patch in list(axx.patches):
                if patch.get_visible():
                    fp_ids.append(id(patch))
                    artists.append(patch)
            for text in list(axx.texts):
                if text.get_visible():
                    fp_ids.append(id(text))
                    artists.append(text)
            for axis_obj in (axx.xaxis, axx.yaxis):
                fp_ids.append(id(axis_obj))
                artists.append(axis_obj)
        fingerprint = tuple(fp_ids)

        if (fingerprint == getattr(self, "_pan_anim_fingerprint", None)
                and self._pan_bg is not None
                and self._pan_animated):
            # Topology unchanged AND we still have a valid bg snapshot
            # from the last setup — reuse everything. Animated flags on
            # the cached artists are still set; nothing to do.
            return

        # Tear down previous set (artists may have been replaced).
        for art in self._pan_animated:
            try:
                art.set_animated(False)
            except Exception:  # noqa: BLE001
                pass
        self._pan_animated = []
        for art in artists:
            try:
                art.set_animated(True)
                self._pan_animated.append(art)
            except Exception:  # noqa: BLE001
                pass
        try:
            self._canvas.draw()
            self._pan_bg = self._canvas.copy_from_bbox(self._figure.bbox)
            # Animated artists are excluded from canvas.draw(), so right
            # after capturing the background they aren't on-screen. If
            # the user press-and-holds without moving, the motion
            # handler never fires and the chart would look empty. Do an
            # initial blit now so the candles stay visible on press.
            for art in self._pan_animated:
                try:
                    ax_ = art.axes
                    if ax_ is not None:
                        ax_.draw_artist(art)
                except Exception:  # noqa: BLE001
                    pass
            self._canvas.blit(self._figure.bbox)
            self._pan_anim_fingerprint = fingerprint
        except Exception:  # noqa: BLE001
            self._pan_bg = None
            self._pan_anim_fingerprint = None

    def _pan_rebind_animated_after_slice(self) -> None:
        """Lightweight rebind when virtualized rendering swaps artists.

        ``_draw_slice`` may replace candle/volume PolyCollections /
        LineCollections with brand-new artist instances when the pan
        scrolls past the safe-zone boundary. The new artists are not
        ``set_animated(True)`` and are not in ``_pan_animated``, so the
        next blit frame would skip painting them — leaving the chart
        visually empty until the next ``_pan_setup_blit`` re-capture.

        The previous fix here did a full ``_pan_setup_blit()`` which
        called ``canvas.draw()``. On Tk/Agg, ``canvas.draw()`` pushes
        the just-painted (candle-less, since data artists are animated
        and thus excluded) frame to the visible widget *before*
        ``blit()`` can repaint candles back on top. Slow event loops
        and slice changes that happen mid-drag exposed that one-frame
        gap to the user's eye as a flicker / missing-candles flash.

        This helper avoids ``canvas.draw()`` entirely. It re-walks the
        figure's artist topology, marks every data artist animated,
        rebuilds ``_pan_animated`` + the fingerprint, and reuses the
        existing ``_pan_bg`` (which is just spines / facecolor /
        gridlines — none of which change when the candle slice
        scrolls). The caller (``_pan_drag``) then proceeds straight to
        the normal restore-region → draw_artist → blit sequence so the
        new artists land on screen on the very next frame.

        If ``_pan_bg`` was never captured (initial setup failed),
        falls back to the heavyweight path so we don't blit on top of
        garbage.
        """
        if self._pan_bg is None:
            self._pan_setup_blit()
            return
        fp_ids: list[int] = []
        artists: list[object] = []
        for axx in self._figure.axes:
            for coll in list(axx.collections):
                fp_ids.append(id(coll))
                artists.append(coll)
            for line in list(axx.lines):
                if line.get_visible():
                    fp_ids.append(id(line))
                    artists.append(line)
            for patch in list(axx.patches):
                if patch.get_visible():
                    fp_ids.append(id(patch))
                    artists.append(patch)
            for text in list(axx.texts):
                if text.get_visible():
                    fp_ids.append(id(text))
                    artists.append(text)
            for axis_obj in (axx.xaxis, axx.yaxis):
                fp_ids.append(id(axis_obj))
                artists.append(axis_obj)
        # Tear down stale animated flags on artists that are no longer
        # in the topology (e.g. removed by _draw_slice).
        new_set = set(id(a) for a in artists)
        for art in self._pan_animated:
            if id(art) not in new_set:
                try:
                    art.set_animated(False)
                except Exception:  # noqa: BLE001
                    pass
        self._pan_animated = []
        for art in artists:
            try:
                art.set_animated(True)
                self._pan_animated.append(art)
            except Exception:  # noqa: BLE001
                pass
        self._pan_anim_fingerprint = tuple(fp_ids)

    def _pan_begin(self, event) -> None:
        if event.inaxes is None:
            return
        ax = event.inaxes
        try:
            press_xlim = ax.get_xlim()
            width_px = max(1, ax.bbox.width)
        except Exception:  # noqa: BLE001
            return
        self._pan_state = {
            "press_x": event.x, "press_xlim": press_xlim,
            "width_px": width_px, "ax": ax,
        }
        # qw-pan-autoscale: fresh gesture — clear the per-frame bar-range
        # memo so the first drag frame recomputes the Y-fit.
        self._pan_last_bar_range = None
        self._hide_overlays()
        # NOTE: Don't run ``_pan_setup_blit`` here. It marks data
        # artists ``animated=True`` and calls ``canvas.draw()`` (which
        # excludes animated artists), then captures the background and
        # blits the candles back. On a Tk canvas there is a brief
        # moment between the data-less ``canvas.draw()`` push and the
        # follow-up ``canvas.blit`` that the user sees as a "candles
        # disappear and come back" flicker — and for a pure click +
        # release (no drag), the setup is wasted entirely. Defer it
        # to the first ``_pan_drag`` call: if the user actually moves
        # the mouse we set up blit lazily on that first frame; if they
        # just click and release, ``_pan_end`` runs without setup
        # ever happening.

    def _pan_drag(self, event) -> None:
        st = self._pan_state
        if st is None or event.x is None:
            return
        lo, hi = st["press_xlim"]
        xrange = hi - lo
        dx_px = event.x - st["press_x"]
        dx_data = -dx_px * xrange / st["width_px"]
        new_lo, new_hi = lo + dx_data, hi + dx_data
        try:
            st["ax"].set_xlim(new_lo, new_hi)
        except Exception:  # noqa: BLE001
            return
        # Lazy blit setup on the first drag frame. Deferred from
        # ``_pan_begin`` to avoid the click+release flicker (see the
        # note in ``_pan_begin``). If the user did pan, set up now;
        # the upfront cost (one ``canvas.draw()`` with data hidden +
        # bg snapshot) is invisible because we IMMEDIATELY blit the
        # candles back via the ``draw_artist`` loop inside
        # ``_pan_setup_blit`` itself, and then again on this very
        # drag frame's ``restore_region`` + per-artist draw + blit.
        if self._pan_bg is None and not self._pan_animated:
            self._pan_setup_blit()
        if self._pan_bg is None:
            # Fallback to full-redraw path if blit setup failed.
            if self._pan_redraw_job is not None:
                return
            try:
                self._pan_redraw_job = self._track_after(
                    _PAN_REDRAW_INTERVAL_MS, self._pan_redraw_tick,
                )
            except tk.TclError:
                pass
            return
        try:
            # Detect whether virtualized rendering swaps artists out.
            prev_ranges = {
                slot: (int(ps.get("render_start", 0)), int(ps.get("render_end", 0)))
                for slot, ps in self._panel_state.items()
            }
            for slot in list(self._panel_state.keys()):
                self._ensure_rendered_for_view(slot)
            slice_changed = any(
                prev_ranges.get(slot) !=
                (int(ps.get("render_start", 0)), int(ps.get("render_end", 0)))
                for slot, ps in self._panel_state.items()
            )
            # Per-frame Y-autoscale (~0.25ms) so candles stay vertically
            # framed while panning.
            #
            # qw-pan-autoscale: the Y-fit is a pure function of the integer
            # bar range in view (the candle data is frozen for the duration
            # of a pan gesture). A fast drag emits many frames that move
            # less than one bar width, so the range is unchanged and the
            # autoscale (per-ax get_xlim + integer slice + y-limit scan over
            # every price/volume/indicator pane) is wasted. All price axes
            # are sharex-linked with offset 0, so the panned axis's integer
            # range is representative. Skip when it (and the virtualized
            # render slice) are unchanged this gesture. The first frame
            # (cache ``None``) and any slice change always recompute.
            try:
                _lo_f, _hi_f = st["ax"].get_xlim()
                cur_bar_range = (
                    int(np.ceil(_lo_f - 1e-6)),
                    int(np.floor(_hi_f + 1e-6)),
                )
            except Exception:  # noqa: BLE001
                cur_bar_range = None
            if (
                slice_changed
                or cur_bar_range is None
                or cur_bar_range != getattr(self, "_pan_last_bar_range", None)
            ):
                self._autoscale_y_to_visible()
                self._pan_last_bar_range = cur_bar_range
            if slice_changed:
                # New artists were created by _draw_slice and aren't
                # animated. Rebind the animated set WITHOUT calling
                # canvas.draw() — the previous heavyweight setup_blit
                # path pushed a candle-less frame to the visible Tk
                # widget for one redraw cycle, which the user saw as
                # a "candles flashed off then back on" glitch during
                # long pans that crossed virtualized-render
                # boundaries. _pan_bg is still valid because it only
                # contains static decorations (spines, gridlines,
                # facecolor) — data artists were animated before too.
                self._pan_rebind_animated_after_slice()
                # Fall through to the normal restore+draw_artist+blit
                # path below so the new animated artists land on this
                # very frame, with no intermediate empty frame.
            canvas = self._canvas
            canvas.restore_region(self._pan_bg)
            for art in self._pan_animated:
                try:
                    ax_ = art.axes
                    if ax_ is not None:
                        ax_.draw_artist(art)
                except Exception:  # noqa: BLE001
                    pass
            canvas.blit(self._figure.bbox)
        except Exception:  # noqa: BLE001
            pass

    def _pan_redraw_tick(self) -> None:
        """Fallback path when blit setup fails — full redraw."""
        self._pan_redraw_job = None
        try:
            for slot in list(self._panel_state.keys()):
                self._ensure_rendered_for_view(slot)
            self._autoscale_y_to_visible()
            self._canvas.draw_idle()
        except Exception:  # noqa: BLE001
            pass

    def _pan_end(self, _event) -> None:
        if self._pan_state is None:
            return
        self._pan_state = None
        # qw-pan-autoscale: gesture over — drop the per-frame bar-range memo.
        self._pan_last_bar_range = None
        # Tear down blit state: unmark animated artists so the next full
        # render includes them in the cached background again.
        for art in self._pan_animated:
            try:
                art.set_animated(False)
            except Exception:  # noqa: BLE001
                pass
        self._pan_animated = []
        self._pan_bg = None
        # Invalidate the global blit bg too. _pan_setup_blit's
        # ``canvas.draw()`` (called with data artists set animated=True)
        # fires draw_event with the data layer EXCLUDED, so _on_draw_event
        # captured a candle-less _blit_bg. If we leave that stale snapshot
        # in place, the next hover will ``restore_region`` it and wipe the
        # candles to blank until the deferred draw_idle below resolves.
        # Setting None forces _blit_overlays to short-circuit (it will
        # request a fresh draw_idle and not restore) so the live blitted
        # frame stays on screen until the proper background is recaptured.
        self._blit_bg = None
        # User explicitly framed the chart via pan — persist this view
        # across subsequent renders (compare toggle, ticker swap, etc.).
        # Mirrors scroll-wheel zoom, rubber-band zoom, and drill-down.
        self._preserve_xlim_on_render = True
        self._slide_xlim_to_right_edge = False
        try:
            self._autoscale_y_to_visible()
            self._canvas.draw_idle()
        except Exception:  # noqa: BLE001
            pass

    def _zoom_begin(self, event) -> None:
        if event.inaxes is None:
            return
        # Draw a matplotlib rubber-band rectangle via a Rectangle artist.
        from matplotlib.patches import Rectangle
        x0, y0 = event.xdata, event.ydata
        try:
            theme = self._theme
            rect = Rectangle(
                (x0, y0), 0, 0, fill=False,
                edgecolor=theme["crosshair"], linestyle="--", linewidth=1.0,
                animated=False, zorder=10,
            )
            event.inaxes.add_patch(rect)
        except Exception:  # noqa: BLE001
            return
        self._zoom_state = {
            "ax": event.inaxes, "x0": x0, "y0": y0, "rect": rect,
        }
        self._hide_overlays()

    def _zoom_drag(self, event) -> None:
        st = self._zoom_state
        if st is None or event.xdata is None or event.ydata is None:
            return
        w = event.xdata - st["x0"]
        h = event.ydata - st["y0"]
        try:
            st["rect"].set_width(w)
            st["rect"].set_height(h)
            self._canvas.draw_idle()
        except Exception:  # noqa: BLE001
            pass

    def _zoom_end(self, event) -> None:
        st = self._zoom_state
        self._zoom_state = None
        if st is None:
            return
        try:
            st["rect"].remove()
        except Exception:  # noqa: BLE001
            pass
        if event.xdata is None:
            self._canvas.draw_idle()
            return
        x0, x1 = sorted([st["x0"], event.xdata])
        if x1 - x0 < 1e-3:
            self._canvas.draw_idle()
            return
        try:
            st["ax"].set_xlim(x0, x1)
            # User explicitly framed the chart via rubber-band zoom —
            # persist this view across subsequent renders so toggling
            # compare on/off, swapping compare ticker, etc. don't snap
            # back to the data extent. Mirrors the scroll-wheel zoom
            # and drill-down paths (app._do_drilldown).
            self._preserve_xlim_on_render = True
            self._slide_xlim_to_right_edge = False
            self._autoscale_y_to_visible()
            self._canvas.draw_idle()
        except Exception:  # noqa: BLE001
            pass

    # ---- mouse-wheel zoom (TradingView-style, cursor-anchored) --------
    #
    # Scroll DOWN gradually zooms IN, scroll UP gradually zooms OUT. The
    # bar under the cursor stays fixed in screen space — that's the
    # TradingView convention and what users expect: hover over a bar of
    # interest, scroll, and the chart "telescopes" toward/away from
    # exactly that bar instead of recentering.
    # Tunables — defaults here mirror defaults.TUNABLES so subclasses
    # can override per-instance, but normal runs read from settings.json
    # via the central registry at class-definition time.
    _SCROLL_ZOOM_PER_STEP = _defaults.get("scroll_zoom_factor_per_step")
    _SCROLL_ZOOM_MIN_BARS = _defaults.get("scroll_zoom_min_bars")
    _SCROLL_ZOOM_STEP_CLAMP = _defaults.get("scroll_zoom_step_clamp")

    def _on_scroll_zoom(self, event) -> None:
        """Cursor-anchored mouse-wheel zoom on the primary x-axis.

        Spec §6.4. Sets xlim on ``_ax_price`` only; matplotlib's
        ``sharex`` broadcasts the change to volume and (when present)
        compare. The bar at ``event.xdata`` is held fixed in screen
        space via ``new_lo = x - (x - lo) * factor`` /
        ``new_hi = x + (hi - x) * factor``. Sets
        ``_preserve_xlim_on_render`` so subsequent renders (theme
        toggle, stream tick, compare toggle…) keep the user's view
        until an explicit interval/source change or reset.

        Blit fast-path: deferred (A6 in audit). A blit-based zoom is
        appealing but breaks two invariants the codebase relies on:
        (a) headless test harnesses inspect ``canvas.buffer_rgba()``
        which is the matplotlib renderer buffer — Agg/Tk's ``blit``
        updates only the Tk widget surface, leaving the renderer
        buffer showing the bg snapshot (data artists are
        ``set_animated(True)`` and excluded from ``canvas.draw()``).
        (b) Zoom can refill the rendered slice when the view crosses
        a virtualization boundary; managing animated-set rebind +
        blit-burst teardown in the presence of slice swaps is
        significantly more complex than pan (which is a single
        continuous gesture with explicit begin/end). The full-redraw
        path is consistent with the headless test contract and only
        marginally slower in practice (a typical user issues at
        most 5-10 wheel ticks per zoom).

        Pinch-zoom / touch input is OS-specific (Windows
        ``WM_POINTER*``) and out of scope for v1 — the wheel handler
        is the only zoom input on this build.

        Gates:
        - cursor must be inside an axes (``event.inaxes is not None``)
          and ``event.xdata`` must be defined;
        - no active pan (``_pan_state``) or rubber-band-zoom
          (``_zoom_state``) gesture — wheel during a drag would
          corrupt the gesture's stashed xlim and blit state;
        - ``event.step`` magnitude is capped at 2 to neutralise
          high-precision trackpads / accelerated wheels that emit
          step values like 12 in a single event (would otherwise
          collapse the chart in one flick).
        """
        if event.inaxes is None or event.xdata is None:
            return
        # Don't fight an in-progress pan or rubber-band zoom — those
        # gestures cached xlim/blit state at gesture-start and reapply
        # it on motion/release; mutating xlim mid-gesture corrupts them.
        if self._pan_state is not None or self._zoom_state is not None:
            return
        ax = getattr(self, "_ax_price", None)
        if ax is None:
            return
        try:
            lo, hi = ax.get_xlim()
        except Exception:  # noqa: BLE001
            return
        width = hi - lo
        if width <= 0:
            return
        # Resolve scroll direction: prefer event.step (signed magnitude
        # on most backends), fall back to event.button on backends that
        # only set 'up'/'down'. Cap |step| to neutralise trackpads/
        # high-resolution wheels that emit large per-event steps.
        raw_step = getattr(event, "step", 0)
        if raw_step:
            try:
                step = float(raw_step)
            except Exception:  # noqa: BLE001
                step = 0.0
        else:
            btn = getattr(event, "button", None)
            step = 1.0 if btn == "up" else (-1.0 if btn == "down" else 0.0)
        if step == 0.0:
            return
        if step > self._SCROLL_ZOOM_STEP_CLAMP:
            step = self._SCROLL_ZOOM_STEP_CLAMP
        elif step < -self._SCROLL_ZOOM_STEP_CLAMP:
            step = -self._SCROLL_ZOOM_STEP_CLAMP
        # User preference: by default scroll DOWN (step<0) → zoom IN
        # (factor<1). The ``_scroll_zoom_invert`` flag (Settings dialog)
        # flips the sign for users who prefer the macOS/natural-scroll
        # convention where scrolling UP zooms IN.
        if getattr(self, "_scroll_zoom_invert", False):
            step = -step
        factor = self._SCROLL_ZOOM_PER_STEP ** step
        x = float(event.xdata)
        new_lo = x - (x - lo) * factor
        new_hi = x + (hi - x) * factor
        new_width = new_hi - new_lo
        if new_width < self._SCROLL_ZOOM_MIN_BARS:
            # Clamp width while preserving cursor anchor so the bar
            # under the mouse still stays put at the floor zoom level.
            left_frac = (x - lo) / width if width > 0 else 0.5
            new_lo = x - left_frac * self._SCROLL_ZOOM_MIN_BARS
            new_hi = new_lo + self._SCROLL_ZOOM_MIN_BARS
        try:
            ax.set_xlim(new_lo, new_hi)
        except Exception:  # noqa: BLE001
            return
        # User explicitly framed the chart — persist this view across
        # subsequent renders, and cancel any pending poll-tick auto-
        # slide-to-right-edge that may already have been pre-armed by
        # _next_bar_fetch_tick before this wheel event arrived.
        self._preserve_xlim_on_render = True
        self._slide_xlim_to_right_edge = False
        # Hover/crosshair were anchored to the previous xlim's pixel
        # geometry; they'll be reacquired on the next motion event.
        try:
            self._hide_overlays()
        except Exception:  # noqa: BLE001
            pass
        # Refill the virtualized artist slice for the new window so
        # bars previously off-screen become visible after a zoom-out,
        # then re-autoscale Y to the new view and schedule a full
        # redraw.
        try:
            for slot in list(self._panel_state.keys()):
                self._ensure_rendered_for_view(slot)
            self._autoscale_y_to_visible()
            self._canvas.draw_idle()
        except Exception:  # noqa: BLE001
            pass

    def _autoscale_y_to_visible(self) -> None:
        """Recompute Y limits on every price+volume axes from visible X range.

        A bar at integer index ``i`` occupies x-range ``[i-0.5, i+0.5]``,
        so it's "in view" iff its center ``i`` falls inside the current
        xlim. Using ``ceil(lo_f)`` / ``floor(hi_f)`` (with a tiny epsilon
        for float-comparison safety) selects exactly those bars and
        excludes the half-overlapping neighbors on either side. That
        matters for drill-down zoom: the bar just before / after a
        trading-day boundary lives on a different day at a possibly
        very different price level, and including it in the autoscale
        slice distorts the visible day's range.
        """
        eps = 1e-6
        for ax, entry in self._ax_candle_map.items():
            candles, kind, offset = entry
            try:
                lo_f, hi_f = ax.get_xlim()
                lo = max(0, int(np.ceil(lo_f - offset - eps)))
                hi = min(len(candles),
                         int(np.floor(hi_f - offset + eps)) + 1)
                if hi <= lo:
                    continue
                if kind == "indicator":
                    # Indicator panes have their own Y semantics
                    # (RSI in [0,100], SMI in ~[-100,100], etc.) — they
                    # MUST NOT be y-fit via volume/price slice math. We
                    # refit them from the lines tracked in the slot's
                    # PanelIndicatorState (skips reference axhlines and
                    # any non-data artists).
                    self._autoscale_indicator_pane(ax, lo, hi)
                    continue
                # For price axes, autoscale from the *displayed* candle
                # list so HA bars (whose [low, high] is a strict
                # superset of the real range) don't get clipped on the
                # toggle ON, and so ylim shrinks back to the real range
                # on toggle OFF. ``ps['display_candles']`` is populated
                # by ``_draw_slice`` and equals ``candles`` when HA is
                # off, so behavior is unchanged for non-HA renders.
                # Volume always uses the real list (HA does not touch
                # volume).
                series_candles = candles
                if kind == "price":
                    for _ps in self._panel_state.values():
                        if _ps.get("price_ax") is ax:
                            disp = _ps.get("display_candles")
                            if disp is not None:
                                series_candles = disp
                            break
                sa = self._series(series_candles)
                use_log = (kind == "price" and ax.get_yscale() == "log")
                ylim = _y_limits_for_slice(sa, kind, lo, hi, log=use_log)
                if ylim is not None:
                    ax.set_ylim(*ylim)
            except Exception:  # noqa: BLE001
                pass

    def _autoscale_indicator_pane(self, ax, lo: int, hi: int) -> None:
        """Refit one indicator pane's y-axis to its visible data lines.

        Walks ``_panel_state`` to find the slot whose ``ind_state`` owns
        ``ax`` as a pane axis, then delegates to
        :func:`indicators.render.autoscale_pane_y` against the lines
        tracked in ``state.pane_lines[cfg_id]``. Reference axhlines
        (e.g. SMI's ±40/0) are excluded because they live on
        ``ax.lines`` directly, not in ``pane_lines``.
        """
        from ..indicators import render as _ind_render

        for ps in self._panel_state.values():
            state = ps.get("ind_state")
            if state is None:
                continue
            for cfg_id, pane_ax in getattr(state, "panes", {}).items():
                if pane_ax is ax:
                    lines = state.pane_lines.get(cfg_id, {}).values()
                    try:
                        _ind_render.autoscale_pane_y(ax, lines, lo, hi)
                    except Exception:  # noqa: BLE001
                        pass
                    return

    # ---- hover + crosshair (spec §11) ---------------------------------
    def _ensure_overlay_artists(self) -> None:
        """(Re)build the animated hover annotation + crosshair artists.

        Called at the end of ``_render`` because ``figure.clear()`` destroys
        axes and their children. Artists are created with ``animated=True``
        so they are excluded from the blit background snapshot and
        composited via ``_blit_overlays``.
        """
        from matplotlib.transforms import blended_transform_factory

        # Drop any stale artists tied to now-dead axes.
        self._crosshair_artists = {}
        self._price_label_artists = {}
        self._time_label_artist = None
        self._time_label_artists = {}
        self._hover_ann = None
        axes = list(self._ax_candle_map.keys())
        if not axes:
            return
        theme = self._theme
        # One vline per axes (all hidden initially).
        for ax in axes:
            try:
                vline = ax.axvline(0, color=theme["crosshair"], linestyle=":",
                                   linewidth=0.8, animated=True, visible=False,
                                   zorder=8)
                hline = ax.axhline(0, color=theme["crosshair"], linestyle=":",
                                   linewidth=0.8, animated=True, visible=False,
                                   zorder=8)
                self._crosshair_artists[ax] = (vline, hline)
            except Exception:  # noqa: BLE001
                pass
        # Floating "current value" badge — every y-axes (price + volume).
        # Anchored at axes-fraction x=1.0 (right spine, TradingView/Sierra
        # convention — matches the right-side y-tick labels installed by
        # ``rendering.setup_price_axes``) with y in data coords via a
        # blended transform so it slides up/down with the cursor and
        # stays pinned horizontally. Opaque bbox visually occludes the
        # y-tick labels baked into the blit background. Each axes uses its
        # own installed major formatter so prices render as e.g. "$280.45"
        # and volumes as e.g. "1.2M" (`fmt_volume` via `FuncFormatter`).
        for ax, entry in self._ax_candle_map.items():
            kind = entry[1] if entry else None
            if kind not in ("price", "volume"):
                continue
            try:
                blended = blended_transform_factory(ax.transAxes, ax.transData)
                label = ax.annotate(
                    "", xy=(1.0, 0.0), xycoords=blended,
                    xytext=(3, 0), textcoords="offset points",
                    ha="left", va="center",
                    bbox=dict(boxstyle="round,pad=0.30",
                              fc=theme["tooltip_bg"], ec=theme["spine"],
                              alpha=1.0, linewidth=0.8),
                    color=theme["tooltip_fg"],
                    fontsize=8, animated=True, visible=False, zorder=10,
                    clip_on=False,
                )
                self._price_label_artists[ax] = label
            except Exception:  # noqa: BLE001
                pass
        # Time badge under the x-crosshair — ONE per pane (slot), each
        # anchored to that pane's bottom-most axes (TradingView parity).
        # Anchored at xdata in data coords / y=0 axes-fraction via a
        # blended transform so it slides left/right with the cursor and
        # hugs the bottom edge of its pane. Opaque bbox occludes the
        # x-tick labels baked into the blit background. Text format
        # mirrors the hover tooltip — ``YYYY-MM-DD HH:MM`` for intraday
        # bars (with the user's display tz applied via ``format_dt``)
        # and ``YYYY-MM-DD`` for daily / weekly / monthly bars.
        #
        # Per-pane (not a single global-bottom badge) so that in compare
        # mode the badge appears under the cursor's chart instead of
        # always the globally lowest chart. Axes are grouped by panel-
        # state slot via ``_slot_key_for_axes``; axes that resolve to no
        # slot fall back to a single ``None``-keyed group (degenerate /
        # pre-panel-state render), preserving the original behaviour.
        slot_axes: dict = {}
        for ax in axes:
            if not self._ax_candle_map.get(ax):
                continue
            slot_axes.setdefault(self._slot_key_for_axes(ax), []).append(ax)
        for slot_key, sl_axes in slot_axes.items():
            try:
                bottom_ax = min(sl_axes, key=lambda a: a.get_position().y0)
                blended_t = blended_transform_factory(
                    bottom_ax.transData, bottom_ax.transAxes)
                self._time_label_artists[slot_key] = bottom_ax.annotate(
                    "", xy=(0.0, 0.0), xycoords=blended_t,
                    xytext=(0, -3), textcoords="offset points",
                    ha="center", va="top",
                    bbox=dict(boxstyle="round,pad=0.30",
                              fc=theme["tooltip_bg"], ec=theme["spine"],
                              alpha=1.0, linewidth=0.8),
                    color=theme["tooltip_fg"],
                    fontsize=8, animated=True, visible=False, zorder=10,
                    clip_on=False,
                )
            except Exception:  # noqa: BLE001
                pass
        # Back-compat alias: call sites + tests that predate the per-pane
        # split read ``_time_label_artist``. Point it at the primary
        # pane's badge (the only badge in non-compare mode, and the
        # bottom-most axes there).
        self._time_label_artist = (
            self._time_label_artists.get("primary")
            or next(iter(self._time_label_artists.values()), None)
        )
        # Top-left OHLCV / %change readout — price panels only (spec §11.6).
        # Built as an AnchoredOffsetbox holding two TextAreas so the
        # bull/bear-coloured pct can sit immediately to the right of the
        # neutral OHLCV string without manual width math.
        from matplotlib.offsetbox import (
            AnchoredOffsetbox,
            HPacker,
            TextArea,
            VPacker,
        )
        self._readout_artists = {}
        for ax, entry in self._ax_candle_map.items():
            kind = entry[1] if entry else None
            if kind != "price":
                continue
            try:
                main_text = TextArea(
                    "",
                    textprops=dict(color=theme["text"], fontsize=9,
                                   family="monospace"),
                )
                pct_text = TextArea(
                    "",
                    textprops=dict(color=theme["text"], fontsize=9,
                                   family="monospace"),
                )
                ohlcv_row = HPacker(children=[main_text, pct_text],
                                    align="center", pad=0, sep=0)
                # TradingView-style indicator legend rows. One row per
                # indicator config (HPacker of segment TextAreas) —
                # multi-output indicators (Bollinger, AVWAP-with-bands,
                # …) collapse to a single line ``NAME upper <v1>
                # middle <v2> lower <v3>`` with each band's value in
                # its own colour. Hidden overlays appear greyed.
                # Audit ``legend-condensation``.
                ind_packers, ind_meta = self._build_readout_indicator_rows(
                    ax, theme)
                packer = VPacker(
                    children=[ohlcv_row, *ind_packers],
                    align="left", pad=0, sep=2,
                )
                box = AnchoredOffsetbox(
                    loc="upper left", child=packer,
                    pad=0.3, borderpad=0.5, frameon=False,
                    bbox_to_anchor=(0.0, 1.0), bbox_transform=ax.transAxes,
                )
                box.set_animated(True)
                box.set_zorder(11)
                box.set_visible(False)
                ax.add_artist(box)
                # Stash component refs for fast updates without searching
                # back through the offsetbox tree on every hover dispatch.
                box._main_text = main_text
                box._pct_text = pct_text
                box._ind_rows = ind_meta
                self._readout_artists[ax] = box
            except Exception:  # noqa: BLE001
                pass
        # Populate readout with latest bar so it's visible immediately —
        # _on_draw_event composites it onto the fresh blit background.
        try:
            self._update_readout(None)
        except Exception:  # noqa: BLE001
            pass
        # Hover annotation on the first axes (reparented per-axes lazily).
        try:
            primary_ax = axes[0]
            self._hover_ann = primary_ax.annotate(
                "", xy=(0, 0), xytext=(12, 12), textcoords="offset points",
                ha="left", va="bottom",
                bbox=dict(boxstyle="round",
                          fc=theme["tooltip_bg"], ec=theme["spine"],
                          alpha=0.95),
                color=theme["tooltip_fg"],
                fontsize=8, animated=True, visible=False, zorder=9,
            )
        except Exception:  # noqa: BLE001
            self._hover_ann = None
        self._hover_visible = False
        self._crosshair_current_ax = None

    # ---- in-readout overlay indicator legend (TradingView-style) ------

    _READOUT_SCOPE_FOR_SLOT = {"primary": "main", "compare": "compare"}

    def _build_readout_indicator_rows(self, ax, theme):
        """Build the per-overlay legend ``HPacker`` rows for ``ax``.

        Returns ``(packers, meta)`` where ``packers`` is the list of
        matplotlib offsetbox containers to stack under the OHLCV strip
        (one per indicator config), and ``meta`` is a parallel list of
        dicts used by :meth:`_update_readout` (live value) and the
        click hit-test::

            {"config_id", "label", "visible", "container", "outputs": [
                {"output_key", "color", "line", "value_textarea"},
                ...
            ]}

        Rows are enumerated via the pure
        :func:`gui.readout_legend.build_overlay_legend_rows`. As of the
        ``legend-condensation`` sprint each row is ONE indicator
        config — multi-output indicators are rendered as
        ``LABEL upper <v1> middle <v2> lower <v3>`` inside an
        ``HPacker`` of multiple ``TextArea``s so each band's value
        keeps its own colour.
        """
        from matplotlib.offsetbox import HPacker, TextArea

        from .readout_legend import build_overlay_legend_rows

        packers: list = []
        meta: list = []
        slot_key = self._slot_key_for_axes(ax)
        scope = self._READOUT_SCOPE_FOR_SLOT.get(str(slot_key), "main")
        mgr = getattr(self, "_indicator_manager", None)
        if mgr is None:
            return packers, meta
        try:
            interval = self.interval_var.get()
        except Exception:  # noqa: BLE001
            interval = ""
        try:
            rows = build_overlay_legend_rows(
                mgr, scope, interval, theme_text=theme["text"])
        except Exception:  # noqa: BLE001
            return packers, meta
        if not rows:
            return packers, meta
        ps_match, _kind = self._find_indicator_panel_for_axes(ax)
        overlay_lines = {}
        if ps_match is not None:
            state = ps_match.get("ind_state")
            overlay_lines = getattr(state, "overlay_lines", {}) or {}
        muted = theme.get("muted") or theme.get("axis") or "#888888"
        for row in rows:
            visible = row.visible
            label_color = theme["text"] if visible else muted
            # Prefix TextArea: "IndicatorName(params) " in neutral colour.
            children: list = [TextArea(
                f"{row.label} ",
                textprops=dict(
                    color=label_color, fontsize=9, family="monospace",
                ),
            )]
            output_metas: list[dict] = []
            for seg in row.outputs:
                seg_color = seg.color if visible else muted
                # Optional band-name TextArea for multi-output rows
                # ("upper" / "middle" / "lower" prefix per value).
                if seg.key_label:
                    children.append(TextArea(
                        f"{seg.key_label} ",
                        textprops=dict(
                            color=seg_color,
                            fontsize=9, family="monospace",
                        ),
                    ))
                # The value placeholder — updated live by
                # :meth:`_update_readout`. Trailing space gives a
                # consistent gap before the next band's label.
                value_ta = TextArea(
                    "  ",   # placeholder until first hover update
                    textprops=dict(
                        color=seg_color,
                        fontsize=9, family="monospace",
                    ),
                )
                children.append(value_ta)
                # Trailing inter-segment spacer for multi-output rows.
                if len(row.outputs) > 1:
                    children.append(TextArea(
                        " ",
                        textprops=dict(
                            color=label_color, fontsize=9, family="monospace",
                        ),
                    ))
                line = None
                if visible:
                    line = overlay_lines.get(row.config_id, {}).get(
                        seg.output_key,
                    )
                output_metas.append({
                    "output_key": seg.output_key,
                    "color": seg_color,
                    "key_label": seg.key_label,
                    "line": line,
                    "value_textarea": value_ta,
                })
            container = HPacker(
                children=children, align="center", pad=0, sep=0,
            )
            packers.append(container)
            meta.append({
                "config_id": row.config_id,
                "label": row.label,
                "visible": visible,
                "container": container,
                "outputs": output_metas,
            })
        return packers, meta

    def _dispatch_hover(self, event) -> None:
        """Hit-test candles + volume bars, update the tooltip/crosshair."""
        if event.inaxes is None:
            self._hide_overlays()
            self._reset_drawing_hover_cursor()
            self._reset_pane_label_hover_cursor()
            return
        # Drawings: hovering a horizontal line swaps the cursor to a
        # vertical double-arrow so the user knows it's hit-testable.
        # The actual click intercepts live in _on_button_press /
        # _on_button_release; this is purely a visual affordance.
        try:
            self._update_drawing_hover_cursor(event)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._update_pane_label_hover_cursor(event)
        except Exception:  # noqa: BLE001
            pass
        ax = event.inaxes
        entry = self._ax_candle_map.get(ax)
        if entry is None:
            self._hide_overlays()
            return
        candles, kind, offset = entry
        # Figure out which slot this axes belongs to so we can gate by
        # render_start/render_end (spec §11.1).
        slot_state = None
        slot_key_hit = None
        for slot_key, ps in self._panel_state.items():
            if ps.get("price_ax") is ax or ps.get("vol_ax") is ax:
                slot_state = ps
                slot_key_hit = slot_key
                break
        # Remember the last hovered slot so click-to-type / watchlist
        # double-click can route to whichever panel the user was last
        # looking at (mirrors the click-to-type slot memory).
        if slot_key_hit is not None:
            self._last_hovered_slot = slot_key_hit
        rs = slot_state.get("render_start", 0) if slot_state else 0
        re_ = slot_state.get("render_end", len(candles)) if slot_state else len(candles)

        # Refresh the top-left OHLCV/%change readout for every price
        # panel based on the cursor's x position (sharex propagates the
        # same xdata across primary/compare/volume axes, so a cursor on
        # the volume panel still updates the price readout above it).
        #
        # qw-hover-cache: skip the readout string-churn (≈5 OHLCV f-strings
        # + one per-indicator value format per price pane, allocated fresh
        # every 16ms tick) when the cursor is still over the SAME sealed
        # bar as the previous hover. Sealed bars are immutable, so the
        # rendered strings are byte-identical — only the crosshair (updated
        # later) actually moves. Every axes registers offset 0
        # (``_ax_candle_map``), so ``round(xdata)`` is the shared bar index
        # across all panes; an unchanged index means no box would repaint.
        # The forming/last bar streams in place, so it is never cached
        # (``ro_idx < len(candles) - 1``); off-chart / gap hovers key as
        # ``None`` and always refresh.
        ro_idx = None
        if event.xdata is not None:
            ri = int(round(event.xdata - offset))
            if 0 <= ri < len(candles):
                ro_idx = ri
        ro_key = (
            (id(candles), ro_idx)
            if ro_idx is not None and ro_idx < len(candles) - 1
            else None
        )
        if ro_key is None or ro_key != getattr(self, "_last_readout_key", None):
            self._update_readout(event.xdata)
            self._last_readout_key = ro_key

        if event.xdata is None or event.ydata is None:
            self._update_crosshair(ax, None, None)
            self._hide_hover_only()
            self._blit_overlays()
            return

        # Event-glyph hover (historical earnings / dividends overlay).
        # Checked BEFORE the candle hit-test so glyphs sitting in the
        # bottom row of the price pane win over the underlying bar's
        # OHLCV pop-up. Volume axes are never glyph hosts, so this
        # only applies on price panes.
        if kind == "price":
            glyph_tip = self._check_event_glyph_hit(slot_state, ax, event)
            if glyph_tip:
                self._update_crosshair(ax, event.xdata, event.ydata)
                self._show_hover_with_text(ax, event, glyph_tip)
                return

        idx = int(round(event.xdata - offset))
        if idx < 0 or idx >= len(candles):
            self._update_crosshair(ax, event.xdata, event.ydata)
            self._hide_hover_only()
            self._blit_overlays()
            return
        if idx < rs or idx >= re_:
            self._update_crosshair(ax, event.xdata, event.ydata)
            self._hide_hover_only()
            self._blit_overlays()
            return
        c = candles[idx]
        if c.is_gap:
            self._update_crosshair(ax, event.xdata, event.ydata)
            self._hide_hover_only()
            self._blit_overlays()
            return
        if abs(event.xdata - (idx + offset)) > 0.3:
            self._update_crosshair(ax, event.xdata, event.ydata)
            self._hide_hover_only()
            self._blit_overlays()
            return
        # Y-axis hit test. In HA display mode the HA bar's [low, high]
        # range is a strict superset of the real bar's, so we hit-test
        # against the displayed candle list (cached by ``_draw_slice``
        # on ``ps['display_candles']``) when available. When HA is off
        # this is the same list as ``candles``, so behavior is
        # unchanged. Volume axes are HA-irrelevant — volume bars are
        # always real.
        if kind == "price":
            display_candles = (
                slot_state.get("display_candles") if slot_state else None
            )
            hit_c = (
                display_candles[idx]
                if display_candles is not None
                and 0 <= idx < len(display_candles)
                else c
            )
            hit = (hit_c.low <= event.ydata <= hit_c.high)
        else:  # volume
            hit = (0 <= event.ydata <= c.volume)
        if not hit:
            self._update_crosshair(ax, event.xdata, event.ydata)
            self._hide_hover_only()
            self._blit_overlays()
            return
        # Show hover + crosshair together.
        self._update_crosshair(ax, event.xdata, event.ydata)
        self._show_hover(ax=ax, event=event, candles=candles, idx=idx)

    def _show_hover(self, ax=None, event=None, candles=None, idx=None) -> None:
        if ax is None or candles is None or idx is None or self._hover_ann is None:
            self._hover_visible = False
            return
        c = candles[idx]
        offset = self._ax_candle_map.get(ax, (None, None, 0))[2]
        txt = (
            f"{self._format_candle_date(c)}\n"
            f"O {c.open:,.2f}  H {c.high:,.2f}\n"
            f"L {c.low:,.2f}  C {c.close:,.2f}\n"
            f"Vol {fmt_volume(c.volume)}"
        )
        ind_lines = self._indicator_lines_at(ax, idx)
        if ind_lines:
            txt = txt + "\n" + "\n".join(ind_lines)
        # Reparent annotation onto the current axes if it drifted.
        try:
            if self._hover_ann.axes is not ax:
                self._hover_ann.remove()
                theme = self._theme
                self._hover_ann = ax.annotate(
                    "", xy=(0, 0), xytext=(12, 12),
                    textcoords="offset points", ha="left", va="bottom",
                    bbox=dict(boxstyle="round",
                              fc=theme["tooltip_bg"], ec=theme["spine"],
                              alpha=0.95),
                    color=theme["tooltip_fg"],
                    fontsize=8, animated=True, visible=False, zorder=9,
                )
        except Exception:  # noqa: BLE001
            pass
        try:
            self._hover_ann.xy = (idx + offset, event.ydata)
            self._hover_ann.set_text(txt)
            # Direction-flip based on figure-fraction (spec §11.3).
            fw, fh = self._figure.bbox.width, self._figure.bbox.height
            rx = (event.x or 0) / max(1, fw)
            ry = (event.y or 0) / max(1, fh)
            ha = "right" if rx >= 0.8 else "left"
            va = "top" if ry >= 0.6 else "bottom"
            dx = -12 if ha == "right" else 12
            dy = -12 if va == "top" else 12
            self._hover_ann.set_ha(ha); self._hover_ann.set_va(va)
            self._hover_ann.set_position((dx, dy))
            self._hover_ann.set_visible(True)
        except Exception:  # noqa: BLE001
            return
        self._hover_visible = True
        self._blit_overlays()

    def _hide_hover(self) -> None:
        self._hide_hover_only()
        self._blit_overlays()

    def _hide_hover_only(self) -> None:
        if self._hover_ann is not None:
            try:
                self._hover_ann.set_visible(False)
            except Exception:  # noqa: BLE001
                pass
        self._hover_visible = False

    # ---- event-glyph hover (earnings / dividends overlay) -------------
    def _check_event_glyph_hit(self, slot_state, ax, event):
        """Return the tooltip string when the cursor is over an event glyph.

        Hit-test geometry:

        * Glyphs render at axes-fraction Y = 0.025 (see
          :mod:`gui.events_overlay._GLYPH_Y`). The hit zone is the
          bottom ~10% of the price axis — outside that band the cursor
          can never be on a glyph.
        * Inside that band, X is compared in display pixels via
          ``ax.transData.transform``. The threshold is the
          ``events_hover_hit_px`` tunable (default 8 px).
        * The right-edge "Next earn T-N" forward badge has no data-X
          anchor — instead the cursor is considered "on the badge" when
          its X is in the rightmost few axes-fraction columns and the Y
          is in the bottom band.

        Returns ``None`` when no glyph qualifies. The candle/volume
        hover path then resumes normal behavior.
        """
        if slot_state is None or ax is None or event is None:
            return None
        meta = slot_state.get("event_hit_meta") or []
        badge_tip = slot_state.get("event_badge_tooltip") or ""
        if not meta and not badge_tip:
            return None
        px_cursor = event.x
        py_cursor = event.y
        if px_cursor is None or py_cursor is None:
            return None
        try:
            bb = ax.get_window_extent()
        except Exception:  # noqa: BLE001
            return None
        if bb.width <= 0 or bb.height <= 0:
            return None
        ax_x_frac = (px_cursor - bb.x0) / bb.width
        ax_y_frac = (py_cursor - bb.y0) / bb.height
        # Cursor must be in the bottom glyph band.
        if ax_y_frac < 0.0 or ax_y_frac > 0.10:
            return None
        # Right-edge forward badge takes priority when both could match.
        if badge_tip and ax_x_frac >= 0.92:
            return badge_tip
        try:
            from .. import defaults as _defs
            hit_px = int(_defs.get("events_hover_hit_px") or 8)
        except Exception:  # noqa: BLE001
            hit_px = 8
        best = None
        best_dx = None
        for x_data, _kind, tooltip in meta:
            try:
                px_glyph, _ = ax.transData.transform((float(x_data), 0.0))
            except Exception:  # noqa: BLE001
                continue
            dx = abs(px_glyph - px_cursor)
            if dx > hit_px * 1.5:
                continue
            if best_dx is None or dx < best_dx:
                best = tooltip
                best_dx = dx
        return best

    def _show_hover_with_text(self, ax, event, text: str) -> None:
        """Show the hover annotation anchored at the cursor with ``text``.

        Sibling of :meth:`_show_hover` for event-glyph tooltips: skips
        the candle/indicator readout assembly and uses the supplied
        free-form string directly. Reparents the annotation when the
        cursor crossed to a new axes (same logic as ``_show_hover``).
        """
        if ax is None or event is None or self._hover_ann is None:
            self._hover_visible = False
            return
        # Reparent annotation to current axes if drifted.
        try:
            if self._hover_ann.axes is not ax:
                self._hover_ann.remove()
                theme = self._theme
                self._hover_ann = ax.annotate(
                    "", xy=(0, 0), xytext=(12, 12),
                    textcoords="offset points", ha="left", va="bottom",
                    bbox=dict(boxstyle="round",
                              fc=theme["tooltip_bg"], ec=theme["spine"],
                              alpha=0.95),
                    color=theme["tooltip_fg"],
                    fontsize=8, animated=True, visible=False, zorder=9,
                )
        except Exception:  # noqa: BLE001
            pass
        try:
            self._hover_ann.xy = (event.xdata, event.ydata)
            self._hover_ann.set_text(text)
            fw, fh = self._figure.bbox.width, self._figure.bbox.height
            rx = (event.x or 0) / max(1, fw)
            ry = (event.y or 0) / max(1, fh)
            ha = "right" if rx >= 0.8 else "left"
            va = "top" if ry >= 0.6 else "bottom"
            dx = -12 if ha == "right" else 12
            dy = -12 if va == "top" else 12
            self._hover_ann.set_ha(ha); self._hover_ann.set_va(va)
            self._hover_ann.set_position((dx, dy))
            self._hover_ann.set_visible(True)
        except Exception:  # noqa: BLE001
            return
        self._hover_visible = True
        self._blit_overlays()

    # ---- indicator hover readout (Phase 2b) ---------------------------
    def _indicator_lines_at(self, ax, idx):
        """Return formatted ``"NAME: value"`` strings for indicators on ``ax``.

        Walks the panel-state attached to the hovered axes and reads
        ``ydata`` from the already-rendered ``Line2D`` artists rather
        than recomputing — so this is O(num_visible_indicator_lines)
        per hover (cheap; the ydata arrays are NumPy slices).

        * On a price axes: every overlay indicator (SMA, EMA, BBands…)
          assigned to that panel's scope contributes one line per
          output key.
        * On a non-overlay pane axes: only the indicator that owns the
          pane (e.g. RSI on its own pane) contributes its outputs.

        Multi-output indicators (e.g. Bollinger Bands → upper/middle/
        lower) prefix the output key after the display_name. Single-
        output indicators show just the display_name.

        NaN / out-of-range values are skipped silently (early bars
        where the indicator isn't yet defined).
        """
        out = []
        ps_match, kind = self._find_indicator_panel_for_axes(ax)
        if ps_match is None:
            return out
        state = ps_match.get("ind_state")
        if state is None:
            return out
        mgr = getattr(self, "_indicator_manager", None)
        if mgr is None:
            return out
        if kind == "price":
            items = list(state.overlay_lines.items())
        else:
            target_cid = next(
                (cid for cid, pa in state.panes.items() if pa is ax),
                None,
            )
            if target_cid is None:
                return out
            items = [(target_cid, state.pane_lines.get(target_cid, {}))]
        for cid, lines in items:
            if not lines:
                continue
            cfg = mgr.get(cid)
            if cfg is None or not cfg.visible:
                continue
            multi = len(lines) > 1
            for key, ln in lines.items():
                val = self._line_value_at(ln, idx)
                if val is None:
                    continue
                name = cfg.display_name or cfg.kind_id or key
                label = f"{name} {key}" if multi else name
                out.append(f"{label}: {val:,.2f}")
        return out

    def _find_indicator_panel_for_axes(self, ax):
        """Return ``(panel_state_dict, kind)`` for the panel owning ``ax``.

        ``kind`` is ``"price"`` if ``ax`` is the slot's price axes,
        ``"ind"`` if it's one of the slot's non-overlay indicator
        panes. Returns ``(None, None)`` when no slot claims the axes
        (e.g. volume axes, or before any render).
        """
        panel_state = getattr(self, "_panel_state", None) or {}
        for ps in panel_state.values():
            if ax is ps.get("price_ax"):
                return ps, "price"
            if ax in (ps.get("ind_axes") or []):
                return ps, "ind"
        return None, None

    @staticmethod
    def _line_value_at(line2d, idx):
        """Return ``float(ydata[idx])`` or ``None`` if invalid / NaN.

        Uses ``get_ydata()`` for ``Line2D`` (cheap NumPy view), and
        falls back to the ``_sc_y_data`` attribute for histogram
        ``LineCollection`` artists. This keeps hover readouts working
        for any indicator output kind without coupling to the
        underlying indicator's output dict.
        """
        try:
            sc_y = getattr(line2d, "_sc_y_data", None)
            if sc_y is not None:
                if idx < 0 or idx >= len(sc_y):
                    return None
                v = float(sc_y[idx])
                if not np.isfinite(v):
                    return None
                return v
            ydata = line2d.get_ydata()
            if idx < 0 or idx >= len(ydata):
                return None
            v = float(ydata[idx])
            if not np.isfinite(v):
                return None
            return v
        except Exception:  # noqa: BLE001
            return None

    def _update_crosshair(self, current_ax=None, xdata=None, ydata=None) -> None:
        # Vertical line on every axes; horizontal line on current only.
        self._crosshair_current_ax = current_ax
        for ax, pair in self._crosshair_artists.items():
            vline, hline = pair
            try:
                if xdata is not None:
                    vline.set_xdata([xdata, xdata])
                    vline.set_visible(True)
                else:
                    vline.set_visible(False)
                if ax is current_ax and ydata is not None:
                    hline.set_ydata([ydata, ydata])
                    hline.set_visible(True)
                else:
                    hline.set_visible(False)
            except Exception:  # noqa: BLE001
                pass
        # Floating value badge — only on the current axes (price or volume).
        for ax, label in self._price_label_artists.items():
            try:
                if ax is current_ax and ydata is not None:
                    label.xy = (1.0, ydata)
                    label.set_text(self._format_price_for_label(ax, ydata))
                    label.set_visible(True)
                else:
                    label.set_visible(False)
            except Exception:  # noqa: BLE001
                pass
        # Time badge — on the pane (slot) the cursor is over, anchored at
        # the bottom of THAT pane; every other pane's badge hides. Shown
        # only when xdata resolves to a real bar (filters cursor in
        # margin / between bars). In single-chart mode there is just the
        # one ("primary") badge.
        time_labels = getattr(self, "_time_label_artists", None) or {}
        current_slot = self._slot_key_for_axes(current_ax)
        time_text = None
        if xdata is not None and current_ax is not None:
            time_text = self._format_time_for_label(current_ax, xdata)
        for slot_key, time_label in time_labels.items():
            try:
                if slot_key == current_slot and time_text:
                    time_label.xy = (float(xdata), 0.0)
                    time_label.set_text(time_text)
                    time_label.set_visible(True)
                else:
                    time_label.set_visible(False)
            except Exception:  # noqa: BLE001
                pass

    def _format_price_for_label(self, ax, value) -> str:
        """Format ``value`` for the floating price badge.

        For price axes we force a fixed 2-decimal format with thousands
        separators so the badge reads as ``172.50`` / ``1,247.83`` etc.,
        regardless of what the axis tick formatter is rendering (some
        log-axis formatters truncate trailing zeros which made AMD's
        ``$172.5`` lose its second decimal). For volume axes we keep the
        major formatter's ``format_data_short`` so the badge matches
        the on-axis ticks (e.g. ``1.2M``). Other panes (custom indicator
        axes) fall through to the axis formatter and finally a 2-decimal
        fallback. Audit ``hover-price-2-decimals``.
        """
        kind = None
        try:
            entry = self._ax_candle_map.get(ax)
            if entry is not None:
                kind = entry[1]
        except Exception:  # noqa: BLE001
            kind = None
        if kind == "price":
            try:
                return f"{float(value):,.2f}"
            except Exception:  # noqa: BLE001
                return ""
        try:
            fmt = ax.yaxis.get_major_formatter()
            # ScalarFormatter exposes format_data_short which works for
            # arbitrary values without needing set_locs to have been
            # called first; FuncFormatter also supports it.
            if hasattr(fmt, "format_data_short"):
                txt = fmt.format_data_short(float(value)).strip()
                if txt:
                    return txt
            txt = fmt(float(value), None)
            if isinstance(txt, str) and txt.strip():
                return txt
        except Exception:  # noqa: BLE001
            pass
        try:
            return f"{float(value):,.2f}"
        except Exception:  # noqa: BLE001
            return ""

    def _format_time_for_label(self, ax, xdata) -> str:
        """Format the timestamp at ``xdata`` for the floating time badge.

        Resolves the bar at ``int(round(xdata))`` against the candle list
        registered for ``ax`` in ``_ax_candle_map``. Returns
        ``YYYY-MM-DD HH:MM`` for intraday bars (with the user's display
        timezone applied via :func:`formatting.format_dt`) and
        ``YYYY-MM-DD`` for daily / weekly / monthly bars. Returns ``""``
        when ``xdata`` falls between bars / before the first bar / after
        the last bar — the caller hides the badge on empty text.
        """
        from ..constants import is_intraday
        from ..formatting import format_dt
        try:
            entry = self._ax_candle_map.get(ax)
            candles = entry[0] if entry else None
            if not candles:
                return ""
            i = int(round(float(xdata)))
            if i < 0 or i >= len(candles):
                return ""
            ts = candles[i].date
        except Exception:  # noqa: BLE001
            return ""
        try:
            interval = self.interval_var.get()
        except Exception:  # noqa: BLE001
            interval = ""
        try:
            if interval and is_intraday(interval):
                return format_dt(ts, "%Y-%m-%d %H:%M",
                                 getattr(self, "_display_tz", ""))
            return ts.strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            try:
                return str(ts)
            except Exception:  # noqa: BLE001
                return ""

    # ---- top-left data readout (spec §11.6) ---------------------------
    def _update_readout(self, xdata=None) -> None:
        """Refresh every price-axes' top-left OHLCV/%change readout.

        Looks up the bar at the cursor's ``xdata`` on each price axes
        (panels share the x-scale via ``sharex``, so a cursor anywhere
        on the figure column updates the price readout above it). When
        ``xdata`` is ``None`` (cursor off the figure, post-render
        revival, or the bar at xdata is out-of-render-window / a gap),
        the readout falls back to the latest non-gap bar inside the
        rendered window so the strip is never blank.

        Format (single dense line, no ticker / no interval per UI spec):
            ``O 142.37  H 143.10  L 141.50  C 142.95  Vol 12.3M  +0.41%``
        Only the trailing ``%change`` segment is bull/bear coloured;
        the rest stays in the theme's neutral text colour.
        """
        # qw-hover-cache: a ``None`` call is the always-refresh path
        # (post-render artist rebuild, streaming revival, cursor-left).
        # Invalidate the hover gate so the next in-bar hover repaints the
        # strip against the fresh data / render generation.
        if xdata is None:
            self._last_readout_key = None
        for ax, box in self._readout_artists.items():
            try:
                entry = self._ax_candle_map.get(ax)
                if entry is None:
                    box.set_visible(False)
                    continue
                candles, _kind, offset = entry
                if not candles:
                    box.set_visible(False)
                    continue
                # Resolve the rendered window for this ax's slot.
                ps = None
                for _slot_key, st in self._panel_state.items():
                    if st.get("price_ax") is ax:
                        ps = st
                        break
                rs = ps.get("render_start", 0) if ps else 0
                re_ = ps.get("render_end", len(candles)) if ps else len(candles)

                idx = None
                if xdata is not None:
                    i = int(round(float(xdata) - offset))
                    if (rs <= i < re_ and 0 <= i < len(candles)
                            and not candles[i].is_gap):
                        idx = i
                if idx is None:
                    end = min(re_, len(candles))
                    start = max(0, rs)
                    for j in range(end - 1, start - 1, -1):
                        if 0 <= j < len(candles) and not candles[j].is_gap:
                            idx = j
                            break
                if idx is None:
                    box.set_visible(False)
                    continue

                c = candles[idx]
                # Walk backwards for the prior non-gap close so the pct
                # works on aligned/compare panels where Candle.gap slots
                # break the strict idx-1 sequence.
                prev_close = None
                for k in range(idx - 1, -1, -1):
                    if not candles[k].is_gap:
                        prev_close = candles[k].close
                        break

                if prev_close is not None and prev_close > 0:
                    pct = (c.close - prev_close) / prev_close * 100.0
                    pct_str = f"  {pct:+.2f}%"
                    pct_color = (_constants.BULL_COLOR if pct >= 0
                                 else _constants.BEAR_COLOR)
                else:
                    pct_str = ""
                    pct_color = self._theme["text"]

                main_str = (
                    f"O {c.open:,.2f}  H {c.high:,.2f}  "
                    f"L {c.low:,.2f}  C {c.close:,.2f}  "
                    f"Vol {fmt_volume(c.volume)}"
                )
                box._main_text.set_text(main_str)
                box._pct_text.set_text(pct_str)
                # TextArea has no public set_color; mutate the inner
                # Text artist directly. Matches the matplotlib examples
                # for offsetbox-styled overlays.
                try:
                    box._pct_text._text.set_color(pct_color)
                except Exception:  # noqa: BLE001
                    pass
                # Live per-indicator legend values (TradingView-style):
                # one row per indicator config; multi-output rows show
                # each band's value beside its key label in the band's
                # own colour. Hidden rows keep just their greyed name.
                for ind_meta in getattr(box, "_ind_rows", None) or ():
                    try:
                        outputs = ind_meta.get("outputs") or ()
                        for seg in outputs:
                            ta = seg.get("value_textarea")
                            if ta is None:
                                continue
                            line = seg.get("line")
                            val = (self._line_value_at(line, idx)
                                   if line is not None else None)
                            ta.set_text(
                                f"{val:,.2f} " if val is not None else "  ",
                            )
                    except Exception:  # noqa: BLE001
                        pass
                box.set_visible(True)
            except Exception:  # noqa: BLE001
                try:
                    box.set_visible(False)
                except Exception:  # noqa: BLE001
                    pass

    def _update_crosshair_pixels(self, current_ax=None, px=None, py=None) -> None:
        """Revive the crosshair after a re-render using cached pixel coords."""
        if current_ax is None or px is None or py is None:
            return
        try:
            inv = current_ax.transData.inverted()
            xdata, ydata = inv.transform((px, py))
        except Exception:  # noqa: BLE001
            return
        self._update_crosshair(current_ax, xdata, ydata)
        self._blit_overlays()

    def _blit_overlays(self) -> None:
        """Compose hover annotation + crosshair lines on top of ``_blit_bg``."""
        canvas = getattr(self, "_canvas", None)
        if canvas is None:
            return
        if self._blit_bg is None:
            # Background not captured yet; force a full redraw so the
            # draw_event handler can populate _blit_bg.
            try:
                canvas.draw_idle()
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            canvas.restore_region(self._blit_bg)
            for ax, (vline, hline) in self._crosshair_artists.items():
                if vline.get_visible():
                    ax.draw_artist(vline)
                if hline.get_visible():
                    ax.draw_artist(hline)
            # Price badge drawn AFTER hline so the crosshair doesn't cut
            # through it; it must visually occlude the baked y-tick labels.
            for ax, label in self._price_label_artists.items():
                if label.get_visible():
                    ax.draw_artist(label)
            # Time badges (one per pane) — same rationale as the price
            # badge but for the x-axis tick labels at the bottom of each
            # pane. Only the hovered pane's badge is visible at a time.
            for time_label in (
                getattr(self, "_time_label_artists", None) or {}
            ).values():
                if time_label is not None and time_label.get_visible():
                    ann_ax = time_label.axes
                    if ann_ax is not None:
                        ann_ax.draw_artist(time_label)
            # Top-left OHLCV / pct readout (spec §11.6) — always-on per
            # price axes. Drawn before the hover tooltip so a hover bbox
            # over the corner still wins z-order.
            for ax, box in self._readout_artists.items():
                if box.get_visible():
                    ax.draw_artist(box)
            if self._hover_ann is not None and self._hover_ann.get_visible():
                ann_ax = self._hover_ann.axes
                if ann_ax is not None:
                    ann_ax.draw_artist(self._hover_ann)
            canvas.blit(self._figure.bbox)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Live-tick blit fast path (cluster 1)
    # ------------------------------------------------------------------
    def _overlay_artist_ids(self) -> set[int]:
        """``id()`` set of the always-on / hover overlay artists.

        These are composited by :meth:`_blit_overlays` on top of
        ``_blit_bg`` every frame, so they MUST be excluded from the
        tick-blit data set — otherwise they would bake into the captured
        ``_blit_bg`` and become "stuck" (a crosshair that never clears).
        """
        ids: set[int] = set()
        for pair in (getattr(self, "_crosshair_artists", None) or {}).values():
            try:
                for a in pair:
                    if a is not None:
                        ids.add(id(a))
            except TypeError:
                pass
        for src in (
            getattr(self, "_price_label_artists", None),
            getattr(self, "_time_label_artists", None),
            getattr(self, "_readout_artists", None),
        ):
            for a in (src or {}).values():
                if a is not None:
                    ids.add(id(a))
        ha = getattr(self, "_hover_ann", None)
        if ha is not None:
            ids.add(id(ha))
        return ids

    def _collect_tick_blit_artists(self) -> list[tuple[Any, Any]]:
        """Return ``(axes, artist)`` pairs for every data artist to blit.

        Walks each slot's price / volume / indicator axes and collects all
        Collections, Line2Ds and Texts (candles, volume, indicators,
        reference levels, drawings, the live-price line + label) MINUS the
        overlay artists from :meth:`_overlay_artist_ids`. Deduplicated by
        ``id()``. The live-price overlay artists are added explicitly as a
        belt-and-braces in case a future matplotlib drops annotations from
        ``ax.texts``.
        """
        exclude = self._overlay_artist_ids()
        out: list[tuple[Any, Any]] = []
        seen: set[int] = set()

        def _add(ax: Any, art: Any) -> None:
            if ax is None or art is None:
                return
            i = id(art)
            if i in seen or i in exclude:
                return
            seen.add(i)
            out.append((ax, art))

        ps_map = getattr(self, "_panel_state", None) or {}
        for ps in ps_map.values():
            axes = []
            for key in ("price_ax", "volume_ax"):
                ax = ps.get(key)
                if ax is not None:
                    axes.append(ax)
            for ax in ps.get("indicator_axes", []) or []:
                if ax is not None:
                    axes.append(ax)
            for ax in axes:
                for coll in list(getattr(ax, "collections", []) or []):
                    _add(ax, coll)
                for ln in list(getattr(ax, "lines", []) or []):
                    _add(ax, ln)
                for tx in list(getattr(ax, "texts", []) or []):
                    _add(ax, tx)
        overlay = getattr(self, "_live_price_overlay", None)
        if overlay is not None:
            for entry in (getattr(overlay, "_artists", None) or {}).values():
                try:
                    line, label = entry
                except (TypeError, ValueError):
                    continue
                for art in (line, label):
                    if art is not None:
                        _add(getattr(art, "axes", None), art)
        return out

    def _paint_tick_frame(self, slot: str = "primary") -> bool:
        """Blit the data artists onto a data-less background (ghost-free).

        The forming bar shares a Collection with every sealed bar, so a
        naive "restore full bg + redraw" ghosts when the bar's body
        shrinks. Instead we keep ``_tick_blit_bg`` — a snapshot of the
        figure with ALL data artists hidden (pure axes decorations) — and
        redraw the data on top each tick. No ghost because the background
        never contained the data.

        Returns ``True`` if the frame was painted via blit; ``False`` to
        tell the caller to fall back to ``canvas.draw_idle()``.
        """
        canvas = getattr(self, "_canvas", None)
        figure = getattr(self, "_figure", None)
        if canvas is None or figure is None:
            return False
        data = self._collect_tick_blit_artists()
        if not data:
            return False
        try:
            if self._tick_blit_bg is None:
                # Seed the data-less background: hide every data artist,
                # do ONE suppressed full draw, snapshot, then restore
                # visibility. ``_suspend_draw_capture`` keeps the draw_event
                # handler from clobbering ``_blit_bg`` with this hidden frame.
                vis: list[tuple[Any, bool]] = []
                for _ax, art in data:
                    try:
                        vis.append((art, bool(art.get_visible())))
                        art.set_visible(False)
                    except Exception:  # noqa: BLE001
                        pass
                self._suspend_draw_capture = True
                try:
                    canvas.draw()
                    self._tick_blit_bg = canvas.copy_from_bbox(figure.bbox)
                finally:
                    self._suspend_draw_capture = False
                    for art, was in vis:
                        try:
                            art.set_visible(was)
                        except Exception:  # noqa: BLE001
                            pass
                if self._tick_blit_bg is None:
                    return False
            canvas.restore_region(self._tick_blit_bg)
            for ax, art in data:
                try:
                    ax.draw_artist(art)
                except Exception:  # noqa: BLE001
                    pass
            # The buffer now holds decorations + data (no overlays). Capture
            # it as the fresh hover/pan background, then let _blit_overlays
            # composite the always-on readout / crosshair on top and blit.
            self._blit_bg = canvas.copy_from_bbox(figure.bbox)
            self._blit_overlays()
            self._tick_blit_fires = getattr(self, "_tick_blit_fires", 0) + 1
            return True
        except Exception:  # noqa: BLE001
            # Any failure: drop the (possibly half-built) snapshot and ask
            # the caller to do a normal full redraw.
            self._tick_blit_bg = None
            return False

    def _hide_overlays(self) -> None:
        self._hide_hover_only()
        self._update_crosshair(None, None, None)
        # Cursor left the chart — the readout falls back to the latest
        # bar (per UI spec the strip is always-on, never blank).
        self._update_readout(None)
        self._last_cursor_px = None
        # Cancel any typing preview when the cursor leaves the chart.
        self._blit_overlays()

    # ---- click-to-type (spec §12) -------------------------------------
    def _begin_click_to_type(self, ax) -> None:
        """Start typing mode on the slot whose axes was clicked."""
        slot = None
        for slot_key, ps in self._panel_state.items():
            if ps.get("price_ax") is ax or ps.get("vol_ax") is ax:
                slot = slot_key
                break
        if slot is None:
            slot = "primary"
        self._typing_target = slot
        self._last_clicked_slot = slot
        self._typing_buffer = ""
        self._refresh_typing_preview()

    def _refresh_typing_preview(self) -> None:
        """Render the grey in-chart preview text for the current typing buffer."""
        # Tear down previous preview artists.
        for art in list(self._typing_preview_artists.values()):
            try:
                art.remove()
            except Exception:  # noqa: BLE001
                pass
        self._typing_preview_artists = {}
        if self._typing_target is None:
            try:
                self._canvas.draw_idle()
            except Exception:  # noqa: BLE001
                pass
            return
        ps = self._panel_state.get(self._typing_target, {})
        ax = ps.get("price_ax")
        if ax is None:
            return
        try:
            art = ax.text(
                0.5, 0.5, self._typing_buffer or "",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=56, fontweight="bold",
                color=self._theme["text"], alpha=0.55, zorder=6,
            )
            self._typing_preview_artists[self._typing_target] = art
            self._canvas.draw_idle()
        except Exception:  # noqa: BLE001
            pass

    def _commit_click_to_type(self) -> None:
        """Commit the typing buffer: set the slot's ticker StringVar + reload."""
        target = self._typing_target
        buf = (self._typing_buffer or "").strip().upper()
        self._typing_target = None
        self._typing_buffer = ""
        self._refresh_typing_preview()
        if not buf or target is None:
            return
        if target == "compare":
            self.compare_ticker_var.set(buf)
        else:
            self.ticker_var.set(buf)
        self._schedule_reload(delay_ms=0)

    def _cancel_click_to_type(self) -> None:
        self._typing_target = None
        self._typing_buffer = ""
        self._refresh_typing_preview()

    # ---- drawings: drag-to-move horizontal lines -----------------------
    #
    # Click-and-drag on a horizontal line moves its price level to
    # where the cursor is when the button is released, snapped to the
    # nearest $0.01. The state machine: B1 press on a line sets
    # ``_drawing_drag_state``; motion updates a preview; B1 release
    # commits via ``store.update(id, price=new_price)`` and triggers
    # the fast-path redraw. Pan is suppressed while the drag is active.

    def _maybe_begin_drawing_drag(self, event) -> bool:
        """Start a drawing drag if B1 pressed on a horizontal line.

        Returns True if a drag was initiated (caller should suppress
        pan). False if the click missed every line.
        """
        hit, _ticker = self._pick_drawing_at_event(event)
        if hit is None:
            return False
        # Record state for the drag gesture.
        ax = event.inaxes
        # Resolve the slot key for this axes.
        slot_key = None
        for key, ps in self._panel_state.items():
            if ps.get("price_ax") is ax:
                slot_key = key
                break
        self._drawing_drag_state = {
            "drawing": hit,
            "ax": ax,
            "slot_key": slot_key,
            "start_y": event.y,
            "start_price": hit.price,
        }
        # Set cursor to indicate drag.
        try:
            self._canvas.get_tk_widget().configure(cursor="sb_v_double_arrow")
        except Exception:  # noqa: BLE001
            pass
        return True

    def _drawing_drag_motion(self, event) -> None:
        """Update the drawing preview during a drag gesture."""
        st = getattr(self, "_drawing_drag_state", None)
        if st is None:
            return
        ax = st["ax"]
        drawing = st["drawing"]
        if ax is None or event.y is None:
            return
        # Convert pixel y to data y on the original axes.
        try:
            _x, y_data = ax.transData.inverted().transform((event.x, event.y))
        except Exception:  # noqa: BLE001
            return
        # Snap to nearest cent.
        snapped = round(float(y_data), 2)
        # Live-update the drawing in the store for immediate visual feedback.
        store = getattr(self, "_drawings", None)
        if store is None:
            return
        try:
            store.update(drawing.id, price=snapped)
        except Exception:  # noqa: BLE001
            pass

    def _maybe_end_drawing_drag(self, event) -> bool:
        """Commit the final price on B1 release. Returns True if a drag
        was active and committed."""
        st = getattr(self, "_drawing_drag_state", None)
        if st is None:
            return False
        self._drawing_drag_state = None
        # Restore cursor.
        try:
            self._canvas.get_tk_widget().configure(cursor="")
            self._drawing_hover_cursor_active = False
        except Exception:  # noqa: BLE001
            pass
        ax = st["ax"]
        drawing = st["drawing"]
        slot_key = st["slot_key"]
        if ax is None or event.y is None:
            return True
        # Convert final pixel y to data y.
        try:
            _x, y_data = ax.transData.inverted().transform((event.x, event.y))
        except Exception:  # noqa: BLE001
            return True
        # Compute the snapped price using the full snap logic (grid +
        # optional OHLC magnet) for the final commit.
        snap_fn = getattr(self, "_compute_snapped_drawing_price", None)
        if callable(snap_fn) and slot_key is not None:
            try:
                snapped = snap_fn(ax, slot_key, float(y_data), float(event.y))
            except Exception:  # noqa: BLE001
                snapped = round(float(y_data), 2)
        else:
            snapped = round(float(y_data), 2)
        # Final commit.
        store = getattr(self, "_drawings", None)
        if store is not None:
            try:
                store.update(drawing.id, price=snapped)
            except Exception:  # noqa: BLE001
                pass
        return True

    # ---- drawings (horizontal lines) hit-test bridge ------------------
    #
    # These helpers route mpl press/release/hover events to the
    # drawings subsystem (model + store + render). They are kept on
    # the interaction mixin (rather than on ChartApp) because the
    # event dispatch is part of the interaction surface; the actual
    # dialog/registry/menu construction lives on ChartApp.
    #
    # All four helpers swallow exceptions: a broken drawings layer
    # must never leave the chart in an unusable state. They early-
    # return when ``self._drawings`` is missing so unit tests of the
    # interaction mixin without a DrawingStore wired in still pass.

    def _pick_drawing_at_event(self, event):
        """Return the closest ``Drawing`` under the cursor or ``None``.

        Uses the price-axes-only hit-test from
        :func:`tradinglab.drawings.render.pick_drawing` (5px display-
        coord threshold at 96 DPI; scaled internally on HiDPI/4K
        displays — audit ``pick-tolerance-dpi``). Volume and
        indicator panes are excluded by checking the per-slot
        ``price_ax`` registry.

        Hover-rate cache (audit ``pick-event-throttle``): keyed by
        ``(slot_key, ticker, int(x), int(y), store_revision)`` so a
        stationary cursor that already triggered one linear scan
        never re-scans. The cache invalidates implicitly when the
        store's revision counter bumps (any add/remove/update/clear/
        loaded) and explicitly when slot, ticker, or pixel coords
        change. Single-entry cache keeps memory bounded.
        """
        store = getattr(self, "_drawings", None)
        if store is None or event.inaxes is None:
            return None, None
        ax = event.inaxes
        slot_key = None
        for key, ps in self._panel_state.items():
            if ps.get("price_ax") is ax:
                slot_key = key
                break
        if slot_key is None:
            return None, None
        ticker = self._slot_symbol(slot_key) if hasattr(
            self, "_slot_symbol") else None
        if not ticker:
            return None, None
        # Fast path: bucket is empty, skip the linear scan AND the
        # list-allocation that ``store.list`` would do.
        try:
            count = store.count(ticker)
        except Exception:  # noqa: BLE001
            count = -1
        if count == 0:
            return None, ticker
        # Cache lookup. Pixel coords are quantized to ints (sub-
        # pixel jitter from matplotlib motion-notify events doesn't
        # change hit-test outcome since tol_px is 5).
        x_px = float(event.x) if event.x is not None else 0.0
        y_px = float(event.y) if event.y is not None else 0.0
        try:
            revision = store.revision()
        except Exception:  # noqa: BLE001
            revision = -1
        cache_key = (slot_key, ticker, int(x_px), int(y_px), revision)
        cached = getattr(self, "_pick_cache", None)
        if cached is not None and cached[0] == cache_key:
            return cached[1], ticker
        try:
            drawings = store.list(ticker)
        except Exception:  # noqa: BLE001
            return None, None
        if not drawings:
            self._pick_cache = (cache_key, None)
            return None, ticker
        try:
            from ..drawings.render import pick_drawing
            hit = pick_drawing(drawings, ax, x_px, y_px, tol_px=5.0)
        except Exception:  # noqa: BLE001
            return None, None
        self._pick_cache = (cache_key, hit)
        return hit, ticker

    def _maybe_handle_drawing_dblclick(self, event) -> bool:
        """Open the per-line edit dialog if the dblclick was on a line.

        Returns True iff a line was hit (so the caller can short-
        circuit the drilldown / pan dispatch). False otherwise.
        """
        hit, _ticker = self._pick_drawing_at_event(event)
        if hit is None:
            return False
        opener = getattr(self, "_open_drawing_dialog", None)
        if callable(opener):
            try:
                opener(hit.id)
            except Exception:  # noqa: BLE001
                pass
        return True

    def _maybe_handle_b3_click_menu(self, event) -> None:
        """Pop the per-line or canvas context menu on a B3 click-no-drag.

        Called from :meth:`_on_button_release` after the rubber-band
        ``_zoom_end`` short-circuit has already removed the rectangle.
        Routing rule:

        * release was on a drawing → per-line menu (Edit / Delete).
        * release was on a price axes background → canvas menu
          (Add Horizontal Line Here / Copy Price / Copy Price + Time
          / Reset Zoom / Snapshot Chart / Clear All Drawings on
          <TICKER>).
        * release was on a non-price-axes (volume, indicator pane) →
          no menu (matches the Alt+H placement-axes constraint).
        """
        if event.inaxes is None:
            return
        slot_key = None
        for key, ps in self._panel_state.items():
            if ps.get("price_ax") is event.inaxes:
                slot_key = key
                break
        if slot_key is None:
            return
        try:
            x_r = int(event.guiEvent.x_root)
            y_r = int(event.guiEvent.y_root)
        except Exception:  # noqa: BLE001
            return
        hit, ticker = self._pick_drawing_at_event(event)
        if hit is not None:
            show = getattr(self, "_show_drawing_context_menu", None)
            if callable(show):
                try:
                    show(hit.id, x_r, y_r)
                except Exception:  # noqa: BLE001
                    pass
            return
        show_canvas = getattr(self, "_show_chart_canvas_menu", None)
        if callable(show_canvas):
            try:
                show_canvas(slot_key, event, x_r, y_r)
            except Exception:  # noqa: BLE001
                pass

    def _update_drawing_hover_cursor(self, event) -> None:
        """Swap to ``sb_v_double_arrow`` while hovering a line."""
        store = getattr(self, "_drawings", None)
        if store is None:
            return
        hit, _ = self._pick_drawing_at_event(event)
        try:
            widget = self._canvas.get_tk_widget()
        except Exception:  # noqa: BLE001
            return
        current = getattr(self, "_drawing_hover_cursor_active", False)
        try:
            if hit is not None and not current:
                widget.configure(cursor="sb_v_double_arrow")
                self._drawing_hover_cursor_active = True
            elif hit is None and current:
                widget.configure(cursor="")
                self._drawing_hover_cursor_active = False
        except Exception:  # noqa: BLE001
            pass

    def _reset_drawing_hover_cursor(self) -> None:
        """Reset the drawing-hover cursor when the pointer leaves the axes."""
        if not getattr(self, "_drawing_hover_cursor_active", False):
            return
        try:
            self._canvas.get_tk_widget().configure(cursor="")
        except Exception:  # noqa: BLE001
            pass
        self._drawing_hover_cursor_active = False

    def _update_pane_label_hover_cursor(self, event) -> None:
        """Swap to ``hand2`` while hovering a clickable indicator pane label."""
        label, _config_id = self._pane_indicator_label_hit(event)
        hit = label is not None
        try:
            widget = self._canvas.get_tk_widget()
        except Exception:  # noqa: BLE001
            return
        current = getattr(self, "_pane_label_hover_cursor_active", False)
        try:
            if hit and not current:
                widget.configure(cursor="hand2")
                self._pane_label_hover_cursor_active = True
            elif not hit and current:
                if not getattr(self, "_drawing_hover_cursor_active", False):
                    widget.configure(cursor="")
                self._pane_label_hover_cursor_active = False
        except Exception:  # noqa: BLE001
            pass

    def _reset_pane_label_hover_cursor(self) -> None:
        """Reset the pane-label hover cursor when leaving the axes."""
        if not getattr(self, "_pane_label_hover_cursor_active", False):
            return
        try:
            if not getattr(self, "_drawing_hover_cursor_active", False):
                self._canvas.get_tk_widget().configure(cursor="")
        except Exception:  # noqa: BLE001
            pass
        self._pane_label_hover_cursor_active = False
