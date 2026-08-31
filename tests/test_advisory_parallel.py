from __future__ import annotations

import importlib.util
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from typing import cast
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "src" / "weightclass" / "advisory"
PARALLEL = TOOLS / "advisory_parallel.py"
REPOSITORY_TOOLS_AVAILABLE = PARALLEL.is_file()


def load_module(path: Path, name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(REPOSITORY_TOOLS_AVAILABLE, "repository-only advisory tools unavailable")
class ParallelAdvisoryTests(unittest.TestCase):
    def test_independent_vendor_processes_start_concurrently(self) -> None:
        parallel = load_module(PARALLEL, "prospective_advisory_parallel_start")
        program = (
            "import pathlib,sys,time;"
            "mine=pathlib.Path(sys.argv[1]);other=pathlib.Path(sys.argv[2]);"
            "mine.write_text('started');deadline=time.monotonic()+2;"
            'exec("while not other.exists() and time.monotonic() < deadline:\\n'
            ' time.sleep(.01)");'
            "print(sys.argv[3]);sys.exit(0 if other.exists() else 9)"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.started"
            second = root / "second.started"
            results = parallel.run_parallel(
                (
                    parallel.AdvisoryJob(
                        "claude",
                        (sys.executable, "-c", program, str(first), str(second), "first"),
                    ),
                    parallel.AdvisoryJob(
                        "codex",
                        (sys.executable, "-c", program, str(second), str(first), "second"),
                    ),
                )
            )
        self.assertEqual([result.returncode for result in results], [0, 0])
        self.assertEqual([result.stdout for result in results], [b"first\n", b"second\n"])

    def test_results_keep_input_order_and_one_failure_does_not_cancel_peers(self) -> None:
        parallel = load_module(PARALLEL, "prospective_advisory_parallel_failure")
        slow_success = (
            sys.executable,
            "-c",
            "import time;time.sleep(.1);print('completed')",
        )
        quick_failure = (
            sys.executable,
            "-c",
            "import sys;print('failed',file=sys.stderr);sys.exit(7)",
        )
        results = parallel.run_parallel(
            (
                parallel.AdvisoryJob("slow", slow_success),
                parallel.AdvisoryJob("fast", quick_failure),
            )
        )
        self.assertEqual([result.label for result in results], ["slow", "fast"])
        self.assertEqual([result.returncode for result in results], [0, 7])
        self.assertEqual(results[0].stdout, b"completed\n")
        self.assertEqual(results[1].stderr, b"failed\n")

    def test_task_bytes_are_delivered_on_stdin_and_hidden_from_job_repr(self) -> None:
        parallel = load_module(PARALLEL, "prospective_advisory_parallel_stdin")
        task = b"PRIVATE CAMPAIGN TASK"
        job = parallel.AdvisoryJob(
            "codex",
            (
                sys.executable,
                "-c",
                "import sys; data=sys.stdin.buffer.read(); print(len(data))",
            ),
            stdin_bytes=task,
        )

        (result,) = parallel.run_parallel((job,))

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, f"{len(task)}\n".encode())
        self.assertNotIn(task.decode(), repr(job))

    def test_blocked_task_delivery_stops_when_the_runner_finishes(self) -> None:
        parallel = load_module(PARALLEL, "prospective_advisory_parallel_stdin_stop")
        read_descriptor, write_descriptor = os.pipe()
        process = mock.Mock()
        process.stdin = os.fdopen(write_descriptor, "wb", buffering=0)
        job = parallel.AdvisoryJob(
            "codex",
            ("runner",),
            stdin_bytes=b"x" * parallel.MAX_JOB_STDIN_BYTES,
        )
        finished = parallel.AdvisoryResult("codex", 0, b"", b"", True)
        started = time.monotonic()
        try:
            with (
                mock.patch.object(parallel.subprocess, "Popen", return_value=process),
                mock.patch.object(
                    parallel,
                    "_capture_job",
                    side_effect=lambda *_args: (
                        threading.Event().wait(0.02),
                        finished,
                    )[1],
                ),
            ):
                result = parallel._run_job(job, threading.Event())
        finally:
            os.close(read_descriptor)
            if not process.stdin.closed:
                process.stdin.close()

        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(result.returncode, 2)

    def test_invalid_or_duplicate_jobs_fail_before_start(self) -> None:
        parallel = load_module(PARALLEL, "prospective_advisory_parallel_validation")
        valid = parallel.AdvisoryJob("codex", (sys.executable, "-c", "pass"))
        invalid_cases = (
            (),
            (valid, valid),
            (parallel.AdvisoryJob("BAD LABEL", valid.command),),
            (parallel.AdvisoryJob("empty", ()),),
            (parallel.AdvisoryJob("nul", (sys.executable, "bad\x00arg")),),
            (
                parallel.AdvisoryJob(
                    "large-input",
                    valid.command,
                    stdin_bytes=b"x" * (parallel.MAX_JOB_STDIN_BYTES + 1),
                ),
            ),
        )
        for jobs in invalid_cases:
            with self.subTest(jobs=len(jobs)):
                with self.assertRaisesRegex(ValueError, "^$"):
                    parallel.run_parallel(jobs)

    def test_child_start_error_is_redacted_without_raising(self) -> None:
        parallel = load_module(PARALLEL, "prospective_advisory_parallel_start_error")
        job = parallel.AdvisoryJob("codex", ("missing",))
        with mock.patch.object(parallel.subprocess, "run", side_effect=FileNotFoundError):
            (result,) = parallel.run_parallel((job,))
        self.assertEqual(result.returncode, 2)
        self.assertFalse(result.started)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"advisory child start failed\n")

    def test_slow_timeout_reap_is_transferred_to_a_daemon(self) -> None:
        parallel = load_module(PARALLEL, "prospective_advisory_parallel_reaper")
        process = cast(subprocess.Popen[bytes], mock.Mock())
        process.pid = 12345
        anchor = mock.Mock()
        thread = mock.Mock()
        with (
            mock.patch.object(parallel.time, "sleep"),
            mock.patch.object(
                parallel,
                "wait_owned_child",
                side_effect=(
                    subprocess.TimeoutExpired(("child",), 1.0),
                    0,
                ),
            ) as wait_owned,
            mock.patch.object(parallel.threading, "Thread", return_value=thread) as factory,
        ):
            parallel._terminate_process_group(process, anchor)
            target = factory.call_args.kwargs["target"]
            target()
            self.assertEqual(wait_owned.call_count, 2)

        self.assertEqual(
            anchor.signal.call_args_list,
            [
                mock.call(signal.SIGTERM),
                mock.call(signal.SIGKILL),
                mock.call(signal.SIGKILL),
            ],
        )
        thread.start.assert_called_once_with()

    def test_resistant_descendant_gets_kill_after_leader_exits_on_term(self) -> None:
        parallel = load_module(PARALLEL, "prospective_advisory_parallel_group_anchor")
        program = (
            "import os, signal, sys, time\n"
            "child = os.fork()\n"
            "if child == 0:\n"
            "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "    print('ready', flush=True)\n"
            "    time.sleep(30)\n"
            "else:\n"
            "    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
            "    time.sleep(30)\n"
        )
        process = subprocess.Popen(
            (sys.executable, "-c", program),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        assert process.stdout is not None
        self.assertEqual(process.stdout.readline(), b"ready\n")
        real_killpg = os.killpg
        signals: list[int] = []

        def record_signal(process_group_id: int, signal_number: int) -> None:
            signals.append(signal_number)
            real_killpg(process_group_id, signal_number)

        try:
            with mock.patch(
                "weightclass.process_context.os.killpg",
                side_effect=record_signal,
            ):
                parallel._terminate_process_group(process)
        finally:
            if process.returncode is None:
                real_killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

        self.assertEqual(signals[:2], [signal.SIGTERM, signal.SIGKILL])

    def test_heartbeat_and_completion_callbacks_are_task_free_and_ordered(self) -> None:
        parallel = load_module(PARALLEL, "prospective_advisory_parallel_progress")
        events: list[tuple[str, str, int]] = []
        results = parallel.run_parallel(
            (
                parallel.AdvisoryJob(
                    "slow",
                    (sys.executable, "-c", "import time;time.sleep(.08)"),
                ),
                parallel.AdvisoryJob("fast", (sys.executable, "-c", "pass")),
            ),
            progress=lambda label, event, elapsed: events.append((label, event, elapsed)),
            heartbeat_seconds=0.01,
        )

        self.assertEqual([result.returncode for result in results], [0, 0])
        self.assertIn(("fast", "completed", 0), events)
        self.assertTrue(any(label == "slow" and event == "heartbeat" for label, event, _ in events))
        self.assertTrue(any(label == "slow" and event == "completed" for label, event, _ in events))

    def test_result_callback_streams_completion_order_while_return_stays_input_order(self) -> None:
        parallel = load_module(PARALLEL, "prospective_advisory_parallel_results")
        streamed: list[str] = []
        results = parallel.run_parallel(
            (
                parallel.AdvisoryJob(
                    "slow",
                    (sys.executable, "-c", "import time;time.sleep(.1)"),
                ),
                parallel.AdvisoryJob("fast", (sys.executable, "-c", "pass")),
            ),
            result_callback=lambda result: streamed.append(result.label),
        )

        self.assertEqual(streamed, ["fast", "slow"])
        self.assertEqual([result.label for result in results], ["slow", "fast"])


if __name__ == "__main__":
    unittest.main()
