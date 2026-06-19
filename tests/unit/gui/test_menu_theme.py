from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")

from tradinglab.constants import DARK_THEME
from tradinglab.gui.menu_builder import MenuBuilder
from tradinglab.gui.menu_theme import (
    CASCADE_ARROW_GLYPH,
    CASCADE_ARROW_SUFFIX,
    append_cascade_glyphs,
    apply_menu_theme,
)
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


class _MenuBuilderCallbacks:
    def __init__(self, root: tk.Tk) -> None:
        self._ha_display_var = tk.BooleanVar(root, value=False)
        self._highlight_ha_flat_var = tk.BooleanVar(root, value=False)
        self._highlight_key_bars_var = tk.BooleanVar(root, value=False)
        self._volume_tod_var = tk.BooleanVar(root, value=False)
        self._chartstack_visible_var = tk.BooleanVar(root, value=False)
        self._ratio_rebase_var = tk.BooleanVar(root, value=False)

    def __getattr__(self, name: str):
        if name.startswith("_on_") or name.startswith("on_"):
            return lambda *args, **kwargs: None
        raise AttributeError(name)

    def _refresh_recent_menu(self, menu: tk.Menu, kind: str, *, on_pick) -> None:
        return None

    def _populate_indicator_preset_menu(self, menu: tk.Menu, action: str) -> None:
        return None

    def _build_help_menu(self, menubar: tk.Menu) -> tk.Menu:
        menu = tk.Menu(menubar, tearoff=0)
        menu.add_command(label="About TradingLab")
        menubar.add_cascade(label="Help", menu=menu, underline=-1)
        return menu


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
    assert str(menu.cget("disabledforeground")).lower() == DARK_THEME["text"]
    assert int(str(menu.cget("borderwidth"))) == 0
    assert str(menu.cget("relief")) == "flat"


def _cascade_labels_below_root(menu: tk.Menu) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()

    def visit(current: tk.Menu, *, collect_current: bool) -> None:
        key = str(current)
        if key in seen:
            return
        seen.add(key)
        end = current.index("end")
        if end is None:
            return
        for idx in range(int(end) + 1):
            if str(current.type(idx)) != "cascade":
                continue
            if collect_current:
                labels.append(str(current.entrycget(idx, "label")))
            child = current.nametowidget(str(current.entrycget(idx, "menu")))
            visit(child, collect_current=True)

    visit(menu, collect_current=False)
    return labels


def test_apply_menu_theme_recurses_into_cascade_submenus(menu_root):
    menubar, view_menu, ha_menu = _build_nested_menu(menu_root)

    apply_menu_theme(menubar, DARK_THEME)

    _assert_dark_menu_options(menubar)
    _assert_dark_menu_options(view_menu)
    _assert_dark_menu_options(ha_menu)


def test_menu_builder_cascade_entries_receive_unicode_chevron(menu_root):
    builder = MenuBuilder(menu_root, _MenuBuilderCallbacks(menu_root))
    menubar = builder.build()
    menu_root.configure(menu=menubar)

    apply_menu_theme(menubar, DARK_THEME)

    cascade_labels = _cascade_labels_below_root(menubar)
    assert cascade_labels
    assert all(label.rstrip().endswith(CASCADE_ARROW_GLYPH) for label in cascade_labels)
    top_labels = [
        str(menubar.entrycget(idx, "label"))
        for idx in range(int(menubar.index("end") or 0) + 1)
        if str(menubar.type(idx)) == "cascade"
    ]
    assert "File" in top_labels
    assert not any(label.rstrip().endswith(CASCADE_ARROW_GLYPH) for label in top_labels)


def test_append_cascade_glyphs_is_idempotent(menu_root):
    _menubar, view_menu, _ha_menu = _build_nested_menu(menu_root)

    append_cascade_glyphs(view_menu)
    append_cascade_glyphs(view_menu)

    label = str(view_menu.entrycget(0, "label"))
    assert label == f"Heikin-Ashi{CASCADE_ARROW_SUFFIX}"
    assert label.count(CASCADE_ARROW_GLYPH) == 1


def test_theme_controller_discovers_cascades_without_legacy_registry(menu_root):
    menubar, view_menu, ha_menu = _build_nested_menu(menu_root)
    menu_root._menubar = menubar
    menu_root._menubar_submenus = []

    ThemeController(menu_root)._apply_menubar_theme(DARK_THEME)

    _assert_dark_menu_options(menubar)
    _assert_dark_menu_options(view_menu)
    _assert_dark_menu_options(ha_menu)
