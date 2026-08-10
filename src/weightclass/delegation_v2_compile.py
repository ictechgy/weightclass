"""Pure delegation protocol-2 validation stages 3 through 14 and compilation."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Literal, cast

from .canonical_v2 import bind_canonical_descriptor_v2
from .delegation_v2_graph import validate_delegation_v2_graph
from .delegation_v2_schema import (
    GRANT_DIMENSIONS,
    DelegationV2InvalidInputError,
    validate_delegation_v2_binding,
)
from .delegation_v2_types import (
    DelegationGrantV2,
    DelegationManifestV2,
    DelegationPolicyV2,
    DelegationProfileV2,
    DelegationTaskBindingV2,
)
from .native_v2_types import CompiledExecutionV2, FrozenCleanupV2
from .v2_validation import (
    DELEGATION_LIST_PATHS,
    V2ValidationError,
    canonicalize_registered_lists,
)

_DIMENSIONS = cast(tuple[str, ...], GRANT_DIMENSIONS)


def _endpoint(profile: DelegationProfileV2) -> dict[str, str]:
    return {
        "router_profile_id": profile.profile_id,
        "provider": profile.provider,
        "intended_recipient": profile.intended_recipient,
        "billing_boundary": profile.billing_boundary,
        "transport": profile.transport,
        "profile": profile.account_profile,
    }


def _compared(endpoint: dict[str, str]) -> tuple[str, ...]:
    return tuple(endpoint[dimension] for dimension in _DIMENSIONS)


def _transition(
    transition_id: str,
    kind: Literal["source_root", "dependency", "gate"],
    source: DelegationProfileV2,
    destination: DelegationProfileV2,
) -> dict[str, Any]:
    source_endpoint = _endpoint(source)
    destination_endpoint = _endpoint(destination)
    changed = [
        dimension
        for dimension, left, right in zip(
            _DIMENSIONS, _compared(source_endpoint), _compared(destination_endpoint), strict=True
        )
        if left != right
    ]
    return {
        "id": transition_id,
        "kind": kind,
        "from_endpoint": source_endpoint,
        "to_endpoint": destination_endpoint,
        "changed_dimensions": changed,
        "authorizations": [],
    }


def _structural_transitions(
    workflow: Any,
    profiles: dict[str, DelegationProfileV2],
) -> list[dict[str, Any]]:
    task_profiles = {task.task_id: profiles[task.destination_profile_id] for task in workflow.tasks}
    incoming = {task.task_id: 0 for task in workflow.tasks}
    for edge in (*workflow.dependencies, *workflow.gates):
        incoming[edge.to_task_id] += 1
    source = profiles[workflow.eligibility.source_profile_id]
    transitions = [
        _transition(f"source-root-{task_id}", "source_root", source, task_profiles[task_id])
        for task_id, count in incoming.items()
        if count == 0
    ]
    transitions.extend(
        _transition(
            edge.dependency_id,
            "dependency",
            task_profiles[edge.from_task_id],
            task_profiles[edge.to_task_id],
        )
        for edge in workflow.dependencies
    )
    transitions.extend(
        _transition(
            gate.gate_id,
            "gate",
            task_profiles[gate.from_task_id],
            task_profiles[gate.to_task_id],
        )
        for gate in workflow.gates
    )
    if len(transitions) > 224 or len({item["id"] for item in transitions}) != len(transitions):
        raise DelegationV2InvalidInputError()
    return transitions


def _grant_lists(workflow: Any) -> dict[str, tuple[DelegationGrantV2, ...]]:
    return {dimension: getattr(workflow.grants, dimension) for dimension in _DIMENSIONS}


def _authorize(transitions: list[dict[str, Any]], workflow: Any) -> dict[str, list[dict[str, str]]]:
    grants = _grant_lists(workflow)
    used: dict[str, set[str]] = {dimension: set() for dimension in _DIMENSIONS}
    for dimension in _DIMENSIONS:
        candidates = grants[dimension]
        if any(grant.from_value == grant.to_value for grant in candidates):
            raise DelegationV2InvalidInputError()
        pairs = [(grant.from_value, grant.to_value) for grant in candidates]
        if len(pairs) != len(set(pairs)):
            raise DelegationV2InvalidInputError()
    for transition in transitions:
        source = transition["from_endpoint"]
        destination = transition["to_endpoint"]
        authorizations: list[dict[str, str]] = []
        for dimension in transition["changed_dimensions"]:
            matches = [
                grant
                for grant in grants[dimension]
                if (grant.from_value, grant.to_value) == (source[dimension], destination[dimension])
            ]
            if len(matches) != 1:
                raise DelegationV2InvalidInputError()
            grant = matches[0]
            used[dimension].add(grant.grant_id)
            authorizations.append({"dimension": dimension, "grant_id": grant.grant_id})
        transition["authorizations"] = authorizations
    if any(
        used[dimension] != {grant.grant_id for grant in grants[dimension]}
        for dimension in _DIMENSIONS
    ):
        raise DelegationV2InvalidInputError()
    return {
        dimension: [
            {"id": grant.grant_id, "from": grant.from_value, "to": grant.to_value}
            for grant in grants[dimension]
        ]
        for dimension in _DIMENSIONS
    }


def _task(task: DelegationTaskBindingV2) -> dict[str, Any]:
    value = asdict(task)
    renames = {
        "task_id": "id",
        "artifact_id": "id",
        "output_id": "id",
        "input_id": "id",
        "projection_id": "id",
        "tool_id": "id",
        "output_type": "type",
        "input_type": "type",
    }

    def rename(node: object) -> object:
        if isinstance(node, dict):
            return {renames.get(key, key): rename(child) for key, child in node.items()}
        if isinstance(node, tuple):
            return [rename(child) for child in node]
        return node

    return cast(dict[str, Any], rename(value))


def compile_delegation_v2(
    policy: DelegationPolicyV2,
    manifest: DelegationManifestV2,
    *,
    source_vendor_family: str,
    source_profile_id: str,
    tier: str,
    runtime_path: str,
) -> CompiledExecutionV2:
    """Select and compile one protocol-2 workflow without task or runtime access."""
    binding = validate_delegation_v2_binding(
        policy,
        manifest,
        source_vendor_family=source_vendor_family,
        source_profile_id=source_profile_id,
        tier=tier,
        runtime_path=runtime_path,
    )
    workflow = next(item for item in policy.workflows if item.workflow_id == binding.workflow_id)
    validate_delegation_v2_graph(workflow)
    profiles = {profile.profile_id: profile for profile in policy.profiles}
    runtime = next(item for item in manifest.runtimes if item.runtime_id == binding.runtime_id)
    transitions = _structural_transitions(workflow, profiles)  # stage 11
    used_grants = _authorize(transitions, workflow)  # stage 12
    selected_profile_ids = {workflow.eligibility.source_profile_id} | {
        task.destination_profile_id for task in workflow.tasks
    }
    argv = (runtime.path, "--weightclass-delegation-protocol", "2")
    descriptor: dict[str, Any] = {
        "descriptor_schema_version": 2,
        "compiler_contract_version": 2,
        "runtime_protocol_version": 2,
        "frame_version": "WCD2",
        "workflow": {
            "id": workflow.workflow_id,
            "requested_run_id": workflow.requested_run_id,
            "eligibility": asdict(workflow.eligibility),
            "terminal_mode": workflow.terminal_mode,
            "terminal_task_id": workflow.terminal_task_id,
            "concurrency": workflow.concurrency,
        },
        "profiles": [
            {
                "id": profile.profile_id,
                "provider": profile.provider,
                "intended_recipient": profile.intended_recipient,
                "billing_boundary": profile.billing_boundary,
                "transport": profile.transport,
                "account_profile": profile.account_profile,
                "capabilities": list(profile.capabilities),
                "allowed_model_effort_pairs": [
                    asdict(pair) for pair in profile.allowed_model_effort_pairs
                ],
            }
            for profile in policy.profiles
            if profile.profile_id in selected_profile_ids
        ],
        "runtime": {
            "id": runtime.runtime_id,
            "path": runtime.path,
            "adapter": {
                "id": runtime.adapter.adapter_id,
                "vendor_family": runtime.adapter.vendor_family,
                "provider": runtime.adapter.provider,
                "runtime_build_id": runtime.adapter.runtime_build_id,
                "supported_transports": list(runtime.adapter.supported_transports),
                "capabilities": list(runtime.adapter.capabilities),
            },
        },
        "tasks": [_task(task) for task in workflow.tasks],
        "dependency_edges": [
            {
                "id": edge.dependency_id,
                "from_task_id": edge.from_task_id,
                "to_task_id": edge.to_task_id,
            }
            for edge in workflow.dependencies
        ],
        "gate_edges": [
            {
                "id": gate.gate_id,
                "from_task_id": gate.from_task_id,
                "to_task_id": gate.to_task_id,
                "output": asdict(gate.output),
                "predicate": asdict(gate.predicate),
            }
            for gate in workflow.gates
        ],
        "transitions": transitions,
        "grants": used_grants,
        "argv": list(argv),
    }
    try:
        canonical = canonicalize_registered_lists(descriptor, DELEGATION_LIST_PATHS)
        return bind_canonical_descriptor_v2(
            canonical,
            argv=argv,
            executable=runtime.path,
            transport="wcd2_stdin",
            transport_version=2,
            cleanup=FrozenCleanupV2(max(task.cleanup.grace_seconds for task in workflow.tasks), 0),
        )
    except V2ValidationError:
        raise DelegationV2InvalidInputError() from None
