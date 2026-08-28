from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
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

    def test_invalid_or_duplicate_jobs_fail_before_start(self) -> None:
        parallel = load_module(PARALLEL, "prospective_advisory_parallel_validation")
        valid = parallel.AdvisoryJob("codex", (sys.executable, "-c", "pass"))
        invalid_cases = (
            (),
            (valid, valid),
            (parallel.AdvisoryJob("BAD LABEL", valid.command),),
            (parallel.AdvisoryJob("empty", ()),),
            (parallel.AdvisoryJob("nul", (sys.executable, "bad\x00arg")),),
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
        thread = mock.Mock()
        with (
            mock.patch.object(parallel.time, "sleep"),
            mock.patch.object(parallel.os, "killpg"),
            mock.patch.object(
                process,
                "wait",
                side_effect=(subprocess.TimeoutExpired(("child",), 1.0), 0),
            ) as wait,
            mock.patch.object(parallel.threading, "Thread", return_value=thread) as factory,
        ):
            parallel._terminate_process_group(process)
            target = factory.call_args.kwargs["target"]
            target()
            self.assertEqual(wait.call_count, 2)

        thread.start.assert_called_once_with()

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
