"""Tests for Save and Close / Cancel button semantics on IndicatorDialog
and _PerIndicatorDialog.

Covers: snapshot/restore, dirty tracking, button state, Ctrl+S binding,
WM_DELETE_WINDOW → cancel, save persistence, and per-indicator popup
cancel with scope-split revert.
"""

from __future__ import annotations

import tkinter as tk
from unittest import mock

import pytest

from tradinglab.indicators.base import LineStyle
from tradinglab.indicators.config import IndicatorConfig, IndicatorManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def root():
    """Headless Tk root with a stub _indicator_manager."""
    try:
        r = tk.Tk()
        r.withdraw()
    except tk.TclError:
        pytest.skip("No display available")
    mgr = IndicatorManager()
    r._indicator_manager = mgr  # type: ignore[attr-defined]
    r._indicator_dialog = None  # type: ignore[attr-defined]
    r._per_indicator_dialogs = {}  # type: ignore[attr-defined]
    # Stubs that the dialog pokes on the app.
    r._theme = {"win_bg": "#ffffff"}  # type: ignore[attr-defined]
    r.interval_var = tk.StringVar(r, value="1d")  # type: ignore[attr-defined]
    r._on_menu_save_config = mock.MagicMock()  # type: ignore[attr-defined]
    yield r
    try:
        r.destroy()
    except tk.TclError:
        pass


@pytest.fixture()
def manager(root):
    return root._indicator_manager


def _add_sma(manager: IndicatorManager, period: int = 20) -> IndicatorConfig:
    cfg = IndicatorConfig(kind_id="sma", params={"length": period},
                          display_name=f"SMA({period})")
    return manager.add(cfg)


# ---------------------------------------------------------------------------
# Main dialog — snapshot / cancel / save
# ---------------------------------------------------------------------------

class TestIndicatorDialogCancel:

    def test_cancel_reverts_added_indicator(self, root, manager):
        """Adding an indicator then pressing Cancel removes it."""
        from tradinglab.gui.indicator_dialog import IndicatorDialog

        assert len(manager) == 0
        dlg = IndicatorDialog(root)
        # Simulate adding an indicator through the manager.
        _add_sma(manager)
        assert len(manager) == 1
        dlg._on_cancel()
        assert len(manager) == 0, "Cancel should restore the pre-dialog state"

    def test_cancel_reverts_removed_indicator(self, root, manager):
        """Removing an indicator then pressing Cancel restores it."""
        from tradinglab.gui.indicator_dialog import IndicatorDialog

        cfg = _add_sma(manager, 50)
        assert len(manager) == 1
        dlg = IndicatorDialog(root)
        manager.remove(cfg.id)
        assert len(manager) == 0
        dlg._on_cancel()
        assert len(manager) == 1, "Cancel should restore the removed indicator"

    def test_cancel_no_op_when_clean(self, root, manager):
        """Cancel with no changes should not call load_dict."""
        from tradinglab.gui.indicator_dialog import IndicatorDialog

        _add_sma(manager)
        dlg = IndicatorDialog(root)
        with mock.patch.object(manager, "load_dict") as mock_load:
            dlg._on_cancel()
            mock_load.assert_not_called()

    def test_cancel_destroys_dialog(self, root, manager):
        """Cancel should destroy the Toplevel."""
        from tradinglab.gui.indicator_dialog import IndicatorDialog

        dlg = IndicatorDialog(root)
        root._indicator_dialog = dlg
        dlg._on_cancel()
        assert root._indicator_dialog is None

    def test_save_close_accepts_live_state_and_closes(self, root, manager):
        """Save and Close should accept the live state (keep changes
        for the session) and close the dialog without persisting to
        a JSON file."""
        from tradinglab.gui.indicator_dialog import IndicatorDialog

        _add_sma(manager)
        dlg = IndicatorDialog(root)
        root._indicator_dialog = dlg
        dlg._mark_dirty()
        dlg._on_save_close()
        # Dialog should be destroyed.
        assert root._indicator_dialog is None
        # The indicator should still be in the manager (not reverted).
        assert len(manager) == 1

    def test_save_close_prevents_cancel_revert(self, root, manager):
        """After Save and Close, the snapshot is discarded so a
        hypothetical cancel would not revert."""
        from tradinglab.gui.indicator_dialog import IndicatorDialog

        dlg = IndicatorDialog(root)
        _add_sma(manager)
        dlg._mark_dirty()
        # Save and Close discards the snapshot.
        assert dlg._snapshot is not None
        dlg._on_save_close()
        # Snapshot should be cleared.
        # (Dialog is destroyed, but we can verify the indicator persists.)
        assert len(manager) == 1


class TestIndicatorDialogDirty:

    def test_initially_clean(self, root, manager):
        from tradinglab.gui.indicator_dialog import IndicatorDialog
        dlg = IndicatorDialog(root)
        assert not dlg._dirty

    def test_add_marks_dirty(self, root, manager):
        from tradinglab.gui.indicator_dialog import IndicatorDialog
        dlg = IndicatorDialog(root)
        _add_sma(manager)
        root.update_idletasks()
        assert dlg._dirty

    def test_save_button_disabled_when_clean(self, root, manager):
        from tradinglab.gui.indicator_dialog import IndicatorDialog
        dlg = IndicatorDialog(root)
        btn = dlg._save_close_btn
        assert str(btn.cget("state")) == "disabled"

    def test_save_button_enabled_when_dirty(self, root, manager):
        from tradinglab.gui.indicator_dialog import IndicatorDialog
        dlg = IndicatorDialog(root)
        dlg._mark_dirty()
        btn = dlg._save_close_btn
        assert str(btn.cget("state")) == "normal"

    def test_dirty_title_has_bullet(self, root, manager):
        from tradinglab.gui.indicator_dialog import IndicatorDialog
        dlg = IndicatorDialog(root)
        dlg._mark_dirty()
        assert "\u2022" in dlg.title()

    def test_clean_title_no_bullet(self, root, manager):
        from tradinglab.gui.indicator_dialog import IndicatorDialog
        dlg = IndicatorDialog(root)
        assert "\u2022" not in dlg.title()


class TestIndicatorDialogKeyBindings:

    def test_escape_binding_exists(self, root, manager):
        """ESC should have a binding on the dialog."""
        from tradinglab.gui.indicator_dialog import IndicatorDialog
        dlg = IndicatorDialog(root)
        bindings = dlg.bind("<Escape>")
        assert bindings, "Escape should be bound"

    def test_ctrl_s_binding_exists(self, root, manager):
        """Ctrl+S should have a binding on the dialog."""
        from tradinglab.gui.indicator_dialog import IndicatorDialog
        dlg = IndicatorDialog(root)
        bindings = dlg.bind("<Control-s>")
        assert bindings, "Ctrl+S should be bound"

    def test_wm_delete_window_is_cancel(self, root, manager):
        """The X button should trigger cancel (revert) semantics.
        Verified by calling _on_close directly (which is the
        WM_DELETE_WINDOW handler) and checking it reverts."""
        from tradinglab.gui.indicator_dialog import IndicatorDialog

        dlg = IndicatorDialog(root)
        root._indicator_dialog = dlg
        _add_sma(manager)
        assert len(manager) == 1
        # _on_close is aliased to _on_cancel.
        dlg._on_close()
        assert len(manager) == 0


# ---------------------------------------------------------------------------
# Per-indicator popup — cancel reverts single config
# ---------------------------------------------------------------------------

class TestPerIndicatorDialogCancel:

    def test_cancel_reverts_param_change(self, root, manager):
        """Editing a param then cancelling restores the original value."""
        from tradinglab.gui.per_indicator_dialog import _PerIndicatorDialog

        cfg = _add_sma(manager, 20)
        dlg = _PerIndicatorDialog(root, cfg.id)
        # Simulate a param change through the manager.
        manager.update(cfg.id, params={"length": 50})
        dlg._mark_dirty()
        dlg._on_cancel()
        restored = manager.get(cfg.id)
        assert restored is not None
        assert restored.params.get("length") == 20

    def test_cancel_no_op_when_clean(self, root, manager):
        """Cancel with no changes should not touch the manager."""
        from tradinglab.gui.per_indicator_dialog import _PerIndicatorDialog

        cfg = _add_sma(manager, 20)
        original_params = dict(cfg.params)
        dlg = _PerIndicatorDialog(root, cfg.id)
        dlg._on_cancel()
        restored = manager.get(cfg.id)
        assert restored is not None
        assert restored.params == original_params

    def test_save_close_accepts_and_closes(self, root, manager):
        """Save and Close on the popup accepts the live state and closes."""
        from tradinglab.gui.per_indicator_dialog import _PerIndicatorDialog

        cfg = _add_sma(manager)
        dlg = _PerIndicatorDialog(root, cfg.id)
        manager.update(cfg.id, params={"length": 50})
        dlg._mark_dirty()
        dlg._on_save_close()
        # The change should be kept (not reverted).
        updated = manager.get(cfg.id)
        assert updated is not None
        assert updated.params.get("length") == 50

    def test_popup_dirty_title(self, root, manager):
        from tradinglab.gui.per_indicator_dialog import _PerIndicatorDialog

        cfg = _add_sma(manager)
        dlg = _PerIndicatorDialog(root, cfg.id)
        assert "\u2022" not in dlg.title()
        dlg._mark_dirty()
        dlg._refresh_title()
        assert "\u2022" in dlg.title()
