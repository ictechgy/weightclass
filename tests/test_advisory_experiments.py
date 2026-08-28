from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from weightclass.advisory import advisory_experiments


class AdvisoryExperimentTests(unittest.TestCase):
    def _record_file(
        self, records: list[dict[str, object]]
    ) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name, "records.jsonl")
        path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
        return directory, path

    def test_sequential_analysis_uses_a_simultaneous_bound_and_never_changes_routing(self) -> None:
        result = advisory_experiments.analyze_sequential(
            [
                {"schema_version": 1, "experiment": "sequential", "accepted": True}
                for _ in range(100)
            ],
            target_rate_bps=7_400,
            alpha_bps=500,
            minimum_samples=20,
            maximum_samples=100,
        )

        self.assertEqual(result["decision"], "signal_above_target")
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["method"], "simultaneous_hoeffding_union_bound")
        self.assertEqual(result["evidence_origin"], "caller_jsonl")
        self.assertFalse(result["promotion_eligible"])
        self.assertFalse(result["policy_decision_allowed"])
        self.assertFalse(result["core_routing_changed"])

    def test_context_matrix_reports_the_descriptive_interaction_only_when_complete(self) -> None:
        records = []
        for cell, accepted, tokens in (
            ("baseline", False, 100),
            ("guard", True, 80),
            ("advisory", True, 120),
            ("guard_advisory", True, 70),
        ):
            records.append(
                {
                    "schema_version": 1,
                    "experiment": "context_2x2",
                    "cell": cell,
                    "accepted": accepted,
                    "input_tokens": tokens,
                    "output_tokens": 0,
                    "elapsed_ms": 10,
                }
            )

        result = advisory_experiments.analyze_context(records)

        self.assertEqual(
            result["descriptive_interaction"],
            {"acceptance_rate_bps": -10_000, "mean_total_tokens": -30},
        )
        self.assertFalse(result["causal_claim"])

    def test_brainstorm_analysis_keeps_preference_compliance_and_duplicates_separate(self) -> None:
        result = advisory_experiments.analyze_brainstorm(
            [
                {
                    "schema_version": 1,
                    "experiment": "brainstorm_generator_critic",
                    "baseline_compliant": False,
                    "treatment_compliant": True,
                    "baseline_critical_violation": True,
                    "treatment_critical_violation": False,
                    "baseline_diversity_bps": 3_000,
                    "treatment_diversity_bps": 8_000,
                    "baseline_duplicate_rate_bps": 4_000,
                    "treatment_duplicate_rate_bps": 1_000,
                    "preference": "treatment",
                    "raters_agree": True,
                },
                {
                    "schema_version": 1,
                    "experiment": "brainstorm_generator_critic",
                    "baseline_compliant": True,
                    "treatment_compliant": True,
                    "baseline_critical_violation": False,
                    "treatment_critical_violation": False,
                    "baseline_diversity_bps": 5_000,
                    "treatment_diversity_bps": 7_000,
                    "baseline_duplicate_rate_bps": 2_000,
                    "treatment_duplicate_rate_bps": 2_000,
                    "preference": "tie",
                    "raters_agree": False,
                },
            ]
        )

        self.assertEqual(result["preference"], {"baseline": 0, "treatment": 1, "tie": 1})
        self.assertEqual(result["treatment_compliance_rate_bps"], 10_000)
        self.assertEqual(result["baseline_critical_violation_rate_bps"], 5_000)
        self.assertEqual(result["treatment_mean_diversity_bps"], 7_500)
        self.assertFalse(result["production_workflow_enabled"])

    def test_confidence_analysis_accounts_for_abstention_without_inventing_outcomes(self) -> None:
        result = advisory_experiments.analyze_confidence(
            [
                {
                    "schema_version": 1,
                    "experiment": "confidence",
                    "predicted_probability_bps": 8_000,
                    "accepted": True,
                    "abstained": False,
                },
                {
                    "schema_version": 1,
                    "experiment": "confidence",
                    "predicted_probability_bps": None,
                    "accepted": None,
                    "abstained": True,
                },
            ]
        )

        self.assertEqual(result["evaluated"], 1)
        self.assertEqual(result["abstained"], 1)
        self.assertEqual(result["brier_squared_error_sum_bps2"], 4_000_000)
        self.assertEqual(result["brier_denominator"], 100_000_000)
        self.assertTrue(result["calibration_metrics_available"])
        self.assertFalse(result["calibration_claim"])

    def test_cli_rejects_unknown_fields_without_echoing_record_content(self) -> None:
        sentinel = "sentinel-private-experiment-8291"
        directory, path = self._record_file(
            [
                {
                    "schema_version": 1,
                    "experiment": "sequential",
                    "accepted": True,
                    "unexpected": sentinel,
                }
            ]
        )
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                result = advisory_experiments.main(["sequential", "--records", str(path)])
        finally:
            directory.cleanup()

        self.assertEqual(result, 2)
        self.assertEqual(json.loads(stderr.getvalue()), {"error": "invalid_experiment_input"})
        self.assertNotIn(sentinel, stderr.getvalue())

    def test_cli_bounds_record_count_before_accumulating_the_study(self) -> None:
        record = json.dumps(
            {"schema_version": 1, "experiment": "sequential", "accepted": True},
            separators=(",", ":"),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "records.jsonl")
            path.write_text(
                (record + "\n") * (advisory_experiments.MAX_EXPERIMENT_RECORDS + 1),
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = advisory_experiments.main(
                    ["sequential", "--records", str(path), "--maximum-samples", "10000"]
                )

        self.assertEqual(result, 2)
        self.assertEqual(json.loads(stderr.getvalue()), {"error": "invalid_experiment_input"})

    def test_cli_redacts_recursive_json_parser_failure(self) -> None:
        directory, path = self._record_file(
            [{"schema_version": 1, "experiment": "sequential", "accepted": True}]
        )
        stderr = io.StringIO()
        try:
            with (
                mock.patch(
                    "weightclass.advisory.advisory_experiments.json.loads",
                    side_effect=RecursionError,
                ),
                contextlib.redirect_stderr(stderr),
            ):
                result = advisory_experiments.main(["sequential", "--records", str(path)])
        finally:
            directory.cleanup()

        self.assertEqual(result, 2)
        self.assertEqual(json.loads(stderr.getvalue()), {"error": "invalid_experiment_input"})

    def test_cli_rejects_real_deep_field_before_retaining_the_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "records.jsonl")
            nested = "[" * 20_000 + "true" + "]" * 20_000
            path.write_text(
                '{"schema_version":1,"experiment":"sequential","accepted":' + nested + "}\n",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = advisory_experiments.main(["sequential", "--records", str(path)])

        self.assertEqual(result, 2)
        self.assertEqual(json.loads(stderr.getvalue()), {"error": "invalid_experiment_input"})


if __name__ == "__main__":
    unittest.main()
