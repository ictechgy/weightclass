from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from importlib.resources import files
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class AdvisoryDistributionTests(unittest.TestCase):
    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "weightclass.advisory", *arguments],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
        )

    def test_installed_command_family_is_explicit_and_separate_from_wclass(self) -> None:
        advisory = self._run("--help")
        core = subprocess.run(
            [sys.executable, "-m", "weightclass", "--help"],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
        )

        self.assertEqual(advisory.returncode, 0, advisory.stderr)
        for command in (
            "review",
            "run",
            "prune",
            "seal",
            "report",
            "portfolio",
            "install-skill",
        ):
            self.assertIn(command, advisory.stdout)
        self.assertNotIn("advisory", core.stdout)

    def test_profile_review_is_task_free_and_uses_the_packaged_route_compiler(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile.json"
            profile.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "vendor": "codex",
                        "models": {"cheap": "c", "advisor": "a", "expensive": "e"},
                        "efforts": {"cheap": "low", "advisor": "high", "expensive": "high"},
                    }
                ),
                encoding="utf-8",
            )
            reviewed = self._run("review", "--profile", str(profile))

        self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
        payload = json.loads(reviewed.stdout)
        self.assertEqual(payload["vendor"], "codex")
        self.assertEqual(payload["task_delivery"], "stdin")
        self.assertTrue(payload["task_egress"])
        self.assertNotIn("task", reviewed.stderr.casefold())

    def test_packaged_skill_bundle_is_complete(self) -> None:
        package = files("weightclass.advisory")
        for relative in (
            "skill/SKILL.md",
            "skill/agents/openai.yaml",
            "skill/references/modes.md",
        ):
            with self.subTest(relative=relative):
                self.assertTrue(package.joinpath(relative).is_file())

    def test_run_help_exposes_private_campaign_boundary(self) -> None:
        result = self._run("run", "--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--campaign-root", result.stdout)
        self.assertIn("--workflow", result.stdout)
        self.assertNotIn("--router-root", result.stdout)


if __name__ == "__main__":
    unittest.main()
