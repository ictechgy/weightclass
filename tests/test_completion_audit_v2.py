from __future__ import annotations

import re
import unittest
from pathlib import Path

REQUIRED_ROWS = {
    *(f"OBJ-{number:02d}" for number in range(1, 13)),
    "CONTRACT-VERSIONING",
    "CONTRACT-IMMUTABLE-TRUTH",
    "CONTRACT-NATIVE",
    "CONTRACT-TASK-BYTES",
    "CONTRACT-EXECUTABLE",
    "CONTRACT-DELEGATION",
    "CONTRACT-PROJECTIONS",
    "CONTRACT-WCD2",
    "CONTRACT-BOUNDS",
    "EVIDENCE-GUARDED-RUNTIME",
    "EVIDENCE-TRACEABILITY",
    "EVIDENCE-DISTRIBUTION",
    "EVIDENCE-CI-RELEASE",
    "AUDIT-FORBIDDEN-BEHAVIOR",
    "AUDIT-REPOSITORY-HYGIENE",
}


class CompletionAuditV2Tests(unittest.TestCase):
    path: Path
    text: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.path = Path("docs/completion-audit-v2.md")
        cls.text = cls.path.read_text(encoding="utf-8")

    def test_every_required_row_has_files_tests_commands_evidence_and_status(self) -> None:
        rows: dict[str, list[str]] = {}
        for line in self.text.splitlines():
            if re.match(r"^\| (?:OBJ|CONTRACT|EVIDENCE|AUDIT)-", line):
                cells = [cell.strip() for cell in line.strip("|").split("|")]
                rows[cells[0]] = cells
        self.assertEqual(set(rows), REQUIRED_ROWS)
        for row_id, cells in rows.items():
            with self.subTest(row=row_id):
                self.assertEqual(len(cells), 6)
                self.assertTrue(all(cells[1:5]))
                self.assertEqual(cells[5], "current")

    def test_audit_records_non_goals_and_does_not_claim_ralplan_consensus(self) -> None:
        for phrase in (
            "no task persistence or task hashes",
            "no credential access",
            "no provider HTTP calls",
            "no retries or fallback",
            "no authentication or profile overrides",
            "no protocol-1 lifecycle migration",
            "no unsupported orchestration claims",
            "RALPLAN: `max_rounds/ITERATE` (not consensus-approved)",
            "No commit, push, tag, release, deployment, or publication was performed",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.text)

    def test_current_audit_contains_no_pending_or_placeholder_evidence(self) -> None:
        lowered = self.text.casefold()
        for forbidden in (
            "awaiting independent leader verification",
            "leader verification pending",
            "manual current-diff audit required",
            "forbidden behavior terms",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lowered)

    def test_unittest_reproduction_commands_work_from_a_src_layout_checkout(self) -> None:
        for line in self.text.splitlines():
            if not re.match(r"^\| (?:OBJ|CONTRACT|EVIDENCE|AUDIT)-", line):
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            command = cells[3]
            if " -m unittest" in command:
                with self.subTest(row=cells[0]):
                    self.assertRegex(
                        command,
                        r"^`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "
                        r"python3\.(?:10|13|14) -m unittest",
                    )

    def test_handoff_points_to_current_g12_audit(self) -> None:
        handoff_path = Path("HANDOFF.md")
        if not handoff_path.is_file():
            self.skipTest("HANDOFF.md is intentionally absent from the sdist")
        handoff = handoff_path.read_text(encoding="utf-8")
        self.assertIn("Goal g12 is leader-verified", handoff)
        self.assertIn("docs/completion-audit-v2.md", handoff)


if __name__ == "__main__":
    unittest.main()
