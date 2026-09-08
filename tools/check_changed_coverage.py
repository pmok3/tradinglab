"""Check added executable production lines against one proven coverage run.

Example (explicit enforcement; there is no implicit threshold)::

    python tools/check_changed_coverage.py --base BEFORE --head MEASURED_SHA \
        --xml coverage.xml --provenance coverage-summary.json \
        --expected-suite unit-scanner-logic-oracles-v1 --minimum 70 --enforce

Exit 0 means a valid report (including explicit N/A), 1 an enforced shortfall,
and 2 an input/measurement error. This does not measure changed-line branches.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path

if __package__:
    from .report_coverage_trend import MAX_XML_BYTES, TrendError, XmlPaths, load_summary
else:
    from report_coverage_trend import MAX_XML_BYTES, TrendError, XmlPaths, load_summary

SOURCE_ROOT = "src/tradinglab"
_HUNK = re.compile(rb"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_REGULAR_MODES = {"100644", "100755"}


class ChangedCoverageError(ValueError):
    """The requested comparison cannot be measured reliably."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ChangedCoverageError(message)


def _git(repo: Path, *args: str, input_data: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", "--no-pager", "--literal-pathspecs", "-C", str(repo), *args],
        input=input_data, capture_output=True, timeout=60, check=False,
    )
    _require(
        result.returncode == 0,
        f"Git command failed ({args[0]}): {result.stderr.decode('utf-8', errors='replace').strip()}",
    )
    return result.stdout


def _commit(repo: Path, revision: str) -> str:
    _require(bool(revision) and not re.fullmatch(r"0+", revision), f"Unavailable Git revision: {revision!r}")
    return _git(repo, "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}").decode().strip()


def _production(path: str) -> bool:
    return path.startswith(f"{SOURCE_ROOT}/") and path.endswith(".py")


@dataclass(frozen=True)
class SourceFile:
    mode: str
    oid: str


@dataclass(frozen=True)
class Change:
    path: str
    previous_path: str
    old_oid: str
    new_oid: str
    status: str


def _source_files(repo: Path, head: str) -> dict[str, SourceFile]:
    files = {}
    for entry in _git(repo, "ls-tree", "-r", "-z", "--full-tree", head, "--", SOURCE_ROOT).split(b"\0"):
        if not entry:
            continue
        metadata, raw_name = entry.split(b"\t", 1)
        mode, kind, oid = metadata.decode("ascii").split()
        name = raw_name.decode("utf-8")
        if _production(name):
            _require(kind == "blob" and mode in _REGULAR_MODES, f"Unsupported Python file mode: {name} ({mode})")
            files[name] = SourceFile(mode, oid)
    return files


def _changes(repo: Path, base: str, head: str) -> list[Change]:
    # Paths come from NUL-delimited metadata, never from quoted patch headers.
    records = iter(_git(
        repo, "diff", "--raw", "-z", "--no-abbrev", "--find-renames",
        "--no-ext-diff", "--no-textconv", base, head, "--",
    ).split(b"\0"))
    changes = []
    for metadata in records:
        if not metadata:
            continue
        fields = metadata.decode("ascii").split()
        _require(len(fields) == 5 and fields[0].startswith(":"), "Malformed Git change metadata")
        _, _, old_oid, new_oid, status = fields
        old_path = next(records).decode("utf-8")
        new_path = next(records).decode("utf-8") if status.startswith(("R", "C")) else old_path
        if _production(new_path) or _production(old_path):
            changes.append(Change(new_path, old_path, old_oid, new_oid, status[0]))
    return changes


def _blobs(repo: Path, oids: set[str]) -> dict[str, bytes]:
    if not oids:
        return {}
    stream = io.BytesIO(_git(
        repo, "cat-file", "--batch",
        input_data="".join(f"{oid}\n" for oid in sorted(oids)).encode("ascii"),
    ))
    blobs = {}
    for oid in sorted(oids):
        header = stream.readline().decode("ascii").split()
        _require(len(header) == 3 and header[:2] == [oid, "blob"], f"Cannot read source blob: {oid}")
        data = stream.read(int(header[2]))
        _require(stream.read(1) == b"\n", f"Incomplete source blob: {oid}")
        blobs[oid] = data
    return blobs


def _added_lines(repo: Path, change: Change, blobs: dict[str, bytes]) -> set[int]:
    if change.status == "D" or not _production(change.path):
        return set()
    data = blobs[change.new_oid]
    _require(b"\0" not in data, f"Unsupported binary Python source: {change.path}")
    if change.status in ("A", "C") or not _production(change.previous_path):
        return set(range(1, len(data.splitlines()) + 1))
    if change.old_oid == change.new_oid:
        return set()
    _require(b"\0" not in blobs[change.old_oid], f"Unsupported binary previous source: {change.previous_path}")
    patch = _git(
        repo, "diff", "--patch", "--no-color", "--no-ext-diff", "--no-textconv",
        "--no-renames", "--diff-algorithm=myers", "--no-indent-heuristic",
        "--unified=0", "--inter-hunk-context=0", change.old_oid, change.new_oid, "--",
    )
    added = set()
    for line in patch.splitlines():
        match = _HUNK.match(line)
        if match:
            first = int(match[1])
            count = int(match[2]) if match[2] is not None else 1
            added.update(range(first, first + count))
    _require(
        not any(line.startswith(b"Binary files ") for line in patch.splitlines()),
        f"Unsupported binary diff: {change.path}",
    )
    return added


def _xml_integer(value: str | None, description: str) -> int:
    _require(
        isinstance(value, str) and len(value) <= 20 and bool(re.fullmatch(r"[0-9]+", value)),
        f"Invalid XML {description}: {value!r}",
    )
    return int(value)


def _xml_lines(
    data: bytes, summary: dict, files: dict[str, SourceFile], blobs: dict[str, bytes],
) -> dict[str, dict[int, int]]:
    _require(len(data) <= MAX_XML_BYTES, "Coverage XML exceeds size limit")
    _require(b"<!DOCTYPE" not in data.upper() and b"<!ENTITY" not in data.upper(), "XML DTD/entities forbidden")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ChangedCoverageError(f"Malformed coverage XML: {exc}") from exc
    _require(root.tag == "coverage", "Expected coverage.py XML root")
    sources = [source.text or "" for source in root.findall("./sources/source")]
    _require(sources == summary["provenance"]["xml_sources"], "XML sources disagree with provenance")
    paths = XmlPaths(summary["provenance"]["producer_root"], sources, files)
    measured = {}
    for cls in root.findall("./packages/package/classes/class"):
        filename = cls.get("filename")
        _require(isinstance(filename, str), "Missing XML class filename")
        name = paths.resolve(filename)
        _require(name not in measured, f"Duplicate XML class for {name}")
        containers = cls.findall("./lines")
        _require(len(containers) == 1, f"Missing/duplicate XML lines container for {name}")
        source = blobs[files[name].oid]
        _require(b"\0" not in source, f"Unsupported binary Python source: {name}")
        line_count = len(source.splitlines())
        lines = {}
        for line in containers[0]:
            _require(line.tag == "line", f"Unexpected XML line record for {name}")
            number = _xml_integer(line.get("number"), "line number")
            hits = _xml_integer(line.get("hits"), "hits")
            _require(0 < number <= line_count, f"XML line outside measured source: {name}:{number}")
            _require(number not in lines, f"Duplicate XML executable line: {name}:{number}")
            lines[number] = hits
        measured[name] = lines
    _require(bool(measured), "Coverage XML contains no measured files")
    _require(set(measured) == set(summary["files"]), "XML and summary file sets disagree")
    for name, lines in measured.items():
        counts = {"covered": sum(hits > 0 for hits in lines.values()), "total": len(lines)}
        _require(counts == summary["files"][name]["statements"], f"XML and summary statements disagree: {name}")
    covered = sum(sum(hits > 0 for hits in lines.values()) for lines in measured.values())
    total = sum(len(lines) for lines in measured.values())
    _require(_xml_integer(root.get("lines-covered"), "lines-covered") == covered, "XML covered totals disagree")
    _require(_xml_integer(root.get("lines-valid"), "lines-valid") == total, "XML executable totals disagree")
    return measured


def _provenance(repo: Path, head: str, xml: bytes, data: bytes, expected_suite: str | None) -> dict:
    summary = load_summary(data)
    _require(summary["commit"] == head, "Stale provenance: measured commit does not match requested head")
    tree = _git(repo, "rev-parse", "--verify", f"{head}:{SOURCE_ROOT}").decode().strip()
    _require(summary["source_tree"] == tree, "Stale provenance: measured source tree does not match head")
    _require(summary["xml_sha256"] == hashlib.sha256(xml).hexdigest(), "XML digest does not match provenance")
    _require(summary["test_exit_code"] == 0, "Coverage measurement did not complete a successful test run")
    definition = summary["scope"]["definition"]
    _require(
        definition.get("xml_path_policy") == "checkout-tracked-v1",
        "Unverified provenance: producer did not capture checkout-confined XML paths",
    )
    suite = definition.get("suite")
    _require(isinstance(suite, str) and bool(suite), "Missing measured test suite")
    if expected_suite is not None:
        _require(suite == expected_suite, f"Wrong measured test suite: expected {expected_suite!r}, got {suite!r}")
    command = definition.get("command")
    _require(
        isinstance(command, list) and bool(command) and all(isinstance(arg, str) and arg for arg in command),
        "Missing measured test command",
    )
    config_hash = hashlib.sha256(_git(repo, "show", f"{head}:pyproject.toml")).hexdigest()
    _require(definition.get("config_sha256") == config_hash, "Stale provenance: coverage configuration differs")
    return summary


def check_changed_coverage(
    repo: Path, *, base: str, head: str, xml_path: Path, provenance_path: Path,
    expected_suite: str | None = None, minimum: Decimal | None = None, enforce: bool = False,
) -> dict:
    """Return a complete single-scope report or raise a measurement error."""
    _require(not enforce or minimum is not None, "--enforce requires an explicit --minimum")
    _require(minimum is None or minimum.is_finite() and 0 <= minimum <= 100, "Minimum must be finite, from 0 to 100")
    repo = repo.resolve()
    base_sha, head_sha = _commit(repo, base), _commit(repo, head)
    xml = xml_path.read_bytes()
    summary = _provenance(repo, head_sha, xml, provenance_path.read_bytes(), expected_suite)
    files = _source_files(repo, head_sha)
    changes = _changes(repo, base_sha, head_sha)
    oids = {entry.oid for entry in files.values()}
    oids.update(
        change.old_oid for change in changes
        if change.status != "A" and _production(change.path) and change.path in files
        and _production(change.previous_path)
    )
    blobs = _blobs(repo, oids)
    measured = _xml_lines(xml, summary, files, blobs)
    results = []
    for change in sorted(changes, key=lambda item: (item.path, item.previous_path)):
        added = _added_lines(repo, change, blobs)
        lines = measured.get(change.path)
        _require(not added or lines is not None, f"Missing XML measurement for changed file: {change.path}")
        executable = added.intersection(lines if lines is not None else ())
        covered = {number for number in executable if lines[number] > 0}
        if change.status == "D" or not _production(change.path):
            status = "deleted_or_out_of_scope"
        elif change.status == "R" and change.old_oid == change.new_oid and not added:
            status = "renamed_only"
        else:
            status = "measured" if executable else "no_executable_changes"
        results.append({
            "path": change.path, "previous_path": change.previous_path, "status": status,
            "added_lines": sorted(added), "executable_lines": sorted(executable),
            "covered_lines": sorted(covered), "uncovered_lines": sorted(executable - covered),
        })
    total = sum(len(item["executable_lines"]) for item in results)
    covered = sum(len(item["covered_lines"]) for item in results)
    threshold_met = covered * 100 >= Fraction(minimum) * total if total and minimum is not None else None
    return {
        "schema_version": 1, "metric": "changed_executable_lines",
        "status": "measured" if total else "not_applicable",
        "base_sha": base_sha, "head_sha": head_sha, "scope": summary["scope"],
        "measurement": {
            key: summary[key] for key in ("commit", "source_tree", "xml_sha256", "run_id", "run_attempt")
        },
        "added_line_count": sum(len(item["added_lines"]) for item in results),
        "changed_executable_lines": total, "covered_lines": covered, "uncovered_lines": total - covered,
        "percentage": 100 * covered / total if total else None,
        "minimum": float(minimum) if minimum is not None else None, "enforce": enforce,
        "threshold_met": threshold_met, "files": results, "diagnostics": [],
    }


def _minimum(value: str) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("minimum must be a number from 0 to 100") from exc
    if not number.is_finite() or not 0 <= number <= 100:
        raise argparse.ArgumentTypeError("minimum must be finite and from 0 to 100")
    return number


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--base", required=True, help="Explicit Git base commit/revision; no fallback")
    parser.add_argument("--head", required=True, help="Git commit/revision actually measured by the producer")
    parser.add_argument("--xml", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True, help="Shared coverage-summary.json")
    parser.add_argument("--expected-suite", help="Require this exact measured test suite label")
    parser.add_argument("--minimum", type=_minimum, help="Optional changed-line percentage, advisory without --enforce")
    parser.add_argument("--enforce", action="store_true", help="Exit 1 for a valid score below --minimum")
    parser.add_argument("--json-out", type=Path, help="Write a UTF-8 machine-readable report, including input errors")
    return parser


def _render(report: dict) -> str:
    if report["status"] == "error":
        return "Changed executable-line coverage ERROR: " + "; ".join(report["diagnostics"])
    scope = report["scope"]["definition"]["suite"]
    if report["status"] == "not_applicable":
        result = f"N/A: no added executable production lines (scope: {scope}); no percentage or threshold result."
    else:
        result = (
            f"Changed executable-line coverage: {report['covered_lines']}/{report['changed_executable_lines']} "
            f"({report['percentage']:.1f}%; scope: {scope})."
        )
        if report["minimum"] is not None:
            comparison = "met" if report["threshold_met"] else "NOT met"
            mode = "enforced" if report["enforce"] else "advisory"
            result += f" Minimum {report['minimum']:g}% {comparison} ({mode})."
    output = [result, "Statement lines only; changed-line branch coverage is not measured."]
    for item in report["files"]:
        if item["uncovered_lines"]:
            numbers = ", ".join(map(str, item["uncovered_lines"]))
            output.append(f"  Uncovered: {item['path']}:{numbers}")
    return "\n".join(output)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.enforce and args.minimum is None:
        parser.error("--enforce requires an explicit --minimum")
    output_allowed = False
    try:
        if args.json_out is not None:
            _require(
                args.json_out.resolve() not in (args.xml.resolve(), args.provenance.resolve()),
                "--json-out must not overwrite XML or provenance input",
            )
            output_allowed = True
        report = check_changed_coverage(
            args.repo_root, base=args.base, head=args.head, xml_path=args.xml,
            provenance_path=args.provenance, expected_suite=args.expected_suite,
            minimum=args.minimum, enforce=args.enforce,
        )
    except (ChangedCoverageError, TrendError, OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        report = {
            "schema_version": 1, "metric": "changed_executable_lines", "status": "error",
            "base_sha": None, "head_sha": None, "percentage": None, "threshold_met": None,
            "files": [], "diagnostics": [str(exc)],
        }
    if output_allowed:
        try:
            args.json_out.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"Changed executable-line coverage ERROR: cannot write JSON output: {exc}", file=sys.stderr)
            return 2
    print(_render(report), file=sys.stderr if report["status"] == "error" else sys.stdout)
    if report["status"] == "error":
        return 2
    return 1 if args.enforce and report["threshold_met"] is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
