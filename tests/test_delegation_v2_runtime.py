import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.runtime_guard import guarded_launch
from weightclass import process_context
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
        process = Mock(spec=subprocess.Popen)
        process.args = compiled().argv
        process.pid = 123
        process.returncode = None
        process.stdin = Mock()
        process.stdin.closed = False
        written = bytearray()

        def write(contents: bytes) -> int:
            written.extend(contents)
            return len(contents)

        process.stdin.write.side_effect = write

        def close() -> None:
            process.stdin.closed = True

        process.stdin.close.side_effect = close
        with (
            patch(
                "weightclass.delegation_v2_runtime.observe_executable",
                return_value=observation(),
            ) as inspect,
            patch(
                "weightclass.delegation_v2_runtime.subprocess.Popen", return_value=process
            ) as spawn,
            patch(
                "weightclass.delegation_v2_runtime.subprocess.run",
                side_effect=AssertionError("subprocess.run must not own child status"),
            ),
            patch("weightclass.process_context.os.waitpid", return_value=(123, 0)),
        ):
            actual = run_delegation_v2_runtime(compiled(), b"WCD2-frame", observation())
        self.assertEqual(actual.args, compiled().argv)
        self.assertEqual(actual.returncode, 0)
        self.assertEqual(written, b"WCD2-frame")
        inspect.assert_called_once_with("/owned/runtime")
        spawn.assert_called_once_with(
            compiled().argv,
            bufsize=0,
            close_fds=True,
            shell=False,
            stdin=subprocess.PIPE,
        )

    def test_returns_the_direct_child_exit_without_task_success_claims(self) -> None:
        expected: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
            compiled().argv, 23
        )
        process = Mock(spec=subprocess.Popen)
        process.args = compiled().argv
        process.pid = 123
        process.returncode = None
        process.stdin = Mock()
        process.stdin.closed = False
        process.stdin.write.return_value = len(b"WCD2-frame")
        with (
            patch(
                "weightclass.delegation_v2_runtime.observe_executable",
                return_value=observation(),
            ),
            patch(
                "weightclass.delegation_v2_runtime.subprocess.Popen",
                return_value=process,
            ),
            patch(
                "weightclass.delegation_v2_runtime.subprocess.run",
                return_value=expected,
            ),
            patch("weightclass.process_context.os.waitpid", return_value=(123, 23 << 8)),
        ):
            actual = run_delegation_v2_runtime(compiled(), b"WCD2-frame", observation())
        self.assertEqual(actual.returncode, 23)

    def test_lost_wait_status_cannot_become_success(self) -> None:
        process = Mock(spec=subprocess.Popen)
        process.args = compiled().argv
        process.pid = 123
        process.returncode = None
        process.stdin = Mock()
        process.stdin.closed = False
        process.stdin.write.return_value = len(b"WCD2-frame")
        with (
            patch(
                "weightclass.delegation_v2_runtime.observe_executable",
                return_value=observation(),
            ),
            patch("weightclass.delegation_v2_runtime.subprocess.Popen", return_value=process),
            patch(
                "weightclass.delegation_v2_runtime.subprocess.run",
                return_value=subprocess.CompletedProcess(compiled().argv, 0),
            ),
            patch(
                "weightclass.process_context.os.waitpid",
                side_effect=ChildProcessError(),
            ),
            self.assertRaises(OSError) as raised,
        ):
            run_delegation_v2_runtime(compiled(), b"WCD2-frame", observation())

        self.assertIs(type(raised.exception), process_context.ChildStatusLostError)
        self.assertIsNotNone(process.returncode)

    def test_replacement_stops_before_spawn(self) -> None:
        with (
            patch(
                "weightclass.delegation_v2_runtime.observe_executable",
                return_value=observation(inode=2),
            ),
            patch("weightclass.delegation_v2_runtime.subprocess.Popen") as spawn,
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
            patch("weightclass.delegation_v2_runtime.subprocess.Popen") as spawn,
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
            patch("weightclass.delegation_v2_runtime.subprocess.Popen", Mock()) as spawn,
        ):
            with self.assertRaises(V2ValidationError):
                run_delegation_v2_runtime(altered, b"frame", observation())
        inspect.assert_not_called()
        spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
