"""Classic Tk menu theme helpers."""

from __future__ import annotations

import contextlib
import tkinter as tk
from collections.abc import Mapping

from ..constants import LIGHT_THEME

CASCADE_ARROW_GLYPH = "\u203a"
CASCADE_ARROW_SUFFIX = f"  {CASCADE_ARROW_GLYPH}"


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
        "disabledforeground": fg,
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


def _is_attached_menubar(menu: tk.Menu) -> bool:
    with contextlib.suppress(tk.TclError):
        return str(menu._root().cget("menu")) == str(menu)
    return False


def _label_has_cascade_glyph(label: str) -> bool:
    return label.rstrip().endswith(CASCADE_ARROW_GLYPH)


def _ensure_cascade_glyph(menu: tk.Menu, idx: int) -> None:
    label = str(menu.entrycget(idx, "label"))
    if label and not _label_has_cascade_glyph(label):
        menu.entryconfigure(idx, label=label + CASCADE_ARROW_SUFFIX)


def append_cascade_glyphs(menu: tk.Menu | None) -> None:
    """Append the Tk-rendered cascade chevron to submenu entries."""
    if menu is None:
        return
    seen: set[str] = set()
    decorate_root = not _is_attached_menubar(menu)

    def visit(current: tk.Menu, *, decorate_entries: bool) -> None:
        key = str(current)
        if key in seen:
            return
        seen.add(key)
        end = None
        with contextlib.suppress(tk.TclError):
            end = current.index("end")
        if end is None:
            return
        for idx in range(int(end) + 1):
            with contextlib.suppress(tk.TclError):
                if str(current.type(idx)) != "cascade":
                    continue
                if decorate_entries:
                    _ensure_cascade_glyph(current, idx)
                child_name = str(current.entrycget(idx, "menu"))
                child = _cascade_child(current, child_name)
                if child is not None:
                    visit(child, decorate_entries=True)

    visit(menu, decorate_entries=decorate_root)


def apply_menu_theme(menu: tk.Menu | None, theme: Mapping[str, str] | None) -> None:
    """Apply ``theme`` to ``menu`` and every nested cascade submenu."""
    if menu is None:
        return
    opts = menu_theme_options(theme)
    seen: set[str] = set()
    decorate_root = not _is_attached_menubar(menu)

    def visit(current: tk.Menu, *, decorate_entries: bool) -> None:
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
                if decorate_entries:
                    _ensure_cascade_glyph(current, idx)
                child_name = str(current.entrycget(idx, "menu"))
                child = _cascade_child(current, child_name)
                if child is not None:
                    visit(child, decorate_entries=True)

    visit(menu, decorate_entries=decorate_root)
