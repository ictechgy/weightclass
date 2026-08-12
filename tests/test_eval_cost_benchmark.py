import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from typing import Any

COST_BENCHMARK_PATH = pathlib.Path(__file__).parent / "eval" / "cost_benchmark.py"
CATEGORIES = (
    "concurrency",
    "data-integrity",
    "destructive-work",
    "migration",
    "performance",
    "privacy",
    "reliability",
    "routine",
    "security",
)
TIERS = ("low", "standard", "high")


def _arm(cost_units: int, *, quality_pass: bool) -> dict[str, Any]:
    return {
        "estimated_cost_units": cost_units,
        "invocations": 1,
        "completed": True,
        "quality_pass": quality_pass,
        "critical_failure": False,
    }


def _evidence() -> dict[str, Any]:
    pairs = []
    for index in range(30):
        pairs.append(
            {
                "id": f"sealed-case-{index + 1:03d}",
                "language": "en" if index % 2 == 0 else "ko",
                "category": CATEGORIES[index % len(CATEGORIES)],
                "expected_tier": TIERS[index % len(TIERS)],
                "baseline": _arm(100, quality_pass=index >= 15),
                "candidate": _arm(
                    60 if index % 2 == 0 else 70,
                    quality_pass=True,
                ),
            }
        )
    return {
        "schema_version": 1,
        "baseline_id": "default-medium-v1",
        "candidate_id": "haiku-low-v1",
        "measurement_contract_id": "claude-reported-cost-v1",
        "baseline_configuration_fingerprint": "sha256:" + "a" * 64,
        "candidate_configuration_fingerprint": "sha256:" + "b" * 64,
        "gate": {
            "minimum_pairs": 30,
            "minimum_estimated_cost_savings": 0.15,
            "maximum_savings_ci_width": 0.8,
            "quality_noninferiority_margin": 0.05,
            "savings_ci_rule": "lower-bound",
            "quality_ci_rule": "lower-bound",
            "required_languages": ["en", "ko"],
            "required_categories": ["security", "routine"],
        },
        "provenance": {
            "fresh_blind_tasks": True,
            "same_sealed_tasks": True,
            "same_provider_runtime": True,
            "only_reviewed_configuration_dimensions_changed": True,
            "estimated_cost_contract_reviewed": True,
            "counterbalanced_order": True,
            "all_attempts_included": True,
            "ids_not_task_derived": True,
            "outside_repository_custody": True,
            "independent_quality_review": True,
        },
        "pairs": pairs,
    }


class CostBenchmarkTests(unittest.TestCase):
    def _run(self, evidence: object) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            evidence_path = pathlib.Path(directory) / "evidence.json"
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(COST_BENCHMARK_PATH), "--evidence", str(evidence_path)],
                check=False,
                capture_output=True,
                text=True,
            )

    def test_scores_externally_normalized_cost_without_inferring_pricing(self) -> None:
        completed = self._run(_evidence())

        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["decision"], "go")
        self.assertEqual(report["baseline"]["estimated_cost_units"], 3_000)
        self.assertEqual(report["candidate"]["estimated_cost_units"], 1_950)
        self.assertEqual(report["measurement"]["kind"], "externally_normalized_estimated_cost")
        self.assertFalse(report["measurement"]["pricing_inferred_by_scorer"])
        self.assertGreater(report["estimated_cost_savings"]["estimate"], 0.3)

    def test_valid_insufficient_cost_savings_is_a_scored_no_go(self) -> None:
        evidence = _evidence()
        for pair in evidence["pairs"]:
            pair["candidate"]["estimated_cost_units"] = 99

        completed = self._run(evidence)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["decision"], "no-go")

    def test_rejects_invalid_or_token_shaped_cost_arms_with_redacted_diagnostic(self) -> None:
        cases = []
        evidence = _evidence()
        evidence["pairs"][0]["candidate"]["estimated_cost_units"] = -1
        cases.append(evidence)
        evidence = _evidence()
        evidence["pairs"][0]["candidate"]["estimated_cost_units"] = 1.5
        cases.append(evidence)
        evidence = _evidence()
        evidence["pairs"][0]["candidate"]["net_tokens"] = 1
        cases.append(evidence)

        for evidence in cases:
            with self.subTest():
                completed = self._run(evidence)
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, "")
                self.assertEqual(completed.stderr, "invalid evidence\n")

    def test_report_omits_pair_records_and_token_metric_names(self) -> None:
        completed = self._run(_evidence())

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("sealed-case-", completed.stdout)
        self.assertNotIn("net_tokens", completed.stdout)
        self.assertNotIn('"pairs":[', completed.stdout)

    def test_rejects_symlink_evidence_without_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / "target.json"
            target.write_text(json.dumps(_evidence()), encoding="utf-8")
            link = root / "evidence.json"
            link.symlink_to(target)

            completed = subprocess.run(
                [sys.executable, str(COST_BENCHMARK_PATH), "--evidence", str(link)],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(
            (completed.returncode, completed.stdout, completed.stderr),
            (2, "", "invalid evidence\n"),
        )


if __name__ == "__main__":
    unittest.main()
