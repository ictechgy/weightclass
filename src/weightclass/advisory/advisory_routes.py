#!/usr/bin/env python3
"""Build reviewable advisory routes from a task-free profile.

Schema 1 contains caller-selected opaque model and effort labels for the four
built-in CLIs. Schema 2 contains exact, bounded command matrices for arbitrary
vendors. Neither schema contains a task, account, credential, price, or
repository path. Both campaign sealing and execution use this module so a
reviewed profile cannot silently compile to different argv in the two stages.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

PROFILE_SCHEMA_VERSION = 1
CUSTOM_PROFILE_SCHEMA_VERSION = 2
MAX_PROFILE_BYTES = 16_384
MAX_LABEL_BYTES = 240
MAX_COMMANDS = 32
MAX_COMMAND_TOKEN_BYTES = 4_096
MAX_COMMAND_AGGREGATE_BYTES = 16_384
ROLES = ("cheap", "advisor", "expensive")
WORKFLOWS = ("implementation", "evidence")
TASK_PLACEHOLDER = "{{task}}"
TASK_FILE_PLACEHOLDER = "{{task_file}}"


def _closed_schema(properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def _schema_ref(name: str) -> dict[str, object]:
    return {"$ref": f"#/$defs/{name}"}


def _evidence_items(name: str, *, minimum: int, maximum: int = 128) -> dict[str, object]:
    return {
        "type": "array",
        "minItems": minimum,
        "maxItems": maximum,
        "items": _schema_ref(name),
    }


_REVIEW_FINDING = _closed_schema(
    {
        "title": _schema_ref("s"),
        "severity": {"enum": ["critical", "high", "medium", "low", "info"]},
        "confidence": {"enum": ["high", "medium", "low"]},
        "disposition": {"enum": ["reportable", "suppressed", "deferred"]},
        "locations": _schema_ref("ns"),
        "evidence": _schema_ref("ns"),
        "counterevidence": _schema_ref("ss"),
        "recommendation": _schema_ref("s"),
    }
)
_RESEARCH_CLAIM = _closed_schema(
    {
        "claim": _schema_ref("s"),
        "status": {"enum": ["supported", "mixed", "unsupported", "unresolved"]},
        "confidence": {"enum": ["high", "medium", "low"]},
        "evidence": _schema_ref("ns"),
        "counterevidence": _schema_ref("ss"),
    }
)
_DIAGNOSTIC_HYPOTHESIS = _closed_schema(
    {
        "cause": _schema_ref("s"),
        "status": {"enum": ["confirmed", "rejected", "plausible", "unresolved"]},
        "confidence": {"enum": ["high", "medium", "low"]},
        "evidence": _schema_ref("ns"),
        "counterevidence": _schema_ref("ss"),
    }
)
_DESIGN_OPTION = _closed_schema(
    {
        "title": _schema_ref("s"),
        "rationale": _schema_ref("s"),
        "evidence": _schema_ref("ns"),
        "strengths": _schema_ref("ns"),
        "risks": _schema_ref("ns"),
        "affected_surfaces": _schema_ref("ns"),
    }
)
EVIDENCE_JSON_SCHEMA = json.dumps(
    {
        "$defs": {
            "s": {"type": "string", "minLength": 1, "maxLength": 8192},
            "ss": _evidence_items("s", minimum=0, maximum=64),
            "ns": _evidence_items("s", minimum=1, maximum=64),
            "rf": _REVIEW_FINDING,
            "rc": _RESEARCH_CLAIM,
            "dh": _DIAGNOSTIC_HYPOTHESIS,
            "do": _DESIGN_OPTION,
            "rfs": _evidence_items("rf", minimum=0),
            "rcs": _evidence_items("rc", minimum=1),
            "dhs": _evidence_items("dh", minimum=1),
            "dos": _evidence_items("do", minimum=1),
        },
        "oneOf": [
            _closed_schema(
                {
                    "schema_version": {"const": 1},
                    "mode": {"const": "review"},
                    "summary": _schema_ref("s"),
                    "findings": _schema_ref("rfs"),
                    "limitations": _schema_ref("ss"),
                }
            ),
            _closed_schema(
                {
                    "schema_version": {"const": 1},
                    "mode": {"const": "research"},
                    "question": _schema_ref("s"),
                    "summary": _schema_ref("s"),
                    "claims": _schema_ref("rcs"),
                    "limitations": _schema_ref("ss"),
                }
            ),
            _closed_schema(
                {
                    "schema_version": {"const": 1},
                    "mode": {"const": "diagnosis"},
                    "symptom": _schema_ref("s"),
                    "summary": _schema_ref("s"),
                    "hypotheses": _schema_ref("dhs"),
                    "reproduction": _schema_ref("ns"),
                    "limitations": _schema_ref("ss"),
                }
            ),
            _closed_schema(
                {
                    "schema_version": {"const": 1},
                    "mode": {"const": "design"},
                    "problem": _schema_ref("s"),
                    "summary": _schema_ref("s"),
                    "principles": _schema_ref("ns"),
                    "options": _schema_ref("dos"),
                    "recommendation": _schema_ref("s"),
                    "acceptance_criteria": _schema_ref("ns"),
                    "validation": _schema_ref("ns"),
                    "limitations": _schema_ref("ss"),
                }
            ),
        ],
    },
    sort_keys=True,
    separators=(",", ":"),
)


class AdvisoryRouteError(ValueError):
    """Raised without caller-provided values when a profile is invalid."""


class AdvisoryRoutes(NamedTuple):
    cheap: tuple[str, ...]
    advisor: tuple[str, ...]
    expensive: tuple[str, ...]


def _schema_version(value: object, expected: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == expected


def _object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AdvisoryRouteError()
        result[key] = value
    return result


def _bounded_regular_file(path: Path) -> bytes:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise AdvisoryRouteError()
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise AdvisoryRouteError()
        chunks: list[bytes] = []
        remaining = MAX_PROFILE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError as error:
        raise AdvisoryRouteError() from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    payload = b"".join(chunks)
    if len(payload) > MAX_PROFILE_BYTES:
        raise AdvisoryRouteError()
    return payload


def _label(value: object) -> str:
    if not isinstance(value, str):
        raise AdvisoryRouteError()
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise AdvisoryRouteError() from error
    if (
        not 1 <= len(encoded) <= MAX_LABEL_BYTES
        or value in {TASK_PLACEHOLDER, TASK_FILE_PLACEHOLDER}
        or value.startswith("-")
        or value != value.strip()
        or any(
            unicodedata.category(character).startswith("C")
            or (character.isspace() and character != " ")
            for character in value
        )
    ):
        raise AdvisoryRouteError()
    return value


def _role_map(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != set(ROLES):
        raise AdvisoryRouteError()
    return {role: _label(value[role]) for role in ROLES}


def _command(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= MAX_COMMANDS:
        raise AdvisoryRouteError()
    command: list[str] = []
    aggregate_bytes = 0
    placeholder_count = 0
    for token in value:
        if not isinstance(token, str):
            raise AdvisoryRouteError()
        try:
            encoded = token.encode("utf-8", errors="strict")
        except UnicodeError as error:
            raise AdvisoryRouteError() from error
        if not 1 <= len(encoded) <= MAX_COMMAND_TOKEN_BYTES or any(
            unicodedata.category(character).startswith("C")
            or (character.isspace() and character != " ")
            for character in token
        ):
            raise AdvisoryRouteError()
        aggregate_bytes += len(encoded)
        if aggregate_bytes > MAX_COMMAND_AGGREGATE_BYTES:
            raise AdvisoryRouteError()
        if TASK_PLACEHOLDER in token or TASK_FILE_PLACEHOLDER in token:
            if token not in {TASK_PLACEHOLDER, TASK_FILE_PLACEHOLDER}:
                raise AdvisoryRouteError()
            placeholder_count += 1
        command.append(token)
    if placeholder_count > 1 or command[0] in {TASK_PLACEHOLDER, TASK_FILE_PLACEHOLDER}:
        raise AdvisoryRouteError()
    return tuple(command)


def _command_matrix(value: object) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, Mapping) or set(value) != set(ROLES):
        raise AdvisoryRouteError()
    return {role: _command(value[role]) for role in ROLES}


def _load_custom_profile(value: dict[str, object]) -> dict[str, object]:
    if not _schema_version(value["schema_version"], CUSTOM_PROFILE_SCHEMA_VERSION):
        raise AdvisoryRouteError()
    vendor = _label(value["vendor"])
    commands = value["commands"]
    if not isinstance(commands, Mapping) or set(commands) != set(WORKFLOWS):
        raise AdvisoryRouteError()
    return {
        "schema_version": CUSTOM_PROFILE_SCHEMA_VERSION,
        "vendor": vendor,
        "commands": {workflow: _command_matrix(commands[workflow]) for workflow in WORKFLOWS},
    }


def load_profile(path: Path) -> dict[str, object]:
    try:
        value = json.loads(
            _bounded_regular_file(path).decode("utf-8", errors="strict"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(AdvisoryRouteError()),
        )
    except (UnicodeDecodeError, ValueError, RecursionError, AdvisoryRouteError) as error:
        raise AdvisoryRouteError() from error
    if not isinstance(value, dict):
        raise AdvisoryRouteError()
    if set(value) not in (
        {"schema_version", "vendor", "models", "efforts"},
        {"schema_version", "vendor", "commands"},
    ):
        raise AdvisoryRouteError()
    schema_version = value["schema_version"]
    if _schema_version(schema_version, CUSTOM_PROFILE_SCHEMA_VERSION):
        if set(value) != {"schema_version", "vendor", "commands"}:
            raise AdvisoryRouteError()
        return _load_custom_profile(value)
    vendor = value["vendor"]
    if (
        not _schema_version(schema_version, PROFILE_SCHEMA_VERSION)
        or not isinstance(vendor, str)
        or vendor not in {"claude", "codex", "agy", "grok"}
    ):
        raise AdvisoryRouteError()
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "vendor": vendor,
        "models": _role_map(value["models"]),
        "efforts": _role_map(value["efforts"]),
    }


def profile_digest(profile: Mapping[str, object]) -> str:
    build_routes(profile)
    if profile.get("schema_version") == CUSTOM_PROFILE_SCHEMA_VERSION:
        build_routes(profile, read_only_executors=True)
    encoded = json.dumps(
        profile,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def profile_sha256(path: Path) -> str:
    return profile_digest(load_profile(path))


def build_routes(
    profile: Mapping[str, object], *, read_only_executors: bool = False
) -> AdvisoryRoutes:
    if profile.get("schema_version") == CUSTOM_PROFILE_SCHEMA_VERSION:
        if set(profile) != {"schema_version", "vendor", "commands"}:
            raise AdvisoryRouteError()
        custom = _load_custom_profile(dict(profile))
        commands = custom["commands"]
        assert isinstance(commands, dict)
        matrix = commands["implementation" if not read_only_executors else "evidence"]
        assert isinstance(matrix, dict)
        return AdvisoryRoutes(*(matrix[role] for role in ROLES))
    if set(profile) != {"schema_version", "vendor", "models", "efforts"}:
        raise AdvisoryRouteError()
    schema_version = profile["schema_version"]
    if not _schema_version(schema_version, PROFILE_SCHEMA_VERSION):
        raise AdvisoryRouteError()
    vendor = profile["vendor"]
    if not isinstance(vendor, str) or vendor not in {"claude", "codex", "agy", "grok"}:
        raise AdvisoryRouteError()
    models = _role_map(profile["models"])
    efforts = _role_map(profile["efforts"])

    def claude(role: str, *, advisor: bool) -> tuple[str, ...]:
        read_only = advisor or read_only_executors
        evidence_executor = read_only_executors and not advisor
        permission = "dontAsk" if evidence_executor else "plan" if advisor else "acceptEdits"
        command: tuple[str, ...] = (
            "claude",
            "--print",
            "--no-session-persistence",
            "--safe-mode",
            "--permission-mode",
            permission,
        )
        command += ("--tools", "Read,Glob,Grep" if read_only else "Read,Edit,Glob,Grep")
        command += (
            "--output-format",
            "json",
        )
        if evidence_executor:
            command += ("--json-schema", EVIDENCE_JSON_SCHEMA)
        return command + (
            "--model",
            models[role],
            "--effort",
            efforts[role],
        )

    def codex(role: str, *, advisor: bool) -> tuple[str, ...]:
        read_only = advisor or read_only_executors
        return (
            "codex",
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "read-only" if read_only else "workspace-write",
            "--json",
            "--model",
            models[role],
            "-c",
            f"model_reasoning_effort={json.dumps(efforts[role], ensure_ascii=False)}",
            "-",
        )

    def agy(role: str, *, advisor: bool) -> tuple[str, ...]:
        permission = "plan" if advisor or read_only_executors else "accept-edits"
        return (
            "agy",
            "--sandbox",
            "--mode",
            permission,
            "--disable-slash-commands",
            "--output-format",
            "json",
            "--model",
            models[role],
            "--effort",
            efforts[role],
            "--print",
            TASK_PLACEHOLDER,
        )

    def grok(role: str, *, advisor: bool) -> tuple[str, ...]:
        permission = "plan" if advisor or read_only_executors else "acceptEdits"
        return (
            "grok",
            "--permission-mode",
            permission,
            "--reasoning-effort",
            efforts[role],
            "--no-subagents",
            "--disable-web-search",
            "--output-format",
            "json",
            "--model",
            models[role],
            "--verbatim",
            "--prompt-file",
            TASK_FILE_PLACEHOLDER,
        )

    builder = {
        "claude": claude,
        "codex": codex,
        "agy": agy,
        "grok": grok,
    }[vendor]
    return AdvisoryRoutes(
        cheap=builder("cheap", advisor=False),
        advisor=builder("advisor", advisor=True),
        expensive=builder("expensive", advisor=False),
    )


def routes_from_profile(path: Path, *, read_only_executors: bool = False) -> AdvisoryRoutes:
    return build_routes(load_profile(path), read_only_executors=read_only_executors)


def command_task_delivery(command: Sequence[str]) -> str:
    validated = _command(command)
    if TASK_PLACEHOLDER in validated:
        return "argv"
    if TASK_FILE_PLACEHOLDER in validated:
        return "file"
    return "stdin"


def _review_task_delivery(routes: AdvisoryRoutes) -> str | dict[str, str]:
    deliveries = {role: command_task_delivery(getattr(routes, role)) for role in ROLES}
    values = set(deliveries.values())
    return next(iter(values)) if len(values) == 1 else deliveries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("review", choices=("review",))
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument(
        "--read-only-executors",
        action="store_true",
        help="compile cheap and expensive routes without repository write authority",
    )
    arguments = parser.parse_args()
    try:
        profile_path = arguments.profile.expanduser()
        profile = load_profile(profile_path)
        routes = build_routes(profile, read_only_executors=arguments.read_only_executors)
        digest = profile_digest(profile)
    except AdvisoryRouteError:
        parser.error("invalid advisory route profile")
    print(
        json.dumps(
            {
                "schema_version": profile["schema_version"],
                "vendor": profile["vendor"],
                "profile_sha256": digest,
                "task_delivery": _review_task_delivery(routes),
                "task_process_exposure": any(
                    command_task_delivery(command) == "argv" for command in routes
                ),
                "task_egress": True,
                "executor_access": (
                    "read_only" if arguments.read_only_executors else "workspace_write"
                ),
                "attempt_bound": {
                    "cheap": 1,
                    "advisor": 2,
                    "advised_retry": 1,
                    "expensive": 1,
                    "total_vendor_children": 5,
                },
                "routes": {
                    "cheap": list(routes.cheap),
                    "advisor": list(routes.advisor),
                    "expensive": list(routes.expensive),
                },
            },
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
