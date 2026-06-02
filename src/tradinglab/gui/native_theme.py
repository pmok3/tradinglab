"""Helpers for dark-mode theming of classic Tk widgets.

``ttk.Style`` does not reach native ``tk.Listbox``, ``tk.Text``, or
``tk.Canvas`` widgets. Dialogs that embed those controls use these
helpers to resolve the active app palette and apply the same dark/light
colors as the rest of TradingLab.
"""
from __future__ import annotations

import tkinter as tk
from typing import Any

from ..constants import LIGHT_THEME, resolve_theme


def current_theme(owner: Any) -> dict:
    """Return the active theme dict for ``owner`` or a safe light fallback.

    Walks the Tk widget ``master`` chain looking for the first ancestor
    that exposes ``_theme_ctrl`` — so a deeply-nested dialog (e.g. the
    color picker opened from inside the IndicatorDialog opened from
    ChartApp) still picks up the root ChartApp's active theme without
    every intermediate Toplevel having to re-expose the controller.

    Audit ``color-picker-theme-walks-master-chain``: previously only
    the directly-provided ``owner`` was inspected — opening the
    picker from a child dialog produced a stuck-light picker even
    when the app was in dark mode. The walk-up is best-effort
    (``getattr`` everywhere) so non-Tk owners (test stubs,
    SimpleNamespace) still resolve via the legacy ``dark_var`` /
    ``_dark_mode`` paths below.
    """
    visited: set[int] = set()
    node = owner
    for _ in range(64):  # hard cap on hierarchy depth (paranoia)
        if node is None or id(node) in visited:
            break
        visited.add(id(node))
        ctrl = getattr(node, "_theme_ctrl", None)
        theme = getattr(ctrl, "theme", None)
        if isinstance(theme, dict) and theme:
            return theme
        # Walk to the parent widget. Tk uses ``master``; the root
        # window's master is None. ``winfo_toplevel`` is a useful
        # shortcut for jumping a Frame straight to its Toplevel.
        nxt = getattr(node, "master", None)
        if nxt is None or nxt is node:
            try:
                top = node.winfo_toplevel()
            except Exception:  # noqa: BLE001
                top = None
            if top is not None and top is not node and id(top) not in visited:
                node = top
                continue
            break
        node = nxt

    dark = False
    dark_var = getattr(owner, "dark_var", None)
    if dark_var is not None:
        try:
            dark = bool(dark_var.get())
        except Exception:  # noqa: BLE001
            dark = False
    else:
        dark = bool(getattr(owner, "_dark_mode", False))
    if dark:
        try:
            return resolve_theme("dark", None)
        except Exception:  # noqa: BLE001
            pass
    return LIGHT_THEME


def apply_listbox_theme(widget: tk.Listbox, theme: dict) -> None:
    """Apply the canonical Listbox palette to a classic Tk Listbox."""
    tree_bg = theme.get("tree_bg", LIGHT_THEME["tree_bg"])
    tree_fg = theme.get("tree_fg", LIGHT_THEME["tree_fg"])
    spine = theme.get("spine", LIGHT_THEME["spine"])
    widget.configure(
        background=tree_bg,
        foreground=tree_fg,
        selectbackground=spine,
        selectforeground=tree_fg,
        highlightbackground=spine,
        highlightcolor=spine,
        highlightthickness=1,
        borderwidth=0,
        relief="flat",
    )


def apply_text_theme(widget: tk.Text, theme: dict) -> None:
    """Apply the canonical editable Text palette to a classic Tk Text widget."""
    ax_bg = theme.get("ax_bg", LIGHT_THEME["ax_bg"])
    text_fg = theme.get("text", LIGHT_THEME["text"])
    spine = theme.get("spine", LIGHT_THEME["spine"])
    widget.configure(
        background=ax_bg,
        foreground=text_fg,
        insertbackground=text_fg,
        selectbackground=spine,
        selectforeground=text_fg,
        highlightbackground=spine,
        highlightcolor=spine,
        highlightthickness=1,
        borderwidth=0,
        relief="flat",
    )


def apply_canvas_theme(widget: tk.Canvas, theme: dict) -> None:
    """Apply the window background to a classic Tk Canvas."""
    win_bg = theme.get("win_bg", LIGHT_THEME["win_bg"])
    widget.configure(background=win_bg)
