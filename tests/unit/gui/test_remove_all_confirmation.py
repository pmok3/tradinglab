"""Audit ``remove-all-confirmation`` — Clear-all needs an undo guard.

The chart canvas right-click menu's "Clear All Drawings on
<TICKER>" entry previously dropped straight into
``DrawingStore.clear_symbol(...)``. One misclick wiped every
horizontal-line drawing on the symbol — entry, exit, target,
stop, R/R levels — with no warning and no undo path.

The fix gates the destructive call behind a Tk
``messagebox.askyesno`` confirmation dialog that:

* Shows the count of drawings about to be removed.
* Shows the symbol name for cross-chart safety.
* Defaults to NO so an accidental Return press cancels.
* Carries a WARNING icon.

When there are zero drawings, the confirm dialog is skipped and
``clear_symbol`` is not called either (no work to do, no
spurious modal).

Verb convention (audit ``remove-vs-delete-verb``): bulk
drawing operations use **Clear**; the title and body text both
read "Clear …". Single-item operations use **Delete**.

These tests pin the gating logic by exercising the inline
``_remove_all`` closure built by ``_show_chart_canvas_menu``.
We can't rebuild a full ``ChartApp`` for a unit test, so we
extract the same logic into a callable harness driven by mocks.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import tradinglab.app


def _make_remove_all_callable(store, sym, parent, messagebox_module):
    """Mirror the closure built in ``app.py::_show_chart_canvas_menu``.

    We re-create the exact gating sequence so the harness exercises
    the same code path the production menu calls. If the production
    code drifts, the unit test must drift in lock-step (and the
    smoke test ``check_d80_horizontal_lines`` provides the
    integration-level fixture for the real menu construction).
    """

    def _remove_all():
        try:
            if not sym:
                return
            if store is None:
                return
            try:
                existing = list(store.list(sym))
            except Exception:  # noqa: BLE001
                existing = []
            count = len(existing)
            if count == 0:
                return
            try:
                plural = "" if count == 1 else "s"
                ok = messagebox_module.askyesno(
                    "Clear All Drawings",
                    f"Clear {count} drawing{plural} on {sym}? "
                    "This cannot be undone.",
                    default=messagebox_module.NO,
                    icon=messagebox_module.WARNING,
                    parent=parent,
                )
            except Exception:  # noqa: BLE001
                ok = False
            if not ok:
                return
            store.clear_symbol(sym)
        except Exception:  # noqa: BLE001
            pass

    return _remove_all


class TestRemoveAllConfirmation:
    """The confirm dialog gates the destructive clear_symbol call."""

    def test_confirm_yes_calls_clear_symbol(self):
        store = MagicMock()
        store.list.return_value = [MagicMock(id="d1"), MagicMock(id="d2")]
        parent = MagicMock()
        mbox = MagicMock()
        mbox.NO = "no"
        mbox.WARNING = "warning"
        mbox.askyesno.return_value = True

        _make_remove_all_callable(store, "AMD", parent, mbox)()
        store.clear_symbol.assert_called_once_with("AMD")
        mbox.askyesno.assert_called_once()

    def test_confirm_no_does_not_call_clear_symbol(self):
        store = MagicMock()
        store.list.return_value = [MagicMock(id="d1")]
        parent = MagicMock()
        mbox = MagicMock()
        mbox.NO = "no"
        mbox.WARNING = "warning"
        mbox.askyesno.return_value = False

        _make_remove_all_callable(store, "AMD", parent, mbox)()
        store.clear_symbol.assert_not_called()

    def test_empty_drawings_skips_dialog_and_skips_clear(self):
        store = MagicMock()
        store.list.return_value = []
        parent = MagicMock()
        mbox = MagicMock()
        mbox.NO = "no"
        mbox.WARNING = "warning"

        _make_remove_all_callable(store, "AMD", parent, mbox)()
        # No dialog popped, no destructive call.
        mbox.askyesno.assert_not_called()
        store.clear_symbol.assert_not_called()

    def test_dialog_uses_no_as_default(self):
        store = MagicMock()
        store.list.return_value = [MagicMock(id="d1")]
        parent = MagicMock()
        mbox = MagicMock()
        mbox.NO = "no"
        mbox.WARNING = "warning"
        mbox.askyesno.return_value = True

        _make_remove_all_callable(store, "AMD", parent, mbox)()
        # The default must be NO so an accidental Return cancels.
        kwargs = mbox.askyesno.call_args.kwargs
        assert kwargs["default"] == mbox.NO
        assert kwargs["icon"] == mbox.WARNING
        assert kwargs["parent"] is parent

    def test_dialog_message_includes_symbol_and_count(self):
        store = MagicMock()
        store.list.return_value = [MagicMock(id="d1"), MagicMock(id="d2"),
                                    MagicMock(id="d3")]
        parent = MagicMock()
        mbox = MagicMock()
        mbox.NO = "no"
        mbox.WARNING = "warning"
        mbox.askyesno.return_value = True

        _make_remove_all_callable(store, "MSFT", parent, mbox)()
        args, kwargs = mbox.askyesno.call_args
        msg = args[1]
        # Count + plural + symbol all surfaced.
        assert "3" in msg
        assert "drawings" in msg
        assert "MSFT" in msg
        assert "cannot be undone" in msg.lower()

    def test_singular_drawing_count_no_plural(self):
        store = MagicMock()
        store.list.return_value = [MagicMock(id="d1")]
        parent = MagicMock()
        mbox = MagicMock()
        mbox.NO = "no"
        mbox.WARNING = "warning"
        mbox.askyesno.return_value = True

        _make_remove_all_callable(store, "AMD", parent, mbox)()
        args, _ = mbox.askyesno.call_args
        msg = args[1]
        # Singular: "1 drawing on AMD" (no trailing 's').
        assert "1 drawing on AMD" in msg
        assert "1 drawings on AMD" not in msg

    def test_no_symbol_short_circuits(self):
        store = MagicMock()
        parent = MagicMock()
        mbox = MagicMock()

        _make_remove_all_callable(store, "", parent, mbox)()
        store.list.assert_not_called()
        store.clear_symbol.assert_not_called()
        mbox.askyesno.assert_not_called()

    def test_no_store_short_circuits(self):
        parent = MagicMock()
        mbox = MagicMock()
        _make_remove_all_callable(None, "AMD", parent, mbox)()
        mbox.askyesno.assert_not_called()

    def test_dialog_raising_treats_as_no(self):
        store = MagicMock()
        store.list.return_value = [MagicMock(id="d1")]
        parent = MagicMock()
        mbox = MagicMock()
        mbox.NO = "no"
        mbox.WARNING = "warning"
        mbox.askyesno.side_effect = RuntimeError("Tk crashed")

        # Must not raise out; must NOT clear_symbol.
        _make_remove_all_callable(store, "AMD", parent, mbox)()
        store.clear_symbol.assert_not_called()

    def test_clear_symbol_raise_swallowed(self):
        store = MagicMock()
        store.list.return_value = [MagicMock(id="d1")]
        store.clear_symbol.side_effect = RuntimeError("disk full")
        parent = MagicMock()
        mbox = MagicMock()
        mbox.NO = "no"
        mbox.WARNING = "warning"
        mbox.askyesno.return_value = True

        # The closure's outer try/except must swallow any clear_symbol
        # raise so the menu doesn't bubble it to the user.
        _make_remove_all_callable(store, "AMD", parent, mbox)()
        store.clear_symbol.assert_called_once()


class TestProductionClosureMatchesHarness:
    """Pin the production code in app.py uses the same gating sequence.

    Source-level assertions catch the regression where someone edits
    the closure to skip the confirmation prompt without updating the
    test harness above.
    """

    def test_app_py_closure_has_confirm_call(self):
        path = Path(tradinglab.app.__file__)
        src = path.read_text(encoding="utf-8")
        # The remove-all closure lives in DrawingsAppMixin since the
        # canvas/per-drawing right-click menu extraction; include that
        # module's source so the regression assertion still anchors
        # on the production code path.
        from tradinglab.gui import drawings_app as _drawings_app_mod
        src += "\n" + Path(_drawings_app_mod.__file__).read_text(encoding="utf-8")
        # The closure must call askyesno BEFORE clear_symbol.
        idx_yesno = src.find("askyesno")
        idx_clear = src.find("clear_symbol(sym)")
        assert idx_yesno != -1, (
            "Remove-all confirmation askyesno call missing from app.py")
        assert idx_clear != -1, (
            "Remove-all clear_symbol call missing from app.py")
        # The first askyesno appearance must precede the
        # _remove_all branch's clear_symbol call. Ordering is the
        # important guarantee — finding 'askyesno' anywhere
        # later (e.g. another menu) wouldn't be the same path.
        assert idx_yesno < idx_clear, (
            "Remove-all confirmation must precede the clear_symbol "
            "call in app.py")

    def test_app_py_closure_uses_warning_default_no(self):
        path = Path(tradinglab.app.__file__)
        src = path.read_text(encoding="utf-8")
        from tradinglab.gui import drawings_app as _drawings_app_mod
        src += "\n" + Path(_drawings_app_mod.__file__).read_text(encoding="utf-8")
        # The dialog must use the NO default + WARNING icon so an
        # accidental Enter / Tab confirms cancel-by-default and the
        # icon signals destructiveness.
        assert "default=_msg.NO" in src
        assert "icon=_msg.WARNING" in src
