"""Immutable values for offline delegation-policy compilation."""

from dataclasses import dataclass
from typing import Literal

VendorFamily = Literal["claude", "codex"]
DelegationTier = Literal["low", "standard", "high"]
RoleName = Literal["orchestrator", "worker", "reviewer"]
Category = Literal["implementation", "tests", "documentation"]


@dataclass(frozen=True)
class PlatformContract:
    os: str
    architecture: str


@dataclass(frozen=True)
class DelegationProfile:
    profile_id: str
    role: RoleName
    vendor_family: VendorFamily
    transport: str
    model: str
    effort: str
    allowed_categories: tuple[Category, ...]
    global_role_process_limit: int


@dataclass(frozen=True)
class RetentionContract:
    worker_context: str
    artifacts: str
    on_reviewer_rejection: str
    after_integration: str


@dataclass(frozen=True)
class DelegatedAssignment:
    category: Category
    execution: str
    review: str
    retention: RetentionContract
    integration: str


@dataclass(frozen=True)
class IntegrationContract:
    inputs: tuple[str, ...]
    allowed_operations: tuple[str, ...]
    verification_commands: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class DirectChildCleanup:
    grace_seconds: int
    terminate_grace_seconds: int


@dataclass(frozen=True)
class BoundaryPair:
    source: str
    destination: str


@dataclass(frozen=True)
class BoundaryAuthorizations:
    provider_pairs: tuple[BoundaryPair, ...]
    recipient_pairs: tuple[BoundaryPair, ...]
    billing_pairs: tuple[BoundaryPair, ...]
    mixed_transport_pairs: tuple[BoundaryPair, ...]


@dataclass(frozen=True)
class DelegationWorkflow:
    workflow_id: str
    eligible_source_vendors: tuple[VendorFamily, ...]
    eligible_tiers: tuple[DelegationTier, ...]
    adapter_id: str
    profile_ids: tuple[tuple[RoleName, str], ...]
    assignments: tuple[DelegatedAssignment, ...]
    integration: IntegrationContract
    runtime_deadline_seconds: int
    direct_child_cleanup: DirectChildCleanup
    boundary_authorizations: BoundaryAuthorizations


@dataclass(frozen=True)
class DelegationPolicy:
    profiles: tuple[DelegationProfile, ...]
    workflows: tuple[DelegationWorkflow, ...]


@dataclass(frozen=True)
class ActionPrimitives:
    allow: str
    deny: str


@dataclass(frozen=True)
class ProcessIsolationPrimitives:
    create: str
    attribute: str


@dataclass(frozen=True)
class EnforcementPrimitives:
    workspace_read: ActionPrimitives
    workspace_write: ActionPrimitives
    command_execution: ActionPrimitives
    process_isolation: ProcessIsolationPrimitives


@dataclass(frozen=True)
class DelegationAdapter:
    adapter_id: str
    vendor_family: VendorFamily
    transports: tuple[str, ...]
    global_role_process_limit: int
    capabilities: tuple[str, ...]
    enforcement_primitives: EnforcementPrimitives


@dataclass(frozen=True)
class DelegationRuntimeManifest:
    runtime_protocol_versions: tuple[int, ...]
    runtime_build_id: str
    supported_platforms: tuple[PlatformContract, ...]
    adapters: tuple[DelegationAdapter, ...]
