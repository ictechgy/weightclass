"""Package-owned qualification records and exact-artifact verification.

This module has no runtime-task input field and never reads task stdin.
Candidate construction validates a task-free conformance result and prints no
claim by itself; only a reviewed record added to the package registry can
satisfy production route selection.
"""

import hashlib
import os
import re
import stat
from dataclasses import asdict, dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Final, Literal, cast

from .delegation_compile import bind_delegation_fingerprint, canonical_json_bytes
from .delegation_schema import (
    CATEGORIES,
    MAX_QUALIFICATION_RECORDS,
    ROLES,
    RUNTIME_PROTOCOL_VERSION,
    SUPPORTED_ARCHITECTURES,
    SUPPORTED_OS,
    VENDORS,
    DelegationInvalidInputError,
    DelegationUnsupportedError,
)
from .delegation_types import Category, PlatformContract, RoleName, VendorFamily
from .json_input import JsonInputError, load_json_object

REGISTRY_SCHEMA_VERSION: Final = 1
EVIDENCE_SCHEMA_VERSION: Final = 1
RECORD_SCHEMA_VERSION: Final = 1
CURRENT_SUITE_REVISION: Final = "delegation-conformance-v1"
MAX_REGISTRY_BYTES: Final = 4_194_304
MAX_EVIDENCE_BYTES: Final = 262_144
MAX_ARTIFACT_SIZE_BYTES: Final = 1_073_741_824
MAX_LABEL_BYTES: Final = 128
READ_CHUNK_BYTES: Final = 65_536
PACKAGE_REGISTRY_NAME: Final = "delegation_qualifications.json"

ActionName = Literal["workspace_read", "workspace_write", "command_execution"]
ActionMode = Literal["allow", "deny"]
ACTIONS: Final[tuple[ActionName, ...]] = (
    "workspace_read",
    "workspace_write",
    "command_execution",
)
MODES: Final[tuple[ActionMode, ...]] = ("allow", "deny")
REQUIRED_SCENARIOS: Final = (
    "action_attribution",
    "artifact_integrity_and_substitution",
    "descendant_cleanup",
    "descendant_leakage",
    "distinct_enforcement_contexts",
    "integration_restriction",
    "integration_verification_commands",
    "output_channel_separation",
    "process_creation_attribution",
    "reviewer_rejection",
    "runtime_deadline",
    "stage_order",
    "worker_concurrency_bound",
)

_IDENTIFIER_PATTERN: Final = re.compile(r"[a-z][a-z0-9._-]*\Z")
_SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}\Z")


class QualificationInvalidInputError(DelegationInvalidInputError):
    """Raised for malformed evidence or package registry data."""


class QualificationUnsupportedError(DelegationUnsupportedError):
    """Raised when no single package qualification matches a route."""


class QualifiedRuntimeUnavailableError(OSError):
    """Raised without path details when exact artifact verification fails."""


@dataclass(frozen=True)
class QualificationObservation:
    role: RoleName
    category: Category
    action: ActionName
    mode: ActionMode
    passed: bool


@dataclass(frozen=True)
class QualificationScenario:
    id: str
    passed: bool


@dataclass(frozen=True)
class QualificationRecord:
    artifact_sha256: str
    artifact_size_bytes: int
    runtime_build_id: str
    platform: PlatformContract
    protocol_version: int
    suite_revision: str
    adapter_id: str
    vendor_family: VendorFamily
    conformance_evidence_sha256: str
    result_matrix: tuple[QualificationObservation, ...]
    scenario_results: tuple[QualificationScenario, ...]

    @property
    def selector(self) -> tuple[str, PlatformContract, int, str, VendorFamily]:
        return (
            self.runtime_build_id,
            self.platform,
            self.protocol_version,
            self.adapter_id,
            self.vendor_family,
        )


@dataclass(frozen=True)
class QualificationRegistry:
    suite_revision: str
    records: tuple[QualificationRecord, ...]


class _ArtifactReadError(OSError):
    pass


def _require_object(value: object, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise QualificationInvalidInputError()
    return value


def _require_integer(value: object, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise QualificationInvalidInputError()
    return value


def _require_identifier(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value.encode("utf-8", errors="surrogatepass")) > MAX_LABEL_BYTES
        or _IDENTIFIER_PATTERN.fullmatch(value) is None
    ):
        raise QualificationInvalidInputError()
    return value


def _require_build_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or not value.isascii()
        or value != value.strip()
        or len(value.encode("ascii")) > MAX_LABEL_BYTES
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise QualificationInvalidInputError()
    return value


def _require_sha256(value: object) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise QualificationInvalidInputError()
    return value


def _require_platform(value: object) -> PlatformContract:
    platform = _require_object(value, {"os", "architecture"})
    operating_system = platform["os"]
    architecture = platform["architecture"]
    if operating_system not in SUPPORTED_OS or architecture not in SUPPORTED_ARCHITECTURES:
        raise QualificationInvalidInputError()
    return PlatformContract(
        os=cast(str, operating_system),
        architecture=cast(str, architecture),
    )


def _parse_result_matrix(value: object) -> tuple[QualificationObservation, ...]:
    if not isinstance(value, list) or len(value) != (
        len(ROLES) * len(CATEGORIES) * len(ACTIONS) * len(MODES)
    ):
        raise QualificationInvalidInputError()
    observations: list[QualificationObservation] = []
    for item in value:
        observation = _require_object(
            item,
            {"role", "category", "action", "mode", "passed"},
        )
        if (
            observation["role"] not in ROLES
            or observation["category"] not in CATEGORIES
            or observation["action"] not in ACTIONS
            or observation["mode"] not in MODES
            or observation["passed"] is not True
        ):
            raise QualificationInvalidInputError()
        observations.append(
            QualificationObservation(
                role=cast(RoleName, observation["role"]),
                category=cast(Category, observation["category"]),
                action=cast(ActionName, observation["action"]),
                mode=cast(ActionMode, observation["mode"]),
                passed=True,
            )
        )
    expected = {
        (role, category, action, mode)
        for role in ROLES
        for category in CATEGORIES
        for action in ACTIONS
        for mode in MODES
    }
    actual = {(item.role, item.category, item.action, item.mode) for item in observations}
    if actual != expected:
        raise QualificationInvalidInputError()
    order = {
        value: index
        for index, value in enumerate(
            (role, category, action, mode)
            for role in ROLES
            for category in CATEGORIES
            for action in ACTIONS
            for mode in MODES
        )
    }
    return tuple(
        sorted(
            observations,
            key=lambda item: order[(item.role, item.category, item.action, item.mode)],
        )
    )


def _parse_scenario_results(value: object) -> tuple[QualificationScenario, ...]:
    if not isinstance(value, list) or len(value) != len(REQUIRED_SCENARIOS):
        raise QualificationInvalidInputError()
    scenarios: list[QualificationScenario] = []
    for item in value:
        scenario = _require_object(item, {"id", "passed"})
        if scenario["id"] not in REQUIRED_SCENARIOS or scenario["passed"] is not True:
            raise QualificationInvalidInputError()
        scenarios.append(QualificationScenario(id=cast(str, scenario["id"]), passed=True))
    if {scenario.id for scenario in scenarios} != set(REQUIRED_SCENARIOS):
        raise QualificationInvalidInputError()
    return tuple(sorted(scenarios, key=lambda item: REQUIRED_SCENARIOS.index(item.id)))


def _normalized_evidence(
    *,
    suite_revision: str,
    runtime_build_id: str,
    platform: PlatformContract,
    protocol_version: int,
    adapter_id: str,
    vendor_family: VendorFamily,
    result_matrix: tuple[QualificationObservation, ...],
    scenario_results: tuple[QualificationScenario, ...],
) -> dict[str, object]:
    return {
        "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
        "suite_revision": suite_revision,
        "runtime_build_id": runtime_build_id,
        "platform": asdict(platform),
        "protocol_version": protocol_version,
        "adapter_id": adapter_id,
        "vendor_family": vendor_family,
        "result_matrix": [asdict(item) for item in result_matrix],
        "scenario_results": [asdict(item) for item in scenario_results],
    }


def _parse_evidence(value: object) -> dict[str, object]:
    evidence = _require_object(
        value,
        {
            "evidence_schema_version",
            "suite_revision",
            "runtime_build_id",
            "platform",
            "protocol_version",
            "adapter_id",
            "vendor_family",
            "result_matrix",
            "scenario_results",
        },
    )
    if (
        _require_integer(evidence["evidence_schema_version"], minimum=1, maximum=1)
        != EVIDENCE_SCHEMA_VERSION
    ):
        raise QualificationInvalidInputError()
    suite_revision = _require_identifier(evidence["suite_revision"])
    if suite_revision != CURRENT_SUITE_REVISION:
        raise QualificationInvalidInputError()
    protocol_version = _require_integer(
        evidence["protocol_version"], minimum=1, maximum=2_147_483_647
    )
    if protocol_version != RUNTIME_PROTOCOL_VERSION:
        raise QualificationInvalidInputError()
    vendor_family = evidence["vendor_family"]
    if vendor_family not in VENDORS:
        raise QualificationInvalidInputError()
    return _normalized_evidence(
        suite_revision=suite_revision,
        runtime_build_id=_require_build_id(evidence["runtime_build_id"]),
        platform=_require_platform(evidence["platform"]),
        protocol_version=protocol_version,
        adapter_id=_require_identifier(evidence["adapter_id"]),
        vendor_family=cast(VendorFamily, vendor_family),
        result_matrix=_parse_result_matrix(evidence["result_matrix"]),
        scenario_results=_parse_scenario_results(evidence["scenario_results"]),
    )


def _artifact_identity(
    path: Path,
    *,
    expected_size: int | None = None,
) -> tuple[int, str]:
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        if not os.access(path, os.X_OK):
            raise _ArtifactReadError()
        file_descriptor = os.open(path, flags)
        try:
            os.set_inheritable(file_descriptor, False)
            before = os.fstat(file_descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or not 1 <= before.st_size <= MAX_ARTIFACT_SIZE_BYTES
                or (expected_size is not None and before.st_size != expected_size)
            ):
                raise _ArtifactReadError()
            digest = hashlib.sha256()
            total = 0
            while True:
                try:
                    chunk = os.read(file_descriptor, READ_CHUNK_BYTES)
                except InterruptedError:
                    continue
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_ARTIFACT_SIZE_BYTES:
                    raise _ArtifactReadError()
                digest.update(chunk)
            after = os.fstat(file_descriptor)
            before_identity = (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            after_identity = (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            if total != before.st_size or before_identity != after_identity:
                raise _ArtifactReadError()
        finally:
            os.close(file_descriptor)
        if not os.access(path, os.X_OK):
            raise _ArtifactReadError()
    except (OSError, ValueError):
        raise _ArtifactReadError() from None
    return total, digest.hexdigest()


def build_qualification_candidate(evidence: object, runtime_path: Path) -> dict[str, object]:
    """Validate complete task-free evidence and build a review candidate.

    The returned object is not trusted until independently reviewed and added to
    the package-owned registry.
    """
    normalized = _parse_evidence(evidence)
    try:
        artifact_size_bytes, artifact_sha256 = _artifact_identity(runtime_path)
    except _ArtifactReadError:
        raise QualificationInvalidInputError() from None
    evidence_sha256 = hashlib.sha256(canonical_json_bytes(normalized)).hexdigest()
    return {
        "record_schema_version": RECORD_SCHEMA_VERSION,
        "artifact_sha256": artifact_sha256,
        "artifact_size_bytes": artifact_size_bytes,
        "runtime_build_id": normalized["runtime_build_id"],
        "platform": normalized["platform"],
        "protocol_version": normalized["protocol_version"],
        "suite_revision": normalized["suite_revision"],
        "adapter_id": normalized["adapter_id"],
        "vendor_family": normalized["vendor_family"],
        "conformance_evidence_sha256": evidence_sha256,
        "result_matrix": normalized["result_matrix"],
        "scenario_results": normalized["scenario_results"],
    }


def load_conformance_evidence(path: Path) -> dict[str, Any]:
    """Load one bounded task-free evidence document for candidate construction."""
    try:
        return load_json_object(path, max_bytes=MAX_EVIDENCE_BYTES)
    except (JsonInputError, RecursionError):
        raise QualificationInvalidInputError() from None


def _parse_record(value: object, suite_revision: str) -> QualificationRecord:
    record = _require_object(
        value,
        {
            "record_schema_version",
            "artifact_sha256",
            "artifact_size_bytes",
            "runtime_build_id",
            "platform",
            "protocol_version",
            "suite_revision",
            "adapter_id",
            "vendor_family",
            "conformance_evidence_sha256",
            "result_matrix",
            "scenario_results",
        },
    )
    if (
        _require_integer(record["record_schema_version"], minimum=1, maximum=1)
        != RECORD_SCHEMA_VERSION
    ):
        raise QualificationInvalidInputError()
    record_suite_revision = _require_identifier(record["suite_revision"])
    if record_suite_revision != suite_revision:
        raise QualificationInvalidInputError()
    protocol_version = _require_integer(
        record["protocol_version"], minimum=1, maximum=2_147_483_647
    )
    if protocol_version != RUNTIME_PROTOCOL_VERSION:
        raise QualificationInvalidInputError()
    vendor_family = record["vendor_family"]
    if vendor_family not in VENDORS:
        raise QualificationInvalidInputError()
    runtime_build_id = _require_build_id(record["runtime_build_id"])
    platform = _require_platform(record["platform"])
    adapter_id = _require_identifier(record["adapter_id"])
    result_matrix = _parse_result_matrix(record["result_matrix"])
    scenario_results = _parse_scenario_results(record["scenario_results"])
    normalized = _normalized_evidence(
        suite_revision=record_suite_revision,
        runtime_build_id=runtime_build_id,
        platform=platform,
        protocol_version=protocol_version,
        adapter_id=adapter_id,
        vendor_family=cast(VendorFamily, vendor_family),
        result_matrix=result_matrix,
        scenario_results=scenario_results,
    )
    evidence_sha256 = _require_sha256(record["conformance_evidence_sha256"])
    if evidence_sha256 != hashlib.sha256(canonical_json_bytes(normalized)).hexdigest():
        raise QualificationInvalidInputError()
    return QualificationRecord(
        artifact_sha256=_require_sha256(record["artifact_sha256"]),
        artifact_size_bytes=_require_integer(
            record["artifact_size_bytes"], minimum=1, maximum=MAX_ARTIFACT_SIZE_BYTES
        ),
        runtime_build_id=runtime_build_id,
        platform=platform,
        protocol_version=protocol_version,
        suite_revision=record_suite_revision,
        adapter_id=adapter_id,
        vendor_family=cast(VendorFamily, vendor_family),
        conformance_evidence_sha256=evidence_sha256,
        result_matrix=result_matrix,
        scenario_results=scenario_results,
    )


def _parse_registry(value: object) -> QualificationRegistry:
    registry = _require_object(
        value,
        {"registry_schema_version", "suite_revision", "records"},
    )
    if (
        _require_integer(registry["registry_schema_version"], minimum=1, maximum=1)
        != REGISTRY_SCHEMA_VERSION
    ):
        raise QualificationInvalidInputError()
    suite_revision = _require_identifier(registry["suite_revision"])
    if suite_revision != CURRENT_SUITE_REVISION:
        raise QualificationInvalidInputError()
    values = registry["records"]
    if not isinstance(values, list) or len(values) > MAX_QUALIFICATION_RECORDS:
        raise QualificationInvalidInputError()
    records = tuple(_parse_record(item, suite_revision) for item in values)
    if len({record.selector for record in records}) != len(records):
        raise QualificationInvalidInputError()
    return QualificationRegistry(suite_revision=suite_revision, records=records)


def load_qualification_registry(path: Path) -> QualificationRegistry:
    """Load a strict bounded registry, primarily for package and test use."""
    try:
        value = load_json_object(path, max_bytes=MAX_REGISTRY_BYTES)
        return _parse_registry(value)
    except (JsonInputError, RecursionError):
        raise QualificationInvalidInputError() from None


def load_packaged_qualification_registry() -> QualificationRegistry:
    """Load the only registry accepted by production route selection."""
    registry_resource = resources.files("weightclass").joinpath(PACKAGE_REGISTRY_NAME)
    try:
        with resources.as_file(registry_resource) as registry_path:
            return load_qualification_registry(registry_path)
    except (FileNotFoundError, OSError):
        raise QualificationInvalidInputError() from None


def select_qualification_for_descriptor(
    descriptor: dict[str, Any],
    registry: QualificationRegistry,
) -> QualificationRecord:
    """Select exactly one package record matching the compiled route identity."""
    try:
        adapter = descriptor["adapter"]
        platform = descriptor["target_platform"]
        if not isinstance(adapter, dict):
            raise QualificationInvalidInputError()
        selector = (
            adapter["runtime_build_id"],
            PlatformContract(os=platform["os"], architecture=platform["architecture"]),
            descriptor["runtime_protocol_version"],
            adapter["id"],
            descriptor["source_vendor"],
        )
    except (KeyError, TypeError):
        raise QualificationInvalidInputError() from None
    matches = [record for record in registry.records if record.selector == selector]
    if len(matches) != 1:
        raise QualificationUnsupportedError()
    return matches[0]


def attach_qualification_requirement(
    descriptor: dict[str, Any],
    record: QualificationRecord,
) -> dict[str, Any]:
    """Bind a selected package record into a newly fingerprinted route."""
    qualified = dict(descriptor)
    qualified["run_requirement"] = {
        "kind": "exact_artifact_conformance",
        "artifact_sha256": record.artifact_sha256,
        "artifact_size_bytes": record.artifact_size_bytes,
        "runtime_build_id": record.runtime_build_id,
        "platform": asdict(record.platform),
        "protocol_version": record.protocol_version,
        "suite_revision": record.suite_revision,
        "adapter_id": record.adapter_id,
        "vendor_family": record.vendor_family,
        "conformance_evidence_sha256": record.conformance_evidence_sha256,
    }
    return bind_delegation_fingerprint(qualified)


def verify_qualified_runtime(runtime_path: Path, record: QualificationRecord) -> None:
    """Require exact recorded bytes at an executable regular-file path."""
    try:
        size, digest = _artifact_identity(
            runtime_path,
            expected_size=record.artifact_size_bytes,
        )
    except _ArtifactReadError:
        raise QualifiedRuntimeUnavailableError() from None
    if size != record.artifact_size_bytes or digest != record.artifact_sha256:
        raise QualifiedRuntimeUnavailableError()
