"""Theme tests for the Manage Indicators dialog.

Audit: ``indicator-dialog-label-theme`` — the ``_apply_theme`` walker
used to skip ``tk.Label`` widgets entirely, leaving drag handles, the
help-icon glyph, and swatch captions on a white background with black
text when the app is in dark mode.
"""
from __future__ import annotations

import tkinter as tk
from unittest import mock

import pytest

from tradinglab.indicators.config import IndicatorConfig, IndicatorManager


@pytest.fixture()
def root():
    try:
        r = tk.Tk()
        r.withdraw()
    except tk.TclError:
        pytest.skip("No display available")
    mgr = IndicatorManager()
    cfg = IndicatorConfig(
        kind_id="sma", params={"length": 20}, display_name="SMA(20)",
    )
    mgr.add(cfg)
    r._indicator_manager = mgr
    r._indicator_dialog = None
    r._per_indicator_dialogs = {}
    r._theme = {"win_bg": "#1e1e1e", "text": "#e0e0e0"}
    r.interval_var = tk.StringVar(r, value="1d")
    r._on_menu_save_config = mock.MagicMock()
    yield r
    try:
        r.destroy()
    except tk.TclError:
        pass


def _open_dialog(root):
    from tradinglab.gui.indicator_dialog import IndicatorDialog
    return IndicatorDialog(root)


def _find_labels(widget):
    out = []
    for child in widget.winfo_children():
        if child.__class__ is tk.Label:
            out.append(child)
        out.extend(_find_labels(child))
    return out


def test_apply_theme_paints_plain_tk_labels(root):
    dlg = _open_dialog(root)
    try:
        labels = _find_labels(dlg)
        assert labels, "Expected at least one tk.Label in the dialog"
        # All labels should now share the dark theme bg.
        for lbl in labels:
            assert str(lbl.cget("background")) == "#1e1e1e", (
                f"Label {lbl} still has bg={lbl.cget('background')}"
            )
        # Non-preserved labels also pick up the dark foreground.
        non_preserved = [
            lbl for lbl in labels if not getattr(lbl, "_preserve_fg", False)
        ]
        assert non_preserved, "Expected at least one normal-fg label"
        for lbl in non_preserved:
            assert str(lbl.cget("foreground")) == "#e0e0e0"
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass


def test_help_icon_preserves_blue_foreground(root):
    dlg = _open_dialog(root)
    try:
        labels = _find_labels(dlg)
        # Help icon is the ⓘ glyph (U+24D8) tagged with
        # _preserve_fg=True so the dark theme doesn't recolour it.
        help_icons = [
            lbl for lbl in labels
            if str(lbl.cget("text")) == "\u24d8"
            and getattr(lbl, "_preserve_fg", False)
        ]
        assert help_icons, "Expected ⓘ help icon with _preserve_fg=True"
        for icon in help_icons:
            # Background still themed (no white sliver).
            assert str(icon.cget("background")) == "#1e1e1e"
            # Foreground still the original blue.
            assert str(icon.cget("foreground")) == "#58a6ff"
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass


def test_apply_theme_is_idempotent(root):
    dlg = _open_dialog(root)
    try:
        dlg._apply_theme()
        dlg._apply_theme()
        labels = _find_labels(dlg)
        for lbl in labels:
            assert str(lbl.cget("background")) == "#1e1e1e"
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass


def test_apply_theme_switches_light_then_dark(root):
    # Open the dialog under a light theme first.
    root._theme = {"win_bg": "#ffffff", "text": "#000000"}
    dlg = _open_dialog(root)
    try:
        labels = _find_labels(dlg)
        non_preserved = [
            lbl for lbl in labels if not getattr(lbl, "_preserve_fg", False)
        ]
        for lbl in non_preserved:
            assert str(lbl.cget("background")) == "#ffffff"
            assert str(lbl.cget("foreground")) == "#000000"

        # Flip to dark and re-apply.
        root._theme = {"win_bg": "#1e1e1e", "text": "#e0e0e0"}
        dlg._apply_theme()
        for lbl in non_preserved:
            assert str(lbl.cget("background")) == "#1e1e1e"
            assert str(lbl.cget("foreground")) == "#e0e0e0"
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass
