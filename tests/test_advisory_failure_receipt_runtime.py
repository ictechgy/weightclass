from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "src" / "weightclass" / "advisory" / "speculative_run.py"


def load_runner() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("advisory_failure_receipt_runtime", RUNNER)
    if spec is None or spec.loader is None:
        raise unittest.SkipTest("repository-only speculative runner unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AdvisoryFailureReceiptRuntimeTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
