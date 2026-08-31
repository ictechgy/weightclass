from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADVISORY = ROOT / "src" / "weightclass" / "advisory"


class AdvisoryDirectScriptTests(unittest.TestCase):
    def test_source_scripts_import_without_pythonpath_or_an_installed_package(self) -> None:
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        commands = (
            (ADVISORY / "speculative_run.py", "--help"),
            (ADVISORY / "advisory_parallel.py",),
            (ADVISORY / "bounded_capture.py",),
            (ADVISORY / "advisory_preflight.py",),
            (ADVISORY / "safe_git.py",),
        )
        for script, *arguments in commands:
            with self.subTest(script=script.name):
                completed = subprocess.run(
                    [sys.executable, str(script), *arguments],
                    cwd=ROOT,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    check=False,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
