"""Informational coverage history for the existing Windows unit/logic CI job.

``measure -- <pytest argv>`` removes stale outputs, runs that command unchanged,
and writes coverage-summary.json even when tests fail (if valid XML exists).
``report`` verifies the local measurement and reads GitHub Actions via gh.
Only completed, successful same-repository main runs of CI, with a successful
coverage job/test step, an ancestor commit and identical scope can be baselines.
Downloaded ZIPs are bounded and read as JSON in memory, never extracted/executed.

Schema 1 stores integer statement/branch counts separately, all file counts,
commit/source-tree/XML hashes, run identity, test outcome, and a canonical scope
digest. Scope includes exact argv, the entire pyproject.toml hash, Python minor,
OS/architecture, test/coverage versions and relevant environment overrides.
Even an unrelated pyproject edit deliberately resets comparison compatibility.
XML source roots and filenames must bind uniquely to tracked files in the
measured checkout; an editable install pointing elsewhere cannot claim its SHA.

History is NOT permanent: artifacts have 90-day retention (subject to repository
limits/deletion); lookup examines at most the latest 100 completed workflow runs.
Missing, expired, incompatible and malformed history never implies a score.
Regressions return success; measurement/retrieval errors return nonzero, inside
the existing informational job. No global threshold or GitHub write is used.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import os
import platform
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from collections.abc import Iterable
from fractions import Fraction
from importlib.metadata import version
from pathlib import Path, PurePosixPath

SCHEMA_VERSION = 1
WORKFLOW = ".github/workflows/ci.yml"
MAIN_REF = "refs/heads/main"
MAIN_EVENTS = {"push", "schedule", "workflow_dispatch"}
METRICS = ("statements", "branches")
MAX_BYTES = 2 * 1024 * 1024
MAX_XML_BYTES = 32 * 1024 * 1024
MAX_RUNS = 100
MAX_FILES = 10
SUMMARY_NAME = "coverage-summary.json"
SOURCE_ROOT = "src/tradinglab"
SHA = re.compile(r"[0-9a-f]{40}")


class TrendError(ValueError):
    """A missing, malformed, stale or untrustworthy measurement."""


class ApiError(TrendError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def require(condition: bool, message: str) -> None:
    if not condition:
        raise TrendError(message)


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def scope_id(definition: dict) -> str:
    return digest(json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))


def git_blob(name: str) -> bytes:
    result = subprocess.run(["git", "show", name], capture_output=True, check=False, timeout=30)
    require(result.returncode == 0, f"Cannot read Git blob {name}: {result.stderr.decode(errors='replace')}")
    return result.stdout


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", check=False, timeout=30,
    )
    require(result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def is_ancestor(base: str, head: str) -> bool:
    require(bool(SHA.fullmatch(base) and SHA.fullmatch(head)), "Invalid ancestry commit SHA")
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", base, head],
        capture_output=True, text=True, check=False, timeout=30,
    )
    require(result.returncode in (0, 1), f"Ancestry lookup failed (full checkout required): {result.stderr}")
    return result.returncode == 0


def file_name(raw: str) -> str:
    require(isinstance(raw, str), "Invalid coverage filename")
    name = raw.replace("\\", "/")
    path = PurePosixPath(name)
    require(
        bool(name) and not path.is_absolute() and ".." not in path.parts
        and not re.match(r"^[A-Za-z]:", name) and path.suffix == ".py"
        and not any(ord(char) < 32 or ord(char) == 127 for char in name),
        f"Unsafe or unsupported coverage filename: {raw!r}",
    )
    name = path.as_posix()
    if name.startswith("src/tradinglab/"):
        return name
    if name.startswith("tradinglab/"):
        name = name[len("tradinglab/"):]
    return f"src/tradinglab/{name}"


def _lexical(raw: str) -> str:
    require(isinstance(raw, str), "Path must be a string")
    require(not any(ord(char) < 32 or ord(char) == 127 for char in raw), f"Control character in path: {raw!r}")
    name = raw.replace("\\", "/")
    require(".." not in name.split("/"), f"Path traversal is not allowed: {raw!r}")
    require(not re.match(r"^[A-Za-z]:(?!/)", name), f"Drive-relative path is not allowed: {raw!r}")
    normalized = PurePosixPath(name).as_posix()
    return normalized + "/" if re.fullmatch(r"[A-Za-z]:", normalized) else normalized


def _absolute(name: str) -> bool:
    return name.startswith("/") or bool(re.match(r"^[A-Za-z]:/", name))


class XmlPaths:
    """Bind XML paths to declared sources and known Git files, on either OS.

    ``files`` supplies canonical repository-relative source paths (a dict's keys
    are sufficient). Relative filenames use only declared XML roots, never an
    inferred basename, package prefix or suffix match.
    """

    def __init__(self, producer_root: str, sources: list[str], files: Iterable[str]):
        self.root = _lexical(producer_root)
        if self.root != "/" and not re.fullmatch(r"[A-Za-z]:/", self.root):
            self.root = self.root.rstrip("/")
        require(_absolute(self.root), "Provenance producer_root must be absolute")
        self.windows = bool(re.match(r"^[A-Za-z]:/", self.root) or self.root.startswith("//"))
        self.names: dict[str, str] = {}
        for name in files:
            require(file_name(name) == name, f"Non-canonical Git source path: {name!r}")
            key = self._key(_lexical(name))
            require(key not in self.names, f"Ambiguous case-colliding Git paths: {name}")
            self.names[key] = name
        require(bool(sources), "Coverage XML has no declared source roots")
        self.roots = []
        for source in sources:
            name = _lexical(source)
            if _absolute(name):
                name = self._relative(name)
            require(
                self._key(name) in (".", "src", SOURCE_ROOT) or self._key(name).startswith(f"{SOURCE_ROOT}/"),
                f"XML source is outside the measured source tree: {source!r}",
            )
            self.roots.append(name)

    def _key(self, name: str) -> str:
        return name.casefold() if self.windows else name

    def _relative(self, name: str) -> str:
        root = self.root.rstrip("/") + "/"
        if self._key(name) == self._key(self.root):
            return "."
        require(self._key(name).startswith(self._key(root)), f"XML path is outside producer checkout: {name!r}")
        return name[len(root):]

    def resolve(self, filename: str) -> str:
        require(bool(filename), "Missing XML class filename")
        name = _lexical(filename)
        if _absolute(name):
            candidates = {self._relative(name)}
        else:
            candidates = {str(PurePosixPath(root) / name) for root in self.roots}
        matches = {
            self.names[self._key(candidate)]
            for candidate in candidates if self._key(candidate) in self.names
        }
        require(
            len(matches) == 1,
            f"XML filename is {'ambiguous' if matches else 'unmapped'} at measured head: {filename!r}",
        )
        return matches.pop()


def empty_counts() -> dict:
    return {metric: {"covered": 0, "total": 0} for metric in METRICS}


def sum_counts(files: dict) -> dict:
    counts = empty_counts()
    for stats in files.values():
        for metric in METRICS:
            for key in ("covered", "total"):
                counts[metric][key] += stats[metric][key]
    return counts


def xml_integer(value: str | None) -> int:
    require(isinstance(value, str) and bool(re.fullmatch(r"\d+", value)), "Invalid/missing XML count")
    return int(value)


def _xml_root(data: bytes) -> ET.Element:
    require(len(data) <= MAX_XML_BYTES, "Coverage XML exceeds size limit")
    require(b"<!DOCTYPE" not in data.upper() and b"<!ENTITY" not in data.upper(), "XML DTD/entities forbidden")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise TrendError(f"Malformed coverage XML: {exc}") from exc
    require(root.tag == "coverage", "Expected coverage.py XML root")
    return root


def parse_xml(data: bytes, *, file_map: dict[str, str] | None = None) -> tuple[dict, dict]:
    root = _xml_root(data)
    files = {}
    for cls in root.findall("./packages/package/classes/class"):
        raw = cls.get("filename")
        if file_map is not None:
            require(raw in file_map, f"XML filename has no measured checkout binding: {raw!r}")
            name = file_name(file_map[raw])
        else:
            name = file_name(raw)
        require(name not in files, f"Duplicate XML file: {name}")
        stats = empty_counts()
        seen = set()
        for line in cls.findall("./lines/line"):
            number = xml_integer(line.get("number"))
            require(number > 0 and number not in seen, f"Duplicate/invalid statement line in {name}")
            seen.add(number)
            stats["statements"]["total"] += 1
            stats["statements"]["covered"] += int(xml_integer(line.get("hits")) > 0)
            branch = line.get("branch", "false")
            require(branch in ("true", "false"), f"Invalid branch flag in {name}")
            if branch == "true":
                match = re.fullmatch(r"[\d.]+% \((\d+)/(\d+)\)", line.get("condition-coverage", ""))
                require(match is not None, f"Missing/malformed branch counts in {name}")
                covered, total = map(int, match.groups())
                require(0 <= covered <= total and total > 0, f"Invalid branch counts in {name}")
                stats["branches"]["covered"] += covered
                stats["branches"]["total"] += total
        files[name] = stats
    overall = sum_counts(files)
    for metric, prefix in (("statements", "lines"), ("branches", "branches")):
        for key, suffix in (("covered", "covered"), ("total", "valid")):
            require(
                overall[metric][key] == xml_integer(root.get(f"{prefix}-{suffix}")),
                f"XML overall {metric} {key} disagrees with file counts",
            )
    require(bool(files), "Coverage XML contains no measured files")
    return overall, dict(sorted(files.items()))


def tracked_source_files() -> set[str]:
    return {
        name for name in git("ls-tree", "-r", "--name-only", "-z", "HEAD", "--", SOURCE_ROOT).split("\0")
        if name.endswith(".py")
    }


def parse_measured_xml(data: bytes) -> tuple[dict, dict]:
    """Validate physical producer paths before attaching checkout provenance."""
    root = _xml_root(data)
    checkout = Path.cwd().resolve()
    sources = [source.text or "" for source in root.findall("./sources/source")]
    paths = XmlPaths(str(checkout), sources, tracked_source_files())
    for source in paths.roots:
        directory = checkout / source
        require(
            directory.is_dir() and directory.resolve().is_relative_to(checkout),
            f"XML source is missing or resolves outside producer checkout: {source!r}",
        )
    mapping = {}
    for cls in root.findall("./packages/package/classes/class"):
        raw = cls.get("filename")
        name = paths.resolve(raw)
        target = checkout / name
        require(
            target.is_file() and target.resolve() == target,
            f"XML file is missing or redirected outside its tracked checkout path: {raw!r}",
        )
        mapping[raw] = name
    return parse_xml(data, file_map=mapping)


def identity() -> dict:
    values = {
        "repository": os.environ["GITHUB_REPOSITORY"],
        "workflow": WORKFLOW,
        "run_id": int(os.environ["GITHUB_RUN_ID"]),
        "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
        "commit": os.environ["GITHUB_SHA"],
        "ref": os.environ["GITHUB_REF"],
        "event": os.environ["GITHUB_EVENT_NAME"],
    }
    require(
        os.environ["GITHUB_WORKFLOW_REF"] == f"{values['repository']}/{WORKFLOW}@{values['ref']}",
        "Unexpected workflow/ref identity",
    )
    require(git("rev-parse", "HEAD") == values["commit"], "Checkout HEAD does not match GITHUB_SHA")
    values["source_tree"] = git("rev-parse", "HEAD:src/tradinglab")
    return values


def scope(command: list[str]) -> dict:
    definition = {
        "suite": "unit-scanner-logic-oracles-v1",
        "command": command,
        "config_sha256": digest(git_blob("HEAD:pyproject.toml")),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "os": platform.system(),
        "arch": os.environ.get("RUNNER_ARCH", platform.machine()),
        "versions": {name: version(name) for name in ("coverage", "pytest-cov", "pytest")},
        "coverage_config": "pyproject.toml",
        "raw_data_file": ".coverage",
        "xml_path_policy": "checkout-tracked-v1",
        "environment": {
            key: digest(value.encode("utf-8")) for key, value in sorted(os.environ.items())
            if key in ("MPLBACKEND", "PYTHONPATH", "PYTEST_ADDOPTS", "PYTEST_DISABLE_PLUGIN_AUTOLOAD")
            or key.startswith(("COVERAGE_", "TRADINGLAB_"))
        },
    }
    return {"id": scope_id(definition), "definition": definition}


def require_clean() -> None:
    dirty = git(
        "status", "--porcelain=v1", "--untracked-files=all", "--",
        "src/tradinglab", "tests", "pyproject.toml", ".coveragerc", "setup.cfg", "tox.ini", "pytest.ini",
    )
    require(not dirty, f"Source/tests/config must be clean before and after measurement: {dirty}")


def validate_counts(stats: object) -> None:
    require(isinstance(stats, dict), "Invalid measurement counts")
    for metric in METRICS:
        counts = stats.get(metric)
        require(isinstance(counts, dict), f"Missing {metric} counts")
        require(
            all(type(counts.get(key)) is int for key in ("covered", "total"))
            and 0 <= counts["covered"] <= counts["total"],
            f"Invalid {metric} counts",
        )


def validate_summary(value: object) -> dict:
    require(isinstance(value, dict), "Summary must be a JSON object")
    require(
        type(value.get("schema_version")) is int and value["schema_version"] == SCHEMA_VERSION,
        "Unsupported coverage summary schema",
    )
    for key in ("commit", "source_tree"):
        require(isinstance(value.get(key), str) and bool(SHA.fullmatch(value[key])), f"Invalid {key}")
    require(
        isinstance(value.get("repository"), str)
        and bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value["repository"])),
        "Invalid repository",
    )
    require(value.get("workflow") == WORKFLOW, "Unexpected measurement workflow")
    for key in ("run_id", "run_attempt"):
        require(type(value.get(key)) is int and value[key] > 0, f"Invalid {key}")
    require(isinstance(value.get("ref"), str), "Missing measurement ref")
    require(value.get("event") in MAIN_EVENTS | {"pull_request"}, "Unexpected measurement event")
    require(type(value.get("test_exit_code")) is int and value["test_exit_code"] >= 0, "Invalid test exit code")
    require(
        isinstance(value.get("xml_sha256"), str) and bool(re.fullmatch(r"[0-9a-f]{64}", value["xml_sha256"])),
        "Invalid XML digest",
    )
    definition = value.get("scope")
    require(isinstance(definition, dict) and isinstance(definition.get("definition"), dict), "Missing scope")
    require(definition.get("id") == scope_id(definition["definition"]), "Scope digest mismatch")
    content = definition["definition"]
    require(
        isinstance(content.get("suite"), str) and bool(content["suite"])
        and isinstance(content.get("command"), list) and bool(content["command"])
        and all(isinstance(arg, str) and bool(arg) for arg in content["command"])
        and isinstance(content.get("config_sha256"), str)
        and bool(re.fullmatch(r"[0-9a-f]{64}", content["config_sha256"])),
        "Invalid scope definition",
    )
    provenance = value.get("provenance")
    require(isinstance(provenance, dict), "Missing provenance")
    require(
        all(provenance.get(key) is True for key in ("clean_start", "clean_end", "fresh_outputs"))
        and provenance.get("source_root") == "src/tradinglab"
        and isinstance(provenance.get("producer_root"), str) and bool(provenance["producer_root"])
        and isinstance(provenance.get("xml_sources"), list)
        and all(isinstance(source, str) for source in provenance["xml_sources"]),
        "Invalid/incomplete clean measurement provenance",
    )
    files = value.get("files")
    require(isinstance(files, dict) and bool(files), "Missing file measurements")
    for name, stats in files.items():
        require(file_name(name) == name, f"Non-canonical summary filename: {name}")
        validate_counts(stats)
    validate_counts(value.get("overall"))
    require(value.get("overall") == sum_counts(files), "Summary totals disagree with file counts")
    return value


def load_summary(data: bytes) -> dict:
    require(len(data) <= MAX_BYTES, "Summary exceeds size limit")

    def unique_object(pairs: list[tuple]) -> dict:
        result = {}
        for key, value in pairs:
            require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        raise TrendError(f"Non-finite JSON value: {value}")

    try:
        return validate_summary(json.loads(data, object_pairs_hook=unique_object, parse_constant=invalid_constant))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise TrendError(f"Malformed coverage summary JSON: {exc}") from exc


def measure(command: list[str], xml: Path, output: Path) -> int:
    require(bool(command), "measure requires a command after --")
    require(
        not any(arg.startswith(("--cov-append", "--cov-config")) for arg in command)
        and not any(option in os.environ.get("PYTEST_ADDOPTS", "") for option in ("--cov-append", "--cov-config")),
        "Measurement forbids append/config overrides; uses fresh data and pyproject.toml",
    )
    xml.unlink(missing_ok=True)
    output.unlink(missing_ok=True)
    raw = Path(".coverage")
    raw.unlink(missing_ok=True)
    for sidecar in raw.parent.glob(raw.name + ".*"):
        sidecar.unlink()
    require_clean()
    metadata = identity()
    measurement_scope = scope(command)
    result = subprocess.run(
        command, check=False,
        env={**os.environ, "COVERAGE_FILE": str(raw.resolve()), "COVERAGE_RCFILE": str(Path("pyproject.toml").resolve())},
    )
    require_clean()
    require(identity() == metadata, "Checkout/run identity changed during measurement")
    require(scope(command) == measurement_scope, "Measurement configuration changed during test execution")
    data = xml.read_bytes()
    overall, files = parse_measured_xml(data)
    summary = validate_summary({
        "schema_version": SCHEMA_VERSION,
        **metadata,
        "scope": measurement_scope,
        "test_exit_code": result.returncode,
        "xml_sha256": digest(data),
        "provenance": {
            "producer_root": str(Path.cwd().resolve()),
            "source_root": "src/tradinglab",
            "clean_start": True,
            "clean_end": True,
            "fresh_outputs": True,
            "xml_sources": [source.text or "" for source in ET.fromstring(data).findall("./sources/source")],
        },
        "overall": overall,
        "files": files,
    })
    output.write_bytes(canonical(summary))
    return result.returncode


class GitHub:
    """GET-only gh adapter; authentication stays with gh, never in ZIP contents."""

    def get(self, endpoint: str, *, binary: bool = False):
        result = subprocess.run(
            ["gh", "api", "--method", "GET", "-H", "Accept: application/vnd.github+json",
             "-H", "X-GitHub-Api-Version: 2022-11-28", endpoint],
            capture_output=True, check=False, timeout=60,
        )
        if result.returncode:
            error = result.stderr.decode("utf-8", errors="replace").strip()
            status = re.search(r"\(HTTP (\d{3})\)", error)
            raise ApiError(f"GitHub GET {endpoint}: {error}", int(status[1]) if status else None)
        if binary:
            require(len(result.stdout) <= MAX_BYTES, "Artifact download exceeds size limit")
            return result.stdout
        try:
            value = json.loads(result.stdout)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ApiError(f"Malformed GitHub JSON for {endpoint}: {exc}") from exc
        require(isinstance(value, dict), f"Malformed GitHub object for {endpoint}")
        return value


def artifact_name(run_id: int, attempt: int) -> str:
    return f"coverage-summary-{run_id}-{attempt}"


def unpack_summary(data: bytes) -> dict:
    require(len(data) <= MAX_BYTES, "Artifact exceeds size limit")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            require(
                len(members) == 1 and members[0].filename == SUMMARY_NAME
                and members[0].file_size <= MAX_BYTES,
                "Expected one bounded coverage-summary.json in artifact",
            )
            return load_summary(archive.read(members[0]))
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise TrendError(f"Invalid summary ZIP: {exc}") from exc


def api_items(value: dict, key: str) -> list[dict]:
    items = value.get(key)
    require(isinstance(items, list) and all(isinstance(item, dict) for item in items), f"Missing API {key}")
    return items


def run_repository(run: dict) -> str | None:
    repository = run.get("head_repository")
    return repository.get("full_name") if isinstance(repository, dict) else None


def same_run(summary: dict, run: dict) -> bool:
    return (
        summary["run_id"] == run.get("id")
        and summary["run_attempt"] == run.get("run_attempt")
        and summary["commit"] == run.get("head_sha")
        and summary["event"] == run.get("event")
        and summary["ref"] == MAIN_REF
    )


def find_baseline(current: dict, api: GitHub) -> tuple[dict | None, str]:
    root = f"repos/{current['repository']}"
    live = api.get(f"{root}/actions/runs/{current['run_id']}")
    require(
        same_run(current, live) and live.get("path") == WORKFLOW
        and run_repository(live) == current["repository"]
        and live.get("head_branch") == "main" and type(live.get("workflow_id")) is int,
        "Current measurement does not match the Actions run",
    )
    response = api.get(
        f"{root}/actions/workflows/{live['workflow_id']}/runs?branch=main&status=completed&per_page={MAX_RUNS}"
    )
    runs = api_items(response, "workflow_runs")
    require(all(type(run.get("id")) is int for run in runs), "Invalid Actions run id")
    skipped = Counter()
    for run in sorted(runs, key=lambda item: item["id"], reverse=True):
        if run["id"] >= current["run_id"]:
            continue
        if not (
            run.get("status") == "completed" and run.get("conclusion") == "success"
            and run.get("event") in MAIN_EVENTS and run.get("head_branch") == "main"
            and run.get("workflow_id") == live["workflow_id"] and run.get("path") == WORKFLOW
            and run_repository(run) == current["repository"]
        ):
            skipped["unsuccessful/untrusted run"] += 1
            continue
        sha = run.get("head_sha")
        require(isinstance(sha, str) and bool(SHA.fullmatch(sha)), "Invalid prior run commit")
        if not is_ancestor(sha, current["commit"]):
            skipped["non-ancestor"] += 1
            continue
        attempt = run.get("run_attempt")
        require(type(attempt) is int and attempt > 0, "Invalid prior run attempt")
        artifacts = api_items(api.get(f"{root}/actions/runs/{run['id']}/artifacts?per_page=100"), "artifacts")
        matches = [a for a in artifacts if a.get("name") == artifact_name(run["id"], attempt)]
        require(len(matches) <= 1, f"Duplicate summary artifacts for run {run['id']}")
        if not matches:
            skipped["artifact absent (bootstrap/deleted/expired/not published)"] += 1
            continue
        artifact = matches[0]
        if artifact.get("expired") is True:
            skipped["expired artifact"] += 1
            continue
        require(
            artifact.get("expired") is False and type(artifact.get("id")) is int
            and type(artifact.get("size_in_bytes")) is int and 0 < artifact["size_in_bytes"] <= MAX_BYTES,
            f"Invalid/oversized artifact metadata for run {run['id']}",
        )
        jobs = api_items(
            api.get(f"{root}/actions/runs/{run['id']}/attempts/{attempt}/jobs?per_page=100"), "jobs"
        )
        eligible = [
            job for job in jobs if job.get("name") == "coverage"
            and job.get("status") == "completed" and job.get("conclusion") == "success"
            and any(
                step.get("name") == "Run tests with coverage"
                and step.get("status") == "completed" and step.get("conclusion") == "success"
                for step in api_items(job, "steps")
            )
        ]
        if len(eligible) != 1:
            skipped["unsuccessful/missing coverage job"] += 1
            continue
        try:
            data = api.get(f"{root}/actions/artifacts/{artifact['id']}/zip", binary=True)
        except ApiError as exc:
            if exc.status not in (404, 410):
                raise
            skipped["artifact unavailable (HTTP 404/410)"] += 1
            continue
        try:
            previous = unpack_summary(data)
            require(
                same_run(previous, run) and previous["repository"] == current["repository"]
                and previous["source_tree"] == git("rev-parse", f"{sha}:src/tradinglab"),
                "Stale/mismatched artifact run, commit or source tree",
            )
        except TrendError as exc:
            raise TrendError(f"Invalid baseline artifact for run {run['id']}: {exc}") from exc
        if previous["test_exit_code"] != 0:
            skipped["unsuccessful measurement"] += 1
            continue
        if previous["scope"] != current["scope"]:
            skipped["incompatible scope/config/runtime"] += 1
            continue
        note = f"Baseline: run {run['id']} attempt {attempt}, commit {sha}."
        if skipped:
            note += " Newer candidates skipped: " + ", ".join(f"{n} {why}" for why, n in skipped.items()) + "."
        return previous, note
    reasons = ", ".join(f"{n} {why}" for why, n in skipped.items()) or "no prior completed main run (bootstrap)"
    return None, f"No compatible baseline: {reasons}. No regression inference is possible."


def rate(counts: dict) -> Fraction | None:
    return Fraction(counts["covered"], counts["total"]) if counts["total"] else None


def delta(before: dict | None, after: dict | None, metric: str) -> Fraction | None:
    if before is None or after is None:
        return None
    old, new = rate(before[metric]), rate(after[metric])
    return None if old is None or new is None else 100 * (new - old)


def score(stats: dict | None, metric: str) -> str:
    if stats is None:
        return "not present"
    counts = stats[metric]
    pct = rate(counts)
    return f"{'N/A' if pct is None else f'{float(pct * 100):.2f}%'} ({counts['covered']}/{counts['total']})"


def delta_text(value: Fraction | None) -> str:
    if value is None:
        return "N/A"
    if value and abs(value) < Fraction(1, 200):
        return ("-" if value < 0 else "+") + "<0.01 pp"
    return f"{float(value):+.2f} pp"


def safe_text(value: object) -> str:
    return html.escape(str(value)).replace("|", "&#124;").replace("`", "&#96;").replace("\r", " ").replace("\n", " ")


def render_report(current: dict, previous: dict | None, note: str) -> str:
    old = previous["overall"] if previous else None
    regressions = [m for m in METRICS if (change := delta(old, current["overall"], m)) is not None and change < 0]
    title = (
        f"Overall regression: {', '.join(regressions)}" if regressions
        else "No overall regression" if previous else "Comparison unavailable"
    )
    lines = [
        f"## Coverage trend: {title}", "",
        f"Commit `{current['commit']}`; run {current['run_id']} attempt {current['run_attempt']}.",
        f"Scope `{current['scope']['id']}` (unit/scanner/logic/oracles; GUI-only coverage is separate).",
        safe_text(note), "",
        "| Metric | Baseline covered/total | Current covered/total | Change |",
        "| --- | --- | --- | --- |",
    ]
    for metric in METRICS:
        lines.append(
            f"| {metric.title()} | {score(old, metric) if previous else 'no baseline'}"
            f" | {score(current['overall'], metric)} | {delta_text(delta(old, current['overall'], metric))} |"
        )
    before_files = previous["files"] if previous else {}
    names = set(current["files"]) | set(before_files)

    def order(name: str) -> tuple:
        changes = [delta(before_files.get(name), current["files"].get(name), m) for m in METRICS]
        comparable = [change for change in changes if change is not None]
        worst = min(comparable, default=Fraction(0))
        missing = name not in before_files or name not in current["files"]
        return (0, worst, name) if worst < 0 else (1 if missing else 2, Fraction(0), name)

    selected = sorted(names, key=order)[:MAX_FILES]
    lines += [
        "", f"### Files (up to {MAX_FILES}; largest percentage-point drops first, then added/removed, then path)",
        "| File | Statements: prior -> current | Change | Branches: prior -> current | Change |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name in selected:
        before, after = before_files.get(name), current["files"].get(name)
        cells = [safe_text(name)]
        for metric in METRICS:
            cells += [
                f"{score(before, metric) if previous else 'no baseline'} -> {score(after, metric)}",
                delta_text(delta(before, after, metric)),
            ]
        lines.append("| " + " | ".join(cells) + " |")
    lines += [
        "", "Statements and branch outcomes are separate; coverage.py's combined percentage is not line coverage.",
        "Zero denominators are N/A. Added/removed files have no per-file delta. Full file counts are in the JSON.",
        "Informational only; no threshold. Artifacts are retained for 90 days, subject to repository limits/deletion.",
        f"Search is bounded to {MAX_RUNS} latest completed CI runs. This is finite history, not permanent storage.",
        "Scope changes (including any pyproject.toml edit or test/coverage version change) reset compatibility.", "",
    ]
    return "\n".join(lines)


def emit(text: str) -> None:
    print(text)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(path).open("a", encoding="utf-8") as stream:
            stream.write(text + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("measure", "report"))
    parser.add_argument("--xml", type=Path, default=Path("coverage.xml"))
    parser.add_argument("--output", type=Path, default=Path(SUMMARY_NAME))
    args_list = list(sys.argv[1:] if argv is None else argv)
    split = args_list.index("--") if "--" in args_list else len(args_list)
    args = parser.parse_args(args_list[:split])
    command = args_list[split + 1:]
    current = None
    try:
        if args.action == "measure":
            return measure(command, args.xml, args.output)
        require(not command, "report does not accept a command")
        current = load_summary(args.output.read_bytes())
        require(all(current[key] == value for key, value in identity().items()), "Stale local measurement identity")
        xml_data = args.xml.read_bytes()
        require(digest(xml_data) == current["xml_sha256"], "Stale local measurement XML digest")
        overall, files = parse_measured_xml(xml_data)
        require(overall == current["overall"] and files == current["files"], "Local XML/summary counts disagree")
        require(
            current["ref"] == MAIN_REF and current["event"] in MAIN_EVENTS,
            "Only main push/schedule/workflow_dispatch measurements may query history",
        )
        if current["test_exit_code"]:
            previous, note = None, "Tests failed: these counts are partial; this run is not baseline-eligible."
        else:
            previous, note = find_baseline(current, GitHub())
        emit(render_report(current, previous, note))
        return 0
    except (TrendError, OSError, KeyError, subprocess.TimeoutExpired) as exc:
        note = f"Coverage trend error: {exc}. No comparison was made."
        # Never display stale/invalid current data as a valid measurement.
        emit(f"## Coverage trend error\n\n{safe_text(note)}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
