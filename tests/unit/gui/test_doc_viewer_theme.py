"""Live-theme-repaint tests for the documentation viewer.

Audit: ``doc-viewer-live-repaint`` — the dialog used to capture its
palette once at construction and never repaint, so toggling dark mode
while the viewer was open (or singleton-reopening after the toggle)
showed stale light-mode chrome.
"""
from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")

from tradinglab.gui.doc_viewer import (  # noqa: E402
    DocViewerDialog,
    _theme_palette,
)


@pytest.fixture
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk display not available")
    root.withdraw()
    root.dark_var = tk.BooleanVar(master=root, value=False)
    yield root
    try:
        root.destroy()
    except tk.TclError:
        pass


def _make_dialog(root):
    return DocViewerDialog(root)


def test_initial_palette_matches_parent_dark(tk_root):
    tk_root.dark_var.set(True)
    dlg = _make_dialog(tk_root)
    try:
        assert dlg._dark is True
        assert dlg._palette["bg"] == _theme_palette(True)["bg"]
    finally:
        dlg.destroy()


def test_apply_theme_switches_light_to_dark(tk_root):
    tk_root.dark_var.set(False)
    dlg = _make_dialog(tk_root)
    try:
        assert dlg._palette["bg"] == _theme_palette(False)["bg"]
        light_bg = dlg.cget("bg")
        # Flip the parent's dark var and repaint.
        tk_root.dark_var.set(True)
        dlg._apply_theme()
        assert dlg._dark is True
        assert dlg._palette["bg"] == _theme_palette(True)["bg"]
        assert dlg.cget("bg") != light_bg
        # Text widget repainted.
        assert str(dlg._text.cget("bg")) == _theme_palette(True)["bg"]
        # Sidebar repainted.
        assert (
            str(dlg._sidebar.cget("bg"))
            == _theme_palette(True)["sidebar_bg"]
        )
    finally:
        dlg.destroy()


def test_apply_theme_switches_dark_to_light(tk_root):
    tk_root.dark_var.set(True)
    dlg = _make_dialog(tk_root)
    try:
        tk_root.dark_var.set(False)
        dlg._apply_theme()
        assert dlg._dark is False
        assert dlg.cget("bg") == _theme_palette(False)["bg"]
        assert str(dlg._text.cget("bg")) == _theme_palette(False)["bg"]
    finally:
        dlg.destroy()


def test_apply_theme_noop_when_unchanged(tk_root):
    tk_root.dark_var.set(False)
    dlg = _make_dialog(tk_root)
    try:
        before = dlg._palette
        dlg._apply_theme()
        # Same dict instance — fast path didn't rebuild.
        assert dlg._palette is before
    finally:
        dlg.destroy()


def test_apply_theme_repaints_tracked_frames_and_labels(tk_root):
    tk_root.dark_var.set(False)
    dlg = _make_dialog(tk_root)
    try:
        # Every tracked widget should report a bg from the
        # corresponding palette slot.
        light = _theme_palette(False)
        for frame in dlg._theme_tk_frames:
            key = getattr(frame, "_dv_bg_key", "bg")
            assert str(frame.cget("bg")) == light[key]
        for lbl in dlg._theme_tk_labels:
            bg_key = getattr(lbl, "_dv_bg_key", "bg")
            fg_key = getattr(lbl, "_dv_fg_key", "fg")
            assert str(lbl.cget("bg")) == light[bg_key]
            assert str(lbl.cget("fg")) == light[fg_key]

        tk_root.dark_var.set(True)
        dlg._apply_theme()
        dark = _theme_palette(True)
        for frame in dlg._theme_tk_frames:
            key = getattr(frame, "_dv_bg_key", "bg")
            assert str(frame.cget("bg")) == dark[key]
        for lbl in dlg._theme_tk_labels:
            bg_key = getattr(lbl, "_dv_bg_key", "bg")
            fg_key = getattr(lbl, "_dv_fg_key", "fg")
            assert str(lbl.cget("bg")) == dark[bg_key]
            assert str(lbl.cget("fg")) == dark[fg_key]
    finally:
        dlg.destroy()


def test_apply_theme_reconfigures_text_tags(tk_root):
    tk_root.dark_var.set(False)
    dlg = _make_dialog(tk_root)
    try:
        light_fg = str(dlg._text.tag_cget("h1", "foreground"))
        tk_root.dark_var.set(True)
        dlg._apply_theme()
        dark_fg = str(dlg._text.tag_cget("h1", "foreground"))
        assert dark_fg == _theme_palette(True)["fg"]
        assert dark_fg != light_fg
    finally:
        dlg.destroy()
