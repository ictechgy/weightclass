from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _release_ref_available() -> bool:
    if not (ROOT / ".git").exists():
        return False
    try:
        result = subprocess.run(
            ("git", "rev-parse", "--verify", "v0.28.0^{commit}"),
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


@unittest.skipUnless(_release_ref_available(), "previous release tag unavailable")
class AdvisoryReleaseLedgerTests(unittest.TestCase):
    def test_previous_release_skill_is_an_admitted_upgrade_source(self) -> None:
        result = subprocess.run(
            (
                sys.executable,
                str(Path(__file__).with_name("verify_advisory_skill_ledger.py")),
                "--repository",
                str(ROOT),
                "--previous-ref",
                "v0.28.0",
            ),
            cwd=ROOT,
            env={"PYTHONPATH": str(ROOT / "src")},
            capture_output=True,
            check=False,
            text=True,
            timeout=15,
        )
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))


if __name__ == "__main__":
    unittest.main()
