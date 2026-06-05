"""Tests for the View → ChartStack Settings… menu entry.

Audit ``chartstack-fixed-preset``: View menu wires a
``ChartStack Settings…`` command (ellipsis since it opens a dialog
per the ``ellipsis-semantics`` convention) that opens the
:class:`ChartStackSettingsDialog` via
``gui.chartstack_settings_dialog.open_chartstack_settings``.

Source-grep style (no full ChartApp fixture) — mirrors the
``test_view_heatmap.py`` shape.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MENU_BUILDER_PY = _REPO_ROOT / "src" / "tradinglab" / "gui" / "menu_builder.py"
_APP_PY = _REPO_ROOT / "src" / "tradinglab" / "app.py"


# ---------------------------------------------------------------------------
# Menu wiring (source-grep)
# ---------------------------------------------------------------------------


def test_view_menu_has_chartstack_settings_entry() -> None:
    src = _MENU_BUILDER_PY.read_text(encoding="utf-8")
    # The label in source may be the literal U+2026 char OR the
    # ``\u2026`` Python string-escape — both are equivalent at
    # runtime. The regex character-class accepts both spellings.
    ellipsis_re = r"(?:\u2026|\\u2026)"
    pattern = re.compile(
        r'view_menu\.add_command\(\s*label\s*=\s*"ChartStack Settings'
        + ellipsis_re
        + r'"\s*,\s*command\s*=\s*self\._cb\._on_view_chartstack_settings'
        r'\s*,?\s*\)',
        re.DOTALL,
    )
    assert pattern.search(src), (
        "chartstack-fixed-preset regression: View menu must add a "
        '"ChartStack Settings\u2026" command wired to '
        "self._cb._on_view_chartstack_settings."
    )


def test_chartstack_settings_label_has_ellipsis() -> None:
    """Opens a dialog → label MUST end in U+2026 per the
    ``ellipsis-semantics`` audit (Apple HIG / MS UWP)."""
    src = _MENU_BUILDER_PY.read_text(encoding="utf-8")
    has_literal = '"ChartStack Settings\u2026"' in src
    has_escape = '"ChartStack Settings\\u2026"' in src
    assert has_literal or has_escape, (
        'ChartStack Settings\u2026 must end in U+2026 (opens a dialog)'
    )
    assert '"ChartStack Settings"' not in src, (
        'ellipsis-semantics regression: the bare "ChartStack Settings" '
        "(no ellipsis) label was reintroduced; it must end in U+2026."
    )


def test_menu_builder_protocol_declares_callback() -> None:
    src = _MENU_BUILDER_PY.read_text(encoding="utf-8")
    assert "def _on_view_chartstack_settings(self)" in src, (
        "MenuBuilderCallbacks protocol must declare "
        "_on_view_chartstack_settings."
    )


def test_chart_app_defines_callback() -> None:
    src = _APP_PY.read_text(encoding="utf-8")
    assert re.search(r"^\s*def _on_view_chartstack_settings\(self\)",
                     src, re.MULTILINE), (
        "ChartApp must define _on_view_chartstack_settings; the menu "
        "entry routes through self._cb._on_view_chartstack_settings."
    )


# ---------------------------------------------------------------------------
# Callback behaviour
# ---------------------------------------------------------------------------


def _bind_callback() -> tuple[SimpleNamespace, callable]:
    """Bind ``ChartApp._on_view_chartstack_settings`` to a stub
    ``self`` that only carries the attributes the method touches."""
    import tradinglab.app as app_mod
    stub = SimpleNamespace()
    return stub, app_mod.ChartApp._on_view_chartstack_settings.__get__(stub)


def test_callback_invokes_open_helper() -> None:
    """Callback delegates to
    ``gui.chartstack_settings_dialog.open_chartstack_settings``
    with the ChartApp as parent."""
    stub, cb = _bind_callback()
    mock = MagicMock()
    with patch(
        "tradinglab.gui.chartstack_settings_dialog."
        "open_chartstack_settings",
        mock,
    ):
        cb()
    assert mock.called, (
        "_on_view_chartstack_settings must call "
        "open_chartstack_settings(self)"
    )
    args, _kwargs = mock.call_args
    assert args[0] is stub, (
        "the first positional arg should be the ChartApp (self)"
    )


def test_callback_swallows_open_exception() -> None:
    """If the popup fails to construct (e.g. Tk init failure on a
    headless run), the callback must not propagate the exception
    into the Tk event loop."""
    stub, cb = _bind_callback()
    with patch(
        "tradinglab.gui.chartstack_settings_dialog."
        "open_chartstack_settings",
        side_effect=RuntimeError("boom"),
    ):
        cb()  # must NOT raise
