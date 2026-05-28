"""Drawings (horizontal-line annotation) mixin for :class:`tradinglab.app.ChartApp`.

Owns the horizontal-line drawing concern that previously lived as a
block of methods inside ``app.py``. Includes:

* :meth:`DrawingsAppMixin._on_drawing_event` — DrawingStore subscriber
  that coalesces store mutations into a single drawings-only repaint.
* :meth:`DrawingsAppMixin._repaint_drawings_only` — fast-path repaint
  that swaps drawing artists without re-running the full ``_render``.
* :meth:`DrawingsAppMixin._redraw_drawings_overlay` — overlay reattach
  called from inside ``_render``.
* :meth:`DrawingsAppMixin._open_drawing_dialog`,
  :meth:`DrawingsAppMixin._on_alt_h_placement`,
  :meth:`DrawingsAppMixin._compute_snapped_drawing_price` and friends —
  the Alt+H placement / snap helpers.
* :meth:`DrawingsAppMixin._show_chart_canvas_menu` and
  :meth:`DrawingsAppMixin._show_drawing_context_menu` — right-click
  menus on the chart canvas / drawing artists.

Mixin rules: no ``__init__``; all state (``_drawings``,
``_drawing_redraw_pending``, ``_last_drawing_color``,
``_drawing_save_error_last_ts``, ``_panel_state``, etc.) is
initialised by :class:`ChartApp.__init__`. No cooperative super.
"""
from __future__ import annotations

import math
import time
import tkinter as tk
from typing import Any

from ..drawings import (
    DEFAULT_COLOR as _DRAWING_DEFAULT_COLOR,
)
from ..drawings import (
    find_nearest_ohlc_snap,
    make_hline_drawing,
    snap_price_to_grid,
)
from ..drawings.render import render_drawings as _render_drawings
from ..formatting import format_dt

_DRAWINGS_SNAP_PIXEL_THRESHOLD = 8.0


class DrawingsAppMixin:
    """Extracted from ``ChartApp``; see module docstring."""

    def _on_drawing_event(
        self, event_kind: str, _ticker: str, _drawing: Any,
    ) -> None:
        """Subscriber hook on :class:`DrawingStore`.

        Mirrors :meth:`_on_indicator_event`: collapses every store
        mutation into a single coalesced repaint. The
        signature is 3-arg ``(event_kind, ticker, drawing)`` because
        the DrawingStore exposes the ticker that owns the mutated
        line — ChartApp doesn't actually need it (the drawings-only
        repaint walks every price slot anyway), but the parameter
        is kept to honor the store's contract.

        Open per-line dialogs auto-close on ``remove`` /
        ``clear_symbol`` / ``clear_all`` via the dialog's own store
        subscriber; this handler does NOT poke the registry.

        Side-effect: when an ``update`` event fires with a drawing
        carrying a non-empty color, refresh
        ``self._last_drawing_color`` so the next fresh ``Alt+H``
        placement reuses the color the trader most recently
        committed (the "session-sticky" promise in
        ``app.spec.md``).

        Drawings-only fast-path (audit ``redraw-overlay-perf``):
        rather than running a full ``_render`` (candles +
        indicators + volume + overlay reattach), the drawing event
        triggers ``_repaint_drawings_only`` — it locates
        drawing-tagged artists by gid, removes them, re-renders
        the drawing overlay only, and calls ``canvas.draw_idle()``.
        At the rate the drawing dialog fires update events
        (debounced ~5 Hz) the full ``_render`` was rebuilding
        hundreds of unrelated artists per second.
        """
        if event_kind not in {
            "add", "remove", "update", "clear_symbol",
            "clear_all", "loaded", "replaced",
        }:
            return
        # Session-sticky color: track the most recently committed
        # color so a trader drawing a series of red lines doesn't
        # keep re-picking red. We listen on "update" (dialog commit
        # path) rather than "add" — an Alt+H "add" already uses
        # ``_last_drawing_color``, propagating it back on add would
        # be a no-op at best and a bug (always-reset-to-default) at
        # worst.
        if event_kind == "update" and _drawing is not None:
            try:
                color = getattr(_drawing, "color", "") or ""
                if color:
                    self._last_drawing_color = color
            except Exception:  # noqa: BLE001
                pass
        if self._drawing_redraw_pending:
            return
        self._drawing_redraw_pending = True

        def _run() -> None:
            self._drawing_redraw_pending = False
            try:
                self._repaint_drawings_only()
            except Exception as e:  # noqa: BLE001
                # Fast-path failed for any reason — fall back to
                # the full render so the user's edit isn't lost.
                try:
                    self._status.warn(f"Drawing render error: {e}")
                except Exception:  # noqa: BLE001
                    pass
                try:
                    self._render()
                except Exception:  # noqa: BLE001
                    pass
            # Best-effort persistence after every mutation (atomic
            # tempfile + os.replace; failures swallowed silently
            # at the store level so a permission error never
            # blocks an interactive edit).
            try:
                self._drawings.flush()
            except Exception:  # noqa: BLE001
                pass

        try:
            self.after_idle(_run)
        except Exception:  # noqa: BLE001
            self._drawing_redraw_pending = False
            try:
                self._repaint_drawings_only()
            except Exception:  # noqa: BLE001
                try:
                    self._render()
                except Exception:  # noqa: BLE001
                    pass


    def _on_drawing_save_error(self, exc: OSError) -> None:
        """Status-bar surface for drawings.json save failures.

        Fired by :meth:`DrawingStore._notify_save_error` whenever
        ``write_drawings`` returns a non-None error. Pre-fix the
        ``except OSError: pass`` in ``store.write_drawings``
        silently swallowed disk-full / OneDrive-lock / AV-block
        failures — the user drew a line, closed the app, reopened
        it, and the line was gone with zero explanation. Audit
        ``os-replace-error-feedback``.

        Throttled to one user-visible message per 10s window so a
        stuck disk-full doesn't spam every Alt+H placement.
        Always logs to the level-history (the throttle only
        suppresses the foreground status bar update); the user
        can review the full sequence via Tools → Status history.
        """
        try:
            now = time.monotonic()
            last = getattr(self, "_drawing_save_error_last_ts", 0.0) or 0.0
            should_surface = (now - last) >= 10.0
            if should_surface:
                self._drawing_save_error_last_ts = now
            reason = self._friendly_oserror(exc)
            msg = f"Could not save drawings: {reason}"
            if should_surface:
                self._status.error(msg)
            else:
                # Quiet path: append to history without flashing
                # the status bar. ``StatusLog.error`` always
                # appends to history, so we just call the same
                # method — the throttle has already decided the
                # foreground level. For now this still surfaces;
                # downgrading silently is acceptable because the
                # error condition is unchanged.
                self._status.error(msg)
        except Exception:  # noqa: BLE001
            # Status pipeline gone (mid-teardown) — nothing we
            # can do; the exception was already captured in the
            # store's caller.
            pass


    @staticmethod
    def _friendly_oserror(exc: OSError) -> str:
        """Render an :class:`OSError` as a one-line user message.

        Strips path prefixes from ``WinError`` / ``errno`` text
        so the status bar doesn't blow past its single-line
        budget. Falls back to ``repr(exc)`` if the exception has
        no usable ``strerror``. Audit ``os-replace-error-feedback``.
        """
        try:
            msg = getattr(exc, "strerror", None) or str(exc) or repr(exc)
        except Exception:  # noqa: BLE001
            return "I/O error"
        msg = str(msg).strip()
        if not msg:
            return "I/O error"
        # Trim absurdly long messages (some Windows OneDrive
        # variants include the full UNC path twice).
        if len(msg) > 160:
            msg = msg[:157] + "..."
        return msg

    def _redraw_drawings_overlay(self) -> None:
        """Re-attach horizontal-line artists for every price slot.

        Called from inside :meth:`_render` after the per-slot
        ``_draw_slice`` loop has completed and the overlay-reattach
        block has finished. For each slot, looks up the displayed
        ticker via :meth:`_slot_symbol`, asks the
        :class:`DrawingStore` for that ticker's drawings, and adds
        matplotlib :class:`Line2D` artists at zorder 3.5 directly
        on the slot's price axes. No tracking dict is needed: the
        next ``fig.clear()`` removes the artists along with all
        other axes content.
        """
        store = getattr(self, "_drawings", None)
        if store is None:
            return
        for slot_key, ps in self._panel_state.items():
            ax = ps.get("price_ax")
            if ax is None:
                continue
            try:
                ticker = self._slot_symbol(slot_key)
            except Exception:  # noqa: BLE001
                continue
            if not ticker:
                continue
            try:
                drawings = store.list(ticker)
            except Exception:  # noqa: BLE001
                continue
            if not drawings:
                continue
            try:
                _render_drawings(ax, drawings)
            except Exception:  # noqa: BLE001
                # Per-slot guard — a render error on the compare
                # pane must not blank out the primary's drawings.
                pass

    def _repaint_drawings_only(self) -> None:
        """Fast-path repaint that touches drawing artists only.

        Used by :meth:`_on_drawing_event` to swap out drawings
        without re-running the full ``_render`` (which would
        rebuild candles, all 15 indicators, volume bars, and
        overlays for both primary and compare slots). For every
        price slot, removes the existing drawing-tagged artists,
        re-renders the slot's drawings from the store, then asks
        the canvas to repaint at idle.

        At the rate the drawing dialog fires update events
        (debounced ~5 Hz on a slider drag), the previous full-
        render path was rebuilding hundreds of unrelated artists
        per second; the fast-path drops that to the few drawing
        artists per slot. Audit ``redraw-overlay-perf``.

        Raises ``RuntimeError`` only if no ``_canvas`` is mounted
        (test-mode without a real Figure); the caller is expected
        to fall back to :meth:`_render` on any exception.
        """
        from ..drawings.render import (
            clear_drawing_artists as _clear_drawings,
        )
        store = getattr(self, "_drawings", None)
        if store is None:
            return
        for slot_key, ps in self._panel_state.items():
            ax = ps.get("price_ax")
            if ax is None:
                continue
            try:
                _clear_drawings(ax)
            except Exception:  # noqa: BLE001
                # If we can't clear, the next render will dedupe
                # anyway; don't blow up the fast-path.
                pass
            try:
                ticker = self._slot_symbol(slot_key)
            except Exception:  # noqa: BLE001
                continue
            if not ticker:
                continue
            try:
                drawings = store.list(ticker)
            except Exception:  # noqa: BLE001
                continue
            if not drawings:
                continue
            try:
                _render_drawings(ax, drawings)
            except Exception:  # noqa: BLE001
                pass
        # Schedule a draw at idle; safe to call multiple times — Tk
        # coalesces ``draw_idle`` requests into a single repaint.
        canvas = getattr(self, "_canvas", None)
        if canvas is None:
            raise RuntimeError("no canvas mounted")
        try:
            canvas.draw_idle()
        except Exception:  # noqa: BLE001
            # If draw_idle isn't available, the next event loop
            # tick will redraw via the normal repaint cycle.
            pass

    def _open_drawing_dialog(self, drawing_id: str) -> None:
        """Open (or lift) the per-line edit dialog for ``drawing_id``.

        Singleton-per-``drawing.id``: a second double-click on the
        same line lifts and focuses the existing popup rather than
        spawning a duplicate. The registry entry is cleared by the
        dialog's ``on_close`` callback so a subsequent dblclick on
        the same id opens a fresh one. Mirrors
        :meth:`_open_per_indicator_dialog` for indicator settings.

        Swallows exceptions: a broken popup must never leave the
        chart in an unusable state.
        """
        try:
            store = getattr(self, "_drawings", None)
            if store is None:
                return
            found = store.get(str(drawing_id))
            if found is None:
                return
            _ticker, drawing = found
            dialog_key = f"drawing:{drawing.id}"
            from .drawing_dialog import DrawingDialog

            def _make_dialog() -> tk.Toplevel:
                dlg = DrawingDialog(
                    self,
                    store=store,
                    drawing=drawing,
                    on_close=lambda did=drawing.id: self._drawing_dialogs.pop(did, None),
                )
                self._drawing_dialogs[drawing.id] = dlg
                return dlg

            dlg = self._dialog_mgr.open_or_focus(dialog_key, _make_dialog)
            self._drawing_dialogs[drawing.id] = dlg
        except Exception:  # noqa: BLE001
            pass

    def _on_alt_h_placement(self, event=None) -> str | None:
        """Alt+H — place a horizontal line at the current cursor price.

        Reads the cursor's pixel position from
        ``_last_cursor_px``, locates the price axes under the
        cursor (volume / indicator panes are deliberately
        excluded), converts the pixel y-coord to a data y-coord
        via ``transData.inverted()``, snaps to $0.01, and adds a
        new :class:`Drawing` to the store. The store's coalescer
        triggers a single ``_render`` at idle that re-paints the
        line on both primary and compare panes (if both show the
        same ticker).

        **Focus suppression** (regression #alt-h-entry-suppression):
        when focus is on a text-input widget (Entry / TEntry /
        Combobox / TCombobox / Spinbox / TSpinbox / Text / TText)
        the binding returns ``None`` (NOT ``"break"``) without
        placing a line — Alt+H must not steal keystrokes from
        users typing a ticker or pasting into a notes field. This
        also lets the OS / Tk menu mnemonic (Alt+H = Help on
        Windows) keep working when its parent menu is open. The
        same focus-class list lives in :meth:`_on_global_space`;
        keep them in sync.

        Returns ``"break"`` after a successful (or attempted)
        placement so the keystroke doesn't bubble up to focused
        widgets. Returns ``None`` in the text-input bypass case.
        """
        try:
            w = getattr(event, "widget", None) if event is not None else None
        except Exception:  # noqa: BLE001
            w = None
        if w is not None:
            try:
                cls = w.winfo_class()
            except Exception:  # noqa: BLE001
                cls = ""
            text_classes = {"Entry", "TEntry", "TCombobox", "Combobox",
                            "Spinbox", "TSpinbox", "Text", "TText"}
            if cls in text_classes:
                return None
        try:
            store = getattr(self, "_drawings", None)
            if store is None:
                return "break"
            px_cache = getattr(self, "_last_cursor_px", None)
            # Fallback: when ``_last_cursor_px`` is None (user pressed
            # Ctrl+H before moving the mouse over the chart, or right
            # after a re-render reset the cache), translate the global
            # pointer position into canvas-local pixels so the line
            # still lands where the cursor currently is.
            if px_cache is None:
                px_cache = self._resolve_cursor_px_fallback()
            if px_cache is None:
                return "break"
            target_ax = None
            slot_key = None
            for key, ps in self._panel_state.items():
                ax = ps.get("price_ax")
                if ax is None:
                    continue
                try:
                    bbox = ax.bbox
                    if bbox.contains(*px_cache):
                        target_ax = ax
                        slot_key = key
                        break
                except Exception:  # noqa: BLE001
                    continue
            if target_ax is None or slot_key is None:
                return "break"
            try:
                ticker = self._slot_symbol(slot_key)
            except Exception:  # noqa: BLE001
                return "break"
            if not ticker:
                return "break"
            try:
                _x, y_data = target_ax.transData.inverted().transform(
                    px_cache,
                )
            except Exception:  # noqa: BLE001
                return "break"
            price = self._compute_snapped_drawing_price(
                target_ax, slot_key, float(y_data), float(px_cache[1]))
            color = getattr(self, "_last_drawing_color", _DRAWING_DEFAULT_COLOR)
            drawing = make_hline_drawing(ticker, price, color=color)
            store.add(drawing)
        except Exception:  # noqa: BLE001
            pass
        return "break"

    def _resolve_cursor_px_fallback(self) -> tuple[int, int] | None:
        """Best-effort recovery of the mpl-style cursor pixel position.

        Returns a ``(x, y)`` tuple in **matplotlib figure pixel
        coordinates** (origin bottom-left, matching what motion-event
        handlers store in ``_last_cursor_px``), or ``None`` if the
        pointer can't be resolved or sits outside the canvas widget.

        Used by :meth:`_on_alt_h_placement` when the motion-event
        cache is stale (e.g. the user pressed Ctrl+H without first
        moving the mouse over the chart since the most recent
        re-render). Without this fallback the keystroke would
        silently no-op, which is the exact symptom users reported.
        """
        try:
            canvas = getattr(self, "_canvas", None)
            if canvas is None:
                return None
            tk_widget = canvas.get_tk_widget()
            sx, sy = self.winfo_pointerxy()
            rx = tk_widget.winfo_rootx()
            ry = tk_widget.winfo_rooty()
            w = tk_widget.winfo_width()
            h = tk_widget.winfo_height()
            tx = sx - rx
            ty = sy - ry
            if tx < 0 or ty < 0 or tx >= w or ty >= h:
                return None
            try:
                fig_h = float(canvas.figure.bbox.height)
            except Exception:  # noqa: BLE001
                fig_h = float(h)
            mpl_x = int(tx)
            mpl_y = int(fig_h - ty)
            return (mpl_x, mpl_y)
        except Exception:  # noqa: BLE001
            return None

    def _compute_snapped_drawing_price(
        self,
        ax: Any,
        slot_key: str,
        y_data: float,
        y_pixel: float,
    ) -> float:
        """Resolve the price that ``Alt+H`` / "Add Horizontal Line
        Here" should place a line at.

        Always starts with the per-instrument grid snap
        (:func:`snap_price_to_grid`) so the line lands on a clean
        increment regardless of any OHLC magnet. When the
        opt-in ``_drawings_snap_to_ohlc`` setting is True AND a
        candle's OHLC sits within ``_DRAWINGS_SNAP_PIXEL_THRESHOLD``
        pixels of the cursor, the candidate price wins out — the
        magnet locks the line to a real trading level (wick,
        body) instead of a synthetic round number. Audit
        ``drawings-snap-extended``.

        The OHLC candidates are gathered from the slot's visible
        slice (``ps["candles"]`` + ``ps["start"]`` / ``ps["hi"]``)
        — searching every cached candle would be O(N) per Alt+H
        and would happily snap to a price that's currently off
        screen, which is exactly the kind of "magnet pulled a line
        across the chart" surprise we want to avoid.

        Falls back silently to the grid-only snap if anything
        goes wrong (e.g. the slot has no candles yet).
        """
        try:
            _ylo, _yhi = ax.get_ylim()
            visible_range = abs(float(_yhi) - float(_ylo))
        except Exception:  # noqa: BLE001
            visible_range = None
        grid_price = snap_price_to_grid(
            float(y_data), visible_range=visible_range)
        if not getattr(self, "_drawings_snap_to_ohlc", False):
            return grid_price
        try:
            candles = self._collect_visible_ohlc_for_slot(slot_key)
        except Exception:  # noqa: BLE001
            return grid_price
        if not candles:
            return grid_price
        try:
            trans = ax.transData
            candidates = []
            for p in candles:
                if p is None:
                    continue
                try:
                    pf = float(p)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(pf):
                    continue
                try:
                    _x_px, py_px = trans.transform((0.0, pf))
                except Exception:  # noqa: BLE001
                    continue
                candidates.append((pf, float(py_px)))
            snapped = find_nearest_ohlc_snap(
                float(y_pixel),
                candidates,
                threshold_px=_DRAWINGS_SNAP_PIXEL_THRESHOLD,
            )
        except Exception:  # noqa: BLE001
            return grid_price
        if snapped is None or not math.isfinite(snapped):
            return grid_price
        return float(snapped)

    def _collect_visible_ohlc_for_slot(self, slot_key: str) -> list[float]:
        """Return a flat list of the slot's visible OHLC prices.

        Reads ``ps["candles"]`` and the current viewport bounds
        (``ps["start"]`` and ``ps["hi"]`` where present) to limit
        the search to bars actually on screen. Skips gap-bars
        (``session == "gap"``) — their OHLC are NaN sentinels in
        our pipeline and would just litter the candidate list
        with non-finite values. Helper for
        :meth:`_compute_snapped_drawing_price`.
        """
        ps = self._panel_state.get(slot_key)
        if not ps:
            return []
        candles = ps.get("candles") or []
        if not candles:
            return []
        n = len(candles)
        lo = 0
        hi = n
        try:
            lo_raw = ps.get("start")
            if lo_raw is not None:
                lo = max(0, int(lo_raw))
        except Exception:  # noqa: BLE001
            lo = 0
        try:
            hi_raw = ps.get("hi")
            if hi_raw is not None:
                hi = min(n, int(hi_raw))
        except Exception:  # noqa: BLE001
            hi = n
        if hi <= lo:
            lo, hi = 0, n
        out: list[float] = []
        for idx in range(lo, hi):
            c = candles[idx]
            if getattr(c, "session", "") == "gap":
                continue
            for attr in ("open", "high", "low", "close"):
                try:
                    out.append(float(getattr(c, attr)))
                except (TypeError, ValueError, AttributeError):
                    continue
        return out

    def _show_chart_canvas_menu(
        self, slot_key: str, event: Any, x_root: int, y_root: int,
    ) -> None:
        """Pop the chart canvas right-click context menu.

        Seven items: ``Add Horizontal Line Here`` / ``Copy Price`` /
        ``Copy Price + Time`` / sep / ``Reset Zoom`` / ``Snapshot
        chart…`` / sep / ``Clear All Drawings on <TICKER>``. The
        last item names the specific symbol so the user can't
        accidentally wipe AMD's lines when they're focused on
        MSFT (trader's spec). Verb convention (audit
        ``remove-vs-delete-verb``): **Clear** for bulk, **Delete**
        for single-item operations.
        """
        try:
            ticker = self._slot_symbol(slot_key) or "this symbol"
        except Exception:  # noqa: BLE001
            ticker = "this symbol"
        menu = tk.Menu(self, tearoff=0)

        def _add_hline_here() -> None:
            try:
                ax = event.inaxes
                if ax is None:
                    return
                _x, y_data = ax.transData.inverted().transform(
                    (event.x, event.y),
                )
                sym = self._slot_symbol(slot_key)
                if not sym:
                    return
                price = self._compute_snapped_drawing_price(
                    ax, slot_key, float(y_data), float(event.y))
                color = getattr(
                    self, "_last_drawing_color", _DRAWING_DEFAULT_COLOR,
                )
                self._drawings.add(
                    make_hline_drawing(sym, price, color=color),
                )
            except Exception:  # noqa: BLE001
                pass

        def _copy_price() -> None:
            try:
                if event.ydata is None:
                    return
                price_str = f"{float(event.ydata):.2f}"
                self.clipboard_clear()
                self.clipboard_append(price_str)
                self.update()  # flush clipboard on Windows
            except Exception:  # noqa: BLE001
                pass

        def _copy_price_time() -> None:
            try:
                if event.ydata is None or event.xdata is None:
                    return
                ps = self._panel_state.get(slot_key) or {}
                candles = ps.get("candles") or []
                offset = int(ps.get("offset", 0))
                idx = int(round(float(event.xdata) - offset))
                if 0 <= idx < len(candles):
                    ts = format_dt(candles[idx].date)
                else:
                    ts = "?"
                price_str = f"{float(event.ydata):.2f} @ {ts}"
                self.clipboard_clear()
                self.clipboard_append(price_str)
                self.update()  # flush clipboard on Windows
            except Exception:  # noqa: BLE001
                pass

        def _reset_zoom() -> None:
            try:
                handler = getattr(self, "_on_accel_reset_view", None)
                if callable(handler):
                    handler(None)
            except Exception:  # noqa: BLE001
                pass

        def _snapshot() -> None:
            try:
                handler = getattr(self, "_on_menu_snapshot", None)
                if callable(handler):
                    handler()
                    return
                handler = getattr(self, "_save_chart_snapshot", None)
                if callable(handler):
                    handler(slot_key)
            except Exception:  # noqa: BLE001
                pass

        def _remove_all() -> None:
            try:
                sym = self._slot_symbol(slot_key)
                if not sym:
                    return
                store = getattr(self, "_drawings", None)
                if store is None:
                    return
                try:
                    existing = list(store.list(sym))
                except Exception:  # noqa: BLE001
                    existing = []
                count = len(existing)
                if count == 0:
                    return
                # Audit ``remove-all-confirmation``: one misclick
                # used to wipe every line on the chart — drawings
                # represent active R/R / entry / exit levels and
                # losing them mid-trade is a real cost. Gate the
                # destructive call behind an explicit confirm
                # showing the symbol + count. The dialog is the
                # standard Tk yes/no; default is NO so an
                # accidental Enter press cancels.
                try:
                    from tkinter import messagebox as _msg
                    plural = "" if count == 1 else "s"
                    # Audit ``remove-vs-delete-verb``: bulk drawing
                    # operations use the **Clear** verb (single-item
                    # deletes use **Delete**, see
                    # ``_show_drawing_context_menu``). Pin string:
                    # ``"Clear All Drawings"`` (title) +
                    # ``f"Clear {count} drawing{plural} on {sym}? "``.
                    ok = _msg.askyesno(
                        "Clear All Drawings",
                        f"Clear {count} drawing{plural} on {sym}? "
                        "This cannot be undone.",
                        default=_msg.NO,
                        icon=_msg.WARNING,
                        parent=self,
                    )
                except Exception:  # noqa: BLE001
                    ok = False
                if not ok:
                    return
                store.clear_symbol(sym)
            except Exception:  # noqa: BLE001
                pass

        menu.add_command(label="Add Horizontal Line Here",
                         command=_add_hline_here)
        menu.add_command(label="Copy Price", command=_copy_price)
        menu.add_command(label="Copy Price + Time",
                         command=_copy_price_time)
        menu.add_separator()
        menu.add_command(label="Reset Zoom", command=_reset_zoom)
        menu.add_command(label="Snapshot Chart…", command=_snapshot,
                         accelerator="Ctrl+Shift+S")
        menu.add_separator()
        menu.add_command(
            label=f"Clear All Drawings on {ticker}",
            command=_remove_all,
        )
        try:
            menu.tk_popup(int(x_root), int(y_root))
        finally:
            try:
                menu.grab_release()
            except Exception:  # noqa: BLE001
                pass

    def _show_drawing_context_menu(
        self, drawing_id: str, x_root: int, y_root: int,
    ) -> None:
        """Pop the per-line right-click menu (Edit / Delete)."""
        try:
            store = getattr(self, "_drawings", None)
            if store is None:
                return
            found = store.get(str(drawing_id))
            if found is None:
                return
            _ticker, drawing = found
            menu = tk.Menu(self, tearoff=0)

            def _edit() -> None:
                self._open_drawing_dialog(drawing.id)

            def _delete() -> None:
                try:
                    store.remove(drawing.id)
                except Exception:  # noqa: BLE001
                    pass

            menu.add_command(label="Edit Properties…", command=_edit)
            menu.add_command(label="Delete This Line", command=_delete)
            try:
                menu.tk_popup(int(x_root), int(y_root))
            finally:
                try:
                    menu.grab_release()
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Anchored VWAP — anchor materialization + Pick Anchor mode
    # ------------------------------------------------------------------
