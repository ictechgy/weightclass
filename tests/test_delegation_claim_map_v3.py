import json
import re
import unittest
from pathlib import Path
from typing import cast

from weightclass.delegation_conformance import CONFORMANCE_CASES
from weightclass.delegation_qualification import (
    ACTIONS,
    MODES,
    REQUIRED_SCENARIOS,
    QualificationInvalidInputError,
    build_qualification_candidate,
    load_qualification_registry,
)
from weightclass.delegation_schema import CATEGORIES, ROLES

CLAIM_MAP_PATH = Path(__file__).parent / "fixtures" / "delegation_claim_map_v3.json"
EXPECTED_ROLES = ("orchestrator", "worker", "reviewer")
EXPECTED_CATEGORIES = ("implementation", "tests", "documentation")
EXPECTED_ACTIONS = ("workspace_read", "workspace_write", "command_execution")
EXPECTED_MODES = ("allow", "deny")
EXPECTED_SCENARIOS = (
    "action_attribution",
    "artifact_integrity_and_substitution",
    "descendant_cleanup",
    "descendant_leakage",
    "distinct_enforcement_contexts",
    "integration_restriction",
    "integration_verification_commands",
    "output_channel_separation",
    "process_creation_attribution",
    "reviewer_rejection",
    "runtime_deadline",
    "stage_order",
    "worker_concurrency_bound",
)
TOP_LEVEL_FIELDS = {
    "claim_map_schema_version",
    "suite_revision",
    "resource_profiles",
    "permission_cases",
    "scenario_cases",
}
CASE_FIELDS = {
    "case_id",
    "evaluated_subject",
    "fixture_id",
    "setup_id",
    "stimulus_id",
    "oracle_id",
    "expected_observation",
    "negative_control_id",
    "identity_requirements",
    "platform_requirements",
    "resource_bounds_id",
    "cleanup_requirement",
    "evidence_projection",
    "feasibility",
    "blocking_reason",
}
RESOURCE_PROFILE = {
    "deadline_ms": 1_000,
    "max_event_bytes": 1_024,
    "max_events": 8,
    "max_file_bytes": 4_096,
    "max_files": 4,
    "max_processes": 4,
}
SCENARIO_SUBJECT_AND_BLOCKER = {
    "action_attribution": (
        "adapter-runtime",
        "action-attribution-not-independent-v1",
    ),
    "artifact_integrity_and_substitution": (
        "launch-identity",
        "verified-object-launch-not-established-v1",
    ),
    "descendant_cleanup": (
        "os-supervisor",
        "authoritative-lifecycle-boundary-not-established-v1",
    ),
    "descendant_leakage": (
        "os-supervisor",
        "escaped-descendant-boundary-not-established-v1",
    ),
    "distinct_enforcement_contexts": (
        "adapter-runtime",
        "context-identity-not-independent-v1",
    ),
    "integration_restriction": (
        "adapter-runtime",
        "integration-enforcement-not-independent-v1",
    ),
    "integration_verification_commands": (
        "adapter-runtime",
        "command-result-not-independent-v1",
    ),
    "output_channel_separation": (
        "adapter-runtime",
        "channel-ownership-not-independent-v1",
    ),
    "process_creation_attribution": (
        "os-supervisor",
        "process-role-attribution-not-independent-v1",
    ),
    "reviewer_rejection": (
        "adapter-runtime",
        "reviewer-decision-is-untrusted-telemetry-v1",
    ),
    "runtime_deadline": (
        "adapter-runtime",
        "runtime-wide-process-closure-not-established-v1",
    ),
    "stage_order": (
        "adapter-runtime",
        "stage-events-are-untrusted-telemetry-v1",
    ),
    "worker_concurrency_bound": (
        "os-supervisor",
        "worker-process-identity-not-independent-v1",
    ),
}
IDENTIFIER_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]*\Z")
CASE_ID_PATTERN = re.compile(r"(?:matrix|scenario)/[a-z0-9][a-z0-9_./-]*\Z")
FORBIDDEN_FIELDS = {
    "credential",
    "driver_path",
    "passed",
    "prompt",
    "response",
    "runtime_path",
    "task",
    "task_hash",
    "transcript",
    "vendor_output",
}


class _DuplicateKeyError(ValueError):
    pass


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKeyError()
        value[key] = item
    return value


def _load_claim_map() -> dict[str, object]:
    value: object = json.loads(
        CLAIM_MAP_PATH.read_text(encoding="utf-8"),
        object_pairs_hook=_object_without_duplicate_keys,
    )
    if not isinstance(value, dict):
        raise AssertionError("claim map must be an object")
    return cast(dict[str, object], value)


def _require_object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise AssertionError("expected object")
    return cast(dict[str, object], value)


def _require_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise AssertionError("expected list")
    return cast(list[object], value)


def _require_identifier(value: object) -> str:
    if not isinstance(value, str) or IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise AssertionError("expected identifier")
    return value


def _require_identifier_list(value: object) -> list[str]:
    return [_require_identifier(item) for item in _require_list(value)]


def _require_case_id(value: object) -> str:
    if not isinstance(value, str) or CASE_ID_PATTERN.fullmatch(value) is None:
        raise AssertionError("expected case ID")
    return value


def _walk_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        typed_value = cast(dict[object, object], value)
        return {str(key) for key in typed_value} | {
            nested for item in typed_value.values() for nested in _walk_keys(item)
        }
    if isinstance(value, list):
        return {nested for item in cast(list[object], value) for nested in _walk_keys(item)}
    return set()


class DelegationClaimMapV3Tests(unittest.TestCase):
    claim_map: dict[str, object]

    @classmethod
    def setUpClass(cls) -> None:
        cls.claim_map = _load_claim_map()

    def _rows(self, field: str) -> list[dict[str, object]]:
        return [_require_object(item) for item in _require_list(self.claim_map[field])]

    def test_claim_map_fixture_exists(self) -> None:
        """Breaks if the reviewed claim inventory is absent from the repository."""
        self.assertTrue(CLAIM_MAP_PATH.is_file())

    def test_claim_map_has_bounded_exact_schema(self) -> None:
        """Breaks if prose or unknown fields can silently change qualification scope."""
        self.assertEqual(set(self.claim_map), TOP_LEVEL_FIELDS)
        self.assertEqual(self.claim_map["claim_map_schema_version"], 1)
        self.assertEqual(self.claim_map["suite_revision"], "delegation-conformance-v2")
        self.assertEqual(
            self.claim_map["resource_profiles"],
            {"bounded-offline-v1": RESOURCE_PROFILE},
        )

        rows = self._rows("permission_cases") + self._rows("scenario_cases")
        self.assertEqual(len(rows), 67)
        self.assertTrue(all(set(row) == CASE_FIELDS for row in rows))
        self.assertEqual(len({_require_case_id(row["case_id"]) for row in rows}), 67)
        self.assertFalse(_walk_keys(self.claim_map) & FORBIDDEN_FIELDS)

        for row in rows:
            for field in CASE_FIELDS - {
                "case_id",
                "identity_requirements",
                "platform_requirements",
            }:
                _require_identifier(row[field])
            self.assertEqual(
                _require_identifier_list(row["identity_requirements"]),
                ["adapter-runtime", "oracle-observer", "fixture-bundle"],
            )
            self.assertEqual(
                _require_identifier_list(row["platform_requirements"]),
                ["darwin", "linux"],
            )
            self.assertEqual(row["resource_bounds_id"], "bounded-offline-v1")
            self.assertEqual(row["oracle_id"], "no-independent-oracle-v1")
            self.assertEqual(row["evidence_projection"], "none-claim-blocked-v1")
            self.assertEqual(row["feasibility"], "blocked")

    def test_permission_claims_match_both_independent_production_catalogs(self) -> None:
        """Breaks if one permission claim drifts, disappears, or gains a weaker meaning."""
        expected_ids = [
            f"matrix/{role}/{category}/{action}/{mode}"
            for role in EXPECTED_ROLES
            for category in EXPECTED_CATEGORIES
            for action in EXPECTED_ACTIONS
            for mode in EXPECTED_MODES
        ]
        qualification_ids = [
            f"matrix/{role}/{category}/{action}/{mode}"
            for role in ROLES
            for category in CATEGORIES
            for action in ACTIONS
            for mode in MODES
        ]
        conformance_ids = [case.case_id for case in CONFORMANCE_CASES if case.role is not None]
        rows = self._rows("permission_cases")

        self.assertEqual(qualification_ids, expected_ids)
        self.assertEqual(conformance_ids, expected_ids)
        self.assertEqual([row["case_id"] for row in rows], expected_ids)

        for row, case_id in zip(rows, expected_ids, strict=True):
            _, _, _, action, mode = case_id.split("/")
            self.assertEqual(row["evaluated_subject"], "adapter-role-permission")
            self.assertEqual(row["fixture_id"], f"fixed-{action}-v1")
            self.assertEqual(row["setup_id"], "unimplemented-permission-context-v1")
            self.assertEqual(row["stimulus_id"], f"attempt-{action}-v1")
            self.assertEqual(
                row["expected_observation"],
                "effect-observed" if mode == "allow" else "effect-blocked",
            )
            self.assertEqual(row["negative_control_id"], "runtime-self-label-v1")
            self.assertEqual(
                row["cleanup_requirement"],
                "cooperative-process-group-only-v1",
            )
            self.assertEqual(
                row["blocking_reason"],
                "role-action-attribution-not-independent-v1",
            )

    def test_scenario_claims_are_exact_and_remain_blocked(self) -> None:
        """Breaks if a scenario becomes eligible without an independent oracle."""
        self.assertEqual(tuple(REQUIRED_SCENARIOS), EXPECTED_SCENARIOS)
        expected_case_ids = [f"scenario/{scenario_id}" for scenario_id in EXPECTED_SCENARIOS]
        conformance_ids = [
            case.case_id for case in CONFORMANCE_CASES if case.scenario_id is not None
        ]
        self.assertEqual(conformance_ids, expected_case_ids)
        rows = self._rows("scenario_cases")
        self.assertEqual([row["case_id"] for row in rows], expected_case_ids)

        for row, scenario_id in zip(rows, EXPECTED_SCENARIOS, strict=True):
            subject, blocker = SCENARIO_SUBJECT_AND_BLOCKER[scenario_id]
            self.assertEqual(row["evaluated_subject"], subject)
            self.assertEqual(row["fixture_id"], f"fixed-{scenario_id}-v1")
            self.assertEqual(row["setup_id"], "unimplemented-scenario-context-v1")
            self.assertEqual(row["stimulus_id"], f"exercise-{scenario_id}-v1")
            self.assertEqual(row["expected_observation"], "scenario-property-established")
            self.assertEqual(row["negative_control_id"], "runtime-self-report-v1")
            self.assertEqual(
                row["cleanup_requirement"],
                "cooperative-process-group-only-v1",
            )
            self.assertEqual(row["blocking_reason"], blocker)

    def test_claim_map_cannot_be_used_as_v2_evidence_or_registry(self) -> None:
        """Breaks if the design inventory can cross either production trust boundary."""
        with self.assertRaises(QualificationInvalidInputError):
            build_qualification_candidate(self.claim_map, Path("/not-used"))
        with self.assertRaises(QualificationInvalidInputError):
            load_qualification_registry(CLAIM_MAP_PATH)
        self.assertNotIn(Path("src").resolve(), CLAIM_MAP_PATH.resolve().parents)


if __name__ == "__main__":
    unittest.main()
