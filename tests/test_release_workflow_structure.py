from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests.test_ci_workflow_structure import MACOS_BOUNDARY_MODULES


class ReleaseWorkflowStructureTests(unittest.TestCase):
    def setUp(self) -> None:
        if not Path(".github/workflows/release.yml").is_file():
            self.skipTest("workflow sources are intentionally absent from the sdist")

    def test_boundary_validators_run_goldens_from_exact_installed_wheel(self) -> None:
        text = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
        for job, following_job in (
            ("validate-python-310", "validate-python-314"),
            ("validate-python-314", "macos-routing-boundaries"),
        ):
            with self.subTest(job=job):
                block = text.split(f"\n  {job}:\n", 1)[1].split(f"\n  {following_job}:\n", 1)[0]
                self.assertIn("--print-staging-paths dist-under-test", block)
                self.assertIn("--no-index --no-deps --target", block)
                self.assertIn('python -m compileall -q "$installed_wheel/weightclass"', block)
                self.assertIn('importlib.metadata.version("weightclass")', block)
                self.assertIn("python -m weightclass --version", block)
                self.assertIn('PYTHONPATH="$installed_wheel"', block)
                installed_wheel_checks = block.split('installed_wheel="$RUNNER_TEMP', 1)[1]
                self.assertNotIn("PYTHONPATH=src python", installed_wheel_checks)

    def test_release_uses_one_immutable_manifest_candidate_without_distribution_globs(self) -> None:
        text = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertEqual(text.count("python -m build"), 1)
        for token in (
            "artifact-download",
            "dist-under-test",
            "publish-staging",
            "release-candidate-v1",
            "tests/verify_release_candidate.py",
        ):
            self.assertIn(token, text)
        self.assertNotIn("dist/*.whl", text)
        self.assertNotIn("dist/*.tar.gz", text)
        self.assertIn(
            "needs: [validate-python-310, validate-python-314, macos-routing-boundaries]",
            text,
        )
        publish_header = text.split("\n  publish:\n", 1)[1].split("\n    steps:\n", 1)[0]
        self.assertIn("contents: read", publish_header)
        self.assertIn("id-token: write", publish_header)

    def test_local_commands_are_labeled_offline_and_ci_install_is_networked(self) -> None:
        self.assertIn(
            "Offline/preprovisioned release verification",
            Path("README.md").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "Networked CI dependency installation",
            Path(".github/workflows/release.yml").read_text(encoding="utf-8"),
        )

    def test_boundary_validators_use_same_candidate_and_never_rebuild(self) -> None:
        text = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertEqual(text.count("python -m build"), 1)
        for job, following_job in (
            ("validate-python-310", "validate-python-314"),
            ("validate-python-314", "macos-routing-boundaries"),
        ):
            block = text.split(f"\n  {job}:\n", 1)[1].split(f"\n  {following_job}:\n", 1)[0]
            with self.subTest(job=job):
                self.assertIn("name: release-candidate-v1", block)
                self.assertIn("path: artifact-download", block)
                self.assertIn("--create-staging dist-under-test", block)
                self.assertGreaterEqual(block.count("--artifact-download artifact-download"), 3)
                self.assertNotIn("python -m build", block)
                self.assertNotIn("build-output", block)

    def test_candidate_is_uploaded_before_post_build_tools_or_extracted_tests(self) -> None:
        text = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
        block = text.split("\n  build-candidate:\n", 1)[1].split("\n  validate-python-310:\n", 1)[0]
        self.assertIn("--create-publish-staging dist-under-test", block)
        self.assertNotIn("--create-staging dist-under-test", block)
        upload_index = block.index("name: release-candidate-v1")
        self.assertLess(upload_index, block.index("python -m twine check"))

    def test_publish_reverifies_commit_candidate_and_exact_staging(self) -> None:
        text = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
        block = text.split("\n  publish:\n", 1)[1]
        self.assertIn(
            "needs: [validate-python-310, validate-python-314, macos-routing-boundaries]",
            block,
        )
        self.assertIn('test "$(git rev-parse HEAD)" = "$GITHUB_SHA"', block)
        self.assertIn("--create-publish-staging publish-staging", block)
        self.assertIn("--print-staging-paths publish-staging", block)
        self.assertIn("packages-dir: publish-staging", block)
        self.assertNotRegex(block, r"(?:\*\.whl|\*\.tar\.gz|dist/\*)")

    def test_macos_release_gate_matches_protocol_two_ci_boundaries(self) -> None:
        text = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
        block = text.split("\n  macos-routing-boundaries:\n", 1)[1].split("\n  publish:\n", 1)[0]
        observed_modules = tuple(re.findall(r"tests\.test_[a-z0-9_]+", block))
        self.assertEqual(observed_modules, MACOS_BOUNDARY_MODULES)
        for module in MACOS_BOUNDARY_MODULES:
            with self.subTest(module=module):
                self.assertIn(module, block)

    def test_source_layout_test_jobs_set_pythonpath_without_bytecode(self) -> None:
        text = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
        for job, following_job in (
            ("build-candidate", "validate-python-310"),
            ("validate-python-310", "validate-python-314"),
            ("validate-python-314", "macos-routing-boundaries"),
        ):
            with self.subTest(job=job):
                block = text.split(f"\n  {job}:\n", 1)[1].split(f"\n  {following_job}:\n", 1)[0]
                self.assertIn(
                    "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "
                    "python -W error::ResourceWarning -m unittest discover -s tests",
                    block,
                )


if __name__ == "__main__":
    unittest.main()
