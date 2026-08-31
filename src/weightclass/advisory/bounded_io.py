"""Shared no-follow bounded reads for advisory-owned regular files."""

from __future__ import annotations

import os
import stat
from pathlib import Path

READ_CHUNK_BYTES = 65_536


class BoundedFileError(ValueError):
    """Value-free rejection of an unsafe or oversized file."""


def read_regular_bytes(
    path: Path,
    maximum: int,
    *,
    require_nonempty: bool = False,
    require_current_owner: bool = False,
    require_private: bool = False,
) -> bytes:
    """Read one exact regular file without following its final component."""

    nofollow = getattr(os, "O_NOFOLLOW", None)
    if (
        not isinstance(nofollow, int)
        or nofollow == 0
        or isinstance(maximum, bool)
        or not isinstance(maximum, int)
        or maximum <= 0
    ):
        raise BoundedFileError()
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > maximum
            or (require_current_owner and metadata.st_uid != os.getuid())
            or (require_private and stat.S_IMODE(metadata.st_mode) & 0o077)
        ):
            raise BoundedFileError()
        payload = bytearray()
        while len(payload) <= maximum:
            try:
                chunk = os.read(
                    descriptor,
                    min(READ_CHUNK_BYTES, maximum + 1 - len(payload)),
                )
            except InterruptedError:
                continue
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > maximum or (require_nonempty and not payload):
            raise BoundedFileError()
        return bytes(payload)
    except BoundedFileError:
        raise
    except (OSError, TypeError, ValueError):
        raise BoundedFileError() from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
