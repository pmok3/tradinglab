"""Unit tests for :mod:`tradinglab.gui._modal_base`."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

tk = pytest.importorskip("tkinter")
ttk = pytest.importorskip("tkinter.ttk")

from tradinglab.gui import _modal_base as M
from tradinglab.gui import geometry_store as gs


@pytest.fixture()
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Visible Tk root with isolated geometry-store path per test.

    NOTE: we don't ``withdraw`` here — focus delivery to child
    Toplevels requires a visible root on some Tk builds (Windows
    Tk 8.6 in particular). The root is positioned off-screen via
    ``geometry`` so it doesn't pop a visible window during pytest.
    """
    monkeypatch.setenv("TRADINGLAB_GEOMETRY_PATH", str(tmp_path / "geom.json"))
    gs._reset_singleton_for_tests()
    try:
        r = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    # Park off-screen so the test root doesn't flash visibly. (1×1 +
    # negative coords keep WMs from clamping.)
    try:
        r.geometry("1x1-3000-3000")
    except tk.TclError:
        pass
    yield r
    try:
        r.update_idletasks()
        r.destroy()
    except tk.TclError:
        pass
    gs._reset_singleton_for_tests()


class _DemoModal(M.BaseModalDialog):
    """Minimal subclass used for boilerplate tests."""

    def __init__(self, parent, **kw) -> None:
        super().__init__(parent, **kw)
        self.cancelled = False
        self.primaried = False
        self._finalize_modal()

    def _on_cancel(self) -> None:
        self.cancelled = True
        super()._on_cancel()

    def _on_primary(self) -> None:
        self.primaried = True
        super()._on_primary()


def test_basemodaldialog_sets_title_and_transient(root) -> None:
    dlg = _DemoModal(
        root, title="My Dialog",
        geometry_key="dlg.demo",
        default_geometry="320x200+0+0",
    )
    assert dlg.title() == "My Dialog"
    # transient parent is the root.
    assert str(dlg.transient()) == str(root)
    dlg.destroy()


def test_basemodaldialog_escape_invokes_cancel(root) -> None:
    dlg = _DemoModal(root, title="x", geometry_key="dlg.demo")
    dlg.update()
    dlg.focus_force()
    dlg.update_idletasks()
    dlg.event_generate("<Escape>")
    dlg.update()
    assert dlg.cancelled is True


def test_basemodaldialog_return_invokes_primary(root) -> None:
    dlg = _DemoModal(root, title="x", geometry_key="dlg.demo")
    dlg.update()
    dlg.focus_force()
    dlg.update_idletasks()
    dlg.event_generate("<Return>")
    dlg.update()
    assert dlg.primaried is True


def test_basemodaldialog_wm_delete_invokes_cancel(root) -> None:
    dlg = _DemoModal(root, title="x", geometry_key="dlg.demo")
    # WM_DELETE_WINDOW is wired to cancel — invoking the protocol
    # handler directly mirrors what closing the OS window-X does.
    handler_name = dlg.protocol("WM_DELETE_WINDOW")
    assert handler_name, "WM_DELETE_WINDOW must be bound"
    dlg.tk.call(handler_name)
    assert dlg.cancelled is True


def test_basemodaldialog_persists_geometry_via_store(tmp_path, root) -> None:
    # First dialog: set a non-default geometry, commit it via the
    # store API, close it. (Driving <Configure> through ``event_generate``
    # is unreliable on Tk for Toplevels that haven't been mapped — the
    # store's own ``set_window`` path is the contract we care about.)
    dlg = _DemoModal(
        root, title="x", geometry_key="dlg.demo",
        default_geometry="320x200+0+0",
    )
    dlg.update()
    # Simulate user-driven resize → debounce flush by writing directly
    # to the store (mirrors what `_schedule_window_save` does after
    # the trailing Configure burst).
    store_obj = gs.store()
    store_obj.set_window("dlg.demo", "500x400+50+60")
    store_obj.save()
    dlg.destroy()
    root.update_idletasks()

    # Second dialog: same key — restored size persists.
    dlg2 = _DemoModal(
        root, title="x", geometry_key="dlg.demo",
        default_geometry="320x200+0+0",
    )
    dlg2.update_idletasks()
    dlg2.update()
    geom = dlg2.winfo_geometry()
    # Width/height should reflect the saved 500x400, not the 320x200
    # default. (Position may shift by WM decorations.)
    parts = geom.split("+")[0]
    w, h = parts.split("x")
    assert int(w) >= 400, f"width should have restored to 500-ish, got {geom}"
    assert int(h) >= 320, f"height should have restored to 400-ish, got {geom}"
    dlg2.destroy()


def test_basemodaldialog_grab_can_be_disabled(root) -> None:
    # Some dialogs (StatusHistory) want a non-modal Toplevel.
    class Loose(M.BaseModalDialog):
        def __init__(self_, parent):
            super().__init__(parent, title="loose")
            self_._finalize_modal(grab=False)

    Loose(root)
    # grab_status will be "none" on the root if the modal didn't grab.
    assert root.grab_status() is None or root.grab_status() == "none"


def test_baseeditordialog_footer_visual_order_left_to_right(root) -> None:
    """[Validate] [Cancel] [Apply] [Save & Close] from left to right."""

    class _Editor(M.BaseEditorDialog):
        def __init__(self_, parent):
            super().__init__(parent, title="ed", geometry_key="dlg.ed")
            footer = self_._build_editor_footer(
                self_,
                on_validate=lambda: None,
                on_cancel=lambda: None,
                on_apply=lambda: None,
                on_save_close=lambda: None,
            )
            footer.pack(fill="x")
            self_._finalize_modal()

    ed = _Editor(root)
    root.update_idletasks()
    assert ed.btn_validate is not None
    assert ed.btn_cancel is not None
    assert ed.btn_apply is not None
    assert ed.btn_save_close is not None

    # All four buttons share a footer parent; x-coordinates must be
    # strictly increasing left-to-right.
    xs = [
        ed.btn_validate.winfo_x(),
        ed.btn_cancel.winfo_x(),
        ed.btn_apply.winfo_x(),
        ed.btn_save_close.winfo_x(),
    ]
    assert xs == sorted(xs), (
        f"editor footer must read [Validate][Cancel][Apply][Save & Close] "
        f"left-to-right; got x positions {xs}"
    )
    ed.destroy()


def test_baseeditordialog_footer_skips_missing_callbacks(root) -> None:
    """If only on_cancel and on_save_close are passed, only those two render."""

    class _Confirm(M.BaseEditorDialog):
        def __init__(self_, parent):
            super().__init__(parent, title="c", geometry_key="dlg.c")
            footer = self_._build_editor_footer(
                self_,
                on_cancel=lambda: None,
                on_save_close=lambda: None,
            )
            footer.pack(fill="x")
            self_._finalize_modal()

    dlg = _Confirm(root)
    assert dlg.btn_validate is None
    assert dlg.btn_apply is None
    assert dlg.btn_cancel is not None
    assert dlg.btn_save_close is not None
    dlg.destroy()


def test_baseeditordialog_set_status_updates_var(root) -> None:
    class _Editor(M.BaseEditorDialog):
        def __init__(self_, parent):
            super().__init__(parent, title="e", geometry_key="dlg.e")
            f = self_._build_editor_footer(self_, on_cancel=lambda: None)
            f.pack(fill="x")
            self_._finalize_modal()

    ed = _Editor(root)
    ed.set_status("bad input", level="error")
    assert ed._status_var.get() == "bad input"
    ed.set_status("", level="info")
    assert ed._status_var.get() == ""
    ed.set_status("looks good", level="ok")
    assert ed._status_var.get() == "looks good"
    ed.destroy()


def test_baseeditordialog_enter_invokes_save_close_via_override(root) -> None:
    """A subclass that overrides ``_on_primary`` to call save_close
    must have Enter trigger Save & Close."""

    captured: dict = {}

    class _Editor(M.BaseEditorDialog):
        def __init__(self_, parent):
            super().__init__(parent, title="e", geometry_key="dlg.e")
            f = self_._build_editor_footer(
                self_,
                on_cancel=lambda: captured.setdefault("act", "cancel"),
                on_save_close=lambda: captured.setdefault("act", "save"),
            )
            f.pack(fill="x")
            self_._finalize_modal(primary=lambda: self_.btn_save_close.invoke())

    ed = _Editor(root)
    ed.update()
    ed.focus_force()
    ed.update_idletasks()
    ed.event_generate("<Return>")
    ed.update()
    assert captured.get("act") == "save"
    ed.destroy()


def test_dark_theme_hook_is_called_when_parent_supports_it(root) -> None:
    called: list = []

    def fake_hook(top):
        called.append(top)

    setattr(root, "apply_dark_theme_to", fake_hook)
    dlg = _DemoModal(root, title="x", geometry_key="dlg.demo")
    assert called and called[0] is dlg
    dlg.destroy()
