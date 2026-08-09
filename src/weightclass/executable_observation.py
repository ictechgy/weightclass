"""Final-component executable observation for future schema-2 spawn checks."""

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


def observe_executable(lexical_path: str) -> ExecutableObservation:
    require_string(lexical_path, max_bytes=4096)
    if not os.path.isabs(lexical_path):
        raise V2ValidationError()
    try:
        observed = os.lstat(lexical_path)
    except OSError as error:
        raise V2ValidationError() from error
    executable_bit = bool(observed.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    if not stat.S_ISREG(observed.st_mode) or not executable_bit:
        raise V2ValidationError()
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
