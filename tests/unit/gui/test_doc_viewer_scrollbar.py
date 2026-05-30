"""Scrollbar dark-mode regression test for the documentation viewer.

The doc viewer mounts a ``ttk.Scrollbar`` for the rendered-markdown
``tk.Text`` widget. Historically the scrollbar inherited the global
ttk theme — on Windows that paints a near-white scrollbar against the
dark text background. The fix is a per-dialog ttk Style derived from
the active palette so the scrollbar's trough + arrows match the rest
of the chrome in both themes.

The tests assert the contract, not the exact pixel colours: the
scrollbar MUST be wired to a custom style name (so it's not at the
mercy of the global ttk theme), and that style's ``troughcolor`` /
``background`` lookups MUST match the active palette in both modes.
"""
from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")
from tkinter import ttk  # noqa: E402

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


def _find_scrollbar(widget: tk.Misc) -> ttk.Scrollbar | None:
    for child in widget.winfo_children():
        if child.winfo_class() == "TScrollbar":
            return child  # type: ignore[return-value]
        nested = _find_scrollbar(child)
        if nested is not None:
            return nested
    return None


def test_scrollbar_uses_dialog_specific_style(tk_root):
    """The scrollbar must opt out of the global ttk theme.

    A bare ``ttk.Scrollbar(...)`` with ``style=""`` falls back to the
    OS-default ``TScrollbar`` style — which is white-on-dark on Windows.
    The viewer should wire an explicit ``style=`` so we can repaint it
    per-theme without yanking the global ttk style out from under
    every other widget in the app.
    """
    tk_root.dark_var.set(True)
    dlg = DocViewerDialog(tk_root)
    try:
        sb = _find_scrollbar(dlg)
        assert sb is not None, "doc viewer must mount a ttk.Scrollbar"
        style_name = str(sb.cget("style") or "")
        assert style_name, (
            "scrollbar must use a dialog-specific ttk Style (so dark "
            "mode can repaint the trough without touching the global "
            "TScrollbar style)"
        )
    finally:
        dlg.destroy()


def test_scrollbar_style_trough_is_dark_in_dark_mode(tk_root):
    tk_root.dark_var.set(True)
    dlg = DocViewerDialog(tk_root)
    try:
        sb = _find_scrollbar(dlg)
        assert sb is not None
        style = ttk.Style(tk_root)
        style_name = str(sb.cget("style"))
        # The scrollbar style should resolve a dark trough colour
        # — pinned against the active palette's ``code_bg`` (a slot
        # that's already dark in the dark palette and acts as the
        # gutter colour throughout the dialog).
        trough = style.lookup(style_name, "troughcolor")
        bg = style.lookup(style_name, "background")
        dark = _theme_palette(True)
        # Either of the two style slots should reflect a dark palette
        # entry — the exact slot is implementation-defined, but at
        # least one must be set.
        assert trough or bg, (
            f"style {style_name!r} must define a dark trough/background"
        )
        # Whichever is set must equal a palette slot from the dark
        # theme (sidebar_bg, code_bg, bg, or btn_bg are all valid
        # gutter candidates).
        valid_dark_slots = {
            dark["bg"], dark["code_bg"], dark["sidebar_bg"], dark["btn_bg"]
        }
        if trough:
            assert trough in valid_dark_slots, (
                f"trough {trough!r} should match a dark palette slot, "
                f"got valid={valid_dark_slots}"
            )
        if bg:
            assert bg in valid_dark_slots, (
                f"background {bg!r} should match a dark palette slot, "
                f"got valid={valid_dark_slots}"
            )
    finally:
        dlg.destroy()


def test_scrollbar_style_trough_is_light_in_light_mode(tk_root):
    tk_root.dark_var.set(False)
    dlg = DocViewerDialog(tk_root)
    try:
        sb = _find_scrollbar(dlg)
        assert sb is not None
        style = ttk.Style(tk_root)
        style_name = str(sb.cget("style"))
        trough = style.lookup(style_name, "troughcolor")
        bg = style.lookup(style_name, "background")
        light = _theme_palette(False)
        valid_light_slots = {
            light["bg"], light["code_bg"], light["sidebar_bg"], light["btn_bg"]
        }
        if trough:
            assert trough in valid_light_slots, (
                f"trough {trough!r} should match a light palette slot, "
                f"got valid={valid_light_slots}"
            )
        if bg:
            assert bg in valid_light_slots, (
                f"background {bg!r} should match a light palette slot, "
                f"got valid={valid_light_slots}"
            )
    finally:
        dlg.destroy()


def test_apply_theme_repaints_scrollbar_style(tk_root):
    """Toggling dark mode after construction must repaint the scrollbar."""
    tk_root.dark_var.set(False)
    dlg = DocViewerDialog(tk_root)
    try:
        sb = _find_scrollbar(dlg)
        assert sb is not None
        style = ttk.Style(tk_root)
        style_name = str(sb.cget("style"))
        trough_light = style.lookup(style_name, "troughcolor")
        bg_light = style.lookup(style_name, "background")

        tk_root.dark_var.set(True)
        dlg._apply_theme()

        trough_dark = style.lookup(style_name, "troughcolor")
        bg_dark = style.lookup(style_name, "background")
        # At least one of {trough, background} must change between
        # themes — otherwise the repaint is silently a no-op.
        assert (trough_light, bg_light) != (trough_dark, bg_dark), (
            "_apply_theme must flip the scrollbar style on theme change"
        )
    finally:
        dlg.destroy()
