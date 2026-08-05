"""Score the local classifier against a validated corpus without external calls."""

import argparse
import json
import math
import pathlib
import sys
from collections.abc import Mapping, Sequence
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from weightclass.classification import classify_task  # noqa: E402

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


class CorpusValidationError(ValueError):
    """A corpus is malformed; messages identify structure but never field values."""


def validate_corpus(
    entries: object, *, require_category: bool, require_vendor_tier: bool = False
) -> list[dict[str, Any]]:
    """Validate fields needed for aggregate scoring without exposing their values."""
    if not isinstance(entries, list) or not entries:
        raise CorpusValidationError("corpus must be a non-empty JSON array")

    validated: list[dict[str, Any]] = []
    required = ("task", "consensus", "language")
    for position, raw_entry in enumerate(entries):
        if not isinstance(raw_entry, dict):
            raise CorpusValidationError(f"entry {position}: must be an object")
        entry: dict[str, Any] = raw_entry
        for field in required:
            if field not in entry:
                raise CorpusValidationError(f"entry {position}: missing {field}")
        if not isinstance(entry["task"], str) or not entry["task"].strip():
            raise CorpusValidationError(f"entry {position}: task must be non-empty text")
        if entry["consensus"] not in TIERS:
            raise CorpusValidationError(f"entry {position}: consensus must be a supported tier")
        if entry["language"] not in LANGUAGES:
            raise CorpusValidationError(f"entry {position}: language must be a reviewed slice")
        if require_category and entry.get("category") not in CATEGORIES:
            raise CorpusValidationError(f"entry {position}: category must be a reviewed slice")
        if require_vendor_tier and entry.get("vendor_tier") not in TIERS:
            raise CorpusValidationError(f"entry {position}: vendor_tier must be a supported tier")
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
    for field, output_name in (("language", "by_language"), ("category", "by_category")):
        values = sorted({record[field] for record in records if record.get(field)})
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
    path: pathlib.Path, *, require_category: bool, require_vendor_tier: bool = False
) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CorpusValidationError("could not read corpus as UTF-8 JSON") from error
    return validate_corpus(
        raw,
        require_category=require_category,
        require_vendor_tier=require_vendor_tier,
    )


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
    arguments = parser.parse_args(argv)
    supplied = arguments.corpus is not None
    if arguments.compare_triage and not supplied:
        print(
            "error: triage comparison requires a fresh supplied corpus",
            file=sys.stderr,
        )
        return 2
    path = arguments.corpus if supplied else PUBLIC_CORPUS

    try:
        entries = _load_corpus(
            path,
            require_category=supplied,
            require_vendor_tier=arguments.compare_triage,
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
    except CorpusValidationError as error:
        print(f"error: invalid corpus: {error}", file=sys.stderr)
        return 2

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
