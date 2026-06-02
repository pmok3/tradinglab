"""Source-hygiene meta-tests.

Pins contracts about what may / may not appear in `src/tradinglab/**/*.py`:

1. **No `print()` / `breakpoint()` / `pdb.set_trace()`** outside an
   explicit allowlist. Prevents debug noise / interactive breakpoints
   from shipping. Allowlist covers the legitimate exceptions: the CLI
   `--version`/`--help` handler, the interactive Schwab OAuth helper,
   and the early-bootstrap status / single-instance diagnostics.

2. **No hardcoded user paths** (`C:\\Users\\...`, `/Users/...`,
   `/tmp/...`, `C:\\tmp\\...`). All user-data paths must come from
   `core.paths` so packaged builds work on every Windows / macOS /
   Linux machine. Comments are ignored.

3. **No raw `open(path, "w"|"a"|"wb"|"ab")` for user state**. Use
   `core.atomic_write.atomic_write_*` so a killed process cannot
   corrupt user state files. Bootstrap files (single-instance lock)
   and user-chosen export targets are grandfathered.

Audit ``source-hygiene``.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src" / "tradinglab"


def _all_py_files() -> list[Path]:
    return [
        p
        for p in sorted(_SRC.rglob("*.py"))
        if "__pycache__" not in p.parts and p.name != "__init__.py"
    ]


# ---------------------------------------------------------------------------
# 1. No debug-statement leaks
# ---------------------------------------------------------------------------


# Files that may legitimately call print()/breakpoint() — typically
# CLI entry points or interactive OAuth helpers. Whole-file exemption.
_DEBUG_STATEMENT_FILE_EXEMPTIONS: dict[str, str] = {
    "app.py": (
        "Contains the `tradinglab --version` / `--help` CLI handler "
        "which legitimately writes to stdout. Audit if NEW print() "
        "calls appear outside the CLI block."
    ),
    "data/schwab_login.py": (
        "Interactive Schwab OAuth helper — prompts the user through "
        "the auth-code flow on the console. Legitimately uses print()."
    ),
    "status.py": (
        "Single-instance status helper — diagnostic stdout for the "
        "command-line `--check` flag."
    ),
    "_single_instance.py": (
        "Bootstrap diagnostics — prints before the logger is wired."
    ),
    # Pre-existing files with stray print() error-log calls.
    # Migration TODO: replace with status_log / stdlib logging so the
    # message can be routed to the log file + status bar consistently.
    "data/yfinance_source.py": (
        "TODO: replace `print(f'Live fetch failed: ...')` at line ~43 "
        "with status_log / logging — currently dumps to stdout where "
        "no user sees it in the frozen .exe."
    ),
    "gui/geometry_store.py": (
        "TODO: replace `print(file=sys.stderr)` at line ~225 with "
        "logging.warning — geometry save errors should appear in the "
        "log file, not stderr."
    ),
    "watchlists/storage.py": (
        "TODO: replace `print(f'Watchlist save failed: ...')` at line "
        "~122 with logging.error — silent print on save failure."
    ),
}


_FORBIDDEN_DEBUG_CALLEES = {
    "print": "print() call (use status_log or logging)",
    "breakpoint": "breakpoint() — interactive debugger left in source",
    "set_trace": "pdb.set_trace() — interactive debugger left in source",
}


def test_no_debug_statements_in_source():
    """No `print()` / `breakpoint()` / `pdb.set_trace()` calls outside
    the explicit per-file allowlist. Catches debug noise being shipped.

    Audit ``source-hygiene``.
    """
    findings: list[str] = []
    for py in _all_py_files():
        rel = py.relative_to(_SRC).as_posix()
        if rel in _DEBUG_STATEMENT_FILE_EXEMPTIONS:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee_name: str | None = None
            if isinstance(node.func, ast.Name):
                callee_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                callee_name = node.func.attr
            if callee_name in _FORBIDDEN_DEBUG_CALLEES:
                findings.append(
                    f"  - {rel}:{node.lineno}  "
                    f"{_FORBIDDEN_DEBUG_CALLEES[callee_name]}"
                )
    if findings:
        pytest.fail(
            "Debug statements in source:\n\n" + "\n".join(findings)
            + "\n\nRemove the call OR (if it's a legitimate CLI/bootstrap "
            "case) add the file to _DEBUG_STATEMENT_FILE_EXEMPTIONS with "
            "a reason."
        )


# ---------------------------------------------------------------------------
# 2. No hardcoded user paths
# ---------------------------------------------------------------------------


# Match common user-path patterns. Each occurrence in a non-comment line
# is flagged. Resolution: pull from core.paths or pass a Path parameter.
_HARDCODED_PATH_RE = re.compile(
    r"(?<![\w/\\])(?:"
    r"[Cc]:[\\]+[Uu]sers[\\]+"
    r"|/[Uu]sers/"
    r"|/tmp/"
    r"|[Cc]:[\\]+tmp[\\]+"
    r"|/var/tmp/"
    r")",
)


def test_no_hardcoded_user_paths_in_source():
    """No hardcoded `C:\\Users\\...`, `/Users/...`, `/tmp/...`, etc.
    Resolves through `core.paths` instead so packaged builds work on
    every machine.

    Audit ``source-hygiene``.
    """
    findings: list[str] = []
    for py in _all_py_files():
        rel = py.relative_to(_SRC).as_posix()
        if rel in _HARDCODED_PATH_EXEMPTIONS:
            continue
        text = py.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if _HARDCODED_PATH_RE.search(line):
                findings.append(f"  - {rel}:{i}  {stripped[:100]}")
    if findings:
        pytest.fail(
            "Hardcoded user paths in source:\n\n" + "\n".join(findings)
            + "\n\nResolve through `core.paths` or pass a Path parameter."
        )


# ---------------------------------------------------------------------------
# 3. Atomic state-file writes
# ---------------------------------------------------------------------------


# Files allowed to use raw open(...,"w") / .write_text / .write_bytes.
# Reasons: bootstrap-time files written before the atomic_write helpers
# exist; user-chosen one-shot exports where partial-write recovery is
# moot (the user picks the destination + retries on failure); files
# that genuinely need append-mode for logging.
_RAW_OPEN_FILE_EXEMPTIONS: dict[str, str] = {
    "_single_instance.py": (
        "Bootstrap lock file — written before atomic-write infra "
        "exists, and must hold the file handle open beyond the "
        "function for fcntl.flock to keep its lock."
    ),
    "gui/scanner_tab.py": (
        "User-chosen Save As destination via filedialog — the user "
        "picks the path AND retries on failure via messagebox. "
        "TODO: migrate to core.io_helpers.atomic_write_json for symmetry."
    ),
}


_HARDCODED_PATH_EXEMPTIONS: dict[str, str] = {
    "recent_files.py": (
        "Module docstring contains an example JSON payload showing "
        "absolute Windows paths in the persisted format — illustrates "
        "the on-disk shape, not a real code path."
    ),
}


def test_no_raw_writeable_open_for_state_files():
    """No raw ``open(path, "w"|"a"|"wb"|"ab")`` in source. Use
    ``core.atomic_write.atomic_write_*`` so a killed process cannot
    corrupt user state files.

    Audit ``source-hygiene``.
    """
    findings: list[str] = []
    for py in _all_py_files():
        rel = py.relative_to(_SRC).as_posix()
        if rel in _RAW_OPEN_FILE_EXEMPTIONS:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            callee_name: str | None = None
            if isinstance(callee, ast.Name):
                callee_name = callee.id
            elif isinstance(callee, ast.Attribute):
                callee_name = callee.attr
            if callee_name != "open":
                continue
            mode_str: str | None = None
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                if isinstance(node.args[1].value, str):
                    mode_str = node.args[1].value
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    if isinstance(kw.value.value, str):
                        mode_str = kw.value.value
            if not isinstance(mode_str, str):
                continue
            if "w" in mode_str or "a" in mode_str or "+" in mode_str:
                findings.append(
                    f"  - {rel}:{node.lineno}  open(..., {mode_str!r})"
                )
    if findings:
        pytest.fail(
            "Raw open(write/append) calls in source:\n\n"
            + "\n".join(findings)
            + "\n\nUse `core.atomic_write.atomic_write_json` / "
            "`atomic_write_text` / `atomic_write_bytes` OR (if "
            "legitimately needed) add the file to "
            "_RAW_OPEN_FILE_EXEMPTIONS with a reason."
        )


def test_debug_statement_file_exemptions_are_actually_present():
    """Catch stale entries in :data:`_DEBUG_STATEMENT_FILE_EXEMPTIONS`."""
    present: set[str] = set()
    for py in _all_py_files():
        rel = py.relative_to(_SRC).as_posix()
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee_name: str | None = None
            if isinstance(node.func, ast.Name):
                callee_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                callee_name = node.func.attr
            if callee_name in _FORBIDDEN_DEBUG_CALLEES:
                present.add(rel)
                break
    stale = sorted(
        f for f in _DEBUG_STATEMENT_FILE_EXEMPTIONS if f not in present
    )
    assert not stale, (
        "Stale entries in _DEBUG_STATEMENT_FILE_EXEMPTIONS (file no "
        "longer contains a flagged callee):\n  - " + "\n  - ".join(stale)
    )


def test_raw_open_file_exemptions_are_actually_present():
    """Catch stale entries in :data:`_RAW_OPEN_FILE_EXEMPTIONS`."""
    present: set[str] = set()
    for py in _all_py_files():
        rel = py.relative_to(_SRC).as_posix()
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            callee_name: str | None = None
            if isinstance(callee, ast.Name):
                callee_name = callee.id
            elif isinstance(callee, ast.Attribute):
                callee_name = callee.attr
            if callee_name != "open":
                continue
            mode_str: str | None = None
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                if isinstance(node.args[1].value, str):
                    mode_str = node.args[1].value
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    if isinstance(kw.value.value, str):
                        mode_str = kw.value.value
            if isinstance(mode_str, str) and (
                "w" in mode_str or "a" in mode_str or "+" in mode_str
            ):
                present.add(rel)
                break
    stale = sorted(
        f for f in _RAW_OPEN_FILE_EXEMPTIONS if f not in present
    )
    assert not stale, (
        "Stale entries in _RAW_OPEN_FILE_EXEMPTIONS:\n  - "
        + "\n  - ".join(stale)
    )
