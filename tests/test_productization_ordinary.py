from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import cast

from tests.test_distribution_isolation import _write_distribution_fixture
from tests.verify_distribution_isolation import IsolationError, verify_sdist, verify_wheel
from weightclass.agent_discovery import render_agent_discovery

ROOT = Path(__file__).resolve().parents[1]


class ProductizationOrdinaryTests(unittest.TestCase):
    def _environment(self, path_value: str) -> dict[str, str]:
        environment = os.environ.copy()
        environment["PATH"] = path_value
        environment["PYTHONPATH"] = str(ROOT / "src")
        return environment

    def test_codex_low_model_example_changes_only_low(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "weightclass",
                "example-policy",
                "codex-cost-focused",
                "--low-model",
                "opaque-low-model",
            ],
            cwd=ROOT,
            capture_output=True,
            check=False,
            env=self._environment(os.environ.get("PATH", "")),
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        policy = json.loads(result.stdout)
        commands = {
            cast(str, route["tier"]): cast(list[str], route["command"])
            for route in cast(list[dict[str, object]], policy["routes"])
        }
        self.assertIn("opaque-low-model", commands["low"])
        self.assertNotIn("--model", commands["standard"])
        self.assertNotIn("--model", commands["high"])

    def test_default_route_uses_an_admitted_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bin_directory = Path(directory) / "bin"
            bin_directory.mkdir(mode=0o700)
            executable = bin_directory / "codex"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o700)
            result = subprocess.run(
                [sys.executable, "-m", "weightclass", "route", "--source-vendor", "codex"],
                cwd=ROOT,
                capture_output=True,
                check=False,
                env=self._environment(str(bin_directory)),
                input="Fix a typo.",
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["command"][0], str(executable))

    def test_default_route_reports_an_unavailable_executable_without_starting_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-m", "weightclass", "route", "--source-vendor", "codex"],
                cwd=ROOT,
                capture_output=True,
                check=False,
                env=self._environment(directory),
                input="Fix a typo.",
                text=True,
            )

        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stderr), {"error": "unsupported_route"})
        self.assertEqual(result.stdout, "")

    def test_admission_is_shared_by_discovery_and_distribution_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "codex"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o777)
            inventory = render_agent_discovery(directory, agent="codex")
            agents = cast(list[dict[str, object]], inventory["agents"])
            self.assertFalse(agents[0]["executable_detected"])

            _source, wheel, _sdist = _write_distribution_fixture(directory)
            with zipfile.ZipFile(wheel, "a") as archive:
                archive.writestr("tools/extra.py", "pass\n")
            with self.assertRaises(IsolationError):
                verify_wheel(wheel)

        with tempfile.TemporaryDirectory() as directory:
            _source, _wheel, sdist = _write_distribution_fixture(
                directory,
                sdist_extra_members=("weightclass-0/skills/extra/SKILL.md",),
            )
            with self.assertRaises(IsolationError):
                verify_sdist(sdist)


if __name__ == "__main__":
    unittest.main()
