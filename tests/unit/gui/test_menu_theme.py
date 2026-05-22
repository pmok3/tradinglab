from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")

from tradinglab.constants import DARK_THEME
from tradinglab.gui.menu_theme import apply_menu_theme
from tradinglab.gui.theme_controller import ThemeController


@pytest.fixture()
def menu_root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    try:
        root.withdraw()
        yield root
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def _build_nested_menu(root: tk.Tk) -> tuple[tk.Menu, tk.Menu, tk.Menu]:
    menubar = tk.Menu(root)
    view_menu = tk.Menu(menubar, tearoff=0)
    ha_menu = tk.Menu(view_menu, tearoff=0)
    ha_menu.add_command(label="Show Heikin-Ashi Candles")
    view_menu.add_cascade(label="Heikin-Ashi", menu=ha_menu)
    menubar.add_cascade(label="View", menu=view_menu)
    return menubar, view_menu, ha_menu


def _assert_dark_menu_options(menu: tk.Menu) -> None:
    assert str(menu.cget("background")).lower() == DARK_THEME["win_bg"]
    assert str(menu.cget("foreground")).lower() == DARK_THEME["text"]
    assert str(menu.cget("activebackground")).lower() == DARK_THEME["grid"]
    assert str(menu.cget("activeforeground")).lower() == DARK_THEME["text"]
    assert str(menu.cget("selectcolor")).lower() == DARK_THEME["text"]
    assert str(menu.cget("disabledforeground")).lower() == DARK_THEME["text_disabled"]
    assert int(str(menu.cget("borderwidth"))) == 0
    assert str(menu.cget("relief")) == "flat"


def test_apply_menu_theme_recurses_into_cascade_submenus(menu_root):
    menubar, view_menu, ha_menu = _build_nested_menu(menu_root)

    apply_menu_theme(menubar, DARK_THEME)

    _assert_dark_menu_options(menubar)
    _assert_dark_menu_options(view_menu)
    _assert_dark_menu_options(ha_menu)


def test_theme_controller_discovers_cascades_without_legacy_registry(menu_root):
    menubar, view_menu, ha_menu = _build_nested_menu(menu_root)
    menu_root._menubar = menubar
    menu_root._menubar_submenus = []

    ThemeController(menu_root)._apply_menubar_theme(DARK_THEME)

    _assert_dark_menu_options(menubar)
    _assert_dark_menu_options(view_menu)
    _assert_dark_menu_options(ha_menu)
