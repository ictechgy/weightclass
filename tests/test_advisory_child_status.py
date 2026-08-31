from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast
from unittest import mock

from weightclass.advisory import advisory_parallel, advisory_preflight, bounded_capture
from weightclass.process_errors import ChildStatusLostError


@contextmanager
def _sleeping_child(
    *, text: bool, sleep_seconds: float = 30.0
) -> Iterator[subprocess.Popen[bytes] | subprocess.Popen[str]]:
    process = subprocess.Popen(
        (sys.executable, "-c", f"import time; time.sleep({sleep_seconds!r})"),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        start_new_session=True,
    )
    try:
        yield process
    finally:
        if process.returncode is None:
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()


@unittest.skipUnless(hasattr(os, "killpg"), "requires POSIX process groups")
class AdvisoryChildStatusTests(unittest.TestCase):
    def test_parallel_rejects_an_unsafe_status_context_before_spawn(self) -> None:
        with (
            mock.patch.object(
                advisory_parallel,
                "has_safe_child_status_context",
                return_value=False,
            ),
            mock.patch("weightclass.advisory.advisory_parallel.subprocess.Popen") as popen,
            self.assertRaisesRegex(ValueError, "^$"),
        ):
            advisory_parallel.run_parallel(
                (advisory_parallel.AdvisoryJob("unsafe", (sys.executable, "-c", "pass")),)
            )

        popen.assert_not_called()

    def test_preflight_rejects_an_unsafe_status_context_before_spawn(self) -> None:
        with (
            mock.patch.object(
                advisory_preflight,
                "has_safe_child_status_context",
                return_value=False,
            ),
            mock.patch("weightclass.advisory.advisory_preflight.subprocess.Popen") as popen,
            self.assertRaises(OSError),
        ):
            advisory_preflight._bounded_command((sys.executable, "--version"))

        popen.assert_not_called()

    def test_capture_status_loss_does_not_repeat_group_signal(self) -> None:
        signals: list[int] = []
        with _sleeping_child(text=True, sleep_seconds=0.05) as untyped_process:
            process = cast(subprocess.Popen[str], untyped_process)

            def terminate_group(child: subprocess.Popen[str]) -> None:
                self.assertIs(child, process)
                signals.append(signal.SIGKILL)

            with (
                mock.patch(
                    "weightclass.process_context.observe_leader_exit",
                    side_effect=ChildProcessError(),
                ),
                self.assertRaises(ChildStatusLostError),
            ):
                bounded_capture.capture_text_process(
                    process,
                    None,
                    timeout_seconds=0.01,
                    max_output_bytes=1024,
                    terminate_group=terminate_group,
                )

        self.assertEqual(signals, [])

    def test_capture_preserves_success(self) -> None:
        process = subprocess.Popen(
            (sys.executable, "-c", "print('captured')"),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        result = bounded_capture.capture_text_process(
            process,
            None,
            timeout_seconds=2.0,
            max_output_bytes=1024,
            terminate_group=lambda child: os.killpg(child.pid, signal.SIGKILL),
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "captured\n")
        self.assertFalse(result.timed_out)
        self.assertFalse(result.output_limited)

    def test_capture_preserves_timeout_result(self) -> None:
        with _sleeping_child(text=True) as untyped_process:
            process = cast(subprocess.Popen[str], untyped_process)
            result = bounded_capture.capture_text_process(
                process,
                None,
                timeout_seconds=0.01,
                max_output_bytes=1024,
                terminate_group=lambda child: os.killpg(child.pid, signal.SIGKILL),
            )

        self.assertTrue(result.timed_out)
        self.assertFalse(result.output_limited)
        self.assertIsNotNone(result.returncode)

    def test_parallel_status_loss_does_not_repeat_group_signal(self) -> None:
        with _sleeping_child(text=False, sleep_seconds=0.05) as untyped_process:
            process = cast(subprocess.Popen[bytes], untyped_process)
            job = advisory_parallel.AdvisoryJob(
                "status-loss",
                (sys.executable, "-c", "pass"),
                timeout_seconds=0.01,
            )
            with (
                mock.patch(
                    "weightclass.process_context.observe_leader_exit",
                    side_effect=ChildProcessError(),
                ),
                mock.patch("weightclass.advisory.advisory_parallel.time.sleep"),
                mock.patch("weightclass.advisory.advisory_parallel.os.killpg") as kill_group,
                self.assertRaises(ChildStatusLostError),
            ):
                advisory_parallel._capture_job(process, job, threading.Event())

        kill_group.assert_not_called()

    def test_parallel_preserves_success(self) -> None:
        (result,) = advisory_parallel.run_parallel(
            (
                advisory_parallel.AdvisoryJob(
                    "success",
                    (sys.executable, "-c", "print('parallel')"),
                    timeout_seconds=2.0,
                ),
            )
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"parallel\n")
        self.assertFalse(result.timed_out)

    def test_parallel_preserves_timeout_result(self) -> None:
        (result,) = advisory_parallel.run_parallel(
            (
                advisory_parallel.AdvisoryJob(
                    "timeout",
                    (sys.executable, "-c", "import time; time.sleep(30)"),
                    timeout_seconds=0.01,
                ),
            )
        )

        self.assertEqual(result.returncode, 124)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.stderr, b"advisory child timed out\n")

    def test_preflight_status_loss_does_not_repeat_group_signal(self) -> None:
        with _sleeping_child(text=False, sleep_seconds=0.05) as untyped_process:
            process = cast(subprocess.Popen[bytes], untyped_process)
            with (
                mock.patch.object(advisory_preflight, "PROBE_TIMEOUT_SECONDS", 0.01),
                mock.patch(
                    "weightclass.process_context.observe_leader_exit",
                    side_effect=ChildProcessError(),
                ),
                mock.patch("weightclass.advisory.advisory_preflight.os.killpg") as kill_group,
                self.assertRaises(ChildStatusLostError),
            ):
                advisory_preflight._capture_bounded_command(process)

        kill_group.assert_not_called()

    def test_preflight_preserves_success(self) -> None:
        code, payload = advisory_preflight._bounded_command(
            (sys.executable, "-c", "print('preflight')")
        )

        self.assertEqual(code, 0)
        self.assertEqual(payload, b"preflight\n")

    def test_preflight_preserves_timeout_result(self) -> None:
        with mock.patch.object(advisory_preflight, "PROBE_TIMEOUT_SECONDS", 0.01):
            code, payload = advisory_preflight._bounded_command(
                (sys.executable, "-c", "import time; time.sleep(30)")
            )

        self.assertEqual(code, 124)
        self.assertEqual(payload, b"")

    def test_exited_leader_does_not_orphan_a_same_group_descendant(self) -> None:
        program = (
            "import os, signal, time\n"
            "child = os.fork()\n"
            "if child == 0:\n"
            "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "    print('ready', flush=True)\n"
            "    time.sleep(30)\n"
            "else:\n"
            "    os._exit(0)\n"
        )
        process = subprocess.Popen(
            (sys.executable, "-c", program),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        signals: list[int] = []

        def kill_group(child: subprocess.Popen[str]) -> None:
            signals.append(signal.SIGKILL)
            os.killpg(child.pid, signal.SIGKILL)

        result = bounded_capture.capture_text_process(
            process,
            None,
            timeout_seconds=0.05,
            max_output_bytes=1024,
            terminate_group=kill_group,
        )

        self.assertTrue(result.timed_out)
        self.assertEqual(signals, [signal.SIGKILL])
        self.assertIsNotNone(process.returncode)


if __name__ == "__main__":
    unittest.main()
