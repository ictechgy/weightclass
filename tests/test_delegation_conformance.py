import hashlib
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
from typing import Any, cast
from unittest import mock

from weightclass.delegation_conformance import (
    CONFORMANCE_CASES,
    ConformanceCase,
    _DeferredSigint,
    _linux_proc_stat_live_group_member,
    _run_driver_case,
    run_conformance,
)
from weightclass.delegation_qualification import (
    QualificationInvalidInputError,
    build_qualification_candidate,
    load_packaged_qualification_registry,
)

FAKE_DRIVER = Path(__file__).parent / "fixtures" / "fake_conformance_driver.py"
FIXED_SENTINEL_RUNTIME = Path(__file__).parent / "fixtures" / "fixed_conformance_sentinel.py"

_SIGNAL_CASE_RUNNER = r"""
import os
import signal
import sys
import time
from pathlib import Path

import weightclass.delegation_conformance as conformance

driver_path = Path(sys.argv[1])
runtime_path = Path(sys.argv[2])
workspace_path = Path(sys.argv[3])
handler_mode = sys.argv[4]
handler_path = Path(sys.argv[5])
cleanup_path = Path(sys.argv[6])

if cleanup_path.name != "-":
    original_cleanup = conformance._DriverCaseOwnership.cleanup

    def delayed_cleanup(ownership):
        cleanup_path.write_text("started", encoding="ascii")
        time.sleep(0.3)
        original_cleanup(ownership)

    conformance._DriverCaseOwnership.cleanup = delayed_cleanup

if handler_mode == "default":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
elif handler_mode == "ignore":
    signal.signal(signal.SIGINT, signal.SIG_IGN)
elif handler_mode == "callable":
    def previous_handler(signal_number, frame):
        del signal_number, frame
        driver_pid = int(Path(os.environ["WEIGHTCLASS_FAKE_CONFORMANCE_PID_PATH"]).read_text())
        try:
            os.kill(driver_pid, 0)
        except ProcessLookupError:
            state = "gone"
        else:
            state = "alive"
        with handler_path.open("a", encoding="ascii") as stream:
            stream.write(state + "\n")

    signal.signal(signal.SIGINT, previous_handler)
else:
    raise SystemExit(90)

try:
    passed = conformance._run_driver_case(
        driver_path,
        runtime_path,
        conformance.CONFORMANCE_CASES[0],
        workspace_path,
        timeout_seconds=0.5,
    )
except KeyboardInterrupt:
    raise SystemExit(130)

if handler_mode == "ignore" and signal.getsignal(signal.SIGINT) != signal.SIG_IGN:
    raise SystemExit(91)
raise SystemExit(0 if passed else 1)
"""

_SIGCHLD_CLI_RUNNER = r"""
import signal
import sys

import weightclass.delegation_conformance as conformance

mode = sys.argv[1]
if mode == "ignore":
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)
elif mode == "callable":
    def sigchld_handler(signal_number, frame):
        del signal_number, frame

    signal.signal(signal.SIGCHLD, sigchld_handler)
else:
    raise SystemExit(90)

conformance.CONFORMANCE_CASES = conformance.CONFORMANCE_CASES[:1]
conformance.CASE_TIMEOUT_SECONDS = 0.1
raise SystemExit(conformance.main(sys.argv[2:]))
"""


def _write_executable(path: Path, contents: bytes) -> None:
    path.write_bytes(contents)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class DelegationConformanceRunnerTests(unittest.TestCase):
    def _wait_for_pid_publication(
        self,
        pid_path: Path,
        process: subprocess.Popen[str],
    ) -> tuple[int, int]:
        for _ in range(500):
            if pid_path.exists():
                pid = int(pid_path.read_text(encoding="ascii"))
                return pid, os.getpgid(pid)
            if process.poll() is not None:
                break
            time.sleep(0.01)
        self.fail("driver PID was not published")

    def _wait_for_path(self, path: Path, process: subprocess.Popen[str]) -> None:
        for _ in range(500):
            if path.exists():
                return
            if process.poll() is not None:
                break
            time.sleep(0.01)
        self.fail("expected subprocess marker was not published")

    def _assert_owned_process_group_gone(self, process_group_id: int, member_pid: int) -> None:
        for _ in range(100):
            try:
                os.kill(member_pid, 0)
            except ProcessLookupError:
                try:
                    os.killpg(process_group_id, 0)
                except (PermissionError, ProcessLookupError):
                    return
            time.sleep(0.01)
        self.fail("owned process group survived cleanup")

    def _cleanup_owned_process_group(
        self,
        process_group_id: int,
        member_pid: int,
        process: subprocess.Popen[Any] | None = None,
    ) -> None:
        try:
            if os.getpgid(member_pid) == process_group_id:
                os.killpg(process_group_id, signal.SIGKILL)
        except (PermissionError, ProcessLookupError):
            pass
        if process is not None:
            try:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=1)
            except (ChildProcessError, OSError, ValueError, subprocess.TimeoutExpired):
                pass
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    try:
                        stream.close()
                    except (OSError, ValueError):
                        pass

    def _cleanup_test_process(self, process: subprocess.Popen[Any]) -> None:
        try:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=1)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass

    def _start_signal_case(
        self,
        directory: Path,
        *,
        handler_mode: str,
        delayed_cleanup: bool = False,
    ) -> tuple[subprocess.Popen[str], Path, Path]:
        runtime_path = directory / "runtime"
        workspace_path = directory / "workspace"
        pid_path = directory / "driver.pid"
        handler_path = directory / "handler.log"
        cleanup_path = directory / ("cleanup.started" if delayed_cleanup else "-")
        workspace_path.mkdir(mode=0o700)
        _write_executable(runtime_path, b"unqualified-test-runtime\n")
        environment = os.environ.copy()
        environment["WEIGHTCLASS_FAKE_CONFORMANCE_MODE"] = "hang"
        environment["WEIGHTCLASS_FAKE_CONFORMANCE_TARGET"] = CONFORMANCE_CASES[0].case_id
        environment["WEIGHTCLASS_FAKE_CONFORMANCE_PID_PATH"] = str(pid_path)
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _SIGNAL_CASE_RUNNER,
                str(FAKE_DRIVER),
                str(runtime_path),
                str(workspace_path),
                handler_mode,
                str(handler_path),
                str(cleanup_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=environment,
            text=True,
        )
        return process, pid_path, cleanup_path

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

    def _run_indistinguishability_mode(
        self,
        mode: str,
    ) -> tuple[dict[str, object], dict[str, object], bool]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            _write_executable(runtime_path, FIXED_SENTINEL_RUNTIME.read_bytes())
            environment = os.environ.copy()
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_MODE"] = mode
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_TARGET"] = (
                "matrix/orchestrator/implementation/workspace_read/allow"
            )
            result = subprocess.run(
                self._arguments(runtime_path),
                capture_output=True,
                check=False,
                env=environment,
                text=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            parsed: object = json.loads(result.stdout)
            self.assertIsInstance(parsed, dict)
            evidence = cast(dict[str, object], parsed)
            candidate = build_qualification_candidate(evidence, runtime_path)
            marker_present = runtime_path.with_suffix(".marker").is_file()
        return evidence, candidate, marker_present

    def test_v2_cannot_distinguish_runtime_invocation_skip_forgery_or_self_attestation(
        self,
    ) -> None:
        """Breaks if this known v2 observability gap is hidden or misstated."""
        self.assertTrue(FIXED_SENTINEL_RUNTIME.is_file())
        modes = (
            ("invoke-runtime", True),
            ("skip-runtime", False),
            ("forge-runtime-marker", True),
            ("self-attest", False),
        )
        evidence_documents: list[dict[str, object]] = []
        candidates: list[dict[str, object]] = []
        marker_observations: list[bool] = []

        for mode, _ in modes:
            evidence, candidate, marker_present = self._run_indistinguishability_mode(mode)
            evidence_documents.append(evidence)
            candidates.append(candidate)
            marker_observations.append(marker_present)

        self.assertEqual(marker_observations, [expected for _, expected in modes])
        self.assertTrue(all(evidence == evidence_documents[0] for evidence in evidence_documents))
        self.assertTrue(all(candidate == candidates[0] for candidate in candidates))
        self.assertEqual(load_packaged_qualification_registry().records, ())

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
            runtime_path = directory / "runtime"
            pid_path = directory / "descendant.pid"
            _write_executable(runtime_path, b"unqualified-test-runtime\n")
            environment = os.environ.copy()
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_MODE"] = "leak"
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_TARGET"] = "scenario/descendant_leakage"
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_PID_PATH"] = str(pid_path)
            real_popen = cast(Any, subprocess.Popen)
            from weightclass import delegation_conformance as conformance_module

            real_group_exists = conformance_module._process_group_exists
            spawned: list[tuple[subprocess.Popen[bytes], int]] = []
            observed_descendants: list[tuple[int, int]] = []

            def capture_process(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
                process = cast(subprocess.Popen[bytes], real_popen(*args, **kwargs))
                spawned.append((process, os.getpgid(process.pid)))
                return process

            def observe_group(process_group_id: int) -> bool:
                if pid_path.exists() and not observed_descendants:
                    descendant_pid = int(pid_path.read_text(encoding="ascii"))
                    observed_descendants.append((descendant_pid, os.getpgid(descendant_pid)))
                return real_group_exists(process_group_id)

            try:
                with (
                    mock.patch.dict(os.environ, environment, clear=True),
                    mock.patch(
                        "weightclass.delegation_conformance.subprocess.Popen",
                        side_effect=capture_process,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._process_group_exists",
                        side_effect=observe_group,
                    ),
                ):
                    evidence, passed = run_conformance(
                        FAKE_DRIVER,
                        runtime_path,
                        runtime_build_id="opaque-runtime-build",
                        adapter_id="claude-native-v1",
                        vendor_family="claude",
                    )

                self.assertFalse(passed)
                self.assertEqual(len(observed_descendants), 1)
                descendant_pid, descendant_process_group_id = observed_descendants[0]
                self.assertIn(
                    descendant_process_group_id,
                    {process_group_id for _, process_group_id in spawned},
                )
                scenario_results = evidence["scenario_results"]
                assert isinstance(scenario_results, list)
                failed = [item for item in scenario_results if item["id"] == "descendant_leakage"]
                self.assertEqual(failed, [{"id": "descendant_leakage", "passed": False}])
                self._assert_owned_process_group_gone(
                    descendant_process_group_id,
                    descendant_pid,
                )
            finally:
                for descendant_pid, process_group_id in observed_descendants:
                    self._cleanup_owned_process_group(process_group_id, descendant_pid)
                for process, process_group_id in spawned:
                    self._cleanup_owned_process_group(
                        process_group_id,
                        process.pid,
                        process,
                    )

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
            real_popen = cast(Any, subprocess.Popen)
            spawned: dict[int, tuple[subprocess.Popen[bytes], int]] = {}

            def capture_process(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
                process = cast(subprocess.Popen[bytes], real_popen(*args, **kwargs))
                spawned[process.pid] = (process, os.getpgid(process.pid))
                return process

            def run_with_target_timeout(
                driver_path: Path,
                selected_runtime_path: Path,
                conformance_case: ConformanceCase,
                workspace_path: Path,
                *,
                timeout_seconds: float,
            ) -> bool:
                if conformance_case.case_id == "scenario/runtime_deadline":
                    timeout_seconds = 0.1
                return _run_driver_case(
                    driver_path,
                    selected_runtime_path,
                    conformance_case,
                    workspace_path,
                    timeout_seconds=timeout_seconds,
                )

            driver_pid: int | None = None
            driver_process_group_id: int | None = None
            try:
                started_at = time.monotonic()
                with (
                    mock.patch.dict(os.environ, environment, clear=True),
                    mock.patch(
                        "weightclass.delegation_conformance.subprocess.Popen",
                        side_effect=capture_process,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._run_driver_case",
                        side_effect=run_with_target_timeout,
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
                driver_process, driver_process_group_id = spawned[driver_pid]

                self.assertEqual(driver_process_group_id, driver_pid)
                self.assertFalse(passed)
                self.assertLess(elapsed, 5)
                result_matrix = evidence["result_matrix"]
                scenario_results = evidence["scenario_results"]
                assert isinstance(result_matrix, list)
                assert isinstance(scenario_results, list)
                self.assertTrue(
                    all(isinstance(item, dict) and item["passed"] is True for item in result_matrix)
                )
                failed = [
                    item
                    for item in scenario_results
                    if isinstance(item, dict) and item["passed"] is False
                ]
                self.assertEqual(failed, [{"id": "runtime_deadline", "passed": False}])
                self._assert_owned_process_group_gone(driver_process_group_id, driver_pid)
            finally:
                for process, process_group_id in spawned.values():
                    self._cleanup_owned_process_group(
                        process_group_id,
                        process.pid,
                        process,
                    )

    def test_interrupt_during_thread_start_reaps_owned_driver_group(self) -> None:
        """Breaks if interruption before wait ownership starts abandons the driver."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            workspace_path = directory / "workspace"
            workspace_path.mkdir(mode=0o700)
            _write_executable(runtime_path, b"unqualified-test-runtime\n")
            real_popen = cast(Any, subprocess.Popen)
            spawned: list[tuple[subprocess.Popen[bytes], int]] = []

            def capture_process(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
                process = cast(subprocess.Popen[bytes], real_popen(*args, **kwargs))
                spawned.append((process, os.getpgid(process.pid)))
                return process

            try:
                with (
                    mock.patch(
                        "weightclass.delegation_conformance.subprocess.Popen",
                        side_effect=capture_process,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance.threading.Thread.start",
                        side_effect=KeyboardInterrupt,
                    ),
                    self.assertRaises(KeyboardInterrupt),
                ):
                    _run_driver_case(
                        FAKE_DRIVER,
                        runtime_path,
                        CONFORMANCE_CASES[0],
                        workspace_path,
                        timeout_seconds=5,
                    )

                self.assertEqual(len(spawned), 1)
                process, process_group_id = spawned[0]
                self.assertEqual(process_group_id, process.pid)
                self._assert_owned_process_group_gone(process_group_id, process.pid)
            finally:
                for process, process_group_id in spawned:
                    self._cleanup_owned_process_group(
                        process_group_id,
                        process.pid,
                        process,
                    )

    def test_sigint_during_spawn_is_deferred_until_driver_group_is_owned(self) -> None:
        """Breaks if SIGINT can interrupt Popen before the new session is recorded."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            workspace_path = directory / "workspace"
            workspace_path.mkdir(mode=0o700)
            _write_executable(runtime_path, b"unqualified-test-runtime\n")
            real_popen = cast(Any, subprocess.Popen)
            spawned: list[tuple[subprocess.Popen[bytes], int]] = []

            def interrupt_spawn(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
                process = cast(subprocess.Popen[bytes], real_popen(*args, **kwargs))
                spawned.append((process, os.getpgid(process.pid)))
                os.kill(os.getpid(), signal.SIGINT)
                return process

            try:
                with (
                    mock.patch(
                        "weightclass.delegation_conformance.subprocess.Popen",
                        side_effect=interrupt_spawn,
                    ),
                    self.assertRaises(KeyboardInterrupt),
                ):
                    _run_driver_case(
                        FAKE_DRIVER,
                        runtime_path,
                        CONFORMANCE_CASES[0],
                        workspace_path,
                        timeout_seconds=5,
                    )

                self.assertEqual(len(spawned), 1)
                process, process_group_id = spawned[0]
                self.assertEqual(process_group_id, process.pid)
                self._assert_owned_process_group_gone(process_group_id, process.pid)
            finally:
                for process, process_group_id in spawned:
                    self._cleanup_owned_process_group(
                        process_group_id,
                        process.pid,
                        process,
                    )

    def test_sigint_with_default_disposition_cleans_group_before_termination(self) -> None:
        """Breaks if SIG_DFL terminates the runner before its owned group is cleaned."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            process, pid_path, _ = self._start_signal_case(
                Path(temporary_directory),
                handler_mode="default",
            )
            owned_groups: list[tuple[int, int]] = []
            try:
                driver_pid, process_group_id = self._wait_for_pid_publication(pid_path, process)
                owned_groups.append((driver_pid, process_group_id))
                self.assertEqual(driver_pid, process_group_id)
                process.send_signal(signal.SIGINT)
                _, stderr = process.communicate(timeout=5)

                self.assertEqual(process.returncode, -signal.SIGINT)
                self.assertEqual(stderr, "")
                self._assert_owned_process_group_gone(process_group_id, driver_pid)
            finally:
                self._cleanup_test_process(process)
                for driver_pid, process_group_id in owned_groups:
                    self._cleanup_owned_process_group(process_group_id, driver_pid)

    def test_sigint_handler_swap_dispatches_each_signal_once(self) -> None:
        """Breaks if restoring the previous handler loses a boundary SIGINT."""
        received_frames: list[Any] = []

        def previous_handler(signal_number: int, frame: Any) -> None:
            del signal_number
            received_frames.append(frame)

        real_signal = signal.signal
        original_handler = real_signal(signal.SIGINT, previous_handler)
        deferred_sigint = _DeferredSigint()
        deferred_sigint.arm()
        deferred_sigint.record_process_group(12345)
        deferred_sigint.clear_process_group()
        injected = False

        def signal_during_swap(signal_number: int, handler: Any) -> Any:
            nonlocal injected
            if signal_number == signal.SIGINT and handler is previous_handler and not injected:
                injected = True
                os.kill(os.getpid(), signal.SIGINT)
            return real_signal(signal_number, handler)

        try:
            with (
                mock.patch(
                    "weightclass.delegation_conformance.signal.signal",
                    side_effect=signal_during_swap,
                ),
                mock.patch(
                    "weightclass.delegation_conformance._signal_process_group"
                ) as signal_group,
            ):
                deferred_sigint.restore()

            self.assertTrue(injected)
            self.assertEqual(len(received_frames), 1)
            self.assertIsNotNone(received_frames[0])
            signal_group.assert_not_called()
            self.assertIs(signal.getsignal(signal.SIGINT), previous_handler)
            os.kill(os.getpid(), signal.SIGINT)
            self.assertEqual(len(received_frames), 2)
            self.assertIsNotNone(received_frames[1])
        finally:
            real_signal(signal.SIGINT, original_handler)

    def test_sigint_callable_runs_only_after_owned_group_cleanup(self) -> None:
        """Breaks if a previous callable handler observes the driver before cleanup."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            process, pid_path, _ = self._start_signal_case(
                directory,
                handler_mode="callable",
            )
            owned_groups: list[tuple[int, int]] = []
            try:
                driver_pid, process_group_id = self._wait_for_pid_publication(pid_path, process)
                owned_groups.append((driver_pid, process_group_id))
                process.send_signal(signal.SIGINT)
                _, stderr = process.communicate(timeout=5)

                self.assertEqual(process.returncode, 1)
                self.assertEqual(stderr, "")
                self.assertEqual(
                    (directory / "handler.log").read_text(encoding="ascii").splitlines(),
                    ["gone"],
                )
                self._assert_owned_process_group_gone(process_group_id, driver_pid)
            finally:
                self._cleanup_test_process(process)
                for driver_pid, process_group_id in owned_groups:
                    self._cleanup_owned_process_group(process_group_id, driver_pid)

    def test_sigint_ignore_disposition_is_preserved_through_timeout_cleanup(self) -> None:
        """Breaks if installing the deferred handler changes an ignored SIGINT."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            process, pid_path, _ = self._start_signal_case(
                directory,
                handler_mode="ignore",
            )
            owned_groups: list[tuple[int, int]] = []
            try:
                driver_pid, process_group_id = self._wait_for_pid_publication(pid_path, process)
                owned_groups.append((driver_pid, process_group_id))
                started_at = time.monotonic()
                process.send_signal(signal.SIGINT)
                _, stderr = process.communicate(timeout=5)

                self.assertEqual(process.returncode, 1)
                self.assertEqual(stderr, "")
                self.assertGreaterEqual(time.monotonic() - started_at, 0.2)
                self.assertFalse((directory / "handler.log").exists())
                self._assert_owned_process_group_gone(process_group_id, driver_pid)
            finally:
                self._cleanup_test_process(process)
                for driver_pid, process_group_id in owned_groups:
                    self._cleanup_owned_process_group(process_group_id, driver_pid)

    def test_two_sigints_during_cleanup_are_deferred_until_group_is_gone(self) -> None:
        """Breaks if a second SIGINT can interrupt the ownership cleanup scope."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            process, pid_path, cleanup_path = self._start_signal_case(
                directory,
                handler_mode="callable",
                delayed_cleanup=True,
            )
            owned_groups: list[tuple[int, int]] = []
            try:
                driver_pid, process_group_id = self._wait_for_pid_publication(pid_path, process)
                owned_groups.append((driver_pid, process_group_id))
                process.send_signal(signal.SIGINT)
                self._wait_for_path(cleanup_path, process)
                process.send_signal(signal.SIGINT)
                _, stderr = process.communicate(timeout=5)

                self.assertEqual(process.returncode, 1)
                self.assertEqual(stderr, "")
                self.assertEqual(
                    (directory / "handler.log").read_text(encoding="ascii").splitlines(),
                    ["gone"],
                )
                self._assert_owned_process_group_gone(process_group_id, driver_pid)
            finally:
                self._cleanup_test_process(process)
                for driver_pid, process_group_id in owned_groups:
                    self._cleanup_owned_process_group(process_group_id, driver_pid)

    def test_group_is_killed_and_checked_before_the_single_final_reap(self) -> None:
        """Breaks if wait reaps the leader before group ownership cleanup finishes."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            workspace_path = directory / "workspace"
            workspace_path.mkdir(mode=0o700)
            _write_executable(runtime_path, b"unqualified-test-runtime\n")
            real_popen = cast(Any, subprocess.Popen)
            spawned: list[tuple[subprocess.Popen[bytes], int]] = []
            events: list[tuple[str, int | None]] = []

            def capture_process(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
                process = cast(subprocess.Popen[bytes], real_popen(*args, **kwargs))
                spawned.append((process, os.getpgid(process.pid)))
                return process

            from weightclass import delegation_conformance as conformance_module

            real_group_exists = conformance_module._process_group_exists
            signal_group = conformance_module._signal_process_group
            wait_after_kill = conformance_module._wait_after_kill

            def observe_group(process_group_id: int) -> bool:
                events.append(("check", spawned[0][0].returncode))
                return real_group_exists(process_group_id)

            def observe_signal(process_group_id: int, signal_number: int) -> None:
                events.append(("kill", spawned[0][0].returncode))
                signal_group(process_group_id, signal_number)

            def observe_reap(process: subprocess.Popen[bytes]) -> int:
                events.append(("reap", process.returncode))
                return wait_after_kill(process)

            try:
                with (
                    mock.patch(
                        "weightclass.delegation_conformance.subprocess.Popen",
                        side_effect=capture_process,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._process_group_exists",
                        side_effect=observe_group,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._signal_process_group",
                        side_effect=observe_signal,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._wait_after_kill",
                        side_effect=observe_reap,
                    ),
                ):
                    passed = _run_driver_case(
                        FAKE_DRIVER,
                        runtime_path,
                        CONFORMANCE_CASES[0],
                        workspace_path,
                        timeout_seconds=5,
                    )

                self.assertTrue(passed)
                self.assertEqual(sum(name == "reap" for name, _ in events), 1)
                reap_index = next(index for index, event in enumerate(events) if event[0] == "reap")
                self.assertLess(
                    next(i for i, event in enumerate(events) if event[0] == "kill"), reap_index
                )
                self.assertLess(
                    max(i for i, event in enumerate(events) if event[0] == "check"), reap_index
                )
                self.assertTrue(
                    all(return_code is None for _, return_code in events),
                    events,
                )
            finally:
                for process, process_group_id in spawned:
                    self._cleanup_owned_process_group(
                        process_group_id,
                        process.pid,
                        process,
                    )

    def test_sigint_at_final_reap_boundary_never_targets_released_group(self) -> None:
        """Breaks if deferred SIGINT can target a numeric PGID after final reap."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            workspace_path = directory / "workspace"
            workspace_path.mkdir(mode=0o700)
            _write_executable(runtime_path, b"unqualified-test-runtime\n")
            real_popen = cast(Any, subprocess.Popen)
            spawned: list[tuple[subprocess.Popen[bytes], int]] = []
            events: list[tuple[str, int | None]] = []
            group_checks = 0

            def capture_process(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
                process = cast(subprocess.Popen[bytes], real_popen(*args, **kwargs))
                spawned.append((process, os.getpgid(process.pid)))
                return process

            from weightclass import delegation_conformance as conformance_module

            real_group_exists = conformance_module._process_group_exists
            signal_group = conformance_module._signal_process_group
            wait_after_kill = conformance_module._wait_after_kill

            def observe_group(process_group_id: int) -> bool:
                nonlocal group_checks
                group_checks += 1
                if group_checks == 2:
                    os.kill(os.getpid(), signal.SIGINT)
                events.append(("check", spawned[0][0].returncode))
                return real_group_exists(process_group_id)

            def observe_signal(process_group_id: int, signal_number: int) -> None:
                events.append(("kill", spawned[0][0].returncode))
                signal_group(process_group_id, signal_number)

            def interrupt_reap_boundary(process: subprocess.Popen[bytes]) -> int:
                events.append(("reap_call", process.returncode))
                os.kill(os.getpid(), signal.SIGINT)
                return_code = wait_after_kill(process)
                events.append(("reap_return", process.returncode))
                os.kill(os.getpid(), signal.SIGINT)
                return return_code

            try:
                with (
                    mock.patch(
                        "weightclass.delegation_conformance.subprocess.Popen",
                        side_effect=capture_process,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._process_group_exists",
                        side_effect=observe_group,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._signal_process_group",
                        side_effect=observe_signal,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._wait_after_kill",
                        side_effect=interrupt_reap_boundary,
                    ),
                    self.assertRaises(KeyboardInterrupt),
                ):
                    _run_driver_case(
                        FAKE_DRIVER,
                        runtime_path,
                        CONFORMANCE_CASES[0],
                        workspace_path,
                        timeout_seconds=5,
                    )

                self.assertEqual(len(spawned), 1)
                self.assertEqual(
                    [event for event in events if event[0].startswith("reap_")],
                    [("reap_call", None), ("reap_return", 0)],
                )
                self.assertEqual(
                    [return_code for name, return_code in events if name == "kill"],
                    [None, None],
                    events,
                )
            finally:
                for process, process_group_id in spawned:
                    self._cleanup_owned_process_group(
                        process_group_id,
                        process.pid,
                        process,
                    )

    def test_late_reader_signal_is_disabled_before_final_reap(self) -> None:
        """Breaks if a reader retains a signal target after PGID ownership ends."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            workspace_path = directory / "workspace"
            workspace_path.mkdir(mode=0o700)
            _write_executable(runtime_path, b"unqualified-test-runtime\n")
            real_popen = cast(Any, subprocess.Popen)
            real_thread_join = threading.Thread.join
            spawned: list[tuple[subprocess.Popen[bytes], int]] = []
            reader_started = threading.Event()
            release_reader = threading.Event()
            reader_finished = threading.Event()
            events: list[tuple[str, int | None]] = []
            stream_states_at_reap: list[tuple[bool, bool]] = []

            def capture_process(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
                process = cast(subprocess.Popen[bytes], real_popen(*args, **kwargs))
                spawned.append((process, os.getpgid(process.pid)))
                return process

            from weightclass import delegation_conformance as conformance_module

            signal_group = conformance_module._signal_process_group
            wait_after_kill = conformance_module._wait_after_kill

            def paused_reader(stream: Any, signal_target: Any, state: Any) -> None:
                del stream, state
                reader_started.set()
                release_reader.wait(timeout=5)
                if isinstance(signal_target, int):
                    conformance_module._signal_process_group(signal_target, signal.SIGKILL)
                else:
                    signal_target.signal(signal.SIGKILL)
                reader_finished.set()

            def observe_signal(process_group_id: int, signal_number: int) -> None:
                events.append(("kill", spawned[0][0].returncode))
                signal_group(process_group_id, signal_number)

            def observe_join(thread: threading.Thread, timeout: float | None = None) -> None:
                del timeout
                events.append(("join", spawned[0][0].returncode))
                real_thread_join(thread, timeout=0)

            def release_after_reap(process: subprocess.Popen[bytes]) -> int:
                assert process.stdin is not None
                assert process.stdout is not None
                stream_states_at_reap.append((process.stdin.closed, process.stdout.closed))
                events.append(("reap_call", process.returncode))
                return_code = wait_after_kill(process)
                events.append(("reaped", process.returncode))
                self.assertTrue(reader_started.wait(timeout=1))
                release_reader.set()
                self.assertTrue(reader_finished.wait(timeout=1))
                return return_code

            try:
                with (
                    mock.patch(
                        "weightclass.delegation_conformance.subprocess.Popen",
                        side_effect=capture_process,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._read_response",
                        side_effect=paused_reader,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._signal_process_group",
                        side_effect=observe_signal,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._wait_after_kill",
                        side_effect=release_after_reap,
                    ),
                    mock.patch.object(threading.Thread, "join", new=observe_join),
                ):
                    _run_driver_case(
                        FAKE_DRIVER,
                        runtime_path,
                        CONFORMANCE_CASES[0],
                        workspace_path,
                        timeout_seconds=5,
                    )

                self.assertEqual(
                    [return_code for name, return_code in events if name == "kill"],
                    [None],
                    events,
                )
                self.assertEqual(stream_states_at_reap, [(True, True)])
                self.assertTrue(
                    all(return_code is None for name, return_code in events if name == "join"),
                    events,
                )
                self.assertEqual(sum(name == "reaped" for name, _ in events), 1)
            finally:
                release_reader.set()
                reader_finished.wait(timeout=1)
                for process, process_group_id in spawned:
                    self._cleanup_owned_process_group(
                        process_group_id,
                        process.pid,
                        process,
                    )

    def test_unsafe_sigchld_dispositions_fail_before_driver_spawn(self) -> None:
        """Breaks if inherited SIGCHLD handling can release the PGID anchor."""
        for mode in ("ignore", "callable"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary_directory:
                directory = Path(temporary_directory)
                runtime_path = directory / "runtime"
                pid_path = directory / "driver.pid"
                _write_executable(runtime_path, b"unqualified-test-runtime\n")
                environment = os.environ.copy()
                environment["WEIGHTCLASS_FAKE_CONFORMANCE_MODE"] = "hang"
                environment["WEIGHTCLASS_FAKE_CONFORMANCE_TARGET"] = CONFORMANCE_CASES[0].case_id
                environment["WEIGHTCLASS_FAKE_CONFORMANCE_PID_PATH"] = str(pid_path)
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        _SIGCHLD_CLI_RUNNER,
                        mode,
                        *self._arguments(runtime_path)[3:],
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=environment,
                    text=True,
                )
                owned_groups: list[tuple[int, int]] = []
                try:
                    stdout, stderr = process.communicate(timeout=5)
                    if pid_path.exists():
                        driver_pid = int(pid_path.read_text(encoding="ascii"))
                        try:
                            process_group_id = os.getpgid(driver_pid)
                        except ProcessLookupError:
                            pass
                        else:
                            owned_groups.append((driver_pid, process_group_id))

                    self.assertEqual(process.returncode, 2)
                    self.assertEqual(stdout, "")
                    self.assertEqual(stderr, '{"error": "invalid_input"}\n')
                    self.assertFalse(pid_path.exists())
                finally:
                    self._cleanup_test_process(process)
                    for driver_pid, process_group_id in owned_groups:
                        self._cleanup_owned_process_group(process_group_id, driver_pid)

    def test_linux_group_probe_ignores_only_dead_members_of_the_anchored_group(self) -> None:
        """Breaks if Linux mistakes the WNOWAIT zombie leader for a live descendant."""
        self.assertTrue(
            _linux_proc_stat_live_group_member(
                b"321 (driver ) name) S 1 321 321 0",
                321,
            )
        )
        self.assertFalse(
            _linux_proc_stat_live_group_member(
                b"321 (driver) Z 1 321 321 0",
                321,
            )
        )
        self.assertFalse(
            _linux_proc_stat_live_group_member(
                b"654 (unrelated) S 1 654 654 0",
                321,
            )
        )
        self.assertIsNone(_linux_proc_stat_live_group_member(b"malformed", 321))

    def test_interrupt_after_leader_wait_reaps_owned_descendant_group(self) -> None:
        """Breaks if interruption during leak inspection abandons a descendant."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            workspace_path = directory / "workspace"
            pid_path = directory / "descendant.pid"
            workspace_path.mkdir(mode=0o700)
            _write_executable(runtime_path, b"unqualified-test-runtime\n")
            environment = os.environ.copy()
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_MODE"] = "leak"
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_TARGET"] = CONFORMANCE_CASES[0].case_id
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_PID_PATH"] = str(pid_path)
            real_popen = cast(Any, subprocess.Popen)
            spawned: list[tuple[subprocess.Popen[bytes], int]] = []
            observed_descendant: list[tuple[int, int]] = []
            from weightclass import delegation_conformance as conformance_module

            real_group_exists = conformance_module._process_group_exists

            def capture_process(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
                process = cast(subprocess.Popen[bytes], real_popen(*args, **kwargs))
                spawned.append((process, os.getpgid(process.pid)))
                return process

            def interrupt_after_wait(process_group_id: int) -> bool:
                if not observed_descendant:
                    descendant_pid = int(pid_path.read_text(encoding="ascii"))
                    observed_descendant.append((descendant_pid, os.getpgid(descendant_pid)))
                    raise KeyboardInterrupt
                return real_group_exists(process_group_id)

            try:
                with (
                    mock.patch.dict(os.environ, environment, clear=True),
                    mock.patch(
                        "weightclass.delegation_conformance.subprocess.Popen",
                        side_effect=capture_process,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._process_group_exists",
                        side_effect=interrupt_after_wait,
                    ),
                    self.assertRaises(KeyboardInterrupt),
                ):
                    _run_driver_case(
                        FAKE_DRIVER,
                        runtime_path,
                        CONFORMANCE_CASES[0],
                        workspace_path,
                        timeout_seconds=5,
                    )

                self.assertEqual(len(spawned), 1)
                process, process_group_id = spawned[0]
                self.assertEqual(process.returncode, 0)
                self.assertEqual(len(observed_descendant), 1)
                descendant_pid, descendant_process_group_id = observed_descendant[0]
                self.assertEqual(descendant_process_group_id, process_group_id)
                self._assert_owned_process_group_gone(process_group_id, descendant_pid)
            finally:
                for descendant_pid, descendant_process_group_id in observed_descendant:
                    self._cleanup_owned_process_group(
                        descendant_process_group_id,
                        descendant_pid,
                    )
                for process, captured_process_group_id in spawned:
                    self._cleanup_owned_process_group(
                        captured_process_group_id,
                        process.pid,
                        process,
                    )

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
            process_group_id: int | None = None
            try:
                for _ in range(500):
                    if pid_path.exists():
                        driver_pid = int(pid_path.read_text(encoding="ascii"))
                        break
                    if process.poll() is not None:
                        break
                    time.sleep(0.01)
                self.assertIsNotNone(driver_pid)
                assert driver_pid is not None
                process_group_id = os.getpgid(driver_pid)
                self.assertEqual(process_group_id, driver_pid)
                process.send_signal(signal.SIGINT)
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 130)
                self.assertEqual(stdout, "")
                self.assertEqual(stderr, '{"error": "interrupted"}\n')
                self._assert_owned_process_group_gone(process_group_id, driver_pid)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                if driver_pid is not None and process_group_id is not None:
                    self._cleanup_owned_process_group(process_group_id, driver_pid)


if __name__ == "__main__":
    unittest.main()
