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
    """Return the active theme dict for ``owner`` or a safe light fallback."""
    ctrl = getattr(owner, "_theme_ctrl", None)
    theme = getattr(ctrl, "theme", None)
    if isinstance(theme, dict) and theme:
        return theme

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
