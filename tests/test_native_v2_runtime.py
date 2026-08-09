import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from weightclass.executable_observation import ExecutableObservation, observe_executable
from weightclass.native_v2_runtime import run_native_v2
from weightclass.native_v2_types import CompiledExecutionV2, FrozenCleanupV2
from weightclass.v2_validation import V2ValidationError

FIXTURE = Path(__file__).parent / "fixtures/fake_native_runtime.py"


def compiled() -> CompiledExecutionV2:
    return CompiledExecutionV2(
        b"{}",
        b"{}",
        "f",
        ("/owned/fake", "--flag"),
        "/owned/fake",
        "native_stdin",
        1,
        FrozenCleanupV2(0, 0),
    )


def observation(*, inode: int = 1) -> ExecutableObservation:
    return ExecutableObservation("/owned/fake", 1, inode, 0o100000, 0o100700, 0, 1, 1, True)


class NativeV2RuntimeTests(unittest.TestCase):
    def test_owned_fixture_runs_once_with_exact_task_bytes(self) -> None:
        executable = str(FIXTURE.resolve())
        value = CompiledExecutionV2(
            b"{}",
            b"{}",
            "f",
            (executable, "--flag"),
            executable,
            "native_stdin",
            1,
            FrozenCleanupV2(0, 0),
        )
        first = observe_executable(executable)
        completed = run_native_v2(value, "검토".encode(), first)
        self.assertEqual(completed.returncode, 0)

    def test_spawns_exact_stored_argv_once_after_equal_second_observation(self) -> None:
        completed: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
            compiled().argv, 0
        )
        with (
            patch(
                "weightclass.native_v2_runtime.observe_executable", return_value=observation()
            ) as inspect,
            patch("weightclass.native_v2_runtime.subprocess.run", return_value=completed) as spawn,
        ):
            self.assertIs(run_native_v2(compiled(), b"task", observation()), completed)
        inspect.assert_called_once_with("/owned/fake")
        spawn.assert_called_once_with(compiled().argv, check=False, input=b"task", shell=False)

    def test_replacement_stops_before_spawn(self) -> None:
        spawn = Mock()
        with (
            patch(
                "weightclass.native_v2_runtime.observe_executable",
                return_value=observation(inode=2),
            ),
            patch("weightclass.native_v2_runtime.subprocess.run", spawn),
        ):
            with self.assertRaises(V2ValidationError):
                run_native_v2(compiled(), b"task", observation())
        spawn.assert_not_called()

    def test_rechecks_executable_matches_argv_at_spawn_seam(self) -> None:
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
            patch("weightclass.native_v2_runtime.observe_executable") as inspect,
            patch("weightclass.native_v2_runtime.subprocess.run") as spawn,
        ):
            with self.assertRaises(V2ValidationError):
                run_native_v2(altered, b"task", observation())
        inspect.assert_not_called()
        spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
