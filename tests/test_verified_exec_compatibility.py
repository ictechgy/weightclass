from __future__ import annotations

import errno
import importlib.util
import json
import os
import subprocess
import sys
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "tests" / "verified_exec_probe.py"
CAMPAIGN_ACCEPTANCE = os.environ.get("WCLASS_CAMPAIGN_ACCEPTANCE") == "1"
EXPECTED_KEYS = {
    "schema_version",
    "platform",
    "fd_exec_advertised",
    "native",
    "script",
    "path_swap",
}
STATUS_KEYS = {"status", "reason"}
STATUSES = {"passed", "unsupported", "failed"}


def load_probe() -> types.ModuleType:
    if not PROBE.is_file():
        raise AssertionError("verified exec compatibility probe is missing")
    spec = importlib.util.spec_from_file_location("prospective_verified_exec_probe", PROBE)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load verified exec compatibility probe")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@unittest.skipIf(
    not PROBE.is_file() and not CAMPAIGN_ACCEPTANCE,
    "prospective verified-exec probe unavailable",
)
class VerifiedExecCompatibilityTests(unittest.TestCase):
    def test_probe_is_bounded_task_free_and_matches_platform_capability(self) -> None:
        probe = load_probe()
        started = time.monotonic()
        result = probe.run_compatibility_probe(timeout_seconds=3.0)
        self.assertLess(time.monotonic() - started, 10.0)
        self.assertEqual(set(result), EXPECTED_KEYS)
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["platform"], sys.platform)
        self.assertIs(result["fd_exec_advertised"], os.execve in os.supports_fd)

        for name in ("native", "script", "path_swap"):
            status = result[name]
            self.assertIsInstance(status, dict)
            self.assertEqual(set(status), STATUS_KEYS)
            self.assertIn(status["status"], STATUSES)
            self.assertIsInstance(status["reason"], str)
            self.assertTrue(status["reason"])

        rendered = json.dumps(result, sort_keys=True)
        for forbidden in ("task", "prompt", "credential", str(ROOT), str(Path.home())):
            self.assertNotIn(forbidden, rendered)

    def test_advertised_native_fd_exec_binds_the_opened_object(self) -> None:
        probe = load_probe()
        result = probe.run_compatibility_probe(timeout_seconds=3.0)
        if result["fd_exec_advertised"]:
            self.assertEqual(result["native"]["status"], "passed")
            self.assertEqual(result["path_swap"]["status"], "passed")
        else:
            for name in ("native", "script", "path_swap"):
                self.assertEqual(result[name]["status"], "unsupported")
                self.assertEqual(result[name]["reason"], "fd_exec_not_advertised")

    def test_script_result_is_independently_characterized(self) -> None:
        probe = load_probe()
        result = probe.run_compatibility_probe(timeout_seconds=3.0)
        script = result["script"]
        self.assertIn(script["status"], STATUSES)
        if script["status"] != "passed":
            self.assertNotEqual(script["reason"], "native_probe_failed")

    def test_cli_prints_only_the_canonical_probe_result(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(PROBE)],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), load_probe().run_compatibility_probe())

    @unittest.skipUnless(CAMPAIGN_ACCEPTANCE, "prospective errno classification")
    def test_script_exec_errno_does_not_hide_operational_failure(self) -> None:
        probe = load_probe()
        for error_number in (errno.ENOENT, errno.ENOEXEC):
            self.assertEqual(
                probe._classify_script_exec_error(error_number),
                {"status": "unsupported", "reason": "shebang_descriptor_exec_unsupported"},
            )
        for error_number in (errno.EACCES, errno.EMFILE, errno.EIO):
            self.assertEqual(
                probe._classify_script_exec_error(error_number),
                {"status": "failed", "reason": "script_exec_failed"},
            )

    @unittest.skipUnless(CAMPAIGN_ACCEPTANCE, "prospective timeout reaping")
    def test_timeout_cleanup_kills_and_reaps_the_owned_child(self) -> None:
        probe = load_probe()
        child_pid = os.fork()
        if child_pid == 0:
            time.sleep(60)
            os._exit(0)
        started = time.monotonic()
        self.assertEqual(probe._terminate_and_reap(child_pid, timeout_seconds=1.0), "reaped")
        self.assertLess(time.monotonic() - started, 2.0)
        with self.assertRaises(ChildProcessError):
            os.waitpid(child_pid, os.WNOHANG)


if __name__ == "__main__":
    unittest.main()
