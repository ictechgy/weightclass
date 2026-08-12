import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from typing import Any
from unittest import mock

BENCHMARK_PATH = pathlib.Path(__file__).parent / "eval" / "token_benchmark.py"
SPEC = importlib.util.spec_from_file_location("eval_token_benchmark", BENCHMARK_PATH)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)

BASELINE_FINGERPRINT = "sha256:" + "a" * 64
CANDIDATE_FINGERPRINT = "sha256:" + "b" * 64
TEST_CATEGORIES = (
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
TEST_TIERS = ("low", "standard", "high")


def _arm(
    net_tokens: int,
    *,
    invocations: int = 1,
    completed: bool = True,
    quality_pass: bool = True,
    critical_failure: bool = False,
) -> dict[str, Any]:
    return {
        "net_tokens": net_tokens,
        "invocations": invocations,
        "completed": completed,
        "quality_pass": quality_pass,
        "critical_failure": critical_failure,
    }


def _evidence() -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "baseline_id": "direct-default-v1",
        "candidate_id": "local-efficient-v1",
        "measurement_contract_id": "opaque-token-contract-v1",
        "baseline_configuration_fingerprint": BASELINE_FINGERPRINT,
        "candidate_configuration_fingerprint": CANDIDATE_FINGERPRINT,
        "gate": {
            "minimum_pairs": 30,
            "minimum_net_token_savings": 0.15,
            "maximum_savings_ci_width": 0.8,
            "quality_noninferiority_margin": 0.1,
            "savings_ci_rule": "lower-bound",
            "quality_ci_rule": "lower-bound",
            "required_languages": ["en", "ko"],
            "required_categories": ["security", "routine"],
        },
        "provenance": {
            "fresh_blind_tasks": True,
            "same_sealed_tasks": True,
            "same_provider_runtime_model": True,
            "counterbalanced_order": True,
            "all_attempts_included": True,
            "ids_not_task_derived": True,
            "outside_repository_custody": True,
            "independent_quality_review": True,
        },
        "pairs": [],
    }
    for index in range(30):
        baseline_quality = index >= 15
        candidate_quality = True
        evidence["pairs"].append(
            {
                "id": f"sealed-case-{index + 1:03d}",
                "language": "en" if index % 2 == 0 else "ko",
                "category": TEST_CATEGORIES[index % len(TEST_CATEGORIES)],
                "expected_tier": TEST_TIERS[index % len(TEST_TIERS)],
                "baseline": _arm(
                    100,
                    invocations=2 if index == 0 else 1,
                    quality_pass=baseline_quality,
                ),
                "candidate": _arm(
                    60 if index % 2 == 0 else 70,
                    invocations=3 if index == 0 else 1,
                    quality_pass=candidate_quality,
                ),
            }
        )
    return evidence


def _two_pair_evidence() -> dict[str, Any]:
    evidence = _evidence()
    evidence["gate"]["minimum_pairs"] = 2
    evidence["pairs"] = evidence["pairs"][:2]
    return evidence


def _run_evidence(payload: object, *, raw: bool = False) -> tuple[int, str, str]:
    with tempfile.TemporaryDirectory() as directory:
        evidence_path = pathlib.Path(directory) / "evidence.json"
        if raw:
            assert isinstance(payload, str)
            evidence_path.write_text(payload, encoding="utf-8")
        else:
            evidence_path.write_text(json.dumps(payload), encoding="utf-8")
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = benchmark.main(["--evidence", str(evidence_path)])
        return result, stdout.getvalue(), stderr.getvalue()


class TokenBenchmarkReportTests(unittest.TestCase):
    def test_valid_go_is_sorted_aggregate_only_and_emits_review_binding(self) -> None:
        result, stdout, stderr = _run_evidence(_evidence())

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        report = json.loads(stdout)
        self.assertEqual(report["decision"], "go")
        self.assertEqual(report["samples"]["pairs"], 30)
        self.assertEqual(report["samples"]["language_coverage"], {"en": 15, "ko": 15})
        self.assertEqual(
            report["samples"]["tier_coverage"],
            {"high": 10, "low": 10, "standard": 10},
        )
        self.assertEqual(report["baseline"]["net_tokens"], 3000)
        self.assertEqual(report["candidate"]["net_tokens"], 1950)
        self.assertEqual(report["baseline"]["invocations"], 31)
        self.assertEqual(report["candidate"]["invocations"], 32)
        self.assertEqual(report["savings"]["estimate"], 0.35)
        self.assertTrue(report["gates"]["passes"])
        self.assertEqual(
            report["binding"],
            {
                "baseline_id": "direct-default-v1",
                "baseline_configuration_fingerprint": BASELINE_FINGERPRINT,
                "candidate_id": "local-efficient-v1",
                "candidate_configuration_fingerprint": CANDIDATE_FINGERPRINT,
                "measurement_contract_id": "opaque-token-contract-v1",
            },
        )
        self.assertNotIn("sealed-case-001", stdout)
        self.assertNotIn("pairs", report)
        self.assertFalse(report["privacy"]["task_content_emitted"])
        self.assertFalse(report["privacy"]["task_hashes_emitted"])
        self.assertFalse(report["privacy"]["pair_identifiers_emitted"])
        self.assertEqual(
            stdout,
            json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n",
        )

    def test_binding_changes_when_reviewed_configuration_changes(self) -> None:
        _, original_stdout, _ = _run_evidence(_evidence())
        original_binding = json.loads(original_stdout)["binding"]
        mutations: tuple[tuple[str, str], ...] = (
            ("baseline_id", "direct-default-v2"),
            ("candidate_id", "local-efficient-v2"),
            ("measurement_contract_id", "opaque-token-contract-v2"),
            ("baseline_configuration_fingerprint", "sha256:" + "c" * 64),
            ("candidate_configuration_fingerprint", "sha256:" + "d" * 64),
        )

        for field, changed_value in mutations:
            with self.subTest(field=field):
                evidence = _evidence()
                evidence[field] = changed_value
                result, stdout, stderr = _run_evidence(evidence)
                self.assertEqual((result, stderr), (0, ""))
                changed_binding = json.loads(stdout)["binding"]
                self.assertEqual(changed_binding[field], changed_value)
                self.assertNotEqual(changed_binding, original_binding)

    def test_structurally_valid_two_pair_evidence_is_no_go(self) -> None:
        result, stdout, stderr = _run_evidence(_two_pair_evidence())
        report = json.loads(stdout)

        self.assertEqual((result, stderr), (0, ""))
        self.assertEqual(report["decision"], "no-go")
        self.assertEqual(report["gates"]["minimum_pairs"]["requested"], 2)
        self.assertEqual(report["gates"]["minimum_pairs"]["built_in_minimum"], 30)
        self.assertEqual(report["gates"]["minimum_pairs"]["required"], 30)
        self.assertFalse(report["gates"]["minimum_pairs"]["passes"])

    def test_requested_thresholds_cannot_weaken_promotion_floors(self) -> None:
        weak_savings = _evidence()
        weak_savings["gate"]["minimum_net_token_savings"] = 0.0
        for index, pair in enumerate(weak_savings["pairs"]):
            pair["candidate"]["net_tokens"] = 85 if index % 2 == 0 else 95

        weak_quality = _evidence()
        weak_quality["gate"]["quality_noninferiority_margin"] = 1.0
        for index, pair in enumerate(weak_quality["pairs"]):
            pair["baseline"]["quality_pass"] = index != 0
            pair["candidate"]["quality_pass"] = index not in (1, 2, 3)

        for name, evidence, gate_name in (
            ("savings", weak_savings, "savings"),
            ("quality", weak_quality, "quality"),
        ):
            with self.subTest(name=name):
                result, stdout, stderr = _run_evidence(evidence)
                report = json.loads(stdout)
                self.assertEqual((result, stderr), (0, ""))
                self.assertEqual(report["decision"], "no-go")
                self.assertFalse(report["gates"][gate_name]["passes"])

        savings_gate = json.loads(_run_evidence(weak_savings)[1])["gates"]["savings"]
        self.assertEqual(savings_gate["requested_minimum"], 0.0)
        self.assertEqual(savings_gate["built_in_minimum"], 0.15)
        self.assertEqual(savings_gate["effective_minimum"], 0.15)
        quality_gate = json.loads(_run_evidence(weak_quality)[1])["gates"]["quality"]
        self.assertEqual(quality_gate["requested_maximum_margin"], 1.0)
        self.assertEqual(quality_gate["built_in_maximum_margin"], 0.05)
        self.assertEqual(quality_gate["effective_maximum_margin"], 0.05)

    def test_builtin_slice_coverage_cannot_be_weakened_by_requested_lists(self) -> None:
        evidence = _evidence()
        evidence["gate"]["required_languages"] = ["en"]
        evidence["gate"]["required_categories"] = ["security"]
        for pair in evidence["pairs"]:
            pair["category"] = "security"
            pair["expected_tier"] = "high"

        result, stdout, stderr = _run_evidence(evidence)
        report = json.loads(stdout)

        self.assertEqual((result, stderr), (0, ""))
        self.assertEqual(report["decision"], "no-go")
        coverage_gate = report["gates"]["coverage"]
        self.assertFalse(coverage_gate["passes"])
        self.assertEqual(coverage_gate["requested_languages"], ["en"])
        self.assertEqual(coverage_gate["built_in_languages"], ["en", "ko"])
        self.assertEqual(coverage_gate["effective_languages"], ["en", "ko"])
        self.assertEqual(coverage_gate["built_in_tiers"], ["high", "low", "standard"])
        self.assertEqual(coverage_gate["effective_tiers"], ["high", "low", "standard"])
        self.assertEqual(coverage_gate["built_in_categories"], list(TEST_CATEGORIES))
        self.assertEqual(coverage_gate["effective_categories"], list(TEST_CATEGORIES))

    def test_valid_no_go_reports_each_failed_gate_without_failing_cli(self) -> None:
        cases: dict[str, tuple[str, Any]] = {}

        evidence = _evidence()
        for pair in evidence["pairs"]:
            pair["candidate"]["net_tokens"] = 100
        cases["savings"] = ("savings", evidence)

        evidence = _evidence()
        for pair in evidence["pairs"]:
            pair["candidate"]["quality_pass"] = False
        cases["quality"] = ("quality", evidence)

        evidence = _evidence()
        evidence["pairs"][0]["candidate"]["completed"] = False
        evidence["pairs"][0]["candidate"]["quality_pass"] = False
        cases["completion"] = ("completion", evidence)

        evidence = _evidence()
        evidence["pairs"][0]["candidate"]["critical_failure"] = True
        cases["critical"] = ("no_new_critical_failures", evidence)

        evidence = _evidence()
        evidence["provenance"]["counterbalanced_order"] = False
        cases["provenance"] = ("provenance", evidence)

        evidence = _evidence()
        evidence["gate"]["required_languages"] = ["en"]
        evidence["gate"]["required_categories"] = ["security"]
        evidence["gate"]["minimum_pairs"] = 31
        cases["minimum_pairs_and_coverage"] = ("minimum_pairs", evidence)

        evidence = _evidence()
        evidence["gate"]["required_categories"] = ["privacy"]
        for pair in evidence["pairs"]:
            if pair["category"] == "privacy":
                pair["category"] = "security"
        cases["coverage"] = ("coverage", evidence)

        evidence = _evidence()
        evidence["gate"]["maximum_savings_ci_width"] = 0.000001
        cases["ci_width"] = ("savings", evidence)

        for name, (gate_name, payload) in cases.items():
            with self.subTest(name=name):
                result, stdout, stderr = _run_evidence(payload)
                report = json.loads(stdout)
                self.assertEqual(result, 0)
                self.assertEqual(stderr, "")
                self.assertEqual(report["decision"], "no-go")
                self.assertFalse(report["gates"][gate_name]["passes"])
                self.assertFalse(report["gates"]["passes"])

    def test_degenerate_savings_evidence_is_no_go(self) -> None:
        savings_evidence = _evidence()
        for pair in savings_evidence["pairs"]:
            pair["candidate"]["net_tokens"] = 60

        result, stdout, stderr = _run_evidence(savings_evidence)
        report = json.loads(stdout)

        self.assertEqual((result, stderr), (0, ""))
        self.assertEqual(report["decision"], "no-go")
        self.assertFalse(report["gates"]["interval_sufficiency"]["passes"])

    def test_thirty_perfectly_matched_quality_pairs_remain_insufficient(self) -> None:
        evidence = _evidence()
        for pair in evidence["pairs"]:
            pair["baseline"]["quality_pass"] = True
            pair["candidate"]["quality_pass"] = True

        result, stdout, stderr = _run_evidence(evidence)
        report = json.loads(stdout)

        self.assertEqual((result, stderr), (0, ""))
        self.assertEqual(report["decision"], "no-go")
        self.assertLess(report["quality"]["confidence_interval_95"][0], -0.05)
        self.assertFalse(report["gates"]["quality"]["passes"])

    def test_enough_perfectly_matched_quality_pairs_can_prove_noninferiority(self) -> None:
        evidence = _evidence()
        templates = evidence["pairs"]
        evidence["pairs"] = []
        for index in range(72):
            pair = copy.deepcopy(templates[index % len(templates)])
            pair["id"] = f"sealed-case-{index + 1:03d}"
            pair["baseline"]["quality_pass"] = True
            pair["candidate"]["quality_pass"] = True
            evidence["pairs"].append(pair)

        result, stdout, stderr = _run_evidence(evidence)
        report = json.loads(stdout)

        self.assertEqual((result, stderr), (0, ""))
        self.assertEqual(report["decision"], "go")
        self.assertGreaterEqual(report["quality"]["confidence_interval_95"][0], -0.05)
        self.assertTrue(report["gates"]["quality"]["passes"])

    def test_promotion_uses_conservative_30_pair_critical_value(self) -> None:
        evidence = _evidence()
        evidence["gate"]["minimum_net_token_savings"] = 0.3315

        result, stdout, stderr = _run_evidence(evidence)
        report = json.loads(stdout)

        self.assertEqual((result, stderr), (0, ""))
        self.assertEqual(report["decision"], "no-go")
        self.assertLess(report["gates"]["savings"]["lower_bound"], 0.3315)

    def test_retries_and_all_net_tokens_are_included_in_aggregate_totals(self) -> None:
        evidence = _two_pair_evidence()
        evidence["pairs"][0]["baseline"] = _arm(11, invocations=5)
        evidence["pairs"][0]["candidate"] = _arm(7, invocations=4)
        evidence["pairs"][1]["baseline"] = _arm(13, invocations=6)
        evidence["pairs"][1]["candidate"] = _arm(9, invocations=8)

        result, stdout, _ = _run_evidence(evidence)
        report = json.loads(stdout)
        self.assertEqual(result, 0)
        self.assertEqual(report["baseline"]["net_tokens"], 24)
        self.assertEqual(report["candidate"]["net_tokens"], 16)
        self.assertEqual(report["baseline"]["invocations"], 11)
        self.assertEqual(report["candidate"]["invocations"], 12)


class TokenBenchmarkValidationTests(unittest.TestCase):
    def test_rejects_missing_unknown_and_duplicate_fields_or_pair_ids(self) -> None:
        mutations: tuple[Callable[[dict[str, Any]], object], ...] = (
            lambda value: value.pop("gate"),
            lambda value: value.update(unexpected="sealed-value"),
            lambda value: value["pairs"][0].update(unexpected="sealed-value"),
            lambda value: value["pairs"].append(copy.deepcopy(value["pairs"][0])),
        )
        for mutation in mutations:
            evidence = _evidence()
            mutation(evidence)
            result, stdout, stderr = _run_evidence(evidence)
            self.assertEqual(result, 2)
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, "invalid evidence\n")
            self.assertNotIn("sealed-value", stderr)
            self.assertNotIn("sealed-case-001", stderr)

    def test_rejects_invalid_numbers_booleans_and_nan_without_echoing_values(self) -> None:
        mutations: tuple[Callable[[dict[str, Any]], object], ...] = (
            lambda value: value["gate"].update(minimum_pairs=True),
            lambda value: value["gate"].update(minimum_net_token_savings=float("nan")),
            lambda value: value["gate"].update(maximum_savings_ci_width=float("inf")),
            lambda value: value["pairs"][0]["baseline"].update(net_tokens=-1),
            lambda value: value["pairs"][0]["candidate"].update(invocations=0),
            lambda value: value["pairs"][0]["candidate"].update(completed=False),
        )
        for mutation in mutations:
            evidence = _evidence()
            mutation(evidence)
            result, stdout, stderr = _run_evidence(evidence)
            self.assertEqual(result, 2)
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, "invalid evidence\n")

    def test_rejects_invalid_identifiers_and_schema_values_without_echoing_them(self) -> None:
        mutations: tuple[Callable[[dict[str, Any]], object], ...] = (
            lambda value: value.update(baseline_id="BAD ID WITH SECRET"),
            lambda value: value.update(candidate_id=value["baseline_id"]),
            lambda value: value.update(baseline_configuration_fingerprint="sha256:short"),
            lambda value: value.update(candidate_configuration_fingerprint="SHA256:" + "b" * 64),
            lambda value: value["pairs"][0].update(id="task derived secret"),
            lambda value: value["pairs"][0].update(language="fr"),
            lambda value: value["pairs"][0].update(category="unknown"),
            lambda value: value["pairs"][0].update(expected_tier="urgent"),
            lambda value: value["gate"].update(required_languages=[]),
            lambda value: value["gate"].update(required_categories=["security", "security"]),
        )
        for mutation in mutations:
            evidence = _evidence()
            mutation(evidence)
            result, stdout, stderr = _run_evidence(evidence)
            self.assertEqual(result, 2)
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, "invalid evidence\n")

    def test_rejects_identical_configuration_fingerprints(self) -> None:
        evidence = _evidence()
        evidence["candidate_configuration_fingerprint"] = evidence[
            "baseline_configuration_fingerprint"
        ]

        result, stdout, stderr = _run_evidence(evidence)

        self.assertEqual((result, stdout, stderr), (2, "", "invalid evidence\n"))
        self.assertNotIn(BASELINE_FINGERPRINT, stderr)

    def test_rejects_missing_pair_fields_and_too_many_pairs(self) -> None:
        evidence = _evidence()
        evidence["pairs"][0].pop("baseline")
        result, stdout, stderr = _run_evidence(evidence)
        self.assertEqual((result, stdout, stderr), (2, "", "invalid evidence\n"))

        evidence = _evidence()
        evidence["pairs"] = evidence["pairs"] * 5001
        result, stdout, stderr = _run_evidence(evidence)
        self.assertEqual((result, stdout, stderr), (2, "", "invalid evidence\n"))

    def test_duplicate_json_field_is_rejected_with_value_free_diagnostic(self) -> None:
        raw = '{"schema_version":1,"schema_version":1,"secret":"DO_NOT_ECHO"}'

        result, stdout, stderr = _run_evidence(raw, raw=True)

        self.assertEqual(result, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "invalid evidence\n")
        self.assertNotIn("schema_version", stderr)
        self.assertNotIn("DO_NOT_ECHO", stderr)

    def test_huge_json_integer_is_rejected_with_value_free_diagnostic(self) -> None:
        raw = json.dumps(_evidence()).replace('"net_tokens": 100', '"net_tokens": ' + "9" * 5000, 1)

        result, stdout, stderr = _run_evidence(raw, raw=True)

        self.assertEqual((result, stdout, stderr), (2, "", "invalid evidence\n"))

    def test_zero_baseline_pair_makes_savings_unavailable_and_no_go(self) -> None:
        evidence = _evidence()
        evidence["pairs"][0]["baseline"]["net_tokens"] = 0

        result, stdout, stderr = _run_evidence(evidence)
        report = json.loads(stdout)

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(report["decision"], "no-go")
        self.assertIsNone(report["savings"]["estimate"])
        self.assertIsNone(report["savings"]["confidence_interval_95"])
        self.assertFalse(report["gates"]["savings"]["passes"])

    def test_rejects_oversized_evidence_before_parsing(self) -> None:
        evidence_text = json.dumps(_evidence())
        oversized_text = evidence_text + " " * (benchmark.MAX_EVIDENCE_BYTES + 1)

        result, stdout, stderr = _run_evidence(oversized_text, raw=True)

        self.assertEqual((result, stdout, stderr), (2, "", "invalid evidence\n"))

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "platform lacks O_NOFOLLOW")
    def test_rejects_symlink_evidence_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evidence_path = pathlib.Path(directory) / "evidence.json"
            evidence_path.write_text(json.dumps(_evidence()), encoding="utf-8")
            symlink_path = pathlib.Path(directory) / "evidence-link.json"
            symlink_path.symlink_to(evidence_path)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = benchmark.main(["--evidence", str(symlink_path)])

        self.assertEqual(
            (result, stdout.getvalue(), stderr.getvalue()),
            (2, "", "invalid evidence\n"),
        )

    def test_reader_fails_closed_when_nofollow_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evidence_path = pathlib.Path(directory) / "evidence.json"
            evidence_path.write_text(json.dumps(_evidence()), encoding="utf-8")
            with mock.patch.object(os, "O_NOFOLLOW", 0):
                with self.assertRaises(benchmark.EvidenceValidationError):
                    benchmark._read_regular_file(str(evidence_path))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "platform lacks FIFOs")
    def test_rejects_fifo_evidence_path_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fifo_path = pathlib.Path(directory) / "evidence.fifo"
            os.mkfifo(fifo_path)
            completed = subprocess.run(
                [sys.executable, str(BENCHMARK_PATH), "--evidence", str(fifo_path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "invalid evidence\n")

    def test_no_subprocess_hash_network_or_persistent_report_side_effects(self) -> None:
        evidence = _evidence()
        with tempfile.TemporaryDirectory() as directory:
            evidence_path = pathlib.Path(directory) / "evidence.json"
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
            before = sorted(pathlib.Path(directory).iterdir())
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.object(subprocess, "run", side_effect=AssertionError),
                mock.patch.object(hashlib, "sha256", side_effect=AssertionError),
                mock.patch.object(socket, "socket", side_effect=AssertionError),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                result = benchmark.main(["--evidence", str(evidence_path)])
            after = sorted(pathlib.Path(directory).iterdir())

        self.assertEqual(result, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(before, after)
        self.assertNotIn("sealed-case-001", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
