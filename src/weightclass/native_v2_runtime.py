"""Exact foreground execution for one compiled native schema-2 route."""

import subprocess

from .delegation_runtime import validate_runtime_process_context
from .executable_observation import ExecutableObservation, observe_executable
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
    return subprocess.run(compiled.argv, check=False, input=task_bytes, shell=False)
