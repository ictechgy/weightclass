import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from typing import Any

BENCHMARK_PATH = pathlib.Path(__file__).parent / "eval" / "provider_usage_benchmark.py"
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


def _arm(usage_units: int) -> dict[str, Any]:
    return {
        "usage_units": usage_units,
        "invocations": 1,
        "completed": True,
        "quality_pass": True,
        "critical_failure": False,
    }


def _evidence(objective: str = "metered_cost") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "objective": objective,
        "baseline_id": "reviewed-baseline-v1",
        "candidate_id": "reviewed-candidate-v1",
        "measurement_contract_id": "provider-export-normalization-v1",
        "baseline_configuration_fingerprint": "sha256:" + "a" * 64,
        "candidate_configuration_fingerprint": "sha256:" + "b" * 64,
        "gate": {
            "minimum_pairs": 30,
            "minimum_usage_savings": 0.15,
            "maximum_savings_ci_width": 0.2,
            "quality_noninferiority_margin": 0.05,
            "savings_ci_rule": "lower-bound",
            "quality_ci_rule": "lower-bound",
            "required_languages": ["en", "ko"],
            "required_categories": list(CATEGORIES),
        },
        "provenance": {
            "fresh_blind_tasks": True,
            "same_sealed_tasks": True,
            "same_provider_runtime": True,
            "only_reviewed_configuration_dimensions_changed": True,
            "provider_export_reviewed": True,
            "normalization_contract_reviewed": True,
            "fixed_subscription_charge": objective == "subscription_quota",
            "export_contains_task_data": False,
            "export_contains_account_identifiers": False,
            "counterbalanced_order": True,
            "all_attempts_included": True,
            "ids_not_task_derived": True,
            "outside_repository_custody": True,
            "independent_quality_review": True,
        },
        "pairs": [
            {
                "id": f"sealed-case-{index + 1:03d}",
                "language": "en" if index % 2 == 0 else "ko",
                "category": CATEGORIES[index % len(CATEGORIES)],
                "expected_tier": TIERS[index % len(TIERS)],
                "baseline": _arm(100 + index),
                "candidate": _arm(50 + index // 2),
            }
            for index in range(72)
        ],
    }


class ProviderUsageBenchmarkTests(unittest.TestCase):
    def _run(self, evidence: object) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            evidence_path = pathlib.Path(directory) / "evidence.json"
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(BENCHMARK_PATH), "--evidence", str(evidence_path)],
                check=False,
                capture_output=True,
                text=True,
            )

    def test_metered_export_can_produce_cost_opt_in_evidence(self) -> None:
        completed = self._run(_evidence())

        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["decision"], "go")
        self.assertEqual(report["measurement"]["objective"], "metered_cost")
        self.assertEqual(report["measurement"]["kind"], "externally_normalized_provider_export")
        self.assertTrue(report["measurement"]["eligible_for_cost_recommendation"])
        self.assertFalse(report["measurement"]["pricing_inferred_by_scorer"])
        self.assertFalse(report["measurement"]["provider_export_verified_by_scorer"])
        self.assertNotIn("sealed-case-", completed.stdout)
        self.assertNotIn('"pairs":[', completed.stdout)

    def test_fixed_subscription_quota_never_claims_bill_reduction(self) -> None:
        completed = self._run(_evidence("subscription_quota"))

        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["decision"], "go")
        self.assertEqual(report["measurement"]["objective"], "subscription_quota")
        self.assertFalse(report["measurement"]["eligible_for_cost_recommendation"])
        self.assertFalse(report["measurement"]["monthly_bill_reduction_claimed"])
        self.assertEqual(report["measurement"]["promotion_scope"], "capacity_only")

    def test_nine_pair_canary_is_scored_but_cannot_promote(self) -> None:
        evidence = _evidence()
        evidence["pairs"] = evidence["pairs"][:9]

        completed = self._run(evidence)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["decision"], "no-go")
        self.assertEqual(report["samples"]["pairs"], 9)
        self.assertFalse(report["gates"]["minimum_pairs"]["passes"])

    def test_rejects_unsanitized_or_misclassified_export_assertions(self) -> None:
        cases = []
        for field, value in (
            ("provider_export_reviewed", False),
            ("normalization_contract_reviewed", False),
            ("export_contains_task_data", True),
            ("export_contains_account_identifiers", True),
            ("fixed_subscription_charge", True),
        ):
            evidence = _evidence()
            evidence["provenance"][field] = value
            cases.append(evidence)
        quota = _evidence("subscription_quota")
        quota["provenance"]["fixed_subscription_charge"] = False
        cases.append(quota)
        invalid_objective = _evidence()
        invalid_objective["objective"] = "tokens"
        cases.append(invalid_objective)
        task_bearing = _evidence()
        task_bearing["task"] = "must not be accepted"
        cases.append(task_bearing)

        for evidence in cases:
            with self.subTest():
                completed = self._run(evidence)
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, "")
                self.assertEqual(completed.stderr, "invalid evidence\n")

    def test_rejects_a_symlink_without_opening_the_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / "target.json"
            target.write_text(json.dumps(_evidence()), encoding="utf-8")
            link = root / "evidence.json"
            link.symlink_to(target)

            completed = subprocess.run(
                [sys.executable, str(BENCHMARK_PATH), "--evidence", str(link)],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "invalid evidence\n")


if __name__ == "__main__":
    unittest.main()
