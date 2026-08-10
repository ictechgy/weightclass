"""Fail-closed native process-context checks shared by child owners."""

import ctypes
import errno
import os
import select
import signal
import sys
from typing import Any, Final

_DARWIN_SA_NOCLDWAIT: Final = 0x20
_LINUX_SA_NOCLDWAIT: Final = 0x02
_DARWIN_P_PID: Final = 1
_DARWIN_WNOHANG: Final = 0x01
_DARWIN_WEXITED: Final = 0x04
_DARWIN_WNOWAIT: Final = 0x20

# `open_leader_exit_queue` 가 "관찰 대상이 이미 종료했다"를 돌려줄 때 쓰는 표식.
# None(=waitid 를 쓰므로 큐가 필요 없음)과 반드시 구분되어야 하므로 별도 객체다.
LEADER_ALREADY_EXITED: Final = object()


class LeaderObserverError(ValueError):
    """Raised without pid detail when a leader-exit observer cannot be registered."""


class _DarwinSigaction(ctypes.Structure):
    _fields_ = [
        ("handler", ctypes.c_void_p),
        ("mask", ctypes.c_uint32),
        ("flags", ctypes.c_int),
    ]


class _DarwinSiginfoBuffer(ctypes.Structure):
    # Darwin's reviewed 64-bit siginfo_t is smaller than this aligned buffer.
    # No caller interprets it; waitid's return value is the ownership oracle,
    # so no field offsets are duplicated here.
    _fields_ = [("storage", ctypes.c_uint64 * 16)]


class _LinuxGlibcSigset(ctypes.Structure):
    # glibc uses a 128-byte sigset_t on the reviewed 64-bit x86/AArch64 ABIs.
    _fields_ = [("values", ctypes.c_ubyte * 128)]


class _LinuxGlibcSigaction(ctypes.Structure):
    _fields_ = [
        ("handler", ctypes.c_void_p),
        ("mask", _LinuxGlibcSigset),
        ("flags", ctypes.c_int),
        ("restorer", ctypes.c_void_p),
    ]


def _has_reviewed_linux_glibc_sigaction_layout() -> bool:
    return (
        ctypes.sizeof(_LinuxGlibcSigset) == 128
        and _LinuxGlibcSigaction.handler.offset == 0
        and _LinuxGlibcSigaction.mask.offset == 8
        and _LinuxGlibcSigaction.flags.offset == 136
        and _LinuxGlibcSigaction.restorer.offset == 144
        and ctypes.sizeof(_LinuxGlibcSigaction) == 152
    )


def has_safe_sigchld_disposition() -> bool:
    """Return whether a reviewed native SIGCHLD disposition preserves status.

    This is a bounded observation, not a lock. Callers still require exclusive
    child-status ownership throughout their invocation and must recheck next to
    process creation because concurrent native mutation remains possible.
    """
    sigchld = getattr(signal, "SIGCHLD", None)
    if sigchld is None:
        return False
    try:
        if signal.getsignal(sigchld) != signal.SIG_DFL:
            return False
    except (OSError, ValueError):
        return False
    if sys.platform == "darwin":
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            action = _DarwinSigaction()
            if libc.sigaction(sigchld, None, ctypes.byref(action)) != 0:
                return False
        except (AttributeError, ctypes.ArgumentError, OSError, TypeError, ValueError):
            return False
        return action.handler in (None, 0) and not action.flags & _DARWIN_SA_NOCLDWAIT
    if not sys.platform.startswith("linux"):
        return False
    try:
        machine = os.uname().machine.lower()
        if machine not in ("aarch64", "amd64", "arm64", "x86_64"):
            return False
        if ctypes.sizeof(ctypes.c_void_p) != 8 or not _has_reviewed_linux_glibc_sigaction_layout():
            return False
        libc = ctypes.CDLL(None, use_errno=True)
        get_libc_version = getattr(libc, "gnu_get_libc_version", None)
        if not callable(get_libc_version):
            return False
        get_libc_version.argtypes = []
        get_libc_version.restype = ctypes.c_char_p
        libc_version = get_libc_version()
        if not isinstance(libc_version, bytes) or not libc_version:
            return False
        action = _LinuxGlibcSigaction()
        if libc.sigaction(sigchld, None, ctypes.byref(action)) != 0:
            return False
    except (AttributeError, ctypes.ArgumentError, OSError, TypeError, ValueError):
        return False
    return action.handler in (None, 0) and not action.flags & _LINUX_SA_NOCLDWAIT


def has_leader_exit_observer() -> bool:
    """Return whether this POSIX runtime can observe exit without reaping."""
    if callable(getattr(os, "waitid", None)):
        return all(hasattr(os, name) for name in ("P_PID", "WEXITED", "WNOHANG", "WNOWAIT"))
    return all(
        hasattr(select, name)
        for name in (
            "kqueue",
            "kevent",
            "KQ_FILTER_PROC",
            "KQ_EV_ADD",
            "KQ_EV_ONESHOT",
            "KQ_NOTE_EXIT",
        )
    )


def darwin_child_status_waitable(pid: int) -> bool:
    """Check Darwin child-status ownership once without consuming the status."""
    if (
        sys.platform != "darwin"
        or ctypes.sizeof(ctypes.c_void_p) != 8
        or pid <= 0
        or pid > 0xFFFFFFFF
    ):
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        waitid = libc.waitid
        waitid.argtypes = [ctypes.c_int, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int]
        waitid.restype = ctypes.c_int
        status = _DarwinSiginfoBuffer()
        ctypes.set_errno(0)
        result = waitid(
            _DARWIN_P_PID,
            pid,
            ctypes.byref(status),
            _DARWIN_WEXITED | _DARWIN_WNOHANG | _DARWIN_WNOWAIT,
        )
    except (
        AttributeError,
        ctypes.ArgumentError,
        OSError,
        OverflowError,
        TypeError,
        ValueError,
    ):
        return False
    # WNOHANG makes this a single bounded native call. ECHILD and every other
    # failure leave ownership unproved and therefore release all signal targets.
    return bool(result == 0)


def open_leader_exit_queue(pid: int) -> Any | None:
    """Register a non-reaping kqueue observer when ``waitid`` is unavailable.

    Returns ``None`` when ``waitid`` serves the observation directly, the queue
    when registration succeeds, and :data:`LEADER_ALREADY_EXITED` when Darwin
    refuses registration only because the leader has already exited while its
    wait status and process-group anchor remain owned.

    Raises ``ChildProcessError`` when child-status ownership is provably lost
    and :class:`LeaderObserverError` for every other registration failure.
    """
    if callable(getattr(os, "waitid", None)):
        return None
    exit_queue: Any | None = None
    try:
        select_features = vars(select)
        event = select_features["kevent"](
            pid,
            filter=select_features["KQ_FILTER_PROC"],
            flags=select_features["KQ_EV_ADD"] | select_features["KQ_EV_ONESHOT"],
            fflags=select_features["KQ_NOTE_EXIT"],
        )
        exit_queue = select_features["kqueue"]()
        exit_queue.control([event], 0, 0)
    except OSError as error:
        if exit_queue is not None:
            exit_queue.close()
        if isinstance(error, ChildProcessError) or error.errno == errno.ECHILD:
            raise ChildProcessError() from None
        if isinstance(error, ProcessLookupError) or error.errno == errno.ESRCH:
            if not darwin_child_status_waitable(pid):
                raise ChildProcessError() from None
            # Darwin rejects EVFILT_PROC registration for a child that already
            # exited even while its wait status and PGID anchor remain owned.
            return LEADER_ALREADY_EXITED
        raise LeaderObserverError() from None
    except ValueError:
        if exit_queue is not None:
            exit_queue.close()
        raise LeaderObserverError() from None
    return exit_queue


def observe_leader_exit(pid: int, exit_queue: Any | None) -> bool:
    """Observe leader exit without releasing its process-group identity."""
    if exit_queue is LEADER_ALREADY_EXITED:
        return True
    waitid = getattr(os, "waitid", None)
    if not callable(waitid):
        assert exit_queue is not None
        while True:
            try:
                return bool(exit_queue.control(None, 1, 0))
            except InterruptedError:
                continue
    while True:
        try:
            result = waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except InterruptedError:
            continue
        return result is not None


def close_leader_exit_queue(exit_queue: Any | None) -> None:
    """Release an observer handle, tolerating ``None`` and the exited sentinel."""
    if exit_queue is None or exit_queue is LEADER_ALREADY_EXITED:
        return
    try:
        exit_queue.close()
    except OSError:
        pass


def signal_process_group(process_group_id: int, signal_number: int) -> None:
    """Signal only the captured process group.

    macOS can report EPERM when only an unreaped zombie leader remains in the
    session, so both refusals are treated as "nothing left to signal".
    """
    try:
        os.killpg(process_group_id, signal_number)
    except (PermissionError, ProcessLookupError):
        pass
