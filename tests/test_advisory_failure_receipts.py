from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "src" / "weightclass" / "advisory" / "speculative_run.py"
if str(RUNNER.parent) not in sys.path:
    sys.path.insert(0, str(RUNNER.parent))


def load_runner() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("advisory_failure_receipts", RUNNER)
    if spec is None or spec.loader is None:
        raise unittest.SkipTest("repository-only speculative runner unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(
    os.environ.get("WCLASS_FAILURE_RECEIPT_ACCEPTANCE") == "1",
    "prospective advisory failure-receipt acceptance",
)
class AdvisoryFailureReceiptAcceptanceTests(unittest.TestCase):
    def test_receipt_is_closed_value_only_and_machine_readable(self) -> None:
        runner = load_runner()
        private_value = "PRIVATE-TASK-AND-PATH-MATERIAL"
        attempt = {
            "route": private_value,
            "workspace": f"/private/{private_value}",
            "child": {
                "exit_code": 0,
                "timed_out": False,
                "seconds": 12.5,
                "failure_code": private_value,
                "stdout_present": True,
                "stderr_present": False,
            },
            "made_changes": True,
            "patch_lines": 27,
            "dropped_ignored": 1,
            "excluded_scaffolding": [private_value],
            "accepted": False,
            "failure_kind": private_value,
            "failure_stage": "verification",
            "result_shape": private_value,
            "envelope_extracted": True,
            "verify": {
                "passed": False,
                "exit_code": 17,
                "timed_out": False,
                "seconds": 3.5,
            },
            "error": private_value,
        }

        receipt = runner.failure_receipt(attempt, route="cheap")
        encoded = json.dumps(receipt, sort_keys=True)

        self.assertEqual(
            receipt,
            {
                "schema_version": 2,
                "event": "advisory_attempt_failed",
                "vendor": "unknown",
                "route": "cheap",
                "role": "cheap",
                "failure_kind": "unknown",
                "failure_stage": "verification",
                "child_exit_code": 0,
                "child_timed_out": False,
                "child_seconds": 12.5,
                "child_failure_code": "unknown",
                "child_stdout_present": True,
                "child_stderr_present": False,
                "candidate_made_changes": True,
                "candidate_patch_lines": 27,
                "candidate_dropped_ignored": 1,
                "candidate_excluded_scaffolding": 1,
                "verify_exit_code": 17,
                "verify_timed_out": False,
                "verify_seconds": 3.5,
                "result_shape": "unknown",
                "envelope_extracted": True,
            },
        )
        self.assertNotIn(private_value, encoded)
        self.assertNotIn("workspace", encoded)
        self.assertNotIn("error", encoded)

        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            runner.emit_failure_receipt(attempt, route="cheap")
        self.assertEqual(json.loads(stderr.getvalue()), receipt)

    def test_child_failure_diagnostics_are_fixed_categories(self) -> None:
        runner = load_runner()
        cases = (
            ("", "Authentication failed: login required", 1, "authentication"),
            ("", "rate_limit: too many requests", 1, "rate_limit"),
            ("", "unknown option --legacy", 2, "invalid_invocation"),
            ("", "permission denied; approval required", 1, "permission_or_approval"),
            ("", "model not found for this account", 1, "model_unavailable"),
            ("", "subscription required", 1, "account_limit"),
            ("", "project is not trusted", 1, "configuration"),
            ("PRIVATE TASK MATERIAL", "opaque provider failure", 1, "unknown"),
            ("PRIVATE TASK MATERIAL", "", 0, "none"),
        )
        for stdout, child_stderr, exit_code, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    runner.classify_child_failure(stdout, child_stderr, exit_code), expected
                )

    def test_stale_workspace_cleanup_receipt_contains_counts_not_paths(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory) / "results"
            workspace = out_dir / ".work" / "spec-cheap-private-name"
            workspace.mkdir(mode=0o700, parents=True)
            registry = out_dir / "workspaces.txt"
            runner.write_registry(registry, [str(workspace)])
            stderr = io.StringIO()

            with mock.patch("sys.stderr", stderr):
                result = runner.cleanup_stale_before_attempt(registry, out_dir)

            self.assertEqual(result, {"registered": 1, "removed": 1, "retained": 0})
            self.assertFalse(workspace.exists())
            receipt = json.loads(stderr.getvalue())
            self.assertEqual(
                receipt,
                {
                    "schema_version": 1,
                    "event": "advisory_stale_workspace_cleanup",
                    "registered": 1,
                    "removed": 1,
                    "retained": 0,
                },
            )
            self.assertNotIn(str(workspace), stderr.getvalue())

    def test_failed_verification_is_classified_before_workspace_cleanup(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            registry = output / "workspaces.txt"
            verify = output / "verify"
            verify.write_text("#!/bin/sh\nexit 17\n", encoding="utf-8")
            verify.chmod(0o700)
            child = (
                "from pathlib import Path;"
                "path=Path('README.md');"
                "path.write_text(path.read_text(encoding='utf-8')+'\\n',encoding='utf-8')"
            )

            record, verify_output, patch = runner.attempt(
                "cheap",
                [sys.executable, "-c", child],
                ROOT,
                runner.head_commit(ROOT),
                "bounded task",
                verify,
                output,
                registry,
                frozenset(),
                None,
                None,
                None,
                False,
            )

            self.assertFalse(record["accepted"])
            self.assertEqual(record["failure_kind"], "route")
            self.assertEqual(record["failure_stage"], "verification")
            self.assertEqual(record["error"], "verification_failed")
            self.assertEqual(record["verify"]["exit_code"], 17)
            self.assertEqual(record["workspace"], None)
            self.assertEqual(verify_output, "")
            self.assertTrue(patch)
            self.assertEqual(list((output / ".work").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
