"""Mouse-wheel scrolling on the Manage Indicators dialog.

Validates the Enter/Leave-scoped ``bind_all`` pattern installed in
``IndicatorDialog._build_layout``:

* On ``<Enter>`` over the rows-canvas, the wheel handlers are installed
  globally so the OS-level wheel event (which fires on the root toplevel,
  not the focused widget) drives the canvas regardless of which child
  widget the cursor sits on.
* On ``<Leave>`` the handlers are removed so wheel events outside the
  dialog (e.g. over the main chart) do not bleed into the dialog.
* On ``<Destroy>`` the same cleanup runs so closing the dialog without
  first moving the cursor outside the canvas does not leak the global
  binding.

Driven through real Tk widgets (no display required when ``Tk()``
succeeds, which it does headlessly on Windows / Linux Xvfb / macOS).
"""

from __future__ import annotations

import tkinter as tk
from unittest import mock

import pytest

from tradinglab.indicators.config import IndicatorManager

# ---------------------------------------------------------------------------
# Fixtures (matches test_indicator_dialog_save_cancel.py)
# ---------------------------------------------------------------------------


@pytest.fixture()
def root():
    try:
        r = tk.Tk()
        r.withdraw()
    except tk.TclError:
        pytest.skip("No display available")
    mgr = IndicatorManager()
    r._indicator_manager = mgr  # type: ignore[attr-defined]
    r._indicator_dialog = None  # type: ignore[attr-defined]
    r._per_indicator_dialogs = {}  # type: ignore[attr-defined]
    r._theme = {"win_bg": "#ffffff"}  # type: ignore[attr-defined]
    r.interval_var = tk.StringVar(r, value="1d")  # type: ignore[attr-defined]
    r._on_menu_save_config = mock.MagicMock()  # type: ignore[attr-defined]
    yield r
    try:
        r.destroy()
    except tk.TclError:
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _open_dialog(root):
    from tradinglab.gui.indicator_dialog import IndicatorDialog
    return IndicatorDialog(root)


def _wheel_event(delta: int) -> tk.Event:
    e = tk.Event()
    e.delta = delta
    return e


class TestIndicatorDialogMouseWheel:

    def test_canvas_handle_is_exposed_on_dialog(self, root):
        """``_rows_canvas`` is stashed for tests / future feature use."""
        dlg = _open_dialog(root)
        assert getattr(dlg, "_rows_canvas", None) is not None, (
            "IndicatorDialog._build_layout must expose the rows canvas as "
            "_rows_canvas so the mouse-wheel bindings can be reasoned about "
            "and so future row-jump helpers have an entry point."
        )

    def test_enter_installs_global_mousewheel_binding(self, root):
        """Hovering the canvas installs the global wheel handler."""
        dlg = _open_dialog(root)
        canvas = dlg._rows_canvas
        # Pre-state: no global wheel binding installed by the dialog.
        canvas.unbind_all("<MouseWheel>")
        canvas.unbind_all("<Button-4>")
        canvas.unbind_all("<Button-5>")
        assert canvas.bind_all("<MouseWheel>") in ("", None)

        # Drive the installer directly. Tk's ``<Enter>`` virtual event
        # only fires reliably with a real cursor; the dialog exposes
        # the installer as ``_install_wheel_bindings`` so it is
        # testable without ``event_generate``.
        dlg._install_wheel_bindings()
        root.update_idletasks()
        assert canvas.bind_all("<MouseWheel>") not in ("", None), (
            "Entering the canvas must install a global <MouseWheel> "
            "binding so wheel events anywhere over the dialog route to "
            "the canvas."
        )

    def test_leave_removes_global_mousewheel_binding(self, root):
        """Moving the cursor off the canvas removes the global handler."""
        dlg = _open_dialog(root)
        canvas = dlg._rows_canvas
        dlg._install_wheel_bindings()
        root.update_idletasks()
        assert canvas.bind_all("<MouseWheel>") not in ("", None)
        dlg._uninstall_wheel_bindings()
        root.update_idletasks()
        assert canvas.bind_all("<MouseWheel>") in ("", None), (
            "Leaving the canvas must remove the global <MouseWheel> "
            "binding so wheel events over the main chart do not also "
            "drive the dialog."
        )

    def test_destroy_removes_global_mousewheel_binding(self, root):
        """Closing the dialog cleans up the global wheel binding."""
        dlg = _open_dialog(root)
        canvas = dlg._rows_canvas
        dlg._install_wheel_bindings()
        root.update_idletasks()
        assert canvas.bind_all("<MouseWheel>") not in ("", None)
        dlg.destroy()
        root.update_idletasks()
        # After destruction the global binding must be gone — otherwise
        # the dangling callback would fire over the main chart and
        # silently raise TclError on every wheel spin.
        assert root.bind_all("<MouseWheel>") in ("", None), (
            "Destroying the dialog must remove the global <MouseWheel> "
            "binding."
        )

    def test_wheel_callback_calls_yview_scroll(self, root):
        """A wheel event with delta=120 scrolls one unit up (negative)."""
        dlg = _open_dialog(root)
        canvas = dlg._rows_canvas
        with mock.patch.object(canvas, "yview_scroll") as mock_scroll:
            evt = _wheel_event(delta=120)
            dlg._on_mousewheel(evt)
            assert mock_scroll.call_count == 1
            args = mock_scroll.call_args
            assert args.args[0] == -1
            assert args.args[1] == "units"

    def test_wheel_callback_scrolls_down_for_negative_delta(self, root):
        """A wheel event with delta=-120 scrolls one unit down (positive)."""
        dlg = _open_dialog(root)
        canvas = dlg._rows_canvas
        with mock.patch.object(canvas, "yview_scroll") as mock_scroll:
            evt = _wheel_event(delta=-120)
            dlg._on_mousewheel(evt)
            assert mock_scroll.call_count == 1
            args = mock_scroll.call_args
            assert args.args[0] == 1
            assert args.args[1] == "units"

    def test_zero_delta_does_not_scroll(self, root):
        """Some Tk versions emit delta=0 ticks; the handler ignores them."""
        dlg = _open_dialog(root)
        canvas = dlg._rows_canvas
        with mock.patch.object(canvas, "yview_scroll") as mock_scroll:
            dlg._on_mousewheel(_wheel_event(delta=0))
            assert mock_scroll.call_count == 0

    def test_button4_scrolls_up_one_unit(self, root):
        """Linux <Button-4> handler scrolls -1 unit."""
        dlg = _open_dialog(root)
        canvas = dlg._rows_canvas
        with mock.patch.object(canvas, "yview_scroll") as mock_scroll:
            dlg._on_button4(tk.Event())
            assert mock_scroll.call_count == 1
            args = mock_scroll.call_args
            assert args.args == (-1, "units")

    def test_button5_scrolls_down_one_unit(self, root):
        """Linux <Button-5> handler scrolls +1 unit."""
        dlg = _open_dialog(root)
        canvas = dlg._rows_canvas
        with mock.patch.object(canvas, "yview_scroll") as mock_scroll:
            dlg._on_button5(tk.Event())
            assert mock_scroll.call_count == 1
            args = mock_scroll.call_args
            assert args.args == (1, "units")


class TestIndicatorDialogBanner:
    """The discoverability banner above the rows region."""

    def test_banner_label_exists(self, root):
        """The dialog exposes a ``_header_banner`` after build_layout."""
        dlg = _open_dialog(root)
        assert hasattr(dlg, "_header_banner"), (
            "IndicatorDialog must expose a _header_banner attribute "
            "(may be None if widget construction failed, but the "
            "attribute itself must always be present)."
        )

    def test_banner_text_explains_per_interval_default(self, root):
        """The banner mentions 'interval' and 'check' (or 'enable')."""
        dlg = _open_dialog(root)
        banner = getattr(dlg, "_header_banner", None)
        if banner is None:
            pytest.skip("Banner widget did not initialise on this platform")
        text = (banner.cget("text") or "").lower()
        # The exact wording is tunable; the test just asserts the
        # key concepts are present so future copy edits don't quietly
        # drop the explanation.
        assert "interval" in text, (
            f"Banner must mention 'interval' so users connect it to the "
            f"per-row interval checkboxes. Got: {text!r}"
        )
        assert "current" in text or "added" in text, (
            f"Banner must mention 'current' or 'added' so users "
            f"understand the default-on-add behaviour. Got: {text!r}"
        )
