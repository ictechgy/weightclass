#!/usr/bin/env python3
"""Run one non-recording read-only advisory executor for managed consult."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from weightclass import __version__

from . import advisory_evidence_contract, advisory_routes, speculative_run
from .advisory_diagnostics import (
    CHILD_FAILURE_CODES,
    CONSULT_FAILURE_CODES,
    CONSULT_FAILURE_STAGES,
    RESULT_SHAPES,
)

RUNNER_VERSION_CHANGED_EXIT = 78


def _safe_exit_code(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _safe_enum(value: object, allowed: frozenset[str], fallback: str) -> str:
    return value if isinstance(value, str) and value in allowed else fallback


def _attempt_failure(record: Mapping[str, object]) -> tuple[str, str]:
    stage = _safe_enum(record.get("failure_stage"), CONSULT_FAILURE_STAGES, "unknown")
    reason = {
        "execution": "route_execution_failed",
        "result": "route_result_rejected",
        "handover": "route_handover_rejected",
        "verification": "verification_rejected",
        "verification_integrity": "verification_integrity_rejected",
        "acceptance": "acceptance_rejected",
    }.get(stage, "internal_failure")
    return stage, reason


def _emit_failure(
    stage: str,
    reason_code: str,
    record: Mapping[str, object] | None = None,
) -> None:
    selected_stage = _safe_enum(stage, CONSULT_FAILURE_STAGES, "unknown")
    selected_reason = _safe_enum(reason_code, CONSULT_FAILURE_CODES, "internal_failure")
    attempt = record if isinstance(record, Mapping) else {}
    child = attempt.get("child")
    child_record = child if isinstance(child, Mapping) else {}
    verify = attempt.get("verify")
    verify_record = verify if isinstance(verify, Mapping) else {}
    payload = {
        "schema_version": 1,
        "event": "advisory_consult_failed",
        "failure_stage": selected_stage,
        "reason_code": selected_reason,
        "child_exit_code": _safe_exit_code(child_record.get("exit_code")),
        "child_timed_out": child_record.get("timed_out") is True,
        "child_failure_code": _safe_enum(
            child_record.get("failure_code"), CHILD_FAILURE_CODES, "unknown"
        ),
        "result_shape": _safe_enum(record.get("result_shape"), RESULT_SHAPES, "unknown")
        if record is not None
        else "unknown",
        "verify_exit_code": _safe_exit_code(verify_record.get("exit_code")),
        "verify_timed_out": verify_record.get("timed_out") is True,
    }
    try:
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")), file=sys.stderr)
    except (OSError, ValueError):
        pass


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
    parser.add_argument("--confirm-task-egress", action="store_true")
    arguments = parser.parse_args(argv)
    if not arguments.confirm_task_egress:
        print(
            json.dumps({"error": "advisory_consult_task_egress_confirmation_required"}),
            file=sys.stderr,
        )
        return 2
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
    except (OSError, advisory_routes.AdvisoryRouteError):
        _emit_failure("configuration", "route_profile_rejected")
        return 2
    if (
        advisory_routes.evidence_routes_digest(profile, routes, arguments.workflow)
        != arguments.expected_route_sha256
    ):
        _emit_failure("configuration", "route_digest_mismatch")
        return 2
    command = list(getattr(routes, arguments.role))
    try:
        task = speculative_run.read_task_file(task_file, require_private=True)
    except (OSError, speculative_run.TaskInputError):
        _emit_failure("task_input", "task_input_rejected")
        return 2
    try:
        task = advisory_evidence_contract.build_evidence_prompt(task, arguments.workflow)
    except advisory_evidence_contract.EvidenceResultError:
        _emit_failure("task_input", "task_prompt_rejected")
        return 2
    try:
        commit = speculative_run.head_commit(repo)
    except (OSError, speculative_run.RunFailure):
        _emit_failure("repository", "repository_rejected")
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
            stage, reason_code = _attempt_failure(record)
            _emit_failure(stage, reason_code, record)
            return 1
        try:
            rendered = payload.decode("utf-8", errors="strict")
            advisory_evidence_contract.parse_evidence_result(rendered, arguments.workflow)
        except (UnicodeError, advisory_evidence_contract.EvidenceResultError):
            _emit_failure("result", "route_result_rejected", record)
            return 1
        try:
            sys.stdout.write(rendered)
            sys.stdout.flush()
        except (OSError, ValueError):
            _emit_failure("output", "output_failed", record)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
