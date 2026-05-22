"""Test that drawing context-menu labels use Title Case.

Audit ID: ``edit-properties-casing``. The legend right-click menu
used Title Case (``"Edit Settings…"``, ``"Change Color…"``) while
the drawing right-click menu used sentence case (``"Edit
properties…"``, ``"Delete this line"``). Normalised to Title Case
for visual consistency across all right-click context menus.

Pins:

* The drawing context menu uses ``"Edit Properties…"`` / ``"Delete
  This Line"``.
* ``app.spec.md`` documents the new labels.
* The pattern matches the legend menu's ``"Edit Settings…"`` casing.
"""
from __future__ import annotations

import re
from pathlib import Path

import tradinglab


def _read(rel: str) -> str:
    pkg = Path(tradinglab.__file__).resolve().parent
    return (pkg / rel).read_text(encoding="utf-8")


class TestEditPropertiesCasing:

    def test_app_drawing_menu_uses_title_case(self):
        src = _read("app.py")
        # "Edit Properties…" labels (Title Case).
        assert 'label="Edit Properties\u2026"' in src or \
            'label="Edit Properties…"' in src, (
            "drawing right-click menu must use 'Edit Properties…' "
            "(Title Case, was sentence case 'Edit properties…')"
        )
        # "Delete This Line" labels (Title Case).
        assert 'label="Delete This Line"' in src, (
            "drawing right-click menu must use 'Delete This Line' "
            "(Title Case, was 'Delete this line')"
        )

    def test_app_drawing_menu_does_not_use_old_sentence_case(self):
        src = _read("app.py")
        # Reject the old sentence-case forms.
        assert "Edit properties\u2026" not in src, (
            "'Edit properties…' (sentence case) still present"
        )
        assert "Edit properties" not in re.sub(
            r"Edit properties\u2026", "", src), (
            "'Edit properties' substring leftover"
        )
        assert "Delete this line" not in src, (
            "'Delete this line' (sentence case) still present"
        )

    def test_spec_documents_new_labels(self):
        spec = _read("app.spec.md")
        assert "Edit Properties\u2026" in spec or \
            "Edit Properties…" in spec, (
            "app.spec.md should document 'Edit Properties…' label"
        )
        assert "Delete This Line" in spec, (
            "app.spec.md should document 'Delete This Line' label"
        )

    def test_legend_menu_title_case_still_in_place(self):
        """Sanity check: the legend right-click menu's Title Case
        labels (which were already correct) are unchanged."""
        src = _read("app.py")
        assert "Edit Settings" in src, (
            "Legend menu 'Edit Settings…' label missing"
        )
        assert "Change Color" in src, (
            "Legend menu 'Change Color…' label missing"
        )
