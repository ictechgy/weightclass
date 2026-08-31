"""Transient local triage and descriptive cross-advisor grouping."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path

if __package__:
    from . import advisory_context, readonly_snapshot
else:  # pragma: no cover - packaged direct-script boundary
    import advisory_context  # type: ignore[import-not-found,no-redef]
    import readonly_snapshot  # type: ignore[import-not-found,no-redef]

TRIAGE_POLICY_VERSION = "1"
MAX_TRIAGE_FILE_BYTES = 1 * 1024 * 1024
MAX_TRIAGE_TOTAL_BYTES = 4 * 1024 * 1024
MAX_LOCATION_LINE = 2_147_483_647
_SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
_ITEM_FIELD = {
    "review": ("findings", "title"),
    "research": ("claims", "claim"),
    "diagnosis": ("hypotheses", "cause"),
    "design": ("options", "title"),
}
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_STOP_WORDS = frozenset({"a", "an", "and", "for", "in", "of", "on", "the", "to"})


def _normalized_words(value: object) -> frozenset[str]:
    if not isinstance(value, str):
        return frozenset()
    normalized = unicodedata.normalize("NFKC", value).casefold()
    words: set[str] = set()
    for word in _WORD.findall(normalized):
        if len(word) <= 1 or word in _STOP_WORDS:
            continue
        words.add(word[:-1] if len(word) > 4 and word.endswith("s") else word)
    return frozenset(words)


def _similar(left: frozenset[str], right: frozenset[str]) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    intersection = len(left & right)
    union = len(left | right)
    return intersection >= 2 and intersection * 4 >= union * 3


def _parse_location(value: object) -> tuple[str, int] | None:
    if not isinstance(value, str) or len(value.encode("utf-8", errors="ignore")) > 4_096:
        return None
    relative, separator, line_text = value.rpartition(":")
    if separator != ":" or not line_text.isascii() or not line_text.isdecimal():
        return None
    try:
        line = int(line_text)
    except ValueError:
        return None
    if not 1 <= line <= MAX_LOCATION_LINE:
        return None
    try:
        advisory_context.validate_context_request("files", (relative,))
    except advisory_context.AdvisoryContextError:
        return None
    return relative, line


def _line_count(payload: bytes) -> int:
    if not payload:
        return 0
    return payload.count(b"\n") + (0 if payload.endswith(b"\n") else 1)


def _cites_location(evidence: str, location: str) -> bool:
    pattern = re.compile(rf"(?<![\w./:-]){re.escape(location)}(?!\d)")
    match = pattern.search(evidence)
    if match is None:
        return False
    remainder = evidence[: match.start()] + evidence[match.end() :]
    return any(character.isalnum() for character in remainder)


def _triage_annotations(
    findings: Sequence[object],
    repo: Path,
    snapshot: readonly_snapshot.TreeSnapshot,
) -> tuple[list[dict[str, object]], list[tuple[frozenset[str], frozenset[str]]]]:
    cache: dict[str, tuple[bool, int]] = {}
    expected_entries = dict(snapshot.entries)
    total_bytes = 0
    annotations: list[dict[str, object]] = []
    identities: list[tuple[frozenset[str], frozenset[str]]] = []
    for index, raw in enumerate(findings):
        finding = raw if isinstance(raw, Mapping) else {}
        raw_locations = finding.get("locations")
        locations = raw_locations if isinstance(raw_locations, list) else []
        valid_locations: set[str] = set()
        reason = "valid_locations"
        for raw_location in locations:
            parsed = _parse_location(raw_location)
            if parsed is None:
                reason = "invalid_location"
                break
            relative, line = parsed
            if relative not in cache:
                remaining = MAX_TRIAGE_TOTAL_BYTES - total_bytes
                expected = expected_entries.get(relative)
                if (
                    remaining <= 0
                    or expected is None
                    or expected.kind != "file"
                    or expected.size > min(MAX_TRIAGE_FILE_BYTES, remaining)
                ):
                    cache[relative] = (False, 0)
                else:
                    try:
                        payload = advisory_context.read_relative_regular(
                            repo,
                            relative,
                            min(MAX_TRIAGE_FILE_BYTES, remaining),
                        )
                        if (
                            expected.size != len(payload)
                            or expected.digest != hashlib.sha256(payload).digest()
                        ):
                            cache[relative] = (False, 0)
                        else:
                            total_bytes += len(payload)
                            cache[relative] = (True, _line_count(payload))
                    except advisory_context.AdvisoryContextError:
                        cache[relative] = (False, 0)
            readable, lines = cache[relative]
            if not readable:
                reason = "unreadable_location"
                break
            if line > lines:
                reason = "line_out_of_range"
                break
            valid_locations.add(f"{relative}:{line}")
        evidence = finding.get("evidence")
        cited = bool(
            valid_locations
            and isinstance(evidence, list)
            and any(
                isinstance(item, str)
                and any(_cites_location(item, location) for location in valid_locations)
                for item in evidence
            )
        )
        if reason != "valid_locations" or not valid_locations:
            triage = "rejected"
        elif cited:
            triage = "confirmed"
            reason = "location_cited"
        else:
            triage = "debatable"
            reason = "location_not_cited"
        annotations.append(
            {
                "finding_index": index,
                "triage": triage,
                "reason": reason,
                "semantic_id": "",
                "duplicate_muted": False,
                "severity_reraised": False,
            }
        )
        identities.append(
            (
                _normalized_words(finding.get("title")),
                frozenset(valid_locations),
            )
        )
    return annotations, identities


def _same_finding(
    left: tuple[frozenset[str], frozenset[str]],
    right: tuple[frozenset[str], frozenset[str]],
) -> bool:
    left_words, left_locations = left
    right_words, right_locations = right
    return (
        bool(left_words)
        and left_words == right_words
        and bool(left_locations)
        and left_locations == right_locations
    )


def triage_review(
    result: Mapping[str, object],
    repo: Path,
    snapshot: readonly_snapshot.TreeSnapshot,
) -> dict[str, object]:
    """Annotate review findings without changing or filtering model output."""

    raw_findings = result.get("findings")
    findings = raw_findings if isinstance(raw_findings, list) else []
    annotations, identities = _triage_annotations(findings, repo, snapshot)
    clusters: list[list[int]] = []
    for index, identity in enumerate(identities):
        matching = next(
            (cluster for cluster in clusters if _same_finding(identity, identities[cluster[0]])),
            None,
        )
        if matching is None:
            clusters.append([index])
        else:
            matching.append(index)
    groups: list[dict[str, object]] = []
    for number, members in enumerate(clusters, start=1):
        semantic_id = f"finding-{number}"
        representative = max(
            members,
            key=lambda index: (
                _SEVERITY_ORDER.get(
                    str(findings[index].get("severity"))
                    if isinstance(findings[index], Mapping)
                    else "",
                    -1,
                ),
                -index,
            ),
        )
        first = members[0]
        first_severity = (
            str(findings[first].get("severity")) if isinstance(findings[first], Mapping) else ""
        )
        representative_severity = (
            str(findings[representative].get("severity"))
            if isinstance(findings[representative], Mapping)
            else ""
        )
        reraised = _SEVERITY_ORDER.get(representative_severity, -1) > _SEVERITY_ORDER.get(
            first_severity, -1
        )
        for member in members:
            severity = (
                str(findings[member].get("severity"))
                if isinstance(findings[member], Mapping)
                else ""
            )
            annotations[member]["semantic_id"] = semantic_id
            annotations[member]["duplicate_muted"] = (
                member != representative and severity != "critical"
            )
            annotations[member]["severity_reraised"] = member == representative and reraised
        groups.append(
            {
                "semantic_id": semantic_id,
                "representative_index": representative,
                "member_indices": members,
                "duplicate_count": len(members) - 1,
                "severity_reraised": reraised,
            }
        )
    return {
        "policy_version": TRIAGE_POLICY_VERSION,
        "validation": "local_file_line_only",
        "semantic_identity_scope": "invocation_only",
        "persistent_state_written": False,
        "annotations": annotations,
        "groups": groups,
    }


def council_synthesis(workflow: str, members: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Group similar independent outputs descriptively, without selecting a winner."""

    field, text_field = _ITEM_FIELD[workflow]
    occurrences: list[tuple[int, int, frozenset[str], frozenset[str]]] = []
    for member_index, member in enumerate(members):
        result = member.get("result")
        items = result.get(field) if isinstance(result, Mapping) else None
        if not isinstance(items, list):
            continue
        for item_index, item in enumerate(items):
            if isinstance(item, Mapping):
                raw_locations = item.get("locations")
                locations = (
                    frozenset(value for value in raw_locations if isinstance(value, str))
                    if isinstance(raw_locations, list)
                    else frozenset()
                )
                occurrences.append(
                    (
                        member_index,
                        item_index,
                        _normalized_words(item.get(text_field)),
                        locations,
                    )
                )
    clusters: list[list[tuple[int, int, frozenset[str], frozenset[str]]]] = []
    for occurrence in occurrences:
        matching = next(
            (
                cluster
                for cluster in clusters
                if all(
                    _similar(occurrence[2], existing[2])
                    and (workflow != "review" or bool(occurrence[3] & existing[3]))
                    for existing in cluster
                )
            ),
            None,
        )
        if matching is None:
            clusters.append([occurrence])
        else:
            matching.append(occurrence)
    rendered: list[dict[str, object]] = []
    consensus_count = 0
    for number, cluster in enumerate(clusters, start=1):
        member_count = len({occurrence[0] for occurrence in cluster})
        classification = "consensus" if member_count >= 2 else "dissent"
        consensus_count += int(classification == "consensus")
        rendered.append(
            {
                "semantic_id": f"item-{number}",
                "classification": classification,
                "member_count": member_count,
                "occurrences": [
                    {"member_index": member_index, "item_index": item_index}
                    for member_index, item_index, _words, _locations in cluster
                ],
            }
        )
    return {
        "method": "deterministic_local_similarity",
        "descriptive_only": True,
        "quality_verified": False,
        "selection_performed": False,
        "dissent_preserved": True,
        "consensus_count": consensus_count,
        "dissent_count": len(rendered) - consensus_count,
        "clusters": rendered,
    }
