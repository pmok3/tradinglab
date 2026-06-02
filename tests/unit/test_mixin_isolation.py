"""Mixin-isolation meta-test.

Pins the contract that the 20+ ChartApp mixins under
`src/tradinglab/gui/` (and `src/tradinglab/backtest/sandbox_app_aliases.py`)
do NOT import from each other. Each mixin is a leaf in the import tree:
it imports from `core/`, `models/`, helpers under `gui/`, the indicator
registry, etc. — but never from another mixin's module.

Rationale (AGENTS.md §7.24): the ChartApp god-file extraction is an
ongoing multi-wave refactor. Mixin → mixin imports create circular-
import time bombs as more mixins are extracted, AND they make it
impossible to reason about which mixin owns which behavior. The
canonical pattern is: all mixins talk through `self.<attr>` (the
ChartApp instance fields) — not through direct module imports.

Audit ``mixin-isolation``.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src" / "tradinglab"


# Exempt mixin → mixin import pairs. Add only with a documented reason.
# Each key is a (importer_relpath, imported_relpath) tuple.
_MIXIN_IMPORT_EXEMPTIONS: dict[tuple[str, str], str] = {
    # (none today)
}


def _discover_mixin_modules() -> dict[str, set[str]]:
    """Return a dict mapping `module_dotted_path` → set of class names
    defined in that module that end with `Mixin`. A "mixin module" is
    any module that defines at least one `*Mixin` class.
    """
    out: dict[str, set[str]] = {}
    for py in sorted(_SRC.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        mixin_classes = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name.endswith("Mixin")
        }
        if mixin_classes:
            rel = py.relative_to(_SRC).as_posix()
            module_dotted = rel.replace("/", ".").removesuffix(".py")
            out[module_dotted] = mixin_classes
    return out


def _resolve_relative_import(
    importing_module: str, level: int, module: str | None
) -> str:
    """Resolve a `from .x import y` / `from ..y import z` against the
    importing module's dotted path. Returns the dotted target module
    (NOT including the imported symbol). Falls back to ``module`` when
    level == 0 (absolute import).
    """
    if level == 0:
        return module or ""
    parts = importing_module.split(".")
    if level > len(parts):
        return ""
    base = parts[: len(parts) - level]
    if module:
        base.extend(module.split("."))
    return ".".join(base)


def test_no_mixin_to_mixin_imports():
    """No mixin module imports from any OTHER mixin module. Mixins
    must remain leaves in the import tree — interaction with other
    mixins happens through `self.<attr>` shared state on the ChartApp
    instance, never through direct module-level imports.

    Audit ``mixin-isolation``.
    """
    mixin_modules = _discover_mixin_modules()
    # Build a set of "module_dotted_path" that defines at least one mixin.
    mixin_module_paths = set(mixin_modules.keys())

    violations: list[str] = []
    for src in mixin_modules:
        py = _SRC / src.replace(".", "/")
        py = py.with_suffix(".py")
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        # Reconstruct the importing module's dotted path (prefixed with
        # "tradinglab." so relative-import resolution works against
        # the absolute paths used in mixin_module_paths).
        importer_full = "tradinglab." + src
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            target_full = _resolve_relative_import(
                importer_full, node.level, node.module
            )
            # Strip the leading "tradinglab." to compare with our
            # mixin_module_paths (which uses paths relative to _SRC).
            if target_full.startswith("tradinglab."):
                target_rel = target_full[len("tradinglab."):]
            else:
                target_rel = target_full
            if target_rel == src:
                continue  # self-import (impossible but be safe)
            if target_rel not in mixin_module_paths:
                continue
            # Only flag if the imported NAMES include a Mixin class.
            imported_mixin_names = [
                alias.name
                for alias in node.names
                if alias.name in mixin_modules[target_rel]
            ]
            if not imported_mixin_names:
                continue
            key = (src, target_rel)
            if key in _MIXIN_IMPORT_EXEMPTIONS:
                continue
            violations.append(
                f"  - {src}:{node.lineno}  from "
                f"{node.module or ('.' * node.level)} import "
                f"{', '.join(imported_mixin_names)}"
            )

    if violations:
        pytest.fail(
            "Mixin → mixin imports (creates circular-import risk and "
            "violates AGENTS.md §7.24):\n\n" + "\n".join(violations)
            + "\n\nMixins should talk through `self.<attr>` shared "
            "state on ChartApp instead. OR add the pair to "
            "_MIXIN_IMPORT_EXEMPTIONS with a documented reason."
        )


def test_mixin_import_exemptions_are_actual_pairs():
    """Catch stale entries in :data:`_MIXIN_IMPORT_EXEMPTIONS`."""
    mixin_modules = _discover_mixin_modules()
    mixin_module_paths = set(mixin_modules.keys())
    invalid = sorted(
        f"({a!r}, {b!r})"
        for (a, b) in _MIXIN_IMPORT_EXEMPTIONS
        if a not in mixin_module_paths or b not in mixin_module_paths
    )
    assert not invalid, (
        "Stale entries in _MIXIN_IMPORT_EXEMPTIONS (one or both modules "
        "no longer define a Mixin class):\n  - " + "\n  - ".join(invalid)
    )
