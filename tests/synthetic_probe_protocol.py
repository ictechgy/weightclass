"""Test-only synthetic probe manifest contract.

This module is deliberately outside ``src``. Its manifests describe bounded
self-tests, not qualification claims, candidate records, or delegation support.
"""

import json
import re
from collections.abc import Iterable, Mapping
from typing import Final, cast

PROBE_MANIFEST_SCHEMA_VERSION: Final = 1
PROBE_PROTOCOL_ID: Final = "weightclass.synthetic-probe-v1"
MAX_MANIFEST_BYTES: Final = 16_384
MAX_SELF_TESTS: Final = 16
MAX_IDENTIFIER_BYTES: Final = 96

PROBE_SELF_TEST_IDS: Final = (
    "wcp-selftest/v1/child-start",
    "wcp-selftest/v1/direct-child-exit",
    "wcp-selftest/v1/runner-deadline",
    "wcp-selftest/v1/runner-framed-fd",
    "wcp-selftest/v1/selected-argv",
)

_SELF_TEST_PATTERN: Final = re.compile(r"wcp-selftest/v1/[a-z][a-z0-9-]*\Z")
_TOP_LEVEL_FIELDS: Final = {
    "delegation_support",
    "probe_manifest_schema_version",
    "probe_protocol_id",
    "provenance",
    "qualification_eligible",
    "self_tests",
}
_PROVENANCE: Final = {
    "collector": "weightclass.synthetic-probe.runner",
    "purpose": "synthetic-self-test-only",
    "trust_boundary": "runner-direct-only",
}
_PROVENANCE_FIELDS: Final = set(_PROVENANCE)
_SELF_TEST_FIELDS: Final = {"self_test_id"}


class ProbeProtocolInvalidInputError(ValueError):
    """Raised without input details when a synthetic manifest is invalid."""


class _DuplicateKeyError(ValueError):
    pass


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKeyError()
        value[key] = item
    return value


def _require_object(value: object, fields: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ProbeProtocolInvalidInputError()
    return cast(dict[str, object], value)


def _require_self_test_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value.encode("utf-8", errors="surrogatepass")) > MAX_IDENTIFIER_BYTES
        or _SELF_TEST_PATTERN.fullmatch(value) is None
        or value not in PROBE_SELF_TEST_IDS
    ):
        raise ProbeProtocolInvalidInputError()
    return value


def _require_provenance(value: object) -> dict[str, str]:
    provenance = _require_object(value, _PROVENANCE_FIELDS)
    if provenance != _PROVENANCE:
        raise ProbeProtocolInvalidInputError()
    return dict(_PROVENANCE)


def _parse_value(value: object) -> dict[str, object]:
    manifest = _require_object(value, _TOP_LEVEL_FIELDS)
    if (
        not isinstance(manifest["probe_manifest_schema_version"], int)
        or isinstance(manifest["probe_manifest_schema_version"], bool)
        or manifest["probe_manifest_schema_version"] != PROBE_MANIFEST_SCHEMA_VERSION
        or manifest["probe_protocol_id"] != PROBE_PROTOCOL_ID
        or manifest["qualification_eligible"] is not False
        or manifest["delegation_support"] is not False
    ):
        raise ProbeProtocolInvalidInputError()
    provenance = _require_provenance(manifest["provenance"])
    raw_self_tests = manifest["self_tests"]
    if (
        not isinstance(raw_self_tests, list)
        or not raw_self_tests
        or len(raw_self_tests) > MAX_SELF_TESTS
    ):
        raise ProbeProtocolInvalidInputError()
    self_test_ids = [
        _require_self_test_id(_require_object(item, _SELF_TEST_FIELDS)["self_test_id"])
        for item in raw_self_tests
    ]
    if self_test_ids != sorted(set(self_test_ids)):
        raise ProbeProtocolInvalidInputError()
    return {
        "delegation_support": False,
        "probe_manifest_schema_version": PROBE_MANIFEST_SCHEMA_VERSION,
        "probe_protocol_id": PROBE_PROTOCOL_ID,
        "provenance": provenance,
        "qualification_eligible": False,
        "self_tests": [{"self_test_id": item} for item in self_test_ids],
    }


def build_probe_manifest(
    self_test_ids: Iterable[str], *, provenance: Mapping[str, object]
) -> dict[str, object]:
    """Build a deterministic synthetic-only probe manifest."""
    identifiers: list[str] = []
    for index, item in enumerate(self_test_ids):
        if index >= MAX_SELF_TESTS:
            raise ProbeProtocolInvalidInputError()
        identifiers.append(_require_self_test_id(item))
    identifiers.sort()
    value: dict[str, object] = {
        "delegation_support": False,
        "probe_manifest_schema_version": PROBE_MANIFEST_SCHEMA_VERSION,
        "probe_protocol_id": PROBE_PROTOCOL_ID,
        "provenance": dict(provenance),
        "qualification_eligible": False,
        "self_tests": [{"self_test_id": item} for item in identifiers],
    }
    return _parse_value(value)


def canonical_probe_manifest_bytes(value: object) -> bytes:
    """Return the sole canonical JSON representation of a valid manifest."""
    normalized = _parse_value(value)
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise ProbeProtocolInvalidInputError()
    return encoded


def parse_probe_manifest(encoded: bytes) -> dict[str, object]:
    """Parse one bounded, strict UTF-8 synthetic probe manifest."""
    if not isinstance(encoded, bytes) or not encoded or len(encoded) > MAX_MANIFEST_BYTES:
        raise ProbeProtocolInvalidInputError()
    try:
        value = json.loads(
            encoded.decode("utf-8", errors="strict"),
            object_pairs_hook=_object_without_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateKeyError):
        raise ProbeProtocolInvalidInputError() from None
    return _parse_value(value)
