"""Regression test: Markdown files must not contain literal `\\uXXXX` escapes.

Markdown does not interpret Python-style Unicode escape sequences. A
literal ``\\u00d7`` in a `.md` file renders as the 6-character ASCII
sequence ``\\u00d7`` — not the multiplication sign ``×``. The bug was
spotted by the 2026-05 adversarial review (audit
``readme-unicode-escapes``) on the user-facing README and release
template; the fix was to replace each escape with the actual Unicode
character. This test prevents regressions.

Note: Python source files (``.py``) and JSON config files are
deliberately not scanned — ``\\uXXXX`` is a legitimate string-literal
syntax there. Only ``.md`` / rendered-text files are checked.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ESCAPE_PATTERN = re.compile(r"\\u[0-9a-fA-F]{4}")

# Repo root: tests/unit/test_readme_unicode_escapes.py -> repo root is 3 up.
_REPO_ROOT = Path(__file__).resolve().parents[2]

# Subtrees that ship user-visible or developer-facing prose. We
# deliberately exclude `tests/` itself (which contains regex
# patterns matching the escape format) and `.venv/` / `.git/` /
# pyinstaller build artifacts.
_SCAN_ROOTS = (
    _REPO_ROOT / "README.md",
    _REPO_ROOT / ".github",
    _REPO_ROOT / "docs",
    _REPO_ROOT / "src",
)


def _md_files_under(root: Path) -> list[Path]:
    if not root.exists():
        return []
    if root.is_file():
        return [root] if root.suffix.lower() == ".md" else []
    return sorted(root.rglob("*.md"))


def _all_md_files() -> list[Path]:
    out: list[Path] = []
    for r in _SCAN_ROOTS:
        out.extend(_md_files_under(r))
    return out


class TestNoLiteralUnicodeEscapes:
    """No `.md` file may contain literal `\\uXXXX` escapes."""

    def test_readme_clean(self) -> None:
        readme = _REPO_ROOT / "README.md"
        assert readme.is_file(), "README.md must exist at repo root"
        text = readme.read_text(encoding="utf-8")
        matches = _ESCAPE_PATTERN.findall(text)
        assert not matches, (
            f"README.md contains literal Unicode escapes that render "
            f"as ASCII text in any Markdown reader: {matches[:5]} "
            f"(showing first 5). Replace each \\uXXXX with the actual "
            f"character it represents."
        )

    def test_release_template_clean(self) -> None:
        path = _REPO_ROOT / ".github" / "RELEASE_TEMPLATE.md"
        if not path.is_file():
            pytest.skip(f"{path} does not exist on this branch")
        text = path.read_text(encoding="utf-8")
        matches = _ESCAPE_PATTERN.findall(text)
        assert not matches, (
            f"RELEASE_TEMPLATE.md (user-visible GitHub release notes) "
            f"contains literal escapes: {matches[:5]}"
        )

    def test_all_md_files_clean(self) -> None:
        offenders: list[tuple[Path, list[str]]] = []
        for path in _all_md_files():
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            matches = _ESCAPE_PATTERN.findall(text)
            if matches:
                offenders.append((path, matches))
        if offenders:
            lines = [
                f"  {p.relative_to(_REPO_ROOT)}: {ms[:3]}"
                for p, ms in offenders
            ]
            pytest.fail(
                "Markdown files contain literal Unicode escapes "
                "(\\uXXXX renders as ASCII text in any Markdown "
                "reader):\n" + "\n".join(lines)
            )
