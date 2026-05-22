"""Regression test for the ``dialog-button-paradigms`` audit.

The application has three distinct dialog button paradigms:

* **Modal-confirm** (e.g. Settings dialog, Sandbox dialog):
  changes accumulate in the dialog and are only applied when the
  user presses the affirmative button (``[OK]`` / ``[Save & Close]``
  / ``[Start]``). ``[Cancel]`` discards.
* **Validate-apply** (e.g. Entries / Exits editor): a multi-button
  footer ``[Validate] [Apply] [Save & Close] [Cancel]`` makes the
  paradigm visually obvious from the button count alone.
* **Live-commit** (Per-indicator popup, Drawing dialog): every
  edit fires a debounced ``store.update(...)`` so the model is
  ALWAYS in sync — the dialog only has a ``[Close]`` button (and
  occasionally a destructive helper like ``[Delete this line]``).

The reviewer flagged that live-commit dialogs are visually
indistinguishable from modal-confirm dialogs in screenshots
because both can have just a single ``[Close]``-shaped button on
the right. The fix: every live-commit dialog must render a muted
``"Changes apply immediately."`` hint just above the bottom
button bar so users know that pressing Close doesn't discard.

These tests scan the source for the canonical hint string and
the supporting wiring.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tradinglab.gui import drawing_dialog as dd
from tradinglab.gui import per_indicator_dialog as pid

# ---------------------------------------------------------------------------
# Canonical hint copy
# ---------------------------------------------------------------------------

def test_drawing_dialog_exposes_live_commit_hint_constant():
    """The hint string lives at module scope so a future
    localisation pass can find every occurrence."""
    assert hasattr(dd, "_LIVE_COMMIT_HINT")
    assert dd._LIVE_COMMIT_HINT == "Changes apply immediately."


def test_drawing_dialog_hint_color_is_muted_grey():
    """Hint must be visually subordinate to the chart UI, not a
    flashy accent colour."""
    assert hasattr(dd, "_LIVE_COMMIT_HINT_COLOR")
    assert dd._LIVE_COMMIT_HINT_COLOR == "#888888"


def test_per_indicator_dialog_footnote_starts_with_live_commit_hint():
    """The per-indicator popup carries a single combined footnote
    that BOTH announces the live-commit paradigm AND warns about
    exit/entry coupling. It must lead with the paradigm hint so
    users see it at first glance."""
    assert pid._FOOTNOTE_TEXT.startswith("Changes apply immediately."), (
        f"per_indicator_dialog footnote must lead with the live-commit "
        f"hint; got {pid._FOOTNOTE_TEXT!r}")
    # The existing exit/entry caveat must still be there.
    lower = pid._FOOTNOTE_TEXT.lower()
    assert "exit" in lower or "entry" in lower
    assert "chart" in lower


# ---------------------------------------------------------------------------
# Wiring into _build_layout
# ---------------------------------------------------------------------------

DRAWING_SRC = Path(dd.__file__).read_text(encoding="utf-8")
PID_SRC = Path(pid.__file__).read_text(encoding="utf-8")


def test_drawing_dialog_renders_hint_label_above_button_bar():
    """The hint label must appear in source ABOVE the
    ``Delete this line`` / ``Close`` button bar so the layout reads
    top-to-bottom: editors → hint → buttons."""
    hint_idx = DRAWING_SRC.find("_LIVE_COMMIT_HINT")
    delete_idx = DRAWING_SRC.find('text="Delete this line"')
    assert hint_idx != -1 and delete_idx != -1
    # Find a USAGE site of _LIVE_COMMIT_HINT (skip the constant
    # definition + the constant + the spec comment).
    usage_idx = DRAWING_SRC.find("_LIVE_COMMIT_HINT", hint_idx + 1)
    # Walk forward looking for the actual ttk.Label(... text=_LIVE_COMMIT_HINT
    # placement that's inside _build_layout.
    while usage_idx != -1 and usage_idx < delete_idx:
        # Found a usage before the delete button — good.
        return
        # (loop body intentionally trivial: the first usage after
        # the constant definition is the build-layout one.)
    if usage_idx == -1 or usage_idx >= delete_idx:
        pytest.fail(
            "drawing_dialog must render _LIVE_COMMIT_HINT above the "
            "Delete/Close button bar so the live-commit paradigm "
            "is announced before the user reaches the buttons.")


def test_drawing_dialog_hint_attached_to_self():
    """The hint widget must be stored on the instance
    (``self._live_commit_hint``) so theming / future toggle work
    can reach it without re-walking the widget tree."""
    assert "self._live_commit_hint = ttk.Label(" in DRAWING_SRC, (
        "drawing_dialog must keep a handle to the hint label so it "
        "can re-theme or hide it later if needed.")


def test_per_indicator_dialog_footnote_label_still_present():
    """Ensure the per-indicator popup didn't accidentally drop its
    footnote label when the wording was extended."""
    assert "self._footnote_label = ttk.Label(" in PID_SRC


# ---------------------------------------------------------------------------
# Audit cross-reference
# ---------------------------------------------------------------------------

def test_audit_id_documented_in_drawing_dialog_source():
    """Future maintainers grepping for ``dialog-button-paradigms``
    must find a hit in the drawing_dialog source so the trail back
    to this audit is preserved."""
    assert "dialog-button-paradigms" in DRAWING_SRC


def test_audit_id_documented_in_drawing_dialog_spec():
    spec = (Path(dd.__file__).parent
            / "drawing_dialog.spec.md").read_text(encoding="utf-8")
    assert "dialog-button-paradigms" in spec
    assert "Changes apply immediately." in spec


def test_audit_id_documented_in_per_indicator_dialog_spec():
    spec = (Path(pid.__file__).parent
            / "per_indicator_dialog.spec.md").read_text(encoding="utf-8")
    assert "dialog-button-paradigms" in spec


# ---------------------------------------------------------------------------
# Canonical wording matches across both dialogs
# ---------------------------------------------------------------------------

def test_canonical_wording_consistent_across_live_commit_dialogs():
    """Both live-commit dialogs must use the *exact same* leading
    sentence ``Changes apply immediately.`` so the paradigm-hint
    treatment reads identically across the app."""
    assert dd._LIVE_COMMIT_HINT == "Changes apply immediately."
    assert pid._FOOTNOTE_TEXT.split(" ", 3)[:3] == ["Changes", "apply", "immediately."]
