"""Pure selection and compilation of one reviewed native schema-2 route."""

from __future__ import annotations

from typing import Any

from .canonical_v2 import bind_canonical_descriptor_v2
from .native_v2_schema import NativePolicyV2, NativeProfileV2, Tier, Vendor
from .native_v2_types import CompiledExecutionV2, FrozenCleanupV2
from .v2_validation import NATIVE_LIST_PATHS, V2ValidationError, canonicalize_registered_lists


def _profile(profile: NativeProfileV2) -> dict[str, object]:
    return {
        "id": profile.id,
        "vendor": profile.vendor,
        "account_profile": profile.account_profile,
    }


def compile_native_v2(
    policy: NativePolicyV2,
    *,
    source_vendor: Vendor,
    source_profile_id: str,
    tier: Tier,
) -> CompiledExecutionV2:
    matches = [
        (route, selector)
        for route in policy.routes
        for selector in route.eligibility
        if (selector.source_vendor, selector.source_profile_id, selector.tier)
        == (source_vendor, source_profile_id, tier)
    ]
    if len(matches) != 1:
        raise V2ValidationError()
    route, selector = matches[0]
    profiles = {profile.id: profile for profile in policy.profiles}
    targets = {target.id: target for target in policy.execution_targets}
    source = profiles[selector.source_profile_id]
    target = targets[route.target_id]
    destination = profiles[target.profile_id]
    argv: tuple[str, ...]
    if target.vendor == "codex":
        argv = (
            target.executable,
            "exec",
            "--ephemeral",
            "--sandbox",
            "workspace-write",
            "--model",
            route.model,
            "-c",
            f"model_reasoning_effort={route.effort}",
            "-",
        )
    else:
        argv = (
            target.executable,
            "--print",
            "--no-session-persistence",
            "--permission-mode",
            "acceptEdits",
            "--model",
            route.model,
            "--effort",
            route.effort,
        )
    encoded_tokens = [token.encode("utf-8") for token in argv]
    if (
        len(argv) > 32
        or any(len(token) > 4096 for token in encoded_tokens)
        or sum(map(len, encoded_tokens)) > 16_384
        or argv[0] != target.executable
    ):
        raise V2ValidationError()
    changed: list[str] = []
    authorizations: list[dict[str, str]] = []
    if source.id != destination.id:
        changed.append("profile")
        profile_grant = next(
            candidate
            for candidate in policy.profile_grants
            if (candidate.from_profile_id, candidate.to_profile_id) == (source.id, destination.id)
        )
        authorizations.append({"dimension": "profile", "grant_id": profile_grant.id})
    if source.vendor != destination.vendor:
        changed.append("vendor")
        vendor_grant = next(
            candidate
            for candidate in policy.vendor_grants
            if (candidate.from_vendor, candidate.to_vendor) == (source.vendor, destination.vendor)
        )
        authorizations.append({"dimension": "vendor", "grant_id": vendor_grant.id})
    descriptor: dict[str, Any] = {
        "descriptor_schema_version": 2,
        "compiler_contract_version": 1,
        "source": _profile(source),
        "route": {
            "id": route.id,
            "eligibility": [
                {
                    "source_vendor": item.source_vendor,
                    "source_profile_id": item.source_profile_id,
                    "tier": item.tier,
                }
                for item in route.eligibility
            ],
            "target_id": route.target_id,
            "model": route.model,
            "effort": route.effort,
        },
        "target": {
            "id": target.id,
            "profile_id": target.profile_id,
            "vendor": target.vendor,
            "executable": target.executable,
            "builder": {"kind": target.builder.kind, "version": target.builder.version},
            "allowed_model_effort_pairs": [
                {"model": pair.model, "effort": pair.effort}
                for pair in target.allowed_model_effort_pairs
            ],
        },
        "selection": {
            "source_vendor": selector.source_vendor,
            "source_profile_id": selector.source_profile_id,
            "tier": selector.tier,
        },
        "argv": list(argv),
        "transition": {
            "id": f"{route.id}-transition",
            "source_profile": _profile(source),
            "destination_profile": _profile(destination),
            "changed_dimensions": changed,
            "authorizations": authorizations,
        },
    }
    canonical = canonicalize_registered_lists(descriptor, NATIVE_LIST_PATHS)
    return bind_canonical_descriptor_v2(
        canonical,
        argv=argv,
        executable=target.executable,
        transport="native_stdin",
        transport_version=1,
        cleanup=FrozenCleanupV2(0, 0),
    )
