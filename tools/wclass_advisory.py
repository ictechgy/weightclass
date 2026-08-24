#!/usr/bin/env python3
"""Repository-owned entry point for the opt-in advisory campaign runner.

The local shim supplies the two roots explicitly and forwards all campaign
arguments to ``speculative_run.py``.  This file deliberately does not parse,
store, or replay task content; campaign locking, ordinal validation, egress
confirmation, and transient result replay remain in the repository runner.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType


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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--router-root",
        required=True,
        type=Path,
        help="repository root containing the reviewed advisory tools",
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


def _load_runner(router_root: Path) -> ModuleType:
    tools_root = router_root.expanduser().resolve() / "tools"
    runner_path = tools_root / "speculative_run.py"
    if not runner_path.is_file():
        raise ValueError()
    if str(tools_root) not in sys.path:
        sys.path.insert(0, str(tools_root))
    spec = importlib.util.spec_from_file_location("wclass_advisory_runner", runner_path)
    if spec is None or spec.loader is None:
        raise ValueError()
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_orchestration(router_root: Path) -> ModuleType:
    tools_root = router_root.expanduser().resolve() / "tools"
    orchestration_path = tools_root / "advisory_orchestration.py"
    if not orchestration_path.is_file():
        raise ValueError()
    if str(tools_root) not in sys.path:
        sys.path.insert(0, str(tools_root))
    spec = importlib.util.spec_from_file_location(
        "wclass_advisory_orchestration", orchestration_path
    )
    if spec is None or spec.loader is None:
        raise ValueError()
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments, forwarded = parser.parse_known_args(argv)
    if any(argument == "--out-dir" or argument.startswith("--out-dir=") for argument in forwarded):
        parser.error("--out-dir is controlled by --campaign-root")
    try:
        runner = _load_runner(arguments.router_root)
        orchestration = _load_orchestration(arguments.router_root)
    except (OSError, ValueError):
        parser.error("invalid router root")
    campaign_root = arguments.campaign_root.expanduser().resolve()
    runner_arguments = [*forwarded, "--workflow", arguments.workflow]
    try:
        campaign_value = _option_value(forwarded, "--campaign")
        campaign_path = (
            Path(campaign_value).expanduser().resolve() if campaign_value is not None else None
        )
    except (OSError, ValueError):
        parser.error("invalid campaign option")
    original_argv = sys.argv
    try:
        if "--prune" in forwarded:
            sys.argv = [
                str(arguments.router_root / "tools" / "speculative_run.py"),
                *runner_arguments,
                "--out-dir",
                str(campaign_root),
            ]
            return int(runner.main())
        request = orchestration.LaneRequest(
            arguments.vendor,
            campaign_root,
            workflow=arguments.workflow,
            campaign_path=campaign_path,
        )
        with orchestration.acquire_campaign_lanes((request,)) as leases:
            if campaign_path is not None:
                campaign = importlib.import_module("advisory_campaign")
                manifest = campaign.load_manifest(campaign_path)
                records = campaign.load_bound_records(leases[0].results_dir / "runs.jsonl")
                ordinals = campaign.validate_record_bindings(manifest, records)
                runner_arguments = _set_option(
                    runner_arguments,
                    "--sample-ordinal",
                    str(len(ordinals) + 1),
                )
            sys.argv = [
                str(arguments.router_root / "tools" / "speculative_run.py"),
                *runner_arguments,
                "--out-dir",
                str(leases[0].results_dir),
            ]
            return int(runner.main())
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    raise SystemExit(main())
