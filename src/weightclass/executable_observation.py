"""Admission and final-component observation for path-based spawn checks.

The admission rules reduce common writable-path exposure. They do not turn the
later path-based process creation into verified-object execution.
"""

import os
import stat
from dataclasses import dataclass

from .v2_validation import V2ValidationError, require_string


@dataclass(frozen=True, slots=True)
class ExecutableObservation:
    lexical_path: str
    st_dev: int
    st_ino: int
    file_type: int
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    executable_bit: bool


def _reject_public_ancestors(start: str) -> None:
    current = start
    while current and current != os.path.dirname(current):
        try:
            ancestor = os.lstat(current)
        except OSError as error:
            raise V2ValidationError() from error
        if (
            stat.S_ISDIR(ancestor.st_mode)
            and ancestor.st_mode & stat.S_IWOTH
            and not ancestor.st_mode & stat.S_ISVTX
        ):
            raise V2ValidationError()
        current = os.path.dirname(current)


def observe_executable(lexical_path: str) -> ExecutableObservation:
    require_string(lexical_path, max_bytes=4096)
    if not os.path.isabs(lexical_path):
        raise V2ValidationError()
    try:
        observed = os.lstat(lexical_path)
    except OSError as error:
        raise V2ValidationError() from error
    executable_bit = bool(observed.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    if (
        not stat.S_ISREG(observed.st_mode)
        or not executable_bit
        or observed.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise V2ValidationError()
    lexical_parent = os.path.abspath(os.path.dirname(lexical_path))
    resolved_parent = os.path.realpath(lexical_parent)
    _reject_public_ancestors(lexical_parent)
    if resolved_parent != lexical_parent:
        _reject_public_ancestors(resolved_parent)
    return ExecutableObservation(
        lexical_path=lexical_path,
        st_dev=observed.st_dev,
        st_ino=observed.st_ino,
        file_type=stat.S_IFMT(observed.st_mode),
        mode=observed.st_mode,
        size=observed.st_size,
        mtime_ns=observed.st_mtime_ns,
        ctime_ns=observed.st_ctime_ns,
        executable_bit=executable_bit,
    )
