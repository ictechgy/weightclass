from __future__ import annotations

import re
import unittest
from pathlib import Path

MACOS_BOUNDARY_MODULES = (
    "tests.test_router",
    "tests.test_agent_discovery",
    "tests.test_native_v2_runtime",
    "tests.test_executable_observation",
    "tests.test_foreground_process",
    "tests.test_process_context",
    "tests.test_triage",
    "tests.test_json_input",
    "tests.test_cost_recommendation",
    "tests.test_delegation_runtime",
    "tests.test_delegation_v2_runtime",
    "tests.test_guarded_runtime_suite",
    "tests.test_delegation_conformance",
)


class CIWorkflowStructureTests(unittest.TestCase):
    text: str

    @classmethod
    def setUpClass(cls) -> None:
        workflow = Path(".github/workflows/ci.yml")
        if not workflow.is_file():
            raise unittest.SkipTest("workflow sources are intentionally absent from the sdist")
        cls.text = workflow.read_text(encoding="utf-8")

    def test_linux_full_suite_matrix_has_every_supported_interpreter(self) -> None:
        matrix = re.search(r"python-version: \[(?P<versions>[^\]]+)\]", self.text)
        self.assertIsNotNone(matrix)
        assert matrix is not None
        self.assertEqual(
            re.findall(r'"(3\.\d+)"', matrix.group("versions")),
            ["3.10", "3.11", "3.12", "3.13", "3.14"],
        )
        test_job = self.text.split("\n  test:\n", 1)[1].split("\n  quality:\n", 1)[0]
        self.assertIn("python -m pytest -q", test_job)
        self.assertIn(
            "matrix.python-version == '3.10' || matrix.python-version == '3.14'", test_job
        )
        self.assertIn("-W error::ResourceWarning", test_job)
        self.assertIn("wclass-advisory --help", test_job)

    def test_feature_branch_pushes_do_not_duplicate_pull_request_ci(self) -> None:
        """Breaks if one PR head starts both push and pull-request matrices."""
        triggers = self.text.split("\njobs:\n", 1)[0]
        self.assertIn("push:\n    branches: [main]", triggers)
        self.assertIn("pull_request:", triggers)
        self.assertIn(
            "group: ${{ github.workflow }}-"
            "${{ github.event.pull_request.number || github.run_id }}",
            triggers,
        )
        self.assertIn("cancel-in-progress: true", triggers)

    def test_quality_job_contains_every_required_gate(self) -> None:
        quality = self.text.split("\n  quality:\n", 1)[1].split(
            "\n  macos-routing-boundaries:\n", 1
        )[0]
        for command in (
            "ruff check .",
            "ruff format --check .",
            "mypy --strict src tests",
            "python -m compileall -q src tests",
            "git diff --check",
            "tests/test_guarded_runtime_suite.py",
            "tests/test_orchestration_traceability.py",
            "tests/test_ci_workflow_structure.py",
            "tests/test_release_workflow_structure.py",
            "tests/test_completion_audit_v2.py",
        ):
            with self.subTest(command=command):
                self.assertIn(command, quality)
        self.assertIn("PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q", quality)

    def test_whitespace_gate_checks_the_committed_head_delta(self) -> None:
        quality = self.text.split("\n  quality:\n", 1)[1].split(
            "\n  macos-routing-boundaries:\n", 1
        )[0]
        self.assertIn("fetch-depth: 2", quality)
        self.assertIn("git diff --check HEAD^1 HEAD", quality)

    def test_setup_python_actions_use_one_pinned_node24_generation(self) -> None:
        """Breaks if CI regresses to a Node 20 setup-python action."""
        release = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
        matches = re.findall(
            r"actions/setup-python@(?P<sha>[0-9a-f]{40}) # v(?P<major>[0-9]+)\.[0-9]+\.[0-9]+",
            self.text + release,
        )

        self.assertEqual(len(matches), 7)
        self.assertEqual(
            set(matches),
            {("5fda3b95a4ea91299a34e894583c3862153e4b97", "7")},
        )

    def test_macos_boundary_matrix_and_claimed_suites_are_exact(self) -> None:
        block = self.text.split("\n  macos-routing-boundaries:\n", 1)[1]
        self.assertIn('python-version: ["3.10", "3.14"]', block)
        observed_modules = tuple(re.findall(r"tests\.test_[a-z0-9_]+", block))
        self.assertEqual(observed_modules, MACOS_BOUNDARY_MODULES)
        for module in MACOS_BOUNDARY_MODULES:
            with self.subTest(module=module):
                self.assertIn(module, block)

    def test_python_314_is_advertised_in_classifier_metadata(self) -> None:
        metadata = Path("pyproject.toml").read_text(encoding="utf-8")
        self.assertEqual(metadata.count('"Programming Language :: Python :: 3.14"'), 1)


if __name__ == "__main__":
    unittest.main()
