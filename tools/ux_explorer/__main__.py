"""Command-line entry point for TradingLab exploratory GUI campaigns."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .campaign import (
    DEFAULT_CAMPAIGN_ROOT,
    DEFAULT_CATALOG,
    CampaignError,
    active_claim,
    claim_next_scenario,
    complete_scenario,
    create_campaign,
    load_catalog,
    render_agent_prompt,
    render_campaign_report,
    summarize_campaign,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan and track screenshot-driven TradingLab GUI exploration.",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG,
        help="Scenario catalog (default: tools/ux_explorer/scenarios.json).",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("validate", help="Validate the committed surface catalog.")

    init = commands.add_parser("init", help="Create a randomized campaign.")
    init.add_argument("--root", type=Path, default=DEFAULT_CAMPAIGN_ROOT)
    init.add_argument("--seed", type=int)
    init.add_argument("--limit", type=int)
    init.add_argument("--tag", action="append", default=[])

    claim = commands.add_parser("claim", help="Claim one pending scenario.")
    claim.add_argument("campaign", type=Path)
    claim.add_argument("--tag", action="append", default=[])

    prompt = commands.add_parser(
        "prompt",
        help="Render a standalone child-session prompt for the active/new claim.",
    )
    prompt.add_argument("campaign", type=Path)
    prompt.add_argument("--exe", required=True)
    prompt.add_argument("--tag", action="append", default=[])

    complete = commands.add_parser("complete", help="Complete the active scenario.")
    complete.add_argument("campaign", type=Path)
    complete.add_argument("--scenario", required=True)
    complete.add_argument("--lease", required=True)
    complete.add_argument(
        "--outcome",
        required=True,
        choices=["passed", "findings", "blocked", "aborted"],
    )
    complete.add_argument("--run-dir", type=Path)
    complete.add_argument(
        "--visited-surface",
        action="append",
        default=[],
        dest="visited_surface_ids",
        help="Catalog surface id actually visited during the GUI run. Repeatable.",
    )
    complete.add_argument("--notes", default="")

    summary = commands.add_parser("summary", help="Summarize campaign progress.")
    summary.add_argument("campaign", type=Path)

    report = commands.add_parser("report", help="Render a human-readable campaign report.")
    report.add_argument("campaign", type=Path)
    report.add_argument("--format", choices=["markdown", "html"], default="markdown")
    report.add_argument("--output", type=Path)
    return parser


def _print_json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            catalog = load_catalog(args.catalog)
            _print_json({
                "schema_version": catalog["schema_version"],
                "surface_count": len(catalog["surfaces"]),
                "scenario_count": len(catalog["scenarios"]),
            })
        elif args.command == "init":
            path, state = create_campaign(
                args.root,
                catalog_path=args.catalog,
                seed=args.seed,
                limit=args.limit,
                tags=args.tag,
            )
            _print_json({"campaign_path": str(path.resolve()), **state})
        elif args.command == "claim":
            _print_json(claim_next_scenario(args.campaign, tags=args.tag))
        elif args.command == "prompt":
            claim = active_claim(args.campaign)
            if claim is None:
                claim = claim_next_scenario(args.campaign, tags=args.tag)
            print(render_agent_prompt(claim, executable_path=args.exe))
        elif args.command == "complete":
            _print_json(complete_scenario(
                args.campaign,
                scenario_id=args.scenario,
                lease_id=args.lease,
                outcome=args.outcome,
                run_dir=args.run_dir,
                visited_surface_ids=args.visited_surface_ids,
                notes=args.notes,
            ))
        elif args.command == "summary":
            _print_json(summarize_campaign(args.campaign))
        elif args.command == "report":
            report = render_campaign_report(args.campaign, output_format=args.format)
            if args.output:
                args.output.write_text(report, encoding="utf-8")
            else:
                print(report, end="")
        return 0
    except CampaignError as exc:
        print(f"ux_explorer: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
