#!/usr/bin/env python3
"""Run independent advisory campaign commands concurrently.

This module only coordinates top-level vendor commands. Each command remains
responsible for its own sequential advisory stages, locking, and evidence log.
"""

from __future__ import annotations

import os
import re
import selectors
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import BinaryIO, cast

MAX_JOBS = 16
MAX_COMMAND_ARGUMENTS = 256
MAX_COMMAND_BYTES = 131_072
# One speculative campaign may legitimately run several one-hour vendor
# children plus verification. Keep a finite outer ceiling without making the
# coordinator shorter than the bounded workflow it supervises.
DEFAULT_TIMEOUT_SECONDS = 28_800.0
DEFAULT_MAX_OUTPUT_BYTES = 1_048_576
MAX_TIMEOUT_SECONDS = 28_800.0
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
DEFAULT_HEARTBEAT_SECONDS = 60.0
_LABEL = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_START_ERROR = b"advisory child start failed\n"
_TIMEOUT_ERROR = b"advisory child timed out\n"


@dataclass(frozen=True)
class AdvisoryJob:
    """One exact, shell-free advisory command."""

    label: str
    command: tuple[str, ...]
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES


@dataclass(frozen=True)
class AdvisoryResult:
    """Captured result for one advisory command."""

    label: str
    returncode: int
    stdout: bytes
    stderr: bytes
    started: bool
    timed_out: bool = False
    output_truncated: bool = False


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
        if (
            isinstance(job.timeout_seconds, bool)
            or not isinstance(job.timeout_seconds, (int, float))
            or not 0 < job.timeout_seconds <= MAX_TIMEOUT_SECONDS
        ):
            raise ValueError
        if (
            isinstance(job.max_output_bytes, bool)
            or not isinstance(job.max_output_bytes, int)
            or not 0 < job.max_output_bytes <= MAX_OUTPUT_BYTES
        ):
            raise ValueError


def _signal_process_group(process: subprocess.Popen[bytes], signum: int) -> None:
    """Signal the isolated session, without exposing process details in errors."""

    try:
        os.killpg(process.pid, signum)
    except (OSError, ProcessLookupError):
        pass


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    """Terminate and then kill every process in one timed-out job's session."""

    _signal_process_group(process, signal.SIGTERM)
    # Do not reap the session leader before SIGKILL. While the unreaped PID
    # remains allocated, its process-group id cannot be recycled for an
    # unrelated process between the graceful and forced signals.
    time.sleep(0.1)
    _signal_process_group(process, signal.SIGKILL)
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        _signal_process_group(process, signal.SIGKILL)

        # An uninterruptible session leader may outlive the bounded timeout.
        # Keep one owner for its eventual status without blocking dispatch.
        def reap() -> None:
            try:
                process.wait()
            except (ChildProcessError, OSError):
                pass

        threading.Thread(target=reap, name="wclass-advisory-reaper", daemon=True).start()


def _capture_job(process: subprocess.Popen[bytes], job: AdvisoryJob) -> AdvisoryResult:
    """Drain both pipes to EOF while retaining only the combined output bound."""

    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    stdout_descriptor = process.stdout.fileno()
    stderr_descriptor = process.stderr.fileno()
    outputs = {stdout_descriptor: bytearray(), stderr_descriptor: bytearray()}
    truncated = False
    timed_out = False
    deadline = time.monotonic() + float(job.timeout_seconds)
    try:
        for stream in (process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                _terminate_process_group(process)
                break
            for key, _ in selector.select(remaining):
                descriptor = key.fd
                try:
                    chunk = os.read(descriptor, 65_536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    cast(BinaryIO, key.fileobj).close()
                    continue
                stored = outputs[descriptor]
                available = job.max_output_bytes - sum(len(value) for value in outputs.values())
                if available > 0:
                    stored.extend(chunk[:available])
                if len(chunk) > max(available, 0):
                    truncated = True
        if timed_out:
            for key in list(selector.get_map().values()):
                selector.unregister(key.fileobj)
                cast(BinaryIO, key.fileobj).close()
            return AdvisoryResult(
                job.label,
                124,
                b"",
                _TIMEOUT_ERROR,
                True,
                True,
                truncated,
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            _terminate_process_group(process)
            return AdvisoryResult(
                job.label,
                124,
                b"",
                _TIMEOUT_ERROR,
                True,
                True,
                truncated,
            )
        try:
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            _terminate_process_group(process)
            return AdvisoryResult(
                job.label,
                124,
                b"",
                _TIMEOUT_ERROR,
                True,
                True,
                truncated,
            )
        return AdvisoryResult(
            job.label,
            returncode,
            bytes(outputs[stdout_descriptor]),
            bytes(outputs[stderr_descriptor]),
            True,
            False,
            truncated,
        )
    finally:
        selector.close()


def _run_job(job: AdvisoryJob) -> AdvisoryResult:
    try:
        process = subprocess.Popen(
            job.command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            start_new_session=True,
        )
    except OSError:
        return AdvisoryResult(job.label, 2, b"", _START_ERROR, False)
    return _capture_job(process, job)


ProgressCallback = Callable[[str, str, int], None]


def _notify_progress(
    callback: ProgressCallback | None, label: str, event: str, elapsed_seconds: int
) -> None:
    if callback is None:
        return
    try:
        callback(label, event, elapsed_seconds)
    except (OSError, ValueError):
        pass


def run_parallel(
    jobs: Sequence[AdvisoryJob],
    *,
    progress: ProgressCallback | None = None,
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
) -> tuple[AdvisoryResult, ...]:
    """Run a validated batch concurrently and return results in input order."""

    selected = tuple(jobs)
    _validate_jobs(selected)
    if (
        isinstance(heartbeat_seconds, bool)
        or not isinstance(heartbeat_seconds, (int, float))
        or not 0 < heartbeat_seconds <= MAX_TIMEOUT_SECONDS
    ):
        raise ValueError
    started = time.monotonic()
    with ThreadPoolExecutor(
        max_workers=len(selected), thread_name_prefix="wclass-advisory"
    ) as executor:
        futures: list[Future[AdvisoryResult]] = [executor.submit(_run_job, job) for job in selected]
        indexes = {future: index for index, future in enumerate(futures)}
        pending = set(futures)
        results: list[AdvisoryResult | None] = [None] * len(selected)
        while pending:
            completed, pending = wait(
                pending,
                timeout=float(heartbeat_seconds),
                return_when=FIRST_COMPLETED,
            )
            elapsed = max(0, int(time.monotonic() - started))
            if not completed:
                for future in sorted(pending, key=indexes.__getitem__):
                    _notify_progress(
                        progress,
                        selected[indexes[future]].label,
                        "heartbeat",
                        elapsed,
                    )
                continue
            for future in sorted(completed, key=indexes.__getitem__):
                index = indexes[future]
                results[index] = future.result()
                _notify_progress(progress, selected[index].label, "completed", elapsed)
        if any(result is None for result in results):
            raise ValueError
        return tuple(cast(AdvisoryResult, result) for result in results)
