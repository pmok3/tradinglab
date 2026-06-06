"""Tests for ``tools/extract_changelog.py`` (release-notes extractor)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))
import extract_changelog  # type: ignore[import-not-found]  # noqa: E402

_SAMPLE = """# Changelog

## [0.3.5] - 2026-06-06

### Changed

- Big change.

### Fixed

- A fix.

## [0.3.4] - 2026-06-05

### Added

- An older feature.

## [0.1.0] - Initial development

- First cut.
"""


def test_extracts_middle_section():
    out = extract_changelog.extract_section("0.3.5", _SAMPLE)
    assert "Big change." in out
    assert "A fix." in out
    # Must NOT bleed into the next version's section.
    assert "An older feature." not in out
    # The version-header line itself is omitted.
    assert "## [0.3.5]" not in out


def test_extracts_last_section_to_eof():
    out = extract_changelog.extract_section("0.1.0", _SAMPLE)
    assert "First cut." in out
    assert "An older feature." not in out


def test_strips_leading_v_prefix():
    assert (extract_changelog.extract_section("v0.3.4", _SAMPLE)
            == extract_changelog.extract_section("0.3.4", _SAMPLE))


def test_missing_version_returns_none():
    assert extract_changelog.extract_section("9.9.9", _SAMPLE) is None


def test_section_is_stripped():
    out = extract_changelog.extract_section("0.3.4", _SAMPLE)
    assert not out.startswith("\n")
    assert not out.endswith("\n")


def test_real_changelog_has_current_release():
    """The shipped CHANGELOG must carry a section for the current
    package version so the release workflow can always populate notes."""
    from tradinglab import _version
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    section = extract_changelog.extract_section(_version.__version__, text)
    assert section, (
        f"CHANGELOG.md is missing a '## [{_version.__version__}]' section — "
        "add one before tagging the release (the Release workflow pipes it "
        "into the GitHub release notes)")


def test_cli_exit_code_and_stdout():
    result = subprocess.run(
        [sys.executable, "tools/extract_changelog.py", "0.3.5",
         str(REPO_ROOT / "CHANGELOG.md")],
        capture_output=True, text=True, encoding="utf-8", timeout=15,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0
    assert result.stdout.strip()


def test_cli_writes_utf8_file(tmp_path):
    """File-output mode must round-trip the curated section as UTF-8,
    including the → / — / … glyphs the real changelog uses (the Windows
    console default can't encode those via stdout)."""
    cl = tmp_path / "CHANGELOG.md"
    cl.write_text(
        "# Changelog\n\n## [1.0.0] - x\n\n- File \u2192 Save \u2014 done\u2026\n",
        encoding="utf-8")
    out = tmp_path / "notes.md"
    result = subprocess.run(
        [sys.executable, "tools/extract_changelog.py", "1.0.0", str(cl),
         str(out)],
        capture_output=True, text=True, encoding="utf-8", timeout=15,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0
    body = out.read_text(encoding="utf-8")
    assert "File \u2192 Save \u2014 done\u2026" in body


def test_cli_missing_version_exits_nonzero(tmp_path):
    cl = tmp_path / "CHANGELOG.md"
    cl.write_text(_SAMPLE, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "tools/extract_changelog.py", "9.9.9", str(cl)],
        capture_output=True, text=True, timeout=15, cwd=REPO_ROOT,
    )
    assert result.returncode == 1


@pytest.fixture(autouse=True)
def _cleanup_syspath():
    yield
    # Keep sys.path tidy across the suite.
    p = str(REPO_ROOT / "tools")
    while p in sys.path:
        sys.path.remove(p)
    sys.path.insert(0, p)
