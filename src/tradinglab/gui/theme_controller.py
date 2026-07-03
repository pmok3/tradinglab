"""Theme palette resolution and application for the TradingLab GUI."""

from __future__ import annotations

import contextlib
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from .. import constants as _constants
from .. import settings as _settings
from ..constants import (
    CUSTOMIZABLE_THEME_KEYS,
    LIGHT_THEME,
    build_ttk_style_spec,
    resolve_theme,
    ttk_combobox_listbox_options,
)
from ..rendering import style_axes
from ._widget_metrics import invalidate_metrics_cache
from .menu_theme import apply_menu_theme


@contextlib.contextmanager
def _silent_tcl(*extra_excs: type[BaseException]):
    """Swallow ``tk.TclError`` (plus any ``extra_excs``) for torn-down widgets."""
    excs = (tk.TclError,) + extra_excs
    try:
        yield
    except excs:
        pass


class ThemeController:
    """Owns the resolved theme palette and user overrides.

    Receives handles to the Tk root, Figure, and Canvas at construction.
    Does NOT own them — just applies styling to them.
    """

    def __init__(self, root: tk.Tk, *, figure=None, canvas=None):
        self._root = root
        self._figure = figure
        self._canvas = canvas
        self._theme: dict[str, str] = {}
        self._theme_overrides = self._load_theme_overrides()
        self._change_callbacks: list[Callable[[dict[str, str]], None]] = []

    @property
    def theme(self) -> dict[str, str]:
        return self._theme

    @property
    def overrides(self) -> dict[str, dict[str, str]]:
        return self._theme_overrides

    def bind_plot(self, *, figure=None, canvas=None) -> None:
        self._figure = figure
        self._canvas = canvas

    def on_change(self, callback: Callable[[dict[str, str]], None]) -> None:
        self._change_callbacks.append(callback)

    def _is_dark_mode(self) -> bool:
        try:
            return bool(self._root.dark_var.get())
        except Exception:  # noqa: BLE001
            return False

    def apply(self, dark: bool) -> None:
        """Resolve and apply the full theme."""
        mode = "dark" if dark else "light"
        theme = resolve_theme(mode, self._theme_overrides)
        self._theme.clear()
        self._theme.update(theme)
        # Drop the cached font-measured widget metrics so the next
        # _ConditionFrame / IndicatorDialog layout pass re-measures
        # against the freshly-applied font. Cheap (one dict.clear);
        # called BEFORE any redraw so consumers re-measure with the
        # new font on their next read.
        invalidate_metrics_cache()
        self._apply_window_theme(self._theme)
        self._apply_axes_theme(self._theme)
        self._apply_ttk_style(self._theme)
        self._apply_treeview_row_tags(self._theme)
        self._apply_overlay_artists(self._theme)
        self._apply_menubar_theme(self._theme)
        for callback in list(self._change_callbacks):
            try:
                callback(self._theme)
            except Exception:  # noqa: BLE001
                pass
        try:
            if self._canvas is not None:
                self._canvas.draw_idle()
        except Exception:  # noqa: BLE001
            pass

    def _apply_window_theme(self, theme: dict) -> None:
        """Paint the top-level Tk root + matplotlib figure background."""
        with _silent_tcl():
            self._root.configure(background=theme["win_bg"])
        if self._figure is not None:
            self._figure.set_facecolor(theme["fig_bg"])

    def _apply_axes_theme(self, theme: dict) -> None:
        """Restyle every live matplotlib axes via :func:`rendering.style_axes`."""
        root = self._root
        axes_iter = list(getattr(root, "_ax_candle_map", {}).keys()) or [
            getattr(root, "_ax_price", None),
            getattr(root, "_ax_volume", None),
        ]
        for ax in axes_iter:
            if ax is None:
                continue
            try:
                style_axes(ax, theme)
            except Exception:  # noqa: BLE001
                pass

    def _apply_ttk_style(self, theme: dict) -> None:
        """Push the palette into ttk.Style and the Combobox popdown."""
        with _silent_tcl():
            style = ttk.Style(self._root)
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass
            # Patch the Treeview.Heading layout: clam's built-in
            # ``Treeheading.cell`` element paints its background with a
            # hard-coded light grey (``#dcdad5``) at the C level — it
            # has zero configurable options, so neither ``style.configure``
            # nor ``style.map`` can recolor it. The visible symptom in
            # dark mode is that every Treeview header row (Watchlists,
            # Entries, Exits, Primary, Compare, Scanner) keeps a glaring
            # light strip at the top of the table. Dropping the
            # ``Treeheading.cell`` element lets the next layer
            # (``Treeheading.border``) paint the header using its
            # configured ``-background`` instead. Audit
            # ``treeview-heading-dark``.
            try:
                style.layout("Treeview.Heading", [
                    ("Treeheading.border", {
                        "sticky": "nswe",
                        "children": [
                            ("Treeheading.padding", {
                                "sticky": "nswe",
                                "children": [
                                    ("Treeheading.image",
                                     {"side": "right", "sticky": ""}),
                                    ("Treeheading.text",
                                     {"sticky": "we"}),
                                ],
                            }),
                        ],
                    }),
                ])
            except tk.TclError:
                pass
            for name, configure_kw, map_kw in build_ttk_style_spec(theme):
                style.configure(name, **configure_kw)
                if map_kw:
                    style.map(name, **map_kw)
            for opt, value in ttk_combobox_listbox_options(theme).items():
                self._root.option_add(opt, value)
            try:
                import tkinter.font as tkfont

                base_font = tkfont.nametofont("TkDefaultFont")
                linespace = int(base_font.metrics("linespace"))
                rowheight = max(20, linespace + 6)
                style.configure("Treeview", rowheight=rowheight)
            except (tk.TclError, RuntimeError, ValueError):
                pass

    def _apply_treeview_row_tags(self, theme: dict) -> None:
        """Push bull/bear row tint onto every Treeview we own.

        Bull/bear backgrounds route through ``constants.bull_row_bg`` /
        ``bear_row_bg`` so that when the Okabe-Ito color-blind palette is
        active the green/red row tints are recoloured to the orange/blue
        hue (preserving each theme's tuned tone). Audit
        ``color-blind-palette-audit``.
        """
        root = self._root
        bull_bg = _constants.bull_row_bg(theme)
        bear_bg = _constants.bear_row_bg(theme)
        bull_fg = (
            _constants.sentiment_recolor(theme["bull_row_fg"], bullish=True)
            if "bull_row_fg" in theme else theme["text"])
        bear_fg = (
            _constants.sentiment_recolor(theme["bear_row_fg"], bullish=False)
            if "bear_row_fg" in theme else theme["text"])
        trees: list = []
        trees.extend(getattr(root, "_watchlist_trees", {}).values())
        # Entries / Exits strategy trees expose ``_tree`` on the tab
        # widget. Forward-looking: today the rows aren't bull/bear-tagged,
        # but registering the palette now keeps every Treeview reachable
        # by the theme controller in one pass (audit
        # ``treeview-heading-dark`` companion change).
        for tab_attr in ("_entries_tab", "_exits_tab"):
            tab = getattr(root, tab_attr, None)
            if tab is not None:
                tree = getattr(tab, "_tree", None)
                if tree is not None:
                    trees.append(tree)
        for tree in trees:
            if tree is None:
                continue
            with _silent_tcl():
                tree.tag_configure("bull", foreground=bull_fg, background=bull_bg)
                tree.tag_configure("bear", foreground=bear_fg, background=bear_bg)

    def _apply_overlay_artists(self, theme: dict) -> None:
        """Repaint hardcoded-color matplotlib overlays."""
        root = self._root
        hover_ann = getattr(root, "_hover_ann", None)
        if hover_ann is not None:
            try:
                bbox = hover_ann.get_bbox_patch()
                if bbox is not None:
                    bbox.set_facecolor(theme["tooltip_bg"])
                    bbox.set_edgecolor(theme["spine"])
                hover_ann.set_color(theme["tooltip_fg"])
            except Exception:  # noqa: BLE001
                pass
        for pair in getattr(root, "_crosshair_artists", {}).values():
            for ln in pair:
                try:
                    ln.set_color(theme["crosshair"])
                except Exception:  # noqa: BLE001
                    pass
        for art in getattr(root, "_price_label_artists", {}).values():
            try:
                bbox = art.get_bbox_patch()
                if bbox is not None:
                    bbox.set_facecolor(theme["tooltip_bg"])
                    bbox.set_edgecolor(theme["spine"])
                art.set_color(theme["tooltip_fg"])
            except Exception:  # noqa: BLE001
                pass
        readout_muted = theme.get("muted") or theme.get("axis") or "#888888"
        for box in getattr(root, "_readout_artists", {}).values():
            try:
                main_t = getattr(box, "_main_text", None)
                if main_t is not None and getattr(main_t, "_text", None) is not None:
                    main_t._text.set_color(theme["text"])
            except Exception:  # noqa: BLE001
                pass
            # Per-indicator legend rows (the overlay NAME labels on the price
            # pane) bake the theme text/muted colour at BUILD time, so a live
            # theme swap must recolor them here — otherwise the names keep
            # their old colour (e.g. black after switching light→dark) until
            # the next full render (the reported bug, where opening "Manage
            # Indicators" was what forced the re-render). Visible row → name in
            # text colour; hidden row → every segment muted. See
            # ``interaction._build_readout_indicator_rows``.
            for row in getattr(box, "_ind_rows", None) or ():
                try:
                    if row.get("visible"):
                        lta = row.get("label_textarea")
                        lt = getattr(lta, "_text", None)
                        if lt is not None:
                            lt.set_color(theme["text"])
                    else:
                        container = row.get("container")
                        kids = container.get_children() if container is not None else ()
                        for ta in kids:
                            t = getattr(ta, "_text", None)
                            if t is not None:
                                t.set_color(readout_muted)
                except Exception:  # noqa: BLE001
                    pass
        for art in getattr(root, "_typing_preview_artists", {}).values():
            try:
                art.set_color(theme["text"])
            except Exception:  # noqa: BLE001
                pass
        # Per-pane hover value badges (volume + indicator panes). Their
        # colour is baked at build time / re-set on hover; recolor here so a
        # live theme swap updates any currently-visible badge. Single-value
        # indicator badges are re-coloured by their line on the next hover.
        for art in getattr(root, "_pane_value_labels", {}).values():
            try:
                art.set_color(theme["text"])
            except Exception:  # noqa: BLE001
                pass
        live_price_overlay = getattr(root, "_live_price_overlay", None)
        if live_price_overlay is not None:
            try:
                live_price_overlay.apply_theme(
                    line_color=theme["text"],
                    label_bg=theme["tooltip_bg"],
                    label_fg=theme["tooltip_fg"],
                    label_edge=theme["spine"],
                )
            except Exception:  # noqa: BLE001
                pass

    def _apply_menubar_theme(self, theme: dict) -> None:
        """Repaint the top-level menubar + every cascade submenu."""
        root = self._root
        mb = getattr(root, "_menubar", None)
        if mb is None:
            return
        palette = theme or LIGHT_THEME
        apply_menu_theme(mb, palette)
        for menu in getattr(root, "_menubar_submenus", []):
            apply_menu_theme(menu, palette)

    def _load_theme_overrides(self) -> dict[str, dict[str, str]]:
        """Load the ``theme_overrides`` dict from settings.json, defensively."""
        try:
            raw = _settings.get("theme_overrides", {}) or {}
        except Exception:  # noqa: BLE001
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        allowed_keys = {k for k, _ in CUSTOMIZABLE_THEME_KEYS}
        out: dict[str, dict[str, str]] = {"light": {}, "dark": {}}
        for mode in ("light", "dark"):
            mode_raw = raw.get(mode, {})
            if not isinstance(mode_raw, dict):
                continue
            for key, value in mode_raw.items():
                if key in allowed_keys and isinstance(value, str):
                    out[mode][key] = value
        return out

    def _save_theme_overrides(self) -> None:
        """Persist ``_theme_overrides`` to settings.json."""
        payload = {
            mode: dict(values)
            for mode, values in self._theme_overrides.items()
            if values
        }
        try:
            _settings.set("theme_overrides", payload)
        except Exception:  # noqa: BLE001
            pass

    def set_theme_override(self, mode: str, key: str, color: str) -> None:
        """Set a single override and re-theme."""
        if mode not in ("light", "dark"):
            return
        allowed = {k for k, _ in CUSTOMIZABLE_THEME_KEYS}
        if key not in allowed or not isinstance(color, str):
            return
        self._theme_overrides.setdefault(mode, {})[key] = color
        self._save_theme_overrides()
        self.apply(self._is_dark_mode())

    def clear_theme_overrides(self, mode: str | None = None) -> None:
        """Wipe overrides for one mode or both and re-theme."""
        if mode in ("light", "dark"):
            self._theme_overrides.setdefault(mode, {}).clear()
        else:
            self._theme_overrides.clear()
            self._theme_overrides.update({"light": {}, "dark": {}})
        self._save_theme_overrides()
        self.apply(self._is_dark_mode())

    def replace_theme_overrides(self, overrides: dict[str, dict[str, str]]) -> None:
        """Replace the entire override dict and re-theme."""
        self._theme_overrides.clear()
        self._theme_overrides.update(
            {
                "light": dict(overrides.get("light", {})),
                "dark": dict(overrides.get("dark", {})),
            }
        )
        self._save_theme_overrides()
        self.apply(self._is_dark_mode())
