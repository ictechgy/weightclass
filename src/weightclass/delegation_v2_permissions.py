"""Closed delegation-v2 write capability and mutable-scope analysis."""

from __future__ import annotations

from .delegation_v2_schema import DelegationV2InvalidInputError
from .delegation_v2_types import DelegationTaskBindingV2, DelegationWorkflowV2

_FILESYSTEM_WRITES = {"none": False, "read": False, "write": True}
_COMMAND_WRITES = {"deny": False, "allow": True}
_WORKTREE_WRITES = {"none": False, "read_only": False, "mutable": True}
_WRITE_CAPABILITIES = frozenset({"workspace-write"})


def task_requests_write(task: DelegationTaskBindingV2) -> bool:
    """Derive requested write capability from the exact closed v2 tables."""
    try:
        return (
            _FILESYSTEM_WRITES[task.request.permissions.filesystem]
            or _COMMAND_WRITES[task.request.permissions.commands]
            or _WORKTREE_WRITES[task.worktree.mode]
            or bool(_WRITE_CAPABILITIES.intersection(task.capabilities))
        )
    except KeyError:
        raise DelegationV2InvalidInputError() from None


def _scope_parts(scope: str) -> tuple[str, ...]:
    try:
        encoded_size = len(scope.encode("utf-8", errors="strict"))
    except UnicodeEncodeError:
        raise DelegationV2InvalidInputError() from None
    parts = tuple(scope.split("/"))
    if (
        not 1 <= encoded_size <= 4_096
        or len(parts) > 4_096
        or any(not part or part in {".", ".."} for part in parts)
    ):
        raise DelegationV2InvalidInputError()
    return parts


def validate_write_scope_conflicts(workflow: DelegationWorkflowV2) -> None:
    """Reject equal or ancestor scope overlap between write-capable tasks."""
    scoped_writers: list[tuple[str, tuple[str, ...]]] = []
    for task in workflow.tasks:
        if len(task.mutable_scopes) > 16:
            raise DelegationV2InvalidInputError()
        parsed_scopes = tuple(_scope_parts(scope) for scope in task.mutable_scopes)
        if task_requests_write(task):
            scoped_writers.extend((task.task_id, scope) for scope in parsed_scopes)

    for index, (left_task, left) in enumerate(scoped_writers):
        for right_task, right in scoped_writers[index + 1 :]:
            if left_task == right_task:
                continue
            shorter = min(len(left), len(right))
            if left[:shorter] == right[:shorter]:
                raise DelegationV2InvalidInputError()
