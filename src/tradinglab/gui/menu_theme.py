"""Classic Tk menu theme helpers."""

from __future__ import annotations

import contextlib
import tkinter as tk
from collections.abc import Mapping

from ..constants import LIGHT_THEME


def _palette_value(theme: Mapping[str, str], key: str, fallback: str) -> str:
    value = theme.get(key, fallback)
    return value if isinstance(value, str) and value else fallback


def menu_theme_options(theme: Mapping[str, str] | None) -> dict[str, object]:
    """Return explicit ``tk.Menu.configure`` options for a resolved palette."""
    palette = theme or LIGHT_THEME
    bg = _palette_value(palette, "win_bg", LIGHT_THEME["win_bg"])
    fg = _palette_value(palette, "text", LIGHT_THEME["text"])
    return {
        "background": bg,
        "foreground": fg,
        "activebackground": _palette_value(palette, "grid", bg),
        "activeforeground": fg,
        "selectcolor": fg,
        "disabledforeground": _palette_value(palette, "text_disabled", fg),
        "borderwidth": 0,
        "relief": tk.FLAT,
    }


def _cascade_child(menu: tk.Menu, child_name: str) -> tk.Menu | None:
    for owner in (menu, menu._root()):
        with contextlib.suppress(KeyError, tk.TclError):
            child = owner.nametowidget(child_name)
            if isinstance(child, tk.Menu):
                return child
    return None


def apply_menu_theme(menu: tk.Menu | None, theme: Mapping[str, str] | None) -> None:
    """Apply ``theme`` to ``menu`` and every nested cascade submenu."""
    if menu is None:
        return
    opts = menu_theme_options(theme)
    seen: set[str] = set()

    def visit(current: tk.Menu) -> None:
        key = str(current)
        if key in seen:
            return
        seen.add(key)
        with contextlib.suppress(tk.TclError):
            current.configure(**opts)
        end = None
        with contextlib.suppress(tk.TclError):
            end = current.index("end")
        if end is None:
            return
        for idx in range(int(end) + 1):
            with contextlib.suppress(tk.TclError):
                if str(current.type(idx)) != "cascade":
                    continue
                child_name = str(current.entrycget(idx, "menu"))
                child = _cascade_child(current, child_name)
                if child is not None:
                    visit(child)

    visit(menu)
