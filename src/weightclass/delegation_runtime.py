"""One-shot process boundary for a trusted external delegation runtime."""

import os
import selectors
import subprocess
import time
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


def _write_all(file_descriptor: int, contents: bytes, timeout: float) -> None:
    """Write every byte without blocking beyond one monotonic deadline."""
    deadline = time.monotonic() + timeout
    remaining = memoryview(contents)
    os.set_blocking(file_descriptor, False)
    with selectors.DefaultSelector() as selector:
        selector.register(file_descriptor, selectors.EVENT_WRITE)
        while remaining:
            if time.monotonic() >= deadline:
                raise DelegationRuntimeFailedError()
            try:
                written = os.write(file_descriptor, remaining)
            except InterruptedError:
                continue
            except BlockingIOError:
                while True:
                    remaining_seconds = deadline - time.monotonic()
                    if remaining_seconds <= 0:
                        raise DelegationRuntimeFailedError() from None
                    try:
                        ready = selector.select(remaining_seconds)
                    except InterruptedError:
                        continue
                    if not ready:
                        raise DelegationRuntimeFailedError() from None
                    break
                continue
            if written <= 0:
                raise DelegationRuntimeFailedError()
            remaining = remaining[written:]


def _wait(
    process: subprocess.Popen[bytes],
    timeout: float | None = None,
) -> int:
    if timeout is None:
        while True:
            try:
                return process.wait()
            except InterruptedError:
                continue

    deadline = time.monotonic() + timeout
    remaining_seconds = timeout
    while True:
        try:
            return process.wait(timeout=remaining_seconds)
        except InterruptedError:
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout) from None


def _close_stdin(process: subprocess.Popen[bytes]) -> None:
    if process.stdin is not None and not process.stdin.closed:
        process.stdin.close()


def _cleanup_direct_child(
    process: subprocess.Popen[bytes],
    cleanup: DirectChildCleanup,
) -> None:
    try:
        _close_stdin(process)
    except (OSError, ValueError):
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
    """Spawn once, deliver one frame within the reviewed grace, and wait."""
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
        try:
            _write_all(process.stdin.fileno(), frame, cleanup.grace_seconds)
            _close_stdin(process)
        except (OSError, ValueError):
            raise DelegationRuntimeFailedError() from None
        return_code = _wait(process)
    except BaseException:
        try:
            _cleanup_direct_child(process, cleanup)
        except BaseException:
            # Cleanup failure must not replace the original interruption.
            pass
        raise
    return subprocess.CompletedProcess(arguments, return_code)
