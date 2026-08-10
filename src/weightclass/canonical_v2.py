"""Canonical schema-2 descriptor encoding and immutable fingerprint binding."""

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Literal

from .native_v2_types import CompiledExecutionV2, FrozenCleanupV2
from .v2_validation import V2ValidationError

MAX_V2_DESCRIPTOR_BYTES = 262_144


def canonical_json_bytes_v2(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise V2ValidationError() from error


def bind_canonical_descriptor_v2(
    descriptor: Mapping[str, Any],
    *,
    argv: tuple[str, ...],
    executable: str,
    transport: Literal["native_stdin", "wcd2_stdin"],
    transport_version: Literal[1, 2],
    cleanup: FrozenCleanupV2,
) -> CompiledExecutionV2:
    payload = dict(descriptor)
    payload.pop("route_fingerprint", None)
    fingerprint_payload_bytes = canonical_json_bytes_v2(payload)
    if len(fingerprint_payload_bytes) > MAX_V2_DESCRIPTOR_BYTES:
        raise V2ValidationError()
    route_fingerprint = f"sha256:{hashlib.sha256(fingerprint_payload_bytes).hexdigest()}"
    bound = dict(payload)
    bound["route_fingerprint"] = route_fingerprint
    canonical_descriptor_bytes = canonical_json_bytes_v2(bound)
    if len(canonical_descriptor_bytes) > MAX_V2_DESCRIPTOR_BYTES:
        raise V2ValidationError()
    if not argv or argv[0] != executable:
        raise V2ValidationError()
    return CompiledExecutionV2(
        canonical_descriptor_bytes=canonical_descriptor_bytes,
        fingerprint_payload_bytes=fingerprint_payload_bytes,
        route_fingerprint=route_fingerprint,
        argv=tuple(argv),
        executable=executable,
        transport=transport,
        transport_version=transport_version,
        cleanup=cleanup,
    )
