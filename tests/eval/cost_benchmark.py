"""Score aggregate-only paired estimated-cost evidence without pricing logic."""

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

COST_GATE_FIELDS = frozenset(
    (
        "minimum_pairs",
        "minimum_estimated_cost_savings",
        "maximum_savings_ci_width",
        "quality_noninferiority_margin",
        "savings_ci_rule",
        "quality_ci_rule",
        "required_languages",
        "required_categories",
    )
)
COST_ARM_FIELDS = frozenset(
    (
        "estimated_cost_units",
        "invocations",
        "completed",
        "quality_pass",
        "critical_failure",
    )
)
COST_PROVENANCE_FIELDS = frozenset(
    (
        "fresh_blind_tasks",
        "same_sealed_tasks",
        "same_provider_runtime",
        "only_reviewed_configuration_dimensions_changed",
        "estimated_cost_contract_reviewed",
        "counterbalanced_order",
        "all_attempts_included",
        "ids_not_task_derived",
        "outside_repository_custody",
        "independent_quality_review",
    )
)


def _token_arm(value: object) -> dict[str, Any]:
    cost_arm = token_benchmark._exact_mapping(value, COST_ARM_FIELDS)
    return {
        "net_tokens": cost_arm["estimated_cost_units"],
        "invocations": cost_arm["invocations"],
        "completed": cost_arm["completed"],
        "quality_pass": cost_arm["quality_pass"],
        "critical_failure": cost_arm["critical_failure"],
    }


def _token_evidence(raw: object) -> dict[str, Any]:
    evidence = token_benchmark._exact_mapping(raw, token_benchmark.TOP_FIELDS)
    cost_gate = token_benchmark._exact_mapping(evidence["gate"], COST_GATE_FIELDS)
    cost_provenance = token_benchmark._exact_mapping(evidence["provenance"], COST_PROVENANCE_FIELDS)
    for value in cost_provenance.values():
        token_benchmark._boolean(value)
    pairs_value = evidence["pairs"]
    if not isinstance(pairs_value, list):
        raise token_benchmark.EvidenceValidationError

    token_pairs = []
    for pair_value in pairs_value:
        pair = token_benchmark._exact_mapping(pair_value, token_benchmark.PAIR_FIELDS)
        token_pairs.append(
            {
                "id": pair["id"],
                "language": pair["language"],
                "category": pair["category"],
                "expected_tier": pair["expected_tier"],
                "baseline": _token_arm(pair["baseline"]),
                "candidate": _token_arm(pair["candidate"]),
            }
        )

    return {
        "schema_version": evidence["schema_version"],
        "baseline_id": evidence["baseline_id"],
        "candidate_id": evidence["candidate_id"],
        "measurement_contract_id": evidence["measurement_contract_id"],
        "baseline_configuration_fingerprint": evidence["baseline_configuration_fingerprint"],
        "candidate_configuration_fingerprint": evidence["candidate_configuration_fingerprint"],
        "gate": {
            "minimum_pairs": cost_gate["minimum_pairs"],
            "minimum_net_token_savings": cost_gate["minimum_estimated_cost_savings"],
            "maximum_savings_ci_width": cost_gate["maximum_savings_ci_width"],
            "quality_noninferiority_margin": cost_gate["quality_noninferiority_margin"],
            "savings_ci_rule": cost_gate["savings_ci_rule"],
            "quality_ci_rule": cost_gate["quality_ci_rule"],
            "required_languages": cost_gate["required_languages"],
            "required_categories": cost_gate["required_categories"],
        },
        "provenance": {
            "fresh_blind_tasks": cost_provenance["fresh_blind_tasks"],
            "same_sealed_tasks": cost_provenance["same_sealed_tasks"],
            "same_provider_runtime_model": (
                cost_provenance["same_provider_runtime"]
                and cost_provenance["only_reviewed_configuration_dimensions_changed"]
                and cost_provenance["estimated_cost_contract_reviewed"]
            ),
            "counterbalanced_order": cost_provenance["counterbalanced_order"],
            "all_attempts_included": cost_provenance["all_attempts_included"],
            "ids_not_task_derived": cost_provenance["ids_not_task_derived"],
            "outside_repository_custody": cost_provenance["outside_repository_custody"],
            "independent_quality_review": cost_provenance["independent_quality_review"],
        },
        "pairs": token_pairs,
    }


def build_report(raw: object) -> dict[str, Any]:
    """Validate cost evidence and return a report that never infers pricing."""
    validated = token_benchmark.validate_evidence(_token_evidence(raw))
    report = token_benchmark.build_report(validated)
    report["baseline"]["estimated_cost_units"] = report["baseline"].pop("net_tokens")
    report["candidate"]["estimated_cost_units"] = report["candidate"].pop("net_tokens")
    report["estimated_cost_savings"] = report.pop("savings")
    report["gates"]["estimated_cost_savings"] = report["gates"].pop("savings")
    report["measurement"] = {
        "kind": "externally_normalized_estimated_cost",
        "pricing_inferred_by_scorer": False,
        "actual_billing_claimed": False,
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
        report = build_report(raw)
        output = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False)
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
