"""Pin the menu-ellipsis convention to "ellipsis iff opens a dialog".

Audit finding ``ellipsis-semantics`` (1 of 75): the Help menu was
inconsistent. ``View Online Docs…`` ended in an ellipsis even though
clicking it just hands off to ``webbrowser.open()`` (no input UI), and
``Getting Started`` did NOT end in an ellipsis even though clicking it
opens an in-app doc viewer (a modal Toplevel).

Convention picked (Apple HIG / MS UWP): a menu label ends in a U+2026
``…`` iff clicking it requires more user input — i.e. it opens a
dialog, file chooser, doc viewer, settings sheet, or wizard. Labels
that perform an immediate action (toggle a setting, navigate, run a
URL hand-off, copy to clipboard) MUST NOT end in an ellipsis.

This test pins the specific Help-menu labels the audit called out and
sanity-checks that no other Help-menu label regresses back to the old
shape.
"""

from __future__ import annotations

import re
from pathlib import Path

HELP_MENU_PY = (
    Path(__file__).resolve().parents[3]
    / "src" / "tradinglab" / "gui" / "help_menu.py"
)


_LABEL_RE = re.compile(
    r"""\.add_command\s*\(
        [^)]*?
        \blabel\s*=\s*
        (?:
            f?"((?:[^"\\]|\\.)*)"
          | f?'((?:[^'\\]|\\.)*)'
        )
    """,
    re.VERBOSE | re.DOTALL,
)


def _help_menu_labels() -> list[str]:
    src = HELP_MENU_PY.read_text(encoding="utf-8")
    out: list[str] = []
    for m in _LABEL_RE.finditer(src):
        out.append(m.group(1) if m.group(1) is not None else m.group(2))
    return out


# ---- Pinned positive assertions -------------------------------------------

# "View Online Docs" must NOT end in an ellipsis (opens a URL via the
# default browser; no dialog rendered).
def test_view_online_docs_has_no_ellipsis() -> None:
    labels = _help_menu_labels()
    has_clean = "View Online Docs" in labels
    has_dirty = "View Online Docs\u2026" in labels
    assert has_clean and not has_dirty, (
        "ellipsis-semantics regression: 'View Online Docs' must NOT end "
        "in an ellipsis (it hands off to webbrowser.open without showing "
        f"a dialog). Help-menu labels currently: {labels}"
    )


# "Getting Started…" must end in an ellipsis (opens a doc viewer
# Toplevel — same UX category as ChartStack Guide… and Documentation
# Library… which already have the ellipsis).
def test_getting_started_has_ellipsis() -> None:
    labels = _help_menu_labels()
    has_dotted = "Getting Started\u2026" in labels
    has_undotted = "Getting Started" in labels
    assert has_dotted and not has_undotted, (
        "ellipsis-semantics regression: 'Getting Started' must end in an "
        "ellipsis — it opens the in-app doc viewer (same UX category as "
        "ChartStack Guide… / Documentation Library…). Help-menu labels "
        f"currently: {labels}"
    )


# ---- Sanity assertions: items that opens a dialog must keep the ellipsis ---

DIALOG_BEARING_LABELS = (
    "About TradingLab\u2026",
    "Keyboard Shortcuts\u2026",
    "ChartStack Guide\u2026",
    "Documentation Library\u2026",
    "Export Diagnostic Bundle\u2026",
)


def test_dialog_bearing_labels_keep_their_ellipsis() -> None:
    labels = _help_menu_labels()
    missing = [s for s in DIALOG_BEARING_LABELS if s not in labels]
    assert not missing, (
        "ellipsis-semantics regression: these Help-menu entries open a "
        f"dialog/wizard but no longer end in an ellipsis: {missing}. "
        f"Labels seen: {labels}"
    )


# ---- Action-only items must NOT have an ellipsis --------------------------

ACTION_ONLY_LABELS = ("Reveal Data Folder",)


def test_action_only_labels_have_no_ellipsis() -> None:
    labels = _help_menu_labels()
    offenders: list[str] = []
    for action in ACTION_ONLY_LABELS:
        if action + "\u2026" in labels:
            offenders.append(action + "\u2026")
    assert not offenders, (
        "ellipsis-semantics regression: these labels perform an immediate "
        "non-dialog action (e.g. opening the file manager) and MUST NOT "
        f"end in an ellipsis: {offenders}. Labels seen: {labels}"
    )
