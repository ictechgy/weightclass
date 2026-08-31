#!/usr/bin/env python3
"""Stateless one-shot read-only advisory execution."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

if not __package__:  # pragma: no cover - packaged direct-script boundary
    source_root = str(Path(__file__).resolve().parents[2])
    if source_root not in sys.path:
        sys.path.insert(0, source_root)

from weightclass.executable_observation import observe_executable
from weightclass.process_context import has_safe_child_status_context
from weightclass.v2_validation import V2ValidationError

if TYPE_CHECKING or __package__:
    from . import (
        advisory_evidence_contract,
        advisory_preflight,
        advisory_routes,
        readonly_snapshot,
        speculative_run,
    )
else:  # pragma: no cover - retained for the packaged direct-script boundary
    import advisory_evidence_contract  # type: ignore[import-not-found,no-redef]
    import advisory_preflight  # type: ignore[import-not-found,no-redef]
    import advisory_routes  # type: ignore[import-not-found,no-redef]
    import readonly_snapshot  # type: ignore[import-not-found,no-redef]
    import speculative_run  # type: ignore[import-not-found,no-redef]

MAX_STANDARD_INPUT_BYTES = speculative_run.MAX_TASK_FILE_BYTES
WORKFLOWS = ("review", "research", "diagnosis", "design")
VENDORS = ("codex", "claude", "agy", "grok")
ROLES = ("cheap", "expensive")


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        super().error("invalid arguments")


class QuickAdvisoryError(RuntimeError):
    """A value-free one-shot failure with a stable public code."""

    def __init__(self, code: str) -> None:
        super().__init__()
        self.code = code


_NEXT_ACTIONS = {
    "ask_confirmation_required": "Run in a terminal or add --confirm-task-egress.",
    "ask_execution_cancelled": "No vendor process was started.",
    "ask_cli_unavailable": "Install or update the selected vendor CLI, then run ask again.",
    "ask_process_context_unsafe": "Run from a process that owns child exit status, then retry.",
    "ask_invalid_input": "Check --help and provide a non-empty UTF-8 task on standard input.",
    "ask_repository_unavailable": "Choose an existing local repository directory.",
    "ask_repository_unsupported": "Use a smaller stable local directory and try again.",
    "ask_repository_changed": "Restore the repository changes before trusting the result.",
    "ask_executor_failed": "Check the selected vendor CLI locally and retry.",
    "ask_result_invalid": "Retry once; the vendor did not return the required closed JSON result.",
}


def _emit_error(code: str, *, human: bool) -> int:
    next_action = _NEXT_ACTIONS[code]
    if human:
        print(f"Advisory failed: {code}", file=sys.stderr)
        print(f"Next: {next_action}", file=sys.stderr)
    else:
        print(
            json.dumps(
                {"error": code, "next_action": next_action},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
    return 1 if code == "ask_execution_cancelled" else 2


def _human_output_requested(arguments: argparse.Namespace) -> bool:
    if arguments.json:
        return False
    if arguments.human:
        return True
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def _read_task_from_standard_input() -> str:
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    try:
        payload = stream.read(MAX_STANDARD_INPUT_BYTES + 1)
        if isinstance(payload, str):
            encoded = payload.encode("utf-8", errors="strict")
        else:
            encoded = bytes(payload)
        if not encoded or len(encoded) > MAX_STANDARD_INPUT_BYTES:
            raise QuickAdvisoryError("ask_invalid_input")
        task = encoded.decode("utf-8", errors="strict")
    except QuickAdvisoryError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError):
        raise QuickAdvisoryError("ask_invalid_input") from None
    if not task.strip():
        raise QuickAdvisoryError("ask_invalid_input")
    return task


def _resolve_executable(vendor: str, repo: Path) -> tuple[str, object]:
    selected = shutil.which(vendor)
    if selected is None:
        raise QuickAdvisoryError("ask_cli_unavailable")
    try:
        executable = Path(selected).resolve(strict=True)
        if not executable.is_absolute() or executable.is_relative_to(repo):
            raise QuickAdvisoryError("ask_cli_unavailable")
        before = observe_executable(os.fspath(executable))
        capability = advisory_preflight.check_local_capability(
            vendor,
            os.fspath(executable),
        )
        observation = observe_executable(os.fspath(executable))
        if not capability.ready or observation != before:
            raise QuickAdvisoryError("ask_cli_unavailable")
    except QuickAdvisoryError:
        raise
    except (OSError, V2ValidationError):
        raise QuickAdvisoryError("ask_cli_unavailable") from None
    return os.fspath(executable), observation


def _confirm_task_egress(*, vendor: str, workflow: str, delivery: str, confirmed: bool) -> None:
    if confirmed:
        return
    try:
        with open(os.ctermid(), "r+", encoding="utf-8", buffering=1) as console:
            print("One-shot advisory", file=console)
            print(f"  Vendor: {vendor} (configured default model)", file=console)
            print(f"  Workflow: {workflow}", file=console)
            print("  Requested repository access: read-only", file=console)
            print(f"  Task delivery: {delivery}", file=console)
            print("  Quality verification: no", file=console)
            console.write("Send the task to this vendor? [y/N] ")
            answer = console.readline(32)
    except (OSError, UnicodeError):
        raise QuickAdvisoryError("ask_confirmation_required") from None
    if answer.strip().lower() not in {"y", "yes"}:
        raise QuickAdvisoryError("ask_execution_cancelled")


def _render_human(receipt: Mapping[str, object]) -> None:
    print("Advisory result (untrusted model-authored content)")
    print(f"Vendor: {receipt['vendor']} (default model; quality not verified)")
    print(f"Workflow: {receipt['workflow']}")
    print()
    print(json.dumps(receipt["result"], ensure_ascii=True, indent=2))


def ask(
    *,
    vendor: str,
    workflow: str,
    role: str,
    repo: Path,
    timeout_seconds: float,
    confirm_task_egress: bool,
) -> dict[str, object]:
    """Run exactly one read-only vendor child without managed state."""

    if (
        vendor not in VENDORS
        or workflow not in WORKFLOWS
        or role not in ROLES
        or not 1 <= timeout_seconds <= 28_800
    ):
        raise QuickAdvisoryError("ask_invalid_input")
    if not has_safe_child_status_context():
        raise QuickAdvisoryError("ask_process_context_unsafe")
    try:
        resolved_repo = repo.expanduser().resolve(strict=True)
        if not resolved_repo.is_dir():
            raise QuickAdvisoryError("ask_repository_unavailable")
    except QuickAdvisoryError:
        raise
    except (OSError, ValueError):
        raise QuickAdvisoryError("ask_repository_unavailable") from None
    try:
        command = advisory_routes.build_default_evidence_route(vendor, workflow)
    except advisory_routes.AdvisoryRouteError:
        raise QuickAdvisoryError("ask_invalid_input") from None
    try:
        delivery = advisory_routes.command_task_delivery(command)
        executable, observation = _resolve_executable(vendor, resolved_repo)
        command = (executable, *command[1:])
        _confirm_task_egress(
            vendor=vendor,
            workflow=workflow,
            delivery=delivery,
            confirmed=confirm_task_egress,
        )
        baseline = readonly_snapshot.snapshot_tree(resolved_repo)
    except QuickAdvisoryError:
        raise
    except (OSError, ValueError, readonly_snapshot.SnapshotError):
        raise QuickAdvisoryError("ask_repository_unsupported") from None

    task = _read_task_from_standard_input()
    execution_failed = False
    child: speculative_run.ChildResult | None = None
    stdout = ""
    try:
        if observe_executable(executable) != observation:
            raise QuickAdvisoryError("ask_cli_unavailable")
        prompt = advisory_evidence_contract.build_evidence_prompt(task, workflow)
        child, stdout = speculative_run.run_child(
            list(command),
            resolved_repo,
            prompt,
            allowed_env=speculative_run.default_child_env(executable),
            timeout_seconds=timeout_seconds,
        )
    except QuickAdvisoryError:
        raise
    except (OSError, ValueError, speculative_run.RunFailure):
        execution_failed = True

    try:
        comparison = readonly_snapshot.compare_tree(
            resolved_repo,
            baseline,
            speculative_run.AGENT_SCAFFOLDING,
        )
    except (OSError, ValueError, readonly_snapshot.SnapshotError):
        raise QuickAdvisoryError("ask_repository_changed") from None
    if comparison.changed:
        raise QuickAdvisoryError("ask_repository_changed")
    if execution_failed or child is None or child["timed_out"] or child["exit_code"] != 0:
        raise QuickAdvisoryError("ask_executor_failed")
    try:
        _raw_result, result = speculative_run.extract_evidence_result(
            stdout,
            list(command),
            workflow,
        )
    except advisory_evidence_contract.EvidenceResultError:
        raise QuickAdvisoryError("ask_result_invalid") from None
    return {
        "schema_version": 1,
        "event": "advisory_ask",
        "vendor": vendor,
        "workflow": workflow,
        "role": role,
        "model_selection": "vendor_default",
        "task_egress_confirmed": True,
        "task_delivery": delivery,
        "repository_access": "read_only",
        "host_filesystem_confined": False,
        "worktree_unchanged": True,
        "git_metadata_checked": False,
        "sample_recorded": False,
        "campaign_state_read": False,
        "project_verifier_used": False,
        "quality_verified": False,
        "content_trust": "untrusted_model_authored",
        "result": result,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = _ArgumentParser(
        prog="wclass-advisory ask",
        description=(
            "Run one stateless, read-only advisory with the selected CLI's default model."
        ),
        allow_abbrev=False,
    )
    parser.add_argument("--vendor", required=True, choices=VENDORS)
    parser.add_argument("--workflow", choices=WORKFLOWS, default="review")
    parser.add_argument("--role", choices=ROLES, default="cheap")
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument(
        "--timeout",
        "--timeout-seconds",
        dest="timeout_seconds",
        type=float,
        default=speculative_run.CHILD_TIMEOUT,
    )
    parser.add_argument("--confirm-task-egress", action="store_true")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true")
    output.add_argument("--human", action="store_true")
    arguments = parser.parse_args(argv)
    human = _human_output_requested(arguments)
    try:
        receipt = ask(
            vendor=arguments.vendor,
            workflow=arguments.workflow,
            role=arguments.role,
            repo=arguments.repo,
            timeout_seconds=arguments.timeout_seconds,
            confirm_task_egress=arguments.confirm_task_egress,
        )
    except QuickAdvisoryError as error:
        return _emit_error(error.code, human=human)
    if human:
        _render_human(receipt)
    else:
        print(json.dumps(receipt, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
