#!/usr/bin/env python3
"""Render task-free status for a set of sealed advisory campaigns."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from tools.advisory_campaign import (
        CampaignError,
        CampaignManifest,
        CampaignProgress,
        campaign_progress,
        load_manifest,
        load_merged_lane_records,
    )
else:
    from advisory_campaign import (
        CampaignError,
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
        usable.append(record)
    return usable


def _next_action(progress: CampaignProgress) -> str:
    if progress.decision_eligible:
        return "make_decision"
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
    cheap_passes = sum(1 for record in usable if _accepted(record.get("cheap")))
    cheap_failures = len(usable) - cheap_passes
    advised_rescues = sum(
        1
        for record in usable
        if not _accepted(record.get("cheap")) and _accepted(record.get("retry"))
    )
    escalations = sum(1 for record in usable if record.get("escalated") is True)
    both_failed = sum(1 for record in usable if _both_failed(record))
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
        "decision_eligible": progress.decision_eligible,
        "reached_cap": progress.reached_cap,
        "abstention_reason": progress.reason,
        "next_action": _next_action(progress),
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--campaign",
        action="append",
        nargs=4,
        metavar=("VENDOR", "WORKFLOW", "MANIFEST", "RESULTS"),
        required=True,
        help="campaign identity and private inputs; may be repeated",
    )
    arguments = parser.parse_args()
    try:
        result = build_portfolio(tuple(_entry(value) for value in arguments.campaign))
    except PortfolioError:
        parser.error("invalid campaign portfolio")
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
