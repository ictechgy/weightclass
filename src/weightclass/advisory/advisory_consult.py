#!/usr/bin/env python3
"""Run one non-recording read-only advisory executor for managed consult."""

from __future__ import annotations

import argparse
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from weightclass import __version__

from . import advisory_evidence_contract, advisory_routes, speculative_run

RUNNER_VERSION_CHANGED_EXIT = 78


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wclass-advisory internal-consult", allow_abbrev=False)
    parser.add_argument("--expected-package-version", required=True)
    parser.add_argument(
        "--workflow",
        required=True,
        choices=tuple(sorted(advisory_evidence_contract.EVIDENCE_WORKFLOWS)),
    )
    parser.add_argument("--vendor", required=True)
    parser.add_argument("--role", required=True, choices=("cheap", "expensive"))
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--task-file", required=True, type=Path)
    parser.add_argument("--route-profile", required=True, type=Path)
    parser.add_argument("--expected-route-sha256", required=True)
    parser.add_argument("--verify", required=True, type=Path)
    arguments = parser.parse_args(argv)
    if arguments.expected_package_version != __version__:
        return RUNNER_VERSION_CHANGED_EXIT

    repo = arguments.repo.resolve()
    task_file = arguments.task_file
    verify = arguments.verify.resolve()
    try:
        profile = advisory_routes.load_profile(arguments.route_profile)
        routes = advisory_routes.build_routes(
            profile,
            read_only_executors=True,
            evidence_workflow=arguments.workflow,
        )
        if (
            advisory_routes.evidence_routes_digest(profile, routes, arguments.workflow)
            != arguments.expected_route_sha256
        ):
            return 2
        command = list(getattr(routes, arguments.role))
        task = speculative_run.read_task_file(task_file, require_private=True)
        task = advisory_evidence_contract.build_evidence_prompt(task, arguments.workflow)
        commit = speculative_run.head_commit(repo)
    except (
        OSError,
        advisory_routes.AdvisoryRouteError,
        advisory_evidence_contract.EvidenceResultError,
        speculative_run.RunFailure,
        speculative_run.TaskInputError,
    ):
        return 2

    with tempfile.TemporaryDirectory(prefix="wclass-consult-") as directory:
        out_dir = Path(directory)
        registry = out_dir / "workspaces.txt"
        record, _, payload = speculative_run.attempt(
            arguments.role,
            command,
            repo,
            commit,
            task,
            verify,
            out_dir,
            registry,
            scaffolding=frozenset(speculative_run.AGENT_SCAFFOLDING),
            rates=None,
            allowed_env=speculative_run.default_child_env(command[0]),
            child_home=None,
            prefer_prices=False,
            workflow=arguments.workflow,
            vendor=arguments.vendor,
        )
        if record.get("accepted") is not True:
            return 1
        try:
            rendered = payload.decode("utf-8", errors="strict")
            advisory_evidence_contract.parse_evidence_result(rendered, arguments.workflow)
        except (UnicodeError, advisory_evidence_contract.EvidenceResultError):
            return 1
        try:
            sys.stdout.write(rendered)
            sys.stdout.flush()
        except (OSError, ValueError):
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
