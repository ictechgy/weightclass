import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.runtime_guard import guarded_launch
from weightclass.delegation_v2_runtime import run_delegation_v2_runtime
from weightclass.executable_observation import ExecutableObservation
from weightclass.native_v2_types import CompiledExecutionV2, FrozenCleanupV2
from weightclass.v2_validation import V2ValidationError

FIXTURE = Path(__file__).parent / "fixtures/fake_delegation_v2_runtime.py"


def compiled() -> CompiledExecutionV2:
    return CompiledExecutionV2(
        b"{}",
        b"{}",
        "sha256:f",
        ("/owned/runtime", "--weightclass-delegation-protocol", "2"),
        "/owned/runtime",
        "wcd2_stdin",
        2,
        FrozenCleanupV2(1, 0),
    )


def observation(*, inode: int = 1) -> ExecutableObservation:
    return ExecutableObservation("/owned/runtime", 1, inode, 0o100000, 0o100700, 1, 1, 1, True)


class DelegationV2RuntimeTests(unittest.TestCase):
    @guarded_launch("delegation_v2")
    def test_owned_fixture_receives_a_real_wcd2_frame(self) -> None:
        executable = str(FIXTURE.resolve())
        value = CompiledExecutionV2(
            b"{}",
            b"{}",
            "sha256:f",
            (executable, "--weightclass-delegation-protocol", "2"),
            executable,
            "wcd2_stdin",
            2,
            FrozenCleanupV2(1, 0),
        )
        from weightclass.executable_observation import observe_executable

        completed = run_delegation_v2_runtime(
            value, b"WCD2\0\0\0\2\0\0\0\4{}task", observe_executable(executable)
        )
        self.assertEqual(completed.returncode, 0)

    def test_reobserves_then_spawns_exact_argv_once_with_exact_frame(self) -> None:
        expected: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
            compiled().argv, 0
        )
        with (
            patch(
                "weightclass.delegation_v2_runtime.observe_executable",
                return_value=observation(),
            ) as inspect,
            patch(
                "weightclass.delegation_v2_runtime.subprocess.run", return_value=expected
            ) as spawn,
        ):
            actual = run_delegation_v2_runtime(compiled(), b"WCD2-frame", observation())
        self.assertIs(actual, expected)
        inspect.assert_called_once_with("/owned/runtime")
        spawn.assert_called_once_with(
            compiled().argv, check=False, input=b"WCD2-frame", shell=False
        )

    def test_returns_the_direct_child_exit_without_task_success_claims(self) -> None:
        expected: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
            compiled().argv, 23
        )
        with (
            patch(
                "weightclass.delegation_v2_runtime.observe_executable",
                return_value=observation(),
            ),
            patch(
                "weightclass.delegation_v2_runtime.subprocess.run",
                return_value=expected,
            ),
        ):
            actual = run_delegation_v2_runtime(compiled(), b"WCD2-frame", observation())
        self.assertIs(actual, expected)
        self.assertEqual(actual.returncode, 23)

    def test_replacement_stops_before_spawn(self) -> None:
        with (
            patch(
                "weightclass.delegation_v2_runtime.observe_executable",
                return_value=observation(inode=2),
            ),
            patch("weightclass.delegation_v2_runtime.subprocess.run") as spawn,
        ):
            with self.assertRaises(V2ValidationError):
                run_delegation_v2_runtime(compiled(), b"frame", observation())
        spawn.assert_not_called()

    def test_unsafe_child_status_context_stops_before_reobservation_and_spawn(self) -> None:
        with (
            patch(
                "weightclass.delegation_v2_runtime.has_safe_sigchld_disposition",
                return_value=False,
            ),
            patch("weightclass.delegation_v2_runtime.observe_executable") as inspect,
            patch("weightclass.delegation_v2_runtime.subprocess.run") as spawn,
        ):
            with self.assertRaises(V2ValidationError):
                run_delegation_v2_runtime(compiled(), b"frame", observation())
        inspect.assert_not_called()
        spawn.assert_not_called()

    def test_rejects_mutated_executable_argv_binding_before_reobservation(self) -> None:
        value = compiled()
        altered = CompiledExecutionV2(
            value.canonical_descriptor_bytes,
            value.fingerprint_payload_bytes,
            value.route_fingerprint,
            ("/other",),
            value.executable,
            value.transport,
            value.transport_version,
            value.cleanup,
        )
        with (
            patch("weightclass.delegation_v2_runtime.observe_executable") as inspect,
            patch("weightclass.delegation_v2_runtime.subprocess.run", Mock()) as spawn,
        ):
            with self.assertRaises(V2ValidationError):
                run_delegation_v2_runtime(altered, b"frame", observation())
        inspect.assert_not_called()
        spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
