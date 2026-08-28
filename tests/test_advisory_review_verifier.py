from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERIFIER = ROOT / ".weightclass" / "verify-review"
REPOSITORY_VERIFIER_AVAILABLE = VERIFIER.is_file()


def finding(
    title: str,
    severity: str,
    disposition: str,
    location: str,
    evidence: str,
) -> dict[str, object]:
    return {
        "title": title,
        "severity": severity,
        "confidence": "high",
        "disposition": disposition,
        "locations": [location],
        "evidence": [f"{location}: {evidence}"],
        "counterevidence": ["The existing controls narrow but do not erase the stated boundary."],
        "recommendation": "Retain the control and verify the smallest compatible hardening option.",
    }


def accepted_result() -> dict[str, object]:
    return {
        "schema_version": 1,
        "mode": "review",
        "summary": (
            "The failure-receipt privacy, reliability, classification, and "
            "measurement controls were checked."
        ),
        "findings": [
            finding(
                "[seed:receipt-privacy] Closed receipt schema excludes untrusted text",
                "info",
                "suppressed",
                "src/weightclass/advisory/speculative_run.py:303",
                "failure_receipt selects only reviewed enums and bounded scalar values.",
            ),
            finding(
                "[seed:emission-reliability] Receipt emission is an operator output boundary",
                "info",
                "suppressed",
                "src/weightclass/advisory/speculative_run.py:352",
                "emit_failure_receipt serializes only the closed receipt and writes it to stderr.",
            ),
            finding(
                "[seed:verification-classification] Ordinary verifier failures are explicit",
                "info",
                "suppressed",
                "src/weightclass/advisory/speculative_run.py:4282",
                "failure_kind and failure_stage are fixed before cleanup when verification fails.",
            ),
            finding(
                "[seed:measurement-compatibility] Rendering does not force "
                "fallback values into records",
                "info",
                "suppressed",
                "src/weightclass/advisory/speculative_run.py:4373",
                "emit_failure_receipt reads fallback values without mutating the stored attempt.",
            ),
        ],
        "limitations": ["Provider-specific stderr transport behavior was not measured."],
    }


@unittest.skipUnless(REPOSITORY_VERIFIER_AVAILABLE, "repository-only review verifier unavailable")
class AdvisoryReviewVerifierTests(unittest.TestCase):
    def run_verifier(self, value: dict[str, object]) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["WCLASS_ADVISORY_WORKFLOW"] = "review"
        return subprocess.run(
            [str(VERIFIER)],
            cwd=ROOT,
            input=json.dumps(value),
            capture_output=True,
            check=False,
            text=True,
            env=environment,
        )

    def test_accepts_seeded_failure_receipt_controls(self) -> None:
        self.assertEqual(self.run_verifier(accepted_result()).returncode, 0)

    def test_baseline_probe_returns_42(self) -> None:
        probe = {
            "schema_version": 1,
            "mode": "review",
            "summary": "Prospective baseline probe; no task was evaluated.",
            "findings": [],
            "limitations": ["baseline_probe"],
        }
        self.assertEqual(self.run_verifier(probe).returncode, 42)

    def test_missing_seed_or_unsupported_new_finding_is_rejected(self) -> None:
        missing = accepted_result()
        findings = missing["findings"]
        assert isinstance(findings, list)
        findings.pop()
        self.assertEqual(self.run_verifier(missing).returncode, 1)

        unsupported = accepted_result()
        findings = unsupported["findings"]
        assert isinstance(findings, list)
        added = finding(
            "[new] Unsupported claim",
            "high",
            "reportable",
            "src/weightclass/advisory/advisory_parallel.py:212",
            "This cites the location but omits counterevidence.",
        )
        added["counterevidence"] = []
        findings.append(added)
        self.assertEqual(self.run_verifier(unsupported).returncode, 1)

    def test_untracked_or_uncited_location_is_rejected(self) -> None:
        for location in (
            "missing.py:1",
            "../HANDOFF.md:1",
            "src/weightclass/advisory/speculative_run.py:336",
            "src/weightclass/advisory/speculative_run.py:4279",
        ):
            with self.subTest(location=location):
                value = accepted_result()
                findings = value["findings"]
                assert isinstance(findings, list)
                first = findings[0]
                assert isinstance(first, dict)
                first["locations"] = [location]
                self.assertEqual(self.run_verifier(value).returncode, 1)

    def test_empty_limitations_or_duplicate_titles_are_rejected(self) -> None:
        no_limits = accepted_result()
        no_limits["limitations"] = []
        self.assertEqual(self.run_verifier(no_limits).returncode, 1)

        duplicate = accepted_result()
        findings = duplicate["findings"]
        assert isinstance(findings, list)
        findings.append(dict(findings[-1]))
        self.assertEqual(self.run_verifier(duplicate).returncode, 1)


if __name__ == "__main__":
    unittest.main()
