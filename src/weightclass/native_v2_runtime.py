"""Exact foreground execution for one compiled native schema-2 route."""

import subprocess

from .delegation_runtime import validate_runtime_process_context
from .executable_observation import ExecutableObservation, observe_executable
from .foreground_process import run_owned_foreground
from .native_v2_types import CompiledExecutionV2
from .v2_validation import V2ValidationError


def run_native_v2(
    compiled: CompiledExecutionV2,
    task_bytes: bytes,
    first_observation: ExecutableObservation,
) -> subprocess.CompletedProcess[bytes]:
    """Reobserve and start exactly the immutable reviewed argv once."""
    if compiled.executable != compiled.argv[0]:
        raise V2ValidationError()
    validate_runtime_process_context()
    second_observation = observe_executable(compiled.executable)
    if second_observation != first_observation:
        raise V2ValidationError()
    return run_owned_foreground(
        compiled.argv,
        task_bytes,
        cleanup_grace_seconds=compiled.cleanup.grace_seconds,
        terminate_grace_seconds=compiled.cleanup.terminate_grace_seconds,
    )
