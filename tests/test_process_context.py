import os
import signal
import subprocess
import unittest
from unittest.mock import Mock, call, patch

from weightclass import process_context


class OwnedChildWaitTests(unittest.TestCase):
    def test_owned_wait_preserves_exit_and_signal_return_codes(self) -> None:
        for wait_status, expected in ((0, 0), (23 << 8, 23), (signal.SIGTERM, -signal.SIGTERM)):
            with self.subTest(wait_status=wait_status):
                process = Mock(spec=subprocess.Popen)
                process.args = ("runtime",)
                process.pid = 123
                process.returncode = None
                with patch(
                    "weightclass.process_context.os.waitpid",
                    return_value=(123, wait_status),
                ):
                    actual = process_context.wait_owned_child(process)

                self.assertEqual(actual, expected)
                self.assertEqual(process.returncode, expected)
                process.wait.assert_not_called()

    def test_owned_wait_fails_closed_and_remembers_lost_status(self) -> None:
        process = Mock(spec=subprocess.Popen)
        process.args = ("runtime",)
        process.pid = 123
        process.returncode = None

        with (
            patch(
                "weightclass.process_context.os.waitpid",
                side_effect=ChildProcessError(),
            ) as waitpid,
            self.assertRaises(OSError) as raised,
        ):
            process_context.wait_owned_child(process)

        self.assertIs(type(raised.exception), process_context.ChildStatusLostError)
        self.assertIsNotNone(process.returncode)
        waitpid.reset_mock()
        with self.assertRaises(process_context.ChildStatusLostError):
            process_context.wait_owned_child(process)
        waitpid.assert_not_called()

    def test_owned_wait_uses_one_finite_deadline_across_interruptions(self) -> None:
        process = Mock(spec=subprocess.Popen)
        process.args = ("runtime",)
        process.pid = 123
        process.returncode = None

        with (
            patch(
                "weightclass.process_context.os.waitpid",
                side_effect=[InterruptedError(), InterruptedError(), (123, 17 << 8)],
            ) as waitpid,
            patch(
                "weightclass.process_context.time.monotonic",
                side_effect=[10.0, 11.0, 12.0],
            ),
        ):
            actual = process_context.wait_owned_child(process, 5.0)

        self.assertEqual(actual, 17)
        self.assertEqual(waitpid.call_args_list, [call(123, os.WNOHANG)] * 3)


if __name__ == "__main__":
    unittest.main()
