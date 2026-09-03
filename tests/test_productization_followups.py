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
from weightclass import __version__
from weightclass.agent_discovery import render_agent_discovery

ROOT = Path(__file__).resolve().parent.parent
ACCEPTANCE_ENABLED = os.environ.get("WCLASS_PRODUCTIZATION_ACCEPTANCE") == "1"


@unittest.skipUnless(ACCEPTANCE_ENABLED, "prospective productization acceptance")
class ProductizationFollowupTests(unittest.TestCase):
    def _environment(self, path_value: str) -> dict[str, str]:
        environment = os.environ.copy()
        environment["PATH"] = path_value
        environment["PYTHONPATH"] = str(ROOT / "src")
        return environment

    def test_documented_low_only_example_policy_is_reachable(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "weightclass",
                "example-policy",
                "codex-cost-focused",
                "--low-model",
                "reviewed-low-model",
            ],
            cwd=ROOT,
            capture_output=True,
            check=False,
            env=self._environment(os.environ.get("PATH", "")),
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        policy = json.loads(completed.stdout)
        commands = {route["tier"]: route["command"] for route in policy["routes"]}
        self.assertIn("--model", commands["low"])
        self.assertNotIn("--model", commands["standard"])
        self.assertNotIn("--model", commands["high"])

    def test_checkout_uses_an_unreleased_development_version(self) -> None:
        self.assertEqual(__version__, "0.16.0.dev0")

    def test_default_route_reviews_and_runs_one_admitted_absolute_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bin_directory = Path(directory) / "bin"
            bin_directory.mkdir(mode=0o700)
            executable = bin_directory / "codex"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o700)
            environment = self._environment(str(bin_directory))

            reviewed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "route",
                    "--suggest-tier",
                    "--source-vendor",
                    "codex",
                ],
                cwd=ROOT,
                capture_output=True,
                check=False,
                env=environment,
                input="Fix a typo.",
                text=True,
            )
            executed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "run",
                    "--tier",
                    "low",
                    "--source-vendor",
                    "codex",
                ],
                cwd=ROOT,
                capture_output=True,
                check=False,
                env=environment,
                input="Fix a typo.",
                text=True,
            )

        self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
        self.assertEqual(json.loads(reviewed.stdout)["command"][0], str(executable))
        self.assertEqual(executed.returncode, 0, executed.stderr)

    def test_default_route_rejects_an_other_writable_path_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bin_directory = Path(directory) / "bin"
            bin_directory.mkdir()
            executable = bin_directory / "codex"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o777)
            inventory = render_agent_discovery(str(bin_directory), agent="codex")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "run",
                    "--tier",
                    "low",
                    "--source-vendor",
                    "codex",
                ],
                cwd=ROOT,
                capture_output=True,
                check=False,
                env=self._environment(str(bin_directory)),
                input="Fix a typo.",
                text=True,
            )

        agents = cast(list[dict[str, object]], inventory["agents"])
        self.assertFalse(agents[0]["executable_detected"])
        self.assertEqual(completed.returncode, 4)
        self.assertEqual(json.loads(completed.stderr), {"error": "executor_unavailable"})

    def test_advisory_directories_are_explicitly_forbidden_from_distributions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _source, wheel, _sdist = _write_distribution_fixture(directory)
            with zipfile.ZipFile(wheel, "a") as archive:
                archive.writestr("tools/advisory.py", "pass\n")
            with self.assertRaises(IsolationError):
                verify_wheel(wheel)

        with tempfile.TemporaryDirectory() as directory:
            _source, _wheel, sdist = _write_distribution_fixture(
                directory,
                sdist_extra_members=("weightclass-0/skills/advisory/SKILL.md",),
            )
            with self.assertRaises(IsolationError):
                verify_sdist(sdist)

    def test_public_entry_documents_have_one_current_identity(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        handoff = (ROOT / "HANDOFF.md").read_text(encoding="utf-8")
        lines = readme.splitlines()

        self.assertLess(lines.index("## Install"), 50)
        self.assertIn("experimental", "\n".join(lines[:30]).casefold())
        self.assertNotIn("/Users/", handoff)
        self.assertNotIn("intentionally uncommitted", handoff)
        self.assertFalse((ROOT / "security_best_practices_report.md").exists())

    def test_verified_object_execution_has_a_bounded_public_decision_record(self) -> None:
        decision = (ROOT / "docs" / "verified-object-execution.md").read_text(encoding="utf-8")
        for phrase in (
            "production status",
            "deferred",
            "linux",
            "macos",
            "shebang",
            "path-based",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, decision.casefold())


if __name__ == "__main__":
    unittest.main()
