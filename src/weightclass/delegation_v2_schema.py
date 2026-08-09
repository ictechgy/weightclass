"""Pure pre-graph structural parser for delegation protocol 2."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Hashable, Sequence
from typing import Any, TypeVar

from .delegation_v2_types import (
    DelegationAdapterV2,
    DelegationAllowedPairV2,
    DelegationArtifactV2,
    DelegationCleanupV2,
    DelegationDependencyV2,
    DelegationDimensionGrantsV2,
    DelegationGateOutputV2,
    DelegationGatePredicateV2,
    DelegationGateV2,
    DelegationGrantV2,
    DelegationInputV2,
    DelegationManifestV2,
    DelegationOutputV2,
    DelegationPermissionV2,
    DelegationPolicyV2,
    DelegationProfileV2,
    DelegationProjectionV2,
    DelegationProviderFamilyMappingV2,
    DelegationRequestV2,
    DelegationRuntimeV2,
    DelegationSettlementV2,
    DelegationTaskBindingV2,
    DelegationToolV2,
    DelegationV2Binding,
    DelegationWorkflowV2,
    DelegationWorktreeV2,
    EligibilityV2,
    OutputTypeV2,
    Provider,
    TransportV2,
    VendorFamilyV2,
)
from .delegation_v2_versions import DelegationVersionError, dispatch_delegation_versions

PROVIDER_VENDOR_FAMILY: dict[Provider, VendorFamilyV2] = {
    "openai": "codex",
    "anthropic": "claude",
}
GRANT_DIMENSIONS = (
    "provider",
    "intended_recipient",
    "billing_boundary",
    "transport",
    "profile",
)
OUTPUT_TYPES: tuple[OutputTypeV2, ...] = ("text", "json", "boolean", "artifact_ref")
MAX_DOCUMENT_BYTES = 262_144

_T = TypeVar("_T")


class DelegationV2InvalidInputError(ValueError):
    """A value-free protocol-2 structural failure."""


def _obj(value: object, keys: set[str]) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != keys
        or not all(isinstance(key, str) for key in value)
    ):
        raise DelegationV2InvalidInputError()
    return value


def _array(value: object, low: int, high: int) -> list[Any]:
    if not isinstance(value, list) or not low <= len(value) <= high:
        raise DelegationV2InvalidInputError()
    return value


def _encoded_string(value: object, maximum: int, *, minimum: int = 1) -> tuple[str, int]:
    if not isinstance(value, str):
        raise DelegationV2InvalidInputError()
    try:
        size = len(value.encode("utf-8", errors="strict"))
    except UnicodeEncodeError:
        raise DelegationV2InvalidInputError() from None
    if not minimum <= size <= maximum:
        raise DelegationV2InvalidInputError()
    return value, size


def _identifier(value: object) -> str:
    result, _ = _encoded_string(value, 64)
    if any(
        character.isspace() or unicodedata.category(character).startswith("C")
        for character in result
    ):
        raise DelegationV2InvalidInputError()
    return result


def _label(value: object, maximum: int = 240) -> str:
    result, _ = _encoded_string(value, maximum)
    if result != result.strip(" ") or any(
        unicodedata.category(character).startswith("C")
        or (character.isspace() and character != " ")
        for character in result
    ):
        raise DelegationV2InvalidInputError()
    return result


def _instruction(value: object) -> str:
    result, _ = _encoded_string(value, 16_384)
    return result


def _runtime_path(value: object) -> str:
    result = _label(value, 4_096)
    if not result.startswith("/"):
        raise DelegationV2InvalidInputError()
    if result == "/":
        return result
    components = result[1:].split("/")
    if any(
        not component or component in {".", ".."} or component != component.strip(" ")
        for component in components
    ):
        raise DelegationV2InvalidInputError()
    return result


def _integer(value: object, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise DelegationV2InvalidInputError()
    return value


def _enum(value: object, choices: tuple[_T, ...]) -> _T:
    if not isinstance(value, str) or value not in choices:
        raise DelegationV2InvalidInputError()
    return value


def _unique(values: Sequence[Hashable]) -> None:
    if len(values) != len(set(values)):
        raise DelegationV2InvalidInputError()


def _require_document_bound(value: object) -> None:
    try:
        size = len(
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise DelegationV2InvalidInputError() from None
    if size > MAX_DOCUMENT_BYTES:
        raise DelegationV2InvalidInputError()


def _parse_request(value: object) -> DelegationRequestV2:
    request = _obj(value, {"mode", "permissions", "tools"})
    permissions_raw = _obj(request["permissions"], {"filesystem", "commands"})
    permissions = DelegationPermissionV2(
        _enum(permissions_raw["filesystem"], ("none", "read", "write")),
        _enum(permissions_raw["commands"], ("deny", "allow")),
    )
    tools: list[DelegationToolV2] = []
    for item in _array(request["tools"], 0, 32):
        tool = _obj(item, {"id", "mode"})
        tools.append(
            DelegationToolV2(
                _identifier(tool["id"]),
                _enum(tool["mode"], ("allow", "deny")),
            )
        )
    return DelegationRequestV2(
        _enum(request["mode"], ("independent", "synthesizer")),
        permissions,
        tuple(tools),
    )


def _parse_outputs(value: object) -> tuple[DelegationOutputV2, ...]:
    outputs: list[DelegationOutputV2] = []
    for item in _array(value, 0, 64):
        output = _obj(item, {"id", "type", "artifacts"})
        artifacts: list[DelegationArtifactV2] = []
        for artifact_item in _array(output["artifacts"], 0, 128):
            artifact = _obj(artifact_item, {"id", "media_type"})
            artifacts.append(
                DelegationArtifactV2(
                    _identifier(artifact["id"]),
                    _label(artifact["media_type"]),
                )
            )
        outputs.append(
            DelegationOutputV2(
                _identifier(output["id"]),
                _enum(output["type"], OUTPUT_TYPES),
                tuple(artifacts),
            )
        )
    return tuple(outputs)


def _parse_inputs(value: object) -> tuple[DelegationInputV2, ...]:
    inputs: list[DelegationInputV2] = []
    for item in _array(value, 0, 32):
        input_value = _obj(item, {"id", "type"})
        inputs.append(
            DelegationInputV2(
                _identifier(input_value["id"]),
                _enum(input_value["type"], OUTPUT_TYPES),
            )
        )
    return tuple(inputs)


def _parse_projections(value: object) -> tuple[DelegationProjectionV2, ...]:
    projections: list[DelegationProjectionV2] = []
    for item in _array(value, 0, 32):
        projection = _obj(
            item,
            {"id", "input_id", "producer_task_id", "producer_output_id"},
        )
        projections.append(
            DelegationProjectionV2(
                _identifier(projection["id"]),
                _identifier(projection["input_id"]),
                _identifier(projection["producer_task_id"]),
                _identifier(projection["producer_output_id"]),
            )
        )
    return tuple(projections)


def _parse_task(value: object) -> DelegationTaskBindingV2:
    task = _obj(
        value,
        {
            "id",
            "requested_task_id",
            "requested_dispatch_id",
            "owner_role",
            "destination_profile_id",
            "adapter_id",
            "model",
            "effort",
            "instruction",
            "request",
            "worktree",
            "settlement",
            "cleanup",
            "outputs",
            "inputs",
            "projections",
            "mutable_scopes",
            "capabilities",
            "turns",
            "deadline_seconds",
            "attempts",
        },
    )
    worktree_raw = _obj(task["worktree"], {"mode", "scope"})
    scope = worktree_raw["scope"]
    parsed_scope = None if scope is None else _label(scope, 4_096)
    settlement_raw = _obj(task["settlement"], {"mode"})
    cleanup_raw = _obj(task["cleanup"], {"mode", "grace_seconds"})
    scopes = [_label(item, 4_096) for item in _array(task["mutable_scopes"], 0, 16)]
    capabilities = [_identifier(item) for item in _array(task["capabilities"], 0, 32)]
    return DelegationTaskBindingV2(
        task_id=_identifier(task["id"]),
        requested_task_id=_identifier(task["requested_task_id"]),
        requested_dispatch_id=_identifier(task["requested_dispatch_id"]),
        owner_role=_label(task["owner_role"], 64),
        destination_profile_id=_identifier(task["destination_profile_id"]),
        adapter_id=_identifier(task["adapter_id"]),
        model=_label(task["model"]),
        effort=_label(task["effort"]),
        instruction=_instruction(task["instruction"]),
        request=_parse_request(task["request"]),
        worktree=DelegationWorktreeV2(
            _enum(worktree_raw["mode"], ("none", "read_only", "mutable")),
            parsed_scope,
        ),
        settlement=DelegationSettlementV2(
            _enum(settlement_raw["mode"], ("requested_external_settlement",))
        ),
        cleanup=DelegationCleanupV2(
            _enum(cleanup_raw["mode"], ("direct_child_only",)),
            _integer(cleanup_raw["grace_seconds"], 0, 86_400),
        ),
        outputs=_parse_outputs(task["outputs"]),
        inputs=_parse_inputs(task["inputs"]),
        projections=_parse_projections(task["projections"]),
        mutable_scopes=tuple(scopes),
        capabilities=tuple(capabilities),
        turns=_integer(task["turns"], 1, 256),
        deadline_seconds=_integer(task["deadline_seconds"], 1, 86_400),
        attempts=_integer(task["attempts"], 1, 1),
    )


def _parse_dependency(value: object) -> DelegationDependencyV2:
    dependency = _obj(value, {"id", "from_task_id", "to_task_id"})
    return DelegationDependencyV2(
        _identifier(dependency["id"]),
        _identifier(dependency["from_task_id"]),
        _identifier(dependency["to_task_id"]),
    )


def _parse_gate(value: object) -> DelegationGateV2:
    gate = _obj(value, {"id", "from_task_id", "to_task_id", "output", "predicate"})
    output = _obj(
        gate["output"],
        {"producer_task_id", "producer_output_id", "required_type"},
    )
    predicate = _obj(gate["predicate"], {"operator", "value"})
    return DelegationGateV2(
        gate_id=_identifier(gate["id"]),
        from_task_id=_identifier(gate["from_task_id"]),
        to_task_id=_identifier(gate["to_task_id"]),
        output=DelegationGateOutputV2(
            _identifier(output["producer_task_id"]),
            _identifier(output["producer_output_id"]),
            _enum(output["required_type"], ("weightclass.gate/v1",)),
        ),
        predicate=DelegationGatePredicateV2(
            _enum(predicate["operator"], ("equals",)),
            _enum(predicate["value"], ("approved",)),
        ),
    )


def _parse_grants(value: object) -> DelegationDimensionGrantsV2:
    grants = _obj(value, set(GRANT_DIMENSIONS))
    by_dimension: dict[str, tuple[DelegationGrantV2, ...]] = {}
    for dimension in GRANT_DIMENSIONS:
        parsed: list[DelegationGrantV2] = []
        for item in _array(grants[dimension], 0, 128):
            grant = _obj(item, {"id", "from", "to"})
            parsed.append(
                DelegationGrantV2(
                    _identifier(grant["id"]),
                    _label(grant["from"]),
                    _label(grant["to"]),
                )
            )
        by_dimension[dimension] = tuple(parsed)
    return DelegationDimensionGrantsV2(
        provider=by_dimension["provider"],
        intended_recipient=by_dimension["intended_recipient"],
        billing_boundary=by_dimension["billing_boundary"],
        transport=by_dimension["transport"],
        profile=by_dimension["profile"],
    )


def _parse_profile(value: object) -> DelegationProfileV2:
    profile = _obj(
        value,
        {
            "id",
            "provider",
            "intended_recipient",
            "billing_boundary",
            "transport",
            "account_profile",
            "capabilities",
            "allowed_model_effort_pairs",
        },
    )
    capabilities = [_identifier(item) for item in _array(profile["capabilities"], 0, 32)]
    pairs: list[DelegationAllowedPairV2] = []
    for item in _array(profile["allowed_model_effort_pairs"], 1, 64):
        pair = _obj(item, {"model", "effort"})
        pairs.append(DelegationAllowedPairV2(_label(pair["model"]), _label(pair["effort"])))
    return DelegationProfileV2(
        profile_id=_identifier(profile["id"]),
        provider=_enum(profile["provider"], ("openai", "anthropic")),
        intended_recipient=_label(profile["intended_recipient"]),
        billing_boundary=_label(profile["billing_boundary"]),
        transport=_enum(profile["transport"], ("native",)),
        account_profile=_label(profile["account_profile"]),
        capabilities=tuple(capabilities),
        allowed_model_effort_pairs=tuple(pairs),
    )


def _parse_workflow(value: object) -> DelegationWorkflowV2:
    workflow = _obj(
        value,
        {
            "id",
            "requested_run_id",
            "eligibility",
            "terminal_mode",
            "terminal_task_id",
            "concurrency",
            "tasks",
            "dependency_edges",
            "gate_edges",
            "grants",
        },
    )
    eligibility = _obj(
        workflow["eligibility"],
        {"source_vendor_family", "source_profile_id", "tier"},
    )
    terminal_task_id = workflow["terminal_task_id"]
    parsed_terminal_task_id = None if terminal_task_id is None else _identifier(terminal_task_id)
    tasks = tuple(_parse_task(item) for item in _array(workflow["tasks"], 1, 32))
    dependencies = tuple(
        _parse_dependency(item) for item in _array(workflow["dependency_edges"], 0, 128)
    )
    gates = tuple(_parse_gate(item) for item in _array(workflow["gate_edges"], 0, 64))
    if sum(len(task.instruction.encode("utf-8")) for task in tasks) > 131_072:
        raise DelegationV2InvalidInputError()
    if sum(len(output.artifacts) for task in tasks for output in task.outputs) > 128:
        raise DelegationV2InvalidInputError()
    return DelegationWorkflowV2(
        workflow_id=_identifier(workflow["id"]),
        requested_run_id=_identifier(workflow["requested_run_id"]),
        eligibility=EligibilityV2(
            _enum(eligibility["source_vendor_family"], ("codex", "claude")),
            _identifier(eligibility["source_profile_id"]),
            _enum(eligibility["tier"], ("low", "standard", "high")),
        ),
        terminal_mode=_enum(workflow["terminal_mode"], ("synthesized", "raw_independent")),
        terminal_task_id=parsed_terminal_task_id,
        concurrency=_integer(workflow["concurrency"], 1, 32),
        tasks=tasks,
        dependencies=dependencies,
        gates=gates,
        grants=_parse_grants(workflow["grants"]),
    )


def _validate_workflow_uniqueness(workflow: DelegationWorkflowV2) -> None:
    _unique([task.task_id for task in workflow.tasks])
    _unique([dependency.dependency_id for dependency in workflow.dependencies])
    _unique([gate.gate_id for gate in workflow.gates])
    for task in workflow.tasks:
        _unique([tool.tool_id for tool in task.request.tools])
        _unique([input_value.input_id for input_value in task.inputs])
        _unique(task.mutable_scopes)
        _unique(task.capabilities)
    for grants in (
        workflow.grants.provider,
        workflow.grants.intended_recipient,
        workflow.grants.billing_boundary,
        workflow.grants.transport,
        workflow.grants.profile,
    ):
        _unique([grant.grant_id for grant in grants])


def parse_delegation_policy_v2(raw: object) -> DelegationPolicyV2:
    policy = _obj(
        raw,
        {
            "schema_version",
            "manifest_schema_version",
            "compiler_contract_version",
            "runtime_protocol_version",
            "frame_version",
            "profiles",
            "workflows",
        },
    )
    _require_document_bound(raw)
    try:
        version = dispatch_delegation_versions(
            (
                policy["schema_version"],
                policy["manifest_schema_version"],
                policy["compiler_contract_version"],
                policy["runtime_protocol_version"],
                policy["frame_version"],
            )
        )
    except DelegationVersionError:
        raise DelegationV2InvalidInputError() from None
    if version != 2:
        raise DelegationV2InvalidInputError()
    profiles = tuple(_parse_profile(item) for item in _array(policy["profiles"], 1, 64))
    workflows = tuple(_parse_workflow(item) for item in _array(policy["workflows"], 1, 32))
    for profile in profiles:
        _unique(profile.capabilities)
        _unique([(pair.model, pair.effort) for pair in profile.allowed_model_effort_pairs])
    for workflow in workflows:
        _validate_workflow_uniqueness(workflow)
    _unique([profile.profile_id for profile in profiles])
    _unique([workflow.workflow_id for workflow in workflows])
    return DelegationPolicyV2(
        schema_version=2,
        manifest_schema_version=2,
        compiler_contract_version=2,
        runtime_protocol_version=2,
        frame_version="WCD2",
        profiles=profiles,
        workflows=workflows,
    )


def _parse_mapping(value: object) -> DelegationProviderFamilyMappingV2:
    mapping = _obj(value, {"provider", "vendor_family"})
    return DelegationProviderFamilyMappingV2(
        _enum(mapping["provider"], ("openai", "anthropic")),
        _enum(mapping["vendor_family"], ("codex", "claude")),
    )


def _parse_runtime(value: object) -> DelegationRuntimeV2:
    runtime = _obj(value, {"id", "path", "adapter"})
    adapter = _obj(
        runtime["adapter"],
        {
            "id",
            "vendor_family",
            "provider",
            "runtime_build_id",
            "supported_transports",
            "capabilities",
        },
    )
    transports: list[TransportV2] = [
        _enum(item, ("native",)) for item in _array(adapter["supported_transports"], 1, 16)
    ]
    capabilities = [_identifier(item) for item in _array(adapter["capabilities"], 0, 32)]
    parsed_adapter = DelegationAdapterV2(
        adapter_id=_identifier(adapter["id"]),
        vendor_family=_enum(adapter["vendor_family"], ("codex", "claude")),
        provider=_enum(adapter["provider"], ("openai", "anthropic")),
        runtime_build_id=_label(adapter["runtime_build_id"]),
        supported_transports=tuple(transports),
        capabilities=tuple(capabilities),
    )
    return DelegationRuntimeV2(
        _identifier(runtime["id"]),
        _runtime_path(runtime["path"]),
        parsed_adapter,
    )


def parse_delegation_manifest_v2(raw: object) -> DelegationManifestV2:
    manifest = _obj(raw, {"schema_version", "provider_family_mappings", "runtimes"})
    _require_document_bound(raw)
    _integer(manifest["schema_version"], 2, 2)
    mappings = tuple(
        _parse_mapping(item) for item in _array(manifest["provider_family_mappings"], 2, 2)
    )
    runtimes = tuple(_parse_runtime(item) for item in _array(manifest["runtimes"], 1, 64))
    if {mapping.provider: mapping.vendor_family for mapping in mappings} != (
        PROVIDER_VENDOR_FAMILY
    ) or len({mapping.provider for mapping in mappings}) != 2:
        raise DelegationV2InvalidInputError()
    for runtime in runtimes:
        _unique(runtime.adapter.supported_transports)
        _unique(runtime.adapter.capabilities)
    _unique([runtime.runtime_id for runtime in runtimes])
    _unique([runtime.adapter.adapter_id for runtime in runtimes])
    return DelegationManifestV2(2, mappings, runtimes)


def _validate_source_profile_coherence(
    policy: DelegationPolicyV2,
    manifest: DelegationManifestV2,
) -> tuple[dict[str, DelegationProfileV2], dict[Provider, VendorFamilyV2]]:
    profiles = {profile.profile_id: profile for profile in policy.profiles}
    mappings = {
        mapping.provider: mapping.vendor_family for mapping in manifest.provider_family_mappings
    }
    for workflow in policy.workflows:
        source_profile = profiles.get(workflow.eligibility.source_profile_id)
        if (
            source_profile is None
            or mappings.get(source_profile.provider) != workflow.eligibility.source_vendor_family
        ):
            raise DelegationV2InvalidInputError()
    return profiles, mappings


def validate_delegation_v2_binding(
    policy: DelegationPolicyV2,
    manifest: DelegationManifestV2,
    *,
    source_vendor_family: str,
    source_profile_id: str,
    tier: str,
    runtime_path: str,
) -> DelegationV2Binding:
    """Bind one exact selector and reviewed manifest runtime without graph validation."""
    validated_selector = (
        _enum(source_vendor_family, ("codex", "claude")),
        _identifier(source_profile_id),
        _enum(tier, ("low", "standard", "high")),
    )
    validated_runtime_path = _runtime_path(runtime_path)
    # Source declarations are coherent before exact-selector uniqueness or any
    # destination endpoint and adapter derivation.
    profiles, mappings = _validate_source_profile_coherence(policy, manifest)
    matches = [
        workflow
        for workflow in policy.workflows
        if (
            workflow.eligibility.source_vendor_family,
            workflow.eligibility.source_profile_id,
            workflow.eligibility.tier,
        )
        == validated_selector
    ]
    if len(matches) != 1:
        raise DelegationV2InvalidInputError()
    selected_workflow = matches[0]

    runtimes = {runtime.adapter.adapter_id: runtime for runtime in manifest.runtimes}
    selected_runtime: DelegationRuntimeV2 | None = None
    for task in selected_workflow.tasks:
        destination = profiles.get(task.destination_profile_id)
        runtime = runtimes.get(task.adapter_id)
        if destination is None or runtime is None:
            raise DelegationV2InvalidInputError()
        adapter = runtime.adapter
        if (
            adapter.provider != destination.provider
            or adapter.vendor_family != mappings.get(destination.provider)
            or destination.transport not in adapter.supported_transports
            or DelegationAllowedPairV2(task.model, task.effort)
            not in destination.allowed_model_effort_pairs
            or not set(task.capabilities).issubset(destination.capabilities)
            or not set(task.capabilities).issubset(adapter.capabilities)
        ):
            raise DelegationV2InvalidInputError()
        if selected_runtime is not None and selected_runtime != runtime:
            raise DelegationV2InvalidInputError()
        selected_runtime = runtime
    if selected_runtime is None or selected_runtime.path != validated_runtime_path:
        raise DelegationV2InvalidInputError()
    return DelegationV2Binding(
        selected_workflow.workflow_id,
        selected_runtime.runtime_id,
        selected_runtime.path,
    )
