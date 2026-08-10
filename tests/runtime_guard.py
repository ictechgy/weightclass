"""Current-test-process guard for the named guarded runtime test suite."""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, TypeVar, cast
from unittest import mock


class RuntimeGuardViolation(AssertionError):
    """The current test process attempted an unregistered process or INET operation."""


_TestFunction = TypeVar("_TestFunction", bound=Callable[..., object])


def guarded_launch(category: str) -> Callable[[_TestFunction], _TestFunction]:
    """Mark an existing launch test as part of the named guard claim."""

    def decorate(function: _TestFunction) -> _TestFunction:
        attribute_name = "__guarded_launch_category__"
        setattr(function, attribute_name, category)
        return function

    return decorate


class RuntimeGuard:
    def __init__(self) -> None:
        self._prefixes: set[tuple[str, ...]] = set()
        self._validated_launch_count = 0

    @property
    def validated_launch_count(self) -> int:
        return self._validated_launch_count

    def register_executable(self, executable: Path, *arguments: str) -> tuple[str, ...]:
        resolved = str(executable.resolve(strict=True))
        prefix = (resolved, *arguments)
        self._prefixes.add(prefix)
        return prefix

    def register_python_harness(self, script: Path, *arguments: str) -> tuple[str, ...]:
        python = Path(os.path.realpath(sys.executable))
        script_path = script.resolve(strict=True)
        return self.register_executable(python, str(script_path), *arguments)

    def _validate(self, argv: object, kwargs: dict[str, Any]) -> None:
        if kwargs.get("shell", False) is not False:
            raise RuntimeGuardViolation("shell execution is forbidden")
        if kwargs.get("executable") is not None:
            raise RuntimeGuardViolation("explicit executable override is forbidden")
        if isinstance(argv, (str, bytes)) or not isinstance(argv, Sequence) or not argv:
            raise RuntimeGuardViolation("argv must be a nonempty sequence")
        if not all(isinstance(token, (str, bytes)) for token in argv):
            raise RuntimeGuardViolation("argv tokens must be strings")
        tokens = tuple(os.fsdecode(token) for token in argv)
        if not Path(tokens[0]).is_absolute():
            raise RuntimeGuardViolation("argv[0] must be absolute")
        resolved = str(Path(tokens[0]).resolve(strict=False))
        normalized = (resolved, *tokens[1:])
        if not any(normalized[: len(prefix)] == prefix for prefix in self._prefixes):
            raise RuntimeGuardViolation("process prefix is not registered")
        self._validated_launch_count += 1

    @contextlib.contextmanager
    def activated(self) -> Iterator[None]:
        original_popen = subprocess.Popen
        original_socket = socket.socket

        def guarded_popen(argv: object, *args: Any, **kwargs: Any) -> Any:
            self._validate(argv, kwargs)
            return original_popen(cast(Sequence[str], argv), *args, **kwargs)

        def guarded_socket(
            family: int = socket.AF_INET,
            type: int = socket.SOCK_STREAM,
            proto: int = 0,
            fileno: int | None = None,
        ) -> socket.socket:
            if family in (socket.AF_INET, socket.AF_INET6):
                raise RuntimeGuardViolation("INET sockets are forbidden")
            return original_socket(family, type, proto, fileno)

        def reject_connection(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeGuardViolation("socket.create_connection is forbidden")

        with (
            mock.patch.object(subprocess, "Popen", guarded_popen),
            mock.patch.object(socket, "socket", guarded_socket),
            mock.patch.object(socket, "create_connection", reject_connection),
        ):
            yield


CLAIMED_LAUNCH_TESTS = {
    "native_v1": "tests.test_router.CommandLineTests.test_passes_through_a_successful_child",
    "native_v2": (
        "tests.test_native_v2_cli.NativeV2CliTests.test_route_then_run_owned_fixture_end_to_end"
    ),
    "delegation_v1": (
        "tests.test_delegation_runtime.DelegationRunTests."
        "test_success_sends_one_reviewed_frame_and_inherits_output"
    ),
    "delegation_v2": (
        "tests.test_delegation_v2_runtime.DelegationV2RuntimeTests."
        "test_owned_fixture_receives_a_real_wcd2_frame"
    ),
    "triage": "tests.test_triage.AskVendorTests.test_guarded_owned_absolute_vendor",
    "conformance": (
        "tests.test_delegation_conformance.DelegationConformanceRunnerTests."
        "test_full_run_emits_candidate_compatible_evidence_without_reading_stdin"
    ),
}

EXCLUDED_LAUNCH_SCOPES = ("build", "packaging", "extracted_sdist")


def register_claimed_launches(guard: RuntimeGuard, category: str) -> None:
    """Register only the exact owned executable or Python harness prefixes for a claim."""
    python = Path(sys.executable).resolve(strict=True)
    fixtures = Path(__file__).parent / "fixtures"
    prefixes: dict[str, tuple[tuple[Path, tuple[str, ...]], ...]] = {
        # run --policy 는 검토한 지문을 요구하므로 native v1 도 route 를 먼저 띄운다.
        "native_v1": (
            (python, ("-m", "weightclass", "route")),
            (python, ("-m", "weightclass", "run")),
        ),
        "native_v2": (
            (python, ("-m", "weightclass", "route")),
            (python, ("-m", "weightclass", "run")),
        ),
        "delegation_v1": (
            (python, ("-m", "weightclass", "delegate", "route")),
            (python, ("-m", "weightclass", "delegate", "run")),
        ),
        "delegation_v2": (
            (
                fixtures / "fake_delegation_v2_runtime.py",
                ("--weightclass-delegation-protocol", "2"),
            ),
        ),
        "triage": ((fixtures / "fake_triage_vendor.py", ()),),
        "conformance": ((python, ("-m", "weightclass.delegation_conformance")),),
    }
    try:
        selected = prefixes[category]
    except KeyError:
        raise RuntimeGuardViolation("unknown guarded launch category") from None
    for executable, arguments in selected:
        guard.register_executable(executable, *arguments)
