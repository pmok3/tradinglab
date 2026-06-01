"""Structural tests for the release workflow's cross-arch matrix.

Pins the contract that the Windows .exe build runs in PARALLEL on
both x64 (``windows-latest``) and ARM64 (``windows-11-arm``) cloud
runners, then a single ``publish`` job collects both artifacts and
uploads them to a GitHub Release.

Background
----------
Before the parallelisation sprint, ``release.yml`` had ONE
``build-windows`` job pinned to ``windows-latest`` (x64 only). The
maintainer cross-built the ARM64 zip locally on a Windows-on-ARM
dev box, then ``gh release upload``-d both zips manually. The
``windows-11-arm`` runner labels went GA for public repos in
August 2025; we use them here to run both architectures in parallel
on cloud runners (no local Prism emulation tax, no manual zip
shuffling, no ``.venv-build`` wipe-and-rebuild dance).

These tests are AST-level so they catch accidental regressions
(somebody removing the matrix; somebody pinning the publish job to
a single arch; somebody forgetting the `needs:` dependency) without
having to actually run the workflow.
"""
from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_RELEASE_YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"


@pytest.fixture(scope="module")
def release_yaml() -> dict:
    return yaml.safe_load(_RELEASE_YML.read_text(encoding="utf-8"))


class TestParallelMatrixBuild:
    """The Windows build job must use a matrix covering x64 + ARM64."""

    def test_build_job_exists(self, release_yaml):
        jobs = release_yaml.get("jobs", {})
        assert "build-windows" in jobs, (
            f"release.yml must define a build-windows job; got "
            f"{sorted(jobs.keys())}"
        )

    def test_build_job_is_a_matrix(self, release_yaml):
        build = release_yaml["jobs"]["build-windows"]
        strategy = build.get("strategy", {})
        matrix = strategy.get("matrix", {})
        assert matrix, (
            "build-windows must declare strategy.matrix so x64 + ARM64 "
            "run in parallel; got no matrix"
        )

    def test_matrix_covers_both_windows_archs(self, release_yaml):
        build = release_yaml["jobs"]["build-windows"]
        matrix = build["strategy"]["matrix"]
        # Accept either `include: [{runner: ...}, ...]` or
        # `runner: [windows-latest, windows-11-arm]` shape.
        runners: set[str] = set()
        if "include" in matrix:
            for entry in matrix["include"]:
                if "runner" in entry:
                    runners.add(str(entry["runner"]))
                elif "os" in entry:
                    runners.add(str(entry["os"]))
        elif "runner" in matrix:
            runners |= {str(v) for v in matrix["runner"]}
        elif "os" in matrix:
            runners |= {str(v) for v in matrix["os"]}
        assert "windows-latest" in runners, (
            f"matrix must include windows-latest (x64); got runners={runners}"
        )
        assert "windows-11-arm" in runners, (
            f"matrix must include windows-11-arm (ARM64); got runners={runners}"
        )

    def test_matrix_fail_fast_is_false(self, release_yaml):
        """One arch failing shouldn't cancel the other arch's build.

        ``fail-fast: false`` is mandatory here — a transient ARM64
        runner issue shouldn't cost us the x64 zip and force a
        full re-run.
        """
        build = release_yaml["jobs"]["build-windows"]
        strategy = build["strategy"]
        assert strategy.get("fail-fast") is False, (
            "strategy.fail-fast must be False so x64 + ARM64 don't "
            "cancel each other on a transient failure"
        )

    def test_runs_on_consumes_matrix_runner(self, release_yaml):
        """The job's ``runs-on:`` must reference the matrix entry."""
        build = release_yaml["jobs"]["build-windows"]
        runs_on = str(build["runs-on"])
        assert "matrix" in runs_on, (
            f"runs-on must reference matrix (e.g. ${{{{ matrix.runner }}}}); "
            f"got {runs_on!r}"
        )


class TestPublishJobCollectsBothArtifacts:
    """A single publish job must depend on both arch builds."""

    def test_publish_job_exists(self, release_yaml):
        jobs = release_yaml.get("jobs", {})
        assert "publish" in jobs, (
            f"release.yml must define a publish job; got {sorted(jobs.keys())}"
        )

    def test_publish_needs_build_windows(self, release_yaml):
        publish = release_yaml["jobs"]["publish"]
        needs = publish.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        assert "build-windows" in needs, (
            f"publish job must `needs: build-windows` so it sees BOTH "
            f"matrix-built artifacts; got needs={needs}"
        )

    def test_publish_publishes_only_on_tag_or_dispatch(self, release_yaml):
        publish = release_yaml["jobs"]["publish"]
        if_expr = str(publish.get("if", ""))
        # Must guard so workflow_dispatch runs DON'T accidentally
        # publish unless the user opts in.
        assert "refs/tags/v" in if_expr or "tags" in if_expr, (
            f"publish job must gate on a tag or explicit opt-in; "
            f"got if={if_expr!r}"
        )


class TestArtifactUploadDownload:
    """Each matrix build uploads with a name; publish downloads them all."""

    def test_each_matrix_build_uploads_artifact(self, release_yaml):
        build = release_yaml["jobs"]["build-windows"]
        steps = build.get("steps", [])
        upload_step = next(
            (s for s in steps if "upload-artifact" in str(s.get("uses", ""))),
            None,
        )
        assert upload_step is not None, (
            "build-windows must include an actions/upload-artifact step"
        )
        # The artifact name must be matrix-arch-distinct so the two
        # parallel uploads don't collide.
        name = str(upload_step.get("with", {}).get("name", ""))
        assert "matrix" in name, (
            f"upload-artifact name must be matrix-distinct (e.g. "
            f"include ${{{{ matrix.arch }}}}) so x64 + ARM64 don't "
            f"overwrite each other; got name={name!r}"
        )

    def test_publish_downloads_all_arch_artifacts(self, release_yaml):
        publish = release_yaml["jobs"]["publish"]
        steps = publish.get("steps", [])
        download_step = next(
            (s for s in steps if "download-artifact" in str(s.get("uses", ""))),
            None,
        )
        assert download_step is not None, (
            "publish must include an actions/download-artifact step"
        )
