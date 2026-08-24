import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest import mock

from weightclass import cli
from weightclass.cost_recommendation import (
    CostRecommendationError,
    build_recommendation_receipt,
    parse_cost_profile,
    parse_qualification_card,
)
from weightclass.router import Route

_ROUTE_BIN: Path | None = None


def setUpModule() -> None:
    global _ROUTE_BIN
    _ROUTE_BIN = Path(tempfile.mkdtemp(prefix="weightclass-cost-route-"))
    for vendor in ("agy", "claude", "codex", "grok"):
        executable = _ROUTE_BIN / vendor
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o700)


def tearDownModule() -> None:
    global _ROUTE_BIN
    if _ROUTE_BIN is None:
        return
    for vendor in ("agy", "claude", "codex", "grok"):
        (_ROUTE_BIN / vendor).unlink(missing_ok=True)
    _ROUTE_BIN.rmdir()
    _ROUTE_BIN = None


def _weightclass(*arguments: str, task: str) -> subprocess.CompletedProcess[str]:
    if _ROUTE_BIN is None:
        raise AssertionError("test route directory was not initialized")
    environment = os.environ.copy()
    environment["PATH"] = str(_ROUTE_BIN)
    return subprocess.run(
        [sys.executable, "-m", "weightclass", *arguments],
        capture_output=True,
        check=False,
        env=environment,
        input=task,
        text=True,
    )


def _profile_fingerprint(profile: dict[str, Any]) -> str:
    encoded = json.dumps(
        profile,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _route(*arguments: str) -> dict[str, Any]:
    completed = _weightclass("route", *arguments, "--tier", "low", task="Fix a typo.")
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    parsed = json.loads(completed.stdout)
    if not isinstance(parsed, dict):
        raise AssertionError("route output was not an object")
    return cast(dict[str, Any], parsed)


def _cost_profile(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "profile_id": "team-cost-v1",
        "measurement_contract_id": "reviewed-cost-units-v1",
        "unit": "reviewed-cost-unit",
        "identifiers_not_task_derived": True,
        "pricing_inferred": False,
        "actual_billing_claimed": False,
        "routes": [
            {
                "route_fingerprint": baseline["route_fingerprint"],
                "expected_completed_cost_units": 100,
            },
            {
                "route_fingerprint": candidate["route_fingerprint"],
                "expected_completed_cost_units": 40,
            },
        ],
    }


def _qualification_card(
    profile: dict[str, Any], baseline: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "card_id": f"{candidate['vendor']}-low-qualified-v1",
        "identifiers_not_task_derived": True,
        "cost_profile_fingerprint": _profile_fingerprint(profile),
        "measurement_contract_id": profile["measurement_contract_id"],
        "vendor": candidate["vendor"],
        "tier": "low",
        "baseline_route_fingerprint": baseline["route_fingerprint"],
        "candidate_route_fingerprint": candidate["route_fingerprint"],
        "status": "qualified",
        "sample_size": 90,
        "cost_savings_lower_bound_basis_points": 4_628,
        "cost_savings_ci_width_basis_points": 1_772,
        "quality_delta_lower_bound_basis_points": -402,
        "quality_noninferiority_margin_basis_points": 500,
        "new_critical_failures": 0,
        "all_attempts_included": True,
        "independent_quality_review": True,
        "covered_languages": ["en", "ko"],
        "covered_categories": [
            "concurrency",
            "data-integrity",
            "destructive-work",
            "migration",
            "performance",
            "privacy",
            "reliability",
            "routine",
            "security",
        ],
        "covered_tiers": ["low", "standard", "high"],
        "valid_until": "9999-12-31",
    }


def _recommend(
    preset: str,
    profile: dict[str, Any],
    card: dict[str, Any],
    *,
    task: str = "Fix a typo.",
    tier: str = "low",
    extra_arguments: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        profile_path = root / "profile.json"
        card_path = root / "card.json"
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        card_path.write_text(json.dumps(card), encoding="utf-8")
        return _weightclass(
            "recommend",
            "--preset",
            preset,
            "--cost-profile",
            str(profile_path),
            "--qualification-card",
            str(card_path),
            "--tier",
            tier,
            *extra_arguments,
            task=task,
        )


class CostRecommendationCliTests(unittest.TestCase):
    def test_rejects_non_integer_schema_versions(self) -> None:
        """Breaks if JSON booleans or floats can impersonate schema version 1."""
        baseline = {"route_fingerprint": "sha256:" + "1" * 64}
        candidate = {
            "route_fingerprint": "sha256:" + "2" * 64,
            "vendor": "claude",
        }
        profile = _cost_profile(baseline, candidate)
        card = _qualification_card(profile, baseline, candidate)

        for parser, document in (
            (parse_cost_profile, profile),
            (parse_qualification_card, card),
        ):
            for invalid_version in (True, 1.0):
                with self.subTest(parser=parser.__name__, value=invalid_version):
                    invalid_document = dict(document)
                    invalid_document["schema_version"] = invalid_version

                    with self.assertRaises(CostRecommendationError):
                        parser(invalid_document)

    def test_rejects_route_objects_that_do_not_match_their_fingerprints(self) -> None:
        """Breaks if a recommendation can display a command not bound by its fingerprint."""
        baseline = _route("--source-vendor", "claude")
        candidate = _route("--preset", "claude-cost-focused")
        profile_document = _cost_profile(baseline, candidate)
        card_document = _qualification_card(profile_document, baseline, candidate)
        forged_candidate = Route(
            route_id="forged-candidate",
            vendor="claude",
            workflow="",
            command=("claude", "--forged-candidate"),
            tier="low",
        )
        baseline_route = Route(
            route_id=baseline["route"],
            vendor=baseline["vendor"],
            workflow="",
            command=tuple(baseline["command"]),
            tier=baseline["tier"],
        )

        with self.assertRaises(CostRecommendationError):
            build_recommendation_receipt(
                parse_cost_profile(profile_document),
                parse_qualification_card(card_document),
                baseline_route=baseline_route,
                baseline_route_fingerprint=baseline["route_fingerprint"],
                baseline_allow_mixed_vendors=False,
                candidate_route=forged_candidate,
                candidate_route_fingerprint=candidate["route_fingerprint"],
                candidate_allow_mixed_vendors=False,
                candidate_posture="balanced",
                routing_reason_code="explicit.requested_tier",
                candidate_configuration_status="measured_low_route_only",
            )

    def test_reviews_a_cost_profile_without_reading_a_task(self) -> None:
        """Breaks if evidence binding requires an undocumented external hash tool."""
        baseline = {"route_fingerprint": "sha256:" + "1" * 64}
        candidate = {
            "route_fingerprint": "sha256:" + "2" * 64,
            "vendor": "claude",
        }
        profile = _cost_profile(baseline, candidate)
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / "profile.json"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            output = io.StringIO()
            with (
                mock.patch.object(
                    cli,
                    "read_task_from_standard_input",
                    side_effect=AssertionError("task input was read"),
                ),
                contextlib.redirect_stdout(output),
            ):
                exit_code = cli.main(["review-cost-profile", "--cost-profile", str(profile_path)])

        self.assertEqual(exit_code, 0)
        review = json.loads(output.getvalue())
        self.assertEqual(review["fingerprint"], _profile_fingerprint(profile))
        self.assertEqual(review["profile_id"], "team-cost-v1")
        self.assertEqual(len(review["routes"]), 2)
        self.assertTrue(review["identifiers_not_task_derived"])
        self.assertFalse(review["values_verified_by_router"])

    def test_recommends_a_qualified_lower_cost_route_without_starting_a_vendor(self) -> None:
        """Breaks if advisory cost routing stops binding qualified exact routes."""
        baseline = _route("--source-vendor", "claude")
        candidate = _route("--preset", "claude-cost-focused")
        profile = _cost_profile(baseline, candidate)
        card = _qualification_card(profile, baseline, candidate)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "profile.json"
            card_path = root / "card.json"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            card_path.write_text(json.dumps(card), encoding="utf-8")

            completed = _weightclass(
                "recommend",
                "--preset",
                "claude-cost-focused",
                "--cost-profile",
                str(profile_path),
                "--qualification-card",
                str(card_path),
                "--tier",
                "low",
                task="Fix a typo.",
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        receipt = json.loads(completed.stdout)
        self.assertEqual(receipt["decision"], "recommend")
        self.assertEqual(receipt["reason_code"], "qualified_cost_advantage")
        self.assertEqual(receipt["objective"], "expected_completed_cost")
        self.assertEqual(receipt["fallback"], "none")
        self.assertTrue(receipt["recommendation_only"])
        self.assertEqual(receipt["baseline"]["route_fingerprint"], baseline["route_fingerprint"])
        self.assertEqual(receipt["candidate"]["route_fingerprint"], candidate["route_fingerprint"])
        self.assertEqual(receipt["baseline"]["expected_completed_cost_units"], 100)
        self.assertEqual(receipt["candidate"]["expected_completed_cost_units"], 40)
        self.assertEqual(receipt["candidate"]["task_delivery"], "stdin")
        self.assertEqual(receipt["qualification"]["sample_size"], 90)
        self.assertEqual(
            receipt["qualification"]["cost_savings_lower_bound_basis_points"],
            4_628,
        )
        self.assertEqual(
            receipt["qualification"]["quality_delta_lower_bound_basis_points"],
            -402,
        )
        self.assertFalse(receipt["qualification"]["assertions_verified_by_router"])
        self.assertTrue(receipt["recommendation_fingerprint"].startswith("sha256:"))
        self.assertNotIn("Fix a typo.", completed.stdout)

    def test_abstains_when_the_reviewed_cost_profile_has_no_cost_advantage(self) -> None:
        """Breaks if a flat subscription-cost scenario is reported as a saving."""
        baseline = _route("--source-vendor", "claude")
        candidate = _route("--preset", "claude-cost-focused")
        profile = _cost_profile(baseline, candidate)
        profile["routes"][1]["expected_completed_cost_units"] = 100
        card = _qualification_card(profile, baseline, candidate)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "profile.json"
            card_path = root / "card.json"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            card_path.write_text(json.dumps(card), encoding="utf-8")
            completed = _weightclass(
                "recommend",
                "--preset",
                "claude-cost-focused",
                "--cost-profile",
                str(profile_path),
                "--qualification-card",
                str(card_path),
                "--tier",
                "low",
                task="Fix a typo.",
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        receipt = json.loads(completed.stdout)
        self.assertEqual(receipt["decision"], "abstain")
        self.assertEqual(receipt["reason_code"], "no_cost_advantage")
        self.assertEqual(receipt["cost_profile"]["profile_cost_savings_basis_points"], 0)

    def test_abstains_when_candidate_and_baseline_commands_are_identical(self) -> None:
        """Breaks if a new route ID can make an unchanged command look cost-qualified."""
        baseline = _route("--source-vendor", "codex")
        candidate = _route("--preset", "codex-cost-focused")
        self.assertEqual(baseline["command"], candidate["command"])
        profile = _cost_profile(baseline, candidate)
        card = _qualification_card(profile, baseline, candidate)

        completed = _recommend("codex-cost-focused", profile, card)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        receipt = json.loads(completed.stdout)
        self.assertEqual(receipt["decision"], "abstain")
        self.assertEqual(receipt["reason_code"], "candidate_route_unchanged")

    def test_recommendation_fingerprint_binds_the_entire_qualification_card(self) -> None:
        """Breaks if evidence can drift without invalidating its recommendation."""
        baseline = _route("--source-vendor", "claude")
        candidate = _route("--preset", "claude-cost-focused")
        profile = _cost_profile(baseline, candidate)
        first_card = _qualification_card(profile, baseline, candidate)
        second_card = dict(first_card)
        second_card["sample_size"] = 91

        first = _recommend("claude-cost-focused", profile, first_card)
        second = _recommend("claude-cost-focused", profile, second_card)

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        first_receipt = json.loads(first.stdout)
        second_receipt = json.loads(second.stdout)
        self.assertTrue(first_receipt["qualification"]["fingerprint"].startswith("sha256:"))
        self.assertNotEqual(
            first_receipt["qualification"]["fingerprint"],
            second_receipt["qualification"]["fingerprint"],
        )
        self.assertNotEqual(
            first_receipt["recommendation_fingerprint"],
            second_receipt["recommendation_fingerprint"],
        )

    def test_abstains_when_qualification_is_bound_to_another_route(self) -> None:
        """Breaks if stale evidence can authorize a changed candidate command."""
        baseline = _route("--source-vendor", "claude")
        candidate = _route("--preset", "claude-cost-focused")
        profile = _cost_profile(baseline, candidate)
        card = _qualification_card(profile, baseline, candidate)
        card["candidate_route_fingerprint"] = "sha256:" + "0" * 64

        completed = _recommend("claude-cost-focused", profile, card)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        receipt = json.loads(completed.stdout)
        self.assertEqual(receipt["decision"], "abstain")
        self.assertEqual(receipt["reason_code"], "qualification_route_mismatch")

    def test_reports_each_vendor_capability_and_task_delivery_without_execution(self) -> None:
        """Breaks if provider differences are normalized into unsupported claims."""
        expected = {
            "agy": {
                "decision": "abstain",
                "task_delivery": "argv",
                "tier_effort_override": False,
                "tier_model_override": False,
            },
            "claude": {
                "decision": "recommend",
                "task_delivery": "stdin",
                "tier_effort_override": True,
                "tier_model_override": True,
            },
            "codex": {
                "decision": "abstain",
                "task_delivery": "stdin",
                "tier_effort_override": True,
                "tier_model_override": True,
            },
            "grok": {
                "decision": "abstain",
                "task_delivery": "argv",
                "tier_effort_override": False,
                "tier_model_override": True,
            },
        }

        for vendor, expected_fields in expected.items():
            with self.subTest(vendor=vendor):
                preset = f"{vendor}-cost-focused"
                baseline = _route("--source-vendor", vendor)
                candidate = _route("--preset", preset)
                profile = _cost_profile(baseline, candidate)
                card = _qualification_card(profile, baseline, candidate)

                completed = _recommend(preset, profile, card)

                self.assertEqual(completed.returncode, 0, completed.stderr)
                receipt = json.loads(completed.stdout)
                self.assertEqual(receipt["decision"], expected_fields["decision"])
                if expected_fields["decision"] == "abstain":
                    self.assertEqual(receipt["reason_code"], "candidate_route_unchanged")
                self.assertTrue(receipt["capability"]["reviewed_effort_routing"])
                self.assertEqual(
                    receipt["capability"]["tier_effort_override"],
                    expected_fields["tier_effort_override"],
                )
                self.assertEqual(
                    receipt["capability"]["tier_model_override"],
                    expected_fields["tier_model_override"],
                )
                self.assertEqual(
                    receipt["candidate"]["task_delivery"],
                    expected_fields["task_delivery"],
                )
                self.assertNotIn("Fix a typo.", completed.stdout)

    def test_abstains_for_each_failed_qualification_gate(self) -> None:
        """Breaks if a weak or stale qualification card can become a recommendation."""
        baseline = _route("--source-vendor", "claude")
        candidate = _route("--preset", "claude-cost-focused")
        profile = _cost_profile(baseline, candidate)
        valid_card = _qualification_card(profile, baseline, candidate)
        cases: tuple[tuple[str, str, object], ...] = (
            ("status", "qualification_not_qualified", "provisional"),
            ("valid_until", "qualification_expired", "2000-01-01"),
            (
                "cost_profile_fingerprint",
                "qualification_profile_mismatch",
                "sha256:" + "0" * 64,
            ),
            (
                "measurement_contract_id",
                "qualification_measurement_mismatch",
                "other-contract",
            ),
            ("vendor", "qualification_vendor_mismatch", "codex"),
            ("tier", "qualification_tier_mismatch", "standard"),
            ("sample_size", "qualification_insufficient_samples", 29),
            (
                "cost_savings_lower_bound_basis_points",
                "qualification_insufficient_cost_savings",
                1_499,
            ),
            (
                "cost_savings_ci_width_basis_points",
                "qualification_cost_interval_too_wide",
                2_001,
            ),
            (
                "quality_delta_lower_bound_basis_points",
                "qualification_quality_not_noninferior",
                -501,
            ),
            (
                "quality_noninferiority_margin_basis_points",
                "qualification_quality_not_noninferior",
                501,
            ),
            ("new_critical_failures", "qualification_new_critical_failure", 1),
            ("all_attempts_included", "qualification_incomplete_attempts", False),
            (
                "independent_quality_review",
                "qualification_missing_independent_quality",
                False,
            ),
            ("covered_languages", "qualification_incomplete_coverage", ["en"]),
        )

        for field, reason_code, value in cases:
            with self.subTest(field=field):
                card = dict(valid_card)
                card[field] = value

                completed = _recommend("claude-cost-focused", profile, card)

                self.assertEqual(completed.returncode, 0, completed.stderr)
                receipt = json.loads(completed.stdout)
                self.assertEqual(receipt["decision"], "abstain")
                self.assertEqual(receipt["reason_code"], reason_code)

    def test_rejects_an_invalid_cost_document_before_reading_task_input(self) -> None:
        """Breaks if unsafe cost metadata can cause task input to be consumed first."""
        baseline = {"route_fingerprint": "sha256:" + "1" * 64}
        candidate = {
            "route_fingerprint": "sha256:" + "2" * 64,
            "vendor": "claude",
        }
        valid_profile = _cost_profile(baseline, candidate)
        valid_card = _qualification_card(valid_profile, baseline, candidate)
        profile_with_task = dict(valid_profile)
        profile_with_task["task"] = "must-not-be-accepted"
        profile_with_derived_ids = dict(valid_profile)
        profile_with_derived_ids["identifiers_not_task_derived"] = False
        card_with_task = dict(valid_card)
        card_with_task["task"] = "must-not-be-accepted"
        card_with_derived_ids = dict(valid_card)
        card_with_derived_ids["identifiers_not_task_derived"] = False

        for profile, card in (
            (profile_with_task, valid_card),
            (profile_with_derived_ids, valid_card),
            (valid_profile, card_with_task),
            (valid_profile, card_with_derived_ids),
        ):
            with self.subTest():
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    profile_path = root / "profile.json"
                    card_path = root / "card.json"
                    profile_path.write_text(json.dumps(profile), encoding="utf-8")
                    card_path.write_text(json.dumps(card), encoding="utf-8")
                    errors = io.StringIO()
                    with (
                        mock.patch.object(
                            cli,
                            "read_task_from_standard_input",
                            side_effect=AssertionError("task input was read"),
                        ),
                        contextlib.redirect_stderr(errors),
                    ):
                        exit_code = cli.main(
                            [
                                "recommend",
                                "--preset",
                                "claude-cost-focused",
                                "--cost-profile",
                                str(profile_path),
                                "--qualification-card",
                                str(card_path),
                            ]
                        )

                self.assertEqual(exit_code, 2)
                self.assertEqual(errors.getvalue(), '{"error": "invalid_input"}\n')


if __name__ == "__main__":
    unittest.main()
