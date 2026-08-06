import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from tests.test_delegation import _manifest, _policy
from tests.test_delegation_qualification import (
    _evidence,
    _registry_value,
    _write_executable,
)
from weightclass.cli import delegation_run_from_standard_input
from weightclass.delegation_compile import compile_delegation_descriptor
from weightclass.delegation_protocol import DelegationFrameError, encode_delegation_frame
from weightclass.delegation_qualification import (
    attach_qualification_requirement,
    build_qualification_candidate,
    load_qualification_registry,
)
from weightclass.delegation_runtime import _write_all
from weightclass.delegation_schema import (
    current_platform_contract,
    load_delegation_manifest,
    load_delegation_policy,
)

EXPECTED_TASK = "Apply the reviewed change. 테스트"
FAKE_RUNTIME = Path(__file__).parent / "fixtures" / "fake_delegation_runtime.py"


class DelegationProtocolUnitTests(unittest.TestCase):
    def test_frame_encoding_has_exact_lengths_and_utf8_bytes(self) -> None:
        """Breaks if runtime framing becomes locale-dependent or structurally ambiguous."""
        frame = encode_delegation_frame(b"{}", "é")

        self.assertEqual(frame, b"WCD1\x00\x00\x00\x02{}\x00\x00\x00\x02\xc3\xa9")

    def test_frame_encoding_rejects_oversized_task_before_spawn(self) -> None:
        """Breaks if a 32-bit frame field bypasses the smaller protocol limit."""
        with self.assertRaises(DelegationFrameError):
            encode_delegation_frame(b"{}", "x" * 80_001)

    def test_write_all_retries_interruptions_and_partial_writes(self) -> None:
        """Breaks if a short write truncates the reviewed descriptor or task."""
        written = bytearray()
        attempts = 0

        def partial_write(file_descriptor: int, contents: bytes) -> int:
            nonlocal attempts
            self.assertEqual(file_descriptor, 99)
            attempts += 1
            if attempts == 1:
                raise InterruptedError()
            length = min(2, len(contents))
            written.extend(contents[:length])
            return length

        with mock.patch("weightclass.delegation_runtime.os.write", side_effect=partial_write):
            _write_all(99, b"abcdef")

        self.assertEqual(bytes(written), b"abcdef")
        self.assertEqual(attempts, 4)


class DelegationRunTests(unittest.TestCase):
    def _write_inputs(
        self,
        directory: Path,
        *,
        cleanup_seconds: int = 1,
    ) -> tuple[Path, Path]:
        policy = _policy()
        workflows = policy["workflows"]
        assert isinstance(workflows, list)
        for workflow in workflows:
            assert isinstance(workflow, dict)
            workflow["direct_child_cleanup"] = {
                "grace_seconds": cleanup_seconds,
                "terminate_grace_seconds": cleanup_seconds,
            }
        policy_path = directory / "delegation-policy.json"
        manifest_path = directory / "runtime-manifest.json"
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
        return policy_path, manifest_path

    def _copy_runtime(self, directory: Path) -> Path:
        runtime_path = directory / "fake-delegation-runtime"
        shutil.copyfile(FAKE_RUNTIME, runtime_path)
        runtime_path.chmod(runtime_path.stat().st_mode | stat.S_IXUSR)
        return runtime_path

    def _arguments(
        self,
        command: str,
        policy_path: Path,
        manifest_path: Path,
        runtime_path: Path,
    ) -> list[str]:
        return [
            sys.executable,
            "-m",
            "weightclass",
            "delegate",
            command,
            "--policy",
            str(policy_path),
            "--runtime-manifest",
            str(manifest_path),
            "--delegation-runtime",
            str(runtime_path),
            "--source-vendor",
            "claude",
            "--tier",
            "standard",
        ]

    def _review(
        self,
        policy_path: Path,
        manifest_path: Path,
        runtime_path: Path,
    ) -> dict[str, object]:
        result = subprocess.run(
            self._arguments("route", policy_path, manifest_path, runtime_path),
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        assert isinstance(value, dict)
        return value

    def _wait_without_closing_stdin(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        process = subprocess.Popen(
            arguments,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            return_code = process.wait(timeout=5)
            stdout, stderr = process.communicate(timeout=1)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        return subprocess.CompletedProcess(arguments, return_code, stdout, stderr)

    def test_confirmation_is_required_before_runtime_or_task_access(self) -> None:
        """Breaks if an unconfirmed run touches a runtime or blocks on task stdin."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path, manifest_path = self._write_inputs(directory)
            runtime_path = directory / "runtime-does-not-exist"

            result = self._wait_without_closing_stdin(
                self._arguments("run", policy_path, manifest_path, runtime_path)
            )

        self.assertEqual(result.returncode, 5)
        self.assertEqual(json.loads(result.stderr), {"error": "delegation_confirmation_required"})

    def test_empty_package_registry_blocks_qualified_run_before_task_access(self) -> None:
        """Breaks if qualification can fall back to declared enforcement during run."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path, manifest_path = self._write_inputs(directory)
            runtime_path = directory / "runtime-does-not-exist"
            arguments = self._arguments("run", policy_path, manifest_path, runtime_path)
            arguments.append("--require-qualified-runtime")

            result = self._wait_without_closing_stdin(arguments)

        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stderr), {"error": "unsupported_route"})

    def test_changed_qualified_artifact_blocks_task_access(self) -> None:
        """Breaks if task stdin is touched before the exact-artifact gate succeeds."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path, manifest_path = self._write_inputs(directory)
            runtime_path = directory / "runtime"
            _write_executable(runtime_path)
            record = build_qualification_candidate(_evidence(), runtime_path)
            registry_path = directory / "registry.json"
            registry_path.write_text(json.dumps(_registry_value(record)), encoding="utf-8")
            registry = load_qualification_registry(registry_path)
            descriptor = compile_delegation_descriptor(
                load_delegation_policy(policy_path),
                load_delegation_manifest(manifest_path),
                runtime_path=str(runtime_path),
                source_vendor="claude",
                tier="standard",
                target_platform=current_platform_contract(),
            )
            qualified = attach_qualification_requirement(descriptor, registry.records[0])
            _write_executable(runtime_path, b"changed-runtime\n")
            diagnostic = io.StringIO()

            with (
                mock.patch(
                    "weightclass.cli.load_packaged_qualification_registry",
                    return_value=registry,
                ),
                mock.patch(
                    "weightclass.cli.read_task_from_standard_input",
                    side_effect=AssertionError("task input must remain untouched"),
                ) as read_task,
                redirect_stderr(diagnostic),
            ):
                result = delegation_run_from_standard_input(
                    policy_path,
                    manifest_path,
                    str(runtime_path),
                    "claude",
                    "standard",
                    True,
                    str(qualified["route_fingerprint"]),
                    True,
                )

        self.assertEqual(result, 4)
        self.assertEqual(json.loads(diagnostic.getvalue()), {"error": "executor_unavailable"})
        read_task.assert_not_called()

    def test_fingerprint_mismatch_precedes_runtime_or_task_access(self) -> None:
        """Breaks if an unreviewed descriptor can reach runtime validation or stdin."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path, manifest_path = self._write_inputs(directory)
            runtime_path = directory / "runtime-does-not-exist"
            arguments = self._arguments("run", policy_path, manifest_path, runtime_path)
            arguments.extend(
                [
                    "--confirm-trusted-delegation-runtime",
                    "--ack-route-fingerprint",
                    "sha256:" + "0" * 64,
                ]
            )

            result = self._wait_without_closing_stdin(arguments)

        self.assertEqual(result.returncode, 6)
        self.assertEqual(json.loads(result.stderr), {"error": "route_fingerprint_mismatch"})

    def test_unavailable_runtime_precedes_task_access(self) -> None:
        """Breaks if task data is read before the reviewed runtime is available."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path, manifest_path = self._write_inputs(directory)
            runtime_path = directory / "runtime-does-not-exist"
            descriptor = self._review(policy_path, manifest_path, runtime_path)
            arguments = self._arguments("run", policy_path, manifest_path, runtime_path)
            arguments.extend(
                [
                    "--confirm-trusted-delegation-runtime",
                    "--ack-route-fingerprint",
                    str(descriptor["route_fingerprint"]),
                ]
            )

            result = self._wait_without_closing_stdin(arguments)

        self.assertEqual(result.returncode, 4)
        self.assertEqual(json.loads(result.stderr), {"error": "executor_unavailable"})

    def test_success_sends_one_reviewed_frame_and_inherits_output(self) -> None:
        """Breaks if run changes the descriptor, task bytes, argv, or spawn count."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path, manifest_path = self._write_inputs(directory)
            runtime_path = self._copy_runtime(directory)
            descriptor = self._review(policy_path, manifest_path, runtime_path)
            arguments = self._arguments("run", policy_path, manifest_path, runtime_path)
            arguments.extend(
                [
                    "--confirm-trusted-delegation-runtime",
                    "--ack-route-fingerprint",
                    str(descriptor["route_fingerprint"]),
                ]
            )

            result = subprocess.run(
                arguments,
                capture_output=True,
                check=False,
                input=EXPECTED_TASK,
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("fake-runtime-ok"), 1)
        self.assertIn(f"fake-runtime-fingerprint:{descriptor['route_fingerprint']}", result.stdout)
        self.assertEqual(result.stderr, "fake-runtime-stderr\n")

    def test_invalid_task_does_not_start_runtime(self) -> None:
        """Breaks if a runtime starts before bounded task validation finishes."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path, manifest_path = self._write_inputs(directory)
            runtime_path = self._copy_runtime(directory)
            descriptor = self._review(policy_path, manifest_path, runtime_path)
            arguments = self._arguments("run", policy_path, manifest_path, runtime_path)
            arguments.extend(
                [
                    "--confirm-trusted-delegation-runtime",
                    "--ack-route-fingerprint",
                    str(descriptor["route_fingerprint"]),
                ]
            )

            result = subprocess.run(
                arguments,
                capture_output=True,
                check=False,
                input="",
                text=True,
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_task"})
        self.assertEqual(result.stdout, "")

    def test_runtime_nonzero_maps_to_router_failure_without_task_content(self) -> None:
        """Breaks if runtime status collides with router codes or leaks task text."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path, manifest_path = self._write_inputs(directory)
            runtime_path = self._copy_runtime(directory)
            descriptor = self._review(policy_path, manifest_path, runtime_path)
            arguments = self._arguments("run", policy_path, manifest_path, runtime_path)
            arguments.extend(
                [
                    "--confirm-trusted-delegation-runtime",
                    "--ack-route-fingerprint",
                    str(descriptor["route_fingerprint"]),
                ]
            )
            environment = os.environ.copy()
            environment["WEIGHTCLASS_FAKE_DELEGATION_MODE"] = "exit-9"

            result = subprocess.run(
                arguments,
                capture_output=True,
                check=False,
                env=environment,
                input="zephyrine glimmerfast quokka",
                text=True,
            )

        self.assertEqual(result.returncode, 7)
        self.assertEqual(
            json.loads([line for line in result.stderr.splitlines() if line][-1]),
            {"error": "executor_failed", "executor_exit_code": 9},
        )
        self.assertNotIn("zephyrine", result.stdout + result.stderr)

    def test_broken_pipe_terminates_and_reaps_the_direct_child(self) -> None:
        """Breaks if post-spawn framing failure hangs or leaves its direct child alive."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path, manifest_path = self._write_inputs(directory, cleanup_seconds=1)
            runtime_path = self._copy_runtime(directory)
            descriptor = self._review(policy_path, manifest_path, runtime_path)
            arguments = self._arguments("run", policy_path, manifest_path, runtime_path)
            arguments.extend(
                [
                    "--confirm-trusted-delegation-runtime",
                    "--ack-route-fingerprint",
                    str(descriptor["route_fingerprint"]),
                ]
            )
            environment = os.environ.copy()
            environment["WEIGHTCLASS_FAKE_DELEGATION_MODE"] = "close-stdin-and-hang"
            started_at = time.monotonic()

            result = subprocess.run(
                arguments,
                capture_output=True,
                check=False,
                env=environment,
                input="🧪" * 20_000,
                text=True,
                timeout=8,
            )
            elapsed = time.monotonic() - started_at

        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertLess(elapsed, 8)
        pid_line = next(
            line for line in result.stdout.splitlines() if line.startswith("fake-runtime-pid:")
        )
        runtime_pid = int(pid_line.removeprefix("fake-runtime-pid:"))
        with self.assertRaises(ProcessLookupError):
            os.kill(runtime_pid, 0)
        self.assertEqual(
            json.loads([line for line in result.stderr.splitlines() if line][-1]),
            {"error": "executor_failed"},
        )


if __name__ == "__main__":
    unittest.main()
