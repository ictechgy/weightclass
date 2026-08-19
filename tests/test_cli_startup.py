from __future__ import annotations

import json
import subprocess
import sys
import unittest
from unittest import mock

from weightclass import classification_cli


class CliStartupTests(unittest.TestCase):
    def test_local_classify_does_not_mislabel_unexpected_failures_as_vendor_unavailable(
        self,
    ) -> None:
        """Breaks if the no-vendor path catches every internal exception."""
        with mock.patch.object(
            classification_cli,
            "classify_task",
            side_effect=RuntimeError("unexpected"),
        ):
            with self.assertRaises(RuntimeError):
                classification_cli.classify_task_input(read_task=lambda: "Fix a spelling typo.")

    def test_local_classify_does_not_load_unrelated_command_families(self) -> None:
        """Breaks if the fast local path eagerly imports runtime protocols again."""
        program = """
import json
import sys

from weightclass.entrypoint import main

exit_code = main(["classify"])
forbidden = sorted(
    name
    for name in sys.modules
    if name in {
        "weightclass.cli",
        "weightclass.delegation_runtime",
        "weightclass.native_v2_compile",
        "weightclass.triage",
        "weightclass.v2",
    }
)
print(json.dumps({"exit_code": exit_code, "forbidden": forbidden}))
"""
        result = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            check=False,
            input="Fix a spelling typo.",
            text=True,
            timeout=5,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], '{"tier": "low"}')
        self.assertEqual(json.loads(lines[1]), {"exit_code": 0, "forbidden": []})
        self.assertEqual(result.stderr, "")

    def test_version_does_not_load_the_full_command_dispatcher(self) -> None:
        """Breaks if a metadata query pays for every runtime protocol import."""
        program = """
import json
import sys

from weightclass.entrypoint import main

exit_code = main(["--version"])
forbidden = sorted(
    name
    for name in sys.modules
    if name in {
        "weightclass.cli",
        "weightclass.delegation_runtime",
        "weightclass.native_v2_compile",
        "weightclass.triage",
        "weightclass.v2",
    }
)
print(json.dumps({"exit_code": exit_code, "forbidden": forbidden}))
"""
        result = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertRegex(lines[0], r"^weightclass [0-9]+\.[0-9]+\.[0-9]+$")
        self.assertEqual(json.loads(lines[1]), {"exit_code": 0, "forbidden": []})
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
