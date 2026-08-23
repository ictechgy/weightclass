from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERIFIER = ROOT / ".weightclass" / "verify-review"


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
        "summary": "The seeded controls and residual risks were checked against tracked source.",
        "findings": [
            finding(
                "[seed:exec-path] Executable path replacement residual",
                "medium",
                "deferred",
                "src/weightclass/foreground_process.py:262",
                "subprocess.Popen still resolves the observed path at spawn time.",
            ),
            finding(
                "[seed:usage-store] Custom usage store ancestor race",
                "low",
                "deferred",
                "src/weightclass/usage_aggregation.py:424",
                "os.replace is pathname based rather than a dirfd transaction.",
            ),
            finding(
                "[seed:parallel-shell] Parallel dispatcher shell-injection control",
                "info",
                "suppressed",
                "tools/advisory_parallel.py:70",
                "subprocess.run receives an exact tuple and never enables a shell.",
            ),
        ],
        "limitations": ["Runtime compatibility of deferred hardening options was not measured."],
    }


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

    def test_accepts_seeded_residuals_and_suppressed_control(self) -> None:
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
            "tools/advisory_parallel.py:70",
            "This cites the location but omits counterevidence.",
        )
        added["counterevidence"] = []
        findings.append(added)
        self.assertEqual(self.run_verifier(unsupported).returncode, 1)

    def test_untracked_or_uncited_location_is_rejected(self) -> None:
        for location in ("missing.py:1", "tools/advisory_parallel.py:70"):
            with self.subTest(location=location):
                value = accepted_result()
                findings = value["findings"]
                assert isinstance(findings, list)
                first = findings[0]
                assert isinstance(first, dict)
                first["locations"] = [location]
                self.assertEqual(self.run_verifier(value).returncode, 1)


if __name__ == "__main__":
    unittest.main()
