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
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
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


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


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
    process_group_id: int,
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
        _signal_process_group(process_group_id, signal.SIGKILL)
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

    assert process.stdin is not None
    assert process.stdout is not None
    state = _ExchangeState(bytearray())
    writer = threading.Thread(
        target=_write_request,
        args=(process.stdin, request, state),
        daemon=True,
    )
    reader = threading.Thread(
        target=_read_response,
        args=(process.stdout, process.pid, state),
        daemon=True,
    )
    writer.start()
    reader.start()
    timed_out = False
    interrupted = False
    try:
        return_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _signal_process_group(process.pid, signal.SIGKILL)
        return_code = _wait_after_kill(process)
    except KeyboardInterrupt:
        interrupted = True
        _signal_process_group(process.pid, signal.SIGKILL)
        try:
            process.kill()
        except (OSError, ValueError):
            pass
        return_code = _wait_after_kill(process)
    except (OSError, ValueError):
        timed_out = True
        _signal_process_group(process.pid, signal.SIGKILL)
        return_code = _wait_after_kill(process)

    descendant_leaked = _process_group_exists(process.pid)
    if descendant_leaked:
        _signal_process_group(process.pid, signal.SIGKILL)
    writer.join(timeout=1)
    reader.join(timeout=1)
    if not process.stdout.closed:
        process.stdout.close()
    if not process.stdin.closed:
        process.stdin.close()
    exchange_incomplete = writer.is_alive() or reader.is_alive()
    if interrupted:
        raise KeyboardInterrupt()
    return (
        not timed_out
        and not descendant_leaked
        and not exchange_incomplete
        and not state.overflow
        and not state.write_failed
        and return_code == 0
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
