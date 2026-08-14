"""Task-free deterministic compilation of one schema-3 native route."""

from __future__ import annotations

import hashlib
import json
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .canonical_v2 import canonical_json_bytes_v2
from .executable_observation import ExecutableObservation
from .native_v3_schema import (
    MAX_NATIVE_POLICY_BYTES,
    BuilderKindV3,
    NativePolicyV3,
    ProfileV3,
    RouteV3,
    TierV3,
    VendorV3,
    validate_argv,
    validate_native_selector_v3,
)
from .router import (
    CLAUDE_COMMAND_PREFIX,
    TASK_PLACEHOLDER,
    agy_command,
    codex_command,
    grok_command,
)
from .v2_validation import V2ValidationError

PurposeV3 = Literal["native_route", "native_delegation"]


@dataclass(frozen=True, slots=True)
class StaticNativeSelectionV3:
    """Immutable task-free selection before any executable is observed."""

    executable: str
    vendor: VendorV3
    argv_template: tuple[str, ...]
    task_delivery: Literal["stdin", "argv"]
    required_confirmations: tuple[str, ...]
    descriptor_without_observation_bytes: bytes


def _profile(profile: ProfileV3) -> dict[str, object]:
    return {
        "id": profile.id,
        "vendor": profile.vendor,
        "account_profile": profile.account_profile,
    }


def _argv(
    route: RouteV3,
    executable: str,
    vendor: VendorV3,
    builder: BuilderKindV3,
) -> tuple[str, ...]:
    """Build only one of the four reviewed native command shapes."""
    if vendor == "codex" and builder == "codex-exec-v1":
        command = list(codex_command(route.effort))
        if route.model is not None:
            insertion = command.index("-c")
            command[insertion:insertion] = ["--model", route.model]
    elif vendor == "claude" and builder == "claude-print-v1":
        command = list(CLAUDE_COMMAND_PREFIX + (route.effort,))
        if route.model is not None:
            insertion = command.index("--effort")
            command[insertion:insertion] = ["--model", route.model]
    elif vendor == "agy" and builder == "agy-print-v1":
        if route.model is not None:
            raise V2ValidationError()
        command = list(agy_command(route.effort))
    elif vendor == "grok" and builder == "grok-print-v1":
        command = list(grok_command(route.effort))
        if route.model is not None:
            insertion = command.index("--reasoning-effort")
            command[insertion:insertion] = ["--model", route.model]
    else:
        raise V2ValidationError()
    command[0] = executable
    return validate_argv(command)


def _observation(value: ExecutableObservation, executable: str) -> dict[str, object]:
    """Project an observation into the task-free, fingerprinted descriptor."""
    numeric = (
        value.st_dev,
        value.st_ino,
        value.file_type,
        value.mode,
        value.size,
        value.mtime_ns,
        value.ctime_ns,
    )
    if any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in numeric):
        raise V2ValidationError()
    if (
        value.lexical_path != executable
        or value.file_type != stat.S_IFREG
        or stat.S_IFMT(value.mode) != stat.S_IFREG
        or not (value.mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
        or value.executable_bit is not True
    ):
        raise V2ValidationError()
    return {
        "lexical_path": value.lexical_path,
        "st_dev": value.st_dev,
        "st_ino": value.st_ino,
        "file_type": value.file_type,
        "mode": value.mode,
        "size": value.size,
        "mtime_ns": value.mtime_ns,
        "ctime_ns": value.ctime_ns,
        "executable_bit": value.executable_bit,
    }


def _bind_descriptor(descriptor: Mapping[str, Any]) -> dict[str, object]:
    """Bind one schema-3 descriptor without borrowing a schema-2 transport."""
    payload = dict(descriptor)
    payload.pop("route_fingerprint", None)
    encoded = canonical_json_bytes_v2(payload)
    if len(encoded) > MAX_NATIVE_POLICY_BYTES:
        raise V2ValidationError()
    bound: dict[str, object] = dict(payload)
    bound["route_fingerprint"] = f"sha256:{hashlib.sha256(encoded).hexdigest()}"
    if len(canonical_json_bytes_v2(bound)) > MAX_NATIVE_POLICY_BYTES:
        raise V2ValidationError()
    return bound


def compile_static_native_policy_v3(
    policy: NativePolicyV3,
    *,
    source_vendor: VendorV3,
    source_profile_id: str,
    tier: TierV3,
    purpose: PurposeV3,
) -> StaticNativeSelectionV3:
    """Select exactly one route without reading a task or observing a process."""
    if purpose not in ("native_route", "native_delegation"):
        raise V2ValidationError()
    validated_vendor, validated_profile, validated_tier = validate_native_selector_v3(
        source_vendor, source_profile_id, tier
    )
    matches = [
        route
        for route in policy.routes
        if (
            route.source_profile_id,
            route.tier,
        )
        == (validated_profile, validated_tier)
    ]
    if len(matches) != 1:
        raise V2ValidationError()
    route = matches[0]
    profiles = {profile.id: profile for profile in policy.profiles}
    targets = {target.id: target for target in policy.execution_targets}
    source = profiles.get(route.source_profile_id)
    target = targets.get(route.target_id)
    if source is None or target is None or source.vendor != validated_vendor:
        raise V2ValidationError()
    destination = profiles.get(target.profile_id)
    if destination is None or destination.vendor != target.vendor:
        raise V2ValidationError()
    argv_template = _argv(route, target.executable, target.vendor, target.builder.kind)
    task_slot_count = argv_template.count(TASK_PLACEHOLDER)
    expected_task_slot_count = 1 if target.vendor in ("agy", "grok") else 0
    if task_slot_count != expected_task_slot_count:
        raise V2ValidationError()
    argv_exposure = task_slot_count == 1
    changed_dimensions: list[str] = []
    authorizations: list[dict[str, str]] = []
    if source.id != destination.id:
        changed_dimensions.append("profile")
        profile_grants = [
            grant
            for grant in policy.profile_grants
            if (grant.from_profile_id, grant.to_profile_id) == (source.id, destination.id)
        ]
        if len(profile_grants) != 1:
            raise V2ValidationError()
        authorizations.append({"dimension": "profile", "grant_id": profile_grants[0].id})
    if source.vendor != destination.vendor:
        changed_dimensions.append("vendor")
        vendor_grants = [
            grant
            for grant in policy.vendor_grants
            if (grant.from_vendor, grant.to_vendor) == (source.vendor, destination.vendor)
        ]
        if len(vendor_grants) != 1:
            raise V2ValidationError()
        authorizations.append({"dimension": "vendor", "grant_id": vendor_grants[0].id})

    required_confirmations: list[str] = []
    if purpose == "native_delegation":
        required_confirmations.append("native_delegation")
    if changed_dimensions:
        required_confirmations.append("endpoint_transition")
    execution_template = {
        "argv_template": list(argv_template),
        "delivery": "argv" if argv_exposure else "stdin",
        "task_slot_count": task_slot_count,
        "transport_version": 1,
        "cleanup": {"grace_seconds": 0, "terminate_grace_seconds": 0},
    }

    descriptor_without_observation: dict[str, Any] = {
        "descriptor_schema_version": 3,
        "compiler_contract_version": 1,
        "purpose": purpose,
        "required_confirmations": required_confirmations,
        "selection": {
            "source_vendor": source.vendor,
            "source_profile_id": source.id,
            "tier": route.tier,
        },
        "source": _profile(source),
        "route": {
            "id": route.id,
            "source_vendor": source.vendor,
            "source_profile_id": route.source_profile_id,
            "tier": route.tier,
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
                for pair in sorted(
                    target.allowed_model_effort_pairs,
                    key=lambda item: (item.model is not None, item.model or "", item.effort),
                )
            ],
        },
        "transition": {
            "id": f"{route.id}-transition",
            "source_profile": _profile(source),
            "destination_profile": _profile(destination),
            "changed_dimensions": changed_dimensions,
            "authorizations": authorizations,
        },
        "task_delivery": "argv" if argv_exposure else "stdin",
        "argv_template": list(argv_template),
        # Keep the conventional descriptor projection available to callers
        # that render reviewed commands; both fields are task-free templates.
        "argv": list(argv_template),
        "confirmations": {
            "cross_endpoint_required": bool(changed_dimensions),
            "argv_task_exposure_required": argv_exposure,
        },
        "execution_template": execution_template,
    }
    encoded = canonical_json_bytes_v2(descriptor_without_observation)
    if len(encoded) > MAX_NATIVE_POLICY_BYTES:
        raise V2ValidationError()
    return StaticNativeSelectionV3(
        executable=target.executable,
        vendor=target.vendor,
        argv_template=argv_template,
        task_delivery="argv" if argv_exposure else "stdin",
        required_confirmations=tuple(required_confirmations),
        descriptor_without_observation_bytes=encoded,
    )


def bind_native_observation_v3(
    selected: StaticNativeSelectionV3,
    observation: ExecutableObservation,
) -> dict[str, object]:
    """Bind one lstat observation to a previously selected task-free route."""
    descriptor = json.loads(selected.descriptor_without_observation_bytes)
    if not isinstance(descriptor, dict):
        raise V2ValidationError()
    descriptor["executable_observation"] = _observation(observation, selected.executable)
    return _bind_descriptor(descriptor)


def compile_native_policy_v3(
    policy: NativePolicyV3,
    *,
    source_vendor: VendorV3,
    source_profile_id: str,
    tier: TierV3,
    purpose: PurposeV3,
    observations: Mapping[str, ExecutableObservation],
) -> dict[str, object]:
    """Compatibility wrapper that selects first and then binds one observation."""
    selected = compile_static_native_policy_v3(
        policy,
        source_vendor=source_vendor,
        source_profile_id=source_profile_id,
        tier=tier,
        purpose=purpose,
    )
    observed = observations.get(selected.executable)
    if observed is None or observed.lexical_path != selected.executable:
        raise V2ValidationError()
    return bind_native_observation_v3(selected, observed)
