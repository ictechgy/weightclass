from __future__ import annotations

import errno
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from weightclass import json_input, triage
from weightclass.advisory import (
    advisory_campaign,
    advisory_parallel,
    bounded_capture,
    managed_advisory,
    speculative_report,
    speculative_run,
)
from weightclass.delegation_runtime import (
    DelegationRuntimeUnavailableError,
    run_delegation_runtime,
    validate_delegation_runtime,
)
from weightclass.delegation_types import DirectChildCleanup
from weightclass.executable_observation import ExecutableObservation


class SecurityPerformanceHardeningTests(unittest.TestCase):
    def test_shared_json_loader_rejects_large_integer_before_schema_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text('{"value":' + "9" * 129 + "}", encoding="ascii")
            with self.assertRaisesRegex(json_input.JsonInputError, "^$"):
                json_input.load_json_object(path, max_bytes=1_024)

    def test_campaign_loader_rejects_excess_records_while_streaming(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.jsonl"
            path.write_text("{}\n{}\n{}\n", encoding="ascii")
            with (
                mock.patch.object(advisory_campaign, "MAX_TASKS", 2),
                self.assertRaisesRegex(
                    advisory_campaign.CampaignError,
                    "^campaign_record_capacity_exceeded$",
                ),
            ):
                advisory_campaign.load_bound_records(path)

    def test_legacy_report_rejects_symlink_and_excess_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "runs.jsonl"
            log.write_text("{}\n{}\n", encoding="ascii")
            link = root / "linked.jsonl"
            link.symlink_to(log)
            with self.assertRaises(advisory_campaign.CampaignError):
                speculative_report._legacy_log_lines(link)
            with (
                mock.patch.object(speculative_report, "MAX_TASKS", 1),
                self.assertRaisesRegex(
                    advisory_campaign.CampaignError,
                    "^campaign_record_capacity_exceeded$",
                ),
            ):
                speculative_report._legacy_log_lines(log)

    def test_legacy_token_scrape_rejects_oversized_counter(self) -> None:
        oversized = "9" * (speculative_run.MAX_TOKEN_COUNTER_DIGITS + 1)
        self.assertIsNone(speculative_run.extract_tokens("", f"tokens used {oversized}"))

    def test_managed_git_preflight_disables_repository_fsmonitor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            marker = root / "fsmonitor-ran"
            fsmonitor = root / "fsmonitor"
            fsmonitor.write_text(
                "#!/bin/sh\nprintf '%s' hit > \"$1\"\nprintf '\\n'\n".replace(
                    '"$1"', f'"{marker}"'
                ),
                encoding="utf-8",
            )
            fsmonitor.chmod(0o700)
            verifier = root / "verify"
            verifier.write_text("#!/bin/sh\nexit 42\n", encoding="ascii")
            verifier.chmod(0o700)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(
                ["git", "config", "core.fsmonitor", str(fsmonitor)], cwd=repo, check=True
            )

            managed_advisory._preflight_repo(repo, "review", verifier)

            self.assertFalse(marker.exists())

    def test_protocol_one_rejects_symlink_and_spawn_adjacent_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
            runtime.chmod(0o700)
            link = root / "runtime-link"
            link.symlink_to(runtime)
            with self.assertRaisesRegex(DelegationRuntimeUnavailableError, "^$"):
                validate_delegation_runtime(str(link))

        expected = ExecutableObservation("/runtime", 1, 1, stat.S_IFREG, 0o700, 1, 1, 1, True)
        changed = ExecutableObservation("/runtime", 1, 2, stat.S_IFREG, 0o700, 1, 1, 1, True)
        with (
            mock.patch(
                "weightclass.delegation_runtime.validate_delegation_runtime",
                return_value=changed,
            ),
            mock.patch(
                "weightclass.delegation_runtime.subprocess.Popen",
                side_effect=AssertionError("replacement must not spawn"),
            ) as popen,
            self.assertRaisesRegex(DelegationRuntimeUnavailableError, "^$"),
        ):
            run_delegation_runtime(
                "/runtime",
                b"frame",
                DirectChildCleanup(1, 1),
                expected,
            )
        popen.assert_not_called()

    def test_triage_status_loss_releases_numeric_signal_targets(self) -> None:
        process = mock.Mock()
        process.stdin = None
        process.stdout = None
        with (
            mock.patch.object(triage, "signal_process_group") as signal_group,
            mock.patch.object(triage, "wait_owned_child") as wait_child,
            mock.patch.object(triage, "close_leader_exit_queue"),
        ):
            failed, return_code, pending = triage._cleanup_vendor_process(
                process,
                None,
                None,
                None,
                None,
                True,
                bytearray(),
                time.monotonic() + 1,
                time.monotonic() + 2,
                True,
            )

        self.assertTrue(failed)
        self.assertIsNone(return_code)
        self.assertIsNone(pending)
        signal_group.assert_not_called()
        wait_child.assert_not_called()

    def test_triage_native_echild_is_classified_as_status_loss(self) -> None:
        self.assertTrue(triage._child_status_lost(OSError(errno.ECHILD, "already reaped")))
        self.assertFalse(triage._child_status_lost(OSError(errno.EIO, "other failure")))

    def test_bounded_capture_terminates_output_overflow(self) -> None:
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys,time;sys.stdout.write('x'*4096);sys.stdout.flush();time.sleep(5)",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )

        def terminate_group(child: subprocess.Popen[str]) -> None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        result = bounded_capture.capture_text_process(
            process,
            None,
            timeout_seconds=2,
            max_output_bytes=64,
            terminate_group=terminate_group,
        )

        self.assertTrue(result.output_limited)
        self.assertLessEqual(len(result.stdout.encode("utf-8")), 64)
        self.assertIsNotNone(process.returncode)

    def test_parallel_interrupt_cancels_owned_job_without_waiting_for_deadline(self) -> None:
        started = time.monotonic()
        with (
            mock.patch(
                "weightclass.advisory.advisory_parallel.wait",
                side_effect=KeyboardInterrupt,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            advisory_parallel.run_parallel(
                (
                    advisory_parallel.AdvisoryJob(
                        "slow",
                        (sys.executable, "-c", "import time;time.sleep(60)"),
                        timeout_seconds=60,
                    ),
                )
            )

        self.assertLess(time.monotonic() - started, 3)

    def test_parallel_cancel_is_checked_after_output_pipes_close(self) -> None:
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import os,time;os.close(1);os.close(2);time.sleep(60)",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        cancel = threading.Event()
        timer = threading.Timer(0.1, cancel.set)
        timer.start()
        started = time.monotonic()
        try:
            result = advisory_parallel._capture_job(
                process,
                advisory_parallel.AdvisoryJob("closed", ("unused",), timeout_seconds=60),
                cancel,
            )
        finally:
            timer.cancel()

        self.assertEqual(result.returncode, 130)
        self.assertLess(time.monotonic() - started, 3)

    def test_managed_import_defers_speculative_runner(self) -> None:
        program = """
import json, sys
import weightclass.advisory.managed_advisory
print(json.dumps('weightclass.advisory.speculative_run' in sys.modules))
"""
        completed = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            check=True,
            text=True,
        )
        self.assertIs(json.loads(completed.stdout), False)


if __name__ == "__main__":
    unittest.main()
