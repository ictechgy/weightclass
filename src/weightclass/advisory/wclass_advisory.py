#!/usr/bin/env python3
"""Installed entry point for explicit, opt-in advisory campaign workflows."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING or __package__:
    from . import (
        advisory_campaign,
        advisory_orchestration,
        advisory_portfolio,
        advisory_routes,
        install_advisory_skill,
        speculative_report,
        speculative_run,
    )
else:
    module_directory = str(Path(__file__).resolve().parent)
    if module_directory not in sys.path:
        sys.path.insert(0, module_directory)
    import advisory_campaign  # type: ignore[import-not-found]
    import advisory_orchestration  # type: ignore[import-not-found]
    import advisory_portfolio  # type: ignore[import-not-found]
    import advisory_routes  # type: ignore[import-not-found]
    import install_advisory_skill  # type: ignore[import-not-found]
    import speculative_report  # type: ignore[import-not-found]
    import speculative_run  # type: ignore[import-not-found]


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


def _run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wclass-advisory run", allow_abbrev=False)
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


def _invoke(module_main: object, arguments: Sequence[str], program: str) -> int:
    if not callable(module_main):
        raise ValueError()
    original_argv = sys.argv
    try:
        sys.argv = [program, *arguments]
        return int(module_main())
    finally:
        sys.argv = original_argv


def _top_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wclass-advisory", description=__doc__, allow_abbrev=False
    )
    parser.add_argument(
        "command",
        choices=("review", "run", "prune", "seal", "report", "portfolio", "install-skill"),
    )
    return parser


def _run(arguments: Sequence[str], *, prune: bool) -> int:
    parser = _run_parser()
    parsed, forwarded = parser.parse_known_args(arguments)
    if any(argument == "--out-dir" or argument.startswith("--out-dir=") for argument in forwarded):
        parser.error("--out-dir is controlled by --campaign-root")
    campaign_root = parsed.campaign_root.expanduser().resolve()
    runner_arguments = [*forwarded, "--workflow", parsed.workflow]
    if prune:
        runner_arguments.append("--prune")
    try:
        campaign_value = _option_value(forwarded, "--campaign")
        campaign_path = (
            Path(campaign_value).expanduser().resolve() if campaign_value is not None else None
        )
    except (OSError, ValueError):
        parser.error("invalid campaign option")
    if prune:
        return _invoke(
            speculative_run.main,
            [*runner_arguments, "--out-dir", str(campaign_root)],
            "wclass-advisory run",
        )
    request = advisory_orchestration.LaneRequest(
        parsed.vendor,
        campaign_root,
        workflow=parsed.workflow,
        campaign_path=campaign_path,
    )
    with advisory_orchestration.acquire_campaign_lanes((request,)) as leases:
        if campaign_path is not None:
            manifest = advisory_campaign.load_manifest(campaign_path)
            records = advisory_campaign.load_bound_records(leases[0].results_dir / "runs.jsonl")
            ordinals = advisory_campaign.validate_record_bindings(manifest, records)
            runner_arguments = _set_option(
                runner_arguments,
                "--sample-ordinal",
                str(len(ordinals) + 1),
            )
        return _invoke(
            speculative_run.main,
            [*runner_arguments, "--out-dir", str(leases[0].results_dir)],
            "wclass-advisory run",
        )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = _top_parser()
    if not arguments or arguments == ["--help"] or arguments == ["-h"]:
        parser.parse_args(arguments)
        return 0
    command = arguments[0]
    if command == "review":
        return _invoke(advisory_routes.main, arguments, "wclass-advisory")
    if command == "run":
        return _run(arguments[1:], prune=False)
    if command == "prune":
        return _run(arguments[1:], prune=True)
    if command == "seal":
        return _invoke(advisory_campaign.main, arguments[1:], "wclass-advisory seal")
    if command == "report":
        return _invoke(speculative_report.main, arguments[1:], "wclass-advisory report")
    if command == "portfolio":
        return _invoke(advisory_portfolio.main, arguments[1:], "wclass-advisory portfolio")
    if command == "install-skill":
        return install_advisory_skill.main(list(arguments[1:]))
    parser.error("invalid command")


if __name__ == "__main__":
    raise SystemExit(main())
