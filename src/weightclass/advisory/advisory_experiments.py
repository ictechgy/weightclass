"""Offline, non-adaptive analysis for pre-registered advisory experiments."""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import stat
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Final

MAX_RECORD_BYTES: Final = 4_194_304
MAX_LINE_BYTES: Final = 65_536
MAX_EXPERIMENT_RECORDS: Final = 10_000
CONTEXT_CELLS: Final = ("baseline", "guard", "advisory", "guard_advisory")
MAX_AGGREGATE_INTEGER: Final = 9_007_199_254_740_991
EXPERIMENT_KEYS: Final = {
    "sequential": frozenset({"schema_version", "experiment", "accepted"}),
    "context_2x2": frozenset(
        {
            "schema_version",
            "experiment",
            "cell",
            "accepted",
            "input_tokens",
            "output_tokens",
            "elapsed_ms",
        }
    ),
    "brainstorm_generator_critic": frozenset(
        {
            "schema_version",
            "experiment",
            "baseline_compliant",
            "treatment_compliant",
            "baseline_critical_violation",
            "treatment_critical_violation",
            "baseline_diversity_bps",
            "treatment_diversity_bps",
            "baseline_duplicate_rate_bps",
            "treatment_duplicate_rate_bps",
            "preference",
            "raters_agree",
        }
    ),
    "confidence": frozenset(
        {
            "schema_version",
            "experiment",
            "predicted_probability_bps",
            "accepted",
            "abstained",
        }
    ),
}


class ExperimentInputError(ValueError):
    """Raised for malformed aggregate experiment evidence."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ExperimentInputError()
        result[key] = value
    return result


def _records(path: Path) -> list[dict[str, Any]]:
    """Load a bounded, regular JSONL file without returning input in errors."""
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_RECORD_BYTES:
            raise ExperimentInputError()
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            payload = stream.read(MAX_RECORD_BYTES + 1)
    except OSError as error:
        raise ExperimentInputError() from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(payload) > MAX_RECORD_BYTES:
        raise ExperimentInputError()
    result: list[dict[str, Any]] = []
    for index, terminated_line in enumerate(io.BytesIO(payload), start=1):
        if index > MAX_EXPERIMENT_RECORDS:
            raise ExperimentInputError()
        raw_line = terminated_line.rstrip(b"\r\n")
        if not raw_line or len(raw_line) > MAX_LINE_BYTES:
            raise ExperimentInputError()
        try:
            value = json.loads(raw_line, object_pairs_hook=_reject_duplicate_keys)
        except (
            ExperimentInputError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            RecursionError,
        ) as error:
            raise ExperimentInputError() from error
        if not isinstance(value, dict):
            raise ExperimentInputError()
        experiment = value.get("experiment")
        expected_keys = EXPERIMENT_KEYS.get(experiment) if isinstance(experiment, str) else None
        if value.get("schema_version") != 1 or expected_keys is None or set(value) != expected_keys:
            raise ExperimentInputError()
        _validate_record_fields(value)
        result.append(value)
    if not result:
        raise ExperimentInputError()
    return result


def _exact(record: dict[str, Any], keys: set[str]) -> None:
    if set(record) != keys or record.get("schema_version") != 1:
        raise ExperimentInputError()


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise ExperimentInputError()
    return value


def _bounded_integer(value: object, *, upper: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ExperimentInputError()
    effective_upper = MAX_AGGREGATE_INTEGER if upper is None else upper
    if value > effective_upper:
        raise ExperimentInputError()
    return value


def _validate_record_fields(record: dict[str, Any]) -> None:
    """Reject nested or out-of-domain values before retaining a parsed record."""
    experiment = record["experiment"]
    if experiment == "sequential":
        _boolean(record["accepted"])
        return
    if experiment == "context_2x2":
        if record["cell"] not in CONTEXT_CELLS:
            raise ExperimentInputError()
        _boolean(record["accepted"])
        _bounded_integer(record["input_tokens"])
        _bounded_integer(record["output_tokens"])
        _bounded_integer(record["elapsed_ms"])
        return
    if experiment == "brainstorm_generator_critic":
        for field in (
            "baseline_compliant",
            "treatment_compliant",
            "baseline_critical_violation",
            "treatment_critical_violation",
            "raters_agree",
        ):
            _boolean(record[field])
        for field in (
            "baseline_diversity_bps",
            "treatment_diversity_bps",
            "baseline_duplicate_rate_bps",
            "treatment_duplicate_rate_bps",
        ):
            _bounded_integer(record[field], upper=10_000)
        preference = record["preference"]
        if not isinstance(preference, str) or preference not in {"baseline", "treatment", "tie"}:
            raise ExperimentInputError()
        return
    if experiment == "confidence":
        abstained = _boolean(record["abstained"])
        if abstained:
            if record["predicted_probability_bps"] is not None or record["accepted"] is not None:
                raise ExperimentInputError()
        else:
            _bounded_integer(record["predicted_probability_bps"], upper=10_000)
            _boolean(record["accepted"])
        return
    raise ExperimentInputError()


def _rate_bps(successes: int, total: int) -> int | None:
    return round(successes * 10_000 / total) if total else None


def _mean(values: list[int]) -> int | None:
    return round(sum(values) / len(values)) if values else None


def _confidence_sequence(successes: int, total: int, alpha_bps: int) -> dict[str, int] | None:
    """A simultaneous Hoeffding interval using a summable per-look error budget."""
    if total == 0:
        return None
    alpha = alpha_bps / 10_000
    per_look_alpha = alpha / (total * (total + 1))
    radius = math.sqrt(math.log(2 / per_look_alpha) / (2 * total))
    estimate = successes / total
    return {
        "lower_bps": max(0, math.floor((estimate - radius) * 10_000)),
        "upper_bps": min(10_000, math.ceil((estimate + radius) * 10_000)),
    }


def analyze_sequential(
    records: list[dict[str, Any]],
    *,
    target_rate_bps: int,
    alpha_bps: int,
    minimum_samples: int,
    maximum_samples: int,
) -> dict[str, Any]:
    keys = {"schema_version", "experiment", "accepted"}
    outcomes: list[bool] = []
    for record in records:
        _exact(record, keys)
        if record["experiment"] != "sequential":
            raise ExperimentInputError()
        outcomes.append(_boolean(record["accepted"]))
    if len(outcomes) > maximum_samples:
        raise ExperimentInputError()
    successes = sum(outcomes)
    interval = _confidence_sequence(successes, len(outcomes), alpha_bps)
    assert interval is not None
    decision = "continue"
    if len(outcomes) >= minimum_samples:
        if interval["lower_bps"] >= target_rate_bps:
            decision = "promote"
        elif interval["upper_bps"] < target_rate_bps:
            decision = "reject"
        elif len(outcomes) == maximum_samples:
            decision = "capacity_reached"
    return {
        "schema_version": 1,
        "analysis": "sequential_acceptance",
        "method": "simultaneous_hoeffding_union_bound",
        "samples": len(outcomes),
        "accepted": successes,
        "acceptance_rate_bps": _rate_bps(successes, len(outcomes)),
        "confidence_sequence": interval,
        "alpha_bps": alpha_bps,
        "target_rate_bps": target_rate_bps,
        "minimum_samples": minimum_samples,
        "maximum_samples": maximum_samples,
        "decision": decision,
        "core_routing_changed": False,
    }


def analyze_context(records: list[dict[str, Any]]) -> dict[str, Any]:
    keys = {
        "schema_version",
        "experiment",
        "cell",
        "accepted",
        "input_tokens",
        "output_tokens",
        "elapsed_ms",
    }
    cells: dict[str, dict[str, Any]] = {
        cell: {"samples": 0, "accepted": 0, "tokens": [], "elapsed": []} for cell in CONTEXT_CELLS
    }
    for record in records:
        _exact(record, keys)
        cell = record["cell"]
        if record["experiment"] != "context_2x2" or cell not in cells:
            raise ExperimentInputError()
        accepted = _boolean(record["accepted"])
        input_tokens = _bounded_integer(record["input_tokens"])
        output_tokens = _bounded_integer(record["output_tokens"])
        elapsed_ms = _bounded_integer(record["elapsed_ms"])
        bucket = cells[cell]
        bucket["samples"] += 1
        bucket["accepted"] += int(accepted)
        bucket["tokens"].append(input_tokens + output_tokens)
        bucket["elapsed"].append(elapsed_ms)
    rendered: dict[str, Any] = {}
    for cell, bucket in cells.items():
        rendered[cell] = {
            "samples": bucket["samples"],
            "accepted": bucket["accepted"],
            "acceptance_rate_bps": _rate_bps(bucket["accepted"], bucket["samples"]),
            "mean_total_tokens": _mean(bucket["tokens"]),
            "mean_elapsed_ms": _mean(bucket["elapsed"]),
        }
    interaction: dict[str, int] | None = None
    if all(rendered[cell]["samples"] for cell in CONTEXT_CELLS):
        interaction = {
            "acceptance_rate_bps": (
                rendered["guard_advisory"]["acceptance_rate_bps"]
                - rendered["guard"]["acceptance_rate_bps"]
                - rendered["advisory"]["acceptance_rate_bps"]
                + rendered["baseline"]["acceptance_rate_bps"]
            ),
            "mean_total_tokens": (
                rendered["guard_advisory"]["mean_total_tokens"]
                - rendered["guard"]["mean_total_tokens"]
                - rendered["advisory"]["mean_total_tokens"]
                + rendered["baseline"]["mean_total_tokens"]
            ),
        }
    return {
        "schema_version": 1,
        "analysis": "context_2x2",
        "cells": rendered,
        "descriptive_interaction": interaction,
        "causal_claim": False,
        "core_routing_changed": False,
    }


def analyze_brainstorm(records: list[dict[str, Any]]) -> dict[str, Any]:
    keys = {
        "schema_version",
        "experiment",
        "baseline_compliant",
        "treatment_compliant",
        "baseline_critical_violation",
        "treatment_critical_violation",
        "baseline_diversity_bps",
        "treatment_diversity_bps",
        "baseline_duplicate_rate_bps",
        "treatment_duplicate_rate_bps",
        "preference",
        "raters_agree",
    }
    baseline_compliant = treatment_compliant = agreements = 0
    baseline_critical = treatment_critical = 0
    baseline_diversity: list[int] = []
    treatment_diversity: list[int] = []
    baseline_duplicates: list[int] = []
    treatment_duplicates: list[int] = []
    preferences = {"baseline": 0, "treatment": 0, "tie": 0}
    for record in records:
        _exact(record, keys)
        if record["experiment"] != "brainstorm_generator_critic":
            raise ExperimentInputError()
        preference = record["preference"]
        if preference not in preferences:
            raise ExperimentInputError()
        preferences[preference] += 1
        baseline_compliant += int(_boolean(record["baseline_compliant"]))
        treatment_compliant += int(_boolean(record["treatment_compliant"]))
        baseline_critical += int(_boolean(record["baseline_critical_violation"]))
        treatment_critical += int(_boolean(record["treatment_critical_violation"]))
        agreements += int(_boolean(record["raters_agree"]))
        baseline_diversity.append(_bounded_integer(record["baseline_diversity_bps"], upper=10_000))
        treatment_diversity.append(
            _bounded_integer(record["treatment_diversity_bps"], upper=10_000)
        )
        baseline_duplicates.append(
            _bounded_integer(record["baseline_duplicate_rate_bps"], upper=10_000)
        )
        treatment_duplicates.append(
            _bounded_integer(record["treatment_duplicate_rate_bps"], upper=10_000)
        )
    total = len(records)
    return {
        "schema_version": 1,
        "analysis": "brainstorm_generator_critic",
        "pairs": total,
        "preference": preferences,
        "treatment_preference_rate_bps_excluding_ties": _rate_bps(
            preferences["treatment"], preferences["baseline"] + preferences["treatment"]
        ),
        "baseline_compliance_rate_bps": _rate_bps(baseline_compliant, total),
        "treatment_compliance_rate_bps": _rate_bps(treatment_compliant, total),
        "baseline_critical_violation_rate_bps": _rate_bps(baseline_critical, total),
        "treatment_critical_violation_rate_bps": _rate_bps(treatment_critical, total),
        "baseline_mean_diversity_bps": _mean(baseline_diversity),
        "treatment_mean_diversity_bps": _mean(treatment_diversity),
        "baseline_mean_duplicate_rate_bps": _mean(baseline_duplicates),
        "treatment_mean_duplicate_rate_bps": _mean(treatment_duplicates),
        "rater_agreement_rate_bps": _rate_bps(agreements, total),
        "production_workflow_enabled": False,
        "core_routing_changed": False,
    }


def analyze_confidence(records: list[dict[str, Any]]) -> dict[str, Any]:
    keys = {
        "schema_version",
        "experiment",
        "predicted_probability_bps",
        "accepted",
        "abstained",
    }
    abstentions = 0
    evaluated = 0
    squared_error_sum = 0
    for record in records:
        _exact(record, keys)
        if record["experiment"] != "confidence":
            raise ExperimentInputError()
        abstained = _boolean(record["abstained"])
        probability = record["predicted_probability_bps"]
        accepted = record["accepted"]
        if abstained:
            if probability is not None or accepted is not None:
                raise ExperimentInputError()
            abstentions += 1
            continue
        probability = _bounded_integer(probability, upper=10_000)
        outcome = _boolean(accepted)
        squared_error_sum += (probability - int(outcome) * 10_000) ** 2
        evaluated += 1
    return {
        "schema_version": 1,
        "analysis": "confidence_calibration",
        "records": len(records),
        "evaluated": evaluated,
        "abstained": abstentions,
        "abstention_rate_bps": _rate_bps(abstentions, len(records)),
        "brier_squared_error_sum_bps2": squared_error_sum,
        "brier_denominator": evaluated * 100_000_000,
        "calibration_metrics_available": evaluated > 0,
        "calibration_claim": False,
        "core_routing_changed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wclass-advisory experiment",
        description="Analyze aggregate-only experiment JSONL without recording advisory samples.",
        allow_abbrev=False,
    )
    parser.add_argument("kind", choices=("sequential", "context-2x2", "brainstorm", "confidence"))
    parser.add_argument("--records", required=True, type=Path)
    parser.add_argument("--target-rate-bps", type=int, default=7_500)
    parser.add_argument("--alpha-bps", type=int, default=500)
    parser.add_argument("--minimum-samples", type=int, default=20)
    parser.add_argument("--maximum-samples", type=int, default=60)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    if not 1 <= arguments.alpha_bps <= 5_000:
        parser.error("--alpha-bps must be between 1 and 5000")
    if not 0 <= arguments.target_rate_bps <= 10_000:
        parser.error("--target-rate-bps must be between 0 and 10000")
    if not 1 <= arguments.minimum_samples <= arguments.maximum_samples <= MAX_EXPERIMENT_RECORDS:
        parser.error(
            f"sample bounds must satisfy 1 <= minimum <= maximum <= {MAX_EXPERIMENT_RECORDS}"
        )
    analyzers: dict[str, Callable[[list[dict[str, Any]]], dict[str, Any]]] = {
        "context-2x2": analyze_context,
        "brainstorm": analyze_brainstorm,
        "confidence": analyze_confidence,
    }
    try:
        records = _records(arguments.records)
        if arguments.kind == "sequential":
            result = analyze_sequential(
                records,
                target_rate_bps=arguments.target_rate_bps,
                alpha_bps=arguments.alpha_bps,
                minimum_samples=arguments.minimum_samples,
                maximum_samples=arguments.maximum_samples,
            )
        else:
            result = analyzers[arguments.kind](records)
    except (ExperimentInputError, OSError, ValueError, OverflowError, RecursionError):
        print(json.dumps({"error": "invalid_experiment_input"}), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
