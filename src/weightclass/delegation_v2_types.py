"""Immutable values produced by delegation protocol-2 structural parsing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

Provider: TypeAlias = Literal["openai", "anthropic"]
VendorFamilyV2: TypeAlias = Literal["codex", "claude"]
TierV2: TypeAlias = Literal["low", "standard", "high"]
TransportV2: TypeAlias = Literal["native"]
TaskModeV2: TypeAlias = Literal["independent", "synthesizer"]
FilesystemPermissionV2: TypeAlias = Literal["none", "read", "write"]
CommandPermissionV2: TypeAlias = Literal["deny", "allow"]
ToolModeV2: TypeAlias = Literal["allow", "deny"]
WorktreeModeV2: TypeAlias = Literal["none", "read_only", "mutable"]
SettlementModeV2: TypeAlias = Literal["requested_external_settlement"]
CleanupModeV2: TypeAlias = Literal["direct_child_only"]
OutputTypeV2: TypeAlias = Literal["text", "json", "boolean", "artifact_ref"]
TerminalModeV2: TypeAlias = Literal["synthesized", "raw_independent"]
GateRequiredTypeV2: TypeAlias = Literal["weightclass.gate/v1"]
GateOperatorV2: TypeAlias = Literal["equals"]
GateValueV2: TypeAlias = Literal["approved"]


@dataclass(frozen=True, slots=True)
class DelegationAllowedPairV2:
    model: str
    effort: str


@dataclass(frozen=True, slots=True)
class DelegationProfileV2:
    profile_id: str
    provider: Provider
    intended_recipient: str
    billing_boundary: str
    transport: TransportV2
    account_profile: str
    capabilities: tuple[str, ...]
    allowed_model_effort_pairs: tuple[DelegationAllowedPairV2, ...]


@dataclass(frozen=True, slots=True)
class EligibilityV2:
    source_vendor_family: VendorFamilyV2
    source_profile_id: str
    tier: TierV2


@dataclass(frozen=True, slots=True)
class DelegationPermissionV2:
    filesystem: FilesystemPermissionV2
    commands: CommandPermissionV2


@dataclass(frozen=True, slots=True)
class DelegationToolV2:
    tool_id: str
    mode: ToolModeV2


@dataclass(frozen=True, slots=True)
class DelegationRequestV2:
    mode: TaskModeV2
    permissions: DelegationPermissionV2
    tools: tuple[DelegationToolV2, ...]


@dataclass(frozen=True, slots=True)
class DelegationWorktreeV2:
    mode: WorktreeModeV2
    scope: str | None


@dataclass(frozen=True, slots=True)
class DelegationSettlementV2:
    mode: SettlementModeV2


@dataclass(frozen=True, slots=True)
class DelegationCleanupV2:
    mode: CleanupModeV2
    grace_seconds: int


@dataclass(frozen=True, slots=True)
class DelegationArtifactV2:
    artifact_id: str
    media_type: str


@dataclass(frozen=True, slots=True)
class DelegationOutputV2:
    output_id: str
    output_type: OutputTypeV2
    artifacts: tuple[DelegationArtifactV2, ...]


@dataclass(frozen=True, slots=True)
class DelegationInputV2:
    input_id: str
    input_type: OutputTypeV2


@dataclass(frozen=True, slots=True)
class DelegationProjectionV2:
    projection_id: str
    input_id: str
    producer_task_id: str
    producer_output_id: str


@dataclass(frozen=True, slots=True)
class DelegationTaskBindingV2:
    task_id: str
    requested_task_id: str
    requested_dispatch_id: str
    owner_role: str
    destination_profile_id: str
    adapter_id: str
    model: str
    effort: str
    instruction: str
    request: DelegationRequestV2
    worktree: DelegationWorktreeV2
    settlement: DelegationSettlementV2
    cleanup: DelegationCleanupV2
    outputs: tuple[DelegationOutputV2, ...]
    inputs: tuple[DelegationInputV2, ...]
    projections: tuple[DelegationProjectionV2, ...]
    mutable_scopes: tuple[str, ...]
    capabilities: tuple[str, ...]
    turns: int
    deadline_seconds: int
    attempts: int


@dataclass(frozen=True, slots=True)
class DelegationDependencyV2:
    dependency_id: str
    from_task_id: str
    to_task_id: str


@dataclass(frozen=True, slots=True)
class DelegationGateOutputV2:
    producer_task_id: str
    producer_output_id: str
    required_type: GateRequiredTypeV2


@dataclass(frozen=True, slots=True)
class DelegationGatePredicateV2:
    operator: GateOperatorV2
    value: GateValueV2


@dataclass(frozen=True, slots=True)
class DelegationGateV2:
    gate_id: str
    from_task_id: str
    to_task_id: str
    output: DelegationGateOutputV2
    predicate: DelegationGatePredicateV2


@dataclass(frozen=True, slots=True)
class DelegationGrantV2:
    grant_id: str
    from_value: str
    to_value: str


@dataclass(frozen=True, slots=True)
class DelegationDimensionGrantsV2:
    provider: tuple[DelegationGrantV2, ...]
    intended_recipient: tuple[DelegationGrantV2, ...]
    billing_boundary: tuple[DelegationGrantV2, ...]
    transport: tuple[DelegationGrantV2, ...]
    profile: tuple[DelegationGrantV2, ...]


@dataclass(frozen=True, slots=True)
class DelegationWorkflowV2:
    workflow_id: str
    requested_run_id: str
    eligibility: EligibilityV2
    terminal_mode: TerminalModeV2
    terminal_task_id: str | None
    concurrency: int
    tasks: tuple[DelegationTaskBindingV2, ...]
    dependencies: tuple[DelegationDependencyV2, ...]
    gates: tuple[DelegationGateV2, ...]
    grants: DelegationDimensionGrantsV2

    @property
    def task_bindings(self) -> tuple[DelegationTaskBindingV2, ...]:
        """Compatibility name retained for the initial G05 binding API."""
        return self.tasks


@dataclass(frozen=True, slots=True)
class DelegationPolicyV2:
    schema_version: int
    manifest_schema_version: int
    compiler_contract_version: int
    runtime_protocol_version: int
    frame_version: Literal["WCD2"]
    profiles: tuple[DelegationProfileV2, ...]
    workflows: tuple[DelegationWorkflowV2, ...]


@dataclass(frozen=True, slots=True)
class DelegationProviderFamilyMappingV2:
    provider: Provider
    vendor_family: VendorFamilyV2


@dataclass(frozen=True, slots=True)
class DelegationAdapterV2:
    adapter_id: str
    vendor_family: VendorFamilyV2
    provider: Provider
    runtime_build_id: str
    supported_transports: tuple[TransportV2, ...]
    capabilities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DelegationRuntimeV2:
    runtime_id: str
    path: str
    adapter: DelegationAdapterV2


@dataclass(frozen=True, slots=True)
class DelegationManifestV2:
    schema_version: int
    provider_family_mappings: tuple[DelegationProviderFamilyMappingV2, ...]
    runtimes: tuple[DelegationRuntimeV2, ...]


@dataclass(frozen=True, slots=True)
class DelegationV2Binding:
    workflow_id: str
    runtime_id: str
    runtime_path: str
