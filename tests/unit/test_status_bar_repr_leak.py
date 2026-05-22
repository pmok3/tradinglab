"""Regression tests for audit ``status-bar-repr-leak``.

User-facing status-bar messages used to format exceptions and
internal source names through Python's ``repr`` (the ``!r``
conversion in f-strings). The reviewer's persona saw messages
like::

    Sandbox failed to start: TimeoutError('connect: timed out')
    No fetcher configured for source 'yfinance'.

…and filed a 1-star because the messages "look like a Python
crash dump, not error text aimed at a human". The audit fix
sweeps the ``_status.error`` / ``_status.warn`` / ``_status.info``
call sites to drop the ``!r`` from exception variables and
vendor source names. Short string identifiers (scan name, scan
id, scanner action kind, indicator kind_id) keep their ``!r``
because the quotes disambiguate the literal from surrounding
text.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _src_files() -> list[Path]:
    """All ``.py`` files under ``src/tradinglab``."""
    root = REPO_ROOT / "src" / "tradinglab"
    return sorted(root.rglob("*.py"))


# Identifier names that are LEGITIMATELY formatted with ``!r``:
# short symbolic strings where the quotes help the user spot the
# literal in the surrounding sentence. Anything OUTSIDE this
# allowlist that gets formatted with ``!r`` in a user-facing
# status call is a regression.
ALLOWED_REPR_NAMES = frozenset({
    "scan.name",
    "scan_id",
    "kind",          # scanner action verb (e.g. "buy", "watch")
    "kind_id",       # indicator kind identifier
})

# Names we explicitly forbid in status messages with ``!r``.
# This catches both exception-variable leaks and vendor source
# name leaks.
FORBIDDEN_REPR_NAMES = frozenset({
    "exc", "e", "err", "error",
    "src",            # data source vendor name (yfinance / alpaca / …)
    "req.src",
})


_STATUS_CALL_RE = re.compile(
    r"_status\.(error|warn|info|success)\(\s*(?:"
    r"f\"([^\"]*)\"|"          # single-line f-string
    r"f'([^']*)'|"             # single-line f-string single-quoted
    r"((?:\s*f?\"[^\"]*\"\s*)+)" # adjacent f-string concatenation
    r")",
    flags=re.DOTALL,
)


def _scan_status_messages() -> list[tuple[Path, int, str]]:
    """Return list of (path, line_no, formatted_string) for every
    user-facing status call. Reads the source line-by-line so we
    can capture multi-line f-string concatenations too."""
    hits: list[tuple[Path, int, str]] = []
    for path in _src_files():
        text = path.read_text(encoding="utf-8")
        # Look for the opening ``self._status.{level}(`` and grab
        # the next line OR the rest of the current logical group.
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            m = re.search(
                r"_status\.(error|warn|info|success)\(", line)
            if m:
                # Gather subsequent f-string literals until the
                # matching closing paren. We bound this at 6 lines
                # to keep the scanner fast and robust against
                # malformed source.
                accum = line[m.end():]
                j = i + 1
                paren_depth = accum.count("(") - accum.count(")") + 1
                while paren_depth > 0 and j < i + 8 and j < len(lines):
                    accum += "\n" + lines[j]
                    paren_depth += (
                        lines[j].count("(") - lines[j].count(")")
                    )
                    j += 1
                hits.append((path, i + 1, accum))
                i = j
            else:
                i += 1
    return hits


def test_no_exception_repr_leak_in_status_calls() -> None:
    """Every ``self._status.X(...)`` user-facing call must NOT
    format an exception with ``!r``. Catch any pattern where a
    forbidden name (exc, e, err, src, req.src) is followed by
    ``!r}``."""
    bad: list[str] = []
    for path, line_no, msg in _scan_status_messages():
        for name in FORBIDDEN_REPR_NAMES:
            # ``\b`` around the name + the literal ``!r}`` suffix.
            pat = re.compile(rf"\{{\s*{re.escape(name)}\s*!r\s*\}}")
            if pat.search(msg):
                rel = path.relative_to(REPO_ROOT).as_posix()
                bad.append(f"{rel}:{line_no} → ...{{{name}!r}}...")
    assert not bad, (
        "status-bar-repr-leak regression: the following user-"
        "facing status calls still use ``!r`` on an exception or "
        "vendor source name. Drop the ``!r`` (use plain ``{exc}``) "
        "so the message reads as human-friendly text instead of a "
        "Python repr.\n\n  " + "\n  ".join(bad)
    )


def test_only_allowed_short_identifiers_keep_repr() -> None:
    """Defense in depth: enumerate every ``{x!r}`` placeholder in
    a user-facing status message and verify the placeholder is in
    the small allowlist of short symbolic identifiers."""
    bad: list[str] = []
    for path, line_no, msg in _scan_status_messages():
        for placeholder in re.finditer(
                r"\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*!r\s*\}", msg):
            name = placeholder.group(1)
            if name in ALLOWED_REPR_NAMES:
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            bad.append(f"{rel}:{line_no} → ...{{{name}!r}}...")
    assert not bad, (
        "status-bar-repr-leak regression: a new ``!r`` placeholder "
        "appeared in a user-facing status call. If the placeholder "
        "is a short symbolic identifier (like ``scan_id``) where "
        "the quotes genuinely help the user spot the literal, add "
        "it to ``ALLOWED_REPR_NAMES`` in this test file. Otherwise "
        "drop the ``!r``.\n\n  " + "\n  ".join(bad)
    )


@pytest.mark.parametrize(
    "name",
    [
        "exc", "e", "src", "req.src",
    ],
)
def test_forbidden_repr_names_are_listed(name: str) -> None:
    """Belt-and-suspenders: the FORBIDDEN_REPR_NAMES set covers
    the named-variable conventions used elsewhere in the codebase
    (``except Exception as exc`` / ``as e`` / ``src = self.source_var.get()``)."""
    assert name in FORBIDDEN_REPR_NAMES


def test_specific_status_messages_use_plain_str() -> None:
    """Pin a handful of the highest-traffic status messages to
    confirm the audit landed at the right call sites."""
    app_text = (REPO_ROOT / "src" / "tradinglab" / "app.py").read_text(
        encoding="utf-8")
    # The Watchlist cycle error path used to read ``{exc!r}``.
    assert "f\"Watchlist cycle error: {exc}\"" in app_text, (
        "status-bar-repr-leak regression: Watchlist cycle error "
        "message lost the friendlier ``{exc}`` form."
    )
    # The Sandbox install render path used to read ``{exc!r}``. Phase 3
    # moved the implementation into ``backtest/sandbox_app.py``.
    sandbox_app_text = (
        REPO_ROOT / "src" / "tradinglab" / "backtest" / "sandbox_app.py"
    ).read_text(encoding="utf-8")
    assert "f\"Sandbox install render failed: {exc}\"" in sandbox_app_text, (
        "status-bar-repr-leak regression: Sandbox install render "
        "failed message no longer uses plain ``{exc}``."
    )
    sandbox_menu_text = (
        REPO_ROOT / "src" / "tradinglab" / "gui" / "sandbox_menu.py"
    ).read_text(encoding="utf-8")
    assert "f\"Sandbox failed to start: {exc}\"" in sandbox_menu_text, (
        "status-bar-repr-leak regression: Sandbox failed-to-start "
        "message no longer uses plain ``{exc}``."
    )
