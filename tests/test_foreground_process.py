import os
import signal
import subprocess
import sys
import unittest
from collections.abc import Callable
from types import FrameType
from typing import cast
from unittest.mock import Mock, call, patch

from weightclass import process_context
from weightclass.foreground_process import ForegroundProcessError, run_owned_foreground


def mock_process(arguments: tuple[str, ...]) -> Mock:
    process = Mock(spec=subprocess.Popen)
    process.args = arguments
    process.pid = 123
    process.returncode = None
    process.stdin = Mock()
    process.stdin.closed = False

    def close() -> None:
        process.stdin.closed = True

    process.stdin.close.side_effect = close
    return process


class ForegroundProcessTests(unittest.TestCase):
    def test_exact_argv_and_partial_input_use_one_owned_child(self) -> None:
        arguments = ("/owned/runtime", "--flag")
        process = mock_process(arguments)
        delivered = bytearray()

        def write(contents: bytes) -> int:
            length = min(2, len(contents))
            delivered.extend(contents[:length])
            return length

        process.stdin.write.side_effect = write

        def wait(owned_process: subprocess.Popen[bytes], timeout: float | None = None) -> int:
            self.assertIs(owned_process, process)
            self.assertIsNone(timeout)
            process.returncode = 0
            return 0

        with (
            patch(
                "weightclass.foreground_process.has_safe_child_status_context",
                return_value=True,
            ),
            patch("weightclass.foreground_process.subprocess.Popen", return_value=process) as spawn,
            patch("weightclass.foreground_process.wait_owned_child", side_effect=wait),
        ):
            completed = run_owned_foreground(
                arguments,
                b"task!",
                cleanup_grace_seconds=1,
                terminate_grace_seconds=2,
            )

        self.assertEqual(bytes(delivered), b"task!")
        self.assertEqual((completed.args, completed.returncode), (arguments, 0))
        spawn.assert_called_once_with(
            arguments,
            bufsize=0,
            close_fds=True,
            shell=False,
            stdin=subprocess.PIPE,
        )

    def test_zero_write_failure_still_reaps_the_owned_child(self) -> None:
        arguments = ("/owned/runtime",)
        process = mock_process(arguments)
        process.stdin.write.return_value = 0

        def reap(owned_process: subprocess.Popen[bytes], timeout: float | None = None) -> int:
            self.assertIs(owned_process, process)
            self.assertEqual(timeout, 1)
            process.returncode = 9
            return 9

        with (
            patch(
                "weightclass.foreground_process.has_safe_child_status_context",
                return_value=True,
            ),
            patch("weightclass.foreground_process.subprocess.Popen", return_value=process),
            patch("weightclass.foreground_process.wait_owned_child", side_effect=reap) as wait,
            patch("weightclass.foreground_process.os.kill") as kill,
            self.assertRaises(ForegroundProcessError),
        ):
            run_owned_foreground(
                arguments,
                b"task",
                cleanup_grace_seconds=1,
                terminate_grace_seconds=2,
            )

        wait.assert_called_once_with(process, 1)
        kill.assert_not_called()
        self.assertEqual(process.returncode, 9)

    def test_broken_pipe_reports_the_owned_child_exit_status(self) -> None:
        arguments = ("/owned/runtime",)
        process = mock_process(arguments)
        process.stdin.write.side_effect = BrokenPipeError()

        def wait(owned_process: subprocess.Popen[bytes], timeout: float | None = None) -> int:
            self.assertIs(owned_process, process)
            self.assertIsNone(timeout)
            process.returncode = 19
            return 19

        with (
            patch(
                "weightclass.foreground_process.has_safe_child_status_context",
                return_value=True,
            ),
            patch("weightclass.foreground_process.subprocess.Popen", return_value=process),
            patch("weightclass.foreground_process.wait_owned_child", side_effect=wait),
        ):
            completed = run_owned_foreground(
                arguments,
                b"task",
                cleanup_grace_seconds=1,
                terminate_grace_seconds=2,
            )

        self.assertEqual(completed.returncode, 19)

    def test_real_early_exit_broken_pipe_preserves_child_status(self) -> None:
        completed = run_owned_foreground(
            (sys.executable, "-c", "raise SystemExit(19)"),
            b"x" * 1_000_000,
            cleanup_grace_seconds=0,
            terminate_grace_seconds=0,
        )

        self.assertEqual(completed.returncode, 19)

    def test_real_child_inherits_stdout_and_stderr(self) -> None:
        program = (
            "import sys; "
            "from weightclass.foreground_process import run_owned_foreground; "
            "result=run_owned_foreground((sys.executable,'-c',"
            "\"import sys; sys.stdin.buffer.read(); print('owned-out'); "
            "print('owned-err', file=sys.stderr)\"),b'payload',"
            "cleanup_grace_seconds=0,terminate_grace_seconds=0); "
            "raise SystemExit(result.returncode)"
        )
        completed = subprocess.run(
            (sys.executable, "-c", program),
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONPATH": "src"},
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "owned-out\n")
        self.assertEqual(completed.stderr, "owned-err\n")

    def test_real_signal_exit_is_preserved(self) -> None:
        completed = run_owned_foreground(
            (
                sys.executable,
                "-c",
                "import os, signal; os.kill(os.getpid(), signal.SIGTERM)",
            ),
            b"",
            cleanup_grace_seconds=0,
            terminate_grace_seconds=0,
        )

        self.assertEqual(completed.returncode, -signal.SIGTERM)

    def test_sigint_inside_popen_is_deferred_until_the_child_is_owned_and_reaped(self) -> None:
        arguments = ("/owned/runtime",)
        process = mock_process(arguments)
        process.stdin.write.return_value = 0
        previous_handler = signal.getsignal(signal.SIGINT)

        def interrupt_then_return_process(*args: object, **kwargs: object) -> Mock:
            del args, kwargs
            installed_handler = signal.getsignal(signal.SIGINT)
            self.assertIsNot(installed_handler, previous_handler)
            self.assertTrue(callable(installed_handler))
            cast(Callable[[int, FrameType | None], object], installed_handler)(signal.SIGINT, None)
            return process

        def reap(owned_process: subprocess.Popen[bytes], timeout: float | None = None) -> int:
            self.assertIs(owned_process, process)
            self.assertEqual(timeout, 1)
            process.returncode = -signal.SIGINT
            return -signal.SIGINT

        try:
            with (
                patch(
                    "weightclass.foreground_process.has_safe_child_status_context",
                    return_value=True,
                ),
                patch(
                    "weightclass.foreground_process.subprocess.Popen",
                    side_effect=interrupt_then_return_process,
                ),
                patch("weightclass.foreground_process.wait_owned_child", side_effect=reap) as wait,
                patch("weightclass.foreground_process.os.kill") as kill,
                self.assertRaises(KeyboardInterrupt),
            ):
                run_owned_foreground(
                    arguments,
                    b"task",
                    cleanup_grace_seconds=1,
                    terminate_grace_seconds=2,
                )
        finally:
            signal.signal(signal.SIGINT, previous_handler)

        wait.assert_called_once_with(process, 1)
        kill.assert_not_called()
        self.assertEqual(process.returncode, -signal.SIGINT)

    def test_term_error_does_not_skip_kill_and_unbounded_owned_reap(self) -> None:
        arguments = ("/owned/runtime",)
        process = mock_process(arguments)
        original_interrupt = KeyboardInterrupt()
        process.stdin.write.side_effect = original_interrupt
        wait_outcomes: list[BaseException | int] = [
            subprocess.TimeoutExpired(arguments, 1),
            subprocess.TimeoutExpired(arguments, 0),
            subprocess.TimeoutExpired(arguments, 0),
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
            patch(
                "weightclass.foreground_process.has_safe_child_status_context",
                return_value=True,
            ),
            patch("weightclass.foreground_process.subprocess.Popen", return_value=process),
            patch(
                "weightclass.foreground_process.wait_owned_child", side_effect=wait
            ) as owned_wait,
            patch("weightclass.foreground_process.os.kill", side_effect=signal_child) as kill,
            self.assertRaises(KeyboardInterrupt) as raised,
        ):
            run_owned_foreground(
                arguments,
                b"task",
                cleanup_grace_seconds=1,
                terminate_grace_seconds=2,
            )

        self.assertIs(raised.exception, original_interrupt)
        self.assertEqual(
            kill.call_args_list,
            [call(123, signal.SIGTERM), call(123, signal.SIGKILL)],
        )
        self.assertEqual(
            owned_wait.call_args_list,
            [call(process, 1), call(process, 0), call(process, 0), call(process)],
        )
        self.assertFalse(wait_outcomes)
        self.assertEqual(process.returncode, 0)

    def test_cleanup_status_loss_takes_priority_over_original_input_error(self) -> None:
        arguments = ("/owned/runtime",)
        process = mock_process(arguments)
        process.stdin.write.side_effect = OSError()
        status_loss = process_context.ChildStatusLostError()
        actual_error: BaseException | None = None

        with (
            patch(
                "weightclass.foreground_process.has_safe_child_status_context",
                return_value=True,
            ),
            patch("weightclass.foreground_process.subprocess.Popen", return_value=process),
            patch("weightclass.foreground_process.wait_owned_child", side_effect=status_loss),
            patch("weightclass.foreground_process.os.kill") as kill,
        ):
            try:
                run_owned_foreground(
                    arguments,
                    b"task",
                    cleanup_grace_seconds=1,
                    terminate_grace_seconds=2,
                )
            except BaseException as raised:
                actual_error = raised
            else:
                self.fail("input delivery failure must not become success")

        self.assertIs(actual_error, status_loss)
        kill.assert_not_called()

    def test_second_interrupt_is_deferred_until_kill_and_reap_then_original_wins(self) -> None:
        arguments = ("/owned/runtime",)
        process = mock_process(arguments)
        original_interrupt = KeyboardInterrupt()
        cleanup_interrupt = KeyboardInterrupt()
        process.stdin.write.side_effect = original_interrupt
        process.stdin.close.side_effect = [cleanup_interrupt, None]
        wait_outcomes: list[BaseException | int] = [
            subprocess.TimeoutExpired(arguments, 1),
            subprocess.TimeoutExpired(arguments, 0),
            subprocess.TimeoutExpired(arguments, 2),
            subprocess.TimeoutExpired(arguments, 0),
            0,
        ]

        def wait(owned_process: subprocess.Popen[bytes], timeout: float | None = None) -> int:
            self.assertIs(owned_process, process)
            outcome = wait_outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            process.returncode = outcome
            return outcome

        with (
            patch(
                "weightclass.foreground_process.has_safe_child_status_context",
                return_value=True,
            ),
            patch("weightclass.foreground_process.subprocess.Popen", return_value=process),
            patch(
                "weightclass.foreground_process.wait_owned_child", side_effect=wait
            ) as owned_wait,
            patch("weightclass.foreground_process.os.kill") as kill,
            self.assertRaises(KeyboardInterrupt) as raised,
        ):
            run_owned_foreground(
                arguments,
                b"task",
                cleanup_grace_seconds=1,
                terminate_grace_seconds=2,
            )

        self.assertIs(raised.exception, original_interrupt)
        self.assertEqual(
            kill.call_args_list,
            [call(123, signal.SIGTERM), call(123, signal.SIGKILL)],
        )
        self.assertEqual(
            owned_wait.call_args_list,
            [
                call(process, 1),
                call(process, 0),
                call(process, 2),
                call(process, 0),
                call(process),
            ],
        )
        self.assertFalse(wait_outcomes)
        self.assertEqual(process.returncode, 0)

    def test_cleanup_continues_when_signal_deferral_cannot_be_armed(self) -> None:
        arguments = ("/owned/runtime",)
        process = mock_process(arguments)
        original_interrupt = KeyboardInterrupt()
        arm_error = ForegroundProcessError()
        process.stdin.write.side_effect = original_interrupt

        def reap(owned_process: subprocess.Popen[bytes], timeout: float | None = None) -> int:
            self.assertIs(owned_process, process)
            self.assertEqual(timeout, 1)
            process.returncode = 0
            return 0

        with (
            patch(
                "weightclass.foreground_process.has_safe_child_status_context",
                return_value=True,
            ),
            patch("weightclass.foreground_process.subprocess.Popen", return_value=process),
            patch(
                "weightclass.foreground_process._DeferredSigint.arm",
                side_effect=[None, arm_error],
            ),
            patch("weightclass.foreground_process.wait_owned_child", side_effect=reap) as wait,
            patch("weightclass.foreground_process.os.kill") as kill,
            self.assertRaises(KeyboardInterrupt) as raised,
        ):
            run_owned_foreground(
                arguments,
                b"task",
                cleanup_grace_seconds=1,
                terminate_grace_seconds=2,
            )

        self.assertIs(raised.exception, original_interrupt)
        wait.assert_called_once_with(process, 1)
        kill.assert_not_called()
        self.assertEqual(process.returncode, 0)

    def test_unarmed_cleanup_retries_after_interrupt_until_the_child_is_reaped(self) -> None:
        arguments = ("/owned/runtime",)
        process = mock_process(arguments)
        original_error = OSError()
        cleanup_interrupt = KeyboardInterrupt()
        process.stdin.write.side_effect = original_error
        cleanup_calls = 0

        def cleanup(
            owned_process: subprocess.Popen[bytes],
            cleanup_grace_seconds: float,
            terminate_grace_seconds: float,
        ) -> BaseException | None:
            nonlocal cleanup_calls
            self.assertIs(owned_process, process)
            self.assertEqual((cleanup_grace_seconds, terminate_grace_seconds), (1, 2))
            cleanup_calls += 1
            if cleanup_calls == 1:
                raise cleanup_interrupt
            process.returncode = 0
            return None

        with (
            patch(
                "weightclass.foreground_process.has_safe_child_status_context",
                return_value=True,
            ),
            patch("weightclass.foreground_process.subprocess.Popen", return_value=process),
            patch(
                "weightclass.foreground_process._DeferredSigint.arm",
                side_effect=[None, ForegroundProcessError()],
            ),
            patch("weightclass.foreground_process._cleanup_owned_child", side_effect=cleanup),
        ):
            try:
                run_owned_foreground(
                    arguments,
                    b"task",
                    cleanup_grace_seconds=1,
                    terminate_grace_seconds=2,
                )
            except BaseException as raised:
                actual_error = raised
            else:
                self.fail("input delivery failure must not become success")

        self.assertIs(actual_error, cleanup_interrupt)
        self.assertEqual(cleanup_calls, 2)
        self.assertEqual(process.returncode, 0)

    def test_repeated_sigint_during_cleanup_is_dispatched_only_after_owned_reap(self) -> None:
        arguments = ("/owned/runtime",)
        process = mock_process(arguments)
        input_error = OSError()
        process.stdin.write.side_effect = input_error
        wait_outcomes: list[BaseException | int] = [
            subprocess.TimeoutExpired(arguments, 1),
            subprocess.TimeoutExpired(arguments, 0),
            subprocess.TimeoutExpired(arguments, 2),
            subprocess.TimeoutExpired(arguments, 0),
            0,
        ]
        previous_handler = signal.getsignal(signal.SIGINT)

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
                installed_handler = signal.getsignal(signal.SIGINT)
                self.assertIsNot(installed_handler, previous_handler)
                self.assertTrue(callable(installed_handler))
                handler = cast(
                    Callable[[int, FrameType | None], object],
                    installed_handler,
                )
                handler(signal.SIGINT, None)
                handler(signal.SIGINT, None)

        try:
            with (
                patch(
                    "weightclass.foreground_process.has_safe_child_status_context",
                    return_value=True,
                ),
                patch("weightclass.foreground_process.subprocess.Popen", return_value=process),
                patch(
                    "weightclass.foreground_process.wait_owned_child", side_effect=wait
                ) as owned_wait,
                patch("weightclass.foreground_process.os.kill", side_effect=signal_child) as kill,
                self.assertRaises(KeyboardInterrupt),
            ):
                run_owned_foreground(
                    arguments,
                    b"task",
                    cleanup_grace_seconds=1,
                    terminate_grace_seconds=2,
                )
        finally:
            signal.signal(signal.SIGINT, previous_handler)

        self.assertEqual(
            kill.call_args_list,
            [call(123, signal.SIGTERM), call(123, signal.SIGKILL)],
        )
        self.assertEqual(
            owned_wait.call_args_list,
            [
                call(process, 1),
                call(process, 0),
                call(process, 2),
                call(process, 0),
                call(process),
            ],
        )
        self.assertFalse(wait_outcomes)
        self.assertEqual(process.returncode, 0)

    def test_spawn_adjacent_unsafe_status_context_stops_before_popen(self) -> None:
        with (
            patch(
                "weightclass.foreground_process.has_safe_child_status_context",
                return_value=False,
            ),
            patch("weightclass.foreground_process.subprocess.Popen") as spawn,
            self.assertRaises(ForegroundProcessError),
        ):
            run_owned_foreground(
                ("/owned/runtime",),
                b"",
                cleanup_grace_seconds=1,
                terminate_grace_seconds=2,
            )

        spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
