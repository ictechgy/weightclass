from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest import mock

from weightclass.advisory import advisory_triage, readonly_snapshot


def _finding(
    title: str,
    severity: str,
    location: str,
    evidence: str,
) -> dict[str, object]:
    return {
        "title": title,
        "severity": severity,
        "confidence": "high",
        "disposition": "reportable",
        "locations": [location],
        "evidence": [evidence],
        "counterevidence": [],
        "recommendation": "fix",
    }


class AdvisoryTriageTests(unittest.TestCase):
    def test_file_line_validation_is_structural_and_non_filtering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
            findings = [
                _finding("Cited issue", "high", "source.py:2", "source.py:2 proves it"),
                _finding("Uncited issue", "medium", "source.py:3", "generic evidence"),
                _finding("Invalid issue", "low", "../secret:1", "../secret:1"),
                _finding("Past EOF", "low", "source.py:99", "source.py:99"),
            ]
            result = {"findings": findings}
            snapshot = readonly_snapshot.snapshot_tree(root)
            triage = advisory_triage.triage_review(result, root, snapshot)
        annotations = cast(list[dict[str, object]], triage["annotations"])
        self.assertEqual(
            [annotation["triage"] for annotation in annotations],
            ["confirmed", "debatable", "rejected", "rejected"],
        )
        self.assertEqual(len(findings), 4)
        self.assertFalse(triage["persistent_state_written"])

    def test_semantic_duplicates_reraise_higher_severity_without_muting_critical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.py").write_text("one\n", encoding="utf-8")
            result = {
                "findings": [
                    _finding("Unsafe request validation", "low", "source.py:1", "source.py:1"),
                    _finding("Unsafe request validation", "high", "source.py:1", "source.py:1"),
                    _finding("Unsafe request validation", "critical", "source.py:1", "source.py:1"),
                ]
            }
            snapshot = readonly_snapshot.snapshot_tree(root)
            triage = advisory_triage.triage_review(result, root, snapshot)
        annotations = cast(list[dict[str, object]], triage["annotations"])
        self.assertTrue(annotations[2]["severity_reraised"])
        self.assertFalse(annotations[2]["duplicate_muted"])
        self.assertTrue(annotations[0]["duplicate_muted"])
        self.assertTrue(annotations[1]["duplicate_muted"])
        groups = cast(list[dict[str, object]], triage["groups"])
        self.assertEqual(groups[0]["member_indices"], [0, 1, 2])

    def test_distinct_lines_are_never_muted_by_similar_titles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.py").write_text("read\ndelete\n", encoding="utf-8")
            result = {
                "findings": [
                    _finding("Unsafe tenant operation", "high", "source.py:1", "source.py:1"),
                    _finding("Unsafe tenant operations", "high", "source.py:2", "source.py:2"),
                ]
            }
            snapshot = readonly_snapshot.snapshot_tree(root)
            triage = advisory_triage.triage_review(result, root, snapshot)
        annotations = cast(list[dict[str, object]], triage["annotations"])
        self.assertFalse(annotations[0]["duplicate_muted"])
        self.assertFalse(annotations[1]["duplicate_muted"])
        self.assertNotEqual(annotations[0]["semantic_id"], annotations[1]["semantic_id"])

    def test_total_file_validation_io_is_capped_before_each_read(self) -> None:
        locations = [f"file-{index}.py:1" for index in range(10)]
        finding = _finding("Many locations", "medium", locations[0], locations[0])
        finding["locations"] = locations
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b"x" * advisory_triage.MAX_TRIAGE_FILE_BYTES
            for index in range(10):
                (root / f"file-{index}.py").write_bytes(payload)
            snapshot = readonly_snapshot.snapshot_tree(root)
            with mock.patch(
                "weightclass.advisory.advisory_triage.advisory_context.read_relative_regular",
                return_value=payload,
            ) as read:
                triage = advisory_triage.triage_review({"findings": [finding]}, root, snapshot)
        expected_reads = (
            advisory_triage.MAX_TRIAGE_TOTAL_BYTES // advisory_triage.MAX_TRIAGE_FILE_BYTES
        )
        self.assertEqual(read.call_count, expected_reads)
        annotations = cast(list[dict[str, object]], triage["annotations"])
        self.assertEqual(annotations[0]["triage"], "rejected")

    def test_location_validation_is_bound_to_the_pre_execution_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = root / "source.py"
            selected.write_text("before\n", encoding="utf-8")
            snapshot = readonly_snapshot.snapshot_tree(root)
            selected.write_text("after!\n", encoding="utf-8")
            result = {"findings": [_finding("Changed file", "high", "source.py:1", "source.py:1")]}
            triage = advisory_triage.triage_review(result, root, snapshot)
        annotations = cast(list[dict[str, object]], triage["annotations"])
        self.assertEqual(annotations[0]["triage"], "rejected")
        self.assertEqual(annotations[0]["reason"], "unreadable_location")

    def test_council_preserves_consensus_and_dissent_without_selection(self) -> None:
        members = [
            {
                "result": {
                    "findings": [
                        _finding("Unsafe request validation", "high", "a.py:1", "a.py:1"),
                        _finding("Only first advisor", "low", "a.py:2", "a.py:2"),
                    ]
                }
            },
            {
                "result": {
                    "findings": [
                        _finding("Unsafe validation of requests", "medium", "a.py:1", "a.py:1")
                    ]
                }
            },
        ]
        synthesis = advisory_triage.council_synthesis("review", members)
        self.assertFalse(synthesis["selection_performed"])
        self.assertTrue(synthesis["dissent_preserved"])
        self.assertEqual(synthesis["consensus_count"], 1)
        self.assertEqual(synthesis["dissent_count"], 1)

    def test_council_does_not_claim_consensus_for_different_locations(self) -> None:
        members = [
            {"result": {"findings": [_finding("Unsafe access", "high", "a.py:1", "a.py:1 bad")]}},
            {"result": {"findings": [_finding("Unsafe access", "high", "b.py:1", "b.py:1 bad")]}},
        ]
        synthesis = advisory_triage.council_synthesis("review", members)
        self.assertEqual(synthesis["consensus_count"], 0)
        self.assertEqual(synthesis["dissent_count"], 2)

    def test_location_citation_requires_boundaries_and_supporting_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.py").write_text("one\n", encoding="utf-8")
            snapshot = readonly_snapshot.snapshot_tree(root)
            result = {
                "findings": [
                    _finding("Bare", "low", "a.py:1", "a.py:1"),
                    _finding("Substring", "low", "a.py:1", "nota.py:12 is unrelated"),
                    _finding("Supported", "low", "a.py:1", "a.py:1 shows the unsafe call"),
                ]
            }
            triage = advisory_triage.triage_review(result, root, snapshot)
        annotations = cast(list[dict[str, object]], triage["annotations"])
        self.assertEqual(
            [annotation["triage"] for annotation in annotations],
            ["debatable", "debatable", "confirmed"],
        )


if __name__ == "__main__":
    unittest.main()
