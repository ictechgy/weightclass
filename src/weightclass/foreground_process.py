"""One foreground child with fail-closed wait-status ownership."""

import os
import signal
import subprocess
from collections.abc import Callable
from types import FrameType
from typing import Any, NoReturn, SupportsIndex, TypeVar

from .process_context import (
    ChildStatusLostError,
    has_safe_child_status_context,
    wait_owned_child,
)


class ForegroundProcessError(OSError):
    """Raised without task or process details when foreground delivery fails."""


class RedactedSpawnInvocation:
    """Immutable task-bearing spawn request with value-free diagnostics."""

    _arguments: tuple[str, ...]
    _input_bytes: bytes
    _cleanup_grace_seconds: float
    _terminate_grace_seconds: float

    __slots__ = (
        "_arguments",
        "_input_bytes",
        "_cleanup_grace_seconds",
        "_terminate_grace_seconds",
    )

    def __init__(
        self,
        arguments: tuple[str, ...],
        input_bytes: bytes,
        *,
        cleanup_grace_seconds: float,
        terminate_grace_seconds: float,
    ) -> None:
        object.__setattr__(self, "_arguments", arguments)
        object.__setattr__(self, "_input_bytes", input_bytes)
        object.__setattr__(self, "_cleanup_grace_seconds", cleanup_grace_seconds)
        object.__setattr__(self, "_terminate_grace_seconds", terminate_grace_seconds)

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        raise AttributeError

    def __repr__(self) -> str:
        return "RedactedSpawnInvocation(<redacted>)"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        return self is other

    __hash__ = object.__hash__

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        del protocol
        raise TypeError

    def __getstate__(self) -> NoReturn:
        raise TypeError


class _DeferredSigint:
    """Record SIGINT while process ownership or cleanup is between operations."""

    def __init__(self) -> None:
        self.previous_handler: Any = None
        self.received_frame: FrameType | None = None
        self.received = False
        self.active = False

    def _handle(self, signal_number: int, frame: FrameType | None) -> None:
        del signal_number
        if self.previous_handler == signal.SIG_IGN:
            return
        if not self.received:
            self.received_frame = frame
        self.received = True

    def arm(self) -> None:
        try:
            self.previous_handler = signal.getsignal(signal.SIGINT)
            if self.previous_handler == signal.SIG_IGN:
                return
            signal.signal(signal.SIGINT, self._handle)
        except (OSError, ValueError):
            raise ForegroundProcessError() from None
        self.active = True

    def restore_and_dispatch(self) -> None:
        if not self.active:
            return
        previous_handler = self.previous_handler
        signal.signal(signal.SIGINT, previous_handler)
        self.active = False
        if not self.received or previous_handler == signal.SIG_IGN:
            return
        if previous_handler == signal.SIG_DFL:
            signal.raise_signal(signal.SIGINT)
            return
        if callable(previous_handler):
            previous_handler(signal.SIGINT, self.received_frame)


_OperationResult = TypeVar("_OperationResult")


def _retry_cleanup_interrupts(
    operation: Callable[[], _OperationResult],
    first_error: BaseException | None,
) -> tuple[_OperationResult, BaseException | None]:
    while True:
        try:
            return operation(), first_error
        except ChildStatusLostError:
            raise
        except KeyboardInterrupt as error:
            if first_error is None:
                first_error = error


def _remember_cleanup_error(
    first_error: BaseException | None,
    error: BaseException,
) -> BaseException:
    if isinstance(error, ChildStatusLostError):
        raise error
    return error if first_error is None else first_error


def _close_input(process: subprocess.Popen[bytes]) -> None:
    if process.stdin is not None and not process.stdin.closed:
        process.stdin.close()


def _write_input(process: subprocess.Popen[bytes], input_bytes: bytes) -> None:
    if process.stdin is None:
        raise ForegroundProcessError()
    remaining = memoryview(input_bytes)
    try:
        while remaining:
            written = process.stdin.write(remaining)
            if written is None or written <= 0:
                raise ForegroundProcessError()
            remaining = remaining[written:]
        _close_input(process)
    except BrokenPipeError:
        # Match subprocess.run: an early child exit is reported through its
        # owned return code, not as a synthetic input-delivery failure.
        try:
            _close_input(process)
        except BrokenPipeError:
            pass


def _signal_running_child(process: subprocess.Popen[bytes], signal_number: int) -> bool:
    try:
        wait_owned_child(process, 0)
        return False
    except subprocess.TimeoutExpired:
        pass
    try:
        os.kill(process.pid, signal_number)
    except ProcessLookupError:
        # A child that exited between the owned check and signal remains ours
        # to reap; do not ask Popen.poll(), which can hide ECHILD as success.
        wait_owned_child(process)
        return False
    return True


def _cleanup_owned_child(
    process: subprocess.Popen[bytes],
    cleanup_grace_seconds: float,
    terminate_grace_seconds: float,
) -> BaseException | None:
    first_error: BaseException | None = None
    try:
        _, first_error = _retry_cleanup_interrupts(
            lambda: _close_input(process),
            first_error,
        )
    except BaseException as error:
        first_error = _remember_cleanup_error(first_error, error)
    try:
        _, first_error = _retry_cleanup_interrupts(
            lambda: wait_owned_child(process, cleanup_grace_seconds),
            first_error,
        )
        return first_error
    except subprocess.TimeoutExpired:
        pass
    except BaseException as error:
        first_error = _remember_cleanup_error(first_error, error)
    term_sent: bool | None
    try:
        term_sent, first_error = _retry_cleanup_interrupts(
            lambda: _signal_running_child(process, signal.SIGTERM),
            first_error,
        )
    except BaseException as error:
        first_error = _remember_cleanup_error(first_error, error)
        term_sent = None
    if term_sent is False:
        return first_error
    if term_sent is True:
        try:
            _, first_error = _retry_cleanup_interrupts(
                lambda: wait_owned_child(process, terminate_grace_seconds),
                first_error,
            )
            return first_error
        except subprocess.TimeoutExpired:
            pass
        except BaseException as error:
            first_error = _remember_cleanup_error(first_error, error)
    kill_sent: bool | None
    try:
        kill_sent, first_error = _retry_cleanup_interrupts(
            lambda: _signal_running_child(process, signal.SIGKILL),
            first_error,
        )
    except BaseException as error:
        first_error = _remember_cleanup_error(first_error, error)
        kill_sent = None
    if kill_sent is False:
        return first_error
    try:
        _, first_error = _retry_cleanup_interrupts(
            lambda: wait_owned_child(process),
            first_error,
        )
    except BaseException as error:
        _remember_cleanup_error(first_error, error)
        raise
    return first_error


def run_owned_foreground(
    arguments: tuple[str, ...],
    input_bytes: bytes,
    *,
    cleanup_grace_seconds: float,
    terminate_grace_seconds: float,
) -> subprocess.CompletedProcess[bytes]:
    """Spawn once, inherit output, deliver exact input, and own the exit status."""
    process: subprocess.Popen[bytes] | None = None
    spawn_sigint = _DeferredSigint()
    try:
        spawn_sigint.arm()
        try:
            if not has_safe_child_status_context():
                raise ForegroundProcessError()
            process = subprocess.Popen(
                arguments,
                bufsize=0,
                close_fds=True,
                shell=False,
                stdin=subprocess.PIPE,
            )
        finally:
            spawn_sigint.restore_and_dispatch()
        _write_input(process, input_bytes)
        return_code = wait_owned_child(process)
    except BaseException as original_error:
        if process is None:
            raise
        cleanup_sigint = _DeferredSigint()
        cleanup_error: BaseException | None = None
        status_loss: ChildStatusLostError | None = None
        cleanup_sigint_armed = False
        try:
            try:
                cleanup_sigint.arm()
                cleanup_sigint_armed = True
            except BaseException as error:
                cleanup_error = error
            while True:
                try:
                    owned_cleanup_error = _cleanup_owned_child(
                        process,
                        cleanup_grace_seconds,
                        terminate_grace_seconds,
                    )
                    if cleanup_error is None or isinstance(owned_cleanup_error, KeyboardInterrupt):
                        cleanup_error = owned_cleanup_error
                    break
                except ChildStatusLostError as error:
                    status_loss = error
                    break
                except KeyboardInterrupt as error:
                    cleanup_error = error
                    if cleanup_sigint_armed:
                        break
                except BaseException as error:
                    if cleanup_error is None:
                        cleanup_error = error
                    break
        finally:
            if cleanup_sigint_armed:
                try:
                    cleanup_sigint.restore_and_dispatch()
                except BaseException as error:
                    if cleanup_error is None:
                        cleanup_error = error
        if status_loss is not None:
            raise status_loss from original_error
        if isinstance(original_error, KeyboardInterrupt):
            raise original_error from None
        if isinstance(cleanup_error, KeyboardInterrupt):
            raise cleanup_error from original_error
        raise original_error from None
    return subprocess.CompletedProcess(arguments, return_code)


def run_owned_foreground_redacted(invocation: RedactedSpawnInvocation) -> int:
    """Run one private invocation without returning task-bearing arguments."""
    completed = run_owned_foreground(
        invocation._arguments,
        invocation._input_bytes,
        cleanup_grace_seconds=invocation._cleanup_grace_seconds,
        terminate_grace_seconds=invocation._terminate_grace_seconds,
    )
    return completed.returncode
