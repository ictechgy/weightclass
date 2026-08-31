"""One-shot process boundary for a trusted external delegation runtime."""

import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from types import FrameType
from typing import Any, Final

from .delegation_types import DirectChildCleanup
from .executable_observation import ExecutableObservation, observe_executable
from .process_context import (
    ChildStatusLostError,
    has_safe_sigchld_disposition,
    wait_owned_child,
)

RUNTIME_ARGUMENTS: Final = ("--weightclass-delegation-protocol", "1")
_SIGINT_POLL_SECONDS: Final = 0.05
_CHILD_STATUS_LOST: Final = -sys.maxsize


class DelegationRuntimeUnavailableError(OSError):
    """Raised without path details when the reviewed runtime cannot start."""


class DelegationRuntimeFailedError(OSError):
    """Raised after a post-spawn framing failure and direct-child cleanup."""


class _ChildOwnedSigintInterrupt(BaseException):
    """Stop runtime exchange only after its direct-child handle is owned."""


@dataclass
class _SigintDeferredUntilChildOwned:
    """Record SIGINT until a spawned direct child can be cleaned safely.

    이 클래스와 delegation_conformance._SigintForwardedToGroup 은 합치면 안 된다.
    한때 둘 다 _DeferredSigint 라는 같은 이름이었고, 그래서 합칠 수 있는 사본처럼
    보였다. 하는 일이 서로 다르다.

    - 여기: SIGINT 를 **연기**한다. 자식 핸들을 소유하기 전에 빠져나가면 정리할
      대상을 잃으므로, 소유가 확인될 때까지 붙들었다가 그때 인터럽트를 올린다.
    - 저기: 잡아둔 프로세스그룹에 **즉시 전달**한다. 이미 그룹을 소유하고 있고,
      exec 된 드라이버가 블록된 마스크를 물려받지 않게 하는 것이 목적이다.

    저장·복원·재전달 로직이 닮은 것은 사실이나, 그 30 줄을 공유하려고 상태
    기계까지 합치면 둘 중 하나의 수명주기가 깨진다.
    """

    previous_handler: Any = None
    received_frame: FrameType | None = None
    active: bool = False
    received: bool = False
    process_owned: bool = False
    cleaning: bool = False

    def _handle(self, signal_number: int, frame: FrameType | None) -> None:
        del signal_number
        if self.previous_handler == signal.SIG_IGN:
            return
        if not self.received:
            self.received_frame = frame
        self.received = True

    def arm(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return
        try:
            self.previous_handler = signal.getsignal(signal.SIGINT)
            if self.previous_handler == signal.SIG_IGN:
                return
            signal.signal(signal.SIGINT, self._handle)
        except (OSError, ValueError):
            return
        self.active = True

    def record_process_ownership(self) -> None:
        self.process_owned = True
        self.check()

    def check(self) -> None:
        if self.active and self.process_owned and self.received and not self.cleaning:
            raise _ChildOwnedSigintInterrupt()

    def begin_cleanup(self) -> None:
        self.cleaning = True

    def restore_and_dispatch(self) -> None:
        if not self.active:
            return
        previous_handler = self.previous_handler
        signal.signal(signal.SIGINT, previous_handler)
        self.active = False
        received = self.received
        received_frame = self.received_frame
        if not received or previous_handler == signal.SIG_IGN:
            return
        if previous_handler == signal.SIG_DFL:
            signal.raise_signal(signal.SIGINT)
            return
        if callable(previous_handler):
            previous_handler(signal.SIGINT, received_frame)


def validate_delegation_runtime(runtime_path: str) -> ExecutableObservation:
    """Observe one admitted runtime identity without exposing path details."""

    try:
        return observe_executable(runtime_path)
    except (OSError, ValueError):
        raise DelegationRuntimeUnavailableError() from None


def validate_runtime_process_context() -> None:
    """Require a reviewed context that preserves direct-child status.

    This observation cannot prevent hostile concurrent native mutation. The
    caller must retain exclusive child-status ownership throughout invocation;
    the second check next to ``Popen`` narrows but cannot remove that residual.
    """
    if (
        threading.current_thread() is not threading.main_thread()
        or not has_safe_sigchld_disposition()
    ):
        raise DelegationRuntimeUnavailableError()


def _write_all(
    file_descriptor: int,
    contents: bytes,
    timeout: float,
    interrupt_check: Callable[[], None] | None = None,
) -> None:
    """Write every byte without blocking beyond one monotonic deadline."""
    deadline = time.monotonic() + timeout
    remaining = memoryview(contents)
    os.set_blocking(file_descriptor, False)
    with selectors.DefaultSelector() as selector:
        selector.register(file_descriptor, selectors.EVENT_WRITE)
        while remaining:
            if interrupt_check is not None:
                interrupt_check()
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
                    wait_seconds = remaining_seconds
                    if interrupt_check is not None:
                        wait_seconds = min(wait_seconds, _SIGINT_POLL_SECONDS)
                    try:
                        ready = selector.select(wait_seconds)
                    except InterruptedError:
                        continue
                    if not ready:
                        if interrupt_check is not None:
                            interrupt_check()
                            continue
                        raise DelegationRuntimeFailedError() from None
                    break
                continue
            if written <= 0:
                raise DelegationRuntimeFailedError()
            remaining = remaining[written:]
        if interrupt_check is not None:
            interrupt_check()


def _wait(
    process: subprocess.Popen[bytes],
    timeout: float | None = None,
) -> int:
    try:
        return wait_owned_child(process, timeout)
    except ChildStatusLostError:
        raise DelegationRuntimeFailedError() from None


def _close_stdin(process: subprocess.Popen[bytes]) -> None:
    if process.stdin is not None and not process.stdin.closed:
        process.stdin.close()


def _signal_direct_child(process: subprocess.Popen[bytes], signal_number: int) -> bool:
    """Signal after a zero-time owned-child wait still observes it running.

    The wait-to-signal sequence narrows stale-PID exposure but is not atomic
    against a foreign concurrent reaper.
    """
    try:
        _wait(process, 0)
        return False
    except subprocess.TimeoutExpired:
        pass
    try:
        os.kill(process.pid, signal_number)
    except ProcessLookupError:
        process.returncode = _CHILD_STATUS_LOST
        raise DelegationRuntimeFailedError() from None
    return True


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
        if not _signal_direct_child(process, signal.SIGTERM):
            return
    try:
        _wait(process, cleanup.terminate_grace_seconds)
        return
    except subprocess.TimeoutExpired:
        if not _signal_direct_child(process, signal.SIGKILL):
            return
    _wait(process)


def _wait_interruptibly(
    process: subprocess.Popen[bytes],
    interrupt_check: Callable[[], None],
) -> int:
    while True:
        interrupt_check()
        try:
            return_code = _wait(process, _SIGINT_POLL_SECONDS)
        except subprocess.TimeoutExpired:
            continue
        interrupt_check()
        return return_code


def run_delegation_runtime(
    runtime_path: str,
    frame: bytes,
    cleanup: DirectChildCleanup,
    expected_observation: ExecutableObservation | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Spawn once, deliver one frame within the reviewed grace, and wait."""
    arguments = (runtime_path, *RUNTIME_ARGUMENTS)
    deferred_sigint = _SigintDeferredUntilChildOwned()
    process: subprocess.Popen[bytes] | None = None
    cleanup_failed = False
    completed = False
    interrupted = False
    return_code = 0
    deferred_sigint.arm()
    try:
        validate_runtime_process_context()
        if (
            expected_observation is not None
            and validate_delegation_runtime(runtime_path) != expected_observation
        ):
            raise DelegationRuntimeUnavailableError()
        try:
            process = subprocess.Popen(
                arguments,
                stdin=subprocess.PIPE,
                close_fds=True,
            )
        except (OSError, ValueError):
            raise DelegationRuntimeUnavailableError() from None
        deferred_sigint.record_process_ownership()
        if process.stdin is None:
            raise DelegationRuntimeFailedError()
        try:
            _write_all(
                process.stdin.fileno(),
                frame,
                cleanup.grace_seconds,
                deferred_sigint.check,
            )
            _close_stdin(process)
        except (OSError, ValueError):
            raise DelegationRuntimeFailedError() from None
        return_code = _wait_interruptibly(process, deferred_sigint.check)
        deferred_sigint.check()
        completed = True
    except _ChildOwnedSigintInterrupt:
        interrupted = True
    finally:
        if process is not None and (not completed or deferred_sigint.received):
            deferred_sigint.begin_cleanup()
            try:
                _cleanup_direct_child(process, cleanup)
            except BaseException:
                # Cleanup failure must not replace the original interruption.
                cleanup_failed = True
        deferred_sigint.restore_and_dispatch()
    if interrupted:
        if cleanup_failed or process is None or process.returncode is None:
            raise DelegationRuntimeFailedError()
        return subprocess.CompletedProcess(arguments, process.returncode)
    return subprocess.CompletedProcess(arguments, return_code)
