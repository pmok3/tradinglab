"""Regression test for the ``button-order-windows`` audit.

Reviewers flagged that the application's modal dialogs used
inconsistent button orders — some put Cancel rightmost (Windows
convention), others put the affirmative action rightmost
(macOS/GNOME convention), and a few (the editor footer) put
Cancel in the *middle* which matches neither convention.

After the fix, every multi-button dialog in
``src/tradinglab/gui/`` follows the **Windows convention**:
affirmative actions on the left of the right-aligned button
group, dismiss action (Cancel/Close) rightmost.

The tests below introspect the source of each dialog and assert
the ``side="right"`` pack order so the dismiss action is packed
FIRST (Tk's ``side="right"`` reverses visual order, so the
first-packed button lands rightmost).

Why source introspection instead of widget-tree inspection
-----------------------------------------------------------
A live ``tk.Tk()`` on Windows hits the intermittent
``_tkinter.TclError: Can't find a usable init.tcl`` flake on
this machine — see ``tests/unit/gui/test_banner_checkbox_default.py``
for the same workaround. A source scan is also a more durable
contract: if someone reorders the pack calls in the future the
test fails with a useful diff regardless of whether the test
runner can spin up a real Tk root.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

GUI_DIR = Path(__file__).resolve().parents[3] / "src" / "tradinglab" / "gui"


def _read_source(name: str) -> str:
    path = GUI_DIR / name
    return path.read_text(encoding="utf-8")


def _pack_order(src: str, button_labels: list[str]) -> list[str]:
    """Return the visual left→right order of ``button_labels`` in
    ``src``.

    Scans for ``text="<label>"`` lines that are eventually followed
    by ``side="right"`` / ``side=tk.RIGHT``. The source-order of
    matches is reversed because ``side="right"`` reverses visual
    order (first-packed → rightmost).
    """
    found: list[tuple[int, str]] = []
    for label in button_labels:
        # The label may sit on the ``ttk.Button(...)`` line and the
        # ``.pack(side="right")`` clause may be on the same line or
        # the next few lines (line continuations with backslashes
        # or trailing commas / parens).
        pattern = re.compile(
            rf'text=(?:"|\'){re.escape(label)}(?:"|\')'
            r'[\s\S]{0,400}?'
            r'\.pack\s*\([\s\S]{0,200}?'
            r'side\s*=\s*(?:"right"|\'right\'|tk\.RIGHT)',
            re.MULTILINE,
        )
        for m in pattern.finditer(src):
            found.append((m.start(), label))
    found.sort()
    # Reverse: pack-order side="right" → visual right-to-left
    visual_left_to_right = [label for _, label in reversed(found)]
    return visual_left_to_right


# ---------------------------------------------------------------------------
# Individual dialogs
# ---------------------------------------------------------------------------

def test_entries_dialog_footer_order_windows():
    """``entries_dialog`` editor footer must be
    ``[Validate] [Apply] [Save & Close] [Cancel]``."""
    src = _read_source("entries_dialog.py")
    order = _pack_order(src, ["Validate", "Apply", "Save & Close", "Cancel"])
    assert order == ["Validate", "Apply", "Save & Close", "Cancel"], (
        f"entries_dialog footer order is {order!r}; expected Windows "
        "convention [Validate] [Apply] [Save & Close] [Cancel].")


def test_exits_dialog_footer_order_windows():
    """``exits_dialog`` editor footer must be
    ``[Validate] [Save] [Close]``."""
    src = _read_source("exits_dialog.py")
    order = _pack_order(src, ["Validate", "Save", "Close"])
    assert order == ["Validate", "Save", "Close"], (
        f"exits_dialog footer order is {order!r}; expected Windows "
        "convention [Validate] [Save] [Close].")


def test_sandbox_dialog_footer_order_windows():
    """``sandbox_dialog`` footer must be ``[Start] [Cancel]``."""
    src = _read_source("sandbox_dialog.py")
    order = _pack_order(src, ["Start", "Cancel"])
    assert order == ["Start", "Cancel"], (
        f"sandbox_dialog footer order is {order!r}; expected Windows "
        "convention [Start] [Cancel].")


def test_sandbox_review_dialog_footer_order_windows():
    """``sandbox_review_dialog`` OK/Cancel pair must be
    ``[OK] [Cancel]``.

    ``sandbox_review_dialog.py`` hosts three dialogs
    (``DecisionLogDialog``, ``PostTradeReviewDialog`` and
    ``TagsEditorDialog``); only ``TagsEditorDialog`` uses the OK/Cancel
    pair, so restrict the scan to its slice — otherwise the
    ``DecisionLogDialog`` Cancel button cross-matches and the whole-file
    scan sees a spurious second Cancel (same reasoning as the
    ``_SettingsDialog`` slice below).
    """
    src = _read_source("sandbox_review_dialog.py")
    start = src.find("class TagsEditorDialog")
    body = src[start:] if start != -1 else src
    order = _pack_order(body, ["OK", "Cancel"])
    assert order == ["OK", "Cancel"], (
        f"sandbox_review_dialog OK/Cancel order is {order!r}; "
        "expected Windows convention [OK] [Cancel].")


def test_decision_log_dialog_footer_order_windows():
    """``DecisionLogDialog`` footer must be ``[Log decision] [Cancel]``.

    The opt-in decision-logging dialog (added alongside blind-replay
    decision logging) shares ``sandbox_review_dialog.py`` with the
    OK/Cancel ``TagsEditorDialog``; restrict the scan to its class slice
    so the two dialogs' Cancel buttons don't cross-match.
    """
    src = _read_source("sandbox_review_dialog.py")
    start = src.find("class DecisionLogDialog")
    end = src.find("class PostTradeReviewDialog", start)
    body = src[start:end] if start != -1 and end != -1 else src
    order = _pack_order(body, ["Log decision", "Cancel"])
    assert order == ["Log decision", "Cancel"], (
        f"DecisionLogDialog footer order is {order!r}; expected Windows "
        "convention [Log decision] [Cancel].")


def test_pre_trade_dialog_footer_order_windows():
    """``pre_trade_dialog`` footer must be ``[Submit] [Cancel]``."""
    src = _read_source("pre_trade_dialog.py")
    order = _pack_order(src, ["Submit", "Cancel"])
    assert order == ["Submit", "Cancel"], (
        f"pre_trade_dialog footer order is {order!r}; expected Windows "
        "convention [Submit] [Cancel].")


def test_dialogs_settings_ok_cancel_order_windows():
    """The shared ``dialogs.py`` Settings affirmative/Cancel pair must
    be ``[Save and Close] [Cancel]``.

    ``dialogs.py`` hosts two dialogs (``_SettingsDialog`` and
    ``_WatchlistDialog``) that both end up with a ``Save and Close``
    button after the fix-8 rename; restrict the scan to the
    ``_SettingsDialog`` slice so we only check the dialog the user
    actually opens via ``File → Settings``.
    """
    src = _read_source("dialogs.py")
    start = src.find("class _SettingsDialog")
    end = src.find("class _WatchlistDialog", start)
    body = src[start:end] if start != -1 and end != -1 else src
    order = _pack_order(body, ["Save and Close", "Cancel"])
    assert order == ["Save and Close", "Cancel"], (
        f"dialogs.py settings Save and Close/Cancel order is {order!r}; "
        "expected Windows convention [Save and Close] [Cancel].")


def test_credentials_dialog_save_cancel_order_windows():
    """``credentials_dialog`` must be ``[Save & Close] [Cancel]``."""
    src = _read_source("credentials_dialog.py")
    order = _pack_order(src, ["Save & Close", "Cancel"])
    assert order == ["Save & Close", "Cancel"], (
        f"credentials_dialog footer order is {order!r}; expected Windows "
        "convention [Save & Close] [Cancel].")


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

def test_modal_base_editor_footer_order_windows():
    """``BaseEditorDialog._build_editor_footer`` must pack Cancel
    FIRST so it lands rightmost (Windows convention)."""
    src = _read_source("_modal_base.py")
    # Restrict to the body of _build_editor_footer.
    start = src.find("def _build_editor_footer(")
    assert start != -1, "BaseEditorDialog._build_editor_footer disappeared"
    end = src.find("def ", start + 10)
    body = src[start:end] if end != -1 else src[start:]
    order = _pack_order(body, ["Validate", "Apply", "Save & Close", "Cancel"])
    assert order == ["Validate", "Apply", "Save & Close", "Cancel"], (
        f"BaseEditorDialog editor footer order is {order!r}; expected "
        "Windows convention [Validate] [Apply] [Save & Close] [Cancel].")


# ---------------------------------------------------------------------------
# Anti-regression: no dialog should ever pack Cancel BEFORE the
# affirmative action when both use ``side="right"``.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "module_name, primary_label",
    [
        ("entries_dialog.py", "Save & Close"),
        ("exits_dialog.py", "Save"),
        ("sandbox_dialog.py", "Start"),
        ("sandbox_review_dialog.py", "OK"),
        ("pre_trade_dialog.py", "Submit"),
        ("dialogs.py", "Save and Close"),
        ("credentials_dialog.py", "Save & Close"),
    ],
)
def test_cancel_packed_before_primary(module_name: str, primary_label: str):
    """Across every multi-button dialog, the source-order line
    number of the ``Cancel`` (or ``Close`` for ``exits_dialog``)
    ``ttk.Button(...).pack(side="right")`` must be SMALLER than
    that of the affirmative action — because pack-first means
    rightmost, the Cancel-rightmost rule reduces to "Cancel-first
    in source"."""
    src = _read_source(module_name)
    cancel_label = "Close" if module_name == "exits_dialog.py" else "Cancel"
    cancel_pos = _pack_order(src, [cancel_label, primary_label])
    assert cancel_pos[-1] == cancel_label, (
        f"{module_name}: {cancel_label!r} must be the rightmost button "
        f"in its row but the actual order is {cancel_pos!r}.")
