"""Test-only runner-direct evidence collector for synthetic probes."""

import json
import math
import os
import select
import subprocess
import time
from collections.abc import Iterator, Mapping, Sequence
from typing import Final, cast

from tests.synthetic_probe_protocol import PROBE_PROTOCOL_ID

MAX_FRAME_BYTES: Final = 1_024
MAX_TIMEOUT_SECONDS: Final = 5.0
MAX_TRAFFIC_BYTES: Final = 4_096
_EXPECTED_IDS: Final = (
    "wcp-selftest/v1/child-start",
    "wcp-selftest/v1/direct-child-exit",
)
_FRAME_FIELDS: Final = {"probe_protocol_id", "self_test_id", "sequence"}
_UNTRUSTED_CHILD_CHANNELS: Final = (
    "frame-payload",
    "self-attestation",
    "stdout",
    "telemetry",
)
_STOP_GRACE_SECONDS: Final = 0.2
_RUNNER_RESULT_TOKEN: Final = object()


class _DuplicateKeyError(ValueError):
    pass


class _RunnerDirectProbeResult(Mapping[str, object]):
    """Immutable result created only through the runner-owned observation path."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, object], *, token: object) -> None:
        if token is not _RUNNER_RESULT_TOKEN:
            raise TypeError("runner result construction is private")
        self._values = dict(values)

    def __getitem__(self, key: str) -> object:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


def is_runner_direct_probe_result(value: object) -> bool:
    """Return whether an observation came from the runner-owned result path."""
    return isinstance(value, _RunnerDirectProbeResult)


def _runner_result(values: Mapping[str, object]) -> _RunnerDirectProbeResult:
    return _RunnerDirectProbeResult(values, token=_RUNNER_RESULT_TOKEN)


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKeyError
        value[key] = item
    return value


def _poll_direct_child(child: subprocess.Popen[bytes]) -> int | None:
    try:
        return child.poll()
    except OSError:
        return None


def _stop_direct_child(child: subprocess.Popen[bytes]) -> tuple[int | None, bool]:
    for stop in (child.terminate, child.kill):
        try:
            stop()
        except OSError:
            pass
        try:
            return child.wait(timeout=_STOP_GRACE_SECONDS), True
        except subprocess.TimeoutExpired:
            continue
        except OSError:
            status = _poll_direct_child(child)
            if status is not None:
                return status, True
    status = _poll_direct_child(child)
    return status, status is not None


def _invalid_result(
    argv: tuple[str, ...],
    *,
    pid: int | None,
    exit_status: int | None,
    timed_out: bool,
    diagnostic: str,
) -> Mapping[str, object]:
    return _runner_result(
        {
            "child_pid": pid,
            "child_started": pid is not None,
            "diagnostic": diagnostic,
            "exit_status": exit_status,
            "frames": [],
            "path_execution_identity": "TOCTOU-UNRESOLVED",
            "selected_argv": list(argv),
            "timed_out": timed_out,
            "untrusted_child_assertion_policy": "rejected-non-decisive",
            "untrusted_child_channels": list(_UNTRUSTED_CHILD_CHANNELS),
            "untrusted_stdout_policy": "discarded-non-decisive",
        }
    )


def _parse_frames(encoded: bytes) -> list[dict[str, object]]:
    if not encoded or len(encoded) > MAX_TRAFFIC_BYTES:
        raise ValueError
    offset = 0
    parsed: list[dict[str, object]] = []
    while offset < len(encoded):
        if len(encoded) - offset < 4:
            raise ValueError
        size = int.from_bytes(encoded[offset : offset + 4], "big")
        offset += 4
        if size < 1 or size > MAX_FRAME_BYTES or len(encoded) - offset < size:
            raise ValueError
        payload = encoded[offset : offset + size]
        offset += size
        try:
            value = json.loads(
                payload.decode("utf-8", errors="strict"),
                object_pairs_hook=_object_without_duplicate_keys,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateKeyError):
            raise ValueError from None
        if not isinstance(value, dict) or set(value) != _FRAME_FIELDS:
            raise ValueError
        parsed.append(cast(dict[str, object], value))
    if len(parsed) != len(_EXPECTED_IDS):
        raise ValueError
    for sequence, (value, expected_id) in enumerate(zip(parsed, _EXPECTED_IDS, strict=True)):
        if (
            value["probe_protocol_id"] != PROBE_PROTOCOL_ID
            or value["self_test_id"] != expected_id
            or not isinstance(value["sequence"], int)
            or isinstance(value["sequence"], bool)
            or value["sequence"] != sequence
        ):
            raise ValueError
    return [
        {
            "payload_assertion_trust": "untrusted-child",
            "self_test_id": expected_id,
            "sequence": sequence,
            "traffic_observation_provenance": "runner-direct",
        }
        for sequence, expected_id in enumerate(_EXPECTED_IDS)
    ]


def _collect_traffic(read_fd: int, *, deadline: float) -> tuple[bytes | None, str | None, bool]:
    traffic = bytearray()
    try:
        os.set_blocking(read_fd, False)
    except OSError:
        return None, "probe_pipe_failed", False
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None, "probe_writer_retained", True
        try:
            readable, _, _ = select.select((read_fd,), (), (), remaining)
        except InterruptedError:
            continue
        except OSError:
            return None, "probe_pipe_failed", False
        if not readable:
            return None, "probe_writer_retained", True
        try:
            chunk = os.read(read_fd, MAX_TRAFFIC_BYTES + 1 - len(traffic))
        except (BlockingIOError, InterruptedError):
            continue
        except OSError:
            return None, "probe_pipe_failed", False
        if not chunk:
            return bytes(traffic), None, False
        traffic.extend(chunk)
        if len(traffic) > MAX_TRAFFIC_BYTES:
            return None, "probe_protocol_invalid", False


def run_synthetic_probe(
    selected_argv: Sequence[str], *, timeout_seconds: float
) -> Mapping[str, object]:
    """Run one synthetic child and collect only direct process/FD observations."""
    argv = tuple(selected_argv)
    if (
        not argv
        or any(not isinstance(item, str) or not item or "\x00" in item for item in argv)
        or isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
        or timeout_seconds > MAX_TIMEOUT_SECONDS
    ):
        return _invalid_result(
            argv,
            pid=None,
            exit_status=None,
            timed_out=False,
            diagnostic="probe_invalid_input",
        )
    deadline = time.monotonic() + float(timeout_seconds)
    try:
        read_fd, write_fd = os.pipe()
    except OSError:
        return _invalid_result(
            argv,
            pid=None,
            exit_status=None,
            timed_out=False,
            diagnostic="probe_pipe_failed",
        )
    child: subprocess.Popen[bytes] | None = None
    try:
        environment = {"WCP_SYNTHETIC_FRAME_FD": str(write_fd)}
        child = subprocess.Popen(
            argv,
            env=environment,
            pass_fds=(write_fd,),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os.close(write_fd)
        write_fd = -1
        try:
            child.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            exit_status, stopped = _stop_direct_child(child)
            return _invalid_result(
                argv,
                pid=child.pid,
                exit_status=exit_status,
                timed_out=True,
                diagnostic="probe_timeout" if stopped else "probe_stop_failed",
            )
        traffic, collection_diagnostic, collection_timed_out = _collect_traffic(
            read_fd, deadline=deadline
        )
        if collection_diagnostic is not None or traffic is None:
            return _invalid_result(
                argv,
                pid=child.pid,
                exit_status=child.returncode,
                timed_out=collection_timed_out,
                diagnostic=collection_diagnostic or "probe_pipe_failed",
            )
        try:
            frames = _parse_frames(traffic)
        except ValueError:
            return _invalid_result(
                argv,
                pid=child.pid,
                exit_status=child.returncode,
                timed_out=False,
                diagnostic="probe_protocol_invalid",
            )
        return _runner_result(
            {
                "child_pid": child.pid,
                "child_started": True,
                "diagnostic": "ok" if child.returncode == 0 else "probe_child_failed",
                "exit_status": child.returncode,
                "frames": frames if child.returncode == 0 else [],
                "path_execution_identity": "TOCTOU-UNRESOLVED",
                "selected_argv": list(argv),
                "timed_out": False,
                "untrusted_child_assertion_policy": "rejected-non-decisive",
                "untrusted_child_channels": list(_UNTRUSTED_CHILD_CHANNELS),
                "untrusted_stdout_policy": "discarded-non-decisive",
            }
        )
    except (OSError, ValueError):
        return _invalid_result(
            argv,
            pid=None,
            exit_status=None,
            timed_out=False,
            diagnostic="probe_start_failed",
        )
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)
        if child is not None and _poll_direct_child(child) is None:
            _stop_direct_child(child)
