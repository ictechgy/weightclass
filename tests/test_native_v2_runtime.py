import signal
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from weightclass import process_context
from weightclass.executable_observation import ExecutableObservation, observe_executable
from weightclass.native_v2_runtime import run_native_v2
from weightclass.native_v2_types import CompiledExecutionV2, FrozenCleanupV2
from weightclass.process_context import DelegationRuntimeUnavailableError
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
                "weightclass.native_v2_runtime.observe_executable", return_value=observation()
            ) as inspect,
            patch("weightclass.native_v2_runtime.subprocess.Popen", return_value=process) as spawn,
            patch(
                "weightclass.native_v2_runtime.subprocess.run",
                side_effect=AssertionError("subprocess.run must not own child status"),
            ),
            patch("weightclass.process_context.os.waitpid", return_value=(123, 0)),
        ):
            actual = run_native_v2(compiled(), b"task", observation())
        self.assertEqual(actual.args, compiled().argv)
        self.assertEqual(actual.returncode, 0)
        self.assertEqual(written, b"task")
        inspect.assert_called_once_with("/owned/fake")
        spawn.assert_called_once_with(
            compiled().argv,
            bufsize=0,
            close_fds=True,
            shell=False,
            stdin=subprocess.PIPE,
        )

    def test_lost_wait_status_cannot_become_success(self) -> None:
        process = Mock(spec=subprocess.Popen)
        process.args = compiled().argv
        process.pid = 123
        process.returncode = None
        process.stdin = Mock()
        process.stdin.closed = False
        process.stdin.write.return_value = 4

        with (
            patch("weightclass.native_v2_runtime.observe_executable", return_value=observation()),
            patch("weightclass.native_v2_runtime.subprocess.Popen", return_value=process),
            patch(
                "weightclass.native_v2_runtime.subprocess.run",
                return_value=subprocess.CompletedProcess(compiled().argv, 0),
            ),
            patch(
                "weightclass.process_context.os.waitpid",
                side_effect=ChildProcessError(),
            ),
            self.assertRaises(OSError) as raised,
        ):
            run_native_v2(compiled(), b"task", observation())

        self.assertIs(type(raised.exception), process_context.ChildStatusLostError)
        self.assertIsNotNone(process.returncode)

    def test_post_spawn_exception_closes_input_and_reaps_owned_child(self) -> None:
        process = Mock(spec=subprocess.Popen)
        process.args = compiled().argv
        process.pid = 123
        process.returncode = None
        process.stdin = Mock()
        process.stdin.closed = False
        interruption = KeyboardInterrupt()
        process.stdin.write.side_effect = interruption

        def close() -> None:
            process.stdin.closed = True

        process.stdin.close.side_effect = close
        with (
            patch("weightclass.native_v2_runtime.observe_executable", return_value=observation()),
            patch("weightclass.native_v2_runtime.subprocess.Popen", return_value=process),
            patch(
                "weightclass.native_v2_runtime.subprocess.run",
                return_value=subprocess.CompletedProcess(compiled().argv, 0),
            ),
            patch(
                "weightclass.process_context.os.waitpid",
                return_value=(123, signal.SIGTERM),
            ),
            self.assertRaises(KeyboardInterrupt) as raised,
        ):
            run_native_v2(compiled(), b"task", observation())

        self.assertIs(raised.exception, interruption)
        self.assertTrue(process.stdin.closed)
        self.assertEqual(process.returncode, -signal.SIGTERM)

    def test_cleanup_continues_to_sigkill_and_reap_after_sigterm_error(self) -> None:
        process = Mock(spec=subprocess.Popen)
        process.args = compiled().argv
        process.pid = 123
        process.returncode = None
        process.stdin = Mock()
        process.stdin.closed = False
        interruption = KeyboardInterrupt()
        process.stdin.write.side_effect = interruption

        def close() -> None:
            process.stdin.closed = True

        process.stdin.close.side_effect = close
        wait_outcomes: list[BaseException | int] = [
            subprocess.TimeoutExpired(process.args, 0),
            subprocess.TimeoutExpired(process.args, 0),
            subprocess.TimeoutExpired(process.args, 0),
            0,
        ]

        def wait(owned_process: subprocess.Popen[bytes], timeout: float | None = None) -> int:
            self.assertIs(owned_process, process)
            outcome = wait_outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            process.returncode = outcome
            return outcome

        def signal_child(process_id: int, signal_number: int) -> None:
            self.assertEqual(process_id, 123)
            if signal_number == signal.SIGTERM:
                raise OSError()

        with (
            patch("weightclass.native_v2_runtime.observe_executable", return_value=observation()),
            patch("weightclass.native_v2_runtime.subprocess.Popen", return_value=process),
            patch(
                "weightclass.foreground_process.wait_owned_child", side_effect=wait
            ) as owned_wait,
            patch("weightclass.foreground_process.os.kill", side_effect=signal_child) as kill,
            self.assertRaises(KeyboardInterrupt) as raised,
        ):
            run_native_v2(compiled(), b"task", observation())

        self.assertIs(raised.exception, interruption)
        self.assertEqual(
            owned_wait.call_args_list,
            [
                call(process, 0),
                call(process, 0),
                call(process, 0),
                call(process),
            ],
        )
        self.assertEqual(
            kill.call_args_list,
            [call(123, signal.SIGTERM), call(123, signal.SIGKILL)],
        )
        self.assertFalse(wait_outcomes)
        self.assertEqual(process.returncode, 0)

    def test_unsafe_process_context_stops_before_observation_or_spawn(self) -> None:
        with (
            patch(
                "weightclass.native_v2_runtime.validate_runtime_process_context",
                side_effect=DelegationRuntimeUnavailableError,
            ),
            patch("weightclass.native_v2_runtime.observe_executable") as inspect,
            patch("weightclass.native_v2_runtime.subprocess.Popen") as spawn,
            self.assertRaises(DelegationRuntimeUnavailableError),
        ):
            run_native_v2(compiled(), b"task", observation())
        inspect.assert_not_called()
        spawn.assert_not_called()

    def test_replacement_stops_before_spawn(self) -> None:
        spawn = Mock()
        with (
            patch(
                "weightclass.native_v2_runtime.observe_executable",
                return_value=observation(inode=2),
            ),
            patch("weightclass.native_v2_runtime.subprocess.Popen", spawn),
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
            patch("weightclass.native_v2_runtime.subprocess.Popen") as spawn,
        ):
            with self.assertRaises(V2ValidationError):
                run_native_v2(altered, b"task", observation())
        inspect.assert_not_called()
        spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
