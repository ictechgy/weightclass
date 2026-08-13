"""Score sanitized provider-export usage without reading raw provider exports."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from tests.eval import token_benchmark  # noqa: E402

OBJECTIVES = frozenset(("metered_cost", "subscription_quota"))
TOP_FIELDS = token_benchmark.TOP_FIELDS | frozenset(("objective",))
GATE_FIELDS = frozenset(
    (
        "minimum_pairs",
        "minimum_usage_savings",
        "maximum_savings_ci_width",
        "quality_noninferiority_margin",
        "savings_ci_rule",
        "quality_ci_rule",
        "required_languages",
        "required_categories",
    )
)
ARM_FIELDS = frozenset(
    ("usage_units", "invocations", "completed", "quality_pass", "critical_failure")
)
PROVENANCE_FIELDS = frozenset(
    (
        "fresh_blind_tasks",
        "same_sealed_tasks",
        "same_provider_runtime",
        "only_reviewed_configuration_dimensions_changed",
        "provider_export_reviewed",
        "normalization_contract_reviewed",
        "fixed_subscription_charge",
        "export_contains_task_data",
        "export_contains_account_identifiers",
        "counterbalanced_order",
        "all_attempts_included",
        "ids_not_task_derived",
        "outside_repository_custody",
        "independent_quality_review",
    )
)


def _token_arm(value: object) -> dict[str, Any]:
    arm = token_benchmark._exact_mapping(value, ARM_FIELDS)
    return {
        "net_tokens": arm["usage_units"],
        "invocations": arm["invocations"],
        "completed": arm["completed"],
        "quality_pass": arm["quality_pass"],
        "critical_failure": arm["critical_failure"],
    }


def _token_evidence(raw: object) -> tuple[str, dict[str, Any]]:
    evidence = token_benchmark._exact_mapping(raw, TOP_FIELDS)
    objective = evidence["objective"]
    if not isinstance(objective, str) or objective not in OBJECTIVES:
        raise token_benchmark.EvidenceValidationError

    gate = token_benchmark._exact_mapping(evidence["gate"], GATE_FIELDS)
    provenance = token_benchmark._exact_mapping(evidence["provenance"], PROVENANCE_FIELDS)
    for value in provenance.values():
        token_benchmark._boolean(value)
    if (
        not provenance["provider_export_reviewed"]
        or not provenance["normalization_contract_reviewed"]
        or provenance["export_contains_task_data"]
        or provenance["export_contains_account_identifiers"]
        or provenance["fixed_subscription_charge"] != (objective == "subscription_quota")
    ):
        raise token_benchmark.EvidenceValidationError

    pairs_value = evidence["pairs"]
    if not isinstance(pairs_value, list):
        raise token_benchmark.EvidenceValidationError
    pairs = []
    for pair_value in pairs_value:
        pair = token_benchmark._exact_mapping(pair_value, token_benchmark.PAIR_FIELDS)
        pairs.append(
            {
                "id": pair["id"],
                "language": pair["language"],
                "category": pair["category"],
                "expected_tier": pair["expected_tier"],
                "baseline": _token_arm(pair["baseline"]),
                "candidate": _token_arm(pair["candidate"]),
            }
        )

    comparable_measurement = (
        provenance["same_provider_runtime"]
        and provenance["only_reviewed_configuration_dimensions_changed"]
        and provenance["provider_export_reviewed"]
        and provenance["normalization_contract_reviewed"]
    )
    return objective, {
        "schema_version": evidence["schema_version"],
        "baseline_id": evidence["baseline_id"],
        "candidate_id": evidence["candidate_id"],
        "measurement_contract_id": evidence["measurement_contract_id"],
        "baseline_configuration_fingerprint": evidence["baseline_configuration_fingerprint"],
        "candidate_configuration_fingerprint": evidence["candidate_configuration_fingerprint"],
        "gate": {
            "minimum_pairs": gate["minimum_pairs"],
            "minimum_net_token_savings": gate["minimum_usage_savings"],
            "maximum_savings_ci_width": gate["maximum_savings_ci_width"],
            "quality_noninferiority_margin": gate["quality_noninferiority_margin"],
            "savings_ci_rule": gate["savings_ci_rule"],
            "quality_ci_rule": gate["quality_ci_rule"],
            "required_languages": gate["required_languages"],
            "required_categories": gate["required_categories"],
        },
        "provenance": {
            "fresh_blind_tasks": provenance["fresh_blind_tasks"],
            "same_sealed_tasks": provenance["same_sealed_tasks"],
            "same_provider_runtime_model": comparable_measurement,
            "counterbalanced_order": provenance["counterbalanced_order"],
            "all_attempts_included": provenance["all_attempts_included"],
            "ids_not_task_derived": provenance["ids_not_task_derived"],
            "outside_repository_custody": provenance["outside_repository_custody"],
            "independent_quality_review": provenance["independent_quality_review"],
        },
        "pairs": pairs,
    }


def build_report(raw: object) -> dict[str, Any]:
    """Return aggregate-only metered-cost or fixed-quota evidence."""
    objective, converted = _token_evidence(raw)
    validated = token_benchmark.validate_evidence(converted)
    report = token_benchmark.build_report(validated)
    report["baseline"]["usage_units"] = report["baseline"].pop("net_tokens")
    report["candidate"]["usage_units"] = report["candidate"].pop("net_tokens")
    report["usage_savings"] = report.pop("savings")
    report["gates"]["usage_savings"] = report["gates"].pop("savings")
    report["measurement"] = {
        "objective": objective,
        "kind": "externally_normalized_provider_export",
        "promotion_scope": "cost_opt_in" if objective == "metered_cost" else "capacity_only",
        "eligible_for_cost_recommendation": (
            objective == "metered_cost" and report["decision"] == "go"
        ),
        "monthly_bill_reduction_claimed": False,
        "pricing_inferred_by_scorer": False,
        "provider_export_verified_by_scorer": False,
    }
    return report


def _invalid() -> int:
    print("invalid evidence", file=sys.stderr)
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 2 or arguments[0] != "--evidence" or not arguments[1]:
        return _invalid()
    try:
        raw_text = token_benchmark._read_regular_file(arguments[1])
        raw = json.loads(
            raw_text,
            object_pairs_hook=token_benchmark._reject_duplicate_fields,
            parse_int=token_benchmark._parse_json_integer,
        )
        output = json.dumps(
            build_report(raw), sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (
        token_benchmark.DuplicateJsonFieldError,
        token_benchmark.EvidenceValidationError,
        json.JSONDecodeError,
        OverflowError,
        RecursionError,
    ):
        return _invalid()
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
