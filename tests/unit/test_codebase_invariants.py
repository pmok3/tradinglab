"""Codebase-wide invariant meta-tests.

These tests pin contracts that span the whole codebase (no specific
subsystem) and fire at PR time when a developer violates one. Each
contract has its own ``_..._EXEMPTIONS`` dict for documented
grandfathered cases.

Contracts in this file:

1. **Spec.md coverage** — every ``.py`` under ``src/tradinglab/``
   (excluding ``__init__.py``) has a colocated ``.spec.md``. HARD
   RULE per AGENTS.md §2 — this is the first CI gate enforcing it.

2. **ChartApp MRO structural invariants** (per AGENTS.md §7.24):
   - ``tk.Tk`` is the LAST base of ``ChartApp``.
   - No mixin defines ``__init__`` (adding one breaks the MRO
     chain to ``tk.Tk`` which is positional-arg sensitive).

3. **TriggerKind dispatch completeness** (per AGENTS.md §7.20):
   - Every member of ``entries.model.TriggerKind`` is a key in
     ``entries.dispatch._ENTRY_DISPATCH``.
   - Every member of ``exits.model.TriggerKind`` is a key in
     ``exits.dispatch._EXIT_DISPATCH``.
   Adding a new TriggerKind without registering a handler would
   silently produce "fires nothing" in both live + mechanical
   evaluators — the worst kind of regression.

4. **``ZoneInfo("America/New_York")`` only in ``core/timezones.py``**
   (per AGENTS.md §7.23): every ET lookup must go through the
   ``core.timezones`` helpers so the missing-tzdata fallback policy
   is uniform.

Audit ``codebase-invariants``.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src" / "tradinglab"


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------


def _all_py_files() -> list[Path]:
    return [
        p
        for p in sorted(_SRC.rglob("*.py"))
        if "__pycache__" not in p.parts and p.name != "__init__.py"
    ]


# ---------------------------------------------------------------------------
# 1. Spec.md coverage
# ---------------------------------------------------------------------------


# Files that legitimately don't need a `.spec.md` (e.g. compiled/generated
# code or trivial shim modules). Add ONLY with a documented reason.
_SPEC_COVERAGE_EXEMPTIONS: dict[str, str] = {
    # (none today — every .py has its spec)
}


def test_every_module_has_colocated_spec_md():
    """HARD RULE per AGENTS.md §2: every `.py` under `src/tradinglab/`
    (excluding `__init__.py`) has a colocated `.spec.md`.

    Audit ``codebase-invariants``.
    """
    missing: list[str] = []
    for py in _all_py_files():
        rel = py.relative_to(_REPO_ROOT).as_posix()
        if rel in _SPEC_COVERAGE_EXEMPTIONS:
            continue
        if not py.with_suffix(".spec.md").exists():
            missing.append(rel)
    if missing:
        pytest.fail(
            "Modules without a colocated .spec.md (HARD RULE per "
            "AGENTS.md §2):\n  - " + "\n  - ".join(missing)
            + "\n\nCreate a sibling .spec.md (see docs/SPEC_STYLE.md) "
            "OR add to _SPEC_COVERAGE_EXEMPTIONS with a reason."
        )


def test_no_orphan_spec_md_files():
    """Every `.spec.md` must have a sibling `.py` file. Catches stale
    specs left behind when a module is renamed or removed.
    """
    orphans: list[str] = []
    for md in sorted(_SRC.rglob("*.spec.md")):
        if "__pycache__" in md.parts:
            continue
        # Top-level spec.md (one per phase) is allowed without a sibling .py.
        if md.name == "spec.md":
            continue
        py = md.with_suffix("").with_suffix(".py")
        if not py.exists():
            orphans.append(md.relative_to(_REPO_ROOT).as_posix())
    if orphans:
        pytest.fail(
            "Orphan .spec.md files (no sibling .py):\n  - "
            + "\n  - ".join(orphans)
            + "\n\nRemove them OR rename to match the .py file."
        )


# ---------------------------------------------------------------------------
# 2. ChartApp MRO structural invariants
# ---------------------------------------------------------------------------


def test_chartapp_mixins_have_no_init_method():
    """Per AGENTS.md §7.24, no mixin in the ChartApp MRO may define
    ``__init__`` or call ``super().__init__()``. All instance state
    lives in ``ChartApp.__init__``; adding ``__init__`` to a mixin
    breaks the MRO chain at ``tk.Tk`` (which is positional-arg
    sensitive).
    """
    import tkinter as tk

    from tradinglab.app import ChartApp

    offenders: list[str] = []
    for base in ChartApp.__bases__:
        if base is tk.Tk:
            continue
        if "__init__" in base.__dict__:
            offenders.append(f"{base.__module__}.{base.__name__}")
    if offenders:
        pytest.fail(
            "Mixins with __init__ (would break MRO chaining to tk.Tk):"
            "\n  - " + "\n  - ".join(offenders)
            + "\n\nMove the state to ChartApp.__init__ and remove the "
            "mixin's __init__ method."
        )


def test_chartapp_last_base_is_tk_tk():
    """Per AGENTS.md §7.24, ``tk.Tk`` MUST stay the final base in
    ``ChartApp.__bases__``. Anything else there would break the Tk
    bootstrap.
    """
    import tkinter as tk

    from tradinglab.app import ChartApp

    last = ChartApp.__bases__[-1]
    assert last is tk.Tk, (
        f"ChartApp's last base is {last.__name__}, not tk.Tk — this "
        "breaks the Tk bootstrap. Reorder __bases__ so tk.Tk is last."
    )


def test_app_spec_md_mro_matches_real_chartapp_bases():
    """The ``class ChartApp(...)`` declaration in ``app.spec.md`` MUST
    list exactly the real ``ChartApp.__bases__`` (same names, same
    order). Guards the exact spec drift found in the 2026-06 audit, where
    wave-3 added ``SandboxAppMixin`` / ``ScannerAppMixin`` to the class
    but not to the spec declaration. The other MRO gates check the CODE
    side; this one pins the SPEC side so the two can't diverge silently.

    Audit ``codebase-invariants``.
    """
    import re

    from tradinglab.app import ChartApp

    real = [b.__name__ for b in ChartApp.__bases__]  # tk.Tk -> "Tk"

    spec = (_SRC / "app.spec.md").read_text(encoding="utf-8")
    match = re.search(r"class ChartApp\((.*?)\)", spec, flags=re.DOTALL)
    assert match, (
        "app.spec.md must document the `class ChartApp(...)` MRO line"
    )
    # Normalise: the spec writes the final base as ``tk.Tk`` while
    # ``__name__`` is ``Tk`` — compare on the last dotted component.
    spec_bases = [
        tok.strip().split(".")[-1]
        for tok in match.group(1).split(",")
        if tok.strip()
    ]
    assert spec_bases == real, (
        "app.spec.md ChartApp MRO is out of sync with the real class.\n"
        f"  spec: {spec_bases}\n  code: {real}\n"
        "Update the `class ChartApp(...)` line in app.spec.md (and the "
        "§11/§12 mixin lists) to match src/tradinglab/app.py."
    )


# ---------------------------------------------------------------------------
# 3. TriggerKind dispatch completeness
# ---------------------------------------------------------------------------


def test_every_entry_trigger_kind_has_dispatch_handler():
    """Per AGENTS.md §7.20: every member of
    ``entries.model.TriggerKind`` MUST have a handler entry in
    ``entries.dispatch._ENTRY_DISPATCH``. Both the live entry
    evaluator AND the strategy_tester mechanical evaluator delegate
    to this dict — a missing handler silently no-fires the trigger
    in BOTH live and mechanical paths.
    """
    from tradinglab.entries.dispatch import _ENTRY_DISPATCH
    from tradinglab.entries.model import TriggerKind

    missing = [
        tk for tk in TriggerKind if tk not in _ENTRY_DISPATCH
    ]
    if missing:
        pytest.fail(
            "TriggerKind members without an _ENTRY_DISPATCH handler "
            "(silent no-fire on both live + mechanical paths):"
            "\n  - " + "\n  - ".join(repr(m) for m in missing)
            + "\n\nRegister handlers in src/tradinglab/entries/dispatch.py."
        )


def test_every_exit_trigger_kind_has_dispatch_handler():
    """Per AGENTS.md §7.20: every member of
    ``exits.model.TriggerKind`` MUST have a handler entry in
    ``exits.dispatch._EXIT_DISPATCH``.
    """
    from tradinglab.exits.dispatch import _EXIT_DISPATCH
    from tradinglab.exits.model import TriggerKind

    missing = [
        tk for tk in TriggerKind if tk not in _EXIT_DISPATCH
    ]
    if missing:
        pytest.fail(
            "TriggerKind members without an _EXIT_DISPATCH handler "
            "(silent no-fire on both live + mechanical paths):"
            "\n  - " + "\n  - ".join(repr(m) for m in missing)
            + "\n\nRegister handlers in src/tradinglab/exits/dispatch.py."
        )


def test_no_orphan_entry_dispatch_handlers():
    """Every key in ``_ENTRY_DISPATCH`` must be a real TriggerKind
    member. Catches stale handler entries when a TriggerKind is
    renamed / removed.
    """
    from tradinglab.entries.dispatch import _ENTRY_DISPATCH
    from tradinglab.entries.model import TriggerKind

    members = set(TriggerKind)
    orphans = [k for k in _ENTRY_DISPATCH if k not in members]
    assert not orphans, (
        f"Stale entries in _ENTRY_DISPATCH (not in TriggerKind enum): "
        f"{orphans}"
    )


def test_no_orphan_exit_dispatch_handlers():
    """Every key in ``_EXIT_DISPATCH`` must be a real TriggerKind member."""
    from tradinglab.exits.dispatch import _EXIT_DISPATCH
    from tradinglab.exits.model import TriggerKind

    members = set(TriggerKind)
    orphans = [k for k in _EXIT_DISPATCH if k not in members]
    assert not orphans, (
        f"Stale entries in _EXIT_DISPATCH (not in TriggerKind enum): "
        f"{orphans}"
    )


# ---------------------------------------------------------------------------
# 6. ZoneInfo consolidation
# ---------------------------------------------------------------------------


# Files that may legitimately construct ZoneInfo("America/New_York") directly
# instead of importing from core.timezones. Add ONLY with a documented reason
# (per AGENTS.md §7.23 deferred-migration list).
_ZONEINFO_EXEMPTIONS: dict[str, str] = {
    "gui/sandbox_panel.py": (
        "Deferred migration site per AGENTS.md §7.23 — uses bespoke "
        "_get_tz_for_label fallback. TODO: collapse into core.timezones."
    ),
}


def test_no_direct_et_zoneinfo_outside_core_timezones():
    """Per AGENTS.md §7.23: the only place that may construct
    ``ZoneInfo("America/New_York")`` directly is ``core/timezones.py``.
    Every other ET lookup must go through ``core.timezones.ET`` /
    ``get_et()`` / ``now_et()`` / ``to_et()`` so the missing-tzdata
    fallback policy stays uniform.

    Grandfathered sites are in ``_ZONEINFO_EXEMPTIONS``; the
    preferred direction is to remove entries from that dict by
    migrating to ``core.timezones``.
    """
    target_substring = 'ZoneInfo("America/New_York")'
    findings: list[str] = []
    for py in _all_py_files():
        rel = py.relative_to(_SRC).as_posix()
        # Allowed: the canonical source
        if rel == "core/timezones.py":
            continue
        text = py.read_text(encoding="utf-8")
        if target_substring not in text:
            continue
        if rel in _ZONEINFO_EXEMPTIONS:
            continue
        # Capture line numbers for clarity
        for i, line in enumerate(text.splitlines(), 1):
            if target_substring in line:
                findings.append(f"{rel}:{i}")
    if findings:
        pytest.fail(
            "Direct ZoneInfo('America/New_York') construction outside "
            "core/timezones.py:\n  - " + "\n  - ".join(findings)
            + "\n\nReplace with `from .core.timezones import ET` (and "
            "branch on `ET is None` for missing-tzdata) OR add to "
            "_ZONEINFO_EXEMPTIONS with a documented reason."
        )


def test_zoneinfo_exemptions_are_actually_present():
    """Catch stale entries in ``_ZONEINFO_EXEMPTIONS``."""
    target_substring = 'ZoneInfo("America/New_York")'
    present_files: set[str] = set()
    for py in _all_py_files():
        rel = py.relative_to(_SRC).as_posix()
        if rel == "core/timezones.py":
            continue
        if target_substring in py.read_text(encoding="utf-8"):
            present_files.add(rel)
    stale = sorted(
        f for f in _ZONEINFO_EXEMPTIONS if f not in present_files
    )
    assert not stale, (
        "Stale entries in _ZONEINFO_EXEMPTIONS (no longer construct "
        "ZoneInfo directly):\n  - " + "\n  - ".join(stale)
    )


# ---------------------------------------------------------------------------
# AST sanity check (helper)
# ---------------------------------------------------------------------------


def test_all_source_files_parse_as_valid_python():
    """Sanity: every `.py` under `src/tradinglab/` is parseable Python.
    Catches Windows-encoding mishaps, accidental binary saves, etc.
    """
    bad: list[tuple[str, str]] = []
    for py in _all_py_files():
        try:
            ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError) as e:
            bad.append((py.relative_to(_REPO_ROOT).as_posix(), str(e)))
    assert not bad, (
        "Files that failed to parse:\n  - "
        + "\n  - ".join(f"{f}: {e}" for f, e in bad)
    )
