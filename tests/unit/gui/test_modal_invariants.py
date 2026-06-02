"""Modal-dialog invariant meta-tests.

Pins contracts about every ``BaseModalDialog`` subclass under
``src/tradinglab/gui/``. Currently:

1. **``protect_combobox_wheel(self)`` is called in __init__** —
   per AGENTS.md §7.11 the wheel-bombing landmine (silent
   ttk.Combobox / ttk.Spinbox value mutation when the user
   scrolls the form) is closed globally by every ``BaseModalDialog``
   calling ``protect_combobox_wheel(self)``. A new dialog that
   skips this call re-opens the landmine; this meta-test catches
   it at PR time.

Audit ``modal-invariants``.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GUI_DIR = _REPO_ROOT / "src" / "tradinglab" / "gui"


# Dialogs that legitimately don't need protect_combobox_wheel. Add only
# with a documented reason — usually because the dialog has NO ttk.Combobox
# or ttk.Spinbox in its widget tree (the only two widgets the guard
# neutralises).
_WHEEL_GUARD_EXEMPTIONS: dict[str, str] = {
    "BaseEditorDialog": "Abstract subclass; concrete dialogs call the guard.",
    "_WatchlistDialog": (
        "No ttk.Combobox / ttk.Spinbox — uses Treeview + Buttons + "
        "Labels only. The wheel-bombing landmine (§7.11) cannot fire "
        "without one of those two widgets."
    ),
    "DocViewerDialog": (
        "No ttk.Combobox / ttk.Spinbox — pure markdown rendering + "
        "sidebar TOC + back/forward buttons. The wheel-bombing "
        "landmine (§7.11) cannot fire without one of those two widgets."
    ),
}


def _discover_basemodal_subclasses() -> list[tuple[str, str, int]]:
    """Walk every .py under `src/tradinglab/gui/`; for each class
    whose bases mention BaseModalDialog or BaseEditorDialog, return
    `(rel_path, class_name, lineno)`.
    """
    out: list[tuple[str, str, int]] = []
    for py in sorted(_GUI_DIR.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        rel = py.relative_to(_REPO_ROOT).as_posix()
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            base_names: list[str] = []
            for base in node.bases:
                if isinstance(base, ast.Name):
                    base_names.append(base.id)
                elif isinstance(base, ast.Attribute):
                    base_names.append(base.attr)
            if any(
                b in {"BaseModalDialog", "BaseEditorDialog"}
                for b in base_names
            ):
                out.append((rel, node.name, node.lineno))
    return out


def _class_calls_protect_combobox_wheel(class_node: ast.ClassDef) -> bool:
    """Return True if any method on this class contains a call to
    ``protect_combobox_wheel(...)`` (any argument list).
    """
    for sub in ast.walk(class_node):
        if not isinstance(sub, ast.Call):
            continue
        callee = sub.func
        name: str | None = None
        if isinstance(callee, ast.Name):
            name = callee.id
        elif isinstance(callee, ast.Attribute):
            name = callee.attr
        if name == "protect_combobox_wheel":
            return True
    return False


def test_every_modal_dialog_applies_wheel_guard():
    """Per AGENTS.md §7.11: every ``BaseModalDialog`` subclass MUST
    call ``protect_combobox_wheel(self)`` in its __init__ (or anywhere
    in the class body) to neutralise the silent combobox/spinbox
    wheel-mutation landmine.

    A new dialog that forgets this call re-opens the landmine — every
    persisted dialog field becomes vulnerable to "user scrolls past a
    combobox while the form is scrollable" silent corruption.

    Audit ``modal-invariants``.
    """
    findings: list[str] = []
    for py in sorted(_GUI_DIR.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        rel = py.relative_to(_REPO_ROOT).as_posix()
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            base_names: list[str] = []
            for base in node.bases:
                if isinstance(base, ast.Name):
                    base_names.append(base.id)
                elif isinstance(base, ast.Attribute):
                    base_names.append(base.attr)
            if not any(
                b in {"BaseModalDialog", "BaseEditorDialog"}
                for b in base_names
            ):
                continue
            if node.name in _WHEEL_GUARD_EXEMPTIONS:
                continue
            if not _class_calls_protect_combobox_wheel(node):
                findings.append(
                    f"  - {node.name} (at {rel}:{node.lineno}) — add "
                    f"`protect_combobox_wheel(self)` to __init__ "
                    f"after the layout is built, OR add to "
                    f"_WHEEL_GUARD_EXEMPTIONS with a documented reason."
                )
    if findings:
        pytest.fail(
            "BaseModalDialog subclasses missing `protect_combobox_wheel` "
            "(AGENTS.md §7.11 landmine reopens):\n\n"
            + "\n".join(findings)
        )


def test_wheel_guard_exemptions_are_actually_present():
    """Catch stale entries in :data:`_WHEEL_GUARD_EXEMPTIONS`."""
    discovered = {n for _, n, _ in _discover_basemodal_subclasses()}
    # Both base classes are themselves Toplevel subclasses we discover.
    stale = sorted(
        name for name in _WHEEL_GUARD_EXEMPTIONS if name not in discovered
    )
    assert not stale, (
        "Stale entries in _WHEEL_GUARD_EXEMPTIONS — these classes "
        "are not BaseModalDialog subclasses:\n  - "
        + "\n  - ".join(stale)
    )
