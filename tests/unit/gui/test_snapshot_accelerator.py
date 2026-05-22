"""Tests for the Ctrl+Shift+S chart-snapshot accelerator.

Audit ID: ``chart-snapshot-help-shortcut``. The right-click
``Snapshot Chart\u2026`` menu entry already exists; the missing piece
was a keyboard shortcut + cheat-sheet entry. This file pins:

1. ``_on_accel_snapshot_chart`` is a callable method on ChartApp.
2. The Ctrl+Shift+S binding is wired in the ``__init__`` source.
3. The canvas right-click menu shows the ``Ctrl+Shift+S`` accelerator
   label next to "Snapshot Chart\u2026".
4. ``_keyboard_shortcut_groups()`` includes the entry under
   "Application".
5. The shortcut handler defers to ``_save_chart_snapshot`` (so any
   future change to the snapshot path automatically benefits the
   accelerator) and respects ``_global_shortcut_allowed``.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import tradinglab.app as _app_mod
from tradinglab.app import ChartApp
from tradinglab.gui.help_menu import _keyboard_shortcut_groups


class TestAcceleratorMethodExists:
    def test_on_accel_snapshot_chart_is_callable(self):
        method = getattr(ChartApp, "_on_accel_snapshot_chart", None)
        assert callable(method), (
            "ChartApp must expose _on_accel_snapshot_chart for the "
            "Ctrl+Shift+S accelerator binding (audit "
            "chart-snapshot-help-shortcut)."
        )

    def test_handler_returns_break_when_allowed(self):
        """Standard accelerator pattern: ``return 'break'`` so the
        keystroke isn't double-delivered to the focused widget."""
        stub = MagicMock()
        stub._global_shortcut_allowed.return_value = True
        stub._save_chart_snapshot = MagicMock(return_value=None)
        method = ChartApp._on_accel_snapshot_chart.__get__(stub, type(stub))
        result = method()
        assert result == "break"
        stub._save_chart_snapshot.assert_called_once_with()

    def test_handler_returns_none_when_typing(self):
        """When the user is typing in a Text / Entry widget, the
        accelerator must no-op so the user's keystroke survives."""
        stub = MagicMock()
        stub._global_shortcut_allowed.return_value = False
        stub._save_chart_snapshot = MagicMock(return_value=None)
        method = ChartApp._on_accel_snapshot_chart.__get__(stub, type(stub))
        result = method()
        assert result is None
        stub._save_chart_snapshot.assert_not_called()

    def test_handler_swallows_save_exceptions(self):
        """A crash in the file-dialog / savefig must not propagate to
        the global event loop (would freeze the chart)."""
        stub = MagicMock()
        stub._global_shortcut_allowed.return_value = True
        stub._save_chart_snapshot = MagicMock(
            side_effect=RuntimeError("disk full")
        )
        method = ChartApp._on_accel_snapshot_chart.__get__(stub, type(stub))
        result = method()
        # Still returns "break" so the keystroke is suppressed.
        assert result == "break"


class TestSourceLevelBindings:
    """Source-level assertions catch drift without needing a Tk root."""

    def _src(self) -> str:
        return Path(_app_mod.__file__).read_text(encoding="utf-8")

    def test_app_py_binds_control_shift_s(self):
        src = self._src()
        assert 'bind_all("<Control-Shift-S>"' in src, (
            "Ctrl+Shift+S accelerator must be bound app-wide via "
            "self.bind_all in ChartApp.__init__."
        )
        # Also check lowercase variant for Caps Lock resilience.
        assert 'bind_all("<Control-Shift-s>"' in src, (
            "Both uppercase and lowercase variants should be bound so "
            "the shortcut works regardless of Caps Lock state."
        )

    def test_app_py_binding_targets_snapshot_handler(self):
        src = self._src()
        assert "self._on_accel_snapshot_chart" in src, (
            "Ctrl+Shift+S must dispatch to "
            "_on_accel_snapshot_chart."
        )

    def test_canvas_menu_shows_accelerator_label(self):
        src = self._src()
        assert 'accelerator="Ctrl+Shift+S"' in src, (
            "Right-click 'Snapshot Chart…' entry must include the "
            "Ctrl+Shift+S accelerator label so users can discover it."
        )


class TestCheatSheetIncludesSnapshot:
    def test_application_group_lists_ctrl_shift_s(self):
        groups = dict(_keyboard_shortcut_groups())
        assert "Application" in groups
        shortcuts = {s for s, _ in groups["Application"]}
        assert "Ctrl+Shift+S" in shortcuts, (
            "Help → Keyboard Shortcuts must list Ctrl+Shift+S in the "
            "Application section so users can discover the snapshot "
            "shortcut."
        )

    def test_ctrl_shift_s_action_mentions_snapshot(self):
        groups = dict(_keyboard_shortcut_groups())
        actions_by_shortcut = {s: a for s, a in groups["Application"]}
        action = actions_by_shortcut.get("Ctrl+Shift+S", "")
        assert action, "Ctrl+Shift+S must be present"
        assert "snapshot" in action.lower(), (
            f"Action text for Ctrl+Shift+S should mention 'snapshot', "
            f"got {action!r}"
        )
