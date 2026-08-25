#!/usr/bin/env python3
"""Owner-private locking and dispatch for installed advisory campaign lanes."""

from __future__ import annotations

import fcntl
import os
import stat
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING or __package__:
    from .advisory_campaign import (
        ANONYMOUS_LANE_COUNT,
        MAX_ANONYMOUS_LANES,
        CampaignError,
        campaign_record_error_code,
        lane_result_directories,
        load_manifest,
        load_merged_lane_records,
    )
    from .advisory_parallel import AdvisoryJob, AdvisoryResult, run_parallel
else:
    from advisory_campaign import (  # type: ignore[import-not-found]
        ANONYMOUS_LANE_COUNT,
        MAX_ANONYMOUS_LANES,
        CampaignError,
        campaign_record_error_code,
        lane_result_directories,
        load_manifest,
        load_merged_lane_records,
    )
    from advisory_parallel import (  # type: ignore[import-not-found]
        AdvisoryJob,
        AdvisoryResult,
        run_parallel,
    )


class LaneUnavailableError(ValueError):
    """Every bounded anonymous lane is currently leased."""


class CampaignCapacityError(ValueError):
    """The sealed campaign cannot admit another in-flight sample."""


class CampaignRecordsInvalidError(ValueError):
    """Existing records do not bind to the selected sealed campaign."""

    code: str

    def __init__(self, error: CampaignError) -> None:
        self.code = campaign_record_error_code(error)
        super().__init__(self.code)


@dataclass(frozen=True)
class LaneRequest:
    """Task-free request for one anonymous vendor/workflow lane."""

    vendor: str
    results_dir: Path
    lane_count: int = ANONYMOUS_LANE_COUNT
    workflow: str = "implementation"
    campaign_path: Path | None = None


@dataclass(frozen=True)
class CampaignJob:
    """One advisory job and its legacy or anonymous lane coordination."""

    job: AdvisoryJob
    lock_path: Path
    lane_request: LaneRequest | None = None


@dataclass(frozen=True)
class LaneLease:
    """An in-memory lease; its descriptor is intentionally not persisted."""

    vendor: str
    workflow: str
    lane_index: int
    results_dir: Path
    _descriptors: tuple[int, ...]


def _open_lane_lock(path: Path, *, blocking: bool = False) -> int:
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
        operation = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        fcntl.flock(descriptor, operation)
        return descriptor
    except (OSError, ValueError):
        if descriptor >= 0:
            os.close(descriptor)
        raise ValueError from None


def _private_directory(path: Path, *, create: bool) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if not create:
            raise ValueError from None
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError:
            raise ValueError from None
        try:
            metadata = path.lstat()
        except OSError:
            raise ValueError from None
    except OSError:
        raise ValueError from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise ValueError


def _prepare_lane_directories(request: LaneRequest) -> tuple[Path, ...]:
    if (
        not isinstance(request.vendor, str)
        or not request.vendor
        or "\x00" in request.vendor
        or not isinstance(request.workflow, str)
        or not request.workflow
        or "\x00" in request.workflow
        or not request.results_dir.is_absolute()
        or not isinstance(request.lane_count, int)
        or isinstance(request.lane_count, bool)
        or not 1 <= request.lane_count <= MAX_ANONYMOUS_LANES
        or (request.campaign_path is not None and not request.campaign_path.is_absolute())
    ):
        raise ValueError
    root = request.results_dir
    _private_directory(root.parent, create=False)
    _private_directory(root, create=True)
    lanes = lane_result_directories(root, request.lane_count)
    if len(lanes) > 1:
        _private_directory(lanes[1].parent, create=True)
        for lane in lanes[1:]:
            _private_directory(lane, create=True)
    return lanes


def _release_descriptors(descriptors: Sequence[int]) -> None:
    for descriptor in reversed(descriptors):
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


@contextmanager
def acquire_campaign_lanes(requests: Sequence[LaneRequest]) -> Iterator[tuple[LaneLease, ...]]:
    """Atomically lease one free anonymous lane per selected vendor/workflow.

    The short allocator lock protects the probe-and-acquire section. Lane lock
    descriptors remain open until the caller's complete run has ended, so a
    terminated owner releases its reservation through the operating system.
    """
    selected = tuple(requests)
    if not selected or len(selected) > MAX_ANONYMOUS_LANES:
        raise ValueError
    keys = [(request.vendor, request.workflow) for request in selected]
    if len(set(keys)) != len(keys):
        raise ValueError
    roots = [request.results_dir for request in selected]
    if len(set(roots)) != len(roots):
        raise ValueError
    allocator_descriptors: list[int] = []
    lane_descriptors: list[int] = []
    leases: list[LaneLease] = []
    try:
        prepared = {request.results_dir: _prepare_lane_directories(request) for request in selected}
        for root in sorted(prepared, key=os.fspath):
            allocator_descriptors.append(_open_lane_lock(root / ".allocator.lock", blocking=True))

        # All allocator locks are held while every lane is probed. No caller
        # can observe a partial allocation or take a lane between probes.
        for request in selected:
            paths = prepared[request.results_dir]
            available: list[tuple[tuple[int, ...], Path, int]] = []
            busy = 0
            for lane_index, results_dir in enumerate(paths):
                descriptors: list[int] = []
                try:
                    descriptors.append(_open_lane_lock(results_dir / ".lane.lock"))
                    if lane_index == 0:
                        descriptors.append(_open_lane_lock(results_dir / "dispatch.lock"))
                except ValueError:
                    _release_descriptors(descriptors)
                    busy += 1
                    continue
                available.append((tuple(descriptors), results_dir, lane_index))
            if request.campaign_path is not None:
                manifest = load_manifest(request.campaign_path)
                completed = len(
                    load_merged_lane_records(manifest, request.results_dir, request.lane_count)
                )
                if completed + busy >= manifest["max_tasks"]:
                    for held_descriptors, _, _ in available:
                        _release_descriptors(held_descriptors)
                    raise CampaignCapacityError
            if not available:
                raise LaneUnavailableError
            selected_descriptors, results_dir, lane_index = available.pop(0)
            for unused, _, _ in available:
                _release_descriptors(unused)
            lane_descriptors.extend(selected_descriptors)
            leases.append(
                LaneLease(
                    request.vendor,
                    request.workflow,
                    lane_index,
                    results_dir,
                    selected_descriptors,
                )
            )
    except (LaneUnavailableError, CampaignCapacityError):
        _release_descriptors(lane_descriptors)
        _release_descriptors(allocator_descriptors)
        raise
    except CampaignError as error:
        _release_descriptors(lane_descriptors)
        _release_descriptors(allocator_descriptors)
        raise CampaignRecordsInvalidError(error) from None
    except (OSError, ValueError):
        _release_descriptors(lane_descriptors)
        _release_descriptors(allocator_descriptors)
        raise ValueError from None
    else:
        _release_descriptors(allocator_descriptors)
        try:
            yield tuple(leases)
        finally:
            _release_descriptors(lane_descriptors)


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
    """Run jobs after atomically acquiring their requested anonymous lanes.

    The old dispatch-lock path remains available for callers that have not yet
    supplied lane requests. New machine shims should provide one request per
    vendor/workflow; those jobs have no shared live ordinal or output root.
    """

    selected = tuple(jobs)
    requested_lanes = tuple(job.lane_request for job in selected)
    if selected and all(request is not None for request in requested_lanes):
        requests = tuple(request for request in requested_lanes if request is not None)
        with acquire_campaign_lanes(requests) as leases:
            lane_jobs = tuple(
                AdvisoryJob(
                    job.job.label,
                    _command_with_output_dir(job.job.command, lease.results_dir),
                    job.job.timeout_seconds,
                    job.job.max_output_bytes,
                )
                for job, lease in zip(selected, leases, strict=True)
            )
            return run_parallel(lane_jobs)
    if any(request is not None for request in requested_lanes):
        raise ValueError
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


def _command_with_output_dir(command: tuple[str, ...], results_dir: Path) -> tuple[str, ...]:
    """Bind a runner command to its leased lane without inspecting task bytes."""
    values = list(command)
    for index, value in enumerate(values):
        if value == "--out-dir":
            if index + 1 >= len(values):
                raise ValueError
            values[index + 1] = os.fspath(results_dir)
            return tuple(values)
        if value.startswith("--out-dir="):
            values[index] = f"--out-dir={os.fspath(results_dir)}"
            return tuple(values)
    values.extend(("--out-dir", os.fspath(results_dir)))
    return tuple(values)
