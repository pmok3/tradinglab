"""Tests for the blocking Git-range-aware spec freshness gate."""

from __future__ import annotations

import subprocess
from pathlib import Path

from tools.check_spec_freshness import check_spec_freshness, spec_header_errors

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _write_pair(repo: Path, *, header: str = "Last updated: 2026-09-07") -> None:
    package = repo / "src" / "tradinglab"
    package.mkdir(parents=True)
    (package / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")
    (package / "sample.spec.md").write_text(
        f"# sample.py - Spec\n\n{header}\n\n## Purpose\nTest fixture.\n",
        encoding="utf-8",
    )


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "--all")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "spec-test@example.invalid")
    _git(repo, "config", "user.name", "Spec Test")
    _write_pair(repo)
    _commit(repo, "baseline")
    return repo


def test_changed_module_and_spec_pass(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "src" / "tradinglab" / "sample.py").write_text(
        "VALUE = 2\n",
        encoding="utf-8",
    )
    spec = repo / "src" / "tradinglab" / "sample.spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace("Test fixture.", "Updated fixture."),
        encoding="utf-8",
    )
    head = _commit(repo, "change module and spec")

    assert check_spec_freshness(repo, base=base, head=head) == []


def test_changed_module_without_spec_fails(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "src" / "tradinglab" / "sample.py").write_text(
        "VALUE = 2\n",
        encoding="utf-8",
    )
    head = _commit(repo, "change module only")

    errors = check_spec_freshness(repo, base=base, head=head)

    assert len(errors) == 1
    assert "changed without colocated" in errors[0]


def test_invalid_last_updated_header_fails(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    spec = repo / "src" / "tradinglab" / "sample.spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(
            "Last updated: 2026-09-07",
            "Last updated: 2026-02-30",
        ),
        encoding="utf-8",
    )
    head = _commit(repo, "break header")

    errors = check_spec_freshness(repo, base=base, head=head)

    assert len(errors) == 1
    assert "not a valid ISO date" in errors[0]


def test_deleted_module_does_not_require_spec_update(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "src" / "tradinglab" / "sample.py").unlink()
    (repo / "src" / "tradinglab" / "sample.spec.md").unlink()
    head = _commit(repo, "remove pair")

    assert check_spec_freshness(repo, base=base, head=head) == []


def test_repository_specs_have_canonical_date_headers() -> None:
    assert spec_header_errors(_REPO_ROOT) == []


def test_ci_and_release_workflows_run_blocking_gate() -> None:
    for workflow in ("ci.yml", "release.yml"):
        text = (_REPO_ROOT / ".github" / "workflows" / workflow).read_text(
            encoding="utf-8"
        )
        assert "python tools/check_spec_freshness.py" in text
