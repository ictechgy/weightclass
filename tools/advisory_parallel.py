#!/usr/bin/env python3
"""Run independent advisory campaign commands concurrently.

This module only coordinates top-level vendor commands. Each command remains
responsible for its own sequential advisory stages, locking, and evidence log.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

MAX_JOBS = 16
MAX_COMMAND_ARGUMENTS = 256
MAX_COMMAND_BYTES = 131_072
_LABEL = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_START_ERROR = b"advisory child start failed\n"


@dataclass(frozen=True)
class AdvisoryJob:
    """One exact, shell-free advisory command."""

    label: str
    command: tuple[str, ...]


@dataclass(frozen=True)
class AdvisoryResult:
    """Captured result for one advisory command."""

    label: str
    returncode: int
    stdout: bytes
    stderr: bytes
    started: bool


def _validate_jobs(jobs: tuple[AdvisoryJob, ...]) -> None:
    if not jobs or len(jobs) > MAX_JOBS:
        raise ValueError
    labels: set[str] = set()
    for job in jobs:
        if not isinstance(job, AdvisoryJob):
            raise ValueError
        if not _LABEL.fullmatch(job.label) or job.label in labels:
            raise ValueError
        labels.add(job.label)
        if not job.command or len(job.command) > MAX_COMMAND_ARGUMENTS:
            raise ValueError
        command_bytes = 0
        for index, argument in enumerate(job.command):
            if not isinstance(argument, str) or "\x00" in argument:
                raise ValueError
            if index == 0 and not argument:
                raise ValueError
            try:
                command_bytes += len(argument.encode("utf-8"))
            except UnicodeEncodeError as error:
                raise ValueError from error
        if command_bytes > MAX_COMMAND_BYTES:
            raise ValueError


def _run_job(job: AdvisoryJob) -> AdvisoryResult:
    try:
        completed = subprocess.run(
            job.command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )
    except OSError:
        return AdvisoryResult(job.label, 2, b"", _START_ERROR, False)
    return AdvisoryResult(
        job.label,
        completed.returncode,
        completed.stdout,
        completed.stderr,
        True,
    )


def run_parallel(jobs: Sequence[AdvisoryJob]) -> tuple[AdvisoryResult, ...]:
    """Run a validated batch concurrently and return results in input order."""

    selected = tuple(jobs)
    _validate_jobs(selected)
    with ThreadPoolExecutor(
        max_workers=len(selected), thread_name_prefix="wclass-advisory"
    ) as executor:
        futures = [executor.submit(_run_job, job) for job in selected]
        return tuple(future.result() for future in futures)
