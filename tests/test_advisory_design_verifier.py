from __future__ import annotations

import copy
import json
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERIFIER = ROOT / ".weightclass" / "verify-design"
REPOSITORY_VERIFIER_AVAILABLE = VERIFIER.is_file()


def option(
    title: str,
    rationale: str,
    evidence: str,
    surface: str,
    strength: str,
    risk: str,
) -> dict[str, object]:
    return {
        "title": title,
        "rationale": rationale,
        "evidence": [evidence],
        "strengths": [strength],
        "risks": [risk],
        "affected_surfaces": [surface],
    }


def accepted_result() -> dict[str, object]:
    return {
        "schema_version": 1,
        "mode": "design",
        "problem": (
            "Choose the next repository-grounded improvements without weakening safety boundaries."
        ),
        "summary": (
            "Three independently useful options cover measurement, runtime security, and "
            "operator workflow."
        ),
        "principles": [
            "Preserve task privacy and fail-closed behavior.",
            "Prefer reversible changes with explicit compatibility gates.",
            "Measure cost, token volume, latency, and accepted quality separately.",
        ],
        "options": [
            option(
                "Adaptive campaign stopping",
                "Improve measurement efficiency while preserving sealed campaign decisions.",
                (
                    "src/weightclass/advisory/advisory_campaign.py:39 bounds anonymous "
                    "campaign lanes and tasks."
                ),
                "src/weightclass/advisory/advisory_campaign.py",
                "Reduces unnecessary cost and token consumption after decisive evidence.",
                "Premature stopping can bias a small sample.",
            ),
            option(
                "Verified runtime isolation",
                "Strengthen security around provider and verifier process execution boundaries.",
                (
                    "src/weightclass/advisory/advisory_orchestration.py:117 prepares "
                    "owner-only lane directories."
                ),
                "src/weightclass/advisory/advisory_orchestration.py",
                "Improves sandbox and credential isolation guarantees.",
                "Additional isolation can increase latency and compatibility risk.",
            ),
            option(
                "Operator decision dashboard",
                (
                    "Turn aggregate evidence into a clearer product workflow without exposing "
                    "task content."
                ),
                (
                    "src/weightclass/advisory/speculative_report.py:1141 computes advisor "
                    "and retry statistics."
                ),
                "src/weightclass/advisory/speculative_report.py",
                "Improves operator usability and integration decisions.",
                "A dashboard can overstate uncertain estimates.",
            ),
        ],
        "recommendation": (
            "Start with Verified runtime isolation, then expose its measured effect in the "
            "operator dashboard."
        ),
        "acceptance_criteria": [
            "Keep 0 task-content fields in every persisted record.",
            "Run at least 60 representative tasks before a promotion verdict.",
            "Keep p95 routing overhead below 50 milliseconds.",
            "Pass the supported Python 3.10 through 3.14 matrix.",
        ],
        "validation": [
            "Run the full unittest suite and the guarded runtime tests.",
            "Benchmark routing latency and peak memory before and after the change.",
            "Measure campaign cost intervals and inspect security boundary failures.",
        ],
        "limitations": [
            "Human preference and provider availability remain outside this mechanical gate."
        ],
    }


@unittest.skipUnless(REPOSITORY_VERIFIER_AVAILABLE, "repository-only design verifier unavailable")
class AdvisoryDesignVerifierTests(unittest.TestCase):
    def run_verifier(self, value: dict[str, object]) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["WCLASS_ADVISORY_WORKFLOW"] = "design"
        return subprocess.run(
            [str(VERIFIER)],
            cwd=ROOT,
            input=json.dumps(value),
            capture_output=True,
            check=False,
            text=True,
            env=environment,
        )

    def test_accepts_grounded_multidimensional_design(self) -> None:
        self.assertEqual(self.run_verifier(accepted_result()).returncode, 0)

    def test_baseline_probe_returns_42(self) -> None:
        probe = accepted_result()
        probe["problem"] = "Prospective baseline probe; no task was evaluated."
        probe["limitations"] = ["baseline_probe"]
        self.assertEqual(self.run_verifier(probe).returncode, 42)

    def test_requires_three_options_and_a_named_recommendation(self) -> None:
        too_few = accepted_result()
        options = too_few["options"]
        assert isinstance(options, list)
        options.pop()
        self.assertEqual(self.run_verifier(too_few).returncode, 1)

        unnamed = accepted_result()
        unnamed["recommendation"] = "Proceed with the strongest alternative after validation."
        self.assertEqual(self.run_verifier(unnamed).returncode, 1)

    def test_rejects_untracked_or_out_of_range_evidence(self) -> None:
        for citation in (
            "missing.py:1",
            "src/weightclass/advisory/advisory_campaign.py:99999",
        ):
            with self.subTest(citation=citation):
                value = accepted_result()
                options = value["options"]
                assert isinstance(options, list)
                first = options[0]
                assert isinstance(first, dict)
                first["evidence"] = [citation]
                self.assertEqual(self.run_verifier(value).returncode, 1)

    def test_requires_all_dimensions_and_measurable_validation(self) -> None:
        missing_security = accepted_result()
        rendered = (
            json.dumps(missing_security)
            .replace("security", "reliability")
            .replace("sandbox", "boundary")
            .replace("credential", "configuration")
            .replace("privacy", "correctness")
            .replace("isolation", "containment")
        )
        missing_security = json.loads(rendered)
        self.assertEqual(self.run_verifier(missing_security).returncode, 1)

        unmeasured = accepted_result()
        unmeasured["acceptance_criteria"] = [
            "Preserve privacy.",
            "Remain compatible.",
            "Improve latency.",
            "Document the workflow.",
        ]
        self.assertEqual(self.run_verifier(unmeasured).returncode, 1)

    def test_does_not_mutate_input_fixture(self) -> None:
        original = accepted_result()
        value = copy.deepcopy(original)
        self.run_verifier(value)
        self.assertEqual(value, original)


if __name__ == "__main__":
    unittest.main()
