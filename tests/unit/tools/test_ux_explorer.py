"""Contract tests for the stochastic TradingLab UX campaign harness."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.ux_explorer.__main__ import main
from tools.ux_explorer.campaign import (
    DEFAULT_CATALOG,
    CampaignError,
    _campaign_lock,
    active_claim,
    claim_next_scenario,
    complete_scenario,
    create_campaign,
    load_catalog,
    render_agent_prompt,
    render_campaign_report,
    summarize_campaign,
)


def _write_run_evidence(
    run_dir: Path,
    *,
    campaign_id: str,
    scenario_id: str,
    run_id: str = "run-1",
    stopped: bool = True,
    findings: list[dict[str, object]] | None = None,
) -> None:
    run_dir.mkdir()
    (run_dir / "screenshots").mkdir()
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "campaign_id": campaign_id,
        "scenario_id": scenario_id,
    }
    summary = {
        "schema_version": 1,
        "run_id": run_id,
        "campaign_id": campaign_id,
        "scenario_id": scenario_id,
        "stopped": stopped,
    }
    trace = [
        {"timestamp": "2026-09-06T00:00:00+00:00", "action": "start"},
        {
            "timestamp": "2026-09-06T00:00:30+00:00",
            "action": "observe",
            "windows": [{"relative_path": "screenshots\\0001\\window-01.png"}],
        },
        {"timestamp": "2026-09-06T00:01:00+00:00", "action": "finish"},
    ]
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (run_dir / "trace.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in trace),
        encoding="utf-8",
    )
    if findings is not None:
        (run_dir / "findings.jsonl").write_text(
            "".join(json.dumps(item) + "\n" for item in findings),
            encoding="utf-8",
        )


def test_committed_catalog_covers_every_registered_surface() -> None:
    catalog = load_catalog()
    surface_ids = {surface["id"] for surface in catalog["surfaces"]}
    referenced = {
        surface_id
        for scenario in catalog["scenarios"]
        for surface_id in scenario["surface_ids"]
    }
    assert len(surface_ids) >= 50
    assert len(catalog["scenarios"]) >= 20
    assert referenced == surface_ids


def test_create_claim_complete_and_summarize(tmp_path: Path) -> None:
    campaign_path, state = create_campaign(
        tmp_path,
        catalog_path=DEFAULT_CATALOG,
        seed=1234,
        limit=3,
    )
    assert campaign_path.exists()
    assert state["seed"] == 1234
    assert len(state["scenarios"]) == 3
    assert (campaign_path.parent / "catalog.json").exists()

    claim = claim_next_scenario(campaign_path)
    assert active_claim(campaign_path) == claim
    with pytest.raises(CampaignError, match="already in progress"):
        claim_next_scenario(campaign_path)

    run_dir = tmp_path / "run"
    screenshot = run_dir / "screenshots" / "window-01.png"
    finding = {
        "finding_id": "finding-1",
        "surface_id": claim["scenario"]["surface_ids"][0],
        "severity": "minor",
        "title": "Synthetic UX finding",
        "evidence": [{"screenshot": "screenshots\\window-01.png"}],
    }
    _write_run_evidence(
        run_dir,
        campaign_id=claim["campaign_id"],
        scenario_id=claim["scenario"]["id"],
        findings=[finding],
    )
    screenshot.write_bytes(b"fake-png")
    result = complete_scenario(
        campaign_path,
        scenario_id=claim["scenario"]["id"],
        lease_id=claim["lease_id"],
        outcome="findings",
        run_dir=run_dir,
        visited_surface_ids=[finding["surface_id"]],
    )
    assert result["finding_count"] == 1
    assert result["visited_surface_ids"] == [finding["surface_id"]]
    summary = summarize_campaign(campaign_path)
    assert summary["scenario_counts"]["done"] == 1
    assert summary["finding_count"] == 1
    assert finding["surface_id"] in summary["finding_surfaces"]
    assert summary["visited_surfaces"] == [finding["surface_id"]]
    assert set(summary["unvisited_surfaces"]).isdisjoint(summary["visited_surfaces"])


def test_aborted_claim_returns_to_pending(tmp_path: Path) -> None:
    campaign_path, _ = create_campaign(
        tmp_path,
        catalog_path=DEFAULT_CATALOG,
        seed=5,
        limit=1,
    )
    claim = claim_next_scenario(campaign_path)
    complete_scenario(
        campaign_path,
        scenario_id=claim["scenario"]["id"],
        lease_id=claim["lease_id"],
        outcome="aborted",
    )
    next_claim = claim_next_scenario(campaign_path)
    assert next_claim["scenario"]["id"] == claim["scenario"]["id"]
    assert next_claim["lease_id"] != claim["lease_id"]


def test_agent_prompt_requires_visible_gui_tools_only(tmp_path: Path) -> None:
    campaign_path, _ = create_campaign(
        tmp_path,
        catalog_path=DEFAULT_CATALOG,
        seed=9,
        limit=1,
    )
    claim = claim_next_scenario(campaign_path)
    prompt = render_agent_prompt(
        claim,
        executable_path=r"C:\build\TradingLab\TradingLab.exe",
    )
    assert "tradinglab_ux_start" in prompt
    assert "tradinglab_ux_pointer" in prompt
    assert 'input_mode="foreground"' in prompt
    assert "400 UX tool actions and 45 minutes" in prompt
    assert "fail closed" in prompt
    assert "message input mode" in prompt
    assert "Modifier key chords are intentionally unavailable" in prompt
    assert "Do not capture the desktop" in prompt
    assert "Do not import TradingLab" in prompt
    assert "record_finding" in prompt
    assert "Do not fix code in this exploration session" in prompt


def test_tag_filter_selects_only_matching_scenarios(tmp_path: Path) -> None:
    campaign_path, state = create_campaign(
        tmp_path,
        catalog_path=DEFAULT_CATALOG,
        seed=2,
        tags=["manual-strategy"],
    )
    catalog = load_catalog(campaign_path.parent / "catalog.json")
    scenario_by_id = {scenario["id"]: scenario for scenario in catalog["scenarios"]}
    selected = [scenario_by_id[item["scenario_id"]] for item in state["scenarios"]]
    assert selected
    assert all("manual-strategy" in scenario["tags"] for scenario in selected)


def test_campaign_lock_is_os_backed_and_persistent(tmp_path: Path) -> None:
    campaign_path, _ = create_campaign(
        tmp_path,
        catalog_path=DEFAULT_CATALOG,
        seed=3,
        limit=1,
    )
    lock_path = campaign_path.with_suffix(campaign_path.suffix + ".lock")
    with _campaign_lock(campaign_path):
        assert lock_path.exists()
        with pytest.raises(CampaignError, match="already being updated"):
            claim_next_scenario(campaign_path)
    claim_next_scenario(campaign_path)
    assert lock_path.exists()


def test_persistent_lock_file_does_not_block_after_release(tmp_path: Path) -> None:
    campaign_path, _ = create_campaign(
        tmp_path,
        catalog_path=DEFAULT_CATALOG,
        seed=4,
        limit=1,
    )
    lock_path = campaign_path.with_suffix(campaign_path.suffix + ".lock")
    lock_path.write_text(json.dumps({"pid": 1}), encoding="utf-8")
    claim = claim_next_scenario(campaign_path)
    assert claim["scenario"]["id"]


def test_expired_scenario_lease_is_reclaimed(tmp_path: Path) -> None:
    campaign_path, _ = create_campaign(
        tmp_path,
        catalog_path=DEFAULT_CATALOG,
        seed=11,
        limit=1,
    )
    first = claim_next_scenario(campaign_path)
    state = json.loads(campaign_path.read_text(encoding="utf-8"))
    state["scenarios"][0]["started_at"] = "2000-01-01T00:00:00+00:00"
    campaign_path.write_text(json.dumps(state), encoding="utf-8")

    second = claim_next_scenario(campaign_path)
    assert second["scenario"]["id"] == first["scenario"]["id"]
    assert second["lease_id"] != first["lease_id"]
    updated = json.loads(campaign_path.read_text(encoding="utf-8"))
    assert updated["scenarios"][0]["attempts"][0]["outcome"] == "aborted"


def test_active_claim_releases_expired_lease_for_prompt(tmp_path: Path) -> None:
    campaign_path, _ = create_campaign(
        tmp_path,
        catalog_path=DEFAULT_CATALOG,
        seed=12,
        limit=1,
    )
    first = claim_next_scenario(campaign_path)
    state = json.loads(campaign_path.read_text(encoding="utf-8"))
    state["scenarios"][0]["started_at"] = "2000-01-01T00:00:00+00:00"
    campaign_path.write_text(json.dumps(state), encoding="utf-8")

    assert active_claim(campaign_path) is None
    second = claim_next_scenario(campaign_path)
    assert second["scenario"]["id"] == first["scenario"]["id"]
    assert second["lease_id"] != first["lease_id"]


def test_malformed_active_lease_is_not_reused(tmp_path: Path) -> None:
    campaign_path, _ = create_campaign(
        tmp_path,
        catalog_path=DEFAULT_CATALOG,
        seed=23,
        limit=1,
    )
    first = claim_next_scenario(campaign_path)
    state = json.loads(campaign_path.read_text(encoding="utf-8"))
    state["scenarios"][0].pop("lease_id")
    campaign_path.write_text(json.dumps(state), encoding="utf-8")

    assert active_claim(campaign_path) is None
    second = claim_next_scenario(campaign_path)
    assert second["scenario"]["id"] == first["scenario"]["id"]


def test_passed_outcome_requires_stopped_gui_run_evidence(tmp_path: Path) -> None:
    campaign_path, _ = create_campaign(tmp_path, catalog_path=DEFAULT_CATALOG, seed=13, limit=1)
    claim = claim_next_scenario(campaign_path)
    with pytest.raises(CampaignError, match="requires a run directory"):
        complete_scenario(
            campaign_path,
            scenario_id=claim["scenario"]["id"],
            lease_id=claim["lease_id"],
            outcome="passed",
        )

    run_dir = tmp_path / "run"
    _write_run_evidence(
        run_dir,
        campaign_id=claim["campaign_id"],
        scenario_id=claim["scenario"]["id"],
        stopped=False,
    )
    with pytest.raises(CampaignError, match="stopped=true"):
        complete_scenario(
            campaign_path,
            scenario_id=claim["scenario"]["id"],
            lease_id=claim["lease_id"],
            outcome="passed",
            run_dir=run_dir,
        )


def test_run_evidence_identity_must_match_campaign_and_scenario(tmp_path: Path) -> None:
    campaign_path, _ = create_campaign(tmp_path, catalog_path=DEFAULT_CATALOG, seed=14, limit=1)
    claim = claim_next_scenario(campaign_path)
    run_dir = tmp_path / "run"
    _write_run_evidence(run_dir, campaign_id="other-campaign", scenario_id=claim["scenario"]["id"])

    with pytest.raises(CampaignError, match="campaign_id does not match"):
        complete_scenario(
            campaign_path,
            scenario_id=claim["scenario"]["id"],
            lease_id=claim["lease_id"],
            outcome="passed",
            run_dir=run_dir,
        )


def test_unknown_visited_surface_is_rejected(tmp_path: Path) -> None:
    campaign_path, _ = create_campaign(tmp_path, catalog_path=DEFAULT_CATALOG, seed=15, limit=1)
    claim = claim_next_scenario(campaign_path)
    run_dir = tmp_path / "run"
    _write_run_evidence(
        run_dir,
        campaign_id=claim["campaign_id"],
        scenario_id=claim["scenario"]["id"],
    )

    with pytest.raises(CampaignError, match="Unknown visited surface"):
        complete_scenario(
            campaign_path,
            scenario_id=claim["scenario"]["id"],
            lease_id=claim["lease_id"],
            outcome="passed",
            run_dir=run_dir,
            visited_surface_ids=["TL.UNKNOWN"],
        )


def test_completed_scenario_does_not_mark_assigned_surfaces_visited(tmp_path: Path) -> None:
    campaign_path, _ = create_campaign(tmp_path, catalog_path=DEFAULT_CATALOG, seed=16, limit=1)
    claim = claim_next_scenario(campaign_path)
    run_dir = tmp_path / "run"
    _write_run_evidence(
        run_dir,
        campaign_id=claim["campaign_id"],
        scenario_id=claim["scenario"]["id"],
    )

    complete_scenario(
        campaign_path,
        scenario_id=claim["scenario"]["id"],
        lease_id=claim["lease_id"],
        outcome="passed",
        run_dir=run_dir,
    )
    summary = summarize_campaign(campaign_path)
    assert summary["visited_surface_count"] == 0
    assert set(summary["unvisited_surfaces"]) == set(claim["scenario"]["surface_ids"])


def test_findings_jsonl_is_required_and_corrupt_lines_raise(tmp_path: Path) -> None:
    campaign_path, _ = create_campaign(tmp_path, catalog_path=DEFAULT_CATALOG, seed=17, limit=1)
    claim = claim_next_scenario(campaign_path)
    run_dir = tmp_path / "run"
    _write_run_evidence(
        run_dir,
        campaign_id=claim["campaign_id"],
        scenario_id=claim["scenario"]["id"],
    )
    with pytest.raises(CampaignError, match="requires .*findings.jsonl"):
        complete_scenario(
            campaign_path,
            scenario_id=claim["scenario"]["id"],
            lease_id=claim["lease_id"],
            outcome="findings",
            run_dir=run_dir,
        )
    (run_dir / "findings.jsonl").write_text("{not-json}\n", encoding="utf-8")
    with pytest.raises(CampaignError, match="Could not parse finding JSONL line 1"):
        complete_scenario(
            campaign_path,
            scenario_id=claim["scenario"]["id"],
            lease_id=claim["lease_id"],
            outcome="findings",
            run_dir=run_dir,
        )


def test_finding_ids_are_deduplicated_across_attempts(tmp_path: Path) -> None:
    campaign_path, _ = create_campaign(tmp_path, catalog_path=DEFAULT_CATALOG, seed=18, limit=2)
    first = claim_next_scenario(campaign_path)
    surface_id = first["scenario"]["surface_ids"][0]
    finding = {"finding_id": "duplicate", "surface_id": surface_id, "title": "Same issue"}
    _write_run_evidence(
        tmp_path / "run-1",
        campaign_id=first["campaign_id"],
        scenario_id=first["scenario"]["id"],
        run_id="run-1",
        findings=[finding],
    )
    complete_scenario(
        campaign_path,
        scenario_id=first["scenario"]["id"],
        lease_id=first["lease_id"],
        outcome="findings",
        run_dir=tmp_path / "run-1",
        visited_surface_ids=[surface_id],
    )

    second = claim_next_scenario(campaign_path)
    second_surface = second["scenario"]["surface_ids"][0]
    _write_run_evidence(
        tmp_path / "run-2",
        campaign_id=second["campaign_id"],
        scenario_id=second["scenario"]["id"],
        run_id="run-2",
        findings=[{**finding, "surface_id": second_surface}],
    )
    complete_scenario(
        campaign_path,
        scenario_id=second["scenario"]["id"],
        lease_id=second["lease_id"],
        outcome="findings",
        run_dir=tmp_path / "run-2",
        visited_surface_ids=[second_surface],
    )

    summary = summarize_campaign(campaign_path)
    assert summary["finding_count"] == 1


def test_blocked_campaign_status_and_report_keep_gaps_visible(tmp_path: Path) -> None:
    campaign_path, _ = create_campaign(tmp_path, catalog_path=DEFAULT_CATALOG, seed=19, limit=2)
    claim = claim_next_scenario(campaign_path)
    complete_scenario(
        campaign_path,
        scenario_id=claim["scenario"]["id"],
        lease_id=claim["lease_id"],
        outcome="blocked",
        notes="Dialog could not be reached with GUI tools.",
    )

    summary = summarize_campaign(campaign_path)
    assert summary["status"] == "blocked"
    assert summary["visited_surface_count"] == 0
    assert summary["scenario_counts"]["pending"] == 1
    assert summary["blocked_scenarios"][0]["reason"] == "Dialog could not be reached with GUI tools."
    report = render_campaign_report(campaign_path)
    assert "Unvisited selected surfaces" in report
    assert "Dialog could not be reached with GUI tools." in report


def test_report_cli_prints_human_readable_markdown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    campaign_path, _ = create_campaign(tmp_path, catalog_path=DEFAULT_CATALOG, seed=20, limit=1)
    assert main(["report", str(campaign_path)]) == 0
    output = capsys.readouterr().out
    assert "# TradingLab UX exploratory campaign report" in output
    assert "not an exhaustive model of every possible TradingLab UI state" in output


def test_complete_cli_accepts_repeated_visited_surface_flags(tmp_path: Path) -> None:
    campaign_path, _ = create_campaign(tmp_path, catalog_path=DEFAULT_CATALOG, seed=21, limit=1)
    claim = claim_next_scenario(campaign_path)
    surfaces = claim["scenario"]["surface_ids"][:2]
    run_dir = tmp_path / "run"
    _write_run_evidence(
        run_dir,
        campaign_id=claim["campaign_id"],
        scenario_id=claim["scenario"]["id"],
    )

    argv = [
        "complete",
        str(campaign_path),
        "--scenario",
        claim["scenario"]["id"],
        "--lease",
        claim["lease_id"],
        "--outcome",
        "passed",
        "--run-dir",
        str(run_dir),
    ]
    for surface_id in surfaces:
        argv.extend(["--visited-surface", surface_id])
    assert main(argv) == 0

    summary = summarize_campaign(campaign_path)
    assert summary["visited_surfaces"] == sorted(surfaces)


def test_blocked_without_run_evidence_cannot_claim_visited_surfaces(tmp_path: Path) -> None:
    campaign_path, _ = create_campaign(tmp_path, catalog_path=DEFAULT_CATALOG, seed=22, limit=1)
    claim = claim_next_scenario(campaign_path)

    with pytest.raises(CampaignError, match="coverage requires a run directory"):
        complete_scenario(
            campaign_path,
            scenario_id=claim["scenario"]["id"],
            lease_id=claim["lease_id"],
            outcome="blocked",
            visited_surface_ids=[claim["scenario"]["surface_ids"][0]],
        )


def test_blocked_keyboard_scenario_does_not_count_assigned_surfaces_visited(tmp_path: Path) -> None:
    campaign_path, _ = create_campaign(
        tmp_path,
        catalog_path=DEFAULT_CATALOG,
        seed=24,
        limit=1,
        tags=["keyboard"],
    )
    claim = claim_next_scenario(campaign_path, tags=["keyboard"])
    run_dir = tmp_path / "run"
    _write_run_evidence(
        run_dir,
        campaign_id=claim["campaign_id"],
        scenario_id=claim["scenario"]["id"],
    )
    complete_scenario(
        campaign_path,
        scenario_id=claim["scenario"]["id"],
        lease_id=claim["lease_id"],
        outcome="blocked",
        run_dir=run_dir,
        visited_surface_ids=claim["scenario"]["surface_ids"],
        notes="Modifier chords are unavailable in the native driver.",
    )

    summary = summarize_campaign(campaign_path)
    assert "keyboard" in claim["scenario"]["tags"]
    assert summary["visited_surface_count"] == 0
    assert set(claim["scenario"]["surface_ids"]).issubset(summary["unvisited_surfaces"])
