#!/usr/bin/env python3
"""Build reviewable Claude or Codex advisory routes from a task-free profile.

The profile contains only caller-selected opaque model and effort labels.  It
does not contain a task, command, executable path, account, credential, price,
or repository path.  Both campaign sealing and execution use this module so a
reviewed profile cannot silently compile to different argv in the two stages.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

PROFILE_SCHEMA_VERSION = 1
MAX_PROFILE_BYTES = 16_384
MAX_LABEL_BYTES = 240
ROLES = ("cheap", "advisor", "expensive")


class AdvisoryRouteError(ValueError):
    """Raised without caller-provided values when a profile is invalid."""


class AdvisoryRoutes(NamedTuple):
    cheap: tuple[str, ...]
    advisor: tuple[str, ...]
    expensive: tuple[str, ...]


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


def load_profile(path: Path) -> dict[str, object]:
    try:
        value = json.loads(
            _bounded_regular_file(path).decode("utf-8", errors="strict"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(AdvisoryRouteError()),
        )
    except (UnicodeDecodeError, ValueError, RecursionError, AdvisoryRouteError) as error:
        raise AdvisoryRouteError() from error
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "vendor",
        "models",
        "efforts",
    }:
        raise AdvisoryRouteError()
    schema_version = value["schema_version"]
    vendor = value["vendor"]
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != PROFILE_SCHEMA_VERSION
        or not isinstance(vendor, str)
        or vendor not in {"claude", "codex"}
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


def build_routes(profile: Mapping[str, object]) -> AdvisoryRoutes:
    if set(profile) != {"schema_version", "vendor", "models", "efforts"}:
        raise AdvisoryRouteError()
    schema_version = profile["schema_version"]
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != PROFILE_SCHEMA_VERSION
    ):
        raise AdvisoryRouteError()
    vendor = profile["vendor"]
    if not isinstance(vendor, str) or vendor not in {"claude", "codex"}:
        raise AdvisoryRouteError()
    models = _role_map(profile["models"])
    efforts = _role_map(profile["efforts"])

    def claude(role: str, *, advisor: bool) -> tuple[str, ...]:
        permission = "plan" if advisor else "acceptEdits"
        command: tuple[str, ...] = (
            "claude",
            "--print",
            "--no-session-persistence",
            "--safe-mode",
            "--permission-mode",
            permission,
        )
        command += ("--tools", "Read,Glob,Grep" if advisor else "Read,Edit,Glob,Grep")
        return command + (
            "--output-format",
            "json",
            "--model",
            models[role],
            "--effort",
            efforts[role],
        )

    def codex(role: str, *, advisor: bool) -> tuple[str, ...]:
        return (
            "codex",
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "read-only" if advisor else "workspace-write",
            "--json",
            "--model",
            models[role],
            "-c",
            f"model_reasoning_effort={efforts[role]}",
            "-",
        )

    builder = claude if vendor == "claude" else codex
    return AdvisoryRoutes(
        cheap=builder("cheap", advisor=False),
        advisor=builder("advisor", advisor=True),
        expensive=builder("expensive", advisor=False),
    )


def routes_from_profile(path: Path) -> AdvisoryRoutes:
    return build_routes(load_profile(path))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review", choices=("review",))
    parser.add_argument("--profile", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        profile_path = arguments.profile.expanduser()
        profile = load_profile(profile_path)
        routes = build_routes(profile)
        digest = profile_digest(profile)
    except AdvisoryRouteError:
        parser.error("invalid advisory route profile")
    print(
        json.dumps(
            {
                "schema_version": PROFILE_SCHEMA_VERSION,
                "vendor": profile["vendor"],
                "profile_sha256": digest,
                "task_delivery": "stdin",
                "task_egress": True,
                "attempt_bound": {
                    "cheap": 1,
                    "advisor": 1,
                    "advised_retry": 1,
                    "expensive": 1,
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
