"""Test that ellipsis is encoded as the literal U+2026 character.

Audit ID: ``ellipsis-encoding``. ``app.py`` mixed Python escape
sequences (``"\\u2026"``) and raw glyphs (``"\u2026"``) for the
same ellipsis character across menu labels, status messages, and
dialog titles. The two forms decode identically at runtime, but
the mixed source is hard to read at a glance: a reviewer skimming
labels can't tell which menu entries actually end with an ellipsis
without mentally evaluating each escape.

Normalised to the literal character throughout
``src/tradinglab/**/*.py``. This test pins the convention so
future drift is caught immediately.
"""
from __future__ import annotations

from pathlib import Path

import tradinglab


def _repo_root() -> Path:
    pkg = Path(tradinglab.__file__).resolve().parent
    return pkg.parent.parent


class TestEllipsisEncoding:

    def test_no_unicode_escape_in_src(self):
        root = _repo_root() / "src" / "tradinglab"
        offenders: list[tuple[str, int, str]] = []
        for path in root.rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                if "\\u2026" in line:
                    offenders.append((str(path), i, line.strip()))
        assert offenders == [], (
            "Found Python escape '\\u2026' in source; should be "
            f"literal '\u2026' (U+2026) glyph. Offenders: {offenders}"
        )

    def test_literal_ellipsis_present_somewhere(self):
        """Positive sanity check: at least one literal '\u2026'
        appears in source (otherwise the normalisation didn't take
        and we're trivially passing the negative test above)."""
        root = _repo_root() / "src" / "tradinglab"
        found = False
        for path in root.rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001
                continue
            if "\u2026" in text:
                found = True
                break
        assert found, (
            "Expected at least one literal '\u2026' glyph in source"
        )

    def test_help_menu_keyboard_shortcuts_label_uses_literal(self):
        """End-to-end check: the cheat-sheet menu label, which is
        a recent addition, uses the literal glyph."""
        from tradinglab.gui import help_menu as _hm
        src = Path(_hm.__file__).read_text(encoding="utf-8")
        assert "Keyboard Shortcuts\u2026" in src, (
            "Help menu label 'Keyboard Shortcuts…' should use the "
            "literal U+2026 ellipsis"
        )
        assert "Keyboard Shortcuts\\u2026" not in src, (
            "Help menu label should not use the '\\u2026' escape form"
        )
