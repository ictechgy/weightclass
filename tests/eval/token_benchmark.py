"""Score aggregate-only paired token evidence without external side effects."""

from __future__ import annotations

import json
import math
import os
import re
import stat
import sys
from collections.abc import Sequence
from typing import Any, cast

LANGUAGES = frozenset(("en", "ko"))
CATEGORIES = frozenset(
    (
        "security",
        "privacy",
        "data-integrity",
        "destructive-work",
        "concurrency",
        "reliability",
        "performance",
        "migration",
        "routine",
    )
)
TIERS = frozenset(("low", "standard", "high"))
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z", re.ASCII)
CONFIGURATION_FINGERPRINT = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
CONSERVATIVE_95_CRITICAL = 2.045
MAX_PAIRS = 10_000
MAX_EVIDENCE_BYTES = 1_048_576
MIN_PROMOTION_PAIRS = 30
MIN_PROMOTION_NET_TOKEN_SAVINGS = 0.15
MAX_PROMOTION_QUALITY_NONINFERIORITY_MARGIN = 0.05
MAX_JSON_INTEGER_DIGITS = 100

TOP_FIELDS = frozenset(
    (
        "schema_version",
        "baseline_id",
        "candidate_id",
        "measurement_contract_id",
        "baseline_configuration_fingerprint",
        "candidate_configuration_fingerprint",
        "gate",
        "provenance",
        "pairs",
    )
)
GATE_FIELDS = frozenset(
    (
        "minimum_pairs",
        "minimum_net_token_savings",
        "maximum_savings_ci_width",
        "quality_noninferiority_margin",
        "savings_ci_rule",
        "quality_ci_rule",
        "required_languages",
        "required_categories",
    )
)
PROVENANCE_FIELDS = frozenset(
    (
        "fresh_blind_tasks",
        "same_sealed_tasks",
        "same_provider_runtime_model",
        "counterbalanced_order",
        "all_attempts_included",
        "ids_not_task_derived",
        "outside_repository_custody",
        "independent_quality_review",
    )
)
PAIR_FIELDS = frozenset(("id", "language", "category", "expected_tier", "baseline", "candidate"))
ARM_FIELDS = frozenset(
    ("net_tokens", "invocations", "completed", "quality_pass", "critical_failure")
)


class EvidenceValidationError(ValueError):
    """Evidence is malformed; diagnostics must not include supplied values."""


class DuplicateJsonFieldError(ValueError):
    """A JSON object repeats a field name; the field name remains redacted."""


def _reject_duplicate_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonFieldError
        result[key] = value
    return result


def _parse_json_integer(value: str) -> int:
    if len(value.removeprefix("-")) > MAX_JSON_INTEGER_DIGITS:
        raise EvidenceValidationError
    return int(value)


def _exact_mapping(value: object, fields: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != fields:
        raise EvidenceValidationError
    return value


def _identifier(value: object) -> str:
    if not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None:
        raise EvidenceValidationError
    return value


def _configuration_fingerprint(value: object) -> str:
    if not isinstance(value, str) or CONFIGURATION_FINGERPRINT.fullmatch(value) is None:
        raise EvidenceValidationError
    return value


def _finite_number(value: object, *, minimum: float, maximum: float | None = None) -> float:
    if type(value) not in (int, float):
        raise EvidenceValidationError
    number = float(cast(int | float, value))
    if not math.isfinite(number) or number < minimum:
        raise EvidenceValidationError
    if maximum is not None and number > maximum:
        raise EvidenceValidationError
    return number


def _positive_int(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise EvidenceValidationError
    return value


def _nonnegative_int(value: object) -> int:
    if type(value) is not int or value < 0:
        raise EvidenceValidationError
    return value


def _boolean(value: object) -> bool:
    if type(value) is not bool:
        raise EvidenceValidationError
    return value


def _vocabulary(value: object, allowed: frozenset[str]) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        raise EvidenceValidationError
    values = cast(list[str], value)
    if len(set(values)) != len(values) or any(item not in allowed for item in values):
        raise EvidenceValidationError
    return values


def _validate_arm(value: object) -> dict[str, Any]:
    arm = _exact_mapping(value, ARM_FIELDS)
    _nonnegative_int(arm["net_tokens"])
    _positive_int(arm["invocations"])
    completed = _boolean(arm["completed"])
    quality_pass = _boolean(arm["quality_pass"])
    if not completed and quality_pass:
        raise EvidenceValidationError
    _boolean(arm["critical_failure"])
    return arm


def validate_evidence(raw: object) -> dict[str, Any]:
    """Validate evidence without retaining or exposing task content."""
    evidence = _exact_mapping(raw, TOP_FIELDS)
    if type(evidence["schema_version"]) is not int or evidence["schema_version"] != 1:
        raise EvidenceValidationError

    baseline_id = _identifier(evidence["baseline_id"])
    candidate_id = _identifier(evidence["candidate_id"])
    measurement_contract_id = _identifier(evidence["measurement_contract_id"])
    if baseline_id == candidate_id:
        raise EvidenceValidationError
    baseline_configuration_fingerprint = _configuration_fingerprint(
        evidence["baseline_configuration_fingerprint"]
    )
    candidate_configuration_fingerprint = _configuration_fingerprint(
        evidence["candidate_configuration_fingerprint"]
    )
    if baseline_configuration_fingerprint == candidate_configuration_fingerprint:
        raise EvidenceValidationError

    gate = _exact_mapping(evidence["gate"], GATE_FIELDS)
    minimum_pairs = _positive_int(gate["minimum_pairs"])
    _finite_number(gate["minimum_net_token_savings"], minimum=0.0, maximum=1.0)
    _finite_number(gate["maximum_savings_ci_width"], minimum=0.0, maximum=2.0)
    if float(cast(int | float, gate["maximum_savings_ci_width"])) <= 0.0:
        raise EvidenceValidationError
    _finite_number(gate["quality_noninferiority_margin"], minimum=0.0, maximum=1.0)
    if gate["savings_ci_rule"] != "lower-bound" or gate["quality_ci_rule"] != "lower-bound":
        raise EvidenceValidationError
    required_languages = _vocabulary(gate["required_languages"], LANGUAGES)
    required_categories = _vocabulary(gate["required_categories"], CATEGORIES)

    provenance = _exact_mapping(evidence["provenance"], PROVENANCE_FIELDS)
    for field in PROVENANCE_FIELDS:
        _boolean(provenance[field])

    pairs_value = evidence["pairs"]
    if not isinstance(pairs_value, list) or not pairs_value or len(pairs_value) > MAX_PAIRS:
        raise EvidenceValidationError
    pairs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for pair_value in pairs_value:
        pair = _exact_mapping(pair_value, PAIR_FIELDS)
        pair_id = _identifier(pair["id"])
        if pair_id in seen_ids:
            raise EvidenceValidationError
        seen_ids.add(pair_id)
        if not isinstance(pair["language"], str) or pair["language"] not in LANGUAGES:
            raise EvidenceValidationError
        if not isinstance(pair["category"], str) or pair["category"] not in CATEGORIES:
            raise EvidenceValidationError
        if not isinstance(pair["expected_tier"], str) or pair["expected_tier"] not in TIERS:
            raise EvidenceValidationError
        _validate_arm(pair["baseline"])
        _validate_arm(pair["candidate"])
        pairs.append(pair)

    # Return only the already validated structure. The caller never emits IDs or pair records.
    return {
        "binding": {
            "baseline_id": baseline_id,
            "candidate_id": candidate_id,
            "measurement_contract_id": measurement_contract_id,
            "baseline_configuration_fingerprint": baseline_configuration_fingerprint,
            "candidate_configuration_fingerprint": candidate_configuration_fingerprint,
        },
        "gate": {
            "minimum_pairs": minimum_pairs,
            "minimum_net_token_savings": float(gate["minimum_net_token_savings"]),
            "maximum_savings_ci_width": float(gate["maximum_savings_ci_width"]),
            "quality_noninferiority_margin": float(gate["quality_noninferiority_margin"]),
            "required_languages": required_languages,
            "required_categories": required_categories,
        },
        "provenance": provenance,
        "pairs": pairs,
    }


def _ratio(small: int, large: int) -> float | None:
    if large <= 0:
        return None
    try:
        return (large - small) / large
    except (OverflowError, ZeroDivisionError):
        return None


def _savings_metrics(
    pairs: Sequence[dict[str, Any]],
) -> tuple[float | None, tuple[float, float] | None]:
    baseline_values = [int(pair["baseline"]["net_tokens"]) for pair in pairs]
    candidate_values = [int(pair["candidate"]["net_tokens"]) for pair in pairs]
    if any(value == 0 for value in baseline_values):
        return None, None
    baseline_total = sum(baseline_values)
    candidate_total = sum(candidate_values)
    estimate = _ratio(candidate_total, baseline_total)
    if estimate is None or len(pairs) < 2:
        return estimate, None

    leave_one_out: list[float] = []
    for baseline, candidate in zip(baseline_values, candidate_values, strict=True):
        jackknife_baseline = baseline_total - baseline
        jackknife_candidate = candidate_total - candidate
        estimate_without_pair = _ratio(jackknife_candidate, jackknife_baseline)
        if estimate_without_pair is None:
            return estimate, None
        leave_one_out.append(estimate_without_pair)
    mean = sum(leave_one_out) / len(leave_one_out)
    variance = (
        (len(leave_one_out) - 1)
        / len(leave_one_out)
        * sum((value - mean) ** 2 for value in leave_one_out)
    )
    half_width = CONSERVATIVE_95_CRITICAL * math.sqrt(max(variance, 0.0))
    return estimate, (estimate - half_width, estimate + half_width)


def _quality_metrics(
    pairs: Sequence[dict[str, Any]],
) -> tuple[float | None, tuple[float, float] | None]:
    if not pairs:
        return None, None
    improvements = sum(
        bool(pair["candidate"]["quality_pass"]) and not bool(pair["baseline"]["quality_pass"])
        for pair in pairs
    )
    regressions = sum(
        bool(pair["baseline"]["quality_pass"]) and not bool(pair["candidate"]["quality_pass"])
        for pair in pairs
    )
    count = len(pairs)
    estimate = (improvements - regressions) / count
    tail_probability = 0.025
    lower = _binomial_lower_bound(improvements, count, tail_probability) - _binomial_upper_bound(
        regressions, count, tail_probability
    )
    upper = _binomial_upper_bound(improvements, count, tail_probability) - _binomial_lower_bound(
        regressions, count, tail_probability
    )
    return estimate, (max(-1.0, lower), min(1.0, upper))


def _binomial_cdf(successes: int, trials: int, probability: float) -> float:
    if successes >= trials:
        return 1.0
    if probability <= 0.0:
        return 1.0
    if probability >= 1.0:
        return 0.0
    logarithms = [
        math.lgamma(trials + 1)
        - math.lgamma(outcome + 1)
        - math.lgamma(trials - outcome + 1)
        + outcome * math.log(probability)
        + (trials - outcome) * math.log1p(-probability)
        for outcome in range(successes + 1)
    ]
    maximum = max(logarithms)
    return min(1.0, math.exp(maximum) * sum(math.exp(value - maximum) for value in logarithms))


def _binomial_upper_bound(successes: int, trials: int, tail_probability: float) -> float:
    if successes >= trials:
        return 1.0
    if successes == 0:
        return 1.0 - math.pow(tail_probability, 1.0 / trials)
    lower = 0.0
    upper = 1.0
    for _ in range(64):
        midpoint = (lower + upper) / 2.0
        if _binomial_cdf(successes, trials, midpoint) > tail_probability:
            lower = midpoint
        else:
            upper = midpoint
    return upper


def _binomial_lower_bound(successes: int, trials: int, tail_probability: float) -> float:
    if successes == 0:
        return 0.0
    return 1.0 - _binomial_upper_bound(trials - successes, trials, tail_probability)


def _savings_are_nondegenerate(pairs: Sequence[dict[str, Any]]) -> bool:
    baseline_first = int(pairs[0]["baseline"]["net_tokens"])
    candidate_first = int(pairs[0]["candidate"]["net_tokens"])
    if baseline_first <= 0:
        return False
    savings_first = baseline_first - candidate_first
    return any(
        int(pair["baseline"]["net_tokens"]) > 0
        and (
            (int(pair["baseline"]["net_tokens"]) - int(pair["candidate"]["net_tokens"]))
            * baseline_first
            != savings_first * int(pair["baseline"]["net_tokens"])
        )
        for pair in pairs[1:]
    )


def _round(value: float | None) -> float | None:
    if value is None:
        return None
    rounded = round(value, 6)
    return 0.0 if rounded == 0 else rounded


def _arm_totals(pairs: Sequence[dict[str, Any]], arm_name: str) -> dict[str, int]:
    arms = [pair[arm_name] for pair in pairs]
    return {
        "net_tokens": sum(int(arm["net_tokens"]) for arm in arms),
        "invocations": sum(int(arm["invocations"]) for arm in arms),
        "completed_count": sum(int(bool(arm["completed"])) for arm in arms),
        "quality_pass_count": sum(int(bool(arm["quality_pass"])) for arm in arms),
        "critical_failure_count": sum(int(bool(arm["critical_failure"])) for arm in arms),
    }


def build_report(evidence: dict[str, Any]) -> dict[str, Any]:
    """Build a compact report without emitting pair IDs or records."""
    binding = evidence["binding"]
    gate = evidence["gate"]
    provenance = evidence["provenance"]
    pairs = cast(list[dict[str, Any]], evidence["pairs"])
    baseline = _arm_totals(pairs, "baseline")
    candidate = _arm_totals(pairs, "candidate")
    savings_estimate, savings_interval = _savings_metrics(pairs)
    quality_estimate, quality_interval = _quality_metrics(pairs)

    language_coverage = {
        language: sum(pair["language"] == language for pair in pairs)
        for language in sorted(LANGUAGES)
    }
    category_coverage = {
        category: sum(pair["category"] == category for pair in pairs)
        for category in sorted(CATEGORIES)
    }
    tier_coverage = {
        tier: sum(pair["expected_tier"] == tier for pair in pairs) for tier in sorted(TIERS)
    }
    both_completed = sum(
        bool(pair["baseline"]["completed"]) and bool(pair["candidate"]["completed"])
        for pair in pairs
    )
    new_critical_total = sum(
        bool(pair["candidate"]["critical_failure"])
        and not bool(pair["baseline"]["critical_failure"])
        for pair in pairs
    )
    new_critical_high = sum(
        pair["expected_tier"] == "high"
        and bool(pair["candidate"]["critical_failure"])
        and not bool(pair["baseline"]["critical_failure"])
        for pair in pairs
    )
    savings_lower = savings_interval[0] if savings_interval is not None else None
    savings_width = (
        savings_interval[1] - savings_interval[0] if savings_interval is not None else None
    )
    quality_width = (
        quality_interval[1] - quality_interval[0] if quality_interval is not None else None
    )
    savings_nondegenerate = _savings_are_nondegenerate(pairs)
    quality_nondegenerate = (
        quality_interval is not None and quality_interval[1] > quality_interval[0]
    )
    quality_lower = quality_interval[0] if quality_interval is not None else None
    required_pairs = max(gate["minimum_pairs"], MIN_PROMOTION_PAIRS)
    effective_minimum_savings = max(
        gate["minimum_net_token_savings"], MIN_PROMOTION_NET_TOKEN_SAVINGS
    )
    effective_quality_margin = min(
        gate["quality_noninferiority_margin"],
        MAX_PROMOTION_QUALITY_NONINFERIORITY_MARGIN,
    )
    effective_languages = frozenset(gate["required_languages"]).union(LANGUAGES)
    effective_categories = frozenset(gate["required_categories"]).union(CATEGORIES)
    effective_tiers = TIERS
    minimum_pairs_passes = len(pairs) >= required_pairs
    coverage_passes = (
        all(language_coverage[language] > 0 for language in effective_languages)
        and all(category_coverage[category] > 0 for category in effective_categories)
        and all(tier_coverage[tier] > 0 for tier in effective_tiers)
    )
    completion_passes = both_completed == len(pairs)
    savings_passes = (
        savings_estimate is not None
        and savings_lower is not None
        and savings_lower >= effective_minimum_savings
        and savings_width is not None
        and savings_width <= gate["maximum_savings_ci_width"]
    )
    quality_passes = (
        quality_estimate is not None
        and quality_lower is not None
        and quality_lower >= -effective_quality_margin
    )
    interval_sufficiency_passes = (
        len(pairs) >= MIN_PROMOTION_PAIRS
        and savings_width is not None
        and savings_nondegenerate
        and quality_width is not None
        and quality_nondegenerate
    )
    provenance_passes = all(bool(provenance[field]) for field in PROVENANCE_FIELDS)
    critical_passes = new_critical_total == 0
    all_gates_pass = all(
        (
            minimum_pairs_passes,
            coverage_passes,
            completion_passes,
            savings_passes,
            quality_passes,
            interval_sufficiency_passes,
            critical_passes,
            provenance_passes,
        )
    )
    return {
        "schema_version": 1,
        "binding": binding,
        "samples": {
            "pairs": len(pairs),
            "both_completed_pairs": both_completed,
            "language_coverage": language_coverage,
            "category_coverage": category_coverage,
            "tier_coverage": tier_coverage,
        },
        "baseline": baseline,
        "candidate": candidate,
        "savings": {
            "estimate": _round(savings_estimate),
            "confidence_interval_95": (
                [_round(savings_interval[0]), _round(savings_interval[1])]
                if savings_interval is not None
                else None
            ),
            "ci_width": _round(savings_width),
        },
        "quality": {
            "paired_binary_delta": _round(quality_estimate),
            "confidence_interval_95": (
                [_round(quality_interval[0]), _round(quality_interval[1])]
                if quality_interval is not None
                else None
            ),
        },
        "critical_failures": {
            "baseline_total": baseline["critical_failure_count"],
            "candidate_total": candidate["critical_failure_count"],
            "new_total": new_critical_total,
            "baseline_high": sum(
                pair["expected_tier"] == "high" and bool(pair["baseline"]["critical_failure"])
                for pair in pairs
            ),
            "candidate_high": sum(
                pair["expected_tier"] == "high" and bool(pair["candidate"]["critical_failure"])
                for pair in pairs
            ),
            "new_high": new_critical_high,
        },
        "gates": {
            "minimum_pairs": {
                "requested": gate["minimum_pairs"],
                "built_in_minimum": MIN_PROMOTION_PAIRS,
                "required": required_pairs,
                "observed": len(pairs),
                "passes": minimum_pairs_passes,
            },
            "coverage": {
                "requested_languages": sorted(gate["required_languages"]),
                "built_in_languages": sorted(LANGUAGES),
                "effective_languages": sorted(effective_languages),
                "requested_categories": sorted(gate["required_categories"]),
                "built_in_categories": sorted(CATEGORIES),
                "effective_categories": sorted(effective_categories),
                "built_in_tiers": sorted(TIERS),
                "effective_tiers": sorted(effective_tiers),
                "passes": coverage_passes,
            },
            "completion": {
                "both_completed_pairs": both_completed,
                "required_pairs": len(pairs),
                "passes": completion_passes,
            },
            "savings": {
                "requested_minimum": _round(gate["minimum_net_token_savings"]),
                "built_in_minimum": MIN_PROMOTION_NET_TOKEN_SAVINGS,
                "effective_minimum": _round(effective_minimum_savings),
                "maximum_ci_width": _round(gate["maximum_savings_ci_width"]),
                "lower_bound": _round(savings_lower),
                "ci_width": _round(savings_width),
                "passes": savings_passes,
            },
            "quality": {
                "requested_maximum_margin": _round(gate["quality_noninferiority_margin"]),
                "built_in_maximum_margin": MAX_PROMOTION_QUALITY_NONINFERIORITY_MARGIN,
                "effective_maximum_margin": _round(effective_quality_margin),
                "lower_bound": _round(quality_lower),
                "passes": quality_passes,
            },
            "interval_sufficiency": {
                "minimum_pairs": MIN_PROMOTION_PAIRS,
                "savings_interval_nondegenerate": savings_nondegenerate,
                "quality_interval_nondegenerate": quality_nondegenerate,
                "passes": interval_sufficiency_passes,
            },
            "no_new_critical_failures": {
                "new_total": new_critical_total,
                "new_high": new_critical_high,
                "passes": critical_passes,
            },
            "provenance": {"passes": provenance_passes},
            "passes": all_gates_pass,
        },
        "privacy": {
            "aggregate_only_report": True,
            "review_binding_emitted": True,
            "pair_identifiers_emitted": False,
            "task_content_emitted": False,
            "task_hashes_emitted": False,
            "per_pair_records_emitted": False,
            "raw_evidence_emitted": False,
        },
        "decision": "go" if all_gates_pass else "no-go",
    }


def _invalid() -> int:
    print("invalid evidence", file=sys.stderr)
    return 2


def _read_regular_file(path: str) -> str:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise EvidenceValidationError
    flags |= nofollow
    try:
        descriptor = os.open(path, flags)
    except (OSError, ValueError) as error:
        raise EvidenceValidationError from error

    try:
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode) or initial.st_size > MAX_EVIDENCE_BYTES:
            raise EvidenceValidationError
        content = bytearray()
        while len(content) <= MAX_EVIDENCE_BYTES:
            try:
                chunk = os.read(descriptor, min(65_536, MAX_EVIDENCE_BYTES + 1 - len(content)))
            except InterruptedError:
                continue
            if not chunk:
                break
            content.extend(chunk)
        final = os.fstat(descriptor)
        if (
            len(content) > MAX_EVIDENCE_BYTES
            or len(content) != initial.st_size
            or final.st_size != initial.st_size
            or final.st_mtime_ns != initial.st_mtime_ns
            or final.st_ctime_ns != initial.st_ctime_ns
        ):
            raise EvidenceValidationError
    except OSError as error:
        raise EvidenceValidationError from error
    finally:
        os.close(descriptor)

    try:
        return bytes(content).decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise EvidenceValidationError from error


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 2 or arguments[0] != "--evidence" or not arguments[1]:
        return _invalid()
    try:
        raw_text = _read_regular_file(arguments[1])
        raw = json.loads(
            raw_text,
            object_pairs_hook=_reject_duplicate_fields,
            parse_int=_parse_json_integer,
        )
        validated = validate_evidence(raw)
        report = build_report(validated)
        output = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (
        DuplicateJsonFieldError,
        EvidenceValidationError,
        json.JSONDecodeError,
        OverflowError,
        RecursionError,
    ):
        return _invalid()
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
