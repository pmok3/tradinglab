"""Stochastic campaign planning and reporting for TradingLab GUI exploration."""
from __future__ import annotations

import hashlib
import html
import json
import os
import random
import re
import secrets
import uuid
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_CATALOG = Path(__file__).with_name("scenarios.json")
DEFAULT_CAMPAIGN_ROOT = Path("_ux_explorer") / "campaigns"
OUTCOMES = frozenset({"passed", "findings", "blocked", "aborted"})
_SURFACE_ID = re.compile(r"^TL\.[A-Z0-9_.-]+$")
_SCENARIO_ID = re.compile(r"^ux-[a-z0-9-]+$")


class CampaignError(ValueError):
    """Raised when a campaign or catalog violates its persisted contract."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CampaignError(f"Could not read JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CampaignError(f"{path} must contain a JSON object.")
    return value


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


@contextmanager
def _campaign_lock(campaign_path: Path) -> Iterator[None]:
    """Hold an OS-backed exclusive lock for one campaign state file."""
    lock_path = campaign_path.with_suffix(campaign_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT)
    locked = False

    def _lock() -> None:
        nonlocal locked
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise CampaignError(
                    f"Campaign {campaign_path} is already being updated.",
                ) from exc
        else:
            import fcntl

            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise CampaignError(
                    f"Campaign {campaign_path} is already being updated.",
                ) from exc
        locked = True

    def _unlock() -> None:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)

    try:
        _lock()
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.ftruncate(descriptor, 0)
        os.write(
            descriptor,
            json.dumps({
                "pid": os.getpid(),
                "created_at": _utc_now(),
            }).encode("utf-8"),
        )
        os.fsync(descriptor)
        yield
    finally:
        try:
            if locked:
                _unlock()
        finally:
            os.close(descriptor)


def _campaign_status(state: dict[str, Any]) -> str:
    statuses = {entry.get("status") for entry in state.get("scenarios", [])}
    if "blocked" in statuses:
        return "blocked"
    if statuses & {"pending", "in_progress"}:
        return "active"
    return "complete"


def _write_campaign_state(state_path: Path, state: dict[str, Any]) -> None:
    state["status"] = _campaign_status(state)
    _write_json_atomic(state_path, state)


def _read_jsonl(path: Path, *, label: str) -> list[dict[str, Any]]:
    if not path.exists():
        raise CampaignError(f"Missing {label} JSONL: {path}")
    entries: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CampaignError(f"Could not read {label} JSONL from {path}: {exc}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CampaignError(
                f"Could not parse {label} JSONL line {line_number} in {path}: {exc}",
            ) from exc
        if not isinstance(value, dict):
            raise CampaignError(f"{label} JSONL line {line_number} in {path} must be an object.")
        entries.append(value)
    return entries


def load_catalog(path: Path = DEFAULT_CATALOG) -> dict[str, Any]:
    """Load and validate the committed UX surface and scenario catalog."""
    catalog = _read_json(path)
    if catalog.get("schema_version") != SCHEMA_VERSION:
        raise CampaignError(
            f"Unsupported catalog schema_version={catalog.get('schema_version')!r}.",
        )
    surfaces = catalog.get("surfaces")
    scenarios = catalog.get("scenarios")
    if not isinstance(surfaces, list) or not surfaces:
        raise CampaignError("Catalog must define a non-empty surfaces list.")
    if not isinstance(scenarios, list) or not scenarios:
        raise CampaignError("Catalog must define a non-empty scenarios list.")

    surface_ids: set[str] = set()
    for surface in surfaces:
        if not isinstance(surface, dict):
            raise CampaignError("Each surface must be an object.")
        surface_id = surface.get("id")
        if not isinstance(surface_id, str) or not _SURFACE_ID.fullmatch(surface_id):
            raise CampaignError(f"Invalid surface id: {surface_id!r}.")
        if surface_id in surface_ids:
            raise CampaignError(f"Duplicate surface id: {surface_id}.")
        if not str(surface.get("title", "")).strip():
            raise CampaignError(f"Surface {surface_id} has no title.")
        if not isinstance(surface.get("tags"), list):
            raise CampaignError(f"Surface {surface_id} tags must be a list.")
        surface_ids.add(surface_id)

    scenario_ids: set[str] = set()
    referenced_surfaces: set[str] = set()
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise CampaignError("Each scenario must be an object.")
        scenario_id = scenario.get("id")
        if not isinstance(scenario_id, str) or not _SCENARIO_ID.fullmatch(scenario_id):
            raise CampaignError(f"Invalid scenario id: {scenario_id!r}.")
        if scenario_id in scenario_ids:
            raise CampaignError(f"Duplicate scenario id: {scenario_id}.")
        scenario_ids.add(scenario_id)
        for required in ("title", "mission"):
            if not str(scenario.get(required, "")).strip():
                raise CampaignError(f"Scenario {scenario_id} has no {required}.")
        if not isinstance(scenario.get("entry_points"), list):
            raise CampaignError(f"Scenario {scenario_id} entry_points must be a list.")
        if not isinstance(scenario.get("tags"), list):
            raise CampaignError(f"Scenario {scenario_id} tags must be a list.")
        weight = scenario.get("weight", 1)
        if not isinstance(weight, int) or weight < 1:
            raise CampaignError(f"Scenario {scenario_id} weight must be positive.")
        max_minutes = scenario.get("max_minutes", 15)
        if not isinstance(max_minutes, int) or max_minutes < 1:
            raise CampaignError(
                f"Scenario {scenario_id} max_minutes must be positive.",
            )
        scenario_surfaces = scenario.get("surface_ids")
        if not isinstance(scenario_surfaces, list) or not scenario_surfaces:
            raise CampaignError(f"Scenario {scenario_id} has no surface_ids.")
        unknown = set(scenario_surfaces) - surface_ids
        if unknown:
            raise CampaignError(
                f"Scenario {scenario_id} references unknown surfaces: {sorted(unknown)}.",
            )
        referenced_surfaces.update(scenario_surfaces)

    uncovered = surface_ids - referenced_surfaces
    if uncovered:
        raise CampaignError(
            f"Catalog surfaces are not assigned to a scenario: {sorted(uncovered)}.",
        )
    return catalog


def catalog_digest(catalog: dict[str, Any]) -> str:
    """Return a stable content digest for campaign provenance."""
    encoded = json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _matches_tags(scenario: dict[str, Any], tags: Sequence[str]) -> bool:
    requested = {tag.strip().lower() for tag in tags if tag.strip()}
    available = {str(tag).lower() for tag in scenario.get("tags", [])}
    return requested.issubset(available)


def _weighted_sample_without_replacement(
    scenarios: list[dict[str, Any]],
    count: int,
    rng: random.Random,
) -> list[dict[str, Any]]:
    pool = list(scenarios)
    selected: list[dict[str, Any]] = []
    while pool and len(selected) < count:
        choice = rng.choices(
            pool,
            weights=[int(item.get("weight", 1)) for item in pool],
            k=1,
        )[0]
        selected.append(choice)
        pool.remove(choice)
    return selected


def create_campaign(
    root: Path = DEFAULT_CAMPAIGN_ROOT,
    *,
    catalog_path: Path = DEFAULT_CATALOG,
    seed: int | None = None,
    limit: int | None = None,
    tags: Sequence[str] = (),
) -> tuple[Path, dict[str, Any]]:
    """Create a randomized campaign and return its state path and document."""
    catalog = load_catalog(catalog_path)
    candidates = [
        scenario
        for scenario in catalog["scenarios"]
        if _matches_tags(scenario, tags)
    ]
    if not candidates:
        raise CampaignError("No scenarios match the requested tags.")
    campaign_seed = seed if seed is not None else secrets.randbits(63)
    rng = random.Random(campaign_seed)
    selected_count = len(candidates) if limit is None else max(1, min(limit, len(candidates)))
    selected = _weighted_sample_without_replacement(candidates, selected_count, rng)
    campaign_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    campaign_dir = root / campaign_id
    campaign_dir.mkdir(parents=True, exist_ok=False)
    catalog_copy = campaign_dir / "catalog.json"
    _write_json_atomic(catalog_copy, catalog)
    state_path = campaign_dir / "campaign.json"
    state: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "created_at": _utc_now(),
        "status": "active",
        "seed": campaign_seed,
        "catalog_sha256": catalog_digest(catalog),
        "catalog_path": catalog_copy.name,
        "claim_index": 0,
        "requested_tags": list(tags),
        "scenarios": [
            {
                "scenario_id": scenario["id"],
                "status": "pending",
                "attempts": [],
            }
            for scenario in selected
        ],
    }
    _write_json_atomic(state_path, state)
    return state_path, state


def resolve_campaign_path(path: Path) -> Path:
    """Resolve either a campaign directory or its campaign.json path."""
    candidate = path / "campaign.json" if path.is_dir() else path
    if not candidate.exists():
        raise CampaignError(f"Campaign does not exist: {candidate}")
    return candidate


def load_campaign(path: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    """Load campaign state and its pinned catalog copy."""
    state_path = resolve_campaign_path(path)
    state = _read_json(state_path)
    if state.get("schema_version") != SCHEMA_VERSION:
        raise CampaignError("Unsupported campaign schema.")
    catalog_path = state_path.parent / str(state.get("catalog_path", "catalog.json"))
    catalog = load_catalog(catalog_path)
    if catalog_digest(catalog) != state.get("catalog_sha256"):
        raise CampaignError("Campaign catalog digest does not match its pinned catalog.")
    return state_path, state, catalog


def _scenario_map(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {scenario["id"]: scenario for scenario in catalog["scenarios"]}


def _surface_map(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {surface["id"]: surface for surface in catalog["surfaces"]}


def _validate_surface_ids(surface_ids: Sequence[str], catalog: dict[str, Any]) -> list[str]:
    known = set(_surface_map(catalog))
    unique: list[str] = []
    seen: set[str] = set()
    for raw_surface_id in surface_ids:
        surface_id = str(raw_surface_id).strip()
        if surface_id not in known:
            raise CampaignError(f"Unknown visited surface id: {surface_id!r}.")
        if surface_id not in seen:
            seen.add(surface_id)
            unique.append(surface_id)
    return sorted(unique)


def _release_expired_claim(
    entry: dict[str, Any],
    scenario: dict[str, Any],
) -> bool:
    missing_lease = not entry.get("lease_id")
    try:
        started = datetime.fromisoformat(str(entry["started_at"]))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        age_seconds = (
            datetime.now(timezone.utc) - started.astimezone(timezone.utc)
        ).total_seconds()
    except (KeyError, TypeError, ValueError):
        age_seconds = float("inf")
    lease_seconds = (int(scenario.get("max_minutes", 15)) + 15) * 60
    if not missing_lease and age_seconds <= lease_seconds:
        return False
    notes = (
        "Automatically released invalid exploration lease."
        if missing_lease
        else "Automatically released expired exploration lease."
    )
    entry["attempts"].append({
        "lease_id": entry.get("lease_id"),
        "started_at": entry.get("started_at"),
        "finished_at": _utc_now(),
        "outcome": "aborted",
        "run_dir": None,
        "run_id": None,
        "run_stopped": None,
        "finding_count": 0,
        "finding_ids": [],
        "finding_surfaces": [],
        "findings": [],
        "visited_surface_ids": [],
        "notes": notes,
    })
    entry["status"] = "pending"
    entry.pop("lease_id", None)
    entry.pop("started_at", None)
    return True


def claim_next_scenario(
    path: Path,
    *,
    tags: Sequence[str] = (),
) -> dict[str, Any]:
    """Claim one pending scenario using weighted stochastic selection."""
    state_path = resolve_campaign_path(path)
    with _campaign_lock(state_path):
        _, state, catalog = load_campaign(state_path)
        scenarios = _scenario_map(catalog)
        released_expired = False
        for entry in state["scenarios"]:
            if entry["status"] == "in_progress":
                released_expired = (
                    _release_expired_claim(entry, scenarios[entry["scenario_id"]])
                    or released_expired
                )
        active = [
            entry
            for entry in state["scenarios"]
            if entry["status"] == "in_progress"
        ]
        if active:
            raise CampaignError(
                f"Scenario {active[0]['scenario_id']} is already in progress.",
            )
        candidates = [
            entry
            for entry in state["scenarios"]
            if entry["status"] == "pending"
            and _matches_tags(scenarios[entry["scenario_id"]], tags)
        ]
        if not candidates:
            if released_expired:
                _write_campaign_state(state_path, state)
            raise CampaignError("No pending scenarios match the requested tags.")
        claim_index = int(state.get("claim_index", 0))
        rng = random.Random(f"{state['seed']}:{claim_index}")
        chosen = rng.choices(
            candidates,
            weights=[
                int(scenarios[entry["scenario_id"]].get("weight", 1))
                for entry in candidates
            ],
            k=1,
        )[0]
        lease_id = uuid.uuid4().hex
        chosen.update({
            "status": "in_progress",
            "lease_id": lease_id,
            "started_at": _utc_now(),
        })
        state["claim_index"] = claim_index + 1
        _write_campaign_state(state_path, state)
        return {
            "campaign_id": state["campaign_id"],
            "lease_id": lease_id,
            "scenario": scenarios[chosen["scenario_id"]],
        }


def active_claim(path: Path) -> dict[str, Any] | None:
    """Return the current claimed scenario, if any."""
    state_path = resolve_campaign_path(path)
    with _campaign_lock(state_path):
        _, state, catalog = load_campaign(path)
        scenarios = _scenario_map(catalog)
        changed = False
        for entry in state["scenarios"]:
            if entry["status"] != "in_progress":
                continue
            if _release_expired_claim(entry, scenarios[entry["scenario_id"]]):
                changed = True
                continue
            lease_id = entry.get("lease_id")
            if not lease_id:
                if _release_expired_claim(entry, scenarios[entry["scenario_id"]]):
                    changed = True
                continue
            if changed:
                _write_campaign_state(state_path, state)
            return {
                "campaign_id": state["campaign_id"],
                "lease_id": lease_id,
                "scenario": scenarios[entry["scenario_id"]],
            }
        if changed:
            _write_campaign_state(state_path, state)
    return None


def _validate_run_evidence(
    run_dir: Path | None,
    *,
    state: dict[str, Any],
    scenario_id: str,
    require_stopped: bool,
) -> dict[str, Any] | None:
    if run_dir is None:
        return None
    if not run_dir.is_dir():
        raise CampaignError(f"Run evidence directory does not exist: {run_dir}")

    manifest = _read_json(run_dir / "manifest.json")
    summary = _read_json(run_dir / "summary.json")
    trace = _read_jsonl(run_dir / "trace.jsonl", label="run trace")
    if not trace:
        raise CampaignError(f"Run trace is empty: {run_dir / 'trace.jsonl'}")
    actions = {str(item.get("action", "")) for item in trace}
    if not {"start", "finish"}.issubset(actions):
        raise CampaignError("Run trace must include both start and finish actions.")
    for label, document in (("manifest", manifest), ("summary", summary)):
        if document.get("campaign_id") != state["campaign_id"]:
            raise CampaignError(
                f"Run {label} campaign_id does not match campaign {state['campaign_id']}.",
            )
        if document.get("scenario_id") != scenario_id:
            raise CampaignError(
                f"Run {label} scenario_id does not match scenario {scenario_id}.",
            )
    manifest_run_id = manifest.get("run_id")
    summary_run_id = summary.get("run_id")
    if manifest_run_id != summary_run_id:
        raise CampaignError("Run manifest and summary have different run_id values.")
    if not isinstance(summary_run_id, str) or not summary_run_id.strip():
        raise CampaignError("Run evidence must include a non-empty run_id.")
    stopped = summary.get("stopped")
    if require_stopped and stopped is not True:
        raise CampaignError("Run summary must report stopped=true before completion.")
    return {
        "run_id": str(summary_run_id or ""),
        "stopped": stopped,
        "manifest": manifest,
        "summary": summary,
        "screenshots": _trace_screenshots(trace, run_dir),
    }


def _trace_screenshots(trace: list[dict[str, Any]], run_dir: Path) -> list[str]:
    screenshots: list[str] = []
    seen: set[str] = set()
    for event in trace:
        windows = event.get("windows", [])
        if not isinstance(windows, list):
            continue
        for window in windows:
            if not isinstance(window, dict):
                continue
            relative_path = window.get("relative_path")
            if not isinstance(relative_path, str) or not relative_path:
                continue
            screenshot_path = Path(relative_path)
            if not screenshot_path.is_absolute():
                screenshot_path = run_dir / screenshot_path
            screenshot = str(screenshot_path)
            if screenshot not in seen:
                seen.add(screenshot)
                screenshots.append(screenshot)
    return screenshots


def _finding_screenshots(finding: dict[str, Any], run_dir: Path) -> list[str]:
    screenshots: list[str] = []
    evidence = finding.get("evidence", [])
    if not isinstance(evidence, list):
        return screenshots
    for item in evidence:
        if not isinstance(item, dict):
            continue
        screenshot = item.get("screenshot")
        if not isinstance(screenshot, str) or not screenshot:
            continue
        screenshot_path = Path(screenshot)
        if not screenshot_path.is_absolute():
            screenshot_path = run_dir / screenshot_path
        screenshots.append(str(screenshot_path))
    return screenshots


def _read_findings(
    run_dir: Path | None,
    *,
    catalog: dict[str, Any],
    required: bool,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    if run_dir is None:
        if required:
            raise CampaignError("A findings outcome requires a run directory.")
        return [], [], []
    path = run_dir / "findings.jsonl"
    if not path.exists():
        if required:
            raise CampaignError(f"A findings outcome requires {path}.")
        return [], [], []
    known_surfaces = set(_surface_map(catalog))
    finding_ids: set[str] = set()
    surfaces: set[str] = set()
    findings: list[dict[str, Any]] = []
    for line_number, finding in enumerate(_read_jsonl(path, label="finding"), start=1):
        finding_id = finding.get("finding_id")
        if not isinstance(finding_id, str) or not finding_id.strip():
            raise CampaignError(f"Finding line {line_number} in {path} has no finding_id.")
        if finding_id in finding_ids:
            continue
        surface_id = finding.get("surface_id")
        if not isinstance(surface_id, str) or surface_id not in known_surfaces:
            raise CampaignError(
                f"Finding {finding_id} references unknown surface_id {surface_id!r}.",
            )
        finding_ids.add(finding_id)
        surfaces.add(surface_id)
        findings.append({
            "finding_id": finding_id,
            "surface_id": surface_id,
            "severity": str(finding.get("severity", "")),
            "title": str(finding.get("title", "")),
            "screenshots": _finding_screenshots(finding, run_dir),
        })
    if required and not findings:
        raise CampaignError(f"A findings outcome requires at least one finding in {path}.")
    return sorted(finding_ids), sorted(surfaces), findings


def complete_scenario(
    path: Path,
    *,
    scenario_id: str,
    lease_id: str,
    outcome: str,
    run_dir: Path | None = None,
    visited_surface_ids: Sequence[str] = (),
    notes: str = "",
) -> dict[str, Any]:
    """Complete or release a claimed scenario and persist its run outcome."""
    if outcome not in OUTCOMES:
        raise CampaignError(f"Unsupported outcome {outcome!r}.")
    state_path = resolve_campaign_path(path)
    with _campaign_lock(state_path):
        _, state, catalog = load_campaign(state_path)
        scenarios = _scenario_map(catalog)
        entry = next(
            (
                item
                for item in state["scenarios"]
                if item["scenario_id"] == scenario_id
            ),
            None,
        )
        if entry is None:
            raise CampaignError(f"Scenario {scenario_id} is not in this campaign.")
        if entry.get("status") != "in_progress":
            raise CampaignError(f"Scenario {scenario_id} is not in progress.")
        if entry.get("lease_id") != lease_id:
            raise CampaignError(f"Lease mismatch for scenario {scenario_id}.")
        if run_dir is None and visited_surface_ids:
            raise CampaignError("Visited surface coverage requires a run directory with GUI evidence.")
        visited_surfaces = _validate_surface_ids(visited_surface_ids, catalog)
        evidence = _validate_run_evidence(
            run_dir,
            state=state,
            scenario_id=scenario_id,
            require_stopped=outcome in {"passed", "findings"},
        )
        if outcome in {"passed", "findings"} and evidence is None:
            raise CampaignError(f"Outcome {outcome!r} requires a run directory with GUI evidence.")
        finding_ids, finding_surfaces, findings = _read_findings(
            run_dir,
            catalog=catalog,
            required=outcome == "findings",
        )
        entry["attempts"].append({
            "lease_id": lease_id,
            "started_at": entry.get("started_at"),
            "finished_at": _utc_now(),
            "outcome": outcome,
            "run_dir": str(run_dir.resolve()) if run_dir else None,
            "run_id": evidence["run_id"] if evidence else None,
            "run_stopped": evidence["stopped"] if evidence else None,
            "run_screenshots": evidence["screenshots"] if evidence else [],
            "finding_count": len(finding_ids),
            "finding_ids": finding_ids,
            "finding_surfaces": finding_surfaces,
            "findings": findings,
            "visited_surface_ids": visited_surfaces,
            "notes": notes,
        })
        if outcome == "blocked":
            entry["status"] = "blocked"
        elif outcome == "aborted":
            entry["status"] = "pending"
        else:
            entry["status"] = "done"
        entry.pop("lease_id", None)
        entry.pop("started_at", None)
        _write_campaign_state(state_path, state)
        return {
            "campaign_id": state["campaign_id"],
            "scenario": scenarios[scenario_id],
            "outcome": outcome,
            "visited_surface_ids": visited_surfaces,
            "finding_count": len(finding_ids),
            "status": state["status"],
        }


def summarize_campaign(path: Path) -> dict[str, Any]:
    """Aggregate progress, covered surfaces, and finding counts."""
    _, state, catalog = load_campaign(path)
    scenarios = _scenario_map(catalog)
    surfaces = _surface_map(catalog)
    counts = Counter(entry["status"] for entry in state["scenarios"])
    for status in ("pending", "in_progress", "done", "blocked"):
        counts.setdefault(status, 0)
    visited_surfaces: set[str] = set()
    finding_surfaces: set[str] = set()
    finding_ids: set[str] = set()
    findings: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    pending_scenarios: list[dict[str, Any]] = []
    blocked_scenarios: list[dict[str, Any]] = []
    for entry in state["scenarios"]:
        scenario = scenarios[entry["scenario_id"]]
        if entry["status"] == "pending":
            pending_scenarios.append({"id": scenario["id"], "title": scenario["title"]})
        if entry["status"] == "blocked":
            latest_attempt = entry.get("attempts", [{}])[-1] if entry.get("attempts") else {}
            blocked_scenarios.append({
                "id": scenario["id"],
                "title": scenario["title"],
                "reason": str(latest_attempt.get("notes", "")),
            })
        for attempt in entry.get("attempts", []):
            if attempt.get("outcome") in {"passed", "findings"}:
                visited_surfaces.update(attempt.get("visited_surface_ids", []))
            if attempt.get("run_dir"):
                runs.append({
                    "scenario_id": scenario["id"],
                    "scenario_title": scenario["title"],
                    "outcome": attempt.get("outcome"),
                    "run_id": attempt.get("run_id"),
                    "run_dir": attempt.get("run_dir"),
                    "stopped": attempt.get("run_stopped"),
                    "screenshots": list(attempt.get("run_screenshots", [])),
                    "visited_surface_ids": list(attempt.get("visited_surface_ids", [])),
                })
            for finding in attempt.get("findings", []):
                finding_id = str(finding.get("finding_id", ""))
                if not finding_id or finding_id in finding_ids:
                    continue
                finding_ids.add(finding_id)
                finding_surfaces.add(str(finding.get("surface_id", "")))
                detail = dict(finding)
                detail["scenario_id"] = scenario["id"]
                detail["scenario_title"] = scenario["title"]
                detail["run_dir"] = attempt.get("run_dir")
                findings.append(detail)
    selected_surfaces = {
        surface_id
        for entry in state["scenarios"]
        for surface_id in scenarios[entry["scenario_id"]]["surface_ids"]
    }
    selected_surface_details = [
        {"id": surface_id, "title": str(surfaces[surface_id].get("title", ""))}
        for surface_id in sorted(selected_surfaces)
    ]
    unvisited_surfaces = selected_surfaces - visited_surfaces
    return {
        "campaign_id": state["campaign_id"],
        "status": _campaign_status(state),
        "scenario_counts": dict(sorted(counts.items())),
        "selected_surface_count": len(selected_surfaces),
        "selected_surfaces": selected_surface_details,
        "covered_surface_count": len(visited_surfaces),
        "visited_surface_count": len(visited_surfaces),
        "visited_surfaces": sorted(visited_surfaces),
        "remaining_surfaces": sorted(unvisited_surfaces),
        "unvisited_surfaces": sorted(unvisited_surfaces),
        "finding_count": len(finding_ids),
        "finding_surfaces": sorted(finding_surfaces),
        "findings": findings,
        "runs": runs,
        "pending_scenarios": pending_scenarios,
        "blocked_scenarios": blocked_scenarios,
    }


def _surface_title(summary: dict[str, Any], surface_id: str) -> str:
    for surface in summary.get("selected_surfaces", []):
        if surface.get("id") == surface_id:
            return str(surface.get("title", surface_id))
    return surface_id


def _markdown_report(summary: dict[str, Any]) -> str:
    lines = [
        "# TradingLab UX exploratory campaign report",
        "",
        f"- Campaign: `{summary['campaign_id']}`",
        f"- Status: **{summary['status']}**",
        f"- Selected catalog surfaces: {summary['selected_surface_count']}",
        f"- Visited surfaces: {summary['visited_surface_count']}",
        f"- Unique findings: {summary['finding_count']}",
        "",
        (
            "This report is based only on UX explorer run manifests, traces, "
            "completion summaries, finding JSONL, and screenshots. The catalog "
            "is a planning aid; it is not an exhaustive model of every possible "
            "TradingLab UI state."
        ),
        "",
        "## Visited surfaces",
    ]
    if summary["visited_surfaces"]:
        for surface_id in summary["visited_surfaces"]:
            lines.append(f"- `{surface_id}` — {_surface_title(summary, surface_id)}")
    else:
        lines.append("- None recorded.")
    lines.extend(["", "## Unvisited selected surfaces"])
    if summary["unvisited_surfaces"]:
        for surface_id in summary["unvisited_surfaces"]:
            lines.append(f"- `{surface_id}` — {_surface_title(summary, surface_id)}")
    else:
        lines.append("- None remaining in the selected campaign scope.")

    lines.extend(["", "## Pending scenarios"])
    if summary["pending_scenarios"]:
        for scenario in summary["pending_scenarios"]:
            lines.append(f"- `{scenario['id']}` — {scenario['title']}")
    else:
        lines.append("- None.")

    lines.extend(["", "## Blocked scenarios"])
    if summary["blocked_scenarios"]:
        for scenario in summary["blocked_scenarios"]:
            reason = scenario.get("reason") or "No reason recorded."
            lines.append(f"- `{scenario['id']}` — {scenario['title']}: {reason}")
    else:
        lines.append("- None.")

    lines.extend(["", "## Runs"])
    if summary["runs"]:
        for run in summary["runs"]:
            visited = ", ".join(run.get("visited_surface_ids", [])) or "none recorded"
            lines.append(
                f"- `{run.get('scenario_id')}` outcome={run.get('outcome')} "
                f"stopped={run.get('stopped')} run_dir=`{run.get('run_dir')}`; "
                f"visited: {visited}"
            )
            screenshots = run.get("screenshots") or []
            if screenshots:
                for screenshot in screenshots:
                    lines.append(f"  - Run screenshot: `{screenshot}`")
            else:
                lines.append("  - Run screenshots: none listed in trace.")
    else:
        lines.append("- No GUI run evidence recorded.")

    lines.extend(["", "## Findings"])
    if summary["findings"]:
        for finding in summary["findings"]:
            title = finding.get("title") or "(untitled)"
            lines.append(
                f"- `{finding.get('finding_id')}` [{finding.get('severity') or 'unspecified'}] "
                f"{title} on `{finding.get('surface_id')}`"
            )
            screenshots = finding.get("screenshots") or []
            if screenshots:
                for screenshot in screenshots:
                    lines.append(f"  - Screenshot: `{screenshot}`")
            else:
                lines.append("  - Screenshot: none recorded in finding evidence.")
    else:
        lines.append("- None recorded.")
    return "\n".join(lines) + "\n"


def _html_report(summary: dict[str, Any]) -> str:
    markdown = _markdown_report(summary)
    body = "\n".join(
        f"<p>{html.escape(line)}</p>" if line else ""
        for line in markdown.splitlines()
    )
    return (
        "<!doctype html>\n"
        "<html><head><meta charset=\"utf-8\"><title>TradingLab UX campaign report"
        "</title></head><body>\n"
        f"{body}\n"
        "</body></html>\n"
    )


def render_campaign_report(path: Path, *, output_format: str = "markdown") -> str:
    """Render a human-readable report from persisted GUI evidence."""
    summary = summarize_campaign(path)
    if output_format == "markdown":
        return _markdown_report(summary)
    if output_format == "html":
        return _html_report(summary)
    raise CampaignError(f"Unsupported report format {output_format!r}.")


def render_agent_prompt(claim: dict[str, Any], *, executable_path: str) -> str:
    """Render a standalone prompt for a screenshot-driven explorer session."""
    scenario = claim["scenario"]
    surfaces = ", ".join(scenario["surface_ids"])
    entries = "; ".join(scenario["entry_points"])
    return f"""You are an exploratory GUI/UX tester for the frozen TradingLab desktop app.

Campaign: {claim["campaign_id"]}
Scenario: {scenario["id"]} - {scenario["title"]}
Time budget: {scenario.get("max_minutes", 15)} minutes
Target surfaces: {surfaces}
Suggested entry points: {entries}

Mission:
{scenario["mission"]}

Hard rules:
- Interact with the application only through tradinglab_ux_start, observe, inspect,
  click, type, key, pointer, scroll, window, record_finding, status, and finish.
- Stay within 400 UX tool actions and 45 minutes unless the coordinator gives a
  smaller budget.
- For a full sweep, start with the default/foreground input mode so the driver uses
  guarded real foreground input. If foreground focus is unavailable, lost, or not
  exactly the selected TradingLab HWND/PID, fail closed and report the scenario
  blocked instead of switching to backend checks or claiming visited coverage.
- Use message input mode only when the coordinator explicitly scopes a limited
  message-mode probe.
- Do not import TradingLab, inspect live Python objects, call internal methods, query
  its data files, or substitute deterministic backend tests for visible GUI behavior.
- Make exploratory choices from screenshots and visible feedback. Vary paths and probe
  nearby controls beyond the happy path.
- Do not capture the desktop; screenshots remain PrintWindow captures of
  TradingLab-owned windows only.
- The extension launches only this checkout's verified dist\\TradingLab\\TradingLab.exe
  with a sanitized system-environment allowlist and an empty isolated profile.
- Modifier key chords are intentionally unavailable: tradinglab_ux_key sends only
  accepted non-chord keys because WM_KEYDOWN does not faithfully set another thread's
  modifier state. Use tradinglab_ux_pointer for pointer move and drag exploration.
- Never enter real credentials. The run profile is isolated, but credentials and
  external side effects remain out of scope.
- Record every credible issue with tradinglab_ux_record_finding before inspecting or
  changing source code. Include exact reproduction steps and retain screenshot evidence.
- Cancel before any action that would affect resources outside the isolated profile.
- Always call tradinglab_ux_finish, even when blocked.

Start with:
tradinglab_ux_start(exe_path={executable_path!r},
                    campaign_id={claim["campaign_id"]!r},
                    scenario_id={scenario["id"]!r},
                    input_mode="foreground")

When finished, report the run directory, findings, blocked areas, and surfaces actually
visited to the coordinating session. The coordinator will complete the scenario with
one --visited-surface flag per genuinely visited catalog surface; do not claim target
surfaces were covered unless screenshots and visible interactions support that claim.
Do not fix code in this exploration session.
"""
