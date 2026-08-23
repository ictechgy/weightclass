#!/usr/bin/env python3
"""Repository-owned locking and dispatch for machine-local advisory shims."""

from __future__ import annotations

import fcntl
import os
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from advisory_parallel import AdvisoryJob, AdvisoryResult, run_parallel


@dataclass(frozen=True)
class CampaignJob:
    """One advisory job and its machine-local dispatch lock."""

    job: AdvisoryJob
    lock_path: Path


def _open_dispatch_lock(path: Path) -> int:
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags, 0o600)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise ValueError
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except (OSError, ValueError):
        if descriptor >= 0:
            os.close(descriptor)
        raise ValueError from None


def run_campaign_jobs(jobs: Sequence[CampaignJob]) -> tuple[AdvisoryResult, ...]:
    """Lock every selected campaign before starting any vendor process."""

    selected = tuple(jobs)
    lock_paths = [job.lock_path for job in selected]
    if (
        not selected
        or len(lock_paths) != len(set(lock_paths))
        or any(not path.is_absolute() for path in lock_paths)
    ):
        raise ValueError
    descriptors: list[int] = []
    try:
        for path in sorted(lock_paths, key=os.fspath):
            descriptors.append(_open_dispatch_lock(path))
        return run_parallel(tuple(job.job for job in selected))
    finally:
        for descriptor in reversed(descriptors):
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
