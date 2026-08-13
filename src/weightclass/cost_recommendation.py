"""Task-free cost evidence and advisory recommendation contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final, Literal, cast

from .classification import Tier
from .json_input import JsonInputError, load_json_object
from .router import Route, uses_argv_task_delivery

MAX_COST_INPUT_BYTES: Final = 262_144
MAX_COST_ROUTES: Final = 128
MIN_QUALIFIED_PAIRS: Final = 30
MIN_COST_SAVINGS_LOWER_BOUND_BASIS_POINTS: Final = 1_500
MAX_COST_SAVINGS_CI_WIDTH_BASIS_POINTS: Final = 2_000
MAX_QUALITY_NONINFERIORITY_MARGIN_BASIS_POINTS: Final = 500
REQUIRED_LANGUAGES: Final = frozenset({"en", "ko"})
REQUIRED_CATEGORIES: Final = frozenset(
    {
        "concurrency",
        "data-integrity",
        "destructive-work",
        "migration",
        "performance",
        "privacy",
        "reliability",
        "routine",
        "security",
    }
)
REQUIRED_TIERS: Final = frozenset({"low", "standard", "high"})
_FINGERPRINT_PATTERN: Final = re.compile(r"sha256:[0-9a-f]{64}")

QualificationStatus = Literal["qualified", "provisional", "unqualified"]


class CostRecommendationError(ValueError):
    """Raised without caller-controlled values when cost input is invalid."""


@dataclass(frozen=True)
class CostRouteEstimate:
    route_fingerprint: str
    expected_completed_cost_units: int


@dataclass(frozen=True)
class CostProfile:
    profile_id: str
    measurement_contract_id: str
    unit: str
    routes: tuple[CostRouteEstimate, ...]

    def canonical_value(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "profile_id": self.profile_id,
            "measurement_contract_id": self.measurement_contract_id,
            "unit": self.unit,
            "identifiers_not_task_derived": True,
            "pricing_inferred": False,
            "actual_billing_claimed": False,
            "routes": [
                {
                    "route_fingerprint": route.route_fingerprint,
                    "expected_completed_cost_units": route.expected_completed_cost_units,
                }
                for route in self.routes
            ],
        }

    def cost_for(self, route_fingerprint: str) -> int | None:
        return next(
            (
                route.expected_completed_cost_units
                for route in self.routes
                if route.route_fingerprint == route_fingerprint
            ),
            None,
        )


@dataclass(frozen=True)
class QualificationCard:
    card_id: str
    cost_profile_fingerprint: str
    measurement_contract_id: str
    vendor: str
    tier: Tier
    baseline_route_fingerprint: str
    candidate_route_fingerprint: str
    status: QualificationStatus
    sample_size: int
    cost_savings_lower_bound_basis_points: int
    cost_savings_ci_width_basis_points: int
    quality_delta_lower_bound_basis_points: int
    quality_noninferiority_margin_basis_points: int
    new_critical_failures: int
    all_attempts_included: bool
    independent_quality_review: bool
    covered_languages: frozenset[str]
    covered_categories: frozenset[str]
    covered_tiers: frozenset[str]
    valid_until: date

    def canonical_value(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "card_id": self.card_id,
            "identifiers_not_task_derived": True,
            "cost_profile_fingerprint": self.cost_profile_fingerprint,
            "measurement_contract_id": self.measurement_contract_id,
            "vendor": self.vendor,
            "tier": self.tier,
            "baseline_route_fingerprint": self.baseline_route_fingerprint,
            "candidate_route_fingerprint": self.candidate_route_fingerprint,
            "status": self.status,
            "sample_size": self.sample_size,
            "cost_savings_lower_bound_basis_points": (self.cost_savings_lower_bound_basis_points),
            "cost_savings_ci_width_basis_points": (self.cost_savings_ci_width_basis_points),
            "quality_delta_lower_bound_basis_points": (self.quality_delta_lower_bound_basis_points),
            "quality_noninferiority_margin_basis_points": (
                self.quality_noninferiority_margin_basis_points
            ),
            "new_critical_failures": self.new_critical_failures,
            "all_attempts_included": self.all_attempts_included,
            "independent_quality_review": self.independent_quality_review,
            "covered_languages": sorted(self.covered_languages),
            "covered_categories": sorted(self.covered_categories),
            "covered_tiers": sorted(self.covered_tiers),
            "valid_until": self.valid_until.isoformat(),
        }


def _exact_mapping(value: object, expected_keys: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise CostRecommendationError()
    return cast(dict[str, Any], value)


def _reviewable_label(value: object) -> str:
    if not isinstance(value, str):
        raise CostRecommendationError()
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise CostRecommendationError() from None
    if not 1 <= len(encoded) <= 128 or any(
        character.isspace() or not character.isprintable() for character in value
    ):
        raise CostRecommendationError()
    return value


def _fingerprint(value: object) -> str:
    if not isinstance(value, str) or _FINGERPRINT_PATTERN.fullmatch(value) is None:
        raise CostRecommendationError()
    return value


def _integer(value: object, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise CostRecommendationError()
    return value


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise CostRecommendationError()
    return value


def _string_set(value: object, allowed: frozenset[str]) -> frozenset[str]:
    if not isinstance(value, list) or not value:
        raise CostRecommendationError()
    parsed = tuple(_reviewable_label(item) for item in value)
    if len(set(parsed)) != len(parsed) or not set(parsed) <= allowed:
        raise CostRecommendationError()
    return frozenset(parsed)


def parse_cost_profile(value: object) -> CostProfile:
    profile = _exact_mapping(
        value,
        frozenset(
            {
                "schema_version",
                "profile_id",
                "measurement_contract_id",
                "unit",
                "identifiers_not_task_derived",
                "pricing_inferred",
                "actual_billing_claimed",
                "routes",
            }
        ),
    )
    if profile["schema_version"] != 1:
        raise CostRecommendationError()
    if not _boolean(profile["identifiers_not_task_derived"]):
        raise CostRecommendationError()
    if _boolean(profile["pricing_inferred"]) or _boolean(profile["actual_billing_claimed"]):
        raise CostRecommendationError()
    raw_routes = profile["routes"]
    if not isinstance(raw_routes, list) or not 1 <= len(raw_routes) <= MAX_COST_ROUTES:
        raise CostRecommendationError()
    routes = []
    for raw_route in raw_routes:
        route = _exact_mapping(
            raw_route,
            frozenset({"route_fingerprint", "expected_completed_cost_units"}),
        )
        routes.append(
            CostRouteEstimate(
                route_fingerprint=_fingerprint(route["route_fingerprint"]),
                expected_completed_cost_units=_integer(
                    route["expected_completed_cost_units"],
                    minimum=0,
                    maximum=2**63 - 1,
                ),
            )
        )
    if len({route.route_fingerprint for route in routes}) != len(routes):
        raise CostRecommendationError()
    return CostProfile(
        profile_id=_reviewable_label(profile["profile_id"]),
        measurement_contract_id=_reviewable_label(profile["measurement_contract_id"]),
        unit=_reviewable_label(profile["unit"]),
        routes=tuple(routes),
    )


def parse_qualification_card(value: object) -> QualificationCard:
    card = _exact_mapping(
        value,
        frozenset(
            {
                "schema_version",
                "card_id",
                "identifiers_not_task_derived",
                "cost_profile_fingerprint",
                "measurement_contract_id",
                "vendor",
                "tier",
                "baseline_route_fingerprint",
                "candidate_route_fingerprint",
                "status",
                "sample_size",
                "cost_savings_lower_bound_basis_points",
                "cost_savings_ci_width_basis_points",
                "quality_delta_lower_bound_basis_points",
                "quality_noninferiority_margin_basis_points",
                "new_critical_failures",
                "all_attempts_included",
                "independent_quality_review",
                "covered_languages",
                "covered_categories",
                "covered_tiers",
                "valid_until",
            }
        ),
    )
    if card["schema_version"] != 1:
        raise CostRecommendationError()
    if not _boolean(card["identifiers_not_task_derived"]):
        raise CostRecommendationError()
    tier = _reviewable_label(card["tier"])
    status = _reviewable_label(card["status"])
    if tier not in REQUIRED_TIERS or status not in {"qualified", "provisional", "unqualified"}:
        raise CostRecommendationError()
    valid_until_value = card["valid_until"]
    if not isinstance(valid_until_value, str):
        raise CostRecommendationError()
    try:
        valid_until = date.fromisoformat(valid_until_value)
    except ValueError:
        raise CostRecommendationError() from None
    if valid_until.isoformat() != valid_until_value:
        raise CostRecommendationError()
    return QualificationCard(
        card_id=_reviewable_label(card["card_id"]),
        cost_profile_fingerprint=_fingerprint(card["cost_profile_fingerprint"]),
        measurement_contract_id=_reviewable_label(card["measurement_contract_id"]),
        vendor=_reviewable_label(card["vendor"]),
        tier=cast(Tier, tier),
        baseline_route_fingerprint=_fingerprint(card["baseline_route_fingerprint"]),
        candidate_route_fingerprint=_fingerprint(card["candidate_route_fingerprint"]),
        status=cast(QualificationStatus, status),
        sample_size=_integer(card["sample_size"], minimum=0, maximum=1_000_000),
        cost_savings_lower_bound_basis_points=_integer(
            card["cost_savings_lower_bound_basis_points"],
            minimum=-1_000_000_000,
            maximum=10_000,
        ),
        cost_savings_ci_width_basis_points=_integer(
            card["cost_savings_ci_width_basis_points"],
            minimum=0,
            maximum=1_000_000_000,
        ),
        quality_delta_lower_bound_basis_points=_integer(
            card["quality_delta_lower_bound_basis_points"],
            minimum=-10_000,
            maximum=10_000,
        ),
        quality_noninferiority_margin_basis_points=_integer(
            card["quality_noninferiority_margin_basis_points"],
            minimum=0,
            maximum=10_000,
        ),
        new_critical_failures=_integer(card["new_critical_failures"], minimum=0, maximum=1_000_000),
        all_attempts_included=_boolean(card["all_attempts_included"]),
        independent_quality_review=_boolean(card["independent_quality_review"]),
        covered_languages=_string_set(card["covered_languages"], REQUIRED_LANGUAGES),
        covered_categories=_string_set(card["covered_categories"], REQUIRED_CATEGORIES),
        covered_tiers=_string_set(card["covered_tiers"], REQUIRED_TIERS),
        valid_until=valid_until,
    )


def load_cost_profile(path: Path) -> CostProfile:
    try:
        value = load_json_object(
            path,
            max_bytes=MAX_COST_INPUT_BYTES,
            require_exclusive_write_owner=True,
        )
    except JsonInputError:
        raise CostRecommendationError() from None
    return parse_cost_profile(value)


def load_qualification_card(path: Path) -> QualificationCard:
    try:
        value = load_json_object(
            path,
            max_bytes=MAX_COST_INPUT_BYTES,
            require_exclusive_write_owner=True,
        )
    except JsonInputError:
        raise CostRecommendationError() from None
    return parse_qualification_card(value)


def cost_profile_fingerprint(profile: CostProfile) -> str:
    encoded = json.dumps(
        profile.canonical_value(),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def qualification_card_fingerprint(card: QualificationCard) -> str:
    encoded = json.dumps(
        card.canonical_value(),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def build_cost_profile_review(profile: CostProfile) -> dict[str, object]:
    """Return task-free binding metadata for a user-supplied cost profile."""
    return {
        "schema_version": 1,
        "profile_id": profile.profile_id,
        "fingerprint": cost_profile_fingerprint(profile),
        "measurement_contract_id": profile.measurement_contract_id,
        "unit": profile.unit,
        "identifiers_not_task_derived": True,
        "pricing_inferred": False,
        "actual_billing_claimed": False,
        "values_verified_by_router": False,
        "routes": [
            {
                "route_fingerprint": route.route_fingerprint,
                "expected_completed_cost_units": route.expected_completed_cost_units,
            }
            for route in profile.routes
        ],
    }


def _capability(vendor: str) -> dict[str, bool]:
    return {
        "reviewed_effort_routing": vendor in {"agy", "claude", "codex", "grok"},
        "tier_effort_override": vendor in {"claude", "codex"},
        "tier_model_override": vendor in {"claude", "codex", "grok"},
    }


def _route_receipt(
    route: Route,
    route_fingerprint: str,
    expected_completed_cost_units: int | None,
) -> dict[str, object]:
    receipt: dict[str, object] = {
        "command": list(route.command),
        "expected_completed_cost_units": expected_completed_cost_units,
        "route": route.route_id,
        "route_fingerprint": route_fingerprint,
        "task_delivery": "argv" if uses_argv_task_delivery(route.command) else "stdin",
        "tier": route.tier,
        "vendor": route.vendor,
    }
    return receipt


def _qualification_reason(
    profile: CostProfile,
    card: QualificationCard,
    *,
    vendor: str,
    tier: Tier,
    baseline_route_fingerprint: str,
    candidate_route_fingerprint: str,
    today: date,
) -> str | None:
    if card.status != "qualified":
        return "qualification_not_qualified"
    if card.valid_until < today:
        return "qualification_expired"
    if card.cost_profile_fingerprint != cost_profile_fingerprint(profile):
        return "qualification_profile_mismatch"
    if card.measurement_contract_id != profile.measurement_contract_id:
        return "qualification_measurement_mismatch"
    if card.vendor != vendor:
        return "qualification_vendor_mismatch"
    if card.tier != tier:
        return "qualification_tier_mismatch"
    if (
        card.baseline_route_fingerprint != baseline_route_fingerprint
        or card.candidate_route_fingerprint != candidate_route_fingerprint
    ):
        return "qualification_route_mismatch"
    if card.sample_size < MIN_QUALIFIED_PAIRS:
        return "qualification_insufficient_samples"
    if card.cost_savings_lower_bound_basis_points < MIN_COST_SAVINGS_LOWER_BOUND_BASIS_POINTS:
        return "qualification_insufficient_cost_savings"
    if card.cost_savings_ci_width_basis_points > MAX_COST_SAVINGS_CI_WIDTH_BASIS_POINTS:
        return "qualification_cost_interval_too_wide"
    if (
        card.quality_noninferiority_margin_basis_points
        > MAX_QUALITY_NONINFERIORITY_MARGIN_BASIS_POINTS
        or card.quality_delta_lower_bound_basis_points
        < -card.quality_noninferiority_margin_basis_points
    ):
        return "qualification_quality_not_noninferior"
    if card.new_critical_failures:
        return "qualification_new_critical_failure"
    if not card.all_attempts_included:
        return "qualification_incomplete_attempts"
    if not card.independent_quality_review:
        return "qualification_missing_independent_quality"
    if (
        card.covered_languages != REQUIRED_LANGUAGES
        or card.covered_categories != REQUIRED_CATEGORIES
        or card.covered_tiers != REQUIRED_TIERS
    ):
        return "qualification_incomplete_coverage"
    return None


def build_recommendation_receipt(
    profile: CostProfile,
    card: QualificationCard,
    *,
    baseline_route: Route,
    baseline_route_fingerprint: str,
    candidate_route: Route,
    candidate_route_fingerprint: str,
    routing_reason_code: str,
    candidate_configuration_status: str,
    today: date | None = None,
) -> dict[str, object]:
    """Return a task-free recommendation or an explicit abstention."""
    if baseline_route.tier is None or candidate_route.tier is None:
        raise CostRecommendationError()
    if baseline_route.tier != candidate_route.tier:
        raise CostRecommendationError()
    tier = baseline_route.tier
    vendor = candidate_route.vendor
    baseline_cost = profile.cost_for(baseline_route_fingerprint)
    candidate_cost = profile.cost_for(candidate_route_fingerprint)
    reason = _qualification_reason(
        profile,
        card,
        vendor=vendor,
        tier=tier,
        baseline_route_fingerprint=baseline_route_fingerprint,
        candidate_route_fingerprint=candidate_route_fingerprint,
        today=today or date.today(),
    )
    profile_savings_basis_points: int | None = None
    if reason is None and (baseline_cost is None or candidate_cost is None):
        reason = "profile_route_cost_missing"
    if reason is None:
        assert baseline_cost is not None
        assert candidate_cost is not None
        if baseline_cost == 0:
            reason = "no_cost_advantage"
        else:
            profile_savings_basis_points = (
                (baseline_cost - candidate_cost) * 10_000 // baseline_cost
            )
            if candidate_cost >= baseline_cost:
                reason = "no_cost_advantage"
            elif card.cost_savings_lower_bound_basis_points > profile_savings_basis_points:
                reason = "qualification_cost_inconsistent"

    decision = "recommend" if reason is None else "abstain"
    reason_code = "qualified_cost_advantage" if reason is None else reason
    recommendation_semantics = {
        "schema_version": 1,
        "algorithm": "expected-completed-cost-v1",
        "baseline_route_fingerprint": baseline_route_fingerprint,
        "candidate_route_fingerprint": candidate_route_fingerprint,
        "card_id": card.card_id,
        "cost_profile_fingerprint": cost_profile_fingerprint(profile),
        "qualification_card_fingerprint": qualification_card_fingerprint(card),
        "decision": decision,
        "reason_code": reason_code,
        "tier": tier,
        "vendor": vendor,
    }
    encoded = json.dumps(
        recommendation_semantics,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    recommendation_fingerprint = f"sha256:{hashlib.sha256(encoded).hexdigest()}"
    return {
        "schema_version": 1,
        "decision": decision,
        "reason_code": reason_code,
        "objective": "expected_completed_cost",
        "fallback": "none",
        "recommendation_only": True,
        "recommendation_fingerprint": recommendation_fingerprint,
        "routing_reason_code": routing_reason_code,
        "cost_profile": {
            "actual_billing_claimed": False,
            "fingerprint": cost_profile_fingerprint(profile),
            "id": profile.profile_id,
            "measurement_contract_id": profile.measurement_contract_id,
            "pricing_inferred": False,
            "profile_cost_savings_basis_points": profile_savings_basis_points,
            "unit": profile.unit,
        },
        "qualification": {
            "all_attempts_included": card.all_attempts_included,
            "assertions_verified_by_router": False,
            "card_id": card.card_id,
            "cost_savings_ci_width_basis_points": (card.cost_savings_ci_width_basis_points),
            "cost_savings_lower_bound_basis_points": (card.cost_savings_lower_bound_basis_points),
            "fingerprint": qualification_card_fingerprint(card),
            "independent_quality_review": card.independent_quality_review,
            "identifiers_not_task_derived": True,
            "new_critical_failures": card.new_critical_failures,
            "quality_delta_lower_bound_basis_points": (card.quality_delta_lower_bound_basis_points),
            "quality_noninferiority_margin_basis_points": (
                card.quality_noninferiority_margin_basis_points
            ),
            "sample_size": card.sample_size,
            "status": card.status,
            "valid_until": card.valid_until.isoformat(),
        },
        "capability": _capability(vendor),
        "candidate_configuration_status": candidate_configuration_status,
        "baseline": _route_receipt(
            baseline_route,
            baseline_route_fingerprint,
            baseline_cost,
        ),
        "candidate": _route_receipt(
            candidate_route,
            candidate_route_fingerprint,
            candidate_cost,
        ),
    }
