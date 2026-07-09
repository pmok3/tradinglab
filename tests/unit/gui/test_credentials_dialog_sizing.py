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

import pytest

from tradinglab.gui import credentials_dialog


@pytest.fixture
def dialog():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    root.withdraw()
    try:
        dlg = credentials_dialog.CredentialsDialog(root)
    except tk.TclError as exc:
        root.destroy()
        pytest.skip(f"Tk unavailable: {exc}")
    try:
        dlg.update_idletasks()
        yield dlg
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass
        try:
            root.destroy()
        except tk.TclError:
            pass


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
