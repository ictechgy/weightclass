"""Bounded, duplicate-safe JSON object loading for installed runtime inputs."""

import json
import os
import stat
from pathlib import Path
from typing import Any, Final

READ_CHUNK_BYTES: Final = 65_536


class JsonInputError(ValueError):
    """Raised without source values when a runtime JSON document is unsafe."""


class _DuplicateKeyError(ValueError):
    """Internal marker that deliberately carries no duplicated key."""


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError()
        result[key] = value
    return result


def _read_bounded_bytes(file_descriptor: int, max_bytes: int) -> bytes:
    contents = bytearray()
    while len(contents) <= max_bytes:
        try:
            chunk = os.read(
                file_descriptor,
                min(READ_CHUNK_BYTES, max_bytes + 1 - len(contents)),
            )
        except InterruptedError:
            continue
        if not chunk:
            return bytes(contents)
        contents.extend(chunk)
    raise JsonInputError()


def _load_json_object_from_open_fd(file_descriptor: int, *, max_bytes: int) -> dict[str, Any]:
    """Consume one opened descriptor and validate that exact file description."""
    try:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise JsonInputError()
        os.set_inheritable(file_descriptor, False)
        metadata = os.fstat(file_descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > max_bytes:
            raise JsonInputError()
        contents = _read_bounded_bytes(file_descriptor, max_bytes)
        decoded = contents.decode("utf-8")
        value = json.loads(decoded, object_pairs_hook=_object_without_duplicate_keys)
    except (
        JsonInputError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateKeyError,
    ):
        raise JsonInputError() from None
    finally:
        os.close(file_descriptor)

    if not isinstance(value, dict):
        raise JsonInputError()
    return value


def load_json_object(path: Path, *, max_bytes: int) -> dict[str, Any]:
    """Load one bounded regular-file JSON object without exposing its values."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise JsonInputError()

    flags = os.O_RDONLY | os.O_NONBLOCK
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        file_descriptor = os.open(path, flags)
    except OSError:
        raise JsonInputError() from None
    return _load_json_object_from_open_fd(file_descriptor, max_bytes=max_bytes)
