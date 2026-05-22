from __future__ import annotations

import contextlib
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace

from tradinglab.constants import DARK_THEME
from tradinglab.core import thread_guard
from tradinglab.entries.audit import AuditLog
from tradinglab.entries.evaluator import EntryEvaluator
from tradinglab.entries.signals import EntryPaperSink
from tradinglab.exits.paper_engine import PaperBrokerEngine
from tradinglab.gui.entries_tab import EntriesTab
from tradinglab.gui.theme_controller import ThemeController
from tradinglab.gui.watchlist_tab import WatchlistTabMixin
from tradinglab.positions.tracker import PositionTracker


class _EmptyEntriesStorage:
    def load_all(self):
        return [], []


class _WatchlistHarness(tk.Toplevel, WatchlistTabMixin):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self.withdraw()
        self._theme_ctrl = SimpleNamespace(theme=DARK_THEME)
        self.listbox_options: dict[str, str] = {}

    def wait_window(self, window=None):  # type: ignore[override]
        window = window or self
        for widget in self._walk(window):
            if isinstance(widget, tk.Listbox):
                self.listbox_options = {
                    "background": str(widget.cget("background")),
                    "foreground": str(widget.cget("foreground")),
                    "selectbackground": str(widget.cget("selectbackground")),
                    "selectforeground": str(widget.cget("selectforeground")),
                    "highlightbackground": str(widget.cget("highlightbackground")),
                    "highlightcolor": str(widget.cget("highlightcolor")),
                    "highlightthickness": str(widget.cget("highlightthickness")),
                    "borderwidth": str(widget.cget("borderwidth")),
                    "relief": str(widget.cget("relief")),
                }
                break
        with contextlib.suppress(tk.TclError):
            window.destroy()

    def _walk(self, widget: tk.Misc):
        yield widget
        for child in widget.winfo_children():
            yield from self._walk(child)


def _make_evaluator() -> EntryEvaluator:
    tracker = PositionTracker()
    engine = PaperBrokerEngine(tracker)
    sink = EntryPaperSink(engine)
    return EntryEvaluator(tracker=tracker, sink=sink, audit=AuditLog())


def _close_entries_tab(tab: EntriesTab, evaluator: EntryEvaluator) -> None:
    if tab._tick_after_id is not None:
        with contextlib.suppress(tk.TclError, ValueError):
            tab.after_cancel(tab._tick_after_id)
        tab._tick_after_id = None
    with contextlib.suppress(Exception):
        evaluator.close()
    with contextlib.suppress(tk.TclError):
        tab.destroy()


def test_dark_ttk_styles_remove_clam_light_chrome(root: tk.Toplevel) -> None:
    ThemeController(root)._apply_ttk_style(DARK_THEME)
    style = ttk.Style(root)
    spine = DARK_THEME["spine"]
    text = DARK_THEME["text"]
    win_bg = DARK_THEME["win_bg"]

    for style_name in (
        "TFrame", "TNotebook", "TNotebook.Tab", "TButton",
        "Treeview", "Treeview.Heading", "TLabelframe", "TPanedwindow",
        "TScrollbar",
    ):
        assert str(style.lookup(style_name, "bordercolor")).lower() == spine
        assert str(style.lookup(style_name, "lightcolor")).lower() == spine
        assert str(style.lookup(style_name, "darkcolor")).lower() == spine
        assert str(style.lookup(style_name, "selectbackground")).lower() == spine
        assert str(style.lookup(style_name, "selectforeground")).lower() == text

    assert str(style.lookup("TScrollbar", "troughcolor")).lower() == win_bg
    assert str(style.lookup("TButton", "lightcolor", ("pressed",))).lower() == spine
    assert str(style.lookup("TButton", "darkcolor", ("pressed",))).lower() == spine
    assert str(style.lookup("Treeview", "bordercolor", ("focus",))).lower() == spine


def test_entries_text_widgets_dark_theme_removes_system_focus_ring(root: tk.Toplevel) -> None:
    evaluator = _make_evaluator()
    with thread_guard.tk_thread_check_disabled():
        tab = EntriesTab(root, evaluator=evaluator, storage=_EmptyEntriesStorage())
    try:
        tab._apply_theme(DARK_THEME)
        for txt in (tab._audit_txt, tab._stats_txt):
            assert str(txt.cget("background")) == DARK_THEME["ax_bg"]
            assert str(txt.cget("foreground")) == DARK_THEME["text"]
            assert str(txt.cget("highlightbackground")) == DARK_THEME["spine"]
            assert str(txt.cget("highlightcolor")) == DARK_THEME["spine"]
            assert str(txt.cget("highlightthickness")) == "1"
            assert str(txt.cget("borderwidth")) == "0"
            assert str(txt.cget("relief")) == "flat"
    finally:
        _close_entries_tab(tab, evaluator)


def test_watchlist_context_menu_uses_dark_active_row(root: tk.Toplevel) -> None:
    harness = SimpleNamespace(_theme_ctrl=SimpleNamespace(theme=DARK_THEME))
    opts = WatchlistTabMixin._current_menu_colors(harness)  # type: ignore[arg-type]
    assert opts["background"] == DARK_THEME["win_bg"]
    assert opts["foreground"] == DARK_THEME["text"]
    assert opts["activebackground"] == DARK_THEME["grid"]
    assert opts["activeforeground"] == DARK_THEME["text"]
    assert opts["borderwidth"] == 0
    assert opts["relief"] == tk.FLAT

    menu = tk.Menu(root, tearoff=0, **opts)
    try:
        assert str(menu.cget("activebackground")) == DARK_THEME["grid"]
        assert str(menu.cget("activeforeground")) == DARK_THEME["text"]
        assert str(menu.cget("background")) == DARK_THEME["win_bg"]
    finally:
        with contextlib.suppress(tk.TclError):
            menu.destroy()


def test_watchlist_picker_listbox_removes_system_focus_ring(
    root: tk.Toplevel, monkeypatch
) -> None:
    from tradinglab.gui import geometry_store

    monkeypatch.setattr(
        geometry_store, "attach_persistent_geometry", lambda *args, **kwargs: None
    )
    harness = _WatchlistHarness(root)
    try:
        selected = harness._prompt_pick_unpinned_watchlist(["Momentum"])
        assert selected is None
        assert harness.listbox_options["background"] == DARK_THEME["tree_bg"]
        assert harness.listbox_options["foreground"] == DARK_THEME["tree_fg"]
        assert harness.listbox_options["selectbackground"] == DARK_THEME["spine"]
        assert harness.listbox_options["selectforeground"] == DARK_THEME["tree_fg"]
        assert harness.listbox_options["highlightbackground"] == DARK_THEME["spine"]
        assert harness.listbox_options["highlightcolor"] == DARK_THEME["spine"]
        assert harness.listbox_options["highlightthickness"] == "1"
        assert harness.listbox_options["borderwidth"] == "0"
        assert harness.listbox_options["relief"] == "flat"
    finally:
        with contextlib.suppress(tk.TclError):
            harness.destroy()
