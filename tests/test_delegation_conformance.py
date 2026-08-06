import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from weightclass.delegation_conformance import run_conformance
from weightclass.delegation_qualification import (
    QualificationInvalidInputError,
    build_qualification_candidate,
    load_packaged_qualification_registry,
)

FAKE_DRIVER = Path(__file__).parent / "fixtures" / "fake_conformance_driver.py"


def _write_executable(path: Path, contents: bytes) -> None:
    path.write_bytes(contents)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class DelegationConformanceRunnerTests(unittest.TestCase):
    def _arguments(self, runtime_path: Path) -> list[str]:
        return [
            sys.executable,
            "-m",
            "weightclass.delegation_conformance",
            "--driver",
            str(FAKE_DRIVER),
            "--runtime",
            str(runtime_path),
            "--runtime-build-id",
            "opaque-runtime-build",
            "--adapter-id",
            "claude-native-v1",
            "--vendor-family",
            "claude",
        ]

    def _run_with_mode(
        self,
        directory: Path,
        *,
        mode: str,
        target: str,
        pid_path: Path | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        runtime_path = directory / "runtime"
        _write_executable(runtime_path, b"unqualified-test-runtime\n")
        environment = os.environ.copy()
        environment["WEIGHTCLASS_FAKE_CONFORMANCE_MODE"] = mode
        environment["WEIGHTCLASS_FAKE_CONFORMANCE_TARGET"] = target
        if pid_path is not None:
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_PID_PATH"] = str(pid_path)
        result = subprocess.run(
            self._arguments(runtime_path),
            capture_output=True,
            check=False,
            env=environment,
            text=True,
            timeout=15,
        )
        return result, runtime_path

    def test_full_run_emits_candidate_compatible_evidence_without_reading_stdin(self) -> None:
        """Breaks if the runner skips cases, consumes a task, or changes the evidence schema."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            _write_executable(runtime_path, b"unqualified-test-runtime\n")
            process = subprocess.Popen(
                self._arguments(runtime_path),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                return_code = process.wait(timeout=15)
                stdout, stderr = process.communicate(timeout=1)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()

            self.assertEqual(return_code, 0, stderr)
            self.assertEqual(stderr, "")
            evidence = json.loads(stdout)
            candidate = build_qualification_candidate(evidence, runtime_path)

        result_matrix = evidence["result_matrix"]
        scenario_results = evidence["scenario_results"]
        self.assertEqual(len(result_matrix), 54)
        self.assertEqual(len(scenario_results), 13)
        self.assertTrue(all(result["passed"] is True for result in result_matrix))
        self.assertTrue(all(result["passed"] is True for result in scenario_results))
        self.assertEqual(candidate["runtime_build_id"], "opaque-runtime-build")
        self.assertEqual(load_packaged_qualification_registry().records, ())

    def test_failed_case_produces_complete_rejected_evidence(self) -> None:
        """Breaks if one driver failure is hidden or converted into a candidate."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            result, runtime_path = self._run_with_mode(
                Path(temporary_directory),
                mode="fail",
                target="scenario/stage_order",
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr, '{"error": "conformance_failed"}\n')
            evidence = json.loads(result.stdout)

            with self.assertRaises(QualificationInvalidInputError):
                build_qualification_candidate(evidence, runtime_path)

        self.assertEqual(len(evidence["result_matrix"]), 54)
        self.assertEqual(len(evidence["scenario_results"]), 13)
        failed = [item for item in evidence["scenario_results"] if item["passed"] is False]
        self.assertEqual(failed, [{"id": "stage_order", "passed": False}])

    def test_evidence_is_bound_to_runtime_bytes_observed_by_runner(self) -> None:
        """Breaks if a different post-suite artifact can inherit passing evidence."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            result, runtime_path = self._run_with_mode(
                Path(temporary_directory),
                mode="pass",
                target="",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            evidence = json.loads(result.stdout)
            original_contents = runtime_path.read_bytes()
            runtime_path.write_bytes(b"U" + original_contents[1:])

            with self.assertRaises(QualificationInvalidInputError):
                build_qualification_candidate(evidence, runtime_path)

        self.assertEqual(evidence["artifact_size_bytes"], len(original_contents))
        self.assertEqual(
            evidence["artifact_sha256"],
            hashlib.sha256(original_contents).hexdigest(),
        )

    def test_spoofed_case_id_and_oversized_valid_json_fail_closed(self) -> None:
        """Breaks if a driver can answer another case or exceed the output contract."""
        for mode, target in (
            ("spoof", "scenario/reviewer_rejection"),
            ("oversized", "scenario/output_channel_separation"),
        ):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary_directory:
                result, _ = self._run_with_mode(
                    Path(temporary_directory),
                    mode=mode,
                    target=target,
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr, '{"error": "conformance_failed"}\n')
                evidence = json.loads(result.stdout)
                scenario_id = target.removeprefix("scenario/")
                failed = [
                    item for item in evidence["scenario_results"] if item["id"] == scenario_id
                ]
                self.assertEqual(failed, [{"id": scenario_id, "passed": False}])

    def test_runtime_mutation_during_suite_fails_artifact_integrity_scenario(self) -> None:
        """Breaks if the runner does not recheck the artifact after all driver cases."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            result, _ = self._run_with_mode(
                Path(temporary_directory),
                mode="mutate-runtime",
                target="scenario/stage_order",
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr, '{"error": "conformance_failed"}\n')
            evidence = json.loads(result.stdout)

        failed = [item for item in evidence["scenario_results"] if item["passed"] is False]
        self.assertEqual(
            failed,
            [{"id": "artifact_integrity_and_substitution", "passed": False}],
        )

    def test_descendant_leak_is_observed_failed_and_process_group_is_cleaned(self) -> None:
        """Breaks if driver self-report hides a live descendant or cleanup leaves it running."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            pid_path = directory / "descendant.pid"
            result, _ = self._run_with_mode(
                directory,
                mode="leak",
                target="scenario/descendant_leakage",
                pid_path=pid_path,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr, '{"error": "conformance_failed"}\n')
            descendant_pid = int(pid_path.read_text(encoding="ascii"))
            for _ in range(100):
                try:
                    os.kill(descendant_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.01)
            evidence = json.loads(result.stdout)

        failed = [
            item for item in evidence["scenario_results"] if item["id"] == "descendant_leakage"
        ]
        self.assertEqual(failed, [{"id": "descendant_leakage", "passed": False}])
        with self.assertRaises(ProcessLookupError):
            os.kill(descendant_pid, 0)

    def test_hanging_driver_times_out_one_case_and_is_reaped(self) -> None:
        """Breaks if one stuck driver blocks the suite or survives its case deadline."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            pid_path = directory / "driver.pid"
            _write_executable(runtime_path, b"unqualified-test-runtime\n")
            environment = os.environ.copy()
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_MODE"] = "hang"
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_TARGET"] = "scenario/runtime_deadline"
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_PID_PATH"] = str(pid_path)
            started_at = time.monotonic()
            with (
                mock.patch.dict(os.environ, environment, clear=True),
                mock.patch(
                    "weightclass.delegation_conformance.CASE_TIMEOUT_SECONDS",
                    0.1,
                ),
            ):
                evidence, passed = run_conformance(
                    FAKE_DRIVER,
                    runtime_path,
                    runtime_build_id="opaque-runtime-build",
                    adapter_id="claude-native-v1",
                    vendor_family="claude",
                )
            elapsed = time.monotonic() - started_at
            driver_pid = int(pid_path.read_text(encoding="ascii"))

        self.assertFalse(passed)
        self.assertLess(elapsed, 5)
        scenario_results = evidence["scenario_results"]
        assert isinstance(scenario_results, list)
        failed = [
            item
            for item in scenario_results
            if isinstance(item, dict) and item["id"] == "runtime_deadline"
        ]
        self.assertEqual(failed, [{"id": "runtime_deadline", "passed": False}])
        with self.assertRaises(ProcessLookupError):
            os.kill(driver_pid, 0)

    def test_interrupt_reaps_active_driver_without_traceback(self) -> None:
        """Breaks if Ctrl-C abandons the driver's new session or prints a traceback."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            pid_path = directory / "driver.pid"
            _write_executable(runtime_path, b"unqualified-test-runtime\n")
            environment = os.environ.copy()
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_MODE"] = "hang"
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_TARGET"] = (
                "matrix/orchestrator/implementation/workspace_read/allow"
            )
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_PID_PATH"] = str(pid_path)
            process = subprocess.Popen(
                self._arguments(runtime_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                text=True,
            )
            driver_pid: int | None = None
            try:
                for _ in range(500):
                    if pid_path.exists():
                        driver_pid = int(pid_path.read_text(encoding="ascii"))
                        break
                    if process.poll() is not None:
                        break
                    time.sleep(0.01)
                self.assertIsNotNone(driver_pid)
                process.send_signal(signal.SIGINT)
                stdout, stderr = process.communicate(timeout=5)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                if driver_pid is not None:
                    try:
                        os.kill(driver_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

        self.assertEqual(process.returncode, 130)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, '{"error": "interrupted"}\n')
        assert driver_pid is not None
        with self.assertRaises(ProcessLookupError):
            os.kill(driver_pid, 0)


if __name__ == "__main__":
    unittest.main()
