"""Regression tests for the ``remove-vs-delete-verb`` audit.

Drawing user-surface verbs were inconsistent: the chart canvas
right-click menu offered "Remove All Drawings on <TICKER>"
while the per-line right-click menu offered "Delete This Line".
The 1-star reviewer flagged this as confusing — a single
operation type (drawing destruction) used two different verbs
depending only on cardinality, with no signalled convention.

The fix locks the convention:

* **Delete** — single-item destructive operations
  ("Delete This Line" / "Delete this line").
* **Clear** — bulk destructive operations
  ("Clear All Drawings" / "Clear All Drawings on <TICKER>").

These tests pin the convention by scanning the production
sources for the forbidden phrasings and asserting the new
phrasings appear in the right places.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import tradinglab
import tradinglab.app
import tradinglab.drawings.store
import tradinglab.gui.drawing_dialog
import tradinglab.gui.drawings_app
import tradinglab.gui.help_menu
import tradinglab.gui.interaction


def _read(mod) -> str:
    text = Path(mod.__file__).read_text(encoding="utf-8")
    # Canvas-menu + per-line context menu builders were extracted
    # from app.py to gui/drawings_app.py (DrawingsAppMixin).
    # Concatenate so the verb-convention pins still anchor on the
    # production code path.
    if mod is tradinglab.app:
        text += "\n" + Path(
            tradinglab.gui.drawings_app.__file__
        ).read_text(encoding="utf-8")
    return text


# ---------------------------------------------------------------------------
# Required new phrasings (must appear)
# ---------------------------------------------------------------------------

class TestRequiredClearPhrasings:

    def test_app_py_menu_label_uses_clear_all_drawings_on_ticker(self):
        src = _read(tradinglab.app)
        # Menu label is interpolated with the ticker.
        assert 'f"Clear All Drawings on {ticker}"' in src, (
            "Chart canvas menu label must read "
            "'Clear All Drawings on <TICKER>' (audit "
            "remove-vs-delete-verb).")

    def test_app_py_confirm_title_is_clear_all_drawings(self):
        src = _read(tradinglab.app)
        assert '"Clear All Drawings"' in src, (
            "Confirm dialog title must be 'Clear All Drawings' "
            "(audit remove-vs-delete-verb).")

    def test_app_py_confirm_body_uses_clear_verb(self):
        src = _read(tradinglab.app)
        # Body text: "Clear N drawing{plural} on {sym}? ..."
        assert 'f"Clear {count} drawing{plural} on {sym}? "' in src, (
            "Confirm dialog body must start with the 'Clear' verb "
            "(audit remove-vs-delete-verb).")

    def test_app_py_per_line_menu_uses_delete_verb(self):
        src = _read(tradinglab.app)
        # Per-line context menu — single-item, "Delete" verb.
        assert '"Delete This Line"' in src, (
            "Per-line context menu must use 'Delete This Line' "
            "(audit remove-vs-delete-verb).")

    def test_drawing_dialog_button_uses_delete_verb(self):
        src = _read(tradinglab.gui.drawing_dialog)
        assert '"Delete this line"' in src, (
            "Drawing dialog destructive button must read "
            "'Delete this line' (audit remove-vs-delete-verb).")


# ---------------------------------------------------------------------------
# Forbidden old phrasings (must NOT appear anywhere user-visible)
# ---------------------------------------------------------------------------

# Walk every .py file in src/tradinglab and ensure the old "Remove ..."
# wordings are gone from user-facing string literals. Docstrings /
# comments are scanned too because they leak into IDE hovers and
# `help()` output that traders sometimes inspect.
def _all_src_files() -> list[Path]:
    pkg_root = Path(tradinglab.__file__).resolve().parent
    return [p for p in pkg_root.rglob("*.py") if p.is_file()]


_FORBIDDEN = (
    "Remove All Drawings",
    "Remove all drawings",
    "remove all drawings",
)


@pytest.mark.parametrize("phrase", _FORBIDDEN)
def test_forbidden_phrasing_absent_from_src(phrase):
    offenders: list[tuple[str, int, str]] = []
    for path in _all_src_files():
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if phrase in line:
                offenders.append((str(path), i, line.strip()))
    assert offenders == [], (
        f"Forbidden phrase {phrase!r} still present in src/tradinglab "
        f"(audit remove-vs-delete-verb). The verb convention is: "
        f"Delete for single-item, Clear for bulk. Offenders: "
        f"{offenders}"
    )


@pytest.mark.parametrize("phrase", ["Remove All Drawings", "Remove all drawings"])
def test_forbidden_phrasing_absent_from_spec_md(phrase):
    """Spec.md files in src/tradinglab/**.spec.md describe behaviour
    — leaving the old wording there would invite a future maintainer
    to flip the code back."""
    pkg_root = Path(tradinglab.__file__).resolve().parent
    offenders: list[tuple[str, int, str]] = []
    for path in pkg_root.rglob("*.spec.md"):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if phrase in line:
                offenders.append((str(path), i, line.strip()))
    assert offenders == [], (
        f"Forbidden phrase {phrase!r} present in spec.md; spec docs "
        f"should describe the new wording (audit "
        f"remove-vs-delete-verb). Offenders: {offenders}"
    )


# ---------------------------------------------------------------------------
# Help menu shortcut sheet uses the new verb
# ---------------------------------------------------------------------------

class TestHelpMenuShortcutSheet:

    def test_help_menu_describes_canvas_menu_with_clear_verb(self):
        src = _read(tradinglab.gui.help_menu)
        assert "clear all" in src.lower(), (
            "Help menu shortcut sheet should describe the canvas "
            "right-click menu as offering 'clear all' (audit "
            "remove-vs-delete-verb).")
        assert "remove all" not in src.lower(), (
            "Help menu shortcut sheet still mentions 'remove all'; "
            "the verb convention is Clear for bulk operations "
            "(audit remove-vs-delete-verb).")


# ---------------------------------------------------------------------------
# Comments and docstrings on the menu builders mention the audit
# so anyone who breaks it has a search anchor.
# ---------------------------------------------------------------------------

class TestAuditAnchorsPresent:

    def test_app_py_show_chart_canvas_menu_mentions_audit(self):
        src = _read(tradinglab.app)
        # Find the _show_chart_canvas_menu method.
        m = re.search(
            r"def _show_chart_canvas_menu\([^)]*\)[^:]*:\n"
            r"(?P<body>.*?)(?=\n    def |\Z)",
            src,
            re.DOTALL,
        )
        assert m, "could not locate _show_chart_canvas_menu definition"
        body = m.group("body")
        assert "remove-vs-delete-verb" in body, (
            "The audit ID should be referenced near the menu builder "
            "so the verb convention is discoverable.")
