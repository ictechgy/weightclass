"""Strict, bounded schemas for offline delegation policy review."""

import platform
import re
import unicodedata
from collections.abc import Hashable
from pathlib import Path
from typing import Any, Final, TypeVar

from .delegation_types import (
    ActionPrimitives,
    BoundaryAuthorizations,
    BoundaryPair,
    Category,
    DelegatedAssignment,
    DelegationAdapter,
    DelegationPolicy,
    DelegationProfile,
    DelegationRuntimeManifest,
    DelegationTier,
    DelegationWorkflow,
    DirectChildCleanup,
    EnforcementPrimitives,
    IntegrationContract,
    PlatformContract,
    ProcessIsolationPrimitives,
    RetentionContract,
    RoleName,
    VendorFamily,
)
from .json_input import JsonInputError, load_json_object

POLICY_SCHEMA_VERSION: Final = 1
MANIFEST_SCHEMA_VERSION: Final = 1
RUNTIME_PROTOCOL_VERSION: Final = 1
MAX_POLICY_BYTES: Final = 262_144
MAX_MANIFEST_BYTES: Final = 262_144
MAX_IDENTIFIER_BYTES: Final = 64
MAX_OPAQUE_LABEL_BYTES: Final = 240
MAX_ASCII_LABEL_BYTES: Final = 128
MAX_RUNTIME_PATH_BYTES: Final = 4_096
MAX_WORKFLOWS: Final = 32
MAX_PROFILES: Final = 48
MAX_ADAPTERS: Final = 4
MAX_SUPPORTED_PLATFORMS: Final = 8
MAX_PROTOCOL_VERSIONS: Final = 8
MAX_CAPABILITIES: Final = 32
MAX_BOUNDARY_PAIRS: Final = 64
MAX_VERIFICATION_COMMANDS: Final = 16
MAX_ARGV_ENTRIES: Final = 32
MAX_ARGV_TOKEN_BYTES: Final = 4_096
MAX_RUNTIME_DEADLINE_SECONDS: Final = 86_400
MAX_ROLE_PROCESS_LIMIT: Final = 32
MAX_CLEANUP_GRACE_SECONDS: Final = 60
MAX_INTEGRATION_INPUTS: Final = 1
MAX_INTEGRATION_OPERATIONS: Final = 2
MAX_ARTIFACT_ID_BYTES: Final = 64
MAX_CONTEXT_ID_BYTES: Final = 64
MAX_QUALIFICATION_RECORDS: Final = 256

ROLES: Final[tuple[RoleName, ...]] = ("orchestrator", "worker", "reviewer")
CATEGORIES: Final[tuple[Category, ...]] = ("implementation", "tests", "documentation")
TIERS: Final[tuple[DelegationTier, ...]] = ("low", "standard", "high")
VENDORS: Final[tuple[VendorFamily, ...]] = ("claude", "codex")
SUPPORTED_OS: Final[tuple[str, ...]] = ("darwin", "linux")
SUPPORTED_ARCHITECTURES: Final[tuple[str, ...]] = ("aarch64", "x86_64")
REQUIRED_CAPABILITIES: Final = (
    "artifact_integrity",
    "descendant_cleanup",
    "distinct_enforcement_contexts",
    "mechanical_integration",
    "observable_action_attribution",
    "runtime_deadline",
)

_IDENTIFIER_PATTERN: Final = re.compile(r"[a-z][a-z0-9._-]*\Z")
_HashableT = TypeVar("_HashableT", bound=Hashable)


class DelegationInvalidInputError(ValueError):
    """Raised for unsafe delegation input without including source values."""


class DelegationUnsupportedError(ValueError):
    """Raised for a safe but unsupported protocol-1 combination."""


def _require_object(value: object, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise DelegationInvalidInputError()
    return value


def _require_array(
    value: object,
    *,
    minimum: int,
    maximum: int,
) -> list[Any]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise DelegationInvalidInputError()
    return value


def _require_integer(value: object, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise DelegationInvalidInputError()
    return value


def _require_identifier(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value.encode("utf-8", errors="surrogatepass")) > MAX_IDENTIFIER_BYTES
        or _IDENTIFIER_PATTERN.fullmatch(value) is None
    ):
        raise DelegationInvalidInputError()
    return value


def _require_reviewable_label(value: object, *, maximum_bytes: int) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise DelegationInvalidInputError()
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise DelegationInvalidInputError() from None
    if len(encoded) > maximum_bytes or any(
        unicodedata.category(character).startswith("C")
        or (character.isspace() and character != " ")
        for character in value
    ):
        raise DelegationInvalidInputError()
    return value


def _require_ascii_label(value: object) -> str:
    label = _require_reviewable_label(value, maximum_bytes=MAX_ASCII_LABEL_BYTES)
    if not label.isascii():
        raise DelegationInvalidInputError()
    return label


def _require_enum(value: object, allowed: tuple[_HashableT, ...]) -> _HashableT:
    if not isinstance(value, str) or value not in allowed:
        raise DelegationInvalidInputError()
    return value


def _require_unique(values: tuple[_HashableT, ...]) -> tuple[_HashableT, ...]:
    if len(set(values)) != len(values):
        raise DelegationInvalidInputError()
    return values


def _parse_profile(value: object) -> DelegationProfile:
    profile = _require_object(
        value,
        {
            "id",
            "role",
            "vendor_family",
            "transport",
            "model",
            "effort",
            "allowed_categories",
            "global_role_process_limit",
        },
    )
    categories = tuple(
        _require_enum(category, CATEGORIES)
        for category in _require_array(
            profile["allowed_categories"], minimum=1, maximum=len(CATEGORIES)
        )
    )
    return DelegationProfile(
        profile_id=_require_identifier(profile["id"]),
        role=_require_enum(profile["role"], ROLES),
        vendor_family=_require_enum(profile["vendor_family"], VENDORS),
        transport=_require_enum(profile["transport"], ("native",)),
        model=_require_reviewable_label(profile["model"], maximum_bytes=MAX_OPAQUE_LABEL_BYTES),
        effort=_require_reviewable_label(profile["effort"], maximum_bytes=MAX_OPAQUE_LABEL_BYTES),
        allowed_categories=_require_unique(categories),
        global_role_process_limit=_require_integer(
            profile["global_role_process_limit"], minimum=1, maximum=MAX_ROLE_PROCESS_LIMIT
        ),
    )


def _parse_retention(value: object) -> RetentionContract:
    retention = _require_object(
        value,
        {
            "worker_context",
            "artifacts",
            "on_reviewer_rejection",
            "after_integration",
        },
    )
    expected = {
        "worker_context": "release_after_workers_completed",
        "artifacts": "retain_through_integration",
        "on_reviewer_rejection": "runtime_destroy",
        "after_integration": "runtime_destroy",
    }
    if retention != expected:
        raise DelegationInvalidInputError()
    return RetentionContract(
        worker_context=retention["worker_context"],
        artifacts=retention["artifacts"],
        on_reviewer_rejection=retention["on_reviewer_rejection"],
        after_integration=retention["after_integration"],
    )


def _parse_assignment(value: object) -> DelegatedAssignment:
    assignment = _require_object(
        value,
        {"category", "execution", "review", "retention", "integration"},
    )
    return DelegatedAssignment(
        category=_require_enum(assignment["category"], CATEGORIES),
        execution=_require_enum(assignment["execution"], ("must_delegate",)),
        review=_require_enum(assignment["review"], ("required",)),
        retention=_parse_retention(assignment["retention"]),
        integration=_require_enum(assignment["integration"], ("mechanical_runtime",)),
    )


def _parse_command(value: object) -> tuple[str, ...]:
    return tuple(
        _require_reviewable_label(token, maximum_bytes=MAX_ARGV_TOKEN_BYTES)
        for token in _require_array(value, minimum=1, maximum=MAX_ARGV_ENTRIES)
    )


def _parse_integration(value: object) -> IntegrationContract:
    integration = _require_object(value, {"inputs", "allowed_operations", "verification_commands"})
    inputs = tuple(
        _require_identifier(item)
        for item in _require_array(
            integration["inputs"], minimum=MAX_INTEGRATION_INPUTS, maximum=MAX_INTEGRATION_INPUTS
        )
    )
    operations = tuple(
        _require_identifier(item)
        for item in _require_array(
            integration["allowed_operations"],
            minimum=MAX_INTEGRATION_OPERATIONS,
            maximum=MAX_INTEGRATION_OPERATIONS,
        )
    )
    if inputs != ("reviewer_approved_worker_artifacts",) or set(operations) != {
        "apply_approved_artifact",
        "run_approved_verification_command",
    }:
        raise DelegationInvalidInputError()
    commands = tuple(
        _parse_command(command)
        for command in _require_array(
            integration["verification_commands"],
            minimum=1,
            maximum=MAX_VERIFICATION_COMMANDS,
        )
    )
    return IntegrationContract(
        inputs=inputs,
        allowed_operations=tuple(sorted(operations)),
        verification_commands=commands,
    )


def _parse_cleanup(value: object) -> DirectChildCleanup:
    cleanup = _require_object(value, {"grace_seconds", "terminate_grace_seconds"})
    return DirectChildCleanup(
        grace_seconds=_require_integer(
            cleanup["grace_seconds"], minimum=1, maximum=MAX_CLEANUP_GRACE_SECONDS
        ),
        terminate_grace_seconds=_require_integer(
            cleanup["terminate_grace_seconds"],
            minimum=1,
            maximum=MAX_CLEANUP_GRACE_SECONDS,
        ),
    )


def _parse_pair(value: object) -> BoundaryPair:
    pair = _require_object(value, {"from", "to"})
    return BoundaryPair(
        source=_require_reviewable_label(pair["from"], maximum_bytes=MAX_OPAQUE_LABEL_BYTES),
        destination=_require_reviewable_label(pair["to"], maximum_bytes=MAX_OPAQUE_LABEL_BYTES),
    )


def _parse_pair_list(value: object) -> tuple[BoundaryPair, ...]:
    pairs = tuple(
        _parse_pair(item) for item in _require_array(value, minimum=0, maximum=MAX_BOUNDARY_PAIRS)
    )
    if len(set(pairs)) != len(pairs):
        raise DelegationInvalidInputError()
    return pairs


def _parse_boundaries(value: object) -> BoundaryAuthorizations:
    boundaries = _require_object(
        value,
        {"provider_pairs", "recipient_pairs", "billing_pairs", "mixed_transport_pairs"},
    )
    return BoundaryAuthorizations(
        provider_pairs=_parse_pair_list(boundaries["provider_pairs"]),
        recipient_pairs=_parse_pair_list(boundaries["recipient_pairs"]),
        billing_pairs=_parse_pair_list(boundaries["billing_pairs"]),
        mixed_transport_pairs=_parse_pair_list(boundaries["mixed_transport_pairs"]),
    )


def _parse_workflow(value: object) -> DelegationWorkflow:
    workflow = _require_object(
        value,
        {
            "id",
            "eligible_source_vendors",
            "eligible_tiers",
            "adapter_id",
            "profiles",
            "assignments",
            "integration",
            "runtime_deadline_seconds",
            "direct_child_cleanup",
            "boundary_authorizations",
        },
    )
    source_vendors = _require_unique(
        tuple(
            _require_enum(item, VENDORS)
            for item in _require_array(
                workflow["eligible_source_vendors"], minimum=1, maximum=len(VENDORS)
            )
        )
    )
    tiers = _require_unique(
        tuple(
            _require_enum(item, TIERS)
            for item in _require_array(workflow["eligible_tiers"], minimum=1, maximum=len(TIERS))
        )
    )
    profile_ids = _require_object(workflow["profiles"], set(ROLES))
    assignments = tuple(
        _parse_assignment(item)
        for item in _require_array(
            workflow["assignments"], minimum=len(CATEGORIES), maximum=len(CATEGORIES)
        )
    )
    if {assignment.category for assignment in assignments} != set(CATEGORIES):
        raise DelegationInvalidInputError()
    return DelegationWorkflow(
        workflow_id=_require_identifier(workflow["id"]),
        eligible_source_vendors=source_vendors,
        eligible_tiers=tiers,
        adapter_id=_require_identifier(workflow["adapter_id"]),
        profile_ids=tuple((role, _require_identifier(profile_ids[role])) for role in ROLES),
        assignments=assignments,
        integration=_parse_integration(workflow["integration"]),
        runtime_deadline_seconds=_require_integer(
            workflow["runtime_deadline_seconds"],
            minimum=1,
            maximum=MAX_RUNTIME_DEADLINE_SECONDS,
        ),
        direct_child_cleanup=_parse_cleanup(workflow["direct_child_cleanup"]),
        boundary_authorizations=_parse_boundaries(workflow["boundary_authorizations"]),
    )


def load_delegation_policy(path: Path) -> DelegationPolicy:
    """Load a bounded policy without retaining any task data."""
    try:
        policy = load_json_object(
            path,
            max_bytes=MAX_POLICY_BYTES,
            require_exclusive_write_owner=True,
        )
    except (JsonInputError, RecursionError):
        raise DelegationInvalidInputError() from None
    _require_object(policy, {"schema_version", "profiles", "workflows"})
    if _require_integer(policy["schema_version"], minimum=1, maximum=1) != (POLICY_SCHEMA_VERSION):
        raise DelegationInvalidInputError()
    profiles = tuple(
        _parse_profile(item)
        for item in _require_array(policy["profiles"], minimum=3, maximum=MAX_PROFILES)
    )
    workflows = tuple(
        _parse_workflow(item)
        for item in _require_array(policy["workflows"], minimum=1, maximum=MAX_WORKFLOWS)
    )
    if len({profile.profile_id for profile in profiles}) != len(profiles) or len(
        {workflow.workflow_id for workflow in workflows}
    ) != len(workflows):
        raise DelegationInvalidInputError()
    return DelegationPolicy(profiles=profiles, workflows=workflows)


def _parse_platform(value: object) -> PlatformContract:
    platform_value = _require_object(value, {"os", "architecture"})
    return PlatformContract(
        os=_require_enum(platform_value["os"], SUPPORTED_OS),
        architecture=_require_enum(platform_value["architecture"], SUPPORTED_ARCHITECTURES),
    )


def _parse_action_primitives(value: object) -> ActionPrimitives:
    primitives = _require_object(value, {"allow", "deny"})
    parsed = ActionPrimitives(
        allow=_require_ascii_label(primitives["allow"]),
        deny=_require_ascii_label(primitives["deny"]),
    )
    if parsed.allow == parsed.deny:
        raise DelegationInvalidInputError()
    return parsed


def _parse_enforcement_primitives(value: object) -> EnforcementPrimitives:
    primitives = _require_object(
        value,
        {"workspace_read", "workspace_write", "command_execution", "process_isolation"},
    )
    process = _require_object(primitives["process_isolation"], {"create", "attribute"})
    parsed = EnforcementPrimitives(
        workspace_read=_parse_action_primitives(primitives["workspace_read"]),
        workspace_write=_parse_action_primitives(primitives["workspace_write"]),
        command_execution=_parse_action_primitives(primitives["command_execution"]),
        process_isolation=ProcessIsolationPrimitives(
            create=_require_ascii_label(process["create"]),
            attribute=_require_ascii_label(process["attribute"]),
        ),
    )
    labels = (
        parsed.workspace_read.allow,
        parsed.workspace_read.deny,
        parsed.workspace_write.allow,
        parsed.workspace_write.deny,
        parsed.command_execution.allow,
        parsed.command_execution.deny,
        parsed.process_isolation.create,
        parsed.process_isolation.attribute,
    )
    if len(set(labels)) != len(labels):
        raise DelegationInvalidInputError()
    return parsed


def _parse_adapter(value: object) -> DelegationAdapter:
    adapter = _require_object(
        value,
        {
            "id",
            "vendor_family",
            "transports",
            "global_role_process_limit",
            "capabilities",
            "enforcement_primitives",
        },
    )
    transports = _require_unique(
        tuple(
            _require_enum(item, ("native",))
            for item in _require_array(adapter["transports"], minimum=1, maximum=1)
        )
    )
    capabilities = _require_unique(
        tuple(
            _require_identifier(item)
            for item in _require_array(adapter["capabilities"], minimum=1, maximum=MAX_CAPABILITIES)
        )
    )
    if set(capabilities) != set(REQUIRED_CAPABILITIES):
        raise DelegationInvalidInputError()
    return DelegationAdapter(
        adapter_id=_require_identifier(adapter["id"]),
        vendor_family=_require_enum(adapter["vendor_family"], VENDORS),
        transports=transports,
        global_role_process_limit=_require_integer(
            adapter["global_role_process_limit"],
            minimum=1,
            maximum=MAX_ROLE_PROCESS_LIMIT,
        ),
        capabilities=tuple(sorted(capabilities)),
        enforcement_primitives=_parse_enforcement_primitives(adapter["enforcement_primitives"]),
    )


def load_delegation_manifest(path: Path) -> DelegationRuntimeManifest:
    """Load an offline capability declaration; do not inspect a runtime path."""
    try:
        manifest = load_json_object(
            path,
            max_bytes=MAX_MANIFEST_BYTES,
            require_exclusive_write_owner=True,
        )
    except (JsonInputError, RecursionError):
        raise DelegationInvalidInputError() from None
    _require_object(
        manifest,
        {
            "manifest_schema_version",
            "runtime_protocol_versions",
            "runtime_build_id",
            "supported_platforms",
            "adapters",
        },
    )
    if _require_integer(manifest["manifest_schema_version"], minimum=1, maximum=1) != (
        MANIFEST_SCHEMA_VERSION
    ):
        raise DelegationInvalidInputError()
    protocol_versions = _require_unique(
        tuple(
            _require_integer(item, minimum=1, maximum=2_147_483_647)
            for item in _require_array(
                manifest["runtime_protocol_versions"],
                minimum=1,
                maximum=MAX_PROTOCOL_VERSIONS,
            )
        )
    )
    platforms = tuple(
        _parse_platform(item)
        for item in _require_array(
            manifest["supported_platforms"], minimum=1, maximum=MAX_SUPPORTED_PLATFORMS
        )
    )
    adapters = tuple(
        _parse_adapter(item)
        for item in _require_array(manifest["adapters"], minimum=1, maximum=MAX_ADAPTERS)
    )
    if len(set(platforms)) != len(platforms) or len(
        {adapter.adapter_id for adapter in adapters}
    ) != len(adapters):
        raise DelegationInvalidInputError()
    return DelegationRuntimeManifest(
        runtime_protocol_versions=protocol_versions,
        runtime_build_id=_require_ascii_label(manifest["runtime_build_id"]),
        supported_platforms=platforms,
        adapters=adapters,
    )


def validate_runtime_path_lexically(value: str) -> str:
    """Validate a POSIX runtime path without resolving or touching it."""
    if not isinstance(value, str):
        raise DelegationInvalidInputError()
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise DelegationInvalidInputError() from None
    if not value.startswith("/") or not 1 <= len(encoded) <= MAX_RUNTIME_PATH_BYTES:
        raise DelegationInvalidInputError()
    if any(
        unicodedata.category(character).startswith("C")
        or (character.isspace() and character != " ")
        for character in value
    ):
        raise DelegationInvalidInputError()
    if value == "/":
        return value
    components = value[1:].split("/")
    if any(
        not component or component in {".", ".."} or component != component.strip(" ")
        for component in components
    ):
        raise DelegationInvalidInputError()
    return value


def current_platform_contract() -> PlatformContract:
    """Normalize the current host without inspecting the runtime executable."""
    os_name = platform.system().lower()
    architecture = {
        "amd64": "x86_64",
        "arm64": "aarch64",
        "x86_64": "x86_64",
        "aarch64": "aarch64",
    }.get(platform.machine().lower(), "")
    if os_name not in SUPPORTED_OS or architecture not in SUPPORTED_ARCHITECTURES:
        raise DelegationUnsupportedError()
    return PlatformContract(os=os_name, architecture=architecture)
