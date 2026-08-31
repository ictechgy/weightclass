#!/usr/bin/env python3
"""Installed entry point for explicit, opt-in advisory campaign workflows."""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def _load_module(name: str) -> Any:
    """Load one advisory command family only when that command is selected."""
    if __package__:
        return importlib.import_module(f"{__package__}.{name}")
    module_directory = str(Path(__file__).resolve().parent)
    if module_directory not in sys.path:
        sys.path.insert(0, module_directory)
    return importlib.import_module(name)


def _option_value(arguments: Sequence[str], name: str) -> str | None:
    values: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == name:
            if index + 1 >= len(arguments):
                raise ValueError
            values.append(arguments[index + 1])
            index += 2
            continue
        if argument.startswith(f"{name}="):
            values.append(argument.split("=", 1)[1])
        index += 1
    if len(values) > 1 or (values and not values[0]):
        raise ValueError
    return values[0] if values else None


def _set_option(arguments: Sequence[str], name: str, value: str) -> list[str]:
    result: list[str] = []
    replaced = False
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == name:
            if index + 1 >= len(arguments) or replaced:
                raise ValueError
            result.extend((name, value))
            replaced = True
            index += 2
            continue
        if argument.startswith(f"{name}="):
            if replaced:
                raise ValueError
            result.extend((name, value))
            replaced = True
            index += 1
            continue
        result.append(argument)
        index += 1
    if not replaced:
        result.extend((name, value))
    return result


def _run_parser(command: str = "run") -> argparse.ArgumentParser:
    if command not in {"run", "prune"}:
        raise ValueError()
    managed_command = "dispatch" if command == "run" else "cleanup"
    parser = argparse.ArgumentParser(
        prog=f"wclass-advisory {command}",
        description=(
            "Advanced explicit-campaign wrapper. Options not owned by this wrapper are "
            "forwarded unchanged to the sealed runner."
        ),
        epilog=(
            "Managed onboarding users should use "
            f"`wclass-advisory {managed_command}` instead.\n\n"
            "Forwarded runner options include:\n"
            "  --repo, --task-file, --route-profile, --campaign, --verify\n"
            "  --advise-first, --advise-on-failure, --advisor-context\n"
            "  --prices, --prefer-prices, --confirm-task-egress\n"
            "  --cheap-home, --advisor-home, --expensive-home, --exclude-dir"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    parser.add_argument(
        "--campaign-root",
        required=True,
        type=Path,
        help="owner-only output root for one advisory campaign",
    )
    parser.add_argument(
        "--vendor",
        default="campaign",
        help="task-free vendor key used only for anonymous lane allocation",
    )
    parser.add_argument(
        "--workflow",
        choices=("implementation", "review", "research", "diagnosis", "design"),
        default="implementation",
        help="advisory workflow; it is also part of lane allocation",
    )
    return parser


def _call(module_main: object, arguments: Sequence[str]) -> int:
    """Call one lazily loaded argv entry point and validate its exit status."""

    if not callable(module_main):
        raise ValueError()
    result = module_main(arguments)
    if isinstance(result, bool) or not isinstance(result, int):
        raise ValueError()
    return result


def _top_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wclass-advisory", description=__doc__, allow_abbrev=False
    )
    parser.add_argument(
        "command",
        choices=(
            "init",
            "migrate-evidence",
            "migrate-routes",
            "migrate-gate",
            "doctor",
            "cli-check",
            "provider-check",
            "review",
            "consult",
            "dispatch",
            "status",
            "campaign-gate",
            "cleanup",
            "run",
            "prune",
            "seal",
            "report",
            "portfolio",
            "experiment",
            "install-skill",
        ),
    )
    return parser


def _run(arguments: Sequence[str], *, prune: bool) -> int:
    parser = _run_parser("prune" if prune else "run")
    parsed, forwarded = parser.parse_known_args(arguments)
    if any(argument == "--out-dir" or argument.startswith("--out-dir=") for argument in forwarded):
        parser.error("--out-dir is controlled by --campaign-root")
    if not prune and "--confirm-task-egress" not in forwarded:
        parser.error("advisory execution requires --confirm-task-egress")
    campaign_root = parsed.campaign_root.expanduser().resolve()
    runner_arguments = [*forwarded, "--workflow", parsed.workflow, "--vendor", parsed.vendor]
    if prune:
        runner_arguments.append("--prune")
    try:
        campaign_value = _option_value(forwarded, "--campaign")
        campaign_path = (
            Path(campaign_value).expanduser().resolve() if campaign_value is not None else None
        )
    except (OSError, ValueError):
        parser.error("invalid campaign option")
    import json

    advisory_campaign = _load_module("advisory_campaign")
    advisory_orchestration = _load_module("advisory_orchestration")
    speculative_run = _load_module("speculative_run")
    if prune:
        return _call(
            speculative_run.main,
            [*runner_arguments, "--out-dir", str(campaign_root)],
        )
    request = advisory_orchestration.LaneRequest(
        parsed.vendor,
        campaign_root,
        workflow=parsed.workflow,
        campaign_path=campaign_path,
    )
    try:
        with advisory_orchestration.acquire_campaign_lanes((request,)) as leases:
            if campaign_path is not None:
                try:
                    manifest = advisory_campaign.load_manifest(campaign_path)
                    records = advisory_campaign.load_bound_records(
                        leases[0].results_dir / "runs.jsonl"
                    )
                    ordinals = advisory_campaign.validate_record_bindings(manifest, records)
                except advisory_campaign.CampaignError as error:
                    raise advisory_orchestration.CampaignRecordsInvalidError(error) from None
                runner_arguments = _set_option(
                    runner_arguments,
                    "--sample-ordinal",
                    str(len(ordinals) + 1),
                )
            return _call(
                speculative_run.main,
                [*runner_arguments, "--out-dir", str(leases[0].results_dir)],
            )
    except advisory_orchestration.CampaignLaneError as error:
        print(
            json.dumps({"error": advisory_orchestration.campaign_lane_error_code(error)}),
            file=sys.stderr,
        )
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = _top_parser()
    if not arguments or arguments == ["--help"] or arguments == ["-h"]:
        parser.parse_args(arguments)
        return 0
    command = arguments[0]
    if command == "init":
        managed_cli = _load_module("managed_cli")
        return _call(managed_cli.init_main, arguments[1:])
    if command == "migrate-evidence":
        managed_cli = _load_module("managed_cli")
        return _call(managed_cli.migrate_evidence_main, arguments[1:])
    if command == "migrate-routes":
        managed_cli = _load_module("managed_cli")
        return _call(managed_cli.migrate_routes_main, arguments[1:])
    if command == "migrate-gate":
        managed_cli = _load_module("managed_cli")
        return _call(managed_cli.migrate_gate_main, arguments[1:])
    if command == "doctor":
        managed_cli = _load_module("managed_cli")
        return _call(managed_cli.doctor_main, arguments[1:])
    if command == "cli-check":
        managed_cli = _load_module("managed_cli")
        return _call(managed_cli.cli_check_main, arguments[1:])
    if command == "provider-check":
        managed_cli = _load_module("managed_cli")
        return _call(managed_cli.provider_check_main, arguments[1:])
    if command == "review":
        if not any(
            argument == "--profile" or argument.startswith("--profile=")
            for argument in arguments[1:]
        ):
            managed_cli = _load_module("managed_cli")
            return _call(
                managed_cli.review_main,
                [argument for argument in arguments[1:] if argument != "--managed"],
            )
        advisory_routes = _load_module("advisory_routes")
        return _call(advisory_routes.main, arguments)
    if command == "consult":
        managed_cli = _load_module("managed_cli")
        return _call(managed_cli.consult_main, arguments[1:])
    if command == "dispatch":
        managed_cli = _load_module("managed_cli")
        return _call(managed_cli.dispatch_main, arguments[1:])
    if command == "status":
        managed_cli = _load_module("managed_cli")
        return _call(managed_cli.status_main, arguments[1:])
    if command == "campaign-gate":
        managed_cli = _load_module("managed_cli")
        return _call(managed_cli.campaign_gate_main, arguments[1:])
    if command == "cleanup":
        managed_cli = _load_module("managed_cli")
        return _call(managed_cli.prune_main, arguments[1:])
    if command == "run":
        return _run(arguments[1:], prune=False)
    if command == "prune":
        return _run(arguments[1:], prune=True)
    if command == "seal":
        advisory_campaign = _load_module("advisory_campaign")
        return _call(advisory_campaign.main, arguments[1:])
    if command == "report":
        speculative_report = _load_module("speculative_report")
        return _call(speculative_report.main, arguments[1:])
    if command == "portfolio":
        advisory_portfolio = _load_module("advisory_portfolio")
        return _call(advisory_portfolio.main, arguments[1:])
    if command == "experiment":
        advisory_experiments = _load_module("advisory_experiments")
        return _call(advisory_experiments.main, arguments[1:])
    if command == "install-skill":
        install_advisory_skill = _load_module("install_advisory_skill")
        return _call(install_advisory_skill.main, arguments[1:])
    parser.error("invalid command")


if __name__ == "__main__":
    raise SystemExit(main())
