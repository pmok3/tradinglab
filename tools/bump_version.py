#!/usr/bin/env python3
"""Bump the version in :mod:`tradinglab._version`.

Single source of truth for the version number is
``src/tradinglab/_version.py``. ``pyproject.toml`` reads it
dynamically via ``[tool.setuptools.dynamic]``, so this script
intentionally rewrites only one file.

Usage::

    python tools/bump_version.py 0.2.0   # explicit version
    python tools/bump_version.py patch   # 0.1.0 -> 0.1.1
    python tools/bump_version.py minor   # 0.1.0 -> 0.2.0
    python tools/bump_version.py major   # 0.1.0 -> 1.0.0
    python tools/bump_version.py --show  # just print the current

The optional ``--no-changelog`` flag suppresses the ``CHANGELOG.md``
stub-section insertion (default: a stub is added at the top
referencing today's date, matching the Keep-a-Changelog format
already in use).

After bumping, the suggested workflow is::

    git add src/tradinglab/_version.py CHANGELOG.md
    git commit -m "Release v$NEW"
    git tag v$NEW
    git push origin main --tags

The tag push triggers ``.github/workflows/release.yml`` which
builds the Windows redistributable.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = REPO_ROOT / "src" / "tradinglab" / "_version.py"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

# Strict semver MAJOR.MINOR.PATCH (PEP 440 compatible). Pre-release
# suffixes (``-rc1`` etc.) are deliberately not handled here — add
# them by hand if needed.
_VERSION_LITERAL = re.compile(
    r'^(?P<prefix>__version__\s*=\s*")(?P<ver>\d+\.\d+\.\d+)(?P<suffix>")',
    re.MULTILINE,
)
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _read_current() -> str:
    text = VERSION_FILE.read_text(encoding="utf-8")
    m = _VERSION_LITERAL.search(text)
    if not m:
        raise SystemExit(
            f"Could not find a literal __version__ = \"X.Y.Z\" in "
            f"{VERSION_FILE}. Refusing to guess."
        )
    return m.group("ver")


def _parse(s: str) -> tuple[int, int, int]:
    if not _SEMVER.match(s):
        raise SystemExit(
            f"Invalid version string: {s!r} (expected MAJOR.MINOR.PATCH)"
        )
    a, b, c = (int(p) for p in s.split("."))
    return a, b, c


def _bump(current: str, kind: str) -> str:
    if kind in ("patch", "minor", "major"):
        major, minor, patch = _parse(current)
        if kind == "patch":
            return f"{major}.{minor}.{patch + 1}"
        if kind == "minor":
            return f"{major}.{minor + 1}.0"
        return f"{major + 1}.0.0"
    # Explicit version literal — must round-trip through the parser.
    _parse(kind)
    return kind


def _write_version(new: str) -> None:
    text = VERSION_FILE.read_text(encoding="utf-8")
    new_text, count = _VERSION_LITERAL.subn(
        rf'\g<prefix>{new}\g<suffix>', text, count=1
    )
    if count != 1:
        raise SystemExit(
            f"Failed to rewrite __version__ in {VERSION_FILE} "
            f"(expected 1 substitution, got {count})"
        )
    VERSION_FILE.write_text(new_text, encoding="utf-8")


def _prepend_changelog(new: str) -> bool:
    """Insert a stub section above ``[Unreleased]`` if present.

    Returns True if the changelog was modified, False otherwise
    (file missing, no anchor, etc.). Keeps the bump idempotent
    if rerun on the same version (we check the new heading isn't
    already present).
    """
    if not CHANGELOG.exists():
        return False
    text = CHANGELOG.read_text(encoding="utf-8")
    today = date.today().isoformat()
    new_heading = f"## [{new}] - {today}"
    if new_heading in text:
        return False  # Already added — idempotent
    stub = (
        f"{new_heading}\n\n"
        "### Added\n"
        "- (describe new behaviour)\n\n"
        "### Changed\n"
        "- (describe behaviour changes)\n\n"
        "### Fixed\n"
        "- (describe bug fixes)\n\n"
    )
    if "## [Unreleased]" in text:
        text = text.replace("## [Unreleased]", stub + "## [Unreleased]", 1)
    else:
        # Drop after the first H1 if no Unreleased header exists.
        text = re.sub(r"^# Changelog\s*\n",
                       f"# Changelog\n\n{stub}", text, count=1,
                       flags=re.MULTILINE)
    CHANGELOG.write_text(text, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bump the package version (single source of truth: "
                    "src/tradinglab/_version.py).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "kind", nargs="?",
        help="One of: patch | minor | major | <X.Y.Z>",
    )
    parser.add_argument(
        "--show", action="store_true",
        help="Just print the current version and exit.",
    )
    parser.add_argument(
        "--no-changelog", action="store_true",
        help="Skip the CHANGELOG.md stub insertion.",
    )
    args = parser.parse_args(argv)

    current = _read_current()

    if args.show or not args.kind:
        print(current)
        return 0 if args.show else 2  # missing-arg = usage error

    new = _bump(current, args.kind)
    if new == current:
        print(f"Version unchanged ({current}).", file=sys.stderr)
        return 1

    _write_version(new)
    chlog_modified = (
        _prepend_changelog(new) if not args.no_changelog else False
    )
    print(f"Bumped {current} -> {new} ({VERSION_FILE.relative_to(REPO_ROOT)})")
    if chlog_modified:
        print(f"  + CHANGELOG.md stub inserted (review + edit before commit)")
    print()
    print("Suggested next steps:")
    print(f"  git add {VERSION_FILE.relative_to(REPO_ROOT)} CHANGELOG.md")
    print(f"  git commit -m \"Release v{new}\"")
    print(f"  git tag v{new}")
    print(f"  git push origin main --tags")
    return 0


if __name__ == "__main__":
    sys.exit(main())
