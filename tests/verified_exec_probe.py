"""Small, task-free compatibility probe for descriptor-based ``execve``.

This module is deliberately test-only.  It does not import the runtime or use
the runtime's executable observation or process-launch paths.
"""

from __future__ import annotations

import errno
import json
import math
import os
import signal
import sys
import tempfile
import time
from typing import TypeAlias

Status: TypeAlias = dict[str, str]

_DEFAULT_TIMEOUT_SECONDS = 3.0
_MAX_TIMEOUT_SECONDS = 5.0
_NATIVE_EXIT = 37
_REPLACEMENT_EXIT = 41
_SCRIPT_EXIT = 43


def _status(state: str, reason: str) -> Status:
    return {"status": state, "reason": reason}


def _unsupported() -> Status:
    return _status("unsupported", "fd_exec_not_advertised")


def _bounded_timeout(timeout_seconds: float) -> float:
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    return min(timeout_seconds, _MAX_TIMEOUT_SECONDS)


def _close_quietly(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass


def _child_error(write_fd: int, error_number: int) -> None:
    try:
        os.write(write_fd, bytes((error_number & 0xFF,)))
    except OSError:
        pass


def _execute_descriptor(
    descriptor: int,
    argv: list[str],
    expected_exit: int,
    timeout_seconds: float,
    success_reason: str,
    failure_reason: str,
    exec_failure_reason: str | None = None,
) -> Status:
    """Execute one already-open descriptor and collect only process status.

    The child has no task-bearing input and its output is redirected to the
    null device.  The one-byte pipe is used only for an ``execve`` errno; a
    successful exec closes it because Python file descriptors are
    close-on-exec by default.
    """

    if not all(hasattr(os, name) for name in ("fork", "waitpid", "kill")):
        return _status("failed", "fork_unavailable")

    read_fd, write_fd = os.pipe()
    child_pid = -1
    try:
        os.set_blocking(read_fd, False)
        child_pid = os.fork()
        if child_pid == 0:
            _close_quietly(read_fd)
            null_fd = -1
            try:
                null_fd = os.open(os.devnull, os.O_RDWR)
                os.dup2(null_fd, 0)
                os.dup2(null_fd, 1)
                os.dup2(null_fd, 2)
                if null_fd not in (0, 1, 2):
                    _close_quietly(null_fd)
                    null_fd = -1
                os.execve(descriptor, argv, {})
            except OSError as error:
                _child_error(write_fd, error.errno or errno.EIO)
            except BaseException:
                _child_error(write_fd, errno.EIO)
            finally:
                if null_fd >= 0:
                    _close_quietly(null_fd)
                _close_quietly(descriptor)
                _close_quietly(write_fd)
            os._exit(127)

        _close_quietly(write_fd)
        write_fd = -1
        deadline = time.monotonic() + timeout_seconds
        wait_status: int | None = None
        timed_out = False
        while True:
            waited_pid, candidate_status = os.waitpid(child_pid, os.WNOHANG)
            if waited_pid == child_pid:
                wait_status = candidate_status
                break
            if time.monotonic() >= deadline:
                timed_out = True
                try:
                    os.kill(child_pid, signal.SIGKILL)
                except OSError:
                    pass
                cleanup_deadline = time.monotonic() + 0.25
                while time.monotonic() < cleanup_deadline:
                    waited_pid, candidate_status = os.waitpid(child_pid, os.WNOHANG)
                    if waited_pid == child_pid:
                        wait_status = candidate_status
                        break
                    time.sleep(0.005)
                break
            time.sleep(0.005)

        try:
            error_bytes = os.read(read_fd, 1)
        except BlockingIOError:
            error_bytes = b""
        if timed_out:
            return _status("failed", "timeout")
        if error_bytes:
            return _status("failed", exec_failure_reason or failure_reason)
        if wait_status is not None and os.WIFEXITED(wait_status):
            if os.WEXITSTATUS(wait_status) == expected_exit:
                return _status("passed", success_reason)
            return _status("failed", failure_reason)
        return _status("failed", failure_reason)
    except OSError:
        return _status("failed", failure_reason)
    finally:
        _close_quietly(read_fd)
        if write_fd >= 0:
            _close_quietly(write_fd)


def _write_all(descriptor: int, data: bytes) -> None:
    position = 0
    while position < len(data):
        position += os.write(descriptor, data[position:])


def _script_bytes(exit_code: int) -> bytes:
    interpreter = os.fsencode(sys.executable)
    return b"#!" + interpreter + b"\nimport os\nos._exit(" + str(exit_code).encode() + b")\n"


def _probe_native(timeout_seconds: float) -> Status:
    try:
        descriptor = os.open(sys.executable, os.O_RDONLY)
    except OSError:
        return _status("failed", "native_open_failed")
    try:
        return _execute_descriptor(
            descriptor,
            ["verified-exec-native", "-c", "import os; os._exit(37)"],
            _NATIVE_EXIT,
            timeout_seconds,
            "native_descriptor_exec",
            "native_exec_failed",
        )
    finally:
        _close_quietly(descriptor)


def _make_script(directory: str, name: str, exit_code: int) -> str:
    path = os.path.join(directory, name)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
    try:
        _write_all(descriptor, _script_bytes(exit_code))
    finally:
        _close_quietly(descriptor)
    return path


def _probe_script(directory: str, timeout_seconds: float) -> Status:
    try:
        path = _make_script(directory, "shebang-probe", _SCRIPT_EXIT)
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return _status("failed", "script_open_failed")
    try:
        result = _execute_descriptor(
            descriptor,
            ["verified-exec-script"],
            _SCRIPT_EXIT,
            timeout_seconds,
            "shebang_descriptor_exec",
            "script_exec_failed",
            "shebang_descriptor_exec_unsupported",
        )
        if result["reason"] == "shebang_descriptor_exec_unsupported":
            return _status("unsupported", result["reason"])
        return result
    finally:
        _close_quietly(descriptor)


def _probe_path_swap(directory: str, timeout_seconds: float) -> Status:
    target = os.path.join(directory, "opened-native")
    try:
        os.symlink(sys.executable, target)
        replacement_path = _make_script(directory, "replacement", _REPLACEMENT_EXIT)
        descriptor = os.open(target, os.O_RDONLY)
    except OSError:
        return _status("failed", "path_swap_setup_failed")
    try:
        os.replace(replacement_path, target)
        return _execute_descriptor(
            descriptor,
            ["verified-exec-path-swap", "-c", "import os; os._exit(37)"],
            _NATIVE_EXIT,
            timeout_seconds,
            "opened_object_survived_path_swap",
            "opened_object_not_bound",
        )
    except OSError:
        return _status("failed", "path_swap_failed")
    finally:
        _close_quietly(descriptor)


def run_compatibility_probe(timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS) -> dict[str, object]:
    """Return the platform's bounded, task-free descriptor-exec result."""

    fd_exec_advertised = os.execve in os.supports_fd
    if not fd_exec_advertised:
        unsupported = _unsupported()
        return {
            "schema_version": 1,
            "platform": sys.platform,
            "fd_exec_advertised": False,
            "native": dict(unsupported),
            "script": dict(unsupported),
            "path_swap": dict(unsupported),
        }

    bounded_timeout = _bounded_timeout(timeout_seconds)
    with tempfile.TemporaryDirectory(prefix="verified-exec-probe-") as directory:
        return {
            "schema_version": 1,
            "platform": sys.platform,
            "fd_exec_advertised": True,
            "native": _probe_native(bounded_timeout),
            "script": _probe_script(directory, bounded_timeout),
            "path_swap": _probe_path_swap(directory, bounded_timeout),
        }


def main() -> None:
    print(json.dumps(run_compatibility_probe(), sort_keys=True))


if __name__ == "__main__":
    main()
