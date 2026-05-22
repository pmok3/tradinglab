"""Per-overlay legend with eye-toggles (big-bet item #9).

A small horizontal strip that sits **inside each price panel**, just
below the top-left OHLCV readout. Each row lists one overlay
indicator config: a small color swatch, the ``display_name``, and an
eye-button that toggles the config's ``visible`` flag through the
:class:`IndicatorManager`. Hidden configs render with a different
glyph so the user can re-enable them with one click.

Position model
==============

The legend was originally placed in the top-right of the chart frame
(a single floating widget). User feedback (2026-05-16): "the button
to hide indicators on the top right of the primary chart …  could be
moved to the chart for each primary and compare for customization,
in a row under the ohlcv. feel free to implement it like
tradingview does, with transparent boxes and a simple eye icon".

So now we instantiate ONE legend per `kind == "price"` axes
(primary + compare) and position it via ``place()`` at the axes'
top-left, with a vertical offset that clears the OHLCV readout strip.
Layout is horizontal (rows pack left-to-right) so each indicator
appears as a small TradingView-style pill: ``● SMA(20)`` with the
swatch + glyph next to the name. Background uses the chart's panel
colour with a 1-pixel border-less inset so the strip blends into the
chart instead of looking like a chunky Tk widget.

Repositioning is driven by the matplotlib ``draw_event`` (so the
legend follows the axes whenever the figure relayouts — compare-toggle,
resize, theme switch). When the axes is destroyed (figure cleared),
the legend stays attached to its parent frame; the next ``refresh()``
re-anchors it to the freshly-built axes.

Design notes
============

* Tk overlay vs. matplotlib artists: native ``ttk.Button`` widgets
  give us proper hover / click semantics out of the box. Matplotlib
  artists would require a manual hit-test in ``_on_button_press``
  and would not get keyboard focus / focus rings.
* The legend is built once at chart construction and refreshed via
  :meth:`refresh` at the tail of ``ChartApp._render`` so it always
  reflects the current set of overlay configs (including hidden
  ones, so the user can re-enable them).
* Toggling persists through the manager: ``manager.update(id,
  visible=...)`` flips the flag, fires the manager's redraw
  subscriber, and the next ``_render`` rebuilds artists with the
  new state. Persistence to ``settings.json`` happens via the
  existing indicator-manager save subscriber.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from ..indicators.config import IndicatorConfig, IndicatorManager


# Glyphs for the visible / hidden eye-button. Lucida Sans Unicode (the
# default Tk font on Windows) renders both reliably; we don't depend on
# a colour emoji font.
_EYE_ON = "\u25cf"   # ● — filled circle (visible)
_EYE_OFF = "\u25cb"  # ○ — empty circle (hidden)


# Pixel offset below the axes' top edge where the legend sits. The
# OHLCV readout strip is ~22 px tall at the default fontsize; we add
# ~6 px of breathing room so the legend doesn't crowd the readout.
_OHLCV_CLEARANCE_PX = 28
# Pixel inset from the axes' left edge — matches the readout's own
# offsetbox inset so the two strips line up vertically.
_LEFT_INSET_PX = 8


# Callback signatures used by the row event bindings.
RowDblClickCallback = Callable[[int], None]
RowContextMenuCallback = Callable[[int, int, int], None]


class OverlayLegend(ttk.Frame):
    """Horizontal legend strip placed below an axes' OHLCV readout."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        manager: "IndicatorManager",
        theme: Dict[str, Any],
        on_row_dblclick: Optional[RowDblClickCallback] = None,
        on_row_context_menu: Optional[RowContextMenuCallback] = None,
    ) -> None:
        super().__init__(master, padding=(2, 1))
        self._manager = manager
        self._theme = dict(theme) if theme else {}
        self._rows: List[ttk.Frame] = []
        self._buttons_by_id: Dict[int, ttk.Button] = {}
        self._placed = False
        # Optional callbacks for user gestures on a row. The row
        # container, swatch, and label all bind to these (the
        # eye-button keeps its single-click toggle action, so we
        # deliberately do NOT bind <Double-Button-1> on the button
        # itself — a quick double-click on the eye would otherwise
        # toggle twice AND fire the dblclick handler).
        self._on_row_dblclick: Optional[RowDblClickCallback] = on_row_dblclick
        self._on_row_context_menu: Optional[RowContextMenuCallback] = on_row_context_menu
        # Axes the legend currently anchors to. Set via
        # :meth:`reposition_for_axes` so a single legend can move with
        # its panel across re-renders (figure.clear() destroys axes
        # objects, but the next ``_render`` calls reposition with the
        # fresh axes handle).
        self._anchor_ax: Any = None

    # ---- public API ---------------------------------------------------

    def refresh(self, overlay_configs: List["IndicatorConfig"]) -> None:
        """Rebuild rows for the supplied overlay configs.

        Pass an empty list to hide the legend entirely. The list MAY
        include hidden configs (``visible=False``) — the legend renders
        them with the ``off`` glyph so the user can toggle them back on.
        """
        for row in self._rows:
            try:
                row.destroy()
            except tk.TclError:
                pass
        self._rows = []
        self._buttons_by_id = {}

        if not overlay_configs:
            try:
                if self._placed:
                    self.place_forget()
                    self._placed = False
            except tk.TclError:
                pass
            return

        for cfg in overlay_configs:
            self._build_row(cfg)

        # If we have an anchor axes, leave actual placement to the
        # caller (it will invoke :meth:`reposition_for_axes`). For
        # legacy callers that don't anchor, fall back to the original
        # top-right corner so existing tests keep passing.
        if self._anchor_ax is None:
            try:
                self.place(relx=1.0, rely=0.0, anchor="ne", x=-8, y=8)
                self.lift()
                self._placed = True
            except tk.TclError:
                pass

    def apply_theme(self, theme: Dict[str, Any]) -> None:
        """Update the swatch outline color when the chart theme changes."""
        if theme:
            self._theme = dict(theme)

    def reposition_for_axes(self, ax: Any, canvas_widget: tk.Widget) -> None:
        """Place the legend at ``ax``'s top-left, below the OHLCV strip.

        ``ax`` is the matplotlib price axes the legend should follow;
        ``canvas_widget`` is the FigureCanvasTkAgg ``get_tk_widget()``
        the legend is parented under so coordinates resolve into a
        common space.

        Coordinates: matplotlib's display extent is bottom-up
        (Y increases upward); Tk widget coords are top-down. We convert
        by ``canvas_height - bbox.y1`` so the legend's Y aligns with
        the axes' top in Tk space, then add ``_OHLCV_CLEARANCE_PX`` to
        clear the OHLCV readout offsetbox.

        Silent no-op if the axes hasn't been laid out yet (canvas
        height is 1 px on first paint) — the next ``draw_event`` will
        retry.
        """
        self._anchor_ax = ax
        if ax is None:
            try:
                if self._placed:
                    self.place_forget()
                    self._placed = False
            except tk.TclError:
                pass
            return
        # Empty row list means refresh() decided there were no overlays
        # to show — keep the legend hidden regardless of axes anchor.
        if not self._rows:
            try:
                if self._placed:
                    self.place_forget()
                    self._placed = False
            except tk.TclError:
                pass
            return
        try:
            bbox = ax.get_window_extent()
        except Exception:  # noqa: BLE001
            return
        try:
            canvas_h = int(canvas_widget.winfo_height())
        except Exception:  # noqa: BLE001
            return
        if canvas_h <= 1:
            # Not laid out yet — the next draw_event will hit this path
            # with a real height.
            return
        x = max(0, int(bbox.x0) + _LEFT_INSET_PX)
        y = max(0, canvas_h - int(bbox.y1) + _OHLCV_CLEARANCE_PX)
        try:
            self.place(in_=canvas_widget, x=x, y=y, anchor="nw")
            self.lift()
            self._placed = True
        except tk.TclError:
            pass

    # ---- internals ----------------------------------------------------

    def _build_row(self, cfg: "IndicatorConfig") -> None:
        try:
            row = ttk.Frame(self, padding=(2, 0))
        except tk.TclError:
            return
        color = self._color_for(cfg)
        try:
            swatch = tk.Frame(
                row, width=10, height=10, bg=color,
                highlightthickness=0,
            )
            swatch.pack(side=tk.LEFT, padx=(0, 4))
            swatch.pack_propagate(False)
        except tk.TclError:
            swatch = None

        label_text = cfg.display_name or cfg.kind_id or "?"
        try:
            label = ttk.Label(row, text=label_text)
            label.pack(side=tk.LEFT)
        except tk.TclError:
            label = None

        glyph = _EYE_ON if cfg.visible else _EYE_OFF
        try:
            btn = ttk.Button(
                row, text=glyph, width=2, takefocus=False,
                command=lambda cid=cfg.id: self._toggle(cid),
            )
            btn.pack(side=tk.LEFT, padx=(3, 6))
            self._buttons_by_id[cfg.id] = btn
        except tk.TclError:
            pass

        # Wire double-click + right-click on every interactive surface
        # EXCEPT the eye button (the button owns single-click toggle;
        # binding dblclick on it as well would fire BOTH the toggle and
        # the popup, which is jarring). ``hand2`` cursor on the same
        # surfaces signals interactivity to users who hover before
        # clicking. Bindings are no-ops when the callbacks weren't
        # wired by the constructor — keeps legacy tests + non-app
        # consumers working unchanged.
        for w in (row, swatch, label):
            if w is None:
                continue
            if self._on_row_dblclick is not None:
                try:
                    w.configure(cursor="hand2")
                except tk.TclError:
                    pass
                try:
                    w.bind(
                        "<Double-Button-1>",
                        lambda _e, cid=cfg.id: self._fire_dblclick(cid),
                    )
                except tk.TclError:
                    pass
            if self._on_row_context_menu is not None:
                try:
                    w.bind(
                        "<Button-3>",
                        lambda e, cid=cfg.id: self._fire_context_menu(
                            cid, e.x_root, e.y_root,
                        ),
                    )
                except tk.TclError:
                    pass

        try:
            # Horizontal layout — each row packs to the left so the
            # legend reads as a single TradingView-style strip.
            row.pack(side=tk.LEFT, padx=(0, 4))
            self._rows.append(row)
        except tk.TclError:
            pass

    def _fire_dblclick(self, config_id: int) -> None:
        """Invoke the user-supplied dblclick callback, swallowing
        exceptions so a buggy host doesn't break legend interaction
        for the rest of the session."""
        cb = self._on_row_dblclick
        if cb is None:
            return
        try:
            cb(config_id)
        except Exception:  # noqa: BLE001
            pass

    def _fire_context_menu(self, config_id: int, x_root: int, y_root: int) -> None:
        """Invoke the user-supplied right-click callback."""
        cb = self._on_row_context_menu
        if cb is None:
            return
        try:
            cb(config_id, int(x_root), int(y_root))
        except Exception:  # noqa: BLE001
            pass

    def _color_for(self, cfg: "IndicatorConfig") -> str:
        """Pick a representative color for the row's swatch.

        Order: cfg's first style override → factory's default style →
        theme text color fallback.
        """
        try:
            for ls in cfg.style.values():
                color = getattr(ls, "color", None)
                if color:
                    return str(color)
        except Exception:  # noqa: BLE001
            pass
        try:
            from ..indicators.base import factory_by_kind_id
            entry = factory_by_kind_id(cfg.kind_id)
            if entry is not None:
                _name, factory = entry
                default_style = getattr(factory, "default_style", None)
                if default_style:
                    if hasattr(default_style, "values"):
                        for ls in default_style.values():
                            color = getattr(ls, "color", None)
                            if color:
                                return str(color)
                    color = getattr(default_style, "color", None)
                    if color:
                        return str(color)
        except Exception:  # noqa: BLE001
            pass
        return self._theme.get("text", "#cccccc")

    def _toggle(self, config_id: int) -> None:
        cfg = self._manager.get(config_id)
        if cfg is None:
            return
        new_visible = not bool(getattr(cfg, "visible", True))
        try:
            self._manager.update(config_id, visible=new_visible)
        except Exception:  # noqa: BLE001
            pass


def collect_overlay_configs(
    manager: "IndicatorManager", scope: str, interval: str,
) -> List["IndicatorConfig"]:
    """Return every overlay-class config for ``(scope, interval)``.

    Mirrors :func:`indicators.render.applicable_overlay_configs` but
    deliberately **does not** filter by ``cfg.visible`` — the legend
    must show hidden configs so the user can re-enable them with a
    single click.
    """
    from ..indicators.config import factory_by_kind_id

    out: List["IndicatorConfig"] = []
    for cfg in manager.list():
        if getattr(cfg, "unknown", False):
            continue
        if scope not in getattr(cfg, "scopes", frozenset()):
            continue
        intervals = getattr(cfg, "intervals", ())
        if intervals and interval not in intervals:
            continue
        entry = factory_by_kind_id(cfg.kind_id)
        if entry is None:
            continue
        _name, factory = entry
        if bool(getattr(factory, "overlay", True)):
            out.append(cfg)
    return out


__all__ = ("OverlayLegend", "collect_overlay_configs")
