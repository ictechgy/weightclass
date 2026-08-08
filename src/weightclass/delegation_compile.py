"""Pure compilation of reviewed delegation policy into one descriptor."""

import hashlib
import json
from dataclasses import asdict
from typing import Any, Final, TypeVar

from .delegation_schema import (
    CATEGORIES,
    MAX_ARTIFACT_ID_BYTES,
    MAX_CONTEXT_ID_BYTES,
    ROLES,
    RUNTIME_PROTOCOL_VERSION,
    DelegationInvalidInputError,
    DelegationUnsupportedError,
)
from .delegation_types import (
    BoundaryAuthorizations,
    DelegatedAssignment,
    DelegationAdapter,
    DelegationPolicy,
    DelegationProfile,
    DelegationRuntimeManifest,
    DelegationTier,
    DelegationWorkflow,
    PlatformContract,
    RoleName,
    VendorFamily,
)

MAX_FINGERPRINT_PAYLOAD_BYTES: Final = 262_144
MAX_REVIEW_DESCRIPTOR_BYTES: Final = 262_144
MAX_RUNTIME_DESCRIPTOR_BYTES: Final = 262_144
MAX_TASK_BYTES: Final = 80_000
MAX_COMPLETE_FRAME_BYTES: Final = 342_156

_ORCHESTRATOR_ACTIONS: Final = {
    "workspace_read": "allow",
    "workspace_write": "deny",
    "command_execution": "deny",
}
_REVIEWER_ACTIONS: Final = dict(_ORCHESTRATOR_ACTIONS)
_WORKER_ACTIONS: Final = {
    "implementation": {
        "workspace_read": "allow",
        "workspace_write": "allow",
        "command_execution": "allow",
    },
    "tests": {
        "workspace_read": "allow",
        "workspace_write": "allow",
        "command_execution": "allow",
    },
    "documentation": {
        "workspace_read": "allow",
        "workspace_write": "allow",
        "command_execution": "deny",
    },
}
_INTEGRATION_ACTIONS: Final = {
    "workspace_read": "allow",
    "workspace_write": "allow",
    "command_execution": "allow",
}
_STAGES: Final = (
    "validated",
    "planned",
    "required_assignments_created",
    "workers_completed",
    "reviewer_approved",
    "integration_completed",
    "descendants_reaped",
    "success",
)
_SelectedT = TypeVar("_SelectedT")


def canonical_json_bytes(value: object) -> bytes:
    """Encode one descriptor representation deterministically."""
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def bind_delegation_fingerprint(descriptor: dict[str, Any]) -> dict[str, Any]:
    """Return a descriptor whose fingerprint binds every other rendered field."""
    bound = dict(descriptor)
    bound.pop("route_fingerprint", None)
    fingerprint_payload = canonical_json_bytes(bound)
    if len(fingerprint_payload) > MAX_FINGERPRINT_PAYLOAD_BYTES:
        raise DelegationInvalidInputError()
    bound["route_fingerprint"] = f"sha256:{hashlib.sha256(fingerprint_payload).hexdigest()}"
    if len(canonical_json_bytes(bound)) > MAX_REVIEW_DESCRIPTOR_BYTES:
        raise DelegationInvalidInputError()
    return bound


def _select_one(items: list[_SelectedT]) -> _SelectedT:
    if len(items) != 1:
        raise DelegationUnsupportedError()
    return items[0]


def _boundaries_are_empty(boundaries: BoundaryAuthorizations) -> bool:
    return not (
        boundaries.provider_pairs
        or boundaries.recipient_pairs
        or boundaries.billing_pairs
        or boundaries.mixed_transport_pairs
    )


def _select_workflow(
    policy: DelegationPolicy,
    source_vendor: VendorFamily,
    tier: DelegationTier,
) -> DelegationWorkflow:
    return _select_one(
        [
            workflow
            for workflow in policy.workflows
            if source_vendor in workflow.eligible_source_vendors and tier in workflow.eligible_tiers
        ]
    )


def _select_profiles(
    policy: DelegationPolicy,
    workflow: DelegationWorkflow,
    source_vendor: VendorFamily,
) -> dict[RoleName, DelegationProfile]:
    profiles_by_id = {profile.profile_id: profile for profile in policy.profiles}
    selected: dict[RoleName, DelegationProfile] = {}
    for role, profile_id in workflow.profile_ids:
        profile = profiles_by_id.get(profile_id)
        if (
            profile is None
            or profile.role != role
            or profile.vendor_family != source_vendor
            or profile.transport != "native"
            or set(profile.allowed_categories) != set(CATEGORIES)
        ):
            raise DelegationUnsupportedError()
        selected[role] = profile
    if set(selected) != {"orchestrator", "worker", "reviewer"}:
        raise DelegationUnsupportedError()
    if (
        selected["worker"].global_role_process_limit < 3
        or selected["orchestrator"].global_role_process_limit < 1
        or selected["reviewer"].global_role_process_limit < 1
    ):
        raise DelegationUnsupportedError()
    return selected


def _select_adapter(
    manifest: DelegationRuntimeManifest,
    workflow: DelegationWorkflow,
    source_vendor: VendorFamily,
) -> DelegationAdapter:
    adapter = _select_one(
        [adapter for adapter in manifest.adapters if adapter.adapter_id == workflow.adapter_id]
    )
    if (
        adapter.vendor_family != source_vendor
        or adapter.transports != ("native",)
        or adapter.global_role_process_limit < 3
    ):
        raise DelegationUnsupportedError()
    return adapter


def _profile_descriptor(profile: DelegationProfile) -> dict[str, object]:
    return {
        "id": profile.profile_id,
        "role": profile.role,
        "vendor_family": profile.vendor_family,
        "transport": profile.transport,
        "model": profile.model,
        "effort": profile.effort,
        "allowed_categories": list(CATEGORIES),
        "global_role_process_limit": profile.global_role_process_limit,
    }


def _assignment_descriptor(assignment: DelegatedAssignment) -> dict[str, object]:
    return {
        "category": assignment.category,
        "execution": assignment.execution,
        "review": assignment.review,
        "retention": asdict(assignment.retention),
        "integration": assignment.integration,
        "worker_actions": dict(_WORKER_ACTIONS[assignment.category]),
    }


def _adapter_descriptor(adapter: DelegationAdapter, runtime_build_id: str) -> dict[str, object]:
    return {
        "id": adapter.adapter_id,
        "vendor_family": adapter.vendor_family,
        "transports": list(adapter.transports),
        "runtime_build_id": runtime_build_id,
        "global_role_process_limit": adapter.global_role_process_limit,
        "capabilities": list(adapter.capabilities),
        "enforcement_primitives": asdict(adapter.enforcement_primitives),
    }


def _boundary_descriptor(boundaries: BoundaryAuthorizations) -> dict[str, object]:
    def render_pair(source: str, destination: str) -> dict[str, str]:
        return {"from": source, "to": destination}

    return {
        "provider_pairs": [
            render_pair(pair.source, pair.destination) for pair in boundaries.provider_pairs
        ],
        "recipient_pairs": [
            render_pair(pair.source, pair.destination) for pair in boundaries.recipient_pairs
        ],
        "billing_pairs": [
            render_pair(pair.source, pair.destination) for pair in boundaries.billing_pairs
        ],
        "mixed_transport_pairs": [
            render_pair(pair.source, pair.destination) for pair in boundaries.mixed_transport_pairs
        ],
    }


def compile_delegation_descriptor(
    policy: DelegationPolicy,
    manifest: DelegationRuntimeManifest,
    *,
    runtime_path: str,
    source_vendor: VendorFamily,
    tier: DelegationTier,
    target_platform: PlatformContract,
) -> dict[str, Any]:
    """Compile only reviewed configuration; never inspect runtime or task data."""
    if RUNTIME_PROTOCOL_VERSION not in manifest.runtime_protocol_versions:
        raise DelegationUnsupportedError()
    if manifest.supported_platforms.count(target_platform) != 1:
        raise DelegationUnsupportedError()
    workflow = _select_workflow(policy, source_vendor, tier)
    profiles = _select_profiles(policy, workflow, source_vendor)
    adapter = _select_adapter(manifest, workflow, source_vendor)
    if not _boundaries_are_empty(workflow.boundary_authorizations):
        raise DelegationUnsupportedError()

    assignments_by_category = {
        assignment.category: assignment for assignment in workflow.assignments
    }
    descriptor: dict[str, Any] = {
        "descriptor_schema_version": 1,
        "runtime_protocol_version": RUNTIME_PROTOCOL_VERSION,
        "schema_compatible": True,
        "assurance": "declared_enforcement",
        "run_requirement": {"kind": "trusted_runtime_confirmation"},
        "workflow_id": workflow.workflow_id,
        "source_vendor": source_vendor,
        "tier": tier,
        "runtime_path": runtime_path,
        "target_platform": asdict(target_platform),
        "roles": {role: _profile_descriptor(profiles[role]) for role in ROLES},
        "role_actions": {
            "orchestrator": dict(_ORCHESTRATOR_ACTIONS),
            "reviewer": dict(_REVIEWER_ACTIONS),
        },
        "assignments": [
            _assignment_descriptor(assignments_by_category[category]) for category in CATEGORIES
        ],
        "adapter": _adapter_descriptor(adapter, manifest.runtime_build_id),
        "stage_contract": {
            "ordered_states": list(_STAGES),
            "only_worker_contexts_may_overlap": True,
            "required_worker_contexts": 3,
            "maximum_simultaneous_role_processes": 3,
            "maximum_simultaneous_role_or_helper_processes": 3,
            "enforced_by": "external_runtime",
            "on_not_applicable": "runtime_must_fail_without_dummy_or_skipped_assignment",
        },
        "integration": {
            "mode": "mechanical_runtime",
            "inputs": list(workflow.integration.inputs),
            "allowed_operations": list(workflow.integration.allowed_operations),
            "verification_commands": [
                list(command) for command in workflow.integration.verification_commands
            ],
            "actions": dict(_INTEGRATION_ACTIONS),
            "reviewer_approval_required": True,
        },
        "artifact_integrity": {
            "artifact_id_syntax": "[a-z][a-z0-9._-]{0,63}",
            "artifact_id_max_bytes": MAX_ARTIFACT_ID_BYTES,
            "context_id_max_bytes": MAX_CONTEXT_ID_BYTES,
            "binding": "immutable_worker_context_and_category_per_run",
            "review_approval": "bind_exact_artifact_id_and_content",
            "integration_rejects": [
                "altered",
                "cross_category",
                "duplicate",
                "missing",
                "unapproved",
            ],
            "enforced_by": "external_runtime",
        },
        "runtime_contract": {
            "runtime_deadline_seconds": workflow.runtime_deadline_seconds,
            "deadline_enforced_by": "external_runtime",
            "direct_child_cleanup": {
                **asdict(workflow.direct_child_cleanup),
                "sequence": ["close", "wait", "terminate", "wait", "kill", "reap"],
                "weightclass_enumerates_descendants": False,
                "descendant_leakage_fails_conformance": True,
            },
            "stdout_stderr": {
                "mode": "inherited",
                "captured": False,
                "parsed": False,
                "redacted": False,
                "byte_limit": "none",
                "outside_weightclass_no_retention_after_emission": True,
            },
        },
        "byte_contract": {
            "fingerprint_payload_max_bytes": MAX_FINGERPRINT_PAYLOAD_BYTES,
            "review_descriptor_max_bytes": MAX_REVIEW_DESCRIPTOR_BYTES,
            "runtime_descriptor_max_bytes": MAX_RUNTIME_DESCRIPTOR_BYTES,
            "runtime_descriptor_is_review_descriptor": True,
            "task_max_bytes": MAX_TASK_BYTES,
            "complete_frame_max_bytes": MAX_COMPLETE_FRAME_BYTES,
        },
        "boundary_authorizations": _boundary_descriptor(workflow.boundary_authorizations),
    }
    return bind_delegation_fingerprint(descriptor)


def render_review_descriptor(descriptor: dict[str, Any]) -> str:
    """Return the exact review/runtime descriptor representation without a newline."""
    encoded = canonical_json_bytes(descriptor)
    if len(encoded) > MAX_REVIEW_DESCRIPTOR_BYTES:
        raise DelegationInvalidInputError()
    return encoded.decode("ascii")
