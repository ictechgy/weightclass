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
            "init",
            "migrate-evidence",
            "migrate-routes",
            "doctor",
            "cli-check",
            "provider-check",
            "review",
            "consult",
            "dispatch",
            "status",
            "campaign-gate",
            "cleanup",
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
        self.assertTrue(package.joinpath("managed_verify.py").is_file())

    def test_packaged_skill_uses_managed_onboarding_instead_of_opaque_paths(self) -> None:
        skill = files("weightclass.advisory").joinpath("skill/SKILL.md").read_text(encoding="utf-8")

        self.assertIn("wclass-advisory init", skill)
        self.assertIn("wclass-advisory doctor", skill)
        self.assertIn("wclass-advisory dispatch", skill)
        self.assertNotIn("--campaign-root <", skill)
        self.assertNotIn("--route-profile <", skill)

    def test_run_help_exposes_private_campaign_boundary(self) -> None:
        result = self._run("run", "--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--campaign-root", result.stdout)
        self.assertIn("--workflow", result.stdout)
        for forwarded in (
            "--repo",
            "--task-file",
            "--route-profile",
            "--campaign",
            "--verify",
            "--advise-on-failure",
            "--confirm-task-egress",
        ):
            self.assertIn(forwarded, result.stdout)
        self.assertIn("wclass-advisory dispatch", result.stdout)
        self.assertNotIn("--router-root", result.stdout)

    def test_subcommand_help_uses_the_public_command_name(self) -> None:
        expected = {
            "prune": "usage: wclass-advisory prune",
            "review": "usage: wclass-advisory review",
            "migrate-evidence": "usage: wclass-advisory migrate-evidence",
            "migrate-routes": "usage: wclass-advisory migrate-routes",
            "install-skill": "usage: wclass-advisory install-skill",
        }
        for command, usage in expected.items():
            with self.subTest(command=command):
                result = self._run(command, "--help")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(usage, result.stdout)
        review = self._run("review", "--help")
        self.assertIn("--profile", review.stdout)

    def test_skill_rejects_stale_run_instructions_instead_of_bypassing_advisory(self) -> None:
        skill = files("weightclass.advisory").joinpath("skill/SKILL.md").read_text(encoding="utf-8")

        self.assertIn("stale", skill.casefold())
        self.assertIn("wclass-advisory dispatch", skill)
        self.assertIn("Do not fall back", skill)


if __name__ == "__main__":
    unittest.main()
