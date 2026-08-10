"""Pure parser for the closed native schema-2 routing policy."""

from __future__ import annotations

import posixpath
from collections.abc import Hashable, Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias

from .canonical_v2 import canonical_json_bytes_v2
from .v2_validation import (
    V2ValidationError,
    require_exact_keys,
    require_integer,
    require_string,
)

Vendor: TypeAlias = Literal["codex", "claude"]
Tier: TypeAlias = Literal["low", "standard", "high"]
BuilderKind: TypeAlias = Literal["codex-exec-v1", "claude-print-v1"]


@dataclass(frozen=True, slots=True)
class NativeProfileV2:
    id: str
    vendor: Vendor
    account_profile: str


@dataclass(frozen=True, slots=True)
class NativeBuilderV2:
    kind: BuilderKind
    version: int


@dataclass(frozen=True, slots=True)
class ModelEffortPairV2:
    model: str
    effort: str


@dataclass(frozen=True, slots=True)
class NativeTargetV2:
    id: str
    profile_id: str
    vendor: Vendor
    executable: str
    builder: NativeBuilderV2
    allowed_model_effort_pairs: tuple[ModelEffortPairV2, ...]


@dataclass(frozen=True, slots=True)
class NativeEligibilityV2:
    source_vendor: Vendor
    source_profile_id: str
    tier: Tier


@dataclass(frozen=True, slots=True)
class NativeRouteV2:
    id: str
    eligibility: tuple[NativeEligibilityV2, ...]
    target_id: str
    model: str
    effort: str


@dataclass(frozen=True, slots=True)
class ProfileGrantV2:
    id: str
    from_profile_id: str
    to_profile_id: str


@dataclass(frozen=True, slots=True)
class VendorGrantV2:
    id: str
    from_vendor: Vendor
    to_vendor: Vendor


@dataclass(frozen=True, slots=True)
class NativePolicyV2:
    schema_version: int
    profiles: tuple[NativeProfileV2, ...]
    execution_targets: tuple[NativeTargetV2, ...]
    routes: tuple[NativeRouteV2, ...]
    profile_grants: tuple[ProfileGrantV2, ...]
    vendor_grants: tuple[VendorGrantV2, ...]


def _sequence(value: object, *, lower: int, upper: int) -> list[object]:
    if not isinstance(value, list) or not lower <= len(value) <= upper:
        raise V2ValidationError()
    return value


def _identifier(value: object) -> str:
    result = require_string(value, max_bytes=64)
    if any(character.isspace() or not character.isprintable() for character in result):
        raise V2ValidationError()
    return result


def _opaque(value: object) -> str:
    result = require_string(value, max_bytes=240)
    if any(not character.isprintable() for character in result):
        raise V2ValidationError()
    return result


def _vendor(value: object) -> Vendor:
    if value not in ("codex", "claude"):
        raise V2ValidationError()
    return value


def _tier(value: object) -> Tier:
    if value not in ("low", "standard", "high"):
        raise V2ValidationError()
    return value


def _executable(value: object) -> str:
    result = require_string(value, max_bytes=4096)
    if (
        not result.startswith("/")
        or "\x00" in result
        or result != posixpath.normpath(result)
        or result.startswith("//")
    ):
        raise V2ValidationError()
    return result


def _unique(values: Sequence[Hashable]) -> None:
    if len(values) != len(set(values)):
        raise V2ValidationError()


def validate_native_selector(
    source_vendor: object, source_profile_id: object
) -> tuple[Vendor, str]:
    """Validate the caller-supplied half of the exact native selector."""
    return _vendor(source_vendor), _identifier(source_profile_id)


def dispatch_native_policy_schema(value: object) -> tuple[int, object]:
    """Select the additive native schema without invoking either runtime path."""
    if not isinstance(value, dict):
        raise V2ValidationError()
    if "schema_version" not in value:
        return 1, value
    version = value["schema_version"]
    if isinstance(version, bool) or not isinstance(version, int):
        raise V2ValidationError()
    if version == 1:
        legacy = dict(value)
        del legacy["schema_version"]
        return 1, legacy
    if version == 2:
        return 2, parse_native_policy_v2(value)
    raise V2ValidationError()


def parse_native_policy_v2(value: object) -> NativePolicyV2:
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
    require_integer(policy["schema_version"], lower=2, upper=2)
    if len(canonical_json_bytes_v2(value)) > 262_144:
        raise V2ValidationError()

    profiles: list[NativeProfileV2] = []
    for raw in _sequence(policy["profiles"], lower=1, upper=64):
        item = require_exact_keys(raw, {"id", "vendor", "account_profile"})
        profiles.append(
            NativeProfileV2(
                _identifier(item["id"]),
                _vendor(item["vendor"]),
                _opaque(item["account_profile"]),
            )
        )

    targets: list[NativeTargetV2] = []
    for raw in _sequence(policy["execution_targets"], lower=1, upper=64):
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
        builder_raw = require_exact_keys(item["builder"], {"kind", "version"})
        kind = builder_raw["kind"]
        if kind not in ("codex-exec-v1", "claude-print-v1"):
            raise V2ValidationError()
        builder = NativeBuilderV2(
            kind,
            require_integer(builder_raw["version"], lower=1, upper=1),
        )
        pairs: list[ModelEffortPairV2] = []
        for raw_pair in _sequence(item["allowed_model_effort_pairs"], lower=1, upper=64):
            pair = require_exact_keys(raw_pair, {"model", "effort"})
            pairs.append(ModelEffortPairV2(_opaque(pair["model"]), _opaque(pair["effort"])))
        _unique([(pair.model, pair.effort) for pair in pairs])
        targets.append(
            NativeTargetV2(
                _identifier(item["id"]),
                _identifier(item["profile_id"]),
                _vendor(item["vendor"]),
                _executable(item["executable"]),
                builder,
                tuple(pairs),
            )
        )

    routes: list[NativeRouteV2] = []
    for raw in _sequence(policy["routes"], lower=1, upper=128):
        item = require_exact_keys(raw, {"id", "eligibility", "target_id", "model", "effort"})
        eligibility: list[NativeEligibilityV2] = []
        for raw_selector in _sequence(item["eligibility"], lower=1, upper=32):
            selector_item = require_exact_keys(
                raw_selector, {"source_vendor", "source_profile_id", "tier"}
            )
            eligibility.append(
                NativeEligibilityV2(
                    _vendor(selector_item["source_vendor"]),
                    _identifier(selector_item["source_profile_id"]),
                    _tier(selector_item["tier"]),
                )
            )
        _unique(
            [(entry.source_vendor, entry.source_profile_id, entry.tier) for entry in eligibility]
        )
        routes.append(
            NativeRouteV2(
                _identifier(item["id"]),
                tuple(eligibility),
                _identifier(item["target_id"]),
                _opaque(item["model"]),
                _opaque(item["effort"]),
            )
        )

    profile_grants: list[ProfileGrantV2] = []
    for raw in _sequence(policy["profile_grants"], lower=0, upper=128):
        item = require_exact_keys(raw, {"id", "from_profile_id", "to_profile_id"})
        profile_grants.append(
            ProfileGrantV2(
                _identifier(item["id"]),
                _identifier(item["from_profile_id"]),
                _identifier(item["to_profile_id"]),
            )
        )
    vendor_grants: list[VendorGrantV2] = []
    for raw in _sequence(policy["vendor_grants"], lower=0, upper=128):
        item = require_exact_keys(raw, {"id", "from_vendor", "to_vendor"})
        vendor_grants.append(
            VendorGrantV2(
                _identifier(item["id"]),
                _vendor(item["from_vendor"]),
                _vendor(item["to_vendor"]),
            )
        )

    _unique([profile.id for profile in profiles])
    _unique([target.id for target in targets])
    _unique([route.id for route in routes])
    _unique([grant.id for grant in profile_grants])
    _unique([grant.id for grant in vendor_grants])
    _unique([(grant.from_profile_id, grant.to_profile_id) for grant in profile_grants])
    _unique([(grant.from_vendor, grant.to_vendor) for grant in vendor_grants])
    if any(grant.from_profile_id == grant.to_profile_id for grant in profile_grants) or any(
        grant.from_vendor == grant.to_vendor for grant in vendor_grants
    ):
        raise V2ValidationError()

    by_profile = {profile.id: profile for profile in profiles}
    by_target = {target.id: target for target in targets}
    for target in targets:
        destination = by_profile.get(target.profile_id)
        expected_builder = "codex-exec-v1" if target.vendor == "codex" else "claude-print-v1"
        if (
            destination is None
            or destination.vendor != target.vendor
            or target.builder.kind != expected_builder
        ):
            raise V2ValidationError()
    selectors: list[tuple[str, str, str]] = []
    for route in routes:
        for route_selector in route.eligibility:
            source = by_profile.get(route_selector.source_profile_id)
            if source is None or source.vendor != route_selector.source_vendor:
                raise V2ValidationError()
            selectors.append(
                (
                    route_selector.source_vendor,
                    route_selector.source_profile_id,
                    route_selector.tier,
                )
            )
    _unique(selectors)
    for route in routes:
        selected_target = by_target.get(route.target_id)
        if (
            selected_target is None
            or ModelEffortPairV2(route.model, route.effort)
            not in selected_target.allowed_model_effort_pairs
        ):
            raise V2ValidationError()
    if any(
        grant.from_profile_id not in by_profile or grant.to_profile_id not in by_profile
        for grant in profile_grants
    ):
        raise V2ValidationError()

    used_profiles: set[str] = set()
    used_vendors: set[str] = set()
    for route in routes:
        selected_target = by_target[route.target_id]
        for route_selector in route.eligibility:
            if route_selector.source_profile_id != selected_target.profile_id:
                profile_matches = [
                    profile_grant
                    for profile_grant in profile_grants
                    if (profile_grant.from_profile_id, profile_grant.to_profile_id)
                    == (route_selector.source_profile_id, selected_target.profile_id)
                ]
                if len(profile_matches) != 1:
                    raise V2ValidationError()
                used_profiles.add(profile_matches[0].id)
            if route_selector.source_vendor != selected_target.vendor:
                vendor_matches = [
                    vendor_grant
                    for vendor_grant in vendor_grants
                    if (vendor_grant.from_vendor, vendor_grant.to_vendor)
                    == (route_selector.source_vendor, selected_target.vendor)
                ]
                if len(vendor_matches) != 1:
                    raise V2ValidationError()
                used_vendors.add(vendor_matches[0].id)
    if used_profiles != {grant.id for grant in profile_grants} or used_vendors != {
        grant.id for grant in vendor_grants
    }:
        raise V2ValidationError()
    return NativePolicyV2(
        2,
        tuple(profiles),
        tuple(targets),
        tuple(routes),
        tuple(profile_grants),
        tuple(vendor_grants),
    )
