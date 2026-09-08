"""Offline contracts for changed executable-line coverage and its CLI."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest

from tools import check_changed_coverage as checker
from tools import report_coverage_trend as trend

_ROOT = Path(__file__).resolve().parents[2]
_SOURCE = "src/tradinglab/"
_SAMPLE = _SOURCE + "sample.py"
_SUITE = "unit-scanner-logic-oracles-v1"
_BEFORE = "UNCHANGED = 0\nA = 1\nB = 2\n"
_AFTER = "UNCHANGED = 0\nA = 10\nB = 20\n"


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=True, timeout=30,
    ).stdout


def _commit(repo: Path) -> str:
    _git(repo, "add", "--all")
    _git(repo, "commit", "--allow-empty", "-m", "fixture")
    return _git(repo, "rev-parse", "HEAD").decode().strip()


def _history(tmp_path: Path, before: dict[str, str], after: dict[str, str]) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Coverage Test")
    _git(repo, "config", "user.email", "coverage@example.invalid")
    _git(repo, "config", "core.autocrlf", "false")
    (repo / "pyproject.toml").write_text("[tool.coverage.run]\nbranch = true\n", encoding="utf-8")
    for name, text in before.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    base = _commit(repo)
    for name in before.keys() - after.keys():
        (repo / name).unlink()
    for name, text in after.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    return repo, base, _commit(repo)


def _summary(repo: Path, head: str, xml: bytes, stats: dict, roots: list[str], producer_root: str) -> dict:
    definition = {
        "suite": _SUITE, "command": ["pytest", "tests/unit", "--cov=tradinglab"],
        "config_sha256": hashlib.sha256(_git(repo, "show", f"{head}:pyproject.toml")).hexdigest(),
        "python": "3.12", "os": "Windows", "arch": "ARM64",
        "versions": {"coverage": "7.13.5", "pytest": "8.0", "pytest-cov": "5.0"},
        "environment": {}, "coverage_config": "pyproject.toml", "raw_data_file": ".coverage",
        "xml_path_policy": "checkout-tracked-v1",
    }
    return {
        "schema_version": 1, "commit": head,
        "source_tree": _git(repo, "rev-parse", f"{head}:src/tradinglab").decode().strip(),
        "xml_sha256": hashlib.sha256(xml).hexdigest(), "test_exit_code": 0,
        "scope": {"definition": definition, "id": trend.scope_id(definition)},
        "repository": "pmok3/tradinglab", "workflow": trend.WORKFLOW,
        "run_id": 123, "run_attempt": 1, "ref": "refs/heads/main", "event": "push",
        "provenance": {
            "producer_root": producer_root, "source_root": "src/tradinglab",
            "clean_start": True, "clean_end": True, "fresh_outputs": True, "xml_sources": roots,
        },
        "files": stats, "overall": trend.sum_counts(stats),
    }


@dataclass
class Measurement:
    repo: Path
    base: str
    head: str
    xml: Path
    summary: Path

    def analyze(self, **kwargs):
        return checker.check_changed_coverage(
            self.repo, base=self.base, head=self.head, xml_path=self.xml, provenance_path=self.summary,
            **kwargs,
        )

    def argv(self, **options) -> list[str]:
        args = [
            "--repo-root", str(self.repo), "--base", self.base, "--head", self.head,
            "--xml", str(self.xml), "--provenance", str(self.summary),
        ]
        for name, value in options.items():
            args.append("--" + name.replace("_", "-"))
            if value is not True:
                args.append(str(value))
        return args

    def change_summary(self, mutate) -> None:
        data = json.loads(self.summary.read_bytes())
        mutate(data)
        self.summary.write_text(json.dumps(data), encoding="utf-8")

    def change_xml(self, mutate) -> None:
        tree = ET.fromstring(self.xml.read_bytes())
        mutate(tree)
        self.xml.write_bytes(ET.tostring(tree))
        self.change_summary(lambda data: data.update(xml_sha256=hashlib.sha256(self.xml.read_bytes()).hexdigest()))


def _measure(
    repo: Path, base: str, head: str, records: dict[str, dict[int, int]], *,
    filenames: dict[str, str] | None = None, roots: list[str] | None = None, producer_root: str | None = None,
) -> Measurement:
    if roots is None:
        roots = [(repo / "src" / "tradinglab").as_posix()]
    root = ET.Element("coverage", {
        "lines-covered": str(sum(hits > 0 for lines in records.values() for hits in lines.values())),
        "lines-valid": str(sum(len(lines) for lines in records.values())),
        "branches-covered": "0", "branches-valid": "0", "line-rate": "0", "branch-rate": "1",
    })
    sources = ET.SubElement(root, "sources")
    for source in roots:
        ET.SubElement(sources, "source").text = source
    classes = ET.SubElement(ET.SubElement(ET.SubElement(root, "packages"), "package"), "classes")
    stats = {}
    for name, lines in records.items():
        filename = filenames[name] if filenames and name in filenames else name.removeprefix(_SOURCE)
        cls = ET.SubElement(classes, "class", {"filename": filename, "line-rate": "1", "branch-rate": "1"})
        container = ET.SubElement(cls, "lines")
        for number, hits in lines.items():
            ET.SubElement(container, "line", {"number": str(number), "hits": str(hits)})
        stats[name] = {
            "statements": {"covered": sum(hits > 0 for hits in lines.values()), "total": len(lines)},
            "branches": {"covered": 0, "total": 0},
        }
    xml = repo / "coverage.xml"
    xml.write_bytes(ET.tostring(root))
    summary = repo / "coverage-summary.json"
    summary.write_text(json.dumps(_summary(
        repo, head, xml.read_bytes(), stats, roots, producer_root or repo.as_posix(),
    )), encoding="utf-8")
    return Measurement(repo, base, head, xml, summary)


@pytest.fixture
def measured(tmp_path: Path) -> Measurement:
    repo, base, head = _history(tmp_path, {_SAMPLE: _BEFORE}, {_SAMPLE: _AFTER})
    return _measure(repo, base, head, {_SAMPLE: {1: 0, 2: 1, 3: 0}})


def test_counts_only_added_executable_lines_not_file_percent(measured):
    result = measured.analyze()
    assert result["covered_lines"] == 1
    assert result["changed_executable_lines"] == 2
    assert result["percentage"] == 50
    assert result["minimum"] is None and result["threshold_met"] is None
    assert result["files"][0]["added_lines"] == [2, 3]
    assert result["files"][0]["uncovered_lines"] == [3]
    assert "branch_percentage" not in result


def test_report_uses_immutable_head_not_dirty_worktree(measured):
    (measured.repo / _SAMPLE).write_text("THIS IS NOW INVALID PYTHON\n", encoding="utf-8")
    assert measured.analyze()["percentage"] == 50


@pytest.mark.parametrize("body", ["", "# A comment\n", "if TYPE_CHECKING:\n    from module import Thing\n"])
def test_new_nonexecutable_module_is_not_fake_100(tmp_path, body):
    empty = _SOURCE + "empty.py"
    repo, base, head = _history(tmp_path, {_SAMPLE: _BEFORE}, {_SAMPLE: _BEFORE, empty: body})
    result = _measure(repo, base, head, {_SAMPLE: {1: 1, 2: 1, 3: 1}, empty: {}}).analyze(
        minimum=Decimal(70), enforce=True,
    )
    assert result["status"] == "not_applicable"
    assert result["percentage"] is None and result["threshold_met"] is None


def test_new_module_counts_all_reported_executable_lines(tmp_path):
    new = _SOURCE + "new.py"
    repo, base, head = _history(tmp_path, {_SAMPLE: _BEFORE}, {_SAMPLE: _BEFORE, new: "# comment\nA = 1\nB = 2\n"})
    result = _measure(repo, base, head, {_SAMPLE: {1: 0, 2: 0, 3: 0}, new: {2: 1, 3: 0}}).analyze()
    assert result["percentage"] == 50 and result["changed_executable_lines"] == 2


@pytest.mark.parametrize("edit", [False, True])
def test_rename_metadata_and_edited_destination(tmp_path, edit):
    new = _SOURCE + "renamed [v1].py"
    old_body = "".join(f"VALUE{i} = {i}\n" for i in range(10))
    new_body = old_body.replace("VALUE4 = 4", "VALUE4 = 44") if edit else old_body
    repo, base, head = _history(tmp_path, {_SAMPLE: old_body}, {new: new_body})
    result = _measure(repo, base, head, {new: dict.fromkeys(range(1, 11), 1)}).analyze()
    item = result["files"][0]
    assert item["path"] == new and item["previous_path"] == _SAMPLE
    assert item["added_lines"] == ([5] if edit else [])
    assert item["status"] == ("measured" if edit else "renamed_only")


@pytest.mark.parametrize("move", ["into", "out", "delete"])
def test_production_scope_boundaries(tmp_path, move):
    outside, inside = "tools/example.py", _SOURCE + "example.py"
    before = {_SAMPLE: _BEFORE, outside if move == "into" else inside: "VALUE = 1\n"}
    after = {_SAMPLE: _BEFORE}
    if move != "delete":
        after[inside if move == "into" else outside] = "VALUE = 1\n"
    repo, base, head = _history(tmp_path, before, after)
    records = {_SAMPLE: {1: 1, 2: 1, 3: 1}}
    if move == "into":
        records[inside] = {1: 0}
    result = _measure(repo, base, head, records).analyze()
    assert result["changed_executable_lines"] == (1 if move == "into" else 0)
    assert result["percentage"] == (0 if move == "into" else None)


def test_empty_diff_is_explicit_na(measured, capsys):
    measured.base = measured.head
    assert checker.main(measured.argv(minimum=70, enforce=True)) == 0
    output = capsys.readouterr().out
    assert "N/A" in output and "100%" not in output and "met (enforced)" not in output


def test_comment_and_excluded_added_lines_are_ignored(tmp_path):
    after = "UNCHANGED = 0\n# new comment\nA = 10\nEXCLUDED = 42\nB = 20\n"
    repo, base, head = _history(tmp_path, {_SAMPLE: _BEFORE}, {_SAMPLE: after})
    result = _measure(repo, base, head, {_SAMPLE: {1: 0, 3: 1, 5: 0}}).analyze()
    assert result["added_line_count"] == 4
    assert result["changed_executable_lines"] == 2
    assert result["files"][0]["executable_lines"] == [3, 5]


def test_multihunk_crlf_and_no_final_newline(tmp_path):
    before = "\r\n".join(f"VALUE{i} = {i}" for i in range(12))
    after = before.replace("VALUE1 = 1", "VALUE1 = 21").replace("VALUE9 = 9", "VALUE9 = 29")
    repo, base, head = _history(tmp_path, {_SAMPLE: before}, {_SAMPLE: after})
    result = _measure(repo, base, head, {_SAMPLE: dict.fromkeys(range(1, 13), 1)}).analyze()
    assert result["files"][0]["added_lines"] == [2, 10]


@pytest.mark.parametrize("filename", ["space name.py", "caf\u00e9.py", "literal [abc].py"])
def test_git_and_xml_support_literal_unicode_and_spaced_paths(tmp_path, filename):
    name = _SOURCE + filename
    repo, base, head = _history(tmp_path, {name: "A = 1\n"}, {name: "A = 2\n"})
    result = _measure(repo, base, head, {name: {1: 1}}).analyze()
    assert result["files"][0]["path"] == name and result["percentage"] == 100


@pytest.mark.parametrize(("producer", "sources", "filename"), [
    (r"D:\agent\repo", [r"D:\agent\repo\src\tradinglab"], r"data\sample.py"),
    (r"D:\agent\repo", [r"D:\agent\repo\src"], r"tradinglab\data\sample.py"),
    (r"D:\agent\repo", [r"D:\agent\repo"], r"src\tradinglab\data\sample.py"),
    (r"D:\agent\repo", ["src/tradinglab"], r"d:\AGENT\REPO\src\tradinglab\data\sample.py"),
    (r"D:\agent\repo", [r"d:\AGENT\REPO\SRC"], r"TRADINGLAB\DATA\SAMPLE.PY"),
    (r"\\server\share\repo", [r"\\server\share\repo\src\tradinglab"], r"data\sample.py"),
    ("/runner/repo", ["/runner/repo/src/tradinglab"], "data/sample.py"),
    ("/runner/repo", ["/runner/repo/src"], "tradinglab/data/sample.py"),
    ("/runner/repo", [""], "src/tradinglab/data/sample.py"),
    ("/runner/repo", ["/runner/repo/src/tradinglab"], "/runner/repo/src/tradinglab/data/sample.py"),
    ("/", ["/src/tradinglab"], "/src/tradinglab/data/sample.py"),
    ("C:/", ["C:/src/tradinglab"], "C:/src/tradinglab/data/sample.py"),
])
def test_portable_xml_paths(producer, sources, filename):
    name = _SOURCE + "data/sample.py"
    paths = checker.XmlPaths(producer, sources, {name: checker.SourceFile("100644", "a" * 40)})
    assert paths.resolve(filename) == name


@pytest.mark.parametrize("filename", [
    "../sample.py", "data/../../sample.py", "/different/repo/sample.py",
    "C:sample.py", "sample.py\x00", "sample.py\n", r"\\other\share\sample.py",
])
def test_unsafe_xml_paths_are_not_guessed(filename):
    paths = checker.XmlPaths("C:/repo", ["src/tradinglab"], {_SAMPLE: checker.SourceFile("100644", "a" * 40)})
    with pytest.raises(trend.TrendError):
        paths.resolve(filename)


def test_xml_path_resolution_never_guesses_by_basename():
    paths = checker.XmlPaths("/repo", ["src/tradinglab"], {
        _SOURCE + "data/sample.py": checker.SourceFile("100644", "a" * 40),
        _SOURCE + "core/sample.py": checker.SourceFile("100644", "b" * 40),
    })
    with pytest.raises(trend.TrendError, match="unmapped"):
        paths.resolve("sample.py")


@pytest.mark.parametrize("source", ["other-package", "../../src/tradinglab", "C:/wrong/src/tradinglab"])
def test_unrelated_xml_source_roots_are_explicit_errors(source):
    with pytest.raises(trend.TrendError):
        checker.XmlPaths("C:/repo", [source], {_SAMPLE: checker.SourceFile("100644", "a" * 40)})


def test_xml_path_resolution_rejects_multiple_valid_interpretations():
    paths = checker.XmlPaths("/repo", [".", "src/tradinglab"], {
        _SAMPLE: checker.SourceFile("100644", "a" * 40),
        _SOURCE + _SAMPLE: checker.SourceFile("100644", "b" * 40),
    })
    with pytest.raises(trend.TrendError, match="ambiguous"):
        paths.resolve(_SAMPLE)


def test_windows_case_collision_is_ambiguous_but_posix_is_not():
    files = {
        _SOURCE + "NAME.py": checker.SourceFile("100644", "a" * 40),
        _SOURCE + "name.py": checker.SourceFile("100644", "b" * 40),
    }
    with pytest.raises(trend.TrendError, match="case-colliding"):
        checker.XmlPaths("C:/repo", ["src/tradinglab"], files)
    assert checker.XmlPaths("/repo", ["src/tradinglab"], files).resolve("NAME.py") == _SOURCE + "NAME.py"


def test_declared_nested_source_never_falls_back_to_package_root():
    paths = checker.XmlPaths("/repo", ["src/tradinglab/data"], {
        _SAMPLE: checker.SourceFile("100644", "a" * 40),
    })
    with pytest.raises(trend.TrendError, match="unmapped"):
        paths.resolve("sample.py")


def test_missing_xml_sources_are_not_inferred_from_filenames():
    with pytest.raises(trend.TrendError):
        checker.XmlPaths("/repo", [], {_SAMPLE: checker.SourceFile("100644", "a" * 40)})


def test_xml_absolute_paths_validate_end_to_end(measured):
    producer = r"\\build-server\share\repo"
    source = producer + r"\src\tradinglab"
    measured.change_summary(lambda data: data["provenance"].update(producer_root=producer, xml_sources=[source]))

    def change(tree):
        tree.find("./sources/source").text = source
        tree.find(".//class").set("filename", source + r"\sample.py")

    measured.change_xml(change)
    assert measured.analyze()["percentage"] == 50


@pytest.mark.parametrize(("producer", "foreign"), [
    ("C:/measured-checkout", "C:/other-checkout"),
    ("//server/share/measured-checkout", "//server/share/other-checkout"),
    ("/measured-checkout", "/other-checkout"),
])
@pytest.mark.parametrize("foreign_class", [False, True])
def test_identical_filename_in_foreign_checkout_cannot_borrow_head_identity(measured, producer, foreign, foreign_class):
    source = (producer if foreign_class else foreign) + "/src/tradinglab"
    measured.change_summary(lambda data: data["provenance"].update(producer_root=producer, xml_sources=[source]))

    def change(tree):
        tree.find("./sources/source").text = source
        filename = foreign + "/src/tradinglab/sample.py" if foreign_class else "sample.py"
        tree.find(".//class").set("filename", filename)

    measured.change_xml(change)
    with pytest.raises(trend.TrendError, match="outside producer checkout"):
        measured.analyze()


@pytest.mark.parametrize(("field", "value", "message"), [
    ("commit", "a" * 40, "measured commit"),
    ("source_tree", "b" * 40, "source tree"),
    ("xml_sha256", "c" * 64, "XML digest"),
    ("test_exit_code", 1, "successful test run"),
    ("test_exit_code", True, "test exit code"),
    ("schema_version", 2, "schema"),
    ("schema_version", True, "schema"),
])
def test_incomplete_or_stale_provenance_is_an_error(measured, field, value, message):
    measured.change_summary(lambda data: data.update({field: value}))
    with pytest.raises((checker.ChangedCoverageError, trend.TrendError), match=message):
        measured.analyze()


@pytest.mark.parametrize("field", ["clean_start", "clean_end", "fresh_outputs"])
@pytest.mark.parametrize("value", [False, None, "true", 1])
def test_provenance_requires_exact_true_capture_flags(measured, field, value):
    measured.change_summary(lambda data: data["provenance"].update({field: value}))
    with pytest.raises(trend.TrendError, match="provenance"):
        measured.analyze()


def test_configuration_hash_checked_against_git_blob(measured):
    def change(data):
        data["scope"]["definition"]["config_sha256"] = "a" * 64
        data["scope"]["id"] = trend.scope_id(data["scope"]["definition"])

    measured.change_summary(change)
    with pytest.raises(checker.ChangedCoverageError, match="configuration"):
        measured.analyze()


def test_old_unconfined_producer_capture_is_not_trusted(measured):
    def change(data):
        del data["scope"]["definition"]["xml_path_policy"]
        data["scope"]["id"] = trend.scope_id(data["scope"]["definition"])

    measured.change_summary(change)
    with pytest.raises(checker.ChangedCoverageError, match="checkout-confined"):
        measured.analyze()


def test_scope_digest_and_expected_suite_are_independent_checks(measured):
    with pytest.raises(checker.ChangedCoverageError, match="Wrong measured test suite"):
        measured.analyze(expected_suite="isolated-gui-v1")
    measured.change_summary(lambda data: data["scope"].update(id="0" * 64))
    with pytest.raises(trend.TrendError, match="Scope digest"):
        measured.analyze()


def test_successful_scope_is_preserved_without_combining(measured):
    result = measured.analyze(expected_suite=_SUITE)
    assert result["scope"]["definition"]["suite"] == _SUITE
    assert result["scope"]["definition"]["command"] == ["pytest", "tests/unit", "--cov=tradinglab"]
    assert result["measurement"]["commit"] == measured.head


@pytest.mark.parametrize("missing", ["xml", "summary"])
def test_missing_measurement_is_error_even_for_empty_diff(measured, missing, capsys):
    measured.base = measured.head
    getattr(measured, missing).unlink()
    assert checker.main(measured.argv()) == 2
    output = capsys.readouterr()
    assert "ERROR" in output.err and "100%" not in output.out


@pytest.mark.parametrize("data", [b"{", b"null", b'{"schema_version":1,"schema_version":1}', b"\xff"])
def test_invalid_json_is_explicit_error(measured, data):
    measured.summary.write_bytes(data)
    assert checker.main(measured.argv()) == 2


def test_xml_altered_after_capture_is_stale_even_if_line_counts_same(measured):
    measured.xml.write_bytes(measured.xml.read_bytes().replace(b'hits="0"', b'hits="1"', 1))
    with pytest.raises(checker.ChangedCoverageError, match="digest"):
        measured.analyze()


@pytest.mark.parametrize("xml", [b"<coverage", b"<wrong/>", b'<!DOCTYPE coverage><coverage/>'])
def test_invalid_xml_after_valid_digest_is_error(measured, xml):
    measured.xml.write_bytes(xml)
    measured.change_summary(lambda data: data.update(xml_sha256=hashlib.sha256(xml).hexdigest()))
    assert checker.main(measured.argv()) == 2


@pytest.mark.parametrize(("attribute", "value"), [
    ("number", "0"), ("number", "-1"), ("number", "4"), ("number", "1.5"), ("number", ""),
    ("hits", "-1"), ("hits", "1.5"), ("hits", "NaN"), ("hits", ""), ("hits", "1" * 100),
])
def test_invalid_xml_line_records(measured, attribute, value):
    measured.change_xml(lambda tree: tree.find(".//line").set(attribute, value))
    with pytest.raises(checker.ChangedCoverageError):
        measured.analyze()


@pytest.mark.parametrize("part", ["line", "class", "lines"])
def test_duplicate_xml_records_are_not_double_counted(measured, part):
    def duplicate(tree):
        element = tree.find(".//" + part)
        parent = tree.find(".//classes" if part == "class" else ".//class" if part == "lines" else ".//lines")
        parent.append(copy.deepcopy(element))

    measured.change_xml(duplicate)
    with pytest.raises(checker.ChangedCoverageError, match="[Dd]uplicate"):
        measured.analyze()


def test_missing_lines_container_is_not_fully_excluded(measured):
    measured.change_xml(lambda tree: tree.find(".//class").remove(tree.find(".//lines")))
    with pytest.raises(checker.ChangedCoverageError, match="lines container"):
        measured.analyze()


def test_missing_changed_module_is_not_assumed_excluded(tmp_path):
    keep = _SOURCE + "keep.py"
    repo, base, head = _history(
        tmp_path, {_SAMPLE: _BEFORE, keep: "A = 1\n"}, {_SAMPLE: _AFTER, keep: "A = 1\n"},
    )
    measurement = _measure(repo, base, head, {keep: {1: 1}})
    with pytest.raises(checker.ChangedCoverageError, match="Missing XML measurement"):
        measurement.analyze()


def test_mismatching_xml_or_summary_counts_are_not_trusted(measured):
    measured.change_xml(lambda tree: tree.set("lines-valid", "999"))
    with pytest.raises(checker.ChangedCoverageError, match="totals"):
        measured.analyze()


def test_xml_source_roots_must_match_capture(measured):
    measured.change_summary(lambda data: data["provenance"].update(xml_sources=[]))
    with pytest.raises(checker.ChangedCoverageError, match="sources disagree"):
        measured.analyze()


@pytest.mark.parametrize("revision", ["", "0" * 40, "not-a-real-revision", "--help"])
def test_invalid_base_never_falls_back_to_head_parent(measured, revision):
    measured.base = revision
    with pytest.raises(checker.ChangedCoverageError):
        measured.analyze()


def test_missing_head_is_input_error(measured):
    measured.head = "missing-measured-head"
    assert checker.main(measured.argv(minimum=70, enforce=True)) == 2


def test_nonproduction_changes_never_enter_denominator(tmp_path):
    before = {_SAMPLE: _BEFORE, "tools/helper.py": "A = 1\n"}
    after = {_SAMPLE: _BEFORE, "tools/helper.py": "A = 2\n", "tests/test_helper.py": "B = 1\n"}
    repo, base, head = _history(tmp_path, before, after)
    report = _measure(repo, base, head, {_SAMPLE: {1: 1, 2: 1, 3: 1}}).analyze()
    assert report["status"] == "not_applicable" and not report["files"]


@pytest.mark.parametrize(("minimum", "enforce", "expected"), [(70, False, 0), (70, True, 1), (50, True, 0)])
def test_explicit_policy_exit_behavior(measured, minimum, enforce, expected):
    options = {"minimum": minimum}
    if enforce:
        options["enforce"] = True
    assert checker.main(measured.argv(**options)) == expected


@pytest.mark.parametrize("minimum", ["-1", "101", "NaN", "Infinity", "-Infinity", "text"])
def test_minimum_validation(measured, minimum):
    with pytest.raises(SystemExit) as exc:
        checker.main(measured.argv(minimum=minimum))
    assert exc.value.code == 2


def test_enforcement_without_minimum_is_usage_error(measured):
    with pytest.raises(SystemExit) as exc:
        checker.main(measured.argv(enforce=True))
    assert exc.value.code == 2


@pytest.mark.parametrize(("count", "covered", "minimum", "expected"), [
    (10, 7, "70", True), (10, 6, "70", False),
    (3, 2, "66.666666666666666", True), (3, 2, "66.666666666666667", False),
    (1, 0, "0", True), (1, 1, "100", True),
])
def test_threshold_comparison_uses_exact_counts(tmp_path, count, covered, minimum, expected):
    before = "".join(f"A{i} = 1\n" for i in range(count))
    after = "".join(f"A{i} = 2\n" for i in range(count))
    repo, base, head = _history(tmp_path, {_SAMPLE: before}, {_SAMPLE: after})
    measurement = _measure(repo, base, head, {_SAMPLE: {i: int(i <= covered) for i in range(1, count + 1)}})
    assert measurement.analyze(minimum=Decimal(minimum), enforce=True)["threshold_met"] is expected


def test_json_report_has_counts_scope_and_missed_locations(measured):
    output = measured.repo / "result.json"
    assert checker.main(measured.argv(json_out=output, minimum=70, enforce=True)) == 1
    result = json.loads(output.read_bytes())
    assert result["status"] == "measured" and result["metric"] == "changed_executable_lines"
    assert result["head_sha"] == measured.head
    assert result["scope"]["definition"]["suite"] == _SUITE
    assert result["files"][0]["uncovered_lines"] == [3]


def test_errors_have_null_score_and_error_json(measured):
    output = measured.repo / "result.json"
    measured.xml.unlink()
    assert checker.main(measured.argv(json_out=output)) == 2
    result = json.loads(output.read_bytes())
    assert result["status"] == "error" and result["percentage"] is None and result["threshold_met"] is None
    assert result["diagnostics"]


def test_output_failure_is_not_a_successful_report(measured, capsys):
    assert checker.main(measured.argv(json_out=measured.repo / "missing" / "result.json")) == 2
    result = capsys.readouterr()
    assert "cannot write JSON output" in result.err and not result.out


def test_output_resolution_error_is_not_repeated_outside_handler(measured, monkeypatch, capsys):
    output = measured.repo / "unresolvable.json"
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        if path == output:
            raise OSError("cannot resolve output path")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert checker.main(measured.argv(json_out=output)) == 2
    assert "cannot resolve output path" in capsys.readouterr().err


@pytest.mark.parametrize("input_name", ["xml", "summary"])
def test_json_cannot_overwrite_measurement_input(measured, input_name):
    path = getattr(measured, input_name)
    before = path.read_bytes()
    assert checker.main(measured.argv(json_out=path)) == 2
    assert path.read_bytes() == before


def test_standalone_command_requires_no_site_packages(measured):
    result = subprocess.run(
        [sys.executable, "-S", str(_ROOT / "tools" / "check_changed_coverage.py"), *measured.argv()],
        capture_output=True, text=True, timeout=30, cwd=measured.repo,
    )
    assert result.returncode == 0, result.stderr
    assert "1/2 (50.0%" in result.stdout
    assert "changed-line branch coverage is not measured" in result.stdout


def test_binary_python_is_explicit_error(tmp_path):
    repo, base, head = _history(tmp_path, {_SAMPLE: "A = 1\n"}, {_SAMPLE: "A = 2\x00\n"})
    with pytest.raises(checker.ChangedCoverageError, match="binary"):
        _measure(repo, base, head, {_SAMPLE: {1: 1}}).analyze()


def test_binary_diff_marker_inside_source_text_is_ordinary_code(tmp_path):
    repo, base, head = _history(
        tmp_path, {_SAMPLE: 'MESSAGE = "old"\n'},
        {_SAMPLE: 'MESSAGE = "Binary files in a documentation string"\n'},
    )
    assert _measure(repo, base, head, {_SAMPLE: {1: 1}}).analyze()["percentage"] == 100


def test_symlink_python_is_explicit_error_without_windows_symlink_privileges(measured):
    oid = _git(measured.repo, "rev-parse", f"{measured.head}:{_SAMPLE}").decode().strip()
    _git(measured.repo, "update-index", "--cacheinfo", f"120000,{oid},{_SAMPLE}")
    _git(measured.repo, "commit", "-m", "symlink fixture")
    head = _git(measured.repo, "rev-parse", "HEAD").decode().strip()
    with pytest.raises(checker.ChangedCoverageError, match="Unsupported Python file mode"):
        _measure(measured.repo, measured.base, head, {_SAMPLE: {1: 1, 2: 1, 3: 1}}).analyze()


def test_real_coverage_xml_preserves_exclusions_and_empty_modules(tmp_path):
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    import not_a_real_module\n"
        "\n"
        "def choose(flag):\n"
        "    if flag:\n"
        "        return (\n"
        "            10\n"
        "            + 20\n"
        "        )\n"
        "    return 0  # pragma: no cover\n"
        "\n"
        "choose(True)\n"
    )
    empty = _SOURCE + "__init__.py"
    repo, base, head = _history(tmp_path, {_SAMPLE: "", empty: ""}, {_SAMPLE: source, empty: ""})
    xml = repo / "coverage.xml"
    script = (
        "import sys; from pathlib import Path; import coverage; "
        "package=Path(sys.argv[1]); source=package/'sample.py'; "
        "cov=coverage.Coverage(source=[str(package)],branch=True,config_file=False); "
        "cov.exclude('if TYPE_CHECKING:'); cov.start(); "
        "exec(compile(source.read_text(encoding='utf-8'),str(source),'exec'),{}); "
        "cov.stop(); cov.xml_report(outfile=sys.argv[2])"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(repo / "src" / "tradinglab"), str(xml)],
        cwd=repo, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    tree = ET.fromstring(xml.read_bytes())
    roots = [node.text or "" for node in tree.findall("./sources/source")]
    _, stats = trend.parse_xml(xml.read_bytes())
    summary = repo / "coverage-summary.json"
    summary.write_text(json.dumps(_summary(repo, head, xml.read_bytes(), stats, roots, repo.as_posix())), encoding="utf-8")
    report = Measurement(repo, base, head, xml, summary).analyze()
    sample = next(item for item in report["files"] if item["path"] == _SAMPLE)
    assert report["changed_executable_lines"] > 0 and report["percentage"] == 100
    assert not set(sample["executable_lines"]).intersection({2, 3, 4, 11, 12})
    assert empty in stats and stats[empty]["statements"]["total"] == 0
