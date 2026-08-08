"""Maintainer-only black-box conformance runner for delegation runtimes.

The runner has no runtime-task input and never updates the package qualification
registry. A separately reviewed adapter-specific driver receives fixed synthetic
case descriptors and returns bounded case verdicts. The resulting evidence is
still untrusted until the driver, runtime artifact, and run are independently
reviewed.
"""

import argparse
import json
import os
import re
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any, BinaryIO, Final, Literal, NoReturn, cast

from .delegation_compile import canonical_json_bytes
from .delegation_qualification import (
    QualificationInvalidInputError,
    qualification_artifact_identity,
)
from .delegation_schema import (
    DelegationInvalidInputError,
    DelegationUnsupportedError,
    current_platform_contract,
    validate_runtime_path_lexically,
)
from .delegation_types import VendorFamily

DRIVER_PROTOCOL_VERSION: Final = 1
EVIDENCE_SCHEMA_VERSION: Final = 2
RUNTIME_PROTOCOL_VERSION: Final = 1
SUITE_REVISION: Final = "delegation-conformance-v2"
CASE_TIMEOUT_SECONDS: Final = 60.0
MAX_CASE_TIMEOUT_SECONDS: Final = 300.0
MAX_DRIVER_OUTPUT_BYTES: Final = 4_096
MAX_REQUEST_BYTES: Final = 32_768
GROUP_CLEANUP_TIMEOUT_SECONDS: Final = 1.0
DRIVER_ARGUMENTS: Final = ("--weightclass-conformance-driver", "1")

Role = Literal["orchestrator", "worker", "reviewer"]
Category = Literal["implementation", "tests", "documentation"]
Action = Literal["workspace_read", "workspace_write", "command_execution"]
Mode = Literal["allow", "deny"]

# These expectations are deliberately owned by the harness instead of imported
# from the candidate validator. A future suite revision must update and review
# both sides rather than letting one implementation define its own proof.
ROLES: Final[tuple[Role, ...]] = ("orchestrator", "worker", "reviewer")
CATEGORIES: Final[tuple[Category, ...]] = ("implementation", "tests", "documentation")
ACTIONS: Final[tuple[Action, ...]] = (
    "workspace_read",
    "workspace_write",
    "command_execution",
)
MODES: Final[tuple[Mode, ...]] = ("allow", "deny")
SCENARIOS: Final = (
    "action_attribution",
    "artifact_integrity_and_substitution",
    "descendant_cleanup",
    "descendant_leakage",
    "distinct_enforcement_contexts",
    "integration_restriction",
    "integration_verification_commands",
    "output_channel_separation",
    "process_creation_attribution",
    "reviewer_rejection",
    "runtime_deadline",
    "stage_order",
    "worker_concurrency_bound",
)

_IDENTIFIER_PATTERN: Final = re.compile(r"[a-z][a-z0-9._-]{0,63}\Z")


class ConformanceInvalidInputError(ValueError):
    """Raised without source values for unsafe harness input."""


class _DuplicateKeyError(ValueError):
    pass


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        raise ConformanceInvalidInputError()


@dataclass(frozen=True)
class ConformanceCase:
    case_id: str
    case: dict[str, str]
    role: Role | None = None
    category: Category | None = None
    action: Action | None = None
    mode: Mode | None = None
    scenario_id: str | None = None


@dataclass
class _ExchangeState:
    output: bytearray
    overflow: bool = False
    write_failed: bool = False


@dataclass
class _DeferredSigint:
    """Defer SIGINT without leaving the exec'd driver with a blocked mask."""

    previous_handler: Any = None
    received_frame: FrameType | None = None
    process_group_id: int | None = None
    active: bool = False
    received: bool = False

    def _handle(self, signal_number: int, frame: FrameType | None) -> None:
        del signal_number
        if self.previous_handler == signal.SIG_IGN:
            return
        self.received = True
        self.received_frame = frame
        if self.process_group_id is not None:
            _signal_process_group(self.process_group_id, signal.SIGKILL)

    def arm(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return
        try:
            self.previous_handler = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, self._handle)
        except (OSError, ValueError):
            return
        self.active = True

    def record_process_group(self, process_group_id: int) -> None:
        self.process_group_id = process_group_id
        if self.received:
            _signal_process_group(process_group_id, signal.SIGKILL)

    def clear_process_group(self) -> None:
        self.process_group_id = None

    def restore(self) -> None:
        if not self.active:
            return
        previous_handler = self.previous_handler
        signal.signal(signal.SIGINT, previous_handler)
        self.active = False
        received = self.received
        received_frame = self.received_frame
        if not received or previous_handler == signal.SIG_IGN:
            return
        if previous_handler == signal.SIG_DFL:
            signal.raise_signal(signal.SIGINT)
            return
        if callable(previous_handler):
            previous_handler(signal.SIGINT, received_frame)


class _ReaderSignalTarget:
    """Serialize reader signals with release of the owned process group."""

    def __init__(self, process_group_id: int) -> None:
        self._process_group_id: int | None = process_group_id
        self._lock = threading.Lock()

    def signal(self, signal_number: int) -> None:
        with self._lock:
            if self._process_group_id is not None:
                _signal_process_group(self._process_group_id, signal_number)

    def clear(self) -> None:
        with self._lock:
            self._process_group_id = None


@dataclass
class _DriverCaseOwnership:
    """Own one driver session and make its complete cleanup idempotent."""

    process: subprocess.Popen[bytes] | None = None
    process_group_id: int | None = None
    reader_signal_target: _ReaderSignalTarget | None = None
    writer: threading.Thread | None = None
    reader: threading.Thread | None = None
    exit_queue: Any | None = None
    return_code: int | None = None
    exchange_incomplete: bool = False
    group_cleanup_incomplete: bool = False
    cleaned: bool = False
    before_final_reap: Callable[[], None] | None = None

    def record_process(self, process: subprocess.Popen[bytes]) -> None:
        self.process = process
        self.process_group_id = process.pid
        self.reader_signal_target = _ReaderSignalTarget(process.pid)

    @staticmethod
    def _thread_is_alive(thread: threading.Thread | None) -> bool:
        return thread is not None and thread.is_alive()

    def cleanup(self) -> None:
        if self.cleaned:
            return
        process = self.process
        if self.process_group_id is not None:
            _signal_process_group(self.process_group_id, signal.SIGKILL)
        if process is not None:
            try:
                os.kill(process.pid, signal.SIGKILL)
            except (PermissionError, ProcessLookupError):
                pass

        if self.process_group_id is not None:
            cleanup_deadline = time.monotonic() + GROUP_CLEANUP_TIMEOUT_SECONDS
            while _process_group_exists(self.process_group_id):
                if time.monotonic() >= cleanup_deadline:
                    self.group_cleanup_incomplete = True
                    break
                time.sleep(0.005)

        if self.exit_queue is not None:
            try:
                self.exit_queue.close()
            except OSError:
                pass

        for thread in (self.writer, self.reader):
            if self._thread_is_alive(thread):
                assert thread is not None
                thread.join(timeout=1)

        if process is not None:
            for stream in (process.stdout, process.stdin):
                if stream is not None and not stream.closed:
                    try:
                        stream.close()
                    except (OSError, ValueError):
                        pass

        for thread in (self.writer, self.reader):
            if self._thread_is_alive(thread):
                assert thread is not None
                thread.join(timeout=1)
        self.exchange_incomplete = self._thread_is_alive(self.writer) or self._thread_is_alive(
            self.reader
        )

        if self.reader_signal_target is not None:
            self.reader_signal_target.clear()
        if self.before_final_reap is not None:
            self.before_final_reap()
        self.process_group_id = None

        if process is not None:
            try:
                self.return_code = _wait_after_kill(process)
            except (ChildProcessError, OSError, ValueError):
                self.return_code = process.returncode
        self.cleaned = True


def _permission_cases() -> tuple[ConformanceCase, ...]:
    return tuple(
        ConformanceCase(
            case_id=f"matrix/{role}/{category}/{action}/{mode}",
            case={
                "kind": "permission",
                "role": role,
                "category": category,
                "action": action,
                "mode": mode,
            },
            role=role,
            category=category,
            action=action,
            mode=mode,
        )
        for role in ROLES
        for category in CATEGORIES
        for action in ACTIONS
        for mode in MODES
    )


def _scenario_cases() -> tuple[ConformanceCase, ...]:
    return tuple(
        ConformanceCase(
            case_id=f"scenario/{scenario_id}",
            case={"kind": "scenario", "id": scenario_id},
            scenario_id=scenario_id,
        )
        for scenario_id in SCENARIOS
    )


CONFORMANCE_CASES: Final = _permission_cases() + _scenario_cases()


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKeyError()
        value[key] = item
    return value


def _parse_driver_response(contents: bytes, expected_case_id: str) -> bool:
    try:
        decoded = contents.decode("utf-8")
        value = json.loads(decoded, object_pairs_hook=_object_without_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateKeyError):
        return False
    return (
        isinstance(value, dict)
        and set(value) == {"case_id", "passed"}
        and value["case_id"] == expected_case_id
        and isinstance(value["passed"], bool)
        and value["passed"]
    )


def _signal_process_group(process_group_id: int, signal_number: int) -> None:
    try:
        os.killpg(process_group_id, signal_number)
    except (PermissionError, ProcessLookupError):
        pass


def _linux_proc_stat_live_group_member(
    stat_contents: bytes,
    process_group_id: int,
) -> bool | None:
    closing_parenthesis = stat_contents.rfind(b") ")
    fields = stat_contents[closing_parenthesis + 2 :].split()
    if closing_parenthesis < 0 or len(fields) < 3 or len(stat_contents) > 4_096:
        return None
    try:
        member_process_group_id = int(fields[2])
    except ValueError:
        return None
    return member_process_group_id == process_group_id and fields[0] not in (b"Z", b"X", b"x")


def _linux_process_group_exists(process_group_id: int) -> bool:
    try:
        entries = os.scandir("/proc")
    except OSError:
        return True
    with entries:
        for entry in entries:
            if not entry.name.isascii() or not entry.name.isdecimal():
                continue
            try:
                with open(f"/proc/{entry.name}/stat", "rb", buffering=0) as stat_file:
                    stat_contents = stat_file.read(4_097)
            except (FileNotFoundError, ProcessLookupError):
                continue
            except OSError:
                return True
            is_live_member = _linux_proc_stat_live_group_member(
                stat_contents,
                process_group_id,
            )
            if is_live_member is None:
                return True
            if is_live_member:
                return True
    return False


def _process_group_exists(process_group_id: int) -> bool:
    """Return whether the anchored group still has a signalable live member."""
    if sys.platform.startswith("linux"):
        return _linux_process_group_exists(process_group_id)
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # macOS reports EPERM when only an unreaped zombie session leader
        # remains. Reviewed drivers and their descendants run as this user, so
        # a live member of the owned group remains signalable.
        return False
    return True


def _has_leader_exit_observer() -> bool:
    if callable(getattr(os, "waitid", None)):
        return all(hasattr(os, name) for name in ("P_PID", "WEXITED", "WNOHANG", "WNOWAIT"))
    return all(
        hasattr(select, name)
        for name in (
            "kqueue",
            "kevent",
            "KQ_FILTER_PROC",
            "KQ_EV_ADD",
            "KQ_EV_ONESHOT",
            "KQ_NOTE_EXIT",
        )
    )


def _has_safe_sigchld_disposition() -> bool:
    sigchld = getattr(signal, "SIGCHLD", None)
    if sigchld is None:
        return False
    try:
        return signal.getsignal(sigchld) == signal.SIG_DFL
    except (OSError, ValueError):
        return False


def _open_leader_exit_queue(pid: int) -> Any | None:
    """Register a non-reaping kqueue observer when waitid is unavailable."""
    if callable(getattr(os, "waitid", None)):
        return None
    exit_queue: Any | None = None
    try:
        select_features = vars(select)
        event = select_features["kevent"](
            pid,
            filter=select_features["KQ_FILTER_PROC"],
            flags=select_features["KQ_EV_ADD"] | select_features["KQ_EV_ONESHOT"],
            fflags=select_features["KQ_NOTE_EXIT"],
        )
        exit_queue = select_features["kqueue"]()
        exit_queue.control([event], 0, 0)
    except (OSError, ValueError):
        if exit_queue is not None:
            exit_queue.close()
        raise ConformanceInvalidInputError() from None
    return exit_queue


def _observe_leader_exit(pid: int, exit_queue: Any | None) -> bool:
    """Observe leader exit without releasing its process-group identity."""
    waitid = getattr(os, "waitid", None)
    if not callable(waitid):
        assert exit_queue is not None
        while True:
            try:
                return bool(exit_queue.control(None, 1, 0))
            except InterruptedError:
                continue
    while True:
        try:
            result = waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except InterruptedError:
            continue
        return result is not None


def _wait_for_leader_exit(pid: int, exit_queue: Any | None, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while True:
        if _observe_leader_exit(pid, exit_queue):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.01, remaining))


def _write_request(stream: BinaryIO, request: bytes, state: _ExchangeState) -> None:
    remaining = memoryview(request)
    try:
        while remaining:
            try:
                written = os.write(stream.fileno(), remaining)
            except InterruptedError:
                continue
            if written <= 0:
                state.write_failed = True
                break
            remaining = remaining[written:]
    except (OSError, ValueError):
        state.write_failed = True
    finally:
        try:
            stream.close()
        except OSError:
            state.write_failed = True


def _read_response(
    stream: BinaryIO,
    signal_target: _ReaderSignalTarget,
    state: _ExchangeState,
) -> None:
    try:
        while len(state.output) <= MAX_DRIVER_OUTPUT_BYTES:
            remaining = MAX_DRIVER_OUTPUT_BYTES + 1 - len(state.output)
            try:
                chunk = os.read(stream.fileno(), min(65_536, remaining))
            except InterruptedError:
                continue
            if not chunk:
                return
            state.output.extend(chunk)
        state.overflow = True
        signal_target.signal(signal.SIGKILL)
    except (OSError, ValueError):
        state.overflow = True


def _wait_after_kill(process: subprocess.Popen[bytes]) -> int:
    while True:
        try:
            return process.wait()
        except InterruptedError:
            continue


def _run_driver_case(
    driver_path: Path,
    runtime_path: Path,
    conformance_case: ConformanceCase,
    workspace_path: Path,
    *,
    timeout_seconds: float,
) -> bool:
    if not _has_safe_sigchld_disposition():
        return False
    request = canonical_json_bytes(
        {
            "case": conformance_case.case,
            "case_id": conformance_case.case_id,
            "driver_protocol_version": DRIVER_PROTOCOL_VERSION,
            "runtime_path": str(runtime_path),
            "workspace_path": str(workspace_path),
        }
    )
    if len(request) > MAX_REQUEST_BYTES:
        return False
    ownership = _DriverCaseOwnership()
    deferred_sigint = _DeferredSigint()
    ownership.before_final_reap = deferred_sigint.clear_process_group
    state = _ExchangeState(bytearray())
    timed_out = False
    descendant_leaked = False
    deferred_sigint.arm()
    try:
        try:
            process = subprocess.Popen(
                (str(driver_path), *DRIVER_ARGUMENTS),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=workspace_path,
                close_fds=True,
                start_new_session=True,
            )
        except (OSError, ValueError):
            return False
        ownership.record_process(process)
        ownership.exit_queue = _open_leader_exit_queue(process.pid)
        deferred_sigint.record_process_group(process.pid)

        if not deferred_sigint.received:
            assert process.stdin is not None
            assert process.stdout is not None
            assert ownership.reader_signal_target is not None
            ownership.writer = threading.Thread(
                target=_write_request,
                args=(process.stdin, request, state),
                daemon=True,
            )
            ownership.reader = threading.Thread(
                target=_read_response,
                args=(process.stdout, ownership.reader_signal_target, state),
                daemon=True,
            )
            ownership.writer.start()
            ownership.reader.start()
            try:
                leader_observed = _wait_for_leader_exit(
                    process.pid,
                    ownership.exit_queue,
                    timeout_seconds,
                )
            except (ChildProcessError, OSError, ValueError):
                leader_observed = False
            timed_out = not leader_observed
            if leader_observed:
                descendant_leaked = _process_group_exists(process.pid)
    finally:
        try:
            ownership.cleanup()
        finally:
            deferred_sigint.restore()
    return (
        not timed_out
        and not deferred_sigint.received
        and not descendant_leaked
        and not ownership.group_cleanup_incomplete
        and not ownership.exchange_incomplete
        and not state.overflow
        and not state.write_failed
        and ownership.return_code == 0
        and _parse_driver_response(bytes(state.output), conformance_case.case_id)
    )


def _require_identity_label(value: object) -> str:
    if not isinstance(value, str) or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ConformanceInvalidInputError()
    return value


def _require_build_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or not value.isascii()
        or value != value.strip()
        or len(value.encode("ascii")) > 128
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ConformanceInvalidInputError()
    return value


def _validate_executable_path(path: Path) -> Path:
    try:
        normalized = Path(validate_runtime_path_lexically(str(path)))
    except DelegationInvalidInputError:
        raise ConformanceInvalidInputError() from None
    if not normalized.is_file() or not os.access(normalized, os.X_OK):
        raise ConformanceInvalidInputError()
    return normalized


def run_conformance(
    driver_path: Path,
    runtime_path: Path,
    *,
    runtime_build_id: str,
    adapter_id: str,
    vendor_family: VendorFamily,
) -> tuple[dict[str, object], bool]:
    """Run all predeclared cases and return task-free untrusted evidence."""
    if (
        os.name != "posix"
        or not hasattr(os, "killpg")
        or not _has_leader_exit_observer()
        or not _has_safe_sigchld_disposition()
        or not 0 < CASE_TIMEOUT_SECONDS <= MAX_CASE_TIMEOUT_SECONDS
    ):
        raise ConformanceInvalidInputError()
    driver_path = _validate_executable_path(driver_path)
    runtime_path = _validate_executable_path(runtime_path)
    runtime_build_id = _require_build_id(runtime_build_id)
    adapter_id = _require_identity_label(adapter_id)
    if vendor_family not in ("claude", "codex"):
        raise ConformanceInvalidInputError()
    try:
        target_platform = current_platform_contract()
    except DelegationUnsupportedError:
        raise ConformanceInvalidInputError() from None
    try:
        artifact_size_bytes, artifact_sha256 = qualification_artifact_identity(runtime_path)
    except QualificationInvalidInputError:
        raise ConformanceInvalidInputError() from None

    matrix: list[dict[str, object]] = []
    scenarios: list[dict[str, object]] = []
    all_passed = True
    with tempfile.TemporaryDirectory(prefix="weightclass-conformance-") as temporary_root:
        root = Path(temporary_root)
        for index, conformance_case in enumerate(CONFORMANCE_CASES):
            workspace_path = root / f"case-{index:03d}"
            workspace_path.mkdir(mode=0o700)
            passed = _run_driver_case(
                driver_path,
                runtime_path,
                conformance_case,
                workspace_path,
                timeout_seconds=CASE_TIMEOUT_SECONDS,
            )
            all_passed = all_passed and passed
            if conformance_case.scenario_id is not None:
                scenarios.append({"id": conformance_case.scenario_id, "passed": passed})
            else:
                matrix.append(
                    {
                        "role": conformance_case.role,
                        "category": conformance_case.category,
                        "action": conformance_case.action,
                        "mode": conformance_case.mode,
                        "passed": passed,
                    }
                )

    try:
        final_artifact_identity = qualification_artifact_identity(
            runtime_path,
            expected_size=artifact_size_bytes,
        )
    except QualificationInvalidInputError:
        final_artifact_identity = None
    if final_artifact_identity != (artifact_size_bytes, artifact_sha256):
        all_passed = False
        for scenario in scenarios:
            if scenario["id"] == "artifact_integrity_and_substitution":
                scenario["passed"] = False
                break

    evidence: dict[str, object] = {
        "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
        "artifact_sha256": artifact_sha256,
        "artifact_size_bytes": artifact_size_bytes,
        "suite_revision": SUITE_REVISION,
        "runtime_build_id": runtime_build_id,
        "platform": {
            "os": target_platform.os,
            "architecture": target_platform.architecture,
        },
        "protocol_version": RUNTIME_PROTOCOL_VERSION,
        "adapter_id": adapter_id,
        "vendor_family": vendor_family,
        "result_matrix": matrix,
        "scenario_results": scenarios,
    }
    return evidence, all_passed


def build_parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        prog="python -m weightclass.delegation_conformance",
        description="Run maintainer conformance cases through a reviewed external driver.",
        allow_abbrev=False,
    )
    parser.add_argument("--driver", required=True, type=Path)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--runtime-build-id", required=True)
    parser.add_argument("--adapter-id", required=True)
    parser.add_argument("--vendor-family", required=True, choices=("claude", "codex"))
    return parser


def main(arguments: list[str] | None = None) -> int:
    try:
        parsed = build_parser().parse_args(arguments)
        evidence, passed = run_conformance(
            parsed.driver,
            parsed.runtime,
            runtime_build_id=parsed.runtime_build_id,
            adapter_id=parsed.adapter_id,
            vendor_family=cast(VendorFamily, parsed.vendor_family),
        )
    except ConformanceInvalidInputError:
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"error": "interrupted"}), file=sys.stderr)
        return 130
    print(canonical_json_bytes(evidence).decode("ascii"))
    if not passed:
        print(json.dumps({"error": "conformance_failed"}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
