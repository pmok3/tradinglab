"""Theme palette resolution and application for the TradingLab GUI."""

from __future__ import annotations

import contextlib
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from .. import settings as _settings
from ..constants import (
    BEAR_COLOR,
    BULL_COLOR,
    CUSTOMIZABLE_THEME_KEYS,
    LIGHT_THEME,
    build_ttk_style_spec,
    resolve_theme,
    ttk_combobox_listbox_options,
)
from ..rendering import style_axes


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
        """Push bull/bear row tint onto every Treeview we own."""
        root = self._root
        bull_bg = theme.get("bull_row_bg", BULL_COLOR)
        bear_bg = theme.get("bear_row_bg", BEAR_COLOR)
        bull_fg = theme.get("bull_row_fg", theme["text"])
        bear_fg = theme.get("bear_row_fg", theme["text"])
        trees: list = []
        trees.extend(getattr(root, "_watchlist_trees", {}).values())
        trees.append(getattr(root, "_primary_table", None))
        trees.append(getattr(root, "_compare_table", None))
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
        for box in getattr(root, "_readout_artists", {}).values():
            try:
                main_t = getattr(box, "_main_text", None)
                if main_t is not None and getattr(main_t, "_text", None) is not None:
                    main_t._text.set_color(theme["text"])
            except Exception:  # noqa: BLE001
                pass
        for art in getattr(root, "_typing_preview_artists", {}).values():
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
        """Repaint the top-level menubar + cascades to match the active theme."""
        root = self._root
        mb = getattr(root, "_menubar", None)
        if mb is None:
            return
        if theme is None:
            theme = LIGHT_THEME
        bg = theme.get("win_bg", "#f0f0f0")
        fg = theme.get("text", "#111111")
        # Foreground for disabled menu entries (e.g. the gated
        # "Highlight Flat HA Candles" entry when HA is off). Picking a
        # muted grey from the palette avoids the Windows-default
        # etched/embossed disabled-text style that looks blurry on
        # dark backgrounds. Falls back to ``fg`` for older palettes
        # that don't carry the key. Audit ``menu-disabled-fg``.
        fg_disabled = theme.get("text_disabled", fg)
        opts = dict(
            background=bg,
            foreground=fg,
            activebackground=fg,
            activeforeground=bg,
            selectcolor=fg,
            disabledforeground=fg_disabled,
        )
        for menu in [mb, *getattr(root, "_menubar_submenus", [])]:
            with _silent_tcl():
                menu.configure(**opts)

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
