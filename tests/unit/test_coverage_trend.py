"""Small offline fixtures for informational history, provenance and trust boundaries."""

from __future__ import annotations

import copy
import io
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import report_coverage_trend as trend

XML = rb"""<?xml version="1.0" ?>
<coverage version="7.10" timestamp="123456789" lines-covered="3" lines-valid="4"
          branches-covered="2" branches-valid="4" line-rate="0.75" branch-rate="0.5">
  <sources><source>C:\runner\repo\src\tradinglab</source></sources>
  <packages><package name="."><classes>
    <class name="a.py" filename="a.py"><lines>
      <line number="1" hits="1" branch="true" condition-coverage="50% (1/2)" />
      <line number="2" hits="0" />
    </lines></class>
    <class name="b.py" filename="data/b.py"><lines>
      <line number="1" hits="4" branch="true" condition-coverage="50% (1/2)" />
      <line number="2" hits="1" />
    </lines></class>
  </classes></package></packages>
</coverage>"""
COMMIT = "a" * 40
TREE = "b" * 40


def measurement(run_id=10, **updates):
    overall, files = trend.parse_xml(XML)
    definition = {
        "suite": "unit-scanner-logic-oracles-v1",
        "command": ["pytest", "tests/unit", "--cov=tradinglab", "--cov-report=xml"],
        "config_sha256": "c" * 64,
    }
    value = {
        "schema_version": 1,
        "repository": "pmok3/tradinglab",
        "workflow": trend.WORKFLOW,
        "run_id": run_id,
        "run_attempt": 1,
        "commit": COMMIT,
        "source_tree": TREE,
        "ref": trend.MAIN_REF,
        "event": "push",
        "scope": {"id": trend.scope_id(definition), "definition": definition},
        "test_exit_code": 0,
        "xml_sha256": trend.digest(XML),
        "provenance": {
            "producer_root": r"C:\runner\repo",
            "source_root": "src/tradinglab",
            "clean_start": True,
            "clean_end": True,
            "fresh_outputs": True,
            "xml_sources": [r"C:\runner\repo\src\tradinglab"],
        },
        "overall": overall,
        "files": files,
    }
    value.update(updates)
    return value


def zipped(value, *, name=trend.SUMMARY_NAME):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, trend.canonical(value))
    return stream.getvalue()


def run_info(value, **updates):
    run = {
        "id": value["run_id"],
        "run_attempt": value["run_attempt"],
        "head_sha": value["commit"],
        "head_branch": "main",
        "event": value["event"],
        "status": "completed",
        "conclusion": "success",
        "workflow_id": 99,
        "path": trend.WORKFLOW,
        "head_repository": {"full_name": value["repository"]},
    }
    run.update(updates)
    return run


class FakeGitHub:
    def __init__(self, current, *previous):
        self.root = f"repos/{current['repository']}"
        self.calls = []
        self.routes = {
            f"{self.root}/actions/runs/{current['run_id']}": run_info(current, status="in_progress"),
            f"{self.root}/actions/workflows/99/runs?branch=main&status=completed&per_page=100": {
                "workflow_runs": [run_info(value) for value in previous],
            },
        }
        for value in previous:
            number, attempt = value["run_id"], value["run_attempt"]
            self.routes[f"{self.root}/actions/runs/{number}/artifacts?per_page=100"] = {
                "artifacts": [{
                    "name": trend.artifact_name(number, attempt),
                    "id": number * 10,
                    "expired": False,
                    "size_in_bytes": len(zipped(value)),
                }],
            }
            self.routes[f"{self.root}/actions/runs/{number}/attempts/{attempt}/jobs?per_page=100"] = {
                "jobs": [{
                    "name": "coverage",
                    "status": "completed",
                    "conclusion": "success",
                    "steps": [{
                        "name": "Run tests with coverage", "status": "completed", "conclusion": "success",
                    }],
                }],
            }
            self.routes[f"{self.root}/actions/artifacts/{number * 10}/zip"] = zipped(value)

    def get(self, endpoint, *, binary=False):
        self.calls.append((endpoint, binary))
        value = self.routes[endpoint]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)

    @property
    def runs(self):
        return self.routes[
            f"{self.root}/actions/workflows/99/runs?branch=main&status=completed&per_page=100"
        ]["workflow_runs"]

    def artifact(self, run_id):
        return self.routes[f"{self.root}/actions/runs/{run_id}/artifacts?per_page=100"]["artifacts"][0]

    def job(self, run_id):
        return self.routes[f"{self.root}/actions/runs/{run_id}/attempts/1/jobs?per_page=100"]["jobs"][0]


@pytest.fixture
def repository(monkeypatch):
    monkeypatch.setattr(trend, "is_ancestor", lambda base, head: True)
    monkeypatch.setattr(trend, "git", lambda *args: TREE)


def test_xml_counts_are_statements_and_branches_not_combined_percentage():
    overall, files = trend.parse_xml(XML)
    assert overall == {
        "statements": {"covered": 3, "total": 4},
        "branches": {"covered": 2, "total": 4},
    }
    assert files["src/tradinglab/data/b.py"]["statements"] == {"covered": 2, "total": 2}
    text = trend.render_report(measurement(), None, "Bootstrap")
    assert "75.00% (3/4)" in text
    assert "50.00% (2/4)" in text
    assert "62.50%" not in text
    assert "90 days" in text and "100 latest completed" in text


@pytest.mark.parametrize("data", [
    b"not XML",
    XML.replace(b'lines-covered="3"', b'lines-covered="4"'),
    XML.replace(b'branches-valid="4"', b'branches-valid="3"'),
    XML.replace(b'condition-coverage="50% (1/2)"', b'condition-coverage="100%"'),
    XML.replace(b"(1/2)", b"(3/2)"),
    XML.replace(b'hits="0"', b'hits="-1"'),
    XML.replace(b'number="2"', b'number="1"'),
    XML.replace(b'filename="data/b.py"', b'filename="a.py"'),
    XML.replace(b'filename="a.py"', b'filename="../escape.py"'),
    XML.replace(b"<coverage ", b'<!DOCTYPE coverage [<!ENTITY bad "x">]><coverage ', 1),
])
def test_malformed_xml_never_invents_coverage(data):
    with pytest.raises(trend.TrendError):
        trend.parse_xml(data)


@pytest.mark.parametrize("name, expected", [
    ("a.py", "src/tradinglab/a.py"),
    ("tradinglab/data/a.py", "src/tradinglab/data/a.py"),
    (r"src\tradinglab\data\a.py", "src/tradinglab/data/a.py"),
    ("data/space name.py", "src/tradinglab/data/space name.py"),
    ("data/\u00e9.py", "src/tradinglab/data/\u00e9.py"),
    ("data/literal[glob]*.py", "src/tradinglab/data/literal[glob]*.py"),
])
def test_xml_paths_are_canonical(name, expected):
    assert trend.file_name(name) == expected


@pytest.mark.parametrize("name", ["../a.py", "/tmp/a.py", "C:/tmp/a.py", "a.py\n", "a.txt"])
def test_unsafe_paths_are_not_accepted(name):
    with pytest.raises(trend.TrendError):
        trend.file_name(name)


@pytest.mark.parametrize("producer, source, filename", [
    ("/checkout", "/checkout", "src/tradinglab/a.py"),
    ("/checkout", ".", "src/tradinglab/a.py"),
    ("/checkout", "", "src/tradinglab/a.py"),
    ("/checkout", "src", "tradinglab/a.py"),
    ("/checkout", "src/tradinglab", "a.py"),
    ("/checkout", "/checkout/src/tradinglab", "a.py"),
    ("/checkout", "/checkout/src/tradinglab", "/checkout/src/tradinglab/a.py"),
    (r"C:\checkout", r"c:\CHECKOUT\src\tradinglab", "a.py"),
    (r"C:\checkout", r"C:\checkout", r"src\tradinglab\a.py"),
    (r"C:\checkout", r"C:\checkout\src", r"tradinglab\a.py"),
    (r"C:\checkout", r"C:\checkout\src\tradinglab", r"C:\CHECKOUT\src\tradinglab\a.py"),
    ("//server/share/checkout", "//SERVER/SHARE/CHECKOUT/src/tradinglab", "a.py"),
    ("/", "/src/tradinglab", "a.py"),
    ("C:/", "C:/src/tradinglab", "a.py"),
])
def test_shared_mapper_supports_explicit_coverage_layouts(producer, source, filename):
    paths = trend.XmlPaths(producer, [source], {"src/tradinglab/a.py"})
    assert paths.resolve(filename) == "src/tradinglab/a.py"


@pytest.mark.parametrize("producer, source", [
    ("/checkout", "/different-checkout/src/tradinglab"),
    ("C:/checkout", "C:/different-checkout/src/tradinglab"),
    ("C:/checkout", "D:/checkout/src/tradinglab"),
    ("//server/share/checkout", "//server/other/checkout/src/tradinglab"),
    ("/checkout", "/checkout-other/src/tradinglab"),
    ("/checkout", "elsewhere"),
    ("/checkout", "../src/tradinglab"),
])
def test_shared_mapper_rejects_foreign_source_even_with_identical_known_filename(producer, source):
    with pytest.raises(trend.TrendError):
        trend.XmlPaths(producer, [source], {"src/tradinglab/a.py"})


def test_shared_mapper_rejects_absent_roots_and_unmapped_or_ambiguous_paths():
    files = {"src/tradinglab/a.py", "src/tradinglab/data/a.py"}
    with pytest.raises(trend.TrendError, match="source roots"):
        trend.XmlPaths("/checkout", [], files)
    paths = trend.XmlPaths("/checkout", ["src/tradinglab", "src/tradinglab/data"], files)
    with pytest.raises(trend.TrendError, match="ambiguous"):
        paths.resolve("a.py")
    paths = trend.XmlPaths("/checkout", ["src/tradinglab/data"], {"src/tradinglab/a.py"})
    with pytest.raises(trend.TrendError, match="unmapped"):
        paths.resolve("a.py")
    with pytest.raises(trend.TrendError, match="outside producer"):
        paths.resolve("/other/src/tradinglab/a.py")
    with pytest.raises(trend.TrendError, match="case-colliding"):
        trend.XmlPaths("C:/checkout", ["src/tradinglab"], {
            "src/tradinglab/A.py", "src/tradinglab/a.py",
        })


def test_round_trip_json_is_deterministic():
    value = measurement()
    assert trend.load_summary(trend.canonical(value)) == value
    assert trend.canonical(dict(reversed(list(value.items())))) == trend.canonical(value)
    compact = json.dumps(value["scope"]["definition"], sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    assert value["scope"]["id"] == trend.digest(compact.encode("utf-8"))


@pytest.mark.parametrize("field, replacement", [
    ("schema_version", True),
    ("schema_version", 2),
    ("commit", "main"),
    ("source_tree", "bad"),
    ("run_id", True),
    ("run_attempt", 0),
    ("test_exit_code", False),
    ("xml_sha256", "unknown"),
    ("files", {}),
    ("overall", {}),
    ("provenance", {}),
])
def test_summary_rejects_bad_schema(field, replacement):
    with pytest.raises(trend.TrendError):
        trend.load_summary(trend.canonical(measurement(**{field: replacement})))


def test_summary_rejects_bad_counts_and_scope():
    value = measurement()
    value["files"]["src/tradinglab/a.py"]["statements"]["covered"] = True
    with pytest.raises(trend.TrendError, match="counts"):
        trend.validate_summary(value)
    value = measurement()
    value["scope"]["definition"]["suite"] = "gui-only"
    with pytest.raises(trend.TrendError, match="Scope digest mismatch"):
        trend.validate_summary(value)
    value = measurement()
    value["provenance"]["clean_start"] = 1
    with pytest.raises(trend.TrendError, match="provenance"):
        trend.validate_summary(value)


@pytest.mark.parametrize("data", [b"bad", b'{"schema_version":1,"schema_version":1}', b"\xff"])
def test_malformed_or_duplicate_json_is_rejected(data):
    with pytest.raises(trend.TrendError):
        trend.load_summary(data)


def test_branch_regression_is_not_masked_by_statement_improvement():
    current, previous = measurement(), measurement(9)
    for stats in previous["files"].values():
        stats["branches"]["covered"] = 2
        stats["statements"]["covered"] = 1
    previous["overall"] = trend.sum_counts(previous["files"])
    text = trend.render_report(current, previous, "Baseline")
    assert "Overall regression: branches" in text
    assert "+25.00 pp" in text and "-50.00 pp" in text
    assert "100.00% (4/4)" in text and "75.00% (3/4)" in text


def test_denominator_growth_uses_rates_not_only_covered_count():
    previous, current = measurement(9), measurement()
    for stats in current["files"].values():
        stats["statements"]["covered"] += 1
        stats["statements"]["total"] += 2
    current["overall"] = trend.sum_counts(current["files"])
    text = trend.render_report(current, previous, "Baseline")
    assert "Overall regression: statements" in text
    assert "62.50% (5/8)" in text and "-12.50 pp" in text


def test_zero_denominators_are_na_and_removed_files_are_not_zero():
    previous, current = measurement(9), measurement()
    del current["files"]["src/tradinglab/a.py"]
    stats = current["files"]["src/tradinglab/data/b.py"]
    stats["branches"] = {"covered": 0, "total": 0}
    current["overall"] = trend.sum_counts(current["files"])
    text = trend.render_report(current, previous, "Baseline")
    assert "N/A (0/0)" in text
    assert "-> not present" in text
    assert "-100.00 pp" not in text


def test_bounded_file_list_prioritizes_regression():
    previous, current = measurement(9), measurement()
    for i in range(30):
        for value in (current, previous):
            value["files"][f"src/tradinglab/file{i:02}.py"] = trend.empty_counts()
    previous["files"]["src/tradinglab/z_regression.py"] = {
        "statements": {"covered": 1, "total": 1}, "branches": {"covered": 0, "total": 0},
    }
    current["files"]["src/tradinglab/z_regression.py"] = {
        "statements": {"covered": 0, "total": 1}, "branches": {"covered": 0, "total": 0},
    }
    text = trend.render_report(current, previous, "Baseline")
    rows = [line for line in text.splitlines() if line.startswith("| src/tradinglab/")]
    assert len(rows) == 10
    assert rows[0].startswith("| src/tradinglab/z_regression.py")


def test_selects_latest_successful_compatible_ancestor(repository):
    current = measurement()
    api = FakeGitHub(current, measurement(7), measurement(9), measurement(8))
    previous, note = trend.find_baseline(current, api)
    assert previous["run_id"] == 9
    assert "Baseline: run 9" in note
    assert len([path for path, binary in api.calls if binary]) == 1


@pytest.mark.parametrize("updates", [
    {"event": "pull_request"},
    {"event": "pull_request_target"},
    {"head_repository": {"full_name": "fork/tradinglab"}},
    {"head_repository": None},
    {"head_branch": "feature"},
    {"status": "in_progress"},
    {"conclusion": "failure"},
    {"conclusion": "cancelled"},
    {"workflow_id": 12},
    {"path": ".github/workflows/not-ci.yml"},
])
def test_failed_fork_pr_and_other_workflow_runs_never_download(repository, updates):
    api = FakeGitHub(measurement(), measurement(9))
    api.runs[0].update(updates)
    previous, note = trend.find_baseline(measurement(), api)
    assert previous is None
    assert "untrusted" in note
    assert len(api.calls) == 2


def test_non_ancestor_and_current_run_attempts_are_not_baselines(repository, monkeypatch):
    current = measurement(run_attempt=2)
    api = FakeGitHub(current, measurement(10), measurement(9), measurement(8))
    monkeypatch.setattr(trend, "is_ancestor", lambda base, head: False)
    previous, note = trend.find_baseline(current, api)
    assert previous is None and "2 non-ancestor" in note
    assert len(api.calls) == 2


@pytest.mark.parametrize("mode", ["job-failed", "test-failed", "job-missing", "measurement-failed"])
def test_informational_job_cannot_supply_failed_baseline(repository, mode):
    value = measurement(9, test_exit_code=1 if mode == "measurement-failed" else 0)
    api = FakeGitHub(measurement(), value)
    if mode == "job-failed":
        api.job(9)["conclusion"] = "failure"
    elif mode == "test-failed":
        api.job(9)["steps"][0]["conclusion"] = "failure"
    elif mode == "job-missing":
        api.job(9)["name"] = "gui-coverage"
    previous, note = trend.find_baseline(measurement(), api)
    assert previous is None and "unsuccessful" in note
    if mode != "measurement-failed":
        assert not any(binary for _, binary in api.calls)


def test_bootstrap_is_not_reported_as_regression(repository):
    previous, note = trend.find_baseline(measurement(), FakeGitHub(measurement()))
    assert previous is None and "bootstrap" in note
    text = trend.render_report(measurement(), previous, note)
    assert "Comparison unavailable" in text and "no baseline" in text
    assert "No overall regression" not in text


@pytest.mark.parametrize("mode, expected", [
    ("missing", "artifact absent"),
    ("expired", "expired artifact"),
    ("404", "artifact unavailable"),
    ("410", "artifact unavailable"),
    ("incompatible", "incompatible scope/config/runtime"),
])
def test_absent_expired_and_incompatible_are_distinct_from_regression(repository, mode, expected):
    previous = measurement(9)
    if mode == "incompatible":
        previous["scope"]["definition"]["config_sha256"] = "d" * 64
        previous["scope"]["id"] = trend.scope_id(previous["scope"]["definition"])
    api = FakeGitHub(measurement(), previous)
    if mode == "missing":
        api.artifact(9)["name"] = "legacy-coverage-summary"
    elif mode == "expired":
        api.artifact(9)["expired"] = True
    elif mode in ("404", "410"):
        api.routes[f"{api.root}/actions/artifacts/90/zip"] = trend.ApiError("deleted", int(mode))
    baseline, note = trend.find_baseline(measurement(), api)
    assert baseline is None and expected in note
    assert "No regression inference" in note


def test_skipped_newer_candidate_is_visible_when_older_baseline_exists(repository):
    api = FakeGitHub(measurement(), measurement(9), measurement(8))
    api.artifact(9)["expired"] = True
    baseline, note = trend.find_baseline(measurement(), api)
    assert baseline["run_id"] == 8
    assert "Newer candidates skipped: 1 expired artifact" in note


@pytest.mark.parametrize("status", [401, 403, 429, 500, None])
def test_real_download_api_errors_propagate(repository, status):
    api = FakeGitHub(measurement(), measurement(9))
    api.routes[f"{api.root}/actions/artifacts/90/zip"] = trend.ApiError("real failure", status)
    with pytest.raises(trend.ApiError, match="real failure"):
        trend.find_baseline(measurement(), api)


def test_listing_404_is_an_api_error_not_missing_artifact(repository):
    api = FakeGitHub(measurement(), measurement(9))
    api.routes[f"{api.root}/actions/runs/9/artifacts?per_page=100"] = trend.ApiError("not found", 404)
    with pytest.raises(trend.ApiError):
        trend.find_baseline(measurement(), api)


@pytest.mark.parametrize("updates", [
    {"commit": "c" * 40}, {"source_tree": "c" * 40}, {"run_id": 8},
    {"run_attempt": 2}, {"repository": "other/repo"}, {"ref": "refs/heads/feature"},
])
def test_stale_artifact_identity_is_an_error_not_a_baseline(repository, updates):
    api = FakeGitHub(measurement(), measurement(9))
    stale = measurement(9)
    stale.update(updates)
    api.routes[f"{api.root}/actions/artifacts/90/zip"] = zipped(stale)
    with pytest.raises(trend.TrendError, match="Invalid baseline artifact"):
        trend.find_baseline(measurement(), api)


@pytest.mark.parametrize("payload", [
    b"not a zip", zipped(measurement(9), name="../coverage-summary.json"),
    zipped(measurement(9), name="run-me.py"), zipped({"schema_version": 1}),
])
def test_malformed_archives_are_never_extracted_or_executed(repository, payload):
    api = FakeGitHub(measurement(), measurement(9))
    api.routes[f"{api.root}/actions/artifacts/90/zip"] = payload
    with pytest.raises(trend.TrendError):
        trend.find_baseline(measurement(), api)


def test_zip_member_count_and_size_are_bounded():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(trend.SUMMARY_NAME, trend.canonical(measurement()))
        archive.writestr("executable.py", "raise AssertionError('never execute')")
    with pytest.raises(trend.TrendError, match="one bounded"):
        trend.unpack_summary(stream.getvalue())
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(trend.SUMMARY_NAME, b" " * (trend.MAX_BYTES + 1))
    with pytest.raises(trend.TrendError, match="one bounded"):
        trend.unpack_summary(stream.getvalue())


def test_git_unknown_commit_error_cannot_masquerade_as_not_ancestor(monkeypatch):
    monkeypatch.setattr(
        trend.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=128, stderr="unknown commit")
    )
    with pytest.raises(trend.TrendError, match="full checkout required"):
        trend.is_ancestor(COMMIT, TREE)


def test_gh_adapter_is_get_only_and_surfaces_status(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1, stderr=b"gh: API rate limit exceeded (HTTP 403)", stdout=b"")

    monkeypatch.setattr(trend.subprocess, "run", run)
    with pytest.raises(trend.ApiError) as error:
        trend.GitHub().get("repos/owner/repo/actions/runs")
    assert error.value.status == 403
    assert calls[0][:4] == ["gh", "api", "--method", "GET"]


def test_scope_is_checkout_independent_and_does_not_store_environment_secrets(monkeypatch):
    monkeypatch.setattr(trend, "git_blob", lambda name: b"[project]\nname = 'tradinglab'\n")
    monkeypatch.setattr(trend, "version", lambda name: "1.0")
    monkeypatch.setenv("TRADINGLAB_EXAMPLE_SECRET", "not-to-be-persisted")
    monkeypatch.setenv("RUNNER_ARCH", "X64")
    value = trend.scope(["pytest", "tests/unit"])
    assert value["definition"]["config_sha256"] == trend.digest(b"[project]\nname = 'tradinglab'\n")
    assert "not-to-be-persisted" not in json.dumps(value)
    assert value == trend.scope(["pytest", "tests/unit"])
    assert value != trend.scope(["pytest", "tests/gui"])


@pytest.fixture
def measurement_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    current = measurement()
    metadata = {key: current[key] for key in (
        "repository", "workflow", "run_id", "run_attempt", "commit", "source_tree", "ref", "event",
    )}
    monkeypatch.setattr(trend, "identity", lambda: metadata)
    monkeypatch.setattr(trend, "scope", lambda command: current["scope"])
    monkeypatch.setattr(trend, "require_clean", lambda: None)
    monkeypatch.setattr(trend, "tracked_source_files", lambda: set(current["files"]))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    for name in current["files"]:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("print('fixture')\nprint('fixture')\n", encoding="utf-8")
    return tmp_path, current


def local_xml(root):
    tree = ET.fromstring(XML)
    tree.find("./sources/source").text = str(root / "src" / "tradinglab")
    return ET.tostring(tree)


def test_measured_xml_uses_nested_root_binding_for_count_keys(measurement_environment):
    root, _ = measurement_environment
    tree = ET.fromstring(local_xml(root))
    tree.find("./sources/source").text = str(root / "src" / "tradinglab" / "data")
    classes = tree.find("./packages/package/classes")
    classes.remove(classes[0])
    classes[0].set("filename", "b.py")
    tree.set("lines-covered", "2")
    tree.set("lines-valid", "2")
    tree.set("branches-covered", "1")
    tree.set("branches-valid", "2")
    overall, files = trend.parse_measured_xml(ET.tostring(tree))
    assert set(files) == {"src/tradinglab/data/b.py"}
    assert overall == {
        "statements": {"covered": 2, "total": 2}, "branches": {"covered": 1, "total": 2},
    }


@pytest.mark.parametrize("redirect_source", [False, True])
def test_measured_xml_rejects_physical_redirects(measurement_environment, monkeypatch, redirect_source):
    root, _ = measurement_environment
    source = root / "src" / "tradinglab"
    redirected = source if redirect_source else source / "a.py"
    original_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        if path == redirected:
            return root.parent / "different-checkout" / redirected.name
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(trend.TrendError, match="outside|redirected"):
        trend.parse_measured_xml(local_xml(root))


@pytest.mark.parametrize("exit_code", [0, 1, 5])
def test_measure_clears_stale_outputs_and_preserves_test_outcome(measurement_environment, monkeypatch, exit_code):
    root, _ = measurement_environment
    for name in ("coverage.xml", trend.SUMMARY_NAME, ".coverage", ".coverage.parallel"):
        (root / name).write_text("stale", encoding="utf-8")

    def run(command, **kwargs):
        assert command == ["pytest", "tests/unit"]
        assert not any((root / name).exists() for name in (
            "coverage.xml", trend.SUMMARY_NAME, ".coverage", ".coverage.parallel",
        ))
        assert kwargs["env"]["COVERAGE_FILE"] == str(root / ".coverage")
        (root / "coverage.xml").write_bytes(local_xml(root))
        return SimpleNamespace(returncode=exit_code)

    monkeypatch.setattr(trend.subprocess, "run", run)
    assert trend.main(["measure", "--", "pytest", "tests/unit"]) == exit_code
    result = trend.load_summary((root / trend.SUMMARY_NAME).read_bytes())
    assert result["test_exit_code"] == exit_code
    assert result["xml_sha256"] == trend.digest(local_xml(root))
    assert result["provenance"]["producer_root"] == str(root)
    assert result["provenance"]["clean_start"] is True
    assert result["provenance"]["clean_end"] is True


def test_measure_cannot_stamp_foreign_source_with_matching_files_and_counts(
    measurement_environment, monkeypatch, capsys,
):
    root, current = measurement_environment
    foreign_root = root.parent / (root.name + "-different-checkout")
    for name in current["files"]:
        foreign = foreign_root / name
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_bytes((root / name).read_bytes())
        assert foreign.read_bytes() == (root / name).read_bytes()
    foreign_xml = local_xml(foreign_root)
    assert trend.parse_xml(foreign_xml) == (current["overall"], current["files"])

    def run(command, **kwargs):
        (root / "coverage.xml").write_bytes(foreign_xml)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(trend.subprocess, "run", run)
    assert trend.main(["measure", "--", "pytest"]) == 2
    assert not (root / trend.SUMMARY_NAME).exists()
    assert "outside producer checkout" in capsys.readouterr().out


@pytest.mark.parametrize("option", ["--cov-append", "--cov-config=elsewhere.ini"])
def test_measure_rejects_unsafe_overrides(measurement_environment, option):
    with pytest.raises(trend.TrendError, match="forbids"):
        trend.measure(["pytest", option], Path("coverage.xml"), Path(trend.SUMMARY_NAME))


def test_missing_new_xml_does_not_reuse_stale_measurement(measurement_environment, monkeypatch, capsys):
    root, _ = measurement_environment
    (root / "coverage.xml").write_bytes(XML)
    (root / trend.SUMMARY_NAME).write_text("stale", encoding="utf-8")
    monkeypatch.setattr(trend.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    assert trend.main(["measure", "--", "pytest"]) == 2
    assert not (root / trend.SUMMARY_NAME).exists()
    assert "Coverage trend error" in capsys.readouterr().out


@pytest.mark.parametrize("when", ["before", "after", "identity", "config"])
def test_dirty_or_changed_source_config_never_publishes_success(measurement_environment, monkeypatch, when):
    root, current = measurement_environment
    calls = []

    def clean():
        calls.append("clean")
        if when == "before" or (when == "after" and len(calls) > 1):
            raise trend.TrendError("dirty source/config")

    def run(command, **kwargs):
        (root / "coverage.xml").write_bytes(XML)
        if when == "identity":
            monkeypatch.setattr(trend, "identity", lambda: {"commit": TREE})
        if when == "config":
            monkeypatch.setattr(trend, "scope", lambda cmd: {})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(trend, "require_clean", clean)
    monkeypatch.setattr(trend.subprocess, "run", run)
    assert trend.main(["measure", "--", "pytest"]) == 2
    assert not (root / trend.SUMMARY_NAME).exists()


def test_clean_guard_includes_untracked_sources_tests_and_config(monkeypatch):
    calls = []

    def git(*args):
        calls.append(args)
        return "?? src/tradinglab/untracked.py"

    monkeypatch.setattr(trend, "git", git)
    with pytest.raises(trend.TrendError, match="must be clean"):
        trend.require_clean()
    assert "--untracked-files=all" in calls[0]
    assert {"src/tradinglab", "tests", "pyproject.toml", ".coveragerc"} <= set(calls[0])


@pytest.mark.parametrize("wrong_checkout", [False, True])
def test_real_pytest_producer_in_temporary_clean_repository(tmp_path, monkeypatch, capsys, wrong_checkout):
    """Exercise actual pytest-cov XML/raw output and Git provenance, without Actions."""
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "src" / "tradinglab"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("", encoding="utf-8")
    (source / "sample.py").write_text(
        "def choose(flag):\n    if flag:\n        return 1\n    return 0\n", encoding="utf-8"
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_sample.py").write_text(
        "from tradinglab.sample import choose\n\ndef test_choose():\n    assert choose(True) == 1\n",
        encoding="utf-8",
    )
    (tmp_path / ".gitignore").write_text(
        "__pycache__/\n.pytest_cache/\n.coverage*\ncoverage*.xml\ncoverage-summary.json\n", encoding="utf-8"
    )
    config = b'[tool.coverage.run]\nsource = ["tradinglab"]\nbranch = true\n'
    (tmp_path / "pyproject.toml").write_bytes(config)
    trend.git("init")
    trend.git("add", ".")
    trend.git("-c", "user.name=Coverage fixture", "-c", "user.email=fixture@example.invalid",
              "-c", "commit.gpgsign=false", "commit", "-m", "fixture")
    head = trend.git("rev-parse", "HEAD")
    imported_source = tmp_path / "src"
    if wrong_checkout:
        imported_source = tmp_path.parent / (tmp_path.name + "-different-checkout") / "src"
        foreign = imported_source / "tradinglab"
        foreign.mkdir(parents=True)
        for name in ("__init__.py", "sample.py"):
            (foreign / name).write_bytes((source / name).read_bytes())
            assert (foreign / name).read_bytes() == (source / name).read_bytes()
    for name, value in {
        "GITHUB_REPOSITORY": "pmok3/tradinglab",
        "GITHUB_RUN_ID": "10",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SHA": head,
        "GITHUB_REF": trend.MAIN_REF,
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_WORKFLOW_REF": f"pmok3/tradinglab/{trend.WORKFLOW}@{trend.MAIN_REF}",
        "PYTHONPATH": str(imported_source),
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    command = [sys.executable, "-m", "pytest", "tests", "--cov=tradinglab", "--cov-report=xml", "-q"]
    result = trend.main(["measure", "--", *command])
    if wrong_checkout:
        assert result == 2
        assert not (tmp_path / trend.SUMMARY_NAME).exists()
        assert "outside producer checkout" in capsys.readouterr().out
        # Identical files/counts elsewhere still cannot establish this checkout's provenance.
        xml = ET.fromstring((tmp_path / "coverage.xml").read_bytes())
        assert tuple(int(xml.get(key)) for key in (
            "lines-covered", "lines-valid", "branches-covered", "branches-valid",
        )) == (3, 4, 1, 2)
        return
    assert result == 0
    value = trend.load_summary((tmp_path / trend.SUMMARY_NAME).read_bytes())
    assert value["commit"] == head
    assert value["source_tree"] == trend.git("rev-parse", "HEAD:src/tradinglab")
    assert value["scope"]["definition"]["config_sha256"] == trend.digest(config)
    assert value["scope"]["definition"]["xml_path_policy"] == "checkout-tracked-v1"
    assert value["overall"]["statements"] == {"covered": 3, "total": 4}
    assert value["overall"]["branches"] == {"covered": 1, "total": 2}
    assert value["xml_sha256"] == trend.digest((tmp_path / "coverage.xml").read_bytes())
    assert (tmp_path / ".coverage").exists()
    trend.require_clean()


def prepare_report(root, current):
    data = local_xml(root)
    current["xml_sha256"] = trend.digest(data)
    (root / "coverage.xml").write_bytes(data)
    (root / trend.SUMMARY_NAME).write_bytes(trend.canonical(current))


def test_report_regression_is_informational(measurement_environment, monkeypatch, repository):
    root, current = measurement_environment
    previous = measurement(9)
    for stats in previous["files"].values():
        stats["branches"]["covered"] = 2
    previous["overall"] = trend.sum_counts(previous["files"])
    api = FakeGitHub(current, previous)
    monkeypatch.setattr(trend, "GitHub", lambda: api)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(root / "step-summary.md"))
    prepare_report(root, current)
    assert trend.main(["report"]) == 0
    text = (root / "step-summary.md").read_text(encoding="utf-8")
    assert "Overall regression: branches" in text and "-50.00 pp" in text


def test_report_failure_counts_are_partial_and_do_not_query_history(measurement_environment, monkeypatch, capsys):
    root, current = measurement_environment
    current["test_exit_code"] = 1
    prepare_report(root, current)
    monkeypatch.setattr(trend, "GitHub", lambda: pytest.fail("failed measurements must not query history"))
    assert trend.main(["report"]) == 0
    text = capsys.readouterr().out
    assert "Tests failed" in text and "not baseline-eligible" in text


@pytest.mark.parametrize("mode", ["wrong-head", "wrong-xml", "malformed-json", "wrong-counts", "api"])
def test_report_errors_are_visible_without_losing_measurement(measurement_environment, monkeypatch, capsys, mode):
    root, current = measurement_environment
    prepare_report(root, current)
    if mode == "wrong-head":
        monkeypatch.setattr(trend, "identity", lambda: {"commit": TREE})
    elif mode == "wrong-xml":
        (root / "coverage.xml").write_bytes(XML + b"\n")
    elif mode == "malformed-json":
        (root / trend.SUMMARY_NAME).write_bytes(b"not JSON")
    elif mode == "wrong-counts":
        for stats in current["files"].values():
            stats["branches"]["covered"] = 2
        current["overall"] = trend.sum_counts(current["files"])
        prepare_report(root, current)
    else:
        api = FakeGitHub(current)
        api.routes[f"{api.root}/actions/runs/10"] = trend.ApiError("permission denied", 403)
        monkeypatch.setattr(trend, "GitHub", lambda: api)
    assert trend.main(["report"]) == 2
    text = capsys.readouterr().out
    assert "Coverage trend error" in text and "No comparison was made" in text
    assert "75.00%" not in text
    assert (root / trend.SUMMARY_NAME).exists()


def test_existing_coverage_job_preserves_gate_scope_and_read_only_main_history():
    workflow = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml").read_text("utf-8")
    match = re.search(r"^  coverage:\n(.*?)(?=^  [\w-]+:\n|\Z)", workflow, re.MULTILINE | re.DOTALL)
    body = match[1]
    assert "continue-on-error: true" in body
    assert "contents: read" in body and "actions: read" in body
    assert "contents: write" not in body and "actions: write" not in body and "fetch-depth: 0" in body
    command = next(line for line in body.splitlines() if "report_coverage_trend.py measure -- " in line)
    assert command.endswith(
        "pytest tests/unit tests/scanner tests/core tests/data tests/entries tests/exits tests/positions "
        "tests/streaming tests/oracles --cov=tradinglab --cov-report=xml "
        "--cov-report=term-missing:skip-covered -q"
    )
    assert "tests/gui" not in command and "--cov-fail-under=" not in command
    assert "name: coverage-xml\n" in body and "path: coverage.xml\n" in body
    assert "summary_artifact=coverage-summary-$env:GITHUB_RUN_ID-$env:GITHUB_RUN_ATTEMPT" in body
    assert "summary_artifact: ${{ steps.coverage_artifact.outputs.summary_artifact }}" in body
    assert "name: ${{ steps.coverage_artifact.outputs.summary_artifact }}" in body
    assert "if: always() && steps.coverage_artifact.outputs.summary_artifact != ''" in body
    assert "retention-days: 90" in body
    assert body.count("if: always() && github.ref == 'refs/heads/main'") == 1
    assert "GH_TOKEN: ${{ github.token }}" in body
    assert "overwrite: true" in body
