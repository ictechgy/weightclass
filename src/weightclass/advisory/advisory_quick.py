#!/usr/bin/env python3
"""Stateless one-shot read-only advisory execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
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
        advisory_context,
        advisory_evidence_contract,
        advisory_preflight,
        advisory_routes,
        advisory_triage,
        readonly_snapshot,
        speculative_run,
    )
else:  # pragma: no cover - retained for the packaged direct-script boundary
    import advisory_context  # type: ignore[import-not-found,no-redef]
    import advisory_evidence_contract  # type: ignore[import-not-found,no-redef]
    import advisory_preflight  # type: ignore[import-not-found,no-redef]
    import advisory_routes  # type: ignore[import-not-found,no-redef]
    import advisory_triage  # type: ignore[import-not-found,no-redef]
    import readonly_snapshot  # type: ignore[import-not-found,no-redef]
    import speculative_run  # type: ignore[import-not-found,no-redef]

MAX_STANDARD_INPUT_BYTES = speculative_run.MAX_TASK_FILE_BYTES
WORKFLOWS = ("review", "research", "diagnosis", "design")
VENDORS = ("codex", "claude", "agy", "grok")
ROLES = ("cheap", "expensive")
STAGES = ("manual", "plan", "pivot", "final")
MAX_COUNCIL_MEMBERS = 4
RECEIPT_SCHEMA_VERSION = 2
# 사람이 읽는 검토 출력에서 한 인자를 그대로 찍는 상한. 넘으면 길이와
# 지문으로 줄인다. 값은 `--json` 에 그대로 남는다.
_MAX_RENDERED_ARGUMENT = 120
_YES_NO = {True: "yes", False: "no"}
# 셸 세션 하나 동안만 사는 송신 동의. 디스크에는 아무것도 쓰지 않는다.
_SESSION_EGRESS_ENVIRONMENT = "WCLASS_ADVISORY_EGRESS"
_SESSION_EGRESS_VALUE = "session"
PARTIAL_COUNCIL_EXIT_CODE = 3


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
    "ask_confirmation_required": (
        "Run in a terminal, add --confirm-task-egress, or export "
        "WCLASS_ADVISORY_EGRESS=session for this shell."
    ),
    "ask_execution_cancelled": "No vendor process was started.",
    "ask_cli_unavailable": "Install or update every selected vendor CLI, then run ask again.",
    "ask_process_context_unsafe": "Run from a process that owns child exit status, then retry.",
    "ask_invalid_input": "Check --help and provide a non-empty UTF-8 task on standard input.",
    "ask_repository_unavailable": "Choose an existing local repository directory.",
    "ask_repository_unsupported": "Use a smaller stable local directory and try again.",
    "ask_repository_changed": "Restore the repository changes before trusting the result.",
    "ask_context_invalid": "Choose task, diff, files, or repo; --file is only for files context.",
    "ask_context_unsupported": "Use bounded UTF-8 files or a supported tracked Git diff.",
    "ask_council_invalid": "Choose two to four distinct supported vendors.",
    "ask_council_deadline": "Increase the council deadline or reduce its members and scope.",
    "ask_executor_failed": "Check the selected vendor CLI locally and retry.",
    "ask_executor_timeout": "Increase the bounded timeout or reduce the advisory scope.",
    "ask_result_invalid": "Retry once; the vendor did not return the required closed JSON result.",
}


def _emit_error(code: str, *, human: bool) -> int:
    next_action = _NEXT_ACTIONS.get(code, _NEXT_ACTIONS["ask_invalid_input"])
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
        encoded = (
            payload.encode("utf-8", errors="strict") if isinstance(payload, str) else bytes(payload)
        )
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


def _resolve_repository(repo: Path) -> Path:
    try:
        resolved = repo.expanduser().resolve(strict=True)
        if not resolved.is_dir():
            raise QuickAdvisoryError("ask_repository_unavailable")
        return resolved
    except QuickAdvisoryError:
        raise
    except (OSError, ValueError):
        raise QuickAdvisoryError("ask_repository_unavailable") from None


def _observe_route_executable(vendor: str, repo: Path) -> tuple[str, object]:
    selected = shutil.which(vendor)
    if selected is None:
        raise QuickAdvisoryError("ask_cli_unavailable")
    try:
        executable = Path(selected).resolve(strict=True)
        if not executable.is_absolute() or executable.is_relative_to(repo):
            raise QuickAdvisoryError("ask_cli_unavailable")
        return os.fspath(executable), observe_executable(os.fspath(executable))
    except QuickAdvisoryError:
        raise
    except (OSError, V2ValidationError):
        raise QuickAdvisoryError("ask_cli_unavailable") from None


def _resolve_executable(vendor: str, repo: Path) -> tuple[str, object]:
    executable, before = _observe_route_executable(vendor, repo)
    try:
        capability = advisory_preflight.check_local_capability(vendor, executable)
        observation = observe_executable(executable)
        if not capability.ready or observation != before:
            raise QuickAdvisoryError("ask_cli_unavailable")
    except QuickAdvisoryError:
        raise
    except (OSError, V2ValidationError):
        raise QuickAdvisoryError("ask_cli_unavailable") from None
    return executable, observation


def _route(vendor: str, workflow: str) -> tuple[tuple[str, ...], str]:
    try:
        command = advisory_routes.build_default_evidence_route(vendor, workflow)
        return command, advisory_routes.command_task_delivery(command)
    except advisory_routes.AdvisoryRouteError:
        raise QuickAdvisoryError("ask_invalid_input") from None


def _preflight_member(vendor: str, workflow: str, repo: Path) -> dict[str, object]:
    command, delivery = _route(vendor, workflow)
    executable, observation = _resolve_executable(vendor, repo)
    return {
        "vendor": vendor,
        "command": (executable, *command[1:]),
        "delivery": delivery,
        "executable": executable,
        "observation": observation,
    }


def session_egress_granted(environment: Mapping[str, str] | None = None) -> bool:
    """이 셸 세션이 이미 태스크 송신에 동의했는가.

    매 호출마다 터미널 확인을 다시 받는 것은 경계를 지키는 대신 사용을
    막는다. 그렇다고 동의를 디스크에 남기면 advisory 상태가 저장소 경로나
    시각을 담게 되는데, `AGENTS.md` 가 그 둘을 금지한다. 그래서 동의를
    **셸 세션의 수명**에만 둔다 — 아무것도 쓰지 않고, 셸이 닫히면 사라진다.

    권한의 크기는 `--confirm-task-egress` 와 정확히 같다. 넓히는 것이 아니라
    같은 승인을 한 번만 말하게 하는 것이다.

    값을 정확히 요구하는 이유: 이 이름이 존재하기만 하면 통과하게 두면,
    비어 있는 값이나 `0` 을 넣어 **끄려던** 사용자가 오히려 켜게 된다.
    """
    source = os.environ if environment is None else environment
    return source.get(_SESSION_EGRESS_ENVIRONMENT) == _SESSION_EGRESS_VALUE


def _confirm_task_egress(
    *,
    members: Sequence[Mapping[str, object]],
    workflow: str,
    stage: str,
    context_mode: str,
    context_file_count: int,
    confirmed: bool,
) -> str:
    """동의를 확인하고 **무엇이 동의했는지** 를 돌려준다.

    출처를 영수증에 남기지 않으면, 세션 승인으로 지나간 호출과 사람이 터미널
    에서 y 를 친 호출이 같은 기록으로 남는다. 둘은 같은 권한이지만 같은
    사건이 아니다.
    """
    if confirmed:
        return "flag"
    if session_egress_granted():
        return "session_environment"
    try:
        ctermid = getattr(os, "ctermid", None)
        if not callable(ctermid):
            raise QuickAdvisoryError("ask_confirmation_required")
        with open(ctermid(), "r+", encoding="utf-8", buffering=1) as console:
            print("One-shot advisory", file=console)
            print(f"  Workflow/stage: {workflow}/{stage}", file=console)
            print(f"  Task-bearing calls: {len(members)} (invocation bound)", file=console)
            for member in members:
                print(
                    f"  Vendor: {member['vendor']} (default model; {member['delivery']} delivery)",
                    file=console,
                )
            if any(member["delivery"] == "argv" for member in members):
                print(
                    "  argv exposure: task and context are visible in local process arguments",
                    file=console,
                )
            detail = f"; {context_file_count} explicit files" if context_file_count else ""
            print(f"  Context sent/accessed: {context_mode}{detail}", file=console)
            if context_mode in {"diff", "files"}:
                print("  Selected repository content may contain secrets.", file=console)
            print("  Requested behavior: read-only; host filesystem confinement: no", file=console)
            print("  Quality verification: no", file=console)
            console.write("Send the task and selected context to these vendors? [y/N] ")
            answer = console.readline(32)
    except QuickAdvisoryError:
        raise
    except (OSError, UnicodeError):
        raise QuickAdvisoryError("ask_confirmation_required") from None
    if answer.strip().lower() not in {"y", "yes"}:
        raise QuickAdvisoryError("ask_execution_cancelled")
    return "terminal"


def _summarize_argument(argument: str) -> str:
    """긴 인자를 길이와 지문으로 줄인다.

    검토용 출력에서 argv 를 보는 이유는 **어떤 플래그가 붙는가** 를 확인하기
    위해서다. 그런데 `--json-schema` 하나가 수천 바이트의 이스케이프된 JSON
    이라, 그대로 찍으면 나머지 플래그가 화면 밖으로 밀린다 — 읽으라고 만든
    출력이 읽을 수 없게 된다.

    값을 지우지는 않는다. 길이와 sha256 앞자리를 남기므로 두 실행의 스키마가
    같은지 비교할 수 있고, 정확한 바이트는 `--json` 에 그대로 있다.
    """
    if len(argument) <= _MAX_RENDERED_ARGUMENT:
        return argument
    digest = hashlib.sha256(argument.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"<{len(argument)} bytes, sha256:{digest}>"


def _render_preview_human(receipt: Mapping[str, object]) -> None:
    """검토 가능한 사실만 사람이 읽는 형태로 찍는다.

    `--preview` 는 태스크를 읽지 않고 벤더도 띄우지 않는다. 그래서 사람이 이
    화면에서 결정하는 것은 하나다: **이 라우트로 내 태스크를 내보낼 것인가.**
    그 결정에 필요한 것은 벤더, 전달 방식, 프로세스 노출, 컨텍스트 범위,
    자식 수와 상한이다. 기계용 전체 영수증은 `--json` 이 계속 낸다.
    """
    print("Advisory egress preview (task-free; no vendor process started)")
    files = receipt.get("context_file_count")
    detail = f" ({files} explicit files)" if isinstance(files, int) and files else ""
    print(f"  Workflow/stage:   {receipt.get('workflow')}/{receipt.get('stage')}")
    print(f"  Context:          {receipt.get('context_mode')}{detail}")
    print(
        f"  Children on run:  {receipt.get('task_bearing_child_bound')} task-bearing, "
        f"{receipt.get('task_free_probe_children_on_run')} task-free probes"
    )
    print(f"  Per-child limit:  {receipt.get('timeout_seconds_per_child')}s")
    total = receipt.get("total_timeout_seconds")
    if total is not None:
        print(f"  Whole-council limit: {total}s")
    print(
        f"  Repository:       {receipt.get('requested_repository_behavior')} requested; "
        f"host confinement {_YES_NO[bool(receipt.get('host_filesystem_confined'))]}"
    )
    print(f"  Quality verified: {_YES_NO[bool(receipt.get('quality_verified'))]}")
    if receipt.get("session_egress_grant_active"):
        print(f"  Consent:          granted for this shell ({_SESSION_EGRESS_ENVIRONMENT})")
    else:
        print("  Consent:          this run will ask on the terminal")
    raw_routes = receipt.get("routes")
    for route in raw_routes if isinstance(raw_routes, list) else []:
        if not isinstance(route, Mapping):
            continue
        delivery = route.get("task_delivery")
        exposure = (
            "task visible in local process arguments"
            if route.get("task_process_exposure")
            else "no argv exposure"
        )
        print()
        print(f"  {route.get('vendor')} — {delivery} delivery, {exposure}")
        print(f"    model: {route.get('model_selection')}")
        command = route.get("command")
        for argument in command if isinstance(command, list) else []:
            if isinstance(argument, str):
                print(f"    {_summarize_argument(argument)}")
    print()
    print("No task was read and no vendor process was started.")
    print("Use --json for the exact machine receipt, including full argument values.")


def _render_human(receipt: Mapping[str, object]) -> None:
    event = receipt.get("event")
    if event == "advisory_preview":
        _render_preview_human(receipt)
        return
    if event == "advisory_skipped":
        print("Advisory skipped by the local trivial-task policy.")
        print("No task-bearing vendor process was started.")
        return
    if event == "advisory_council":
        print("Advisory council result (untrusted model-authored content)")
        print(f"Workflow/stage: {receipt['workflow']}/{receipt['stage']}")
        raw_members = receipt.get("members")
        for member in raw_members if isinstance(raw_members, list) else []:
            if isinstance(member, Mapping):
                print()
                print(f"Vendor: {member.get('vendor')} — {member.get('status')}")
                if "result" in member:
                    print(json.dumps(member["result"], ensure_ascii=True, indent=2))
        print()
        print("Descriptive consensus/dissent (not quality verification)")
        print(json.dumps(receipt["synthesis"], ensure_ascii=True, indent=2))
        return
    print("Advisory result (untrusted model-authored content)")
    print(f"Vendor: {receipt['vendor']} (default model; quality not verified)")
    print(f"Workflow/stage: {receipt['workflow']}/{receipt['stage']}")
    print(f"Context: {receipt['context_mode']}")
    print()
    triage = receipt.get("triage")
    result = receipt["result"]
    hidden = 0
    rejected_urgent = 0
    if isinstance(triage, Mapping) and isinstance(result, Mapping):
        annotations = triage.get("annotations")
        findings = result.get("findings")
        if isinstance(annotations, list) and isinstance(findings, list):
            visible_indices: set[int] = set()
            for annotation in annotations:
                if not isinstance(annotation, Mapping):
                    continue
                index = annotation.get("finding_index")
                if isinstance(index, bool) or not isinstance(index, int):
                    continue
                finding = findings[index] if 0 <= index < len(findings) else None
                severity = finding.get("severity") if isinstance(finding, Mapping) else None
                locally_checkable = annotation.get("triage") in {
                    "confirmed",
                    "debatable",
                } and not annotation.get("duplicate_muted")
                urgent_rejected = annotation.get("triage") == "rejected" and severity in {
                    "critical",
                    "high",
                }
                if locally_checkable or urgent_rejected:
                    visible_indices.add(index)
                rejected_urgent += int(urgent_rejected)
            visible = [
                finding for index, finding in enumerate(findings) if index in visible_indices
            ]
            hidden = len(findings) - len(visible)
            result = {**result, "findings": visible}
    print(json.dumps(result, ensure_ascii=True, indent=2))
    if isinstance(triage, Mapping):
        annotations = triage.get("annotations")
        supported = (
            sum(
                1
                for annotation in annotations
                if isinstance(annotation, Mapping)
                and annotation.get("triage") in {"confirmed", "debatable"}
                and not annotation.get("duplicate_muted")
            )
            if isinstance(annotations, list)
            else 0
        )
        print()
        print(f"Locally checkable distinct findings: {supported}")
        if rejected_urgent:
            print(
                f"Rejected high/critical findings still shown for manual review: {rejected_urgent}"
            )
        if hidden:
            print(f"Locally rejected or duplicate findings hidden: {hidden} (use --json for all)")


def _validate_common(
    *,
    workflow: str,
    role: str,
    stage: str,
    context_mode: str,
    context_files: Sequence[str],
    timeout_seconds: float,
) -> tuple[str, ...]:
    if (
        workflow not in WORKFLOWS
        or role not in ROLES
        or stage not in STAGES
        or not math.isfinite(timeout_seconds)
        or not 1 <= timeout_seconds <= 28_800
    ):
        raise QuickAdvisoryError("ask_invalid_input")
    try:
        return advisory_context.validate_context_request(context_mode, context_files)
    except advisory_context.AdvisoryContextError as error:
        raise QuickAdvisoryError(error.code) from None


def _preflight_context_git(context_mode: str, repo: Path) -> tuple[str, object] | None:
    if context_mode != "diff":
        return None
    try:
        return advisory_context.preflight_git(repo)
    except advisory_context.AdvisoryContextError as error:
        raise QuickAdvisoryError(error.code) from None


def _preflight_trivial_classifier(enabled: bool) -> None:
    if not enabled:
        return
    try:
        from weightclass.classification import classify_task
    except (ImportError, AttributeError):
        raise QuickAdvisoryError("ask_invalid_input") from None
    if not callable(classify_task):
        raise QuickAdvisoryError("ask_invalid_input")


def _snapshot(repo: Path) -> readonly_snapshot.TreeSnapshot:
    try:
        return readonly_snapshot.snapshot_tree(repo)
    except (OSError, ValueError, readonly_snapshot.SnapshotError):
        raise QuickAdvisoryError("ask_repository_unsupported") from None


def _require_unchanged(repo: Path, baseline: readonly_snapshot.TreeSnapshot) -> None:
    try:
        comparison = readonly_snapshot.compare_tree(
            repo, baseline, speculative_run.AGENT_SCAFFOLDING
        )
    except (OSError, ValueError, readonly_snapshot.SnapshotError):
        raise QuickAdvisoryError("ask_repository_changed") from None
    if comparison.changed:
        raise QuickAdvisoryError("ask_repository_changed")


def _should_skip(task: str, *, stage: str, enabled: bool) -> bool:
    if not enabled or stage not in {"plan", "final"}:
        return False
    try:
        from weightclass.classification import InvalidTaskError, classify_task

        return classify_task(task) == "low"
    except InvalidTaskError:
        return False


def _run_member(
    member: Mapping[str, object],
    *,
    repo: Path,
    workflow: str,
    stage: str,
    context_mode: str,
    context_payload: str,
    task: str,
    timeout_seconds: float,
) -> tuple[str, dict[str, object]]:
    executable = member["executable"]
    observation = member["observation"]
    command = member["command"]
    if not isinstance(executable, str) or not isinstance(command, tuple):
        raise QuickAdvisoryError("ask_invalid_input")
    try:
        if observe_executable(executable) != observation:
            raise QuickAdvisoryError("ask_cli_unavailable")
        prompt = advisory_evidence_contract.build_evidence_prompt(
            task,
            workflow,
            stage=stage,
            context_mode=context_mode,
            context_payload=context_payload,
        )
        if context_mode == "repo":
            child, stdout = speculative_run.run_child(
                list(command),
                repo,
                prompt,
                allowed_env=speculative_run.default_child_env(executable),
                timeout_seconds=timeout_seconds,
            )
        else:
            with tempfile.TemporaryDirectory(prefix="wclass-advisory-") as directory:
                child, stdout = speculative_run.run_child(
                    list(command),
                    Path(directory),
                    prompt,
                    allowed_env=speculative_run.default_child_env(executable),
                    timeout_seconds=timeout_seconds,
                )
    except QuickAdvisoryError:
        raise
    except (OSError, ValueError, speculative_run.RunFailure):
        return "ask_executor_failed", {}
    if child["timed_out"]:
        return "ask_executor_timeout", {}
    if child["exit_code"] != 0:
        return "ask_executor_failed", {}
    try:
        _raw_result, result = speculative_run.extract_evidence_result(
            stdout, list(command), workflow
        )
    except advisory_evidence_contract.EvidenceResultError:
        return "ask_result_invalid", {}
    return "ok", result


def _single_receipt(
    *,
    egress_source: str,
    vendor: str,
    workflow: str,
    role: str,
    stage: str,
    context_mode: str,
    context_file_count: int,
    delivery: str,
    result: Mapping[str, object],
    repo: Path,
    snapshot: readonly_snapshot.TreeSnapshot | None,
    triage: bool,
) -> dict[str, object]:
    receipt: dict[str, object] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "event": "advisory_ask",
        "vendor": vendor,
        "workflow": workflow,
        "role": role,
        "stage": stage,
        "model_selection": "vendor_default",
        "task_egress_confirmed": True,
        "task_egress_confirmation_source": egress_source,
        "task_egressed": True,
        "task_delivery": delivery,
        "context_mode": context_mode,
        "context_file_count": context_file_count,
        "requested_repository_access": (
            "none"
            if context_mode == "task"
            else ("read_only" if context_mode == "repo" else "prompt_context_only")
        ),
        "repository_access_verified": False,
        "host_filesystem_confined": False,
        "worktree_checked": snapshot is not None,
        "worktree_unchanged": True if snapshot is not None else None,
        "git_metadata_checked": False,
        "sample_recorded": False,
        "persistent_state_written": False,
        "campaign_state_read": False,
        "project_verifier_used": False,
        "quality_verified": False,
        "content_trust": "untrusted_model_authored",
        "fresh_process": True,
        "call_budget": {"scope": "invocation", "maximum_task_bearing_children": 1, "used": 1},
        "result": dict(result),
    }
    if workflow == "review" and triage and snapshot is not None:
        receipt["triage"] = advisory_triage.triage_review(result, repo, snapshot)
    elif workflow == "review" and triage:
        receipt["triage_skipped_reason"] = "task_context_has_no_repository_snapshot"
    return receipt


def ask(
    *,
    vendor: str,
    workflow: str,
    role: str,
    repo: Path,
    timeout_seconds: float,
    confirm_task_egress: bool,
    stage: str = "manual",
    context_mode: str = "repo",
    context_files: Sequence[str] = (),
    auto_skip_trivial: bool = False,
    triage: bool = True,
) -> dict[str, object]:
    """Run exactly one read-only vendor child without managed state."""
    selected_files = _validate_common(
        workflow=workflow,
        role=role,
        stage=stage,
        context_mode=context_mode,
        context_files=context_files,
        timeout_seconds=timeout_seconds,
    )
    if vendor not in VENDORS:
        raise QuickAdvisoryError("ask_invalid_input")
    if auto_skip_trivial and stage not in {"plan", "final"}:
        raise QuickAdvisoryError("ask_invalid_input")
    _preflight_trivial_classifier(auto_skip_trivial)
    resolved_repo = _resolve_repository(repo)
    task: str | None = None
    if auto_skip_trivial:
        task = _read_task_from_standard_input()
        if _should_skip(task, stage=stage, enabled=True):
            return {
                "schema_version": RECEIPT_SCHEMA_VERSION,
                "event": "advisory_skipped",
                "vendor": vendor,
                "workflow": workflow,
                "stage": stage,
                "reason": "local_trivial_task",
                "classification_policy": "weightclass_local",
                "task_egress_confirmed": False,
                "task_egressed": False,
                "sample_recorded": False,
                "persistent_state_written": False,
                "worktree_checked": False,
                "worktree_unchanged": None,
                "call_budget": {
                    "scope": "invocation",
                    "maximum_task_bearing_children": 1,
                    "used": 0,
                },
            }
    if not has_safe_child_status_context():
        raise QuickAdvisoryError("ask_process_context_unsafe")
    git_preflight = _preflight_context_git(context_mode, resolved_repo)
    member = _preflight_member(vendor, workflow, resolved_repo)
    egress_source = _confirm_task_egress(
        members=(member,),
        workflow=workflow,
        stage=stage,
        context_mode=context_mode,
        context_file_count=len(selected_files),
        confirmed=confirm_task_egress,
    )
    baseline = None if context_mode == "task" else _snapshot(resolved_repo)
    if task is None:
        task = _read_task_from_standard_input()
    try:
        context_payload = advisory_context.build_context(
            context_mode,
            repo=resolved_repo,
            files=selected_files,
            environment=os.environ,
            snapshot=baseline,
            git_preflight=git_preflight,
        )
    except advisory_context.AdvisoryContextError as error:
        raise QuickAdvisoryError(error.code) from None
    if baseline is not None:
        _require_unchanged(resolved_repo, baseline)
    status, result = _run_member(
        member,
        repo=resolved_repo,
        workflow=workflow,
        stage=stage,
        context_mode=context_mode,
        context_payload=context_payload,
        task=task,
        timeout_seconds=timeout_seconds,
    )
    if baseline is not None:
        _require_unchanged(resolved_repo, baseline)
    if status != "ok":
        raise QuickAdvisoryError(status)
    receipt = _single_receipt(
        egress_source=egress_source,
        vendor=vendor,
        workflow=workflow,
        role=role,
        stage=stage,
        context_mode=context_mode,
        context_file_count=len(selected_files),
        delivery=str(member["delivery"]),
        result=result,
        repo=resolved_repo,
        snapshot=baseline,
        triage=triage,
    )
    if baseline is not None:
        _require_unchanged(resolved_repo, baseline)
    return receipt


def _run_members_concurrently(
    members: Sequence[Mapping[str, object]],
    *,
    repo: Path,
    workflow: str,
    stage: str,
    context_mode: str,
    context_payload: str,
    task: str,
    timeout_seconds: float,
    deadline: float,
) -> tuple[tuple[str, dict[str, object]], ...]:
    """의회 구성원 전부를 동시에, 각자 새 벤더 프로세스에서 돌린다.

    구성원은 서로의 출력을 보지 않고 같은 태스크·컨텍스트만 받는다. 그래서
    순차 실행이 사는 것은 대기 시간뿐이고, 대가는 두 가지였다: 네 구성원이면
    벽시계가 합이 되고, 전체 데드라인이 **뒤쪽 구성원만** 굶겼다. 동시에
    띄우면 둘 다 사라진다 — 합이 최댓값이 되고, 남은 시간을 모두가 같은
    조건으로 나눠 갖는다.

    각 자식은 `run_child` 가 자기 프로세스 그룹에서 돌리고 자기 상한으로
    닫는다. 상태 수거는 `os.waitpid(pid)` 로 지목한 자식만 기다리므로,
    구성원끼리 서로의 종료 상태를 가져가지 않는다.

    실패한 피어는 취소하지 않는다. 이미 시작된 호출은 과금이 끝났고, 중간에
    끊으면 그 비용만 버린다. 이것은 `advisory_parallel` 이 캠페인에서 내린
    결정과 같다.

    반환은 언제나 **입력 순서**다. 완료 순서로 돌려주면 같은 의회를 두 번
    돌렸을 때 영수증의 구성원 순서가 달라진다.
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return tuple(("ask_council_deadline", {}) for _ in members)
    # 상한은 제출 시점에 한 번만 정한다. 스레드 안에서 다시 재면 스케줄링
    # 지연이 구성원마다 다른 상한이 되어, 같은 입력이 실행마다 다른 결과를
    # 낸다.
    member_timeout = min(timeout_seconds, remaining)

    def run_one(member: Mapping[str, object]) -> tuple[str, dict[str, object]]:
        try:
            return _run_member(
                member,
                repo=repo,
                workflow=workflow,
                stage=stage,
                context_mode=context_mode,
                context_payload=context_payload,
                task=task,
                timeout_seconds=member_timeout,
            )
        except QuickAdvisoryError as error:
            if error.code != "ask_cli_unavailable":
                raise
            return error.code, {}

    executor = ThreadPoolExecutor(max_workers=len(members), thread_name_prefix="wclass-council")
    futures: list[Future[tuple[str, dict[str, object]]]] = []
    try:
        futures = [executor.submit(run_one, member) for member in members]
        outcomes: list[tuple[str, dict[str, object]]] = []
        failure: QuickAdvisoryError | None = None
        for future in futures:
            try:
                outcomes.append(future.result())
            except QuickAdvisoryError as error:
                # 첫 구성원의 오류로 즉시 빠져나가면 남은 자식이 부모 없이
                # 계속 돈다. 전부 거둔 뒤 입력 순서로 가장 앞선 오류를 낸다.
                outcomes.append(("ask_cli_unavailable", {}))
                if failure is None:
                    failure = error
    finally:
        executor.shutdown(wait=True)
    if failure is not None:
        raise failure
    return tuple(outcomes)


def ask_council(
    *,
    vendors: Sequence[str],
    workflow: str,
    role: str,
    repo: Path,
    timeout_seconds: float,
    total_timeout_seconds: float | None = None,
    confirm_task_egress: bool,
    stage: str = "manual",
    context_mode: str = "repo",
    context_files: Sequence[str] = (),
    auto_skip_trivial: bool = False,
    triage: bool = True,
) -> dict[str, object]:
    """Run an explicit bounded council with independent fresh vendor processes."""
    selected_vendors = tuple(vendors)
    if (
        not 2 <= len(selected_vendors) <= MAX_COUNCIL_MEMBERS
        or len(set(selected_vendors)) != len(selected_vendors)
        or any(vendor not in VENDORS for vendor in selected_vendors)
    ):
        raise QuickAdvisoryError("ask_council_invalid")
    selected_files = _validate_common(
        workflow=workflow,
        role=role,
        stage=stage,
        context_mode=context_mode,
        context_files=context_files,
        timeout_seconds=timeout_seconds,
    )
    if auto_skip_trivial and stage not in {"plan", "final"}:
        raise QuickAdvisoryError("ask_invalid_input")
    effective_total_timeout = (
        timeout_seconds if total_timeout_seconds is None else total_timeout_seconds
    )
    if not math.isfinite(effective_total_timeout) or not 1 <= effective_total_timeout <= 28_800:
        raise QuickAdvisoryError("ask_invalid_input")
    _preflight_trivial_classifier(auto_skip_trivial)
    resolved_repo = _resolve_repository(repo)
    task: str | None = None
    if auto_skip_trivial:
        task = _read_task_from_standard_input()
        if _should_skip(task, stage=stage, enabled=True):
            return {
                "schema_version": RECEIPT_SCHEMA_VERSION,
                "event": "advisory_skipped",
                "vendors": list(selected_vendors),
                "workflow": workflow,
                "stage": stage,
                "reason": "local_trivial_task",
                "task_egress_confirmed": False,
                "task_egressed": False,
                "persistent_state_written": False,
                "worktree_checked": False,
                "worktree_unchanged": None,
                "call_budget": {
                    "scope": "invocation",
                    "maximum_task_bearing_children": len(selected_vendors),
                    "used": 0,
                },
            }
    if not has_safe_child_status_context():
        raise QuickAdvisoryError("ask_process_context_unsafe")
    git_preflight = _preflight_context_git(context_mode, resolved_repo)
    members = tuple(
        _preflight_member(vendor, workflow, resolved_repo) for vendor in selected_vendors
    )
    egress_source = _confirm_task_egress(
        members=members,
        workflow=workflow,
        stage=stage,
        context_mode=context_mode,
        context_file_count=len(selected_files),
        confirmed=confirm_task_egress,
    )
    baseline = None if context_mode == "task" else _snapshot(resolved_repo)
    if task is None:
        task = _read_task_from_standard_input()
    deadline = time.monotonic() + effective_total_timeout
    try:
        context_payload = advisory_context.build_context(
            context_mode,
            repo=resolved_repo,
            files=selected_files,
            environment=os.environ,
            snapshot=baseline,
            git_preflight=git_preflight,
        )
    except advisory_context.AdvisoryContextError as error:
        raise QuickAdvisoryError(error.code) from None
    if baseline is not None:
        _require_unchanged(resolved_repo, baseline)
    rendered_members: list[dict[str, object]] = []
    outcomes = _run_members_concurrently(
        members,
        repo=resolved_repo,
        workflow=workflow,
        stage=stage,
        context_mode=context_mode,
        context_payload=context_payload,
        task=task,
        timeout_seconds=timeout_seconds,
        deadline=deadline,
    )
    # 구성원 사이가 아니라 의회 전체의 앞뒤로 확인한다. 동시에 도는 자식들
    # 중 하나라도 작업 트리를 건드렸으면 여기서 닫힌다.
    if baseline is not None:
        _require_unchanged(resolved_repo, baseline)
    for member, (status, result) in zip(members, outcomes, strict=True):
        rendered: dict[str, object] = {
            "vendor": member["vendor"],
            "status": status,
            "task_delivery": member["delivery"],
            "fresh_process": True,
            "quality_verified": False,
        }
        if status == "ok":
            rendered["result"] = result
            if workflow == "review" and triage and baseline is not None:
                rendered["triage"] = advisory_triage.triage_review(result, resolved_repo, baseline)
                _require_unchanged(resolved_repo, baseline)
            elif workflow == "review" and triage:
                rendered["triage_skipped_reason"] = "task_context_has_no_repository_snapshot"
        rendered_members.append(rendered)
    successful = sum(member["status"] == "ok" for member in rendered_members)
    task_bearing_children = sum(
        member["status"] not in {"ask_cli_unavailable", "ask_council_deadline"}
        for member in rendered_members
    )
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "event": "advisory_council",
        "workflow": workflow,
        "role": role,
        "stage": stage,
        "vendors": list(selected_vendors),
        "model_selection": "vendor_default",
        "task_egress_confirmed": True,
        "task_egress_confirmation_source": egress_source,
        "task_egressed": task_bearing_children > 0,
        "context_mode": context_mode,
        "context_file_count": len(selected_files),
        "requested_repository_access": (
            "none"
            if context_mode == "task"
            else ("read_only" if context_mode == "repo" else "prompt_context_only")
        ),
        "repository_access_verified": False,
        "host_filesystem_confined": False,
        "worktree_checked": baseline is not None,
        "worktree_unchanged": True if baseline is not None else None,
        "git_metadata_checked": False,
        "sample_recorded": False,
        "persistent_state_written": False,
        "quality_verified": False,
        "content_trust": "untrusted_model_authored",
        "complete": successful == len(rendered_members),
        "successful_members": successful,
        "total_timeout_seconds": effective_total_timeout,
        "call_budget": {
            "scope": "invocation",
            "maximum_task_bearing_children": len(members),
            "used": task_bearing_children,
        },
        "members": rendered_members,
        "synthesis": advisory_triage.council_synthesis(workflow, rendered_members),
    }


def preview(
    *,
    vendors: Sequence[str],
    workflow: str,
    role: str,
    repo: Path,
    timeout_seconds: float,
    total_timeout_seconds: float | None = None,
    stage: str,
    context_mode: str,
    context_files: Sequence[str],
) -> dict[str, object]:
    """Return a task-free egress plan without starting any vendor process."""
    selected_vendors = tuple(vendors)
    if (
        not 1 <= len(selected_vendors) <= MAX_COUNCIL_MEMBERS
        or len(set(selected_vendors)) != len(selected_vendors)
        or any(vendor not in VENDORS for vendor in selected_vendors)
    ):
        raise QuickAdvisoryError(
            "ask_council_invalid" if len(selected_vendors) != 1 else "ask_invalid_input"
        )
    selected_files = _validate_common(
        workflow=workflow,
        role=role,
        stage=stage,
        context_mode=context_mode,
        context_files=context_files,
        timeout_seconds=timeout_seconds,
    )
    if total_timeout_seconds is not None and (
        len(selected_vendors) == 1
        or not math.isfinite(total_timeout_seconds)
        or not 1 <= total_timeout_seconds <= 28_800
    ):
        raise QuickAdvisoryError("ask_invalid_input")
    resolved_repo = _resolve_repository(repo)
    git_preflight = _preflight_context_git(context_mode, resolved_repo)
    routes: list[dict[str, object]] = []
    for vendor in selected_vendors:
        command, delivery = _route(vendor, workflow)
        _executable, _observation = _observe_route_executable(vendor, resolved_repo)
        routes.append(
            {
                "vendor": vendor,
                "model_selection": "vendor_default",
                "task_delivery": delivery,
                "task_process_exposure": delivery == "argv",
                "command": list(command),
                "executable_observed": True,
            }
        )
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "event": "advisory_preview",
        "preview_only": True,
        "vendor_process_started": False,
        "task_read": False,
        "repository_content_read": False,
        "workflow": workflow,
        "role": role,
        "stage": stage,
        "context_mode": context_mode,
        "context_file_count": len(selected_files),
        "task_egress_required": True,
        # 미리보기는 확인을 받지 않지만, 이 세션에 이미 승인이 있으면 실제
        # 실행이 프롬프트 없이 지나간다. 그 사실을 여기서 말하지 않으면
        # 미리보기가 실행과 다른 그림을 보여 준다.
        "session_egress_grant_active": session_egress_granted(),
        "local_capability_checked": False,
        "process_context_checked": False,
        "task_free_probe_children_on_run": (
            advisory_preflight.TASK_FREE_PROBE_CHILDREN * len(routes)
        ),
        "task_bearing_child_bound": len(routes),
        "timeout_seconds_per_child": timeout_seconds,
        "total_timeout_seconds": (
            timeout_seconds
            if len(routes) > 1 and total_timeout_seconds is None
            else total_timeout_seconds
        ),
        "requested_repository_behavior": ("none" if context_mode == "task" else "read_only"),
        "host_filesystem_confined": False,
        "worktree_snapshot_on_run": context_mode != "task",
        "git_metadata_checked": False,
        "git_executable_observed": git_preflight is not None,
        "repository_path_disclosed": False,
        "untracked_files_included": False if context_mode == "diff" else None,
        "quality_verified": False,
        "routes": routes,
    }


def _parse_council(value: str) -> tuple[str, ...]:
    selected = tuple(value.split(","))
    if (
        not 2 <= len(selected) <= MAX_COUNCIL_MEMBERS
        or len(set(selected)) != len(selected)
        or any(vendor not in VENDORS for vendor in selected)
    ):
        raise argparse.ArgumentTypeError("invalid council")
    return selected


def main(argv: Sequence[str] | None = None) -> int:
    parser = _ArgumentParser(
        prog="wclass-advisory ask",
        description="Run one stateless read-only advisory, or an explicit bounded vendor council.",
        allow_abbrev=False,
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--vendor", choices=VENDORS)
    target.add_argument(
        "--council",
        type=_parse_council,
        help="comma-separated list of two to four distinct supported vendors",
    )
    parser.add_argument("--workflow", choices=WORKFLOWS, default="review")
    parser.add_argument("--stage", choices=STAGES, default="manual")
    parser.add_argument("--context", choices=advisory_context.CONTEXT_MODES, default="repo")
    parser.add_argument(
        "--file",
        dest="context_files",
        action="append",
        default=[],
        metavar="RELATIVE_PATH",
        help="explicit UTF-8 repository file for --context files; repeatable",
    )
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument(
        "--timeout",
        "--timeout-seconds",
        dest="timeout_seconds",
        type=float,
        default=speculative_run.CHILD_TIMEOUT,
    )
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--auto-skip-trivial", action="store_true")
    parser.add_argument("--no-triage", action="store_false", dest="triage")
    parser.add_argument(
        "--total-timeout-seconds",
        type=float,
        help="whole-council deadline; defaults to one per-child timeout",
    )
    parser.add_argument("--confirm-task-egress", action="store_true")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true")
    output.add_argument("--human", action="store_true")
    arguments = parser.parse_args(argv)
    human = _human_output_requested(arguments)
    vendors = arguments.council or (arguments.vendor,)
    try:
        if arguments.preview:
            receipt = preview(
                vendors=vendors,
                workflow=arguments.workflow,
                role="cheap",
                repo=arguments.repo,
                timeout_seconds=arguments.timeout_seconds,
                total_timeout_seconds=arguments.total_timeout_seconds,
                stage=arguments.stage,
                context_mode=arguments.context,
                context_files=arguments.context_files,
            )
        elif arguments.council:
            receipt = ask_council(
                vendors=arguments.council,
                workflow=arguments.workflow,
                role="cheap",
                repo=arguments.repo,
                timeout_seconds=arguments.timeout_seconds,
                total_timeout_seconds=arguments.total_timeout_seconds,
                confirm_task_egress=arguments.confirm_task_egress,
                stage=arguments.stage,
                context_mode=arguments.context,
                context_files=arguments.context_files,
                auto_skip_trivial=arguments.auto_skip_trivial,
                triage=arguments.triage,
            )
        else:
            if arguments.total_timeout_seconds is not None:
                raise QuickAdvisoryError("ask_invalid_input")
            receipt = ask(
                vendor=arguments.vendor,
                workflow=arguments.workflow,
                role="cheap",
                repo=arguments.repo,
                timeout_seconds=arguments.timeout_seconds,
                confirm_task_egress=arguments.confirm_task_egress,
                stage=arguments.stage,
                context_mode=arguments.context,
                context_files=arguments.context_files,
                auto_skip_trivial=arguments.auto_skip_trivial,
                triage=arguments.triage,
            )
    except QuickAdvisoryError as error:
        return _emit_error(error.code, human=human)
    if human:
        _render_human(receipt)
    else:
        print(json.dumps(receipt, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    if receipt.get("event") == "advisory_council" and not receipt.get("complete"):
        return PARTIAL_COUNCIL_EXIT_CODE
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
