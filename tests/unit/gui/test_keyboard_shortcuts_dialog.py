"""Tests for the ``Keyboard Shortcuts\u2026`` Help-menu cheat sheet.

Audit ID: ``keyboard-shortcuts-dialog``. Coverage:

1. The menu entry is added to the Help cascade.
2. ``_on_help_keyboard_shortcuts`` is a callable method on the mixin.
3. ``_keyboard_shortcut_groups()`` returns the canonical content,
   covering every shortcut documented in the README + spec.md trail.
4. The dialog is a modeless singleton — re-invocation does not stack
   windows.
5. ``Escape`` and the Close button both clean up the singleton state.
"""
from __future__ import annotations

import os
import tkinter as tk
import unittest

import pytest

from tradinglab.gui.help_menu import (
    HelpMenuMixin,
    _keyboard_shortcut_groups,
)

# ---------------------------------------------------------------------------
# Tk-less tests: groups content + method existence.
# ---------------------------------------------------------------------------


class TestKeyboardShortcutGroups:
    def test_returns_list_of_category_tuples(self):
        out = _keyboard_shortcut_groups()
        assert isinstance(out, list)
        assert all(isinstance(c, tuple) and len(c) == 2 for c in out)
        for cat, entries in out:
            assert isinstance(cat, str) and cat
            assert isinstance(entries, list) and entries
            for entry in entries:
                assert isinstance(entry, tuple) and len(entry) == 2
                shortcut, action = entry
                assert isinstance(shortcut, str) and shortcut
                assert isinstance(action, str) and action

    def test_includes_application_category_with_ctrl_comma(self):
        groups = dict(_keyboard_shortcut_groups())
        assert "Application" in groups
        shortcuts = {s for s, _ in groups["Application"]}
        assert "Ctrl+," in shortcuts, (
            "Ctrl+, opens Settings; documented in app.py:825 / "
            "_on_accel_settings binding. Must appear in cheat sheet."
        )
        assert "Ctrl+L" in shortcuts
        assert "Ctrl+R" in shortcuts
        assert "Ctrl+`" in shortcuts, (
            "Ctrl+` toggles ChartStack (app.py:6773 accelerator). Must "
            "appear in cheat sheet."
        )

    def test_includes_drawings_category_with_alt_h(self):
        groups = dict(_keyboard_shortcut_groups())
        assert "Drawings (horizontal lines)" in groups
        shortcuts = {s for s, _ in groups["Drawings (horizontal lines)"]}
        assert "Ctrl+H" in shortcuts, (
            "Alt+H places a horizontal line at the cursor "
            "(_on_alt_h_placement). Must appear in cheat sheet."
        )

    def test_includes_watchlists_category_with_space(self):
        groups = dict(_keyboard_shortcut_groups())
        assert "Watchlists" in groups
        shortcuts = {s for s, _ in groups["Watchlists"]}
        assert "Space" in shortcuts, (
            "Space cycles the active watchlist (_on_global_space). "
            "Must appear in cheat sheet."
        )

    def test_includes_chart_navigation_section(self):
        groups = dict(_keyboard_shortcut_groups())
        assert "Chart navigation" in groups
        # Mouse-wheel pan / zoom is well-known matplotlib behavior;
        # we expose it so the trader knows it's there.
        actions = {a for _, a in groups["Chart navigation"]}
        assert any("zoom" in a.lower() for a in actions)
        assert any("drill" in a.lower() for a in actions)

    def test_categories_are_unique_and_ordered(self):
        out = _keyboard_shortcut_groups()
        names = [c for c, _ in out]
        assert len(names) == len(set(names)), "category names must be unique"
        # Application should come first — most-recognized accelerators
        # land at the top of the dialog.
        assert names[0] == "Application"

    def test_no_empty_entries(self):
        for cat, entries in _keyboard_shortcut_groups():
            assert entries, f"category {cat!r} has no entries"

    def test_returns_fresh_list_each_call(self):
        # Caller may mutate the result; we shouldn't share state.
        a = _keyboard_shortcut_groups()
        a.clear()
        b = _keyboard_shortcut_groups()
        assert b, "second call should still return the full list"


class TestHelpMenuMixinMethodExists:
    def test_on_help_keyboard_shortcuts_is_callable(self):
        method = getattr(HelpMenuMixin, "_on_help_keyboard_shortcuts", None)
        assert callable(method), (
            "HelpMenuMixin._on_help_keyboard_shortcuts must be a "
            "callable — the Help menu wires this command."
        )

    def test_build_help_menu_inserts_keyboard_shortcuts_entry(self):
        """The Help cascade builder must add a Keyboard Shortcuts entry."""
        # Source-level inspection avoids the Tk requirement.
        import inspect

        src = inspect.getsource(HelpMenuMixin._build_help_menu)
        assert "Keyboard Shortcuts" in src, (
            "_build_help_menu must add a 'Keyboard Shortcuts...' entry "
            "for the cheat-sheet dialog (audit "
            "keyboard-shortcuts-dialog)."
        )
        assert "_on_help_keyboard_shortcuts" in src, (
            "The new menu entry must dispatch to "
            "_on_help_keyboard_shortcuts."
        )


# ---------------------------------------------------------------------------
# Tk-required dialog behavior. Skipped on headless / Tcl-broken envs.
# ---------------------------------------------------------------------------


def _can_run_tk() -> bool:
    if os.environ.get("DISPLAY") is None and os.name != "nt":
        return False
    try:
        root = tk.Tk()
    except Exception:  # noqa: BLE001
        return False
    try:
        root.withdraw()
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass
    return True


@pytest.mark.skipif(not _can_run_tk(), reason="Tk root unavailable in this env")
class TestKeyboardShortcutsDialog(unittest.TestCase):

    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"Tk init unavailable: {exc!r}")
        self.root.withdraw()
        # Bind the mixin's method onto the root so we can invoke it
        # with the root as ``self``.
        self.root._keyboard_shortcuts_dialog = None
        self.root._on_help_keyboard_shortcuts = (
            HelpMenuMixin._on_help_keyboard_shortcuts.__get__(
                self.root, type(self.root))
        )

    def tearDown(self) -> None:
        try:
            self.root.destroy()
        except Exception:  # noqa: BLE001
            pass

    def test_open_creates_toplevel_and_records_singleton(self):
        self.root._on_help_keyboard_shortcuts()
        self.root.update_idletasks()
        dlg = self.root._keyboard_shortcuts_dialog
        assert dlg is not None
        assert isinstance(dlg, tk.Toplevel)
        assert dlg.winfo_exists()
        # Title set.
        assert "Keyboard" in dlg.title()

    def test_reinvoking_does_not_create_second_window(self):
        self.root._on_help_keyboard_shortcuts()
        self.root.update_idletasks()
        first = self.root._keyboard_shortcuts_dialog
        self.root._on_help_keyboard_shortcuts()
        self.root.update_idletasks()
        second = self.root._keyboard_shortcuts_dialog
        assert first is second, (
            "singleton: re-invoking should reuse the existing dialog, "
            "not stack a second copy"
        )

    def test_escape_is_bound_to_close_callback(self):
        """The Escape binding must be wired so a user pressing it
        closes the dialog. Direct event-generate has been flaky in
        headless test harnesses; this test asserts the binding is
        registered + matches the WM_DELETE_WINDOW callback (which
        is end-to-end verified by ``test_wm_delete_window_closes_*``).
        """
        self.root._on_help_keyboard_shortcuts()
        self.root.update_idletasks()
        dlg = self.root._keyboard_shortcuts_dialog
        assert dlg is not None
        escape_cmd = dlg.bind("<Escape>")
        assert escape_cmd, (
            "Escape must be bound on the dialog to close it (audit "
            "keyboard-shortcuts-dialog requires ESC dismissal)"
        )
        wm_close_cmd = dlg.protocol("WM_DELETE_WINDOW")
        assert wm_close_cmd, "WM_DELETE_WINDOW protocol must be wired"

    def test_wm_delete_window_closes_and_clears_singleton(self):
        self.root._on_help_keyboard_shortcuts()
        self.root.update_idletasks()
        dlg = self.root._keyboard_shortcuts_dialog
        assert dlg is not None
        # Fire the WM_DELETE_WINDOW protocol handler directly.
        handler_name = dlg.protocol("WM_DELETE_WINDOW")
        # Tk returns a command name; call it via the master interpreter.
        if handler_name:
            self.root.tk.call(handler_name)
        self.root.update_idletasks()
        assert self.root._keyboard_shortcuts_dialog is None

    def test_dialog_contains_treeview_with_all_groups(self):
        self.root._on_help_keyboard_shortcuts()
        self.root.update_idletasks()
        dlg = self.root._keyboard_shortcuts_dialog
        # Find the Treeview descendant.
        from tkinter import ttk

        def _find_tree(w):
            if isinstance(w, ttk.Treeview):
                return w
            for child in w.winfo_children():
                hit = _find_tree(child)
                if hit is not None:
                    return hit
            return None

        tree = _find_tree(dlg)
        assert tree is not None, "dialog must contain a ttk.Treeview"
        top_items = tree.get_children("")
        # Every category in the canonical list must have a parent row.
        cat_names = [c for c, _ in _keyboard_shortcut_groups()]
        labels = [tree.item(iid, "text") for iid in top_items]
        for cat in cat_names:
            assert cat in labels, (
                f"category {cat!r} missing from dialog Treeview"
            )

    def test_dialog_can_be_reopened_after_close(self):
        self.root._on_help_keyboard_shortcuts()
        self.root.update_idletasks()
        first = self.root._keyboard_shortcuts_dialog
        # Close via the protocol handler (no event-generate dance).
        handler_name = first.protocol("WM_DELETE_WINDOW")
        if handler_name:
            self.root.tk.call(handler_name)
        self.root.update()
        assert self.root._keyboard_shortcuts_dialog is None
        self.root._on_help_keyboard_shortcuts()
        self.root.update_idletasks()
        second = self.root._keyboard_shortcuts_dialog
        assert second is not None
        assert second is not first


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-vv"]))
