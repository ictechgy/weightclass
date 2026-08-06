"""One-shot process boundary for a trusted external delegation runtime."""

import os
import subprocess
from pathlib import Path
from typing import Final

from .delegation_types import DirectChildCleanup

RUNTIME_ARGUMENTS: Final = ("--weightclass-delegation-protocol", "1")


class DelegationRuntimeUnavailableError(OSError):
    """Raised without path details when the reviewed runtime cannot start."""


class DelegationRuntimeFailedError(OSError):
    """Raised after a post-spawn framing failure and direct-child cleanup."""


def validate_delegation_runtime(runtime_path: str) -> None:
    """Require the exact reviewed path to name a regular executable file."""
    path = Path(runtime_path)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise DelegationRuntimeUnavailableError()


def _write_all(file_descriptor: int, contents: bytes) -> None:
    """Write every byte, retrying only interrupted writes."""
    remaining = memoryview(contents)
    while remaining:
        try:
            written = os.write(file_descriptor, remaining)
        except InterruptedError:
            continue
        if written <= 0:
            raise DelegationRuntimeFailedError()
        remaining = remaining[written:]


def _wait(
    process: subprocess.Popen[bytes],
    timeout: float | None = None,
) -> int:
    while True:
        try:
            return process.wait(timeout=timeout)
        except InterruptedError:
            continue


def _close_stdin(process: subprocess.Popen[bytes]) -> None:
    if process.stdin is not None and not process.stdin.closed:
        process.stdin.close()


def _cleanup_after_framing_failure(
    process: subprocess.Popen[bytes],
    cleanup: DirectChildCleanup,
) -> None:
    try:
        _close_stdin(process)
    except OSError:
        pass
    try:
        _wait(process, cleanup.grace_seconds)
        return
    except subprocess.TimeoutExpired:
        process.terminate()
    try:
        _wait(process, cleanup.terminate_grace_seconds)
        return
    except subprocess.TimeoutExpired:
        process.kill()
    _wait(process)


def run_delegation_runtime(
    runtime_path: str,
    frame: bytes,
    cleanup: DirectChildCleanup,
) -> subprocess.CompletedProcess[bytes]:
    """Spawn once, write one complete frame, and wait for the direct child."""
    arguments = (runtime_path, *RUNTIME_ARGUMENTS)
    try:
        process = subprocess.Popen(
            arguments,
            stdin=subprocess.PIPE,
            close_fds=True,
        )
    except (OSError, ValueError):
        raise DelegationRuntimeUnavailableError() from None

    try:
        if process.stdin is None:
            raise DelegationRuntimeFailedError()
        _write_all(process.stdin.fileno(), frame)
        _close_stdin(process)
    except (OSError, ValueError):
        _cleanup_after_framing_failure(process, cleanup)
        raise DelegationRuntimeFailedError() from None

    return_code = _wait(process)
    return subprocess.CompletedProcess(arguments, return_code)
