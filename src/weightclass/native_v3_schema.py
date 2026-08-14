"""Pure validation for the closed schema-3 native routing policy.

Schema 3 is deliberately task-free.  It describes profiles, reviewed native
CLI builders, and one exact route for each source-profile/tier selector.  The
compiler is the only place that turns those declarations into an argv template.
"""

from __future__ import annotations

import posixpath
import unicodedata
from collections.abc import Hashable, Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias

from .canonical_v2 import canonical_json_bytes_v2
from .v2_validation import V2ValidationError, require_exact_keys, require_integer

VendorV3: TypeAlias = Literal["agy", "claude", "codex", "grok"]
TierV3: TypeAlias = Literal["low", "standard", "high"]
EffortV3: TypeAlias = Literal["low", "medium", "high"]
BuilderKindV3: TypeAlias = Literal[
    "agy-print-v1",
    "claude-print-v1",
    "codex-exec-v1",
    "grok-print-v1",
]

MAX_NATIVE_POLICY_BYTES = 262_144
MAX_PROFILES_V3 = 64
MAX_TARGETS_V3 = 64
MAX_ROUTES_V3 = 128
MAX_GRANTS_V3 = 128
MAX_PAIRS_V3 = 64
MAX_IDENTIFIER_BYTES_V3 = 64
MAX_LABEL_BYTES_V3 = 240
MAX_EXECUTABLE_BYTES_V3 = 4_096
MAX_ARGV_TOKENS_V3 = 32
MAX_ARGV_TOKEN_BYTES_V3 = 4_096
MAX_ARGV_TOTAL_BYTES_V3 = 16_384


@dataclass(frozen=True, slots=True)
class ProfileV3:
    id: str
    vendor: VendorV3
    account_profile: str


@dataclass(frozen=True, slots=True)
class BuilderV3:
    kind: BuilderKindV3
    version: int


@dataclass(frozen=True, slots=True)
class PairV3:
    model: str | None
    effort: EffortV3


@dataclass(frozen=True, slots=True)
class TargetV3:
    id: str
    profile_id: str
    vendor: VendorV3
    executable: str
    builder: BuilderV3
    allowed_model_effort_pairs: tuple[PairV3, ...]


# Descriptive aliases make the schema names usable without breaking the short
# names used by the first implementation and its callers.
ExecutionTargetV3 = TargetV3
ModelEffortPairV3 = PairV3
NativeProfileV3 = ProfileV3
NativeBuilderV3 = BuilderV3
NativePairV3 = PairV3
NativeTargetV3 = TargetV3
NativeModelEffortPairV3 = PairV3


@dataclass(frozen=True, slots=True)
class RouteV3:
    id: str
    source_profile_id: str
    tier: TierV3
    target_id: str
    model: str | None
    effort: EffortV3


@dataclass(frozen=True, slots=True)
class ProfileGrantV3:
    id: str
    from_profile_id: str
    to_profile_id: str


@dataclass(frozen=True, slots=True)
class VendorGrantV3:
    id: str
    from_vendor: VendorV3
    to_vendor: VendorV3


NativeRouteV3 = RouteV3
NativeProfileGrantV3 = ProfileGrantV3
NativeVendorGrantV3 = VendorGrantV3


@dataclass(frozen=True, slots=True)
class NativePolicyV3:
    schema_version: int
    profiles: tuple[ProfileV3, ...]
    execution_targets: tuple[TargetV3, ...]
    routes: tuple[RouteV3, ...]
    profile_grants: tuple[ProfileGrantV3, ...]
    vendor_grants: tuple[VendorGrantV3, ...]


def _encoded_string(value: object, maximum: int, *, minimum: int = 1) -> str:
    """Validate a string by strict UTF-8 byte count and NFC normalization."""
    if not isinstance(value, str):
        raise V2ValidationError()
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise V2ValidationError() from None
    if not minimum <= len(encoded) <= maximum:
        raise V2ValidationError()
    if unicodedata.normalize("NFC", value) != value:
        raise V2ValidationError()
    return value


def _forbidden_character(character: str) -> bool:
    """Reject controls, format characters, surrogates, and unassigned text."""
    return unicodedata.category(character).startswith("C") or "\x00" in character


def validate_identifier(value: object) -> str:
    """Validate a compact policy identifier without interpreting its value."""
    result = _encoded_string(value, MAX_IDENTIFIER_BYTES_V3)
    if result.startswith("-") or any(
        character.isspace() or _forbidden_character(character) for character in result
    ):
        raise V2ValidationError()
    return result


def validate_label(value: object, *, maximum: int = MAX_LABEL_BYTES_V3) -> str:
    """Validate a human/account label while allowing ordinary internal spaces."""
    result = _encoded_string(value, maximum)
    if (
        result.startswith("-")
        or result != result.strip(" ")
        or any(
            _forbidden_character(character) or (character.isspace() and character != " ")
            for character in result
        )
    ):
        raise V2ValidationError()
    return result


def validate_opaque_token(value: object, *, maximum: int = MAX_LABEL_BYTES_V3) -> str:
    """Validate a user-provided model label as one reviewable argv token."""
    result = _encoded_string(value, maximum)
    if result.startswith("-") or any(
        character.isspace() or _forbidden_character(character) for character in result
    ):
        raise V2ValidationError()
    return result


def validate_executable_path(value: object) -> str:
    """Validate an absolute, normalized, non-directory POSIX path spelling."""
    result = _encoded_string(value, MAX_EXECUTABLE_BYTES_V3)
    if result == "/":
        return result
    if not result.startswith("/") or result.startswith("//"):
        raise V2ValidationError()
    if result != posixpath.normpath(result):
        raise V2ValidationError()
    components = result[1:].split("/")
    if any(
        not component
        or component in {".", ".."}
        or component != component.strip(" ")
        or any(
            _forbidden_character(character) or (character.isspace() and character != " ")
            for character in component
        )
        for component in components
    ):
        raise V2ValidationError()
    return result


def validate_argv(argv: Sequence[object]) -> tuple[str, ...]:
    """Validate the bounded argv/template representation used in a review."""
    if not isinstance(argv, (list, tuple)) or not 1 <= len(argv) <= MAX_ARGV_TOKENS_V3:
        raise V2ValidationError()
    tokens: list[str] = []
    total = 0
    for value in argv:
        token = _encoded_string(value, MAX_ARGV_TOKEN_BYTES_V3)
        if any(
            _forbidden_character(character) or (character.isspace() and character != " ")
            for character in token
        ):
            raise V2ValidationError()
        tokens.append(token)
        total += len(token.encode("utf-8"))
    if total > MAX_ARGV_TOTAL_BYTES_V3:
        raise V2ValidationError()
    return tuple(tokens)


# Private names remain as small adapters for the style used by schema 2 and
# for callers that imported the initial v3 scaffold.
def _sequence(value: object, lower: int, upper: int) -> list[object]:
    if not isinstance(value, list) or not lower <= len(value) <= upper:
        raise V2ValidationError()
    return value


def _unique(values: Sequence[Hashable]) -> None:
    if len(values) != len(set(values)):
        raise V2ValidationError()


def _id(value: object) -> str:
    return validate_identifier(value)


def _text(value: object, maximum: int = MAX_LABEL_BYTES_V3) -> str:
    return validate_label(value, maximum=maximum)


def _vendor(value: object) -> VendorV3:
    if value not in ("agy", "claude", "codex", "grok"):
        raise V2ValidationError()
    return value


def _tier(value: object) -> TierV3:
    if value not in ("low", "standard", "high"):
        raise V2ValidationError()
    return value


def _effort(value: object) -> EffortV3:
    if value not in ("low", "medium", "high"):
        raise V2ValidationError()
    return value


def _model(value: object) -> str | None:
    return None if value is None else validate_opaque_token(value)


def _path(value: object) -> str:
    return validate_executable_path(value)


# Reusable validator spellings for policy adapters and focused callers.
_identifier = validate_identifier
_label = validate_label
_runtime_path = validate_executable_path


def _builder(value: object, vendor: VendorV3) -> BuilderV3:
    raw = require_exact_keys(value, {"kind", "version"})
    expected: dict[VendorV3, BuilderKindV3] = {
        "agy": "agy-print-v1",
        "claude": "claude-print-v1",
        "codex": "codex-exec-v1",
        "grok": "grok-print-v1",
    }
    kind = raw["kind"]
    if kind != expected[vendor]:
        raise V2ValidationError()
    return BuilderV3(kind, require_integer(raw["version"], lower=1, upper=1))


def validate_native_selector_v3(
    source_vendor: object, source_profile_id: object, tier: object
) -> tuple[VendorV3, str, TierV3]:
    """Validate the explicit source half of a schema-3 selection."""
    return _vendor(source_vendor), _id(source_profile_id), _tier(tier)


def parse_native_policy_v3(value: object) -> NativePolicyV3:
    """Parse and cross-check one exact schema-3 policy."""
    policy = require_exact_keys(
        value,
        {
            "schema_version",
            "profiles",
            "execution_targets",
            "routes",
            "profile_grants",
            "vendor_grants",
        },
    )
    require_integer(policy["schema_version"], lower=3, upper=3)
    try:
        if len(canonical_json_bytes_v2(value)) > MAX_NATIVE_POLICY_BYTES:
            raise V2ValidationError()
    except (RecursionError, V2ValidationError):
        raise V2ValidationError() from None

    profiles: list[ProfileV3] = []
    for raw in _sequence(policy["profiles"], 1, MAX_PROFILES_V3):
        item = require_exact_keys(raw, {"id", "vendor", "account_profile"})
        profiles.append(
            ProfileV3(_id(item["id"]), _vendor(item["vendor"]), _text(item["account_profile"]))
        )

    targets: list[TargetV3] = []
    for raw in _sequence(policy["execution_targets"], 1, MAX_TARGETS_V3):
        item = require_exact_keys(
            raw,
            {
                "id",
                "profile_id",
                "vendor",
                "executable",
                "builder",
                "allowed_model_effort_pairs",
            },
        )
        vendor = _vendor(item["vendor"])
        pairs: list[PairV3] = []
        for raw_pair in _sequence(item["allowed_model_effort_pairs"], 1, MAX_PAIRS_V3):
            pair = require_exact_keys(raw_pair, {"model", "effort"})
            model = _model(pair["model"])
            if vendor == "agy" and model is not None:
                raise V2ValidationError()
            pairs.append(PairV3(model, _effort(pair["effort"])))
        _unique([(pair.model, pair.effort) for pair in pairs])
        targets.append(
            TargetV3(
                _id(item["id"]),
                _id(item["profile_id"]),
                vendor,
                _path(item["executable"]),
                _builder(item["builder"], vendor),
                tuple(pairs),
            )
        )

    routes: list[RouteV3] = []
    for raw in _sequence(policy["routes"], 1, MAX_ROUTES_V3):
        item = require_exact_keys(
            raw, {"id", "source_profile_id", "tier", "target_id", "model", "effort"}
        )
        routes.append(
            RouteV3(
                _id(item["id"]),
                _id(item["source_profile_id"]),
                _tier(item["tier"]),
                _id(item["target_id"]),
                _model(item["model"]),
                _effort(item["effort"]),
            )
        )

    profile_grants: list[ProfileGrantV3] = []
    for raw in _sequence(policy["profile_grants"], 0, MAX_GRANTS_V3):
        item = require_exact_keys(raw, {"id", "from_profile_id", "to_profile_id"})
        profile_grants.append(
            ProfileGrantV3(
                _id(item["id"]), _id(item["from_profile_id"]), _id(item["to_profile_id"])
            )
        )

    vendor_grants: list[VendorGrantV3] = []
    for raw in _sequence(policy["vendor_grants"], 0, MAX_GRANTS_V3):
        item = require_exact_keys(raw, {"id", "from_vendor", "to_vendor"})
        vendor_grants.append(
            VendorGrantV3(_id(item["id"]), _vendor(item["from_vendor"]), _vendor(item["to_vendor"]))
        )

    _unique([profile.id for profile in profiles])
    _unique([target.id for target in targets])
    _unique([route.id for route in routes])
    _unique([grant.id for grant in profile_grants])
    _unique([grant.id for grant in vendor_grants])
    _unique(
        [profile.id for profile in profiles]
        + [target.id for target in targets]
        + [route.id for route in routes]
        + [grant.id for grant in profile_grants]
        + [grant.id for grant in vendor_grants]
    )
    _unique([(grant.from_profile_id, grant.to_profile_id) for grant in profile_grants])
    _unique([(grant.from_vendor, grant.to_vendor) for grant in vendor_grants])

    profiles_by_id = {profile.id: profile for profile in profiles}
    targets_by_id = {target.id: target for target in targets}
    if any(
        grant.from_profile_id == grant.to_profile_id
        or grant.from_profile_id not in profiles_by_id
        or grant.to_profile_id not in profiles_by_id
        for grant in profile_grants
    ):
        raise V2ValidationError()
    if any(grant.from_vendor == grant.to_vendor for grant in vendor_grants):
        raise V2ValidationError()

    selectors: list[tuple[VendorV3, str, TierV3]] = []
    required_profiles: set[tuple[str, str]] = set()
    required_vendors: set[tuple[VendorV3, VendorV3]] = set()
    for route in routes:
        source = profiles_by_id.get(route.source_profile_id)
        target = targets_by_id.get(route.target_id)
        if source is None or target is None:
            raise V2ValidationError()
        destination = profiles_by_id.get(target.profile_id)
        if destination is None or destination.vendor != target.vendor:
            raise V2ValidationError()
        selectors.append((source.vendor, source.id, route.tier))
        if PairV3(route.model, route.effort) not in target.allowed_model_effort_pairs:
            raise V2ValidationError()
        if source.id != destination.id:
            required_profiles.add((source.id, destination.id))
        if source.vendor != destination.vendor:
            required_vendors.add((source.vendor, destination.vendor))
    _unique(selectors)
    if required_profiles != {
        (grant.from_profile_id, grant.to_profile_id) for grant in profile_grants
    }:
        raise V2ValidationError()
    if required_vendors != {(grant.from_vendor, grant.to_vendor) for grant in vendor_grants}:
        raise V2ValidationError()

    return NativePolicyV3(
        schema_version=3,
        profiles=tuple(profiles),
        execution_targets=tuple(targets),
        routes=tuple(routes),
        profile_grants=tuple(profile_grants),
        vendor_grants=tuple(vendor_grants),
    )
