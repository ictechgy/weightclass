import contextlib
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest
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


class OfflineOutputTests(unittest.TestCase):
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
