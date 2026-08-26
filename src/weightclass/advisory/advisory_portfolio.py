#!/usr/bin/env python3
"""Render task-free status for a set of sealed advisory campaigns."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING or __package__:
    from .advisory_campaign import (
        CampaignError,
        CampaignManifest,
        CampaignProgress,
        campaign_progress,
        load_manifest,
        load_merged_lane_records,
    )
else:
    from advisory_campaign import (  # type: ignore[import-not-found]
        CampaignError,
        CampaignManifest,
        CampaignProgress,
        campaign_progress,
        load_manifest,
        load_merged_lane_records,
    )


class PortfolioError(ValueError):
    """Value-free rejection of an invalid campaign portfolio."""


class PortfolioEntry(NamedTuple):
    vendor: str
    workflow: str
    manifest: Path
    results_root: Path


MAX_PORTFOLIO_CAMPAIGNS = 64
VENDOR_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
WORKFLOWS = frozenset({"implementation", "review", "research", "diagnosis", "design"})
STAGES = ("cheap", "advice_first", "advice_failure", "retry", "expensive")
_PROFILE_NAME = re.compile(r"(?P<vendor>[a-z0-9][a-z0-9._-]{0,63})-profile\.json\Z")
_WORKFLOW_ORDER = ("implementation", "review", "research", "diagnosis", "design")
FAILURE_STAGES = frozenset(
    {
        "setup",
        "execution",
        "result",
        "handover",
        "verification",
        "verification_integrity",
        "acceptance",
        "persistence",
        "unknown",
    }
)
RESULT_SHAPES = frozenset(
    {
        "empty",
        "unstructured",
        "structured_output",
        "json_text",
        "fenced_json",
        "prose",
        "envelope_without_result",
        "malformed_envelope",
        "unknown",
    }
)


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _stage_value(stage: object, metric: str) -> tuple[bool, int | float | None]:
    """Return whether a stage ran and its one requested, independently recorded value."""
    if stage is None:
        return False, None
    if not isinstance(stage, Mapping):
        return True, None
    child = stage.get("child")
    if not isinstance(child, Mapping):
        return True, None
    if metric == "money_cost":
        usage = child.get("usage")
        if not isinstance(usage, Mapping):
            return True, None
        return True, _finite_number(usage.get("cost_usd"))
    if metric == "tokens":
        value = child.get("tokens")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return True, None
        return True, value
    value = _finite_number(child.get("seconds"))
    return True, value


def _normalized_metric(name: str, value: int | float) -> int | float:
    if name == "tokens":
        return int(value)
    if name == "latency_seconds":
        return round(float(value), 1)
    return round(float(value), 12)


def _metrics(records: Sequence[Mapping[str, object]]) -> tuple[dict[str, object], list[str]]:
    metrics: dict[str, object] = {}
    incomplete: list[str] = []
    for name in ("money_cost", "tokens", "latency_seconds"):
        by_stage: dict[str, int | float | None] = {}
        complete = True
        total: int | float = 0 if name == "tokens" else 0.0
        for stage_name in STAGES:
            stage_total: int | float = 0 if name == "tokens" else 0.0
            stage_complete = True
            for record in records:
                stage = record.get(stage_name)
                # An escalated record represents an attempted expensive stage even
                # when a damaged record omitted its payload.
                if stage_name == "expensive" and record.get("escalated") is True and stage is None:
                    stage = {}
                ran, value = _stage_value(stage, name)
                if not ran:
                    continue
                if value is None:
                    stage_complete = False
                else:
                    stage_total += value
            if stage_complete:
                normalized = _normalized_metric(name, stage_total)
                by_stage[stage_name] = normalized
                total += normalized
            else:
                by_stage[stage_name] = None
                complete = False
        metrics[name] = {
            "complete": complete,
            "total": _normalized_metric(name, total) if complete else None,
            "by_stage": by_stage,
        }
        if not complete:
            reason_name = {"money_cost": "cost", "tokens": "tokens", "latency_seconds": "latency"}[
                name
            ]
            incomplete.append(f"incomplete_{reason_name}")
    return metrics, incomplete


def _valid_entry(entry: PortfolioEntry) -> bool:
    return (
        isinstance(entry.vendor, str)
        and VENDOR_NAME.fullmatch(entry.vendor) is not None
        and isinstance(entry.workflow, str)
        and entry.workflow in WORKFLOWS
        and isinstance(entry.manifest, Path)
        and entry.manifest.is_absolute()
        and isinstance(entry.results_root, Path)
        and entry.results_root.is_absolute()
    )


def _owner_only_directory(directory: Path) -> bool:
    try:
        metadata = directory.lstat()
    except OSError:
        return False
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
        return False
    get_uid = getattr(os, "getuid", None)
    return get_uid is None or metadata.st_uid == get_uid()


def _directory_entry_kind(path: Path) -> str | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise PortfolioError() from error
    if stat.S_ISREG(metadata.st_mode):
        return "file"
    if stat.S_ISDIR(metadata.st_mode):
        return "directory"
    return "other"


def _campaign_names(vendor: str, workflow: str) -> tuple[str, str]:
    stem = vendor if workflow == "implementation" else f"{vendor}-{workflow}"
    return f"{stem}-shape-b.json", f"{stem}-results"


def discover_campaigns(directory: Path) -> tuple[PortfolioEntry, ...]:
    """Discover complete, named populations below one private directory."""

    if (
        not isinstance(directory, Path)
        or not directory.is_absolute()
        or not _owner_only_directory(directory)
    ):
        raise PortfolioError()
    try:
        children = tuple(directory.iterdir())
    except OSError as error:
        raise PortfolioError() from error

    vendors: list[str] = []
    for child in children:
        match = _PROFILE_NAME.fullmatch(child.name)
        if match is None or _directory_entry_kind(child) != "file":
            continue
        vendors.append(match.group("vendor"))

    entries: list[PortfolioEntry] = []
    for vendor in sorted(set(vendors)):
        for workflow in _WORKFLOW_ORDER:
            manifest_name, results_name = _campaign_names(vendor, workflow)
            manifest = directory / manifest_name
            results_root = directory / results_name
            manifest_kind = _directory_entry_kind(manifest)
            results_kind = _directory_entry_kind(results_root)
            if manifest_kind is None and results_kind is None:
                continue
            if manifest_kind != "file" or results_kind != "directory":
                raise PortfolioError()
            entries.append(PortfolioEntry(vendor, workflow, manifest, results_root))
    return tuple(entries)


def _accepted(value: object) -> bool:
    return isinstance(value, Mapping) and value.get("accepted") is True


def _both_failed(record: Mapping[str, object]) -> bool:
    if _accepted(record.get("cheap")):
        return False
    expensive = record.get("expensive")
    return (
        isinstance(expensive, Mapping)
        and expensive.get("failure_kind") != "infrastructure"
        and not _accepted(expensive)
    )


def _infrastructure_failure(record: Mapping[str, object]) -> bool:
    cheap = record.get("cheap")
    return isinstance(cheap, Mapping) and cheap.get("failure_kind") == "infrastructure"


def _usable_records(records: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    usable: list[Mapping[str, object]] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise PortfolioError()
        cheap = record.get("cheap")
        if not isinstance(cheap, Mapping) or not isinstance(cheap.get("accepted"), bool):
            raise PortfolioError()
        if cheap.get("failure_kind") == "infrastructure":
            continue
        expensive = record.get("expensive")
        if expensive is not None and (
            not isinstance(expensive, Mapping) or not isinstance(expensive.get("accepted"), bool)
        ):
            raise PortfolioError()
        retry = record.get("retry")
        if retry is not None and (
            not isinstance(retry, Mapping) or not isinstance(retry.get("accepted"), bool)
        ):
            raise PortfolioError()
        for stage_name in ("advice_first", "advice_failure"):
            stage = record.get(stage_name)
            if stage is not None and not isinstance(stage, Mapping):
                raise PortfolioError()
        usable.append(record)
    return usable


def _next_action(progress: CampaignProgress, abstention_reasons: Sequence[str]) -> str:
    if not abstention_reasons:
        return "run_statistical_gate"
    if any(
        reason.startswith("incomplete_") or reason == "infrastructure_failures"
        for reason in abstention_reasons
    ):
        return "repair_measurement"
    if progress.reached_cap:
        return "stop_at_cap"
    if progress.reason == "planned_tasks_not_reached":
        return "collect_tasks"
    return "collect_advised_failures"


def _campaign_status(
    vendor: str,
    workflow: str,
    manifest: CampaignManifest,
    records: Sequence[Mapping[str, object]],
    progress: CampaignProgress,
) -> dict[str, object]:
    usable = _usable_records(records)
    metrics, metric_abstentions = _metrics(records)
    infrastructure_failures = sum(1 for record in records if _infrastructure_failure(record))
    cheap_passes = sum(1 for record in usable if _accepted(record.get("cheap")))
    cheap_failures = len(usable) - cheap_passes
    advised_rescues = sum(
        1
        for record in usable
        if not _accepted(record.get("cheap")) and _accepted(record.get("retry"))
    )
    escalations = sum(1 for record in usable if record.get("escalated") is True)
    both_failed = sum(1 for record in usable if _both_failed(record))
    failure_stages: dict[str, int] = {}
    result_shapes: dict[str, int] = {}
    for record in records:
        for stage_name in ("cheap", "retry", "expensive"):
            attempt = record.get(stage_name)
            if not isinstance(attempt, Mapping):
                continue
            failure_stage = attempt.get("failure_stage")
            if isinstance(failure_stage, str):
                failure_stage = failure_stage if failure_stage in FAILURE_STAGES else "unknown"
                failure_stages[failure_stage] = failure_stages.get(failure_stage, 0) + 1
            result_shape = attempt.get("result_shape")
            if isinstance(result_shape, str):
                result_shape = result_shape if result_shape in RESULT_SHAPES else "unknown"
                result_shapes[result_shape] = result_shapes.get(result_shape, 0) + 1
    abstention_reasons = [] if progress.decision_eligible else [progress.reason]
    abstention_reasons.extend(metric_abstentions)
    if infrastructure_failures:
        abstention_reasons.append("infrastructure_failures")
    evaluate_ready = not abstention_reasons
    return {
        "vendor": vendor,
        "workflow": workflow,
        "tasks": progress.usable_tasks,
        "planned_tasks": manifest["planned_tasks"],
        "max_tasks": manifest["max_tasks"],
        "advised_failures": progress.advised_failures,
        "minimum_advised_failures": manifest["minimum_advised_failures"],
        "cheap_passes": cheap_passes,
        "cheap_failures": cheap_failures,
        "advised_rescues": advised_rescues,
        "escalations": escalations,
        "both_failed": both_failed,
        "infrastructure_failures": infrastructure_failures,
        "failure_stages": dict(sorted(failure_stages.items())),
        "result_shapes": dict(sorted(result_shapes.items())),
        "decision_eligible": progress.decision_eligible,
        "reached_cap": progress.reached_cap,
        "abstention_reason": progress.reason,
        "next_action": _next_action(progress, abstention_reasons),
        "metrics": metrics,
        "decision_state": "evaluate" if evaluate_ready else "abstain",
        "abstention_reasons": abstention_reasons,
        "policy_decision_allowed": False,
        "policy_decision_reason": (
            "statistical_gate_required" if evaluate_ready else "portfolio_abstained"
        ),
    }


def build_portfolio(entries: Sequence[PortfolioEntry]) -> dict[str, object]:
    """Load and summarize campaigns without returning their private inputs."""
    try:
        candidates = tuple(entries)
        if not 1 <= len(candidates) <= MAX_PORTFOLIO_CAMPAIGNS or any(
            not isinstance(entry, PortfolioEntry) for entry in candidates
        ):
            raise PortfolioError()
        ordered = sorted(candidates, key=lambda entry: (entry.vendor, entry.workflow))
        seen: set[tuple[str, str]] = set()
        seen_manifests: set[Path] = set()
        seen_results: set[Path] = set()
        if any(not _valid_entry(entry) for entry in ordered):
            raise PortfolioError()
        for entry in ordered:
            population = (entry.vendor, entry.workflow)
            if population in seen:
                raise PortfolioError()
            seen.add(population)
            if entry.manifest in seen_manifests or entry.results_root in seen_results:
                raise PortfolioError()
            seen_manifests.add(entry.manifest)
            seen_results.add(entry.results_root)

        campaigns: list[dict[str, object]] = []
        for entry in ordered:
            manifest = load_manifest(entry.manifest)
            records = load_merged_lane_records(manifest, entry.results_root)
            progress = campaign_progress(manifest, records)
            campaigns.append(
                _campaign_status(entry.vendor, entry.workflow, manifest, records, progress)
            )
        return {"schema_version": 1, "campaigns": campaigns}
    except PortfolioError:
        raise
    except (
        AttributeError,
        CampaignError,
        IndexError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
    ) as error:
        raise PortfolioError() from error


def _entry(value: Sequence[str]) -> PortfolioEntry:
    if len(value) != 4:
        raise PortfolioError()
    vendor, workflow, manifest, results_root = value
    entry = PortfolioEntry(vendor, workflow, Path(manifest), Path(results_root))
    if not _valid_entry(entry):
        raise PortfolioError()
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--campaign",
        action="append",
        nargs=4,
        metavar=("VENDOR", "WORKFLOW", "MANIFEST", "RESULTS"),
        help="campaign identity and private inputs; may be repeated",
    )
    source.add_argument(
        "--campaign-directory",
        metavar="DIRECTORY",
        help="discover complete named campaigns below one owner-only directory",
    )
    arguments = parser.parse_args()
    try:
        if arguments.campaign_directory is not None:
            entries = discover_campaigns(Path(arguments.campaign_directory))
        else:
            entries = tuple(_entry(value) for value in arguments.campaign)
        result = build_portfolio(entries)
    except PortfolioError:
        parser.error("invalid campaign portfolio")
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
