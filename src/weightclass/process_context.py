"""Fail-closed native process-context checks shared by child owners."""

import ctypes
import os
import signal
import sys
from typing import Final

_DARWIN_SA_NOCLDWAIT: Final = 0x20
_LINUX_SA_NOCLDWAIT: Final = 0x02


class _DarwinSigaction(ctypes.Structure):
    _fields_ = [
        ("handler", ctypes.c_void_p),
        ("mask", ctypes.c_uint32),
        ("flags", ctypes.c_int),
    ]


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
