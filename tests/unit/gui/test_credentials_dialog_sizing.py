"""Audit ``credentials-dialog-sizing`` — the Configure Credentials dialog
must be resizable and never open smaller than its content.

Bug (user-reported): the dialog opened at a fixed ``560x420`` and was
``resizable=(False, False)``, so on the reporter's Windows-on-ARM display
(font/DPI scaling) the bottom section (Polygon field, status line, buttons)
was clipped with no way to enlarge the window.

Fix (mirrors ``sandbox_dialog`` — see its spec.md "Sizing" note): open
resizable and derive ``minsize`` from the *actual* laid-out request size
(``winfo_reqwidth/height`` + a small margin, floored). This is
self-correcting under any font / DPI scaling, and the WM clamps a stale-small
persisted ``dlg.credentials`` geometry back up to it.

These tests pin the two guarantees:
* the dialog is resizable in both axes, and
* ``minsize`` is at least as large as the content's requested size (so the
  window can never open with clipped content).
"""
from __future__ import annotations

import tkinter as tk
from tkinter import font, ttk

import pytest

from tradinglab.gui import _modal_base, credentials_dialog
from tradinglab.gui.geometry_store import GeometryStore


@pytest.fixture(scope="module")
def credentials_root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def dialog(credentials_root, tmp_path, monkeypatch, request):
    root = credentials_root
    sizes = {name: font.nametofont(name, root=root).actual("size") for name in ("TkDefaultFont", "TkTextFont")}
    geometry = GeometryStore(tmp_path / "geometry.json")
    geometry.load()
    options = getattr(request, "param", {})
    if options.get("saved"):
        geometry._windows["dlg.credentials"] = options["saved"]
    if options.get("font_size"):
        for name in ("TkDefaultFont", "TkTextFont"):
            font.nametofont(name, root=root).configure(size=options["font_size"])
    monkeypatch.setattr(_modal_base, "_gstore", lambda: geometry)
    monkeypatch.setenv("TRADINGLAB_TOKEN_DIR", str(tmp_path))
    try:
        dlg = credentials_dialog.CredentialsDialog(root)
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    try:
        dlg.update_idletasks()
        yield dlg
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass
        for name, size in sizes.items():
            font.nametofont(name, root=root).configure(size=size)
        root.withdraw()


def test_dialog_is_resizable_both_axes(dialog):
    # Tk returns (1, 1) for a both-axes-resizable window.
    assert tuple(dialog.resizable()) == (1, 1)


def test_minsize_not_smaller_than_content(dialog):
    """The window can never open smaller than its laid-out content.

    Deriving ``minsize`` from the request size is what makes this
    self-correcting across DPI / font scaling — on a higher-DPI host the
    request size (and therefore ``minsize``) simply grows to match.
    """
    min_w, min_h = dialog.minsize()
    req_w = dialog.winfo_reqwidth()
    req_h = dialog.winfo_reqheight()
    assert min_w >= req_w, (
        f"minsize width {min_w} < content reqwidth {req_w} "
        "(bottom/side content would be clipped)")
    assert min_h >= req_h, (
        f"minsize height {min_h} < content reqheight {req_h} "
        "(bottom content — Polygon field / status / buttons — would clip)")


def test_minsize_is_positive_and_sane(dialog):
    # Guard against a degenerate (0, 0) minsize if the request-size probe
    # ever fails silently: the floors must always apply.
    min_w, min_h = dialog.minsize()
    assert min_w >= 540
    assert min_h >= 480


@pytest.mark.parametrize("dialog", [
    {}, {"saved": "340x480+20+20"},
    {"font_size": 16}, {"font_size": 16, "saved": "600x760+20+20"},
], indirect=True)
def test_default_and_restored_width_fit_the_actual_inner_form(dialog):
    dialog.master.deiconify()
    dialog.update()
    canvas, form = dialog._form_canvas, dialog._form
    assert dialog.winfo_width() > 1
    assert canvas.winfo_width() >= form.winfo_reqwidth()
    assert int(dialog._default_geometry.split("x")[0]) >= 720
    for entry in dialog._entries.values():
        assert entry.winfo_rootx() >= canvas.winfo_rootx()
        assert entry.winfo_rootx() + entry.winfo_width() <= canvas.winfo_rootx() + canvas.winfo_width()
        assert entry.winfo_width() >= entry.winfo_reqwidth()
    for widget in dialog._schwab_panel.winfo_children()[0].winfo_children():
        if isinstance(widget, ttk.Frame) and widget.winfo_ismapped():
            assert widget.winfo_reqwidth() <= canvas.winfo_width()


def test_entry_column_uses_extra_window_width(dialog):
    dialog.master.deiconify()
    dialog.update()
    entry = dialog._entries["SCHWAB_APP_KEY"]
    before = entry.winfo_width()
    dialog.geometry(f"{dialog.winfo_width() + 150}x760")
    dialog.update()
    assert entry.winfo_width() >= before + 140
