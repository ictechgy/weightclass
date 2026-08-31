"""Bounded, duplicate-safe JSON object loading for installed runtime inputs."""

import json
import os
import stat
from pathlib import Path
from typing import Any, Final

READ_CHUNK_BYTES: Final = 65_536
MAX_JSON_INTEGER_DIGITS: Final = 128


class JsonInputError(ValueError):
    """Raised without source values when a runtime JSON document is unsafe."""


class DuplicateJsonKeyError(ValueError):
    """Marker that deliberately carries no duplicated key."""


def json_object_pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object, rejecting any repeated key without naming it.

    `json.loads` keeps the last value for a repeated key, so a document can carry
    one value past review and a different value into use. This is the shared
    `object_pairs_hook` for every JSON document this package parses.
    """
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError()
        result[key] = value
    return result


def bounded_json_integer(value: str) -> int:
    """Reject pathological integer tokens before allocating a large integer."""

    digits = value[1:] if value.startswith("-") else value
    if not digits or len(digits) > MAX_JSON_INTEGER_DIGITS:
        raise ValueError
    return int(value)


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


def _has_exclusive_write_owner(metadata: os.stat_result) -> bool:
    """Report whether the opened file is closed to writers other than its owner.

    A policy, manifest, or descriptor decides which argv runs and which vendor
    boundary a task crosses, so whoever can write it can choose both. `route`
    and `run` read the file in separate processes, so a review of the first read
    says nothing about the second unless the set of writers is closed.

    Two conditions are rejected, both unambiguous on every supported platform:
    world-writable, and ownership by neither this user nor root. Root ownership
    is accepted because a root-writable file is already outside any boundary
    this tool could defend.

    Group-writable is deliberately **not** rejected, and that is a documented
    residual. Whether it is dangerous depends on the group: under the user
    private group convention the group holds only the owner and group-write is
    harmless, while a shared primary group such as Darwin's `staff` makes it
    equivalent to world-writable. Nothing in `stat` distinguishes the two —
    `grp` misses members who hold the group as their primary. Rejecting it
    outright would fail every file created under `umask 002`, which is a common
    default, so the check would fire on correct setups far more often than on
    dangerous ones. Keep a shared-group policy at `0o644`.

    The check runs on `fstat` of the already-open descriptor, not on the path,
    so nothing can be swapped between the check and the read.
    """
    if metadata.st_mode & stat.S_IWOTH:
        return False
    return metadata.st_uid in (os.geteuid(), 0)


def _load_json_object_from_open_fd(
    file_descriptor: int,
    *,
    max_bytes: int,
    require_exclusive_write_owner: bool = False,
) -> dict[str, Any]:
    """Consume one opened descriptor and validate that exact file description."""
    try:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise JsonInputError()
        os.set_inheritable(file_descriptor, False)
        metadata = os.fstat(file_descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > max_bytes:
            raise JsonInputError()
        if require_exclusive_write_owner and not _has_exclusive_write_owner(metadata):
            raise JsonInputError()
        contents = _read_bounded_bytes(file_descriptor, max_bytes)
        decoded = contents.decode("utf-8")
        value = json.loads(
            decoded,
            object_pairs_hook=json_object_pairs_without_duplicates,
            parse_int=bounded_json_integer,
        )
    except (OSError, RecursionError, ValueError):
        raise JsonInputError() from None
    finally:
        os.close(file_descriptor)

    if not isinstance(value, dict):
        raise JsonInputError()
    return value


def load_json_object(
    path: Path,
    *,
    max_bytes: int,
    require_exclusive_write_owner: bool = False,
) -> dict[str, Any]:
    """Load one bounded regular-file JSON object without exposing its values.

    Set `require_exclusive_write_owner` for any document that selects what gets
    executed; leave it off for package-owned resources, whose integrity is the
    installation's concern rather than this check's.
    """
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise JsonInputError()

    flags = os.O_RDONLY | os.O_NONBLOCK
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        file_descriptor = os.open(path, flags)
    except OSError:
        raise JsonInputError() from None
    return _load_json_object_from_open_fd(
        file_descriptor,
        max_bytes=max_bytes,
        require_exclusive_write_owner=require_exclusive_write_owner,
    )
