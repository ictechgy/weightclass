from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from weightclass.advisory import verifier_cli


class AdvisoryVerifierCliTests(unittest.TestCase):
    def _git(self, root: Path, *arguments: str) -> None:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_scaffold_is_reject_all_until_committed_criteria_replace_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._git(root, "init", "-q")
            self._git(root, "config", "user.name", "Verifier Test")
            self._git(root, "config", "user.email", "verifier@example.invalid")
            receipt = verifier_cli.scaffold(root, "review")
            verifier = root / ".weightclass" / "verify-review"

            self.assertFalse(receipt["ready"])
            self.assertTrue(receipt["rejects_all"])
            self.assertTrue(verifier.is_file())
            self.assertEqual(verifier.stat().st_mode & 0o777, 0o700)
            self.assertFalse(verifier_cli.check(root, "review")["committed"])

            self._git(root, "add", ".weightclass/verify-review")
            self._git(root, "commit", "-qm", "add verifier scaffold")
            scaffold = verifier_cli.check(root, "review")
            self.assertTrue(scaffold["committed"])
            self.assertTrue(scaffold["scaffold_only"])
            self.assertFalse(scaffold["ready"])

            verifier.write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
            verifier.chmod(0o700)
            self._git(root, "add", ".weightclass/verify-review")
            self._git(root, "commit", "-qm", "implement verifier criteria")
            ready = verifier_cli.check(root, "review")

        self.assertTrue(ready["committed"])
        self.assertFalse(ready["scaffold_only"])
        self.assertTrue(ready["baseline_rejected"])
        self.assertTrue(ready["ready"])

    def test_scaffold_never_overwrites_an_existing_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".weightclass").mkdir()
            verifier = root / ".weightclass" / "verify-review"
            verifier.write_text("custom", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                verifier_cli.scaffold(root, "review")
            self.assertEqual(verifier.read_text(encoding="utf-8"), "custom")


if __name__ == "__main__":
    unittest.main()
