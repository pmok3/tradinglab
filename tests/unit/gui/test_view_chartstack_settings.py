"""Tests for the View → ChartStack cascade.

Audit ``chartstack-fixed-preset`` + ``chartstack-menu-cascade``:
ChartStack lives as a **cascade** in the View menu (mirroring the
Heikin-Ashi cascade), containing:

- ``Show ChartStack`` — checkbutton bound to
  ``_chartstack_visible_var`` / ``_on_view_toggle_chartstack``
  (keeps the ``Ctrl+`` accelerator).
- ``Settings…`` — command (ellipsis: opens the
  :class:`ChartStackSettingsDialog` popup) wired to
  ``_on_view_chartstack_settings``.

The old flat layout — a top-level ``ChartStack`` checkbutton plus a
top-level ``ChartStack Settings…`` command — is gone; settings is now
a subset of the ChartStack cascade.

Source-grep style (no full ChartApp fixture), mirroring
``test_view_heatmap.py``.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MENU_BUILDER_PY = _REPO_ROOT / "src" / "tradinglab" / "gui" / "menu_builder.py"
_APP_PY = _REPO_ROOT / "src" / "tradinglab" / "app.py"
# The ChartStack callbacks were extracted from app.py to ChartStackAppMixin
# (gui/chartstack_app.py) in the wave-4 mixin extraction (AGENTS.md §7.24).
_CHARTSTACK_APP_PY = _REPO_ROOT / "src" / "tradinglab" / "gui" / "chartstack_app.py"

# Accept the ellipsis as either the literal U+2026 char or its
# ``\u2026`` Python string-escape — both are equivalent at runtime.
_ELLIPSIS = r"(?:\u2026|\\u2026)"


# ---------------------------------------------------------------------------
# Cascade structure (source-grep)
# ---------------------------------------------------------------------------


def test_view_menu_has_chartstack_cascade() -> None:
    src = _MENU_BUILDER_PY.read_text(encoding="utf-8")
    pattern = re.compile(
        r'view_menu\.add_cascade\(\s*label\s*=\s*"ChartStack"\s*,'
        r'\s*menu\s*=\s*cs_menu\s*,?\s*\)',
        re.DOTALL,
    )
    assert pattern.search(src), (
        "chartstack-menu-cascade regression: View menu must add a "
        '"ChartStack" cascade backed by ``cs_menu``.'
    )


def test_cascade_has_show_chartstack_checkbutton() -> None:
    src = _MENU_BUILDER_PY.read_text(encoding="utf-8")
    pattern = re.compile(
        r'cs_menu\.add_checkbutton\(\s*label\s*=\s*"Show ChartStack"',
        re.DOTALL,
    )
    assert pattern.search(src), (
        'ChartStack cascade must contain a "Show ChartStack" checkbutton.'
    )
    # Must still be wired to the same toggle var + command.
    assert "self._cb._chartstack_visible_var" in src
    assert "self._cb._on_view_toggle_chartstack" in src


def test_cascade_has_settings_command_with_ellipsis() -> None:
    src = _MENU_BUILDER_PY.read_text(encoding="utf-8")
    pattern = re.compile(
        r'cs_menu\.add_command\(\s*label\s*=\s*"Settings' + _ELLIPSIS + r'"\s*,'
        r'\s*command\s*=\s*self\._cb\._on_view_chartstack_settings\s*,?\s*\)',
        re.DOTALL,
    )
    assert pattern.search(src), (
        'ChartStack cascade must contain a "Settings\u2026" command '
        "(ellipsis since it opens a dialog) wired to "
        "self._cb._on_view_chartstack_settings."
    )


def test_old_flat_chartstack_entries_are_gone() -> None:
    """The pre-cascade layout must not linger — no top-level
    ``ChartStack`` checkbutton, no top-level ``ChartStack Settings…``
    command."""
    src = _MENU_BUILDER_PY.read_text(encoding="utf-8")
    assert 'view_menu.add_checkbutton(\n            label="ChartStack"' not in src, (
        "old top-level ChartStack checkbutton must be removed (it now "
        "lives inside the ChartStack cascade as 'Show ChartStack')."
    )
    assert '"ChartStack Settings\u2026"' not in src and \
           '"ChartStack Settings\\u2026"' not in src, (
        "old top-level 'ChartStack Settings…' command must be removed "
        "(settings is now the cascade child 'Settings…')."
    )


def test_menu_builder_protocol_declares_callbacks() -> None:
    src = _MENU_BUILDER_PY.read_text(encoding="utf-8")
    assert "def _on_view_chartstack_settings(self)" in src
    assert "def _on_view_toggle_chartstack(self)" in src


def test_chart_app_defines_settings_callback() -> None:
    # Extracted to ChartStackAppMixin (gui/chartstack_app.py); ChartApp still
    # exposes it via inheritance. Read the mixin source where the def now lives.
    src = _CHARTSTACK_APP_PY.read_text(encoding="utf-8")
    assert re.search(r"^\s*def _on_view_chartstack_settings\(self\)",
                     src, re.MULTILINE), (
        "ChartStackAppMixin must define _on_view_chartstack_settings."
    )


# ---------------------------------------------------------------------------
# Callback behaviour
# ---------------------------------------------------------------------------


def _bind_callback() -> tuple[SimpleNamespace, callable]:
    import tradinglab.app as app_mod
    stub = SimpleNamespace()
    return stub, app_mod.ChartApp._on_view_chartstack_settings.__get__(stub)


def test_callback_invokes_open_helper() -> None:
    stub, cb = _bind_callback()
    mock = MagicMock()
    with patch(
        "tradinglab.gui.chartstack_settings_dialog.open_chartstack_settings",
        mock,
    ):
        cb()
    assert mock.called
    args, _kwargs = mock.call_args
    assert args[0] is stub


def test_callback_swallows_open_exception() -> None:
    stub, cb = _bind_callback()
    with patch(
        "tradinglab.gui.chartstack_settings_dialog.open_chartstack_settings",
        side_effect=RuntimeError("boom"),
    ):
        cb()  # must NOT raise
