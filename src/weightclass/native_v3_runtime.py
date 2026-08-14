"""One reviewed schema-3 native foreground invocation."""

from __future__ import annotations

from .executable_observation import ExecutableObservation, observe_executable
from .foreground_process import RedactedSpawnInvocation, run_owned_foreground_redacted
from .native_v3_compile import StaticNativeSelectionV3
from .router import TASK_PLACEHOLDER
from .task_v2 import ValidatedTaskV2
from .v2_validation import V2ValidationError


class NativeV3ExecutorUnavailableError(ValueError):
    """Raised without runtime details when the final executable cannot be observed."""


class NativeV3FingerprintMismatchError(ValueError):
    """Raised without runtime details when the executable changed before spawn."""


def _materialize(
    selected: StaticNativeSelectionV3,
    task: ValidatedTaskV2,
) -> RedactedSpawnInvocation:
    task_bytes = task.delivery_bytes()
    if selected.task_delivery == "stdin":
        arguments = selected.argv_template
        child_input = task_bytes
    else:
        task_text = task.classification_text()
        if (
            selected.argv_template.count(TASK_PLACEHOLDER) != 1
            or b"\x00" in task_bytes
            or len(task_bytes) > 32_768
            or (selected.vendor == "grok" and task_text.startswith("-"))
        ):
            raise V2ValidationError()
        arguments = tuple(
            task_text if token == TASK_PLACEHOLDER else token for token in selected.argv_template
        )
        child_input = b""
    encoded_arguments = [argument.encode("utf-8", errors="strict") for argument in arguments]
    if (
        not 1 <= len(arguments) <= 32
        or any(len(argument) > 32_768 for argument in encoded_arguments)
        or sum(map(len, encoded_arguments)) > 49_152
    ):
        raise V2ValidationError()
    return RedactedSpawnInvocation(
        arguments,
        child_input,
        cleanup_grace_seconds=0,
        terminate_grace_seconds=0,
    )


def run_native_v3(
    selected: StaticNativeSelectionV3,
    task: ValidatedTaskV2,
    first_observation: ExecutableObservation,
) -> int:
    """Reobserve, materialize once, and spawn one foreground child."""
    try:
        final_observation = observe_executable(selected.executable)
    except (OSError, V2ValidationError, ValueError):
        raise NativeV3ExecutorUnavailableError() from None
    if final_observation != first_observation:
        raise NativeV3FingerprintMismatchError()
    return run_owned_foreground_redacted(_materialize(selected, task))
