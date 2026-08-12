"""Exact one-child foreground execution for delegation protocol 2."""

import subprocess
import threading

from .executable_observation import ExecutableObservation, observe_executable
from .foreground_process import run_owned_foreground
from .native_v2_types import CompiledExecutionV2
from .process_context import has_safe_sigchld_disposition
from .v2_validation import V2ValidationError


def run_delegation_v2_runtime(
    compiled: CompiledExecutionV2,
    frame: bytes,
    first_observation: ExecutableObservation,
) -> subprocess.CompletedProcess[bytes]:
    """Reobserve and start the reviewed external runtime exactly once."""
    if (
        compiled.executable != compiled.argv[0]
        or threading.current_thread() is not threading.main_thread()
        or not has_safe_sigchld_disposition()
    ):
        raise V2ValidationError()
    second_observation = observe_executable(compiled.executable)
    if second_observation != first_observation:
        raise V2ValidationError()
    return run_owned_foreground(
        compiled.argv,
        frame,
        cleanup_grace_seconds=compiled.cleanup.grace_seconds,
        terminate_grace_seconds=compiled.cleanup.terminate_grace_seconds,
    )
