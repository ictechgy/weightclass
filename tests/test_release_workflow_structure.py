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
                self.assertIn('python -m venv "$installed_venv"', block)
                self.assertIn('"$installed_venv/bin/wclass" --version', block)
                self.assertIn('"$installed_venv/bin/wclass" classify', block)
                for policy_name in (
                    "agy-cost-focused",
                    "claude-cost-focused",
                    "codex-cost-focused",
                    "grok-cost-focused",
                ):
                    self.assertIn(
                        f'"$installed_venv/bin/wclass" example-policy {policy_name}',
                        block,
                    )
                self.assertIn(
                    '"$installed_venv/bin/wclass" example-policy codex-cost-focused '
                    "--model release-smoke-model",
                    block,
                )
                self.assertIn(
                    '"$installed_venv/bin/wclass" route --cost-focused '
                    "--source-vendor codex --model release-smoke-model --tier standard",
                    block,
                )
                self.assertIn(
                    '"$installed_venv/bin/wclass" review-preset '
                    "claude-cost-focused --low-model release-smoke-claude-model "
                    "--low-effort low",
                    block,
                )
                self.assertIn(
                    '"$installed_venv/bin/wclass" route --preset codex-cost-focused '
                    "--standard-model release-smoke-codex-model "
                    "--standard-effort medium --tier standard",
                    block,
                )
                self.assertIn(
                    '"$installed_venv/bin/wclass" route --preset grok-cost-focused '
                    "--standard-model release-smoke-grok-model --tier standard",
                    block,
                )
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

    def test_release_toolchain_is_exactly_hash_pinned_before_the_only_build(self) -> None:
        """Breaks if a tag build can resolve mutable tool or backend artifacts."""
        requirements_path = Path("requirements/release.txt")
        self.assertTrue(requirements_path.is_file(), "release requirements lock is missing")
        if not requirements_path.is_file():
            return
        requirements = requirements_path.read_text(encoding="utf-8")
        workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
        normalized_workflow = " ".join(workflow.split())
        pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

        first_requirement = re.search(r"^[a-z0-9][a-z0-9._-]*==", requirements, re.MULTILINE)
        self.assertIsNotNone(first_requirement)
        if first_requirement is None:
            return
        requirement_blocks = re.split(
            r"\n(?=[a-z0-9][a-z0-9._-]*==)", requirements[first_requirement.start() :]
        )
        pinned_names: set[str] = set()
        for block in requirement_blocks:
            stripped = block.lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            first_line = stripped.splitlines()[0]
            match = re.fullmatch(r"([a-z0-9][a-z0-9._-]*)==[^ \\]+(?: \\)?", first_line)
            self.assertIsNotNone(match, first_line)
            if match is None:
                continue
            pinned_names.add(match.group(1))
            self.assertRegex(block, r"--hash=sha256:[0-9a-f]{64}")

        self.assertEqual(
            pinned_names,
            {
                "ast-serialize",
                "build",
                "docutils",
                "librt",
                "markdown-it-py",
                "mdurl",
                "mypy",
                "mypy-extensions",
                "nh3",
                "packaging",
                "pathspec",
                "pygments",
                "pyproject-hooks",
                "readme-renderer",
                "rich",
                "ruff",
                "setuptools",
                "twine",
                "typing-extensions",
            },
        )
        self.assertIn(
            "python -m pip install --require-hashes --only-binary=:all: --no-deps "
            "--requirement requirements/release.txt",
            normalized_workflow,
        )
        self.assertIn("python -m build --no-isolation --outdir build-output", workflow)
        build_job = workflow.split("\n  build-candidate:\n", 1)[1].split(
            "\n  validate-python-310:\n", 1
        )[0]
        self.assertIn('python-version: "3.13.12"', build_job)
        self.assertRegex(pyproject, r'requires = \["setuptools==[0-9]+(?:\.[0-9]+)+"\]')

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

    def test_release_build_proves_the_tag_is_reachable_from_origin_main(self) -> None:
        """Breaks if a detached or unmerged tag can reach trusted publishing."""
        text = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
        block = text.split("\n  build-candidate:\n", 1)[1].split("\n  validate-python-310:\n", 1)[0]

        self.assertIn("fetch-depth: 0", block)
        self.assertIn("tests/verify_release_source.py", block)
        self.assertIn('--tag-commit "$GITHUB_SHA"', block)
        self.assertIn("--main-ref refs/remotes/origin/main", block)

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
