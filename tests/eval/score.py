"""Score the local classifier against a validated corpus without external calls."""

import argparse
import json
import math
import pathlib
import re
import sys
from collections.abc import Mapping, Sequence
from typing import Any, cast

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from weightclass.classification import InvalidTaskError, classify_task, validate_task  # noqa: E402

PUBLIC_CORPUS = pathlib.Path(__file__).with_name("corpus.json")
TIERS = ("low", "standard", "high")
RANK = {tier: rank for rank, tier in enumerate(TIERS)}
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
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
QUALITY_FIELDS = frozenset(
    (
        "high_tier_recall_min",
        "high_tier_recall_ci_rule",
        "over_routing_max",
        "over_routing_ci_rule",
        "slices_reviewed",
        "unexplained_slice_regression",
    )
)
RESOURCE_FIELDS = frozenset(
    (
        "startup_accepted",
        "latency_accepted",
        "memory_accepted",
        "supported_platform_determinism_accepted",
    )
)
SUPPLY_CHAIN_FIELDS = frozenset(
    (
        "dependency_pin_reviewed",
        "dependency_audit_accepted",
        "model_download_required",
        "maintenance_cost_accepted",
    )
)


class CorpusValidationError(ValueError):
    """A corpus is malformed; messages identify structure but never field values."""


class CandidateValidationError(ValueError):
    """Candidate evidence is malformed; messages never include supplied values."""


class DuplicateJsonFieldError(ValueError):
    """A JSON object repeats a field name; the field itself remains redacted."""


def _reject_duplicate_json_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonFieldError
        result[key] = value
    return result


def _require_exact_fields(value: object, fields: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != fields:
        raise CandidateValidationError(f"{label} must contain exactly the documented fields")
    return value


def _require_boolean_fields(value: object, fields: frozenset[str], label: str) -> dict[str, bool]:
    mapping = _require_exact_fields(value, fields, label)
    if any(type(mapping[field]) is not bool for field in fields):
        raise CandidateValidationError(f"{label} fields must be booleans")
    return mapping


def validate_candidate(raw: object, *, corpus: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate bounded, output-safe candidate predictions and gate metadata."""
    fields = frozenset(
        (
            "schema_version",
            "candidate_id",
            "baseline_id",
            "predictions",
            "quality_gate",
            "resource_gate",
            "supply_chain_gate",
        )
    )
    candidate = _require_exact_fields(raw, fields, "candidate")
    if type(candidate["schema_version"]) is not int or candidate["schema_version"] != 1:
        raise CandidateValidationError("schema_version must be 1")
    for field in ("candidate_id", "baseline_id"):
        if not isinstance(candidate[field], str) or not IDENTIFIER.fullmatch(candidate[field]):
            raise CandidateValidationError(f"{field} must be a reviewed opaque identifier")
    if candidate["candidate_id"] == candidate["baseline_id"]:
        raise CandidateValidationError("candidate_id and baseline_id must differ")

    predictions = candidate["predictions"]
    if not isinstance(predictions, list):
        raise CandidateValidationError("predictions must be an array")
    if len(predictions) != len(corpus):
        raise CandidateValidationError("prediction count does not match corpus")
    prediction_fields = frozenset(("id", "label", "prediction"))
    corpus_ids = {entry["id"] for entry in corpus}
    seen_ids: set[str] = set()
    for position, prediction in enumerate(predictions):
        record = _require_exact_fields(prediction, prediction_fields, f"prediction {position}")
        identifier = record["id"]
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier):
            raise CandidateValidationError(
                f"prediction {position} id must be a reviewed opaque identifier"
            )
        if identifier in seen_ids:
            raise CandidateValidationError(f"prediction {position} id is duplicated")
        if identifier not in corpus_ids:
            raise CandidateValidationError(f"prediction {position} id is unknown")
        if (
            not isinstance(record["label"], str)
            or record["label"] not in TIERS
            or record["label"] != corpus[position]["consensus"]
        ):
            raise CandidateValidationError(f"prediction {position} label does not match corpus")
        if not isinstance(record["prediction"], str) or record["prediction"] not in TIERS:
            raise CandidateValidationError(
                f"prediction {position} prediction must be a supported tier"
            )
        if identifier != corpus[position]["id"]:
            raise CandidateValidationError(f"prediction {position} id is out of order")
        seen_ids.add(identifier)

    quality = _require_exact_fields(candidate["quality_gate"], QUALITY_FIELDS, "quality_gate")
    for field in ("high_tier_recall_min", "over_routing_max"):
        threshold = quality[field]
        if type(threshold) not in (int, float) or (
            isinstance(threshold, float) and not math.isfinite(threshold)
        ):
            raise CandidateValidationError(f"quality_gate {field} must be a finite number")
        if not 0 <= threshold <= 1:
            raise CandidateValidationError(f"quality_gate {field} must be between zero and one")
    if quality["high_tier_recall_ci_rule"] != "lower-bound":
        raise CandidateValidationError("high_tier_recall_ci_rule must be lower-bound")
    if quality["over_routing_ci_rule"] != "upper-bound":
        raise CandidateValidationError("over_routing_ci_rule must be upper-bound")
    for field in ("slices_reviewed", "unexplained_slice_regression"):
        if type(quality[field]) is not bool:
            raise CandidateValidationError(f"quality_gate {field} must be boolean")

    _require_boolean_fields(candidate["resource_gate"], RESOURCE_FIELDS, "resource_gate")
    _require_boolean_fields(
        candidate["supply_chain_gate"], SUPPLY_CHAIN_FIELDS, "supply_chain_gate"
    )
    return candidate


def validate_corpus(
    entries: object,
    *,
    require_category: bool,
    require_vendor_tier: bool = False,
    require_id: bool = False,
) -> list[dict[str, Any]]:
    """Validate fields needed for aggregate scoring without exposing their values."""
    if not isinstance(entries, list) or not entries:
        raise CorpusValidationError("corpus must be a non-empty JSON array")

    validated: list[dict[str, Any]] = []
    required = ("task", "consensus", "language")
    seen_ids: set[str] = set()
    for position, raw_entry in enumerate(entries):
        if not isinstance(raw_entry, dict):
            raise CorpusValidationError(f"entry {position}: must be an object")
        entry: dict[str, Any] = raw_entry
        for field in required:
            if field not in entry:
                raise CorpusValidationError(f"entry {position}: missing {field}")
        task = entry["task"]
        if not isinstance(task, str) or not task.strip():
            raise CorpusValidationError(f"entry {position}: task must be non-empty text")
        try:
            validate_task(task)
        except InvalidTaskError as error:
            raise CorpusValidationError(
                f"entry {position}: task is outside classifier limits"
            ) from error
        consensus = entry["consensus"]
        if not isinstance(consensus, str) or consensus not in TIERS:
            raise CorpusValidationError(f"entry {position}: consensus must be a supported tier")
        language = entry["language"]
        if not isinstance(language, str) or language not in LANGUAGES:
            raise CorpusValidationError(f"entry {position}: language must be a reviewed slice")
        if require_category:
            category = entry.get("category")
            if not isinstance(category, str) or category not in CATEGORIES:
                raise CorpusValidationError(f"entry {position}: category must be a reviewed slice")
        if require_id:
            identifier = entry.get("id")
            if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier):
                raise CorpusValidationError(
                    f"entry {position}: id must be a reviewed opaque identifier"
                )
            if identifier in seen_ids:
                raise CorpusValidationError(f"entry {position}: id must be unique")
            seen_ids.add(identifier)
        if require_vendor_tier:
            vendor_tier = entry.get("vendor_tier")
            if not isinstance(vendor_tier, str) or vendor_tier not in TIERS:
                raise CorpusValidationError(
                    f"entry {position}: vendor_tier must be a supported tier"
                )
        validated.append(entry)
    return validated


def _rate(count: int, total: int) -> dict[str, Any]:
    proportion = count / total if total else 0.0
    if not total:
        interval = (0.0, 0.0)
    else:
        z = 1.96
        denominator = 1 + z * z / total
        centre = (proportion + z * z / (2 * total)) / denominator
        margin = (
            z
            * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
            / denominator
        )
        interval = (max(0.0, centre - margin), min(1.0, centre + margin))
    return {
        "count": count,
        "total": total,
        "rate": proportion,
        "confidence_interval_95": interval,
    }


def _summary(records: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    total = len(records)
    agreed = sum(record["expected"] == record["predicted"] for record in records)
    high = [record for record in records if record["expected"] == "high"]
    high_found = sum(record["predicted"] == "high" for record in high)
    over = sum(RANK[record["predicted"]] > RANK[record["expected"]] for record in records)
    return {
        "total": total,
        "agreement": _rate(agreed, total),
        "high_recall": _rate(high_found, len(high)),
        "over_routing": _rate(over, total),
    }


def aggregate_metrics(records: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    """Return aggregate quality metrics and aggregate language/category slices."""
    matrix = {expected: {predicted: 0 for predicted in TIERS} for expected in TIERS}
    for record in records:
        matrix[record["expected"]][record["predicted"]] += 1

    result = _summary(records)
    result["confusion_matrix"] = matrix
    for field, output_name, values in (
        ("language", "by_language", sorted(LANGUAGES)),
        ("category", "by_category", sorted(CATEGORIES)),
    ):
        result[output_name] = {
            value: _summary([record for record in records if record.get(field) == value])
            for value in values
        }
    return result


def _format_rate(metric: Mapping[str, Any]) -> str:
    low, high = metric["confidence_interval_95"]
    return (
        f"{metric['count']}/{metric['total']} ({metric['rate'] * 100:.1f}%; "
        f"95% CI {low * 100:.1f}–{high * 100:.1f}%)"
    )


def render_report(label: str, metrics: Mapping[str, Any]) -> None:
    print(label)
    print(f"  agreement        {_format_rate(metrics['agreement'])}")
    print(f"  high-tier recall {_format_rate(metrics['high_recall'])}")
    print(f"  over-routing     {_format_rate(metrics['over_routing'])}")
    print("  confusion matrix (expected rows, predicted columns: low standard high)")
    for expected in TIERS:
        row = metrics["confusion_matrix"][expected]
        print(f"    {expected:<8} {row['low']} {row['standard']} {row['high']}")
    for heading, key in (("language slices", "by_language"), ("category slices", "by_category")):
        print(f"  {heading}")
        slices = metrics[key]
        if not slices:
            print("    unavailable")
        for name, summary in slices.items():
            agreement = summary["agreement"]["rate"] * 100
            high_recall = summary["high_recall"]["rate"] * 100
            over_routing = summary["over_routing"]["rate"] * 100
            print(
                f"    {name}: n={summary['total']}, agreement={agreement:.1f}%, "
                f"high-recall={high_recall:.1f}%, over-routing={over_routing:.1f}%"
            )


def _load_corpus(
    path: pathlib.Path,
    *,
    require_category: bool,
    require_vendor_tier: bool = False,
    require_id: bool = False,
) -> list[dict[str, Any]]:
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_fields,
        )
    except DuplicateJsonFieldError as error:
        raise CorpusValidationError("corpus contains duplicate object fields") from error
    except (OSError, ValueError) as error:
        raise CorpusValidationError("could not read corpus as UTF-8 JSON") from error
    return validate_corpus(
        raw,
        require_category=require_category,
        require_vendor_tier=require_vendor_tier,
        require_id=require_id,
    )


def _load_candidate(path: pathlib.Path, *, corpus: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_fields,
        )
    except DuplicateJsonFieldError as error:
        raise CandidateValidationError("candidate contains duplicate object fields") from error
    except (OSError, ValueError) as error:
        raise CandidateValidationError("could not read candidate as UTF-8 JSON") from error
    return validate_candidate(raw, corpus=corpus)


def _rounded(value: Any) -> Any:
    """Normalize floating-point evidence for stable machine-readable output."""
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, tuple):
        return [_rounded(item) for item in value]
    if isinstance(value, list):
        return [_rounded(item) for item in value]
    if isinstance(value, dict):
        return {key: _rounded(item) for key, item in value.items()}
    return value


def candidate_report(
    candidate: Mapping[str, Any],
    metrics: Mapping[str, Any],
    *,
    baseline_metrics: Mapping[str, Any],
    corpus_size: int,
    candidate_below_baseline_count: int,
) -> dict[str, Any]:
    """Build an aggregate-only decision record from predeclared candidate gates."""
    quality = candidate["quality_gate"]
    high_recall = metrics["high_recall"]
    over_routing = metrics["over_routing"]
    high_passes = (
        high_recall["total"] > 0
        and high_recall["confidence_interval_95"][0] >= quality["high_tier_recall_min"]
    )
    over_passes = over_routing["confidence_interval_95"][1] <= quality["over_routing_max"]
    raise_only_passes = candidate_below_baseline_count == 0
    quality_passes = (
        high_passes
        and over_passes
        and quality["slices_reviewed"]
        and not quality["unexplained_slice_regression"]
    )
    resource_passes = all(candidate["resource_gate"].values())
    supply_chain = candidate["supply_chain_gate"]
    supply_chain_passes = (
        supply_chain["dependency_pin_reviewed"]
        and supply_chain["dependency_audit_accepted"]
        and not supply_chain["model_download_required"]
        and supply_chain["maintenance_cost_accepted"]
    )
    report = {
        "schema_version": 1,
        "baseline_metrics": baseline_metrics,
        "corpus": {
            "entries": corpus_size,
            "evaluator_supplied": True,
            "freshness_verified_by_scorer": False,
        },
        "quality_gate": {
            "high_tier_recall": {
                **high_recall,
                "minimum": quality["high_tier_recall_min"],
                "ci_rule": quality["high_tier_recall_ci_rule"],
                "passes": high_passes,
            },
            "over_routing": {
                **over_routing,
                "maximum": quality["over_routing_max"],
                "ci_rule": quality["over_routing_ci_rule"],
                "passes": over_passes,
            },
            "confusion_matrix": metrics["confusion_matrix"],
            "by_language": metrics["by_language"],
            "by_category": metrics["by_category"],
            "slices_reviewed": quality["slices_reviewed"],
            "unexplained_slice_regression": quality["unexplained_slice_regression"],
            "passes": quality_passes,
        },
        "comparison_gate": {
            "candidate_below_baseline_count": candidate_below_baseline_count,
            "high_tier_recall_rate_delta": (
                high_recall["rate"] - baseline_metrics["high_recall"]["rate"]
            ),
            "over_routing_rate_delta": (
                over_routing["rate"] - baseline_metrics["over_routing"]["rate"]
            ),
            "raise_only_required": True,
            "passes": raise_only_passes,
        },
        "resource_gate": {**candidate["resource_gate"], "passes": resource_passes},
        "supply_chain_gate": {**supply_chain, "passes": supply_chain_passes},
        "privacy_gate": {
            "aggregate_only_report": True,
            "candidate_and_baseline_identifiers_emitted": False,
            "corpus_task_field_or_per_task_record_emitted": False,
        },
        "decision": (
            "go"
            if quality_passes and raise_only_passes and resource_passes and supply_chain_passes
            else "no-go"
        ),
    }
    return cast(dict[str, Any], _rounded(report))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        type=pathlib.Path,
        help="fresh synthetic blind corpus (JSON); omitted means the public regression fixture",
    )
    parser.add_argument(
        "--compare-triage",
        action="store_true",
        help="offline comparison using vendor_tier values already present in a fresh corpus",
    )
    parser.add_argument(
        "--candidate",
        type=pathlib.Path,
        help="offline candidate predictions and predeclared gate evidence (JSON)",
    )
    arguments = parser.parse_args(argv)
    supplied = arguments.corpus is not None
    if (arguments.compare_triage or arguments.candidate) and not supplied:
        print(
            "error: comparison requires a fresh supplied corpus",
            file=sys.stderr,
        )
        return 2
    if arguments.compare_triage and arguments.candidate:
        print("error: select exactly one comparison mode", file=sys.stderr)
        return 2
    path = arguments.corpus if supplied else PUBLIC_CORPUS

    try:
        if arguments.candidate:
            try:
                is_public_fixture = path.samefile(PUBLIC_CORPUS)
            except (OSError, RuntimeError) as error:
                raise CorpusValidationError("could not validate corpus path") from error
            if is_public_fixture:
                raise CorpusValidationError(
                    "public regression fixture cannot be used in candidate mode"
                )
        entries = _load_corpus(
            path,
            require_category=supplied,
            require_vendor_tier=arguments.compare_triage,
            require_id=arguments.candidate is not None,
        )
        records = [
            {
                "expected": entry["consensus"],
                "predicted": classify_task(entry["task"]),
                "language": entry["language"],
                "category": entry.get("category", ""),
            }
            for entry in entries
        ]
        candidate = (
            _load_candidate(arguments.candidate, corpus=entries) if arguments.candidate else None
        )
    except (CorpusValidationError, CandidateValidationError) as error:
        print(f"error: invalid corpus: {error}", file=sys.stderr)
        return 2

    if candidate is not None:
        candidate_records = [
            {**record, "predicted": prediction["prediction"]}
            for record, prediction in zip(records, candidate["predictions"], strict=True)
        ]
        candidate_below_baseline_count = sum(
            RANK[candidate_record["predicted"]] < RANK[baseline_record["predicted"]]
            for candidate_record, baseline_record in zip(candidate_records, records, strict=True)
        )
        print(
            json.dumps(
                candidate_report(
                    candidate,
                    aggregate_metrics(candidate_records),
                    baseline_metrics=aggregate_metrics(records),
                    corpus_size=len(entries),
                    candidate_below_baseline_count=candidate_below_baseline_count,
                ),
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0

    kind = "synthetic blind corpus" if supplied else "public regression fixture"
    print(f"corpus kind: {kind}; entries: {len(entries)}")
    render_report("local classifier", aggregate_metrics(records))
    if arguments.compare_triage:
        vendor_records = [
            {**record, "predicted": entry["vendor_tier"]}
            for record, entry in zip(records, entries, strict=True)
        ]
        raise_only_records = [
            {
                **record,
                "predicted": max((record["predicted"], entry["vendor_tier"]), key=RANK.__getitem__),
            }
            for record, entry in zip(records, entries, strict=True)
        ]
        render_report("vendor-only experiment", aggregate_metrics(vendor_records))
        render_report("raise-only experiment", aggregate_metrics(raise_only_records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
