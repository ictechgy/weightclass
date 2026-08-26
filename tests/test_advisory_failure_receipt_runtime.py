from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "src" / "weightclass" / "advisory" / "speculative_run.py"
REPOSITORY_GIT_AVAILABLE = (ROOT / ".git").exists()
if str(RUNNER.parent) not in sys.path:
    sys.path.insert(0, str(RUNNER.parent))


def load_runner() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("advisory_failure_receipt_runtime", RUNNER)
    if spec is None or spec.loader is None:
        raise unittest.SkipTest("repository-only speculative runner unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AdvisoryFailureReceiptRuntimeTests(unittest.TestCase):
    @unittest.skipUnless(REPOSITORY_GIT_AVAILABLE, "source Git repository unavailable")
    def test_each_failed_arm_emits_one_private_receipt(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            verify = output / "verify"
            verify.write_text(
                "#!/bin/sh\nprintf '%s\\n' 'PRIVATE-VERIFIER-OUTPUT' >&2\nexit 17\n",
                encoding="utf-8",
            )
            verify.chmod(0o700)
            child = (
                "from pathlib import Path;"
                "path=Path('README.md');"
                "path.write_text(path.read_text(encoding='utf-8')+'\\n',encoding='utf-8')"
            )

            for route in ("cheap", "retry", "expensive"):
                registry = output / f"{route}.txt"
                route_output = output / route
                route_output.mkdir()
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    record, verify_output, patch = runner.attempt(
                        route,
                        [sys.executable, "-c", child],
                        ROOT,
                        runner.head_commit(ROOT),
                        "PRIVATE-TASK-TEXT",
                        verify,
                        route_output,
                        registry,
                        frozenset(),
                        None,
                        None,
                        None,
                        False,
                    )

                receipts = [json.loads(line) for line in stderr.getvalue().splitlines()]
                self.assertEqual(len(receipts), 1)
                self.assertEqual(receipts[0]["route"], route)
                self.assertEqual(receipts[0]["failure_stage"], "verification")
                self.assertEqual(receipts[0]["verify_exit_code"], 17)
                self.assertNotIn("PRIVATE", stderr.getvalue())
                self.assertEqual(record["error"], "verification_failed")
                self.assertEqual(verify_output, "PRIVATE-VERIFIER-OUTPUT")
                self.assertTrue(patch)

    def test_closed_stderr_cannot_change_a_failure_verdict(self) -> None:
        runner = load_runner()
        attempt = {
            "accepted": False,
            "failure_kind": "route",
            "failure_stage": "verification",
            "verify": {"passed": False, "exit_code": 17, "timed_out": False},
        }
        stderr = io.StringIO()
        stderr.close()

        with mock.patch("sys.stderr", stderr):
            runner.emit_failure_receipt(attempt, route="cheap")

    def test_cleanup_diagnostic_contains_no_workspace_or_error_text(self) -> None:
        runner = load_runner()
        private_value = "PRIVATE-WORKSPACE-MATERIAL"
        stderr = io.StringIO()
        with (
            mock.patch.object(runner, "resolved_own_workspace", return_value=None),
            mock.patch("sys.stderr", stderr),
        ):
            runner.discard(
                Path("/private") / private_value,
                Path("/private") / private_value,
                Path("/private/results"),
            )

        self.assertEqual(
            json.loads(stderr.getvalue()),
            {
                "code": "workspace_not_owned",
                "event": "advisory_diagnostic",
                "schema_version": 1,
            },
        )
        self.assertNotIn(private_value, stderr.getvalue())

    def test_partial_or_unprotected_patch_is_removed_on_failure(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = (
                mock.patch.object(os, "write", side_effect=OSError("write failed")),
                mock.patch.object(os, "fchmod", side_effect=OSError("chmod failed")),
            )
            for index, failure in enumerate(cases):
                with self.subTest(index=index):
                    path = root / f"candidate-{index}.patch"
                    with failure, self.assertRaises(OSError):
                        runner.write_verified_patch(path, b"reviewed patch bytes")
                    self.assertFalse(path.exists())

            completed = root / "accepted.patch"
            runner.write_verified_patch(completed, b"reviewed patch bytes")
            self.assertEqual(completed.read_bytes(), b"reviewed patch bytes")
            self.assertEqual(stat.S_IMODE(completed.stat().st_mode), 0o400)

    @unittest.skipUnless(REPOSITORY_GIT_AVAILABLE, "source Git repository unavailable")
    def test_verifier_mutation_has_a_distinct_integrity_stage(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            verify = output / "verify"
            verify.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            verify.chmod(0o700)
            child = (
                "from pathlib import Path;"
                "path=Path('README.md');"
                "path.write_text(path.read_text(encoding='utf-8')+'\\n',encoding='utf-8')"
            )
            stderr = io.StringIO()
            with (
                mock.patch.object(runner, "tracked_files_unchanged", return_value=False),
                mock.patch("sys.stderr", stderr),
            ):
                record, _, _ = runner.attempt(
                    "expensive",
                    [sys.executable, "-c", child],
                    ROOT,
                    runner.head_commit(ROOT),
                    "bounded task",
                    verify,
                    output,
                    output / "workspaces.txt",
                    frozenset(),
                    None,
                    None,
                    None,
                    False,
                )

            self.assertEqual(record["failure_stage"], "verification_integrity")
            self.assertEqual(record["verify"]["exit_code"], 0)
            self.assertEqual(
                json.loads(stderr.getvalue())["failure_stage"],
                "verification_integrity",
            )

    @unittest.skipUnless(REPOSITORY_GIT_AVAILABLE, "source Git repository unavailable")
    def test_nonzero_implementation_child_takes_precedence_over_verification(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            stderr = io.StringIO()
            with (
                mock.patch.object(
                    runner,
                    "run_verify",
                    side_effect=AssertionError("verifier must not run"),
                ),
                mock.patch("sys.stderr", stderr),
            ):
                record, verify_output, patch = runner.attempt(
                    "cheap",
                    [sys.executable, "-c", "raise SystemExit(7)"],
                    ROOT,
                    runner.head_commit(ROOT),
                    "bounded task",
                    output / "unused-verify",
                    output,
                    output / "workspaces.txt",
                    frozenset(),
                    None,
                    None,
                    None,
                    False,
                )

            receipt = json.loads(stderr.getvalue())
            self.assertEqual(record["failure_stage"], "execution")
            self.assertEqual(record["error"], "route_execution_failed")
            self.assertEqual(record["failure_kind"], "infrastructure")
            self.assertEqual(record["verify"]["exit_code"], None)
            self.assertEqual(receipt["child_exit_code"], 7)
            self.assertEqual(receipt["child_failure_code"], "unknown")
            self.assertFalse(receipt["child_stdout_present"])
            self.assertFalse(receipt["child_stderr_present"])
            self.assertEqual(receipt["failure_stage"], "execution")
            self.assertEqual(receipt["verify_exit_code"], None)
            self.assertEqual(verify_output, "")
            self.assertEqual(patch, b"")


if __name__ == "__main__":
    unittest.main()
