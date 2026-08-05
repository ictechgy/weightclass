import contextlib
import hashlib
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from unittest import mock

SCORE_PATH = pathlib.Path(__file__).parent / "eval" / "score.py"
SPEC = importlib.util.spec_from_file_location("eval_score", SCORE_PATH)
assert SPEC is not None and SPEC.loader is not None
score = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(score)


class CorpusValidationTests(unittest.TestCase):
    def test_accepts_a_complete_synthetic_corpus(self) -> None:
        entries = [
            {
                "index": 0,
                "task": "secret sentinel task",
                "consensus": "high",
                "language": "en",
                "category": "security",
            }
        ]

        self.assertEqual(score.validate_corpus(entries, require_category=True), entries)

    def test_rejects_invalid_entries_without_including_field_values(self) -> None:
        secret = "secret sentinel task"
        entries = [
            {
                "index": 0,
                "task": secret,
                "consensus": "urgent",
                "language": "en",
                "category": "security",
            }
        ]

        with self.assertRaises(score.CorpusValidationError) as raised:
            score.validate_corpus(entries, require_category=True)

        self.assertIn("entry 0", str(raised.exception))
        self.assertIn("consensus", str(raised.exception))
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn("urgent", str(raised.exception))

    def test_synthetic_corpus_requires_category_slices(self) -> None:
        entries = [{"index": 0, "task": "hidden", "consensus": "low", "language": "ko"}]

        with self.assertRaises(score.CorpusValidationError) as raised:
            score.validate_corpus(entries, require_category=True)

        self.assertIn("category", str(raised.exception))

    def test_synthetic_corpus_rejects_unreviewed_slice_labels_without_echoing_them(self) -> None:
        """Breaks if an arbitrary label can carry sealed task text into reports."""
        sentinel = "SEALED TASK TEXT MUST NEVER BECOME A SLICE LABEL"
        for field in ("language", "category"):
            entry = {
                "task": sentinel,
                "consensus": "high",
                "language": "en",
                "category": "security",
            }
            entry[field] = sentinel

            with self.subTest(field=field):
                with self.assertRaises(score.CorpusValidationError) as raised:
                    score.validate_corpus([entry], require_category=True)

                self.assertNotIn(sentinel, str(raised.exception))


class MetricAggregationTests(unittest.TestCase):
    def test_aggregates_confusion_recall_overrouting_and_slices(self) -> None:
        records = [
            {"expected": "high", "predicted": "high", "language": "en", "category": "security"},
            {"expected": "high", "predicted": "standard", "language": "ko", "category": "security"},
            {"expected": "low", "predicted": "high", "language": "en", "category": "routine"},
            {
                "expected": "standard",
                "predicted": "standard",
                "language": "ko",
                "category": "routine",
            },
        ]

        metrics = score.aggregate_metrics(records)

        self.assertEqual(
            metrics["confusion_matrix"],
            {
                "low": {"low": 0, "standard": 0, "high": 1},
                "standard": {"low": 0, "standard": 1, "high": 0},
                "high": {"low": 0, "standard": 1, "high": 1},
            },
        )
        self.assertEqual(metrics["agreement"]["count"], 2)
        self.assertEqual(metrics["high_recall"]["count"], 1)
        self.assertEqual(metrics["high_recall"]["total"], 2)
        self.assertEqual(metrics["over_routing"]["count"], 1)
        self.assertEqual(metrics["over_routing"]["total"], 4)
        self.assertEqual(metrics["by_language"]["en"]["total"], 2)
        self.assertEqual(metrics["by_category"]["security"]["high_recall"]["count"], 1)
        self.assertEqual(len(metrics["high_recall"]["confidence_interval_95"]), 2)

    def test_reports_every_supported_slice_including_empty_slices(self) -> None:
        records = [{"expected": "low", "predicted": "low", "language": "en", "category": "routine"}]

        metrics = score.aggregate_metrics(records)

        self.assertEqual(set(metrics["by_language"]), score.LANGUAGES)
        self.assertEqual(set(metrics["by_category"]), score.CATEGORIES)
        self.assertEqual(metrics["by_language"]["ko"]["total"], 0)
        self.assertEqual(metrics["by_category"]["security"]["agreement"]["rate"], 0.0)

    def test_wilson_interval_boundary_cases_are_bounded_and_defined(self) -> None:
        self.assertEqual(score._rate(0, 0)["confidence_interval_95"], (0.0, 0.0))
        self.assertEqual(score._rate(0, 1)["confidence_interval_95"][0], 0.0)
        self.assertEqual(score._rate(1, 1)["confidence_interval_95"][1], 1.0)


class OfflineOutputTests(unittest.TestCase):
    def test_candidate_scores_predictions_and_emits_structured_gate_decision(self) -> None:
        secret = "SEALED CANDIDATE TASK"
        entries = [
            {
                "id": "task-1",
                "task": f"{secret} security",
                "consensus": "high",
                "language": "en",
                "category": "security",
            },
            {
                "id": "task-2",
                "task": "another sealed task typo",
                "consensus": "low",
                "language": "ko",
                "category": "routine",
            },
        ]
        candidate = {
            "schema_version": 1,
            "candidate_id": "semantic-candidate-1",
            "baseline_id": "deterministic-baseline-1",
            "predictions": [
                {"id": "task-1", "label": "high", "prediction": "high"},
                {"id": "task-2", "label": "low", "prediction": "low"},
            ],
            "quality_gate": {
                "high_tier_recall_min": 0.20,
                "high_tier_recall_ci_rule": "lower-bound",
                "over_routing_max": 0.70,
                "over_routing_ci_rule": "upper-bound",
                "slices_reviewed": True,
                "unexplained_slice_regression": False,
            },
            "resource_gate": {
                "startup_accepted": True,
                "latency_accepted": True,
                "memory_accepted": True,
                "supported_platform_determinism_accepted": True,
            },
            "supply_chain_gate": {
                "dependency_pin_reviewed": True,
                "dependency_audit_accepted": True,
                "model_download_required": False,
                "maintenance_cost_accepted": True,
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            corpus = pathlib.Path(directory) / "blind.json"
            candidate_path = pathlib.Path(directory) / "candidate.json"
            corpus.write_text(json.dumps(entries), encoding="utf-8")
            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = score.main(["--corpus", str(corpus), "--candidate", str(candidate_path)])

        report = json.loads(stdout.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertNotIn("candidate_id", report)
        self.assertNotIn("baseline_id", report)
        self.assertNotIn("semantic-candidate-1", stdout.getvalue())
        self.assertNotIn("deterministic-baseline-1", stdout.getvalue())
        self.assertEqual(
            report["corpus"],
            {
                "entries": 2,
                "evaluator_supplied": True,
                "freshness_verified_by_scorer": False,
            },
        )
        self.assertEqual(report["quality_gate"]["high_tier_recall"]["count"], 1)
        self.assertEqual(report["quality_gate"]["over_routing"]["count"], 0)
        self.assertEqual(
            report["quality_gate"]["high_tier_recall"]["confidence_interval_95"],
            [0.206543, 1.0],
        )
        self.assertEqual(report["quality_gate"]["by_language"]["ko"]["total"], 1)
        self.assertEqual(report["quality_gate"]["by_category"]["security"]["total"], 1)
        self.assertEqual(
            report["comparison_gate"],
            {
                "candidate_below_baseline_count": 0,
                "high_tier_recall_rate_delta": 0.0,
                "over_routing_rate_delta": 0.0,
                "raise_only_required": True,
                "passes": True,
            },
        )
        self.assertEqual(
            report["resource_gate"],
            {
                "startup_accepted": True,
                "latency_accepted": True,
                "memory_accepted": True,
                "supported_platform_determinism_accepted": True,
                "passes": True,
            },
        )
        self.assertEqual(
            report["supply_chain_gate"],
            {
                "dependency_pin_reviewed": True,
                "dependency_audit_accepted": True,
                "model_download_required": False,
                "maintenance_cost_accepted": True,
                "passes": True,
            },
        )
        self.assertEqual(
            report["privacy_gate"],
            {
                "aggregate_only_report": True,
                "candidate_and_baseline_identifiers_emitted": False,
                "corpus_task_field_or_per_task_record_emitted": False,
            },
        )
        self.assertEqual(report["decision"], "go")
        self.assertNotIn(secret, stdout.getvalue() + stderr.getvalue())

    def test_candidate_report_includes_same_corpus_local_baseline_metrics(self) -> None:
        entries = [
            {
                "id": "task-1",
                "task": "Fix typo.",
                "consensus": "high",
                "language": "en",
                "category": "security",
            },
            {
                "id": "task-2",
                "task": "Rotate leaked credentials now.",
                "consensus": "low",
                "language": "ko",
                "category": "routine",
            },
        ]
        candidate = self._complete_candidate(
            predictions=[
                self._prediction(label="high", prediction="high"),
                self._prediction(identifier="task-2", label="low", prediction="low"),
            ]
        )

        result, output = self._run_candidate(entries, candidate)

        report = json.loads(output)
        self.assertEqual(result, 0)
        self.assertIn("baseline_metrics", report)
        self.assertEqual(report["baseline_metrics"]["high_recall"]["count"], 0)
        self.assertEqual(report["baseline_metrics"]["high_recall"]["total"], 1)
        self.assertEqual(report["baseline_metrics"]["over_routing"]["count"], 1)
        self.assertEqual(report["baseline_metrics"]["over_routing"]["total"], 2)
        self.assertEqual(report["quality_gate"]["high_tier_recall"]["count"], 1)
        self.assertEqual(report["quality_gate"]["over_routing"]["count"], 0)

    def test_candidate_report_does_not_claim_to_verify_corpus_freshness(self) -> None:
        entries = [
            {
                "id": "task-1",
                "task": "Hidden evaluator task.",
                "consensus": "low",
                "language": "en",
                "category": "routine",
            }
        ]
        candidate = self._complete_candidate(predictions=[self._prediction()])

        result, output = self._run_candidate(entries, candidate)

        report = json.loads(output)
        self.assertEqual(result, 0)
        self.assertEqual(
            report["corpus"],
            {
                "entries": 1,
                "evaluator_supplied": True,
                "freshness_verified_by_scorer": False,
            },
        )

    def test_candidate_privacy_gate_does_not_emit_unverified_identifiers(self) -> None:
        candidate_sentinel = "SEALED-CANDIDATE-TASK-TEXT"
        baseline_sentinel = "SEALED-BASELINE-TASK-TEXT"
        entries = [
            {
                "id": "task-1",
                "task": "Hidden evaluator task.",
                "consensus": "low",
                "language": "en",
                "category": "routine",
            }
        ]
        candidate = self._complete_candidate(predictions=[self._prediction()])
        candidate["candidate_id"] = candidate_sentinel
        candidate["baseline_id"] = baseline_sentinel

        result, output = self._run_candidate(entries, candidate)

        report = json.loads(output)
        self.assertEqual(result, 0)
        self.assertEqual(
            report["privacy_gate"],
            {
                "aggregate_only_report": True,
                "candidate_and_baseline_identifiers_emitted": False,
                "corpus_task_field_or_per_task_record_emitted": False,
            },
        )
        self.assertNotIn(candidate_sentinel, output)
        self.assertNotIn(baseline_sentinel, output)

    def test_candidate_rejects_unhashable_metadata_without_a_traceback(self) -> None:
        base_entry: dict[str, object] = {
            "id": "task-1",
            "task": "SEALED-METADATA-TASK",
            "consensus": "low",
            "language": "en",
            "category": "routine",
        }
        for field, invalid_value in (
            ("consensus", []),
            ("language", []),
            ("category", {}),
        ):
            with self.subTest(field=field):
                entry = {**base_entry, field: invalid_value}
                result, output = self._run_candidate(
                    [entry], self._complete_candidate(predictions=[self._prediction()])
                )
                self.assertEqual(result, 2)
                self.assertNotIn("Traceback", output)
                self.assertNotIn("SEALED-METADATA-TASK", output)

    def test_candidate_rejects_an_oversized_task_without_a_traceback(self) -> None:
        sentinel = "SEALED-OVERSIZED-TASK"
        entries = [
            {
                "id": "task-1",
                "task": sentinel + ("x" * 20_001),
                "consensus": "low",
                "language": "en",
                "category": "routine",
            }
        ]

        result, output = self._run_candidate(
            entries, self._complete_candidate(predictions=[self._prediction()])
        )

        self.assertEqual(result, 2)
        self.assertNotIn("Traceback", output)
        self.assertNotIn(sentinel, output)

    def test_candidate_rejects_prediction_count_mismatch_without_task_output(self) -> None:
        secret = "MISMATCHED SEALED TASK"
        entries = [
            {
                "id": "task-1",
                "task": secret,
                "consensus": "high",
                "language": "en",
                "category": "security",
            }
        ]
        candidate = self._complete_candidate(predictions=[])

        result, output = self._run_candidate(entries, candidate)

        self.assertEqual(result, 2)
        self.assertIn("prediction count does not match corpus", output)
        self.assertNotIn(secret, output)

    def test_candidate_rejects_output_bearing_metadata_without_echoing_it(self) -> None:
        sentinel = "SEALED TASK TEXT IN CANDIDATE ID"
        entries = [
            {
                "id": "task-1",
                "task": "hidden",
                "consensus": "low",
                "language": "en",
                "category": "routine",
            }
        ]
        candidate = self._complete_candidate(predictions=[self._prediction()])
        candidate["candidate_id"] = sentinel

        result, output = self._run_candidate(entries, candidate)

        self.assertEqual(result, 2)
        self.assertIn("candidate_id", output)
        self.assertNotIn(sentinel, output)

    def test_candidate_rejects_matching_candidate_and_baseline_ids(self) -> None:
        entries = [
            {
                "id": "task-1",
                "task": "hidden",
                "consensus": "low",
                "language": "en",
                "category": "routine",
            }
        ]
        candidate = self._complete_candidate(predictions=[self._prediction()])
        candidate["baseline_id"] = candidate["candidate_id"]

        result, output = self._run_candidate(entries, candidate)

        self.assertEqual(result, 2)
        self.assertIn("candidate_id and baseline_id must differ", output)
        self.assertNotIn("candidate-1", output)

    def test_candidate_mode_rejects_duplicate_json_fields(self) -> None:
        entry = {
            "id": "task-1",
            "task": "hidden",
            "consensus": "low",
            "language": "en",
            "category": "routine",
        }
        candidate = self._complete_candidate(predictions=[self._prediction()])
        valid_corpus = json.dumps([entry])
        valid_candidate = json.dumps(candidate)
        cases = {
            "corpus": (
                '[{"id":"task-1","task":"SEALED-FIRST-VALUE","task":"SEALED-SECOND-VALUE",'
                '"consensus":"low","language":"en","category":"routine"}]',
                valid_candidate,
                ("task", "SEALED-FIRST-VALUE", "SEALED-SECOND-VALUE"),
            ),
            "candidate": (
                valid_corpus,
                valid_candidate.replace(
                    '"candidate_id": "candidate-1"',
                    '"candidate_id": "SEALED-FIRST-ID", "candidate_id": "SEALED-SECOND-ID"',
                    1,
                ),
                ("candidate_id", "SEALED-FIRST-ID", "SEALED-SECOND-ID"),
            ),
        }
        for name, (corpus_json, candidate_json, sensitive_values) in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                corpus_path = pathlib.Path(directory) / "blind.json"
                candidate_path = pathlib.Path(directory) / "candidate.json"
                corpus_path.write_text(corpus_json, encoding="utf-8")
                candidate_path.write_text(candidate_json, encoding="utf-8")
                stdout = io.StringIO()
                stderr = io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    result = score.main(
                        ["--corpus", str(corpus_path), "--candidate", str(candidate_path)]
                    )
                output = stdout.getvalue() + stderr.getvalue()
                self.assertEqual(result, 2)
                self.assertIn("duplicate object fields", output)
                for sensitive_value in sensitive_values:
                    self.assertNotIn(sensitive_value, output)

    def test_candidate_rejects_boolean_schema_version(self) -> None:
        entries = [
            {
                "id": "task-1",
                "task": "hidden",
                "consensus": "low",
                "language": "en",
                "category": "routine",
            }
        ]
        candidate = self._complete_candidate(predictions=[self._prediction()])
        candidate["schema_version"] = True

        result, output = self._run_candidate(entries, candidate)

        self.assertEqual(result, 2)
        self.assertIn("schema_version", output)

    def test_candidate_rejects_the_public_fixture(self) -> None:
        candidate = self._complete_candidate(predictions=[])
        with tempfile.TemporaryDirectory() as directory:
            candidate_path = pathlib.Path(directory) / "candidate.json"
            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = score.main(["--candidate", str(candidate_path)])

        self.assertEqual(result, 2)
        self.assertIn("fresh supplied corpus", stderr.getvalue())

    def test_candidate_rejects_an_explicit_public_fixture_before_reading_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            corpus_alias = pathlib.Path(directory) / "corpus-alias.json"
            corpus_alias.symlink_to(score.PUBLIC_CORPUS.resolve())
            stderr = io.StringIO()
            with (
                mock.patch.object(
                    score,
                    "_load_corpus",
                    side_effect=AssertionError("public fixture was read"),
                ),
                contextlib.redirect_stderr(stderr),
            ):
                result = score.main(["--corpus", str(corpus_alias), "--candidate", "unused.json"])

        self.assertEqual(result, 2)
        self.assertIn("public regression fixture", stderr.getvalue())

    def test_candidate_rejects_a_public_fixture_hardlink_before_reading_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            corpus_hardlink = pathlib.Path(directory) / "corpus-hardlink.json"
            try:
                corpus_hardlink.hardlink_to(score.PUBLIC_CORPUS.resolve())
            except OSError as error:
                self.skipTest(f"hardlinks unavailable: {error.__class__.__name__}")
            stderr = io.StringIO()
            with (
                mock.patch.object(
                    score,
                    "_load_corpus",
                    side_effect=AssertionError("public fixture was read"),
                ),
                contextlib.redirect_stderr(stderr),
            ):
                result = score.main(
                    ["--corpus", str(corpus_hardlink), "--candidate", "unused.json"]
                )

        self.assertEqual(result, 2)
        self.assertIn("public regression fixture", stderr.getvalue())

    def test_candidate_rejects_malformed_and_unmatched_prediction_records(self) -> None:
        entries = [
            {
                "id": "task-1",
                "task": "hidden",
                "consensus": "low",
                "language": "en",
                "category": "routine",
            },
            {
                "id": "task-2",
                "task": "also hidden",
                "consensus": "low",
                "language": "ko",
                "category": "security",
            },
        ]
        cases: dict[str, tuple[object, str, tuple[str, ...]]] = {
            "not an array": (
                "SEALED-NOT-AN-ARRAY",
                "predictions must be an array",
                ("SEALED-NOT-AN-ARRAY",),
            ),
            "missing record": (
                [self._prediction()],
                "prediction count does not match corpus",
                (),
            ),
            "duplicate id": (
                [self._prediction(), self._prediction()],
                "id is duplicated",
                ("task-1",),
            ),
            "unknown id": (
                [
                    self._prediction(),
                    self._prediction(identifier="task-3", label="high", prediction="high"),
                ],
                "id is unknown",
                ("task-3",),
            ),
            "label mismatch": (
                [
                    self._prediction(label="high"),
                    self._prediction(identifier="task-2", label="high", prediction="high"),
                ],
                "label does not match corpus",
                (),
            ),
            "out of order": (
                [
                    self._prediction(identifier="task-2"),
                    self._prediction(identifier="task-1", prediction="high"),
                ],
                "id is out of order",
                ("task-1", "task-2"),
            ),
            "unsupported tier": (
                [
                    self._prediction(prediction="urgent"),
                    self._prediction(identifier="task-2", label="high", prediction="high"),
                ],
                "prediction must be a supported tier",
                ("urgent",),
            ),
            "unhashable label": (
                [
                    {"id": "task-1", "label": [], "prediction": "low"},
                    self._prediction(identifier="task-2", label="high", prediction="high"),
                ],
                "label does not match corpus",
                (),
            ),
            "unhashable prediction": (
                [
                    {"id": "task-1", "label": "low", "prediction": {}},
                    self._prediction(identifier="task-2", label="high", prediction="high"),
                ],
                "prediction must be a supported tier",
                (),
            ),
            "unknown field": (
                [
                    {**self._prediction(), "task": "must not be accepted"},
                    self._prediction(identifier="task-2", label="high", prediction="high"),
                ],
                "must contain exactly the documented fields",
                ("must not be accepted",),
            ),
        }
        for name, (predictions, expected_error, sensitive_values) in cases.items():
            with self.subTest(name=name):
                candidate = self._complete_candidate(predictions=[])
                candidate["predictions"] = predictions
                result, output = self._run_candidate(entries, candidate)
                self.assertEqual(result, 2)
                self.assertIn(expected_error, output)
                self.assertNotIn("Traceback", output)
                self.assertNotIn("hidden", output)
                for sensitive_value in sensitive_values:
                    self.assertNotIn(sensitive_value, output)

    def test_candidate_rejects_duplicate_corpus_identifiers(self) -> None:
        entries = [
            {
                "id": "same-id",
                "task": "first hidden task",
                "consensus": "low",
                "language": "en",
                "category": "routine",
            },
            {
                "id": "same-id",
                "task": "second hidden task",
                "consensus": "high",
                "language": "ko",
                "category": "security",
            },
        ]
        candidate = self._complete_candidate(predictions=[])

        result, output = self._run_candidate(entries, candidate)

        self.assertEqual(result, 2)
        self.assertIn("id must be unique", output)
        self.assertNotIn("same-id", output)
        self.assertNotIn("hidden task", output)

    def test_candidate_identifier_character_and_length_boundaries(self) -> None:
        entries = [
            {
                "id": "task-1",
                "task": "hidden",
                "consensus": "low",
                "language": "en",
                "category": "routine",
            }
        ]
        valid = self._complete_candidate(predictions=[self._prediction()])
        valid["candidate_id"] = "a" + "." * 63
        valid["baseline_id"] = "b"

        result, output = self._run_candidate(entries, valid)

        self.assertEqual(result, 0)
        self.assertNotIn(str(valid["candidate_id"]), output)

        for identifier in (".leading-punctuation", "a" * 65):
            with self.subTest(identifier_shape=len(identifier)):
                invalid = self._complete_candidate(predictions=[self._prediction()])
                invalid["candidate_id"] = identifier
                result, output = self._run_candidate(entries, invalid)
                self.assertEqual(result, 2)
                self.assertIn("candidate_id", output)
                self.assertNotIn(identifier, output)

    def test_candidate_rejects_incomplete_or_malformed_gate_evidence(self) -> None:
        corpus = [
            {
                "id": "task-1",
                "task": "hidden",
                "consensus": "low",
                "language": "en",
                "category": "routine",
            }
        ]
        cases: dict[str, tuple[dict[str, object], str, tuple[str, ...]]] = {}
        missing_field = self._complete_candidate(predictions=[self._prediction()])
        del missing_field["resource_gate"]
        cases["missing top-level field"] = (
            missing_field,
            "candidate must contain exactly the documented fields",
            (),
        )
        invalid_threshold = self._complete_candidate(predictions=[self._prediction()])
        assert isinstance(invalid_threshold["quality_gate"], dict)
        invalid_threshold["quality_gate"]["over_routing_max"] = float("nan")
        cases["non-finite threshold"] = (
            invalid_threshold,
            "quality_gate over_routing_max must be a finite number",
            (),
        )
        invalid_boolean = self._complete_candidate(predictions=[self._prediction()])
        assert isinstance(invalid_boolean["resource_gate"], dict)
        invalid_boolean["resource_gate"]["startup_accepted"] = 1
        cases["non-boolean evidence"] = (
            invalid_boolean,
            "resource_gate fields must be booleans",
            (),
        )
        invalid_rule = self._complete_candidate(predictions=[self._prediction()])
        assert isinstance(invalid_rule["quality_gate"], dict)
        invalid_rule["quality_gate"]["high_tier_recall_ci_rule"] = "point-estimate"
        cases["unsupported interval rule"] = (
            invalid_rule,
            "high_tier_recall_ci_rule must be lower-bound",
            ("point-estimate",),
        )

        for name, (candidate, expected_error, sensitive_values) in cases.items():
            with self.subTest(name=name):
                result, output = self._run_candidate(corpus, candidate)
                self.assertEqual(result, 2)
                self.assertIn(expected_error, output)
                self.assertNotIn("Traceback", output)
                self.assertNotIn("hidden", output)
                for sensitive_value in sensitive_values:
                    self.assertNotIn(sensitive_value, output)

    def test_candidate_rejects_a_parsed_huge_threshold_without_a_traceback(self) -> None:
        entries = [
            {
                "id": "task-1",
                "task": "hidden",
                "consensus": "low",
                "language": "en",
                "category": "routine",
            }
        ]
        candidate = self._complete_candidate(predictions=[self._prediction()])
        quality = candidate["quality_gate"]
        assert isinstance(quality, dict)
        quality["over_routing_max"] = 10**4000

        result, output = self._run_candidate(entries, candidate)

        self.assertEqual(result, 2)
        self.assertIn("over_routing_max", output)
        self.assertNotIn("Traceback", output)
        self.assertNotIn("hidden", output)

    def test_candidate_rejects_a_json_integer_over_the_parser_limit(self) -> None:
        entries = [
            {
                "id": "task-1",
                "task": "hidden",
                "consensus": "low",
                "language": "en",
                "category": "routine",
            }
        ]
        candidate = self._complete_candidate(predictions=[self._prediction()])
        candidate_json = json.dumps(candidate).replace(
            '"over_routing_max": 0.1',
            f'"over_routing_max": {"9" * 5000}',
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            corpus_path = root / "blind.json"
            candidate_path = root / "candidate.json"
            corpus_path.write_text(json.dumps(entries), encoding="utf-8")
            candidate_path.write_text(candidate_json, encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = score.main(
                    ["--corpus", str(corpus_path), "--candidate", str(candidate_path)]
                )
        output = stdout.getvalue() + stderr.getvalue()

        self.assertEqual(result, 2)
        self.assertIn("could not read candidate", output)
        self.assertNotIn("Traceback", output)
        self.assertNotIn("hidden", output)

    def test_candidate_gate_boundaries_and_no_high_case_fail_closed(self) -> None:
        records = [
            {
                "expected": "high",
                "predicted": "high",
                "language": "en",
                "category": "security",
            }
        ]
        metrics = score.aggregate_metrics(records)
        candidate = self._complete_candidate(
            predictions=[self._prediction(label="high", prediction="high")]
        )
        quality = candidate["quality_gate"]
        assert isinstance(quality, dict)
        quality["high_tier_recall_min"] = metrics["high_recall"]["confidence_interval_95"][0]
        quality["over_routing_max"] = metrics["over_routing"]["confidence_interval_95"][1]

        report = score.candidate_report(
            candidate,
            metrics,
            baseline_metrics=metrics,
            corpus_size=1,
            candidate_below_baseline_count=0,
        )

        self.assertTrue(report["quality_gate"]["high_tier_recall"]["passes"])
        self.assertTrue(report["quality_gate"]["over_routing"]["passes"])
        self.assertEqual(report["decision"], "go")

        no_high_metrics = score.aggregate_metrics(
            [{"expected": "low", "predicted": "low", "language": "en", "category": "routine"}]
        )
        no_high_report = score.candidate_report(
            candidate,
            no_high_metrics,
            baseline_metrics=no_high_metrics,
            corpus_size=1,
            candidate_below_baseline_count=0,
        )
        self.assertFalse(no_high_report["quality_gate"]["high_tier_recall"]["passes"])
        self.assertEqual(no_high_report["decision"], "no-go")

    def test_candidate_below_baseline_fails_the_raise_only_gate(self) -> None:
        metrics = score.aggregate_metrics(
            [{"expected": "high", "predicted": "high", "language": "en", "category": "security"}]
        )
        candidate = self._complete_candidate(
            predictions=[self._prediction(label="high", prediction="high")]
        )
        quality = candidate["quality_gate"]
        assert isinstance(quality, dict)
        quality["high_tier_recall_min"] = 0.0
        quality["over_routing_max"] = 1.0

        report = score.candidate_report(
            candidate,
            metrics,
            baseline_metrics=metrics,
            corpus_size=1,
            candidate_below_baseline_count=1,
        )

        self.assertFalse(report["comparison_gate"]["passes"])
        self.assertEqual(report["comparison_gate"]["candidate_below_baseline_count"], 1)
        self.assertEqual(report["decision"], "no-go")

    def test_candidate_cli_counts_a_prediction_below_the_local_baseline(self) -> None:
        entries = [
            {
                "id": "task-1",
                "task": "security incident",
                "consensus": "low",
                "language": "en",
                "category": "security",
            },
            {
                "id": "task-2",
                "task": "security migration",
                "consensus": "high",
                "language": "ko",
                "category": "migration",
            },
        ]
        candidate = self._complete_candidate(
            predictions=[
                self._prediction(prediction="low"),
                self._prediction(identifier="task-2", label="high", prediction="high"),
            ]
        )
        quality = candidate["quality_gate"]
        assert isinstance(quality, dict)
        quality["high_tier_recall_min"] = 0.0
        quality["over_routing_max"] = 1.0

        result, output = self._run_candidate(entries, candidate)

        report = json.loads(output)
        self.assertEqual(result, 0)
        self.assertEqual(report["comparison_gate"]["candidate_below_baseline_count"], 1)
        self.assertFalse(report["comparison_gate"]["passes"])
        self.assertEqual(report["decision"], "no-go")

    def test_each_resource_and_supply_chain_field_controls_the_decision(self) -> None:
        metrics = score.aggregate_metrics(
            [{"expected": "high", "predicted": "high", "language": "en", "category": "security"}]
        )
        for gate_name, fields in (
            ("resource_gate", score.RESOURCE_FIELDS),
            ("supply_chain_gate", score.SUPPLY_CHAIN_FIELDS),
        ):
            for field in fields:
                with self.subTest(gate=gate_name, field=field):
                    candidate = self._complete_candidate(
                        predictions=[self._prediction(label="high", prediction="high")]
                    )
                    quality = candidate["quality_gate"]
                    assert isinstance(quality, dict)
                    quality["high_tier_recall_min"] = 0.0
                    quality["over_routing_max"] = 1.0
                    gate = candidate[gate_name]
                    assert isinstance(gate, dict)
                    gate[field] = field == "model_download_required"

                    report = score.candidate_report(
                        candidate,
                        metrics,
                        baseline_metrics=metrics,
                        corpus_size=1,
                        candidate_below_baseline_count=0,
                    )

                    self.assertFalse(report[gate_name]["passes"])
                    self.assertEqual(report["decision"], "no-go")

    def test_each_manual_quality_field_controls_the_decision(self) -> None:
        metrics = score.aggregate_metrics(
            [{"expected": "high", "predicted": "high", "language": "en", "category": "security"}]
        )
        for field, failing_value in (
            ("slices_reviewed", False),
            ("unexplained_slice_regression", True),
        ):
            with self.subTest(field=field):
                candidate = self._complete_candidate(
                    predictions=[self._prediction(label="high", prediction="high")]
                )
                quality = candidate["quality_gate"]
                assert isinstance(quality, dict)
                quality["high_tier_recall_min"] = 0.0
                quality["over_routing_max"] = 1.0
                quality[field] = failing_value

                report = score.candidate_report(
                    candidate,
                    metrics,
                    baseline_metrics=metrics,
                    corpus_size=1,
                    candidate_below_baseline_count=0,
                )

                self.assertFalse(report["quality_gate"]["passes"])
                self.assertEqual(report["decision"], "no-go")

    def test_candidate_output_is_stable_and_creates_no_artifacts_or_hashes(self) -> None:
        sentinel = "SENTINEL TASK TEXT MUST STAY TRANSIENT"
        entries = [
            {
                "id": "task-1",
                "task": sentinel,
                "consensus": "low",
                "language": "en",
                "category": "routine",
            }
        ]
        candidate = self._complete_candidate(predictions=[self._prediction()])
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            corpus = root / "blind.json"
            evidence = root / "candidate.json"
            corpus.write_text(json.dumps(entries), encoding="utf-8")
            evidence.write_text(json.dumps(candidate), encoding="utf-8")
            before = sorted(path.name for path in root.iterdir())
            outputs = []
            with (
                mock.patch.object(hashlib, "new", side_effect=AssertionError("task hashed")),
                mock.patch.object(hashlib, "sha256", side_effect=AssertionError("task hashed")),
                mock.patch.object(
                    tempfile, "mkdtemp", side_effect=AssertionError("artifact created")
                ),
                mock.patch.object(
                    tempfile, "mkstemp", side_effect=AssertionError("artifact created")
                ),
                mock.patch.object(
                    tempfile, "NamedTemporaryFile", side_effect=AssertionError("artifact created")
                ),
                mock.patch("subprocess.Popen", side_effect=AssertionError("process invoked")),
            ):
                for _ in range(2):
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                        result = score.main(["--corpus", str(corpus), "--candidate", str(evidence)])
                    self.assertEqual(result, 0)
                    outputs.append((stdout.getvalue(), stderr.getvalue()))

            after = sorted(path.name for path in root.iterdir())

        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(before, after)
        serialized_report = json.dumps(json.loads(outputs[0][0]), sort_keys=True)
        self.assertNotIn(sentinel, outputs[0][0] + outputs[0][1] + serialized_report)
        self.assertNotIn("hash", serialized_report.lower())

    def test_candidate_mode_rejects_ambiguous_comparison_flags(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = score.main(
                ["--corpus", "unused.json", "--candidate", "unused.json", "--compare-triage"]
            )

        self.assertEqual(result, 2)
        self.assertIn("exactly one comparison mode", stderr.getvalue())

    @staticmethod
    def _complete_candidate(*, predictions: list[object]) -> dict[str, object]:
        return {
            "schema_version": 1,
            "candidate_id": "candidate-1",
            "baseline_id": "baseline-1",
            "predictions": predictions,
            "quality_gate": {
                "high_tier_recall_min": 0.9,
                "high_tier_recall_ci_rule": "lower-bound",
                "over_routing_max": 0.1,
                "over_routing_ci_rule": "upper-bound",
                "slices_reviewed": True,
                "unexplained_slice_regression": False,
            },
            "resource_gate": {
                "startup_accepted": True,
                "latency_accepted": True,
                "memory_accepted": True,
                "supported_platform_determinism_accepted": True,
            },
            "supply_chain_gate": {
                "dependency_pin_reviewed": True,
                "dependency_audit_accepted": True,
                "model_download_required": False,
                "maintenance_cost_accepted": True,
            },
        }

    @staticmethod
    def _prediction(
        *, identifier: str = "task-1", label: str = "low", prediction: str = "low"
    ) -> dict[str, str]:
        return {"id": identifier, "label": label, "prediction": prediction}

    @staticmethod
    def _run_candidate(
        entries: Sequence[Mapping[str, object]], candidate: Mapping[str, object]
    ) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as directory:
            corpus = pathlib.Path(directory) / "blind.json"
            candidate_path = pathlib.Path(directory) / "candidate.json"
            corpus.write_text(json.dumps(entries), encoding="utf-8")
            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = score.main(["--corpus", str(corpus), "--candidate", str(candidate_path)])
        return result, stdout.getvalue() + stderr.getvalue()

    def test_supplied_corpus_output_contains_only_aggregate_results(self) -> None:
        secret = "DO NOT PRINT THIS SYNTHETIC TASK"
        entries = [
            {
                "index": 0,
                "task": secret,
                "consensus": "standard",
                "language": "en",
                "category": "reliability",
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            corpus = pathlib.Path(directory) / "blind.json"
            corpus.write_text(json.dumps(entries), encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = score.main(["--corpus", str(corpus)])

        output = stdout.getvalue() + stderr.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("synthetic blind corpus", output)
        self.assertIn("confusion matrix", output)
        self.assertIn("high-tier recall", output)
        self.assertIn("language slices", output)
        self.assertIn("category slices", output)
        self.assertNotIn(secret, output)

    def test_triage_comparison_is_offline_and_reports_all_candidates(self) -> None:
        secret = "SEALED COMPARISON TASK"
        entries = [
            {
                "task": secret,
                "consensus": "high",
                "language": "en",
                "category": "security",
                "vendor_tier": "high",
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            corpus = pathlib.Path(directory) / "blind.json"
            corpus.write_text(json.dumps(entries), encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch("subprocess.Popen", side_effect=AssertionError("vendor invoked")),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                result = score.main(["--corpus", str(corpus), "--compare-triage"])

        output = stdout.getvalue() + stderr.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("local classifier", output)
        self.assertIn("vendor-only experiment", output)
        self.assertIn("raise-only experiment", output)
        self.assertNotIn(secret, output)

    def test_triage_comparison_rejects_the_public_fixture(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = score.main(["--compare-triage"])

        self.assertEqual(result, 2)
        self.assertIn("fresh supplied corpus", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
