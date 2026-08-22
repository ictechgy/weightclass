#!/usr/bin/env python3
"""Strict transient result contracts for read-only advisory workflows."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping

EVIDENCE_SCHEMA_VERSION = 1
EVIDENCE_WORKFLOWS = frozenset({"review", "research", "diagnosis", "design"})
MAX_EVIDENCE_RESULT_BYTES = 131_072
MAX_EVIDENCE_STRING_BYTES = 8_192
MAX_EVIDENCE_ITEMS = 128
MAX_EVIDENCE_LIST_ITEMS = 64


class EvidenceResultError(ValueError):
    """Value-free rejection of malformed or unbounded model evidence."""


def _object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceResultError()
        result[key] = value
    return result


def _mapping(value: object, keys: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise EvidenceResultError()
    return value


def _string(value: object, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise EvidenceResultError()
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise EvidenceResultError() from None
    if len(encoded) > MAX_EVIDENCE_STRING_BYTES or (not allow_empty and not encoded):
        raise EvidenceResultError()
    if any(
        unicodedata.category(character).startswith("C") and character not in {"\n", "\t"}
        for character in value
    ):
        raise EvidenceResultError()
    return value


def _choice(value: object, choices: frozenset[str]) -> str:
    selected = _string(value)
    if selected not in choices:
        raise EvidenceResultError()
    return selected


def _string_list(value: object, *, minimum: int = 0) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= MAX_EVIDENCE_LIST_ITEMS:
        raise EvidenceResultError()
    return [_string(item) for item in value]


def _review_finding(value: object) -> dict[str, object]:
    item = _mapping(
        value,
        frozenset(
            {
                "title",
                "severity",
                "confidence",
                "disposition",
                "locations",
                "evidence",
                "counterevidence",
                "recommendation",
            }
        ),
    )
    return {
        "title": _string(item["title"]),
        "severity": _choice(
            item["severity"], frozenset({"critical", "high", "medium", "low", "info"})
        ),
        "confidence": _choice(item["confidence"], frozenset({"high", "medium", "low"})),
        "disposition": _choice(
            item["disposition"], frozenset({"reportable", "suppressed", "deferred"})
        ),
        "locations": _string_list(item["locations"], minimum=1),
        "evidence": _string_list(item["evidence"], minimum=1),
        "counterevidence": _string_list(item["counterevidence"]),
        "recommendation": _string(item["recommendation"]),
    }


def _research_claim(value: object) -> dict[str, object]:
    item = _mapping(
        value,
        frozenset({"claim", "status", "confidence", "evidence", "counterevidence"}),
    )
    return {
        "claim": _string(item["claim"]),
        "status": _choice(
            item["status"], frozenset({"supported", "mixed", "unsupported", "unresolved"})
        ),
        "confidence": _choice(item["confidence"], frozenset({"high", "medium", "low"})),
        "evidence": _string_list(item["evidence"], minimum=1),
        "counterevidence": _string_list(item["counterevidence"]),
    }


def _diagnostic_hypothesis(value: object) -> dict[str, object]:
    item = _mapping(
        value,
        frozenset({"cause", "status", "confidence", "evidence", "counterevidence"}),
    )
    return {
        "cause": _string(item["cause"]),
        "status": _choice(
            item["status"], frozenset({"confirmed", "rejected", "plausible", "unresolved"})
        ),
        "confidence": _choice(item["confidence"], frozenset({"high", "medium", "low"})),
        "evidence": _string_list(item["evidence"], minimum=1),
        "counterevidence": _string_list(item["counterevidence"]),
    }


def _design_option(value: object) -> dict[str, object]:
    item = _mapping(
        value,
        frozenset(
            {
                "title",
                "rationale",
                "evidence",
                "strengths",
                "risks",
                "affected_surfaces",
            }
        ),
    )
    return {
        "title": _string(item["title"]),
        "rationale": _string(item["rationale"]),
        "evidence": _string_list(item["evidence"], minimum=1),
        "strengths": _string_list(item["strengths"], minimum=1),
        "risks": _string_list(item["risks"], minimum=1),
        "affected_surfaces": _string_list(item["affected_surfaces"], minimum=1),
    }


def _items(value: object, validator: object, *, minimum: int) -> list[dict[str, object]]:
    if not isinstance(value, list) or not minimum <= len(value) <= MAX_EVIDENCE_ITEMS:
        raise EvidenceResultError()
    if not callable(validator):
        raise EvidenceResultError()
    return [validator(item) for item in value]


def parse_evidence_result(text: str, expected_mode: str) -> dict[str, object]:
    """Parse one closed mode-specific JSON result without retaining its source text."""
    if expected_mode not in EVIDENCE_WORKFLOWS or not isinstance(text, str):
        raise EvidenceResultError()
    try:
        payload = text.encode("utf-8", errors="strict")
    except UnicodeError:
        raise EvidenceResultError() from None
    if not payload or len(payload) > MAX_EVIDENCE_RESULT_BYTES:
        raise EvidenceResultError()
    try:
        raw = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(EvidenceResultError()),
        )
    except (UnicodeError, ValueError, RecursionError, EvidenceResultError):
        raise EvidenceResultError() from None

    common = {"schema_version", "mode", "summary", "limitations"}
    if expected_mode == "review":
        root = _mapping(raw, frozenset(common | {"findings"}))
        result: dict[str, object] = {
            "schema_version": _schema_version(root["schema_version"]),
            "mode": _mode(root["mode"], expected_mode),
            "summary": _string(root["summary"]),
            "findings": _items(root["findings"], _review_finding, minimum=0),
            "limitations": _string_list(root["limitations"]),
        }
    elif expected_mode == "research":
        root = _mapping(raw, frozenset(common | {"question", "claims"}))
        result = {
            "schema_version": _schema_version(root["schema_version"]),
            "mode": _mode(root["mode"], expected_mode),
            "question": _string(root["question"]),
            "summary": _string(root["summary"]),
            "claims": _items(root["claims"], _research_claim, minimum=1),
            "limitations": _string_list(root["limitations"]),
        }
    elif expected_mode == "diagnosis":
        root = _mapping(raw, frozenset(common | {"symptom", "hypotheses", "reproduction"}))
        result = {
            "schema_version": _schema_version(root["schema_version"]),
            "mode": _mode(root["mode"], expected_mode),
            "symptom": _string(root["symptom"]),
            "summary": _string(root["summary"]),
            "hypotheses": _items(root["hypotheses"], _diagnostic_hypothesis, minimum=1),
            "reproduction": _string_list(root["reproduction"], minimum=1),
            "limitations": _string_list(root["limitations"]),
        }
    elif expected_mode == "design":
        root = _mapping(
            raw,
            frozenset(
                common
                | {
                    "problem",
                    "principles",
                    "options",
                    "recommendation",
                    "acceptance_criteria",
                    "validation",
                }
            ),
        )
        result = {
            "schema_version": _schema_version(root["schema_version"]),
            "mode": _mode(root["mode"], expected_mode),
            "problem": _string(root["problem"]),
            "summary": _string(root["summary"]),
            "principles": _string_list(root["principles"], minimum=1),
            "options": _items(root["options"], _design_option, minimum=1),
            "recommendation": _string(root["recommendation"]),
            "acceptance_criteria": _string_list(root["acceptance_criteria"], minimum=1),
            "validation": _string_list(root["validation"], minimum=1),
            "limitations": _string_list(root["limitations"]),
        }
    else:  # pragma: no cover - guarded by EVIDENCE_WORKFLOWS above
        raise EvidenceResultError()
    return result


def _schema_version(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value != EVIDENCE_SCHEMA_VERSION:
        raise EvidenceResultError()
    return value


def _mode(value: object, expected: str) -> str:
    if value != expected:
        raise EvidenceResultError()
    return expected


def evidence_item_count(result: Mapping[str, object], workflow: str) -> int:
    field = {
        "review": "findings",
        "research": "claims",
        "diagnosis": "hypotheses",
        "design": "options",
    }.get(workflow)
    items = result.get(field) if field else None
    if not isinstance(items, list):
        raise EvidenceResultError()
    return len(items)


def build_evidence_prompt(task: str, workflow: str) -> str:
    """Wrap an operator task with the selected closed, read-only result contract."""
    if workflow not in EVIDENCE_WORKFLOWS or not isinstance(task, str) or not task:
        raise EvidenceResultError()
    examples: dict[str, dict[str, object]] = {
        "review": {
            "schema_version": 1,
            "mode": "review",
            "summary": "summary",
            "findings": [
                {
                    "title": "title",
                    "severity": "info",
                    "confidence": "low",
                    "disposition": "deferred",
                    "locations": ["path:line"],
                    "evidence": ["evidence"],
                    "counterevidence": [],
                    "recommendation": "recommendation",
                }
            ],
            "limitations": [],
        },
        "research": {
            "schema_version": 1,
            "mode": "research",
            "question": "question",
            "summary": "summary",
            "claims": [
                {
                    "claim": "claim",
                    "status": "unresolved",
                    "confidence": "low",
                    "evidence": ["evidence"],
                    "counterevidence": [],
                }
            ],
            "limitations": [],
        },
        "diagnosis": {
            "schema_version": 1,
            "mode": "diagnosis",
            "symptom": "symptom",
            "summary": "summary",
            "hypotheses": [
                {
                    "cause": "cause",
                    "status": "unresolved",
                    "confidence": "low",
                    "evidence": ["evidence"],
                    "counterevidence": [],
                }
            ],
            "reproduction": ["step"],
            "limitations": [],
        },
        "design": {
            "schema_version": 1,
            "mode": "design",
            "problem": "problem",
            "summary": "summary",
            "principles": ["principle"],
            "options": [
                {
                    "title": "title",
                    "rationale": "rationale",
                    "evidence": ["evidence"],
                    "strengths": ["strength"],
                    "risks": ["risk"],
                    "affected_surfaces": ["surface"],
                }
            ],
            "recommendation": "recommendation",
            "acceptance_criteria": ["criterion"],
            "validation": ["validation step"],
            "limitations": [],
        },
    }
    schema = json.dumps(examples[workflow], ensure_ascii=True, separators=(",", ":"))
    return (
        "This is a read-only advisory workflow. Do not edit, create, delete, or rename any "
        "repository file. Return exactly one JSON object and no surrounding prose. Schema "
        "example (replace placeholder content, preserve the closed keys; review findings may "
        "be empty):\n"
        f"{schema}\n\n----- OPERATOR TASK -----\n{task}\n----- END TASK -----\n"
    )
