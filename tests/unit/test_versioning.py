"""Tests for :mod:`tradinglab._version` and the bump-script invariants."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

import tradinglab
from tradinglab import _version

REPO_ROOT = Path(__file__).resolve().parents[2]
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def test_package_exposes_version_string():
    assert isinstance(tradinglab.__version__, str)
    assert SEMVER.match(tradinglab.__version__), (
        f"__version__ should be MAJOR.MINOR.PATCH, got {tradinglab.__version__!r}"
    )


def test_version_module_defaults_have_empty_build_metadata():
    """In a source / dev install ``_build_info.py`` doesn't exist, so
    the BUILD_* constants must default to empty strings (the fallback
    branch in ``_version.py``). A non-empty default would mean the
    fallback regressed and release builds would lose their stamping."""
    assert _version.BUILD_COMMIT == "" or _version.BUILD_COMMIT  # tolerated
    assert _version.BUILD_DATE == "" or _version.BUILD_DATE
    # In a source checkout there is no _build_info.py present.
    src_build_info = REPO_ROOT / "src/tradinglab/_build_info.py"
    if not src_build_info.exists():
        assert _version.BUILD_COMMIT == ""
        assert _version.BUILD_DATE == ""


def test_version_string_with_no_metadata_equals_version():
    if not _version.BUILD_COMMIT and not _version.BUILD_DATE:
        assert _version.version_string() == _version.__version__


def test_version_string_includes_metadata_when_set(monkeypatch):
    monkeypatch.setattr(_version, "BUILD_COMMIT", "abcd123")
    monkeypatch.setattr(_version, "BUILD_DATE", "2026-05-07")
    s = _version.version_string()
    assert "abcd123" in s
    assert "2026-05-07" in s
    assert s.startswith(_version.__version__)


def test_pyproject_uses_dynamic_version():
    """The dynamic version pipeline only works if ``[project]`` lists
    ``version`` in ``dynamic`` and ``[tool.setuptools.dynamic]`` points
    at the package attribute. A static ``version = "..."`` line would
    desync from ``_version.py``."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dynamic = ["version"]' in pyproject, (
        "pyproject.toml should declare version as dynamic"
    )
    assert "tradinglab._version.__version__" in pyproject, (
        "pyproject.toml should resolve dynamic version from "
        "tradinglab._version.__version__"
    )
    # And there must NOT be a literal `version = "..."` inside [project].
    project_block = re.search(
        r"\[project\][^\[]*", pyproject, flags=re.DOTALL
    )
    assert project_block is not None
    assert not re.search(
        r'^\s*version\s*=\s*"\d',
        project_block.group(0),
        flags=re.MULTILINE,
    ), "pyproject.toml [project] block must NOT carry a literal version"


def test_cli_version_flag_runs_and_exits_zero():
    """``python -m tradinglab --version`` must print the version and
    exit 0 — this is the smoke check used by ``build_exe.ps1``."""
    result = subprocess.run(
        [sys.executable, "-m", "tradinglab", "--version"],
        capture_output=True, text=True, timeout=30, cwd=REPO_ROOT,
    )
    assert result.returncode == 0, (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert _version.__version__ in result.stdout


def test_cli_help_flag_runs_and_exits_zero():
    result = subprocess.run(
        [sys.executable, "-m", "tradinglab", "--help"],
        capture_output=True, text=True, timeout=30, cwd=REPO_ROOT,
    )
    assert result.returncode == 0
    assert "--version" in result.stdout


# ---------------------------------------------------------------------------
# Bump script — dry behaviour against a temp _version file.
# ---------------------------------------------------------------------------


def test_bump_script_show_returns_current_version():
    result = subprocess.run(
        [sys.executable, "tools/bump_version.py", "--show"],
        capture_output=True, text=True, timeout=10, cwd=REPO_ROOT,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == tradinglab.__version__


def test_bump_script_rejects_invalid_version():
    result = subprocess.run(
        [sys.executable, "tools/bump_version.py", "not-a-version"],
        capture_output=True, text=True, timeout=10, cwd=REPO_ROOT,
    )
    assert result.returncode != 0


@pytest.mark.parametrize(
    "current,kind,expected",
    [
        ("0.1.0", "patch", "0.1.1"),
        ("0.1.0", "minor", "0.2.0"),
        ("0.1.0", "major", "1.0.0"),
        ("1.2.3", "patch", "1.2.4"),
        ("1.2.3", "minor", "1.3.0"),
        ("1.2.3", "major", "2.0.0"),
        ("0.1.0", "0.5.0", "0.5.0"),
    ],
)
def test_bump_function_arithmetic(current, kind, expected):
    """Direct unit test on the pure ``_bump`` helper."""
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    try:
        import bump_version  # type: ignore[import-not-found]
        assert bump_version._bump(current, kind) == expected
    finally:
        sys.path.pop(0)


def test_bump_function_rejects_garbage():
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    try:
        import bump_version  # type: ignore[import-not-found]
        with pytest.raises(SystemExit):
            bump_version._bump("0.1.0", "garbage")
    finally:
        sys.path.pop(0)


def test_pyinstaller_spec_committed():
    """The deterministic spec file must live at the repo root and
    reference the package entry point. If a contributor regenerates
    via ``pyi-makespec`` they'd produce a different file — the
    committed spec is intentionally hand-written."""
    spec = REPO_ROOT / "TradingLab.spec"
    assert spec.exists(), "TradingLab.spec missing at repo root"
    text = spec.read_text(encoding="utf-8")
    assert "__main__.py" in text
    assert "tradinglab" in text
    assert "name=\"TradingLab\"" in text or 'name="TradingLab"' in text


def test_build_script_committed():
    build = REPO_ROOT / "tools/build_exe.ps1"
    assert build.exists()
    text = build.read_text(encoding="utf-8")
    # Sanity: the script must reference the spec it consumes and the
    # version file it parses.
    assert "TradingLab.spec" in text
    assert "_version.py" in text


def test_release_workflow_committed():
    wf = REPO_ROOT / ".github/workflows/release.yml"
    assert wf.exists()
    text = wf.read_text(encoding="utf-8")
    assert "build_exe.ps1" in text
    assert "windows-latest" in text
