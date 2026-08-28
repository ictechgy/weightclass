#!/usr/bin/env python3
"""Owner-private onboarding and dispatch for reusable advisory campaigns."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, NoReturn, TypedDict

if TYPE_CHECKING or __package__:
    from weightclass import __version__ as _PACKAGE_VERSION

    from . import (
        advisory_campaign,
        advisory_evidence_contract,
        advisory_experiments,
        advisory_orchestration,
        advisory_parallel,
        advisory_portfolio,
        advisory_preflight,
        advisory_routes,
        managed_verify,
        speculative_run,
    )
    from .advisory_diagnostics import (
        CHILD_FAILURE_CODES,
        CONSULT_FAILURE_CODES,
        CONSULT_FAILURE_STAGES,
        PROVIDER_CHECK_FAILURE_CODES,
        RESULT_SHAPES,
    )
else:
    import advisory_campaign  # type: ignore[import-not-found]
    import advisory_evidence_contract  # type: ignore[import-not-found]
    import advisory_experiments  # type: ignore[import-not-found]
    import advisory_orchestration  # type: ignore[import-not-found]
    import advisory_parallel  # type: ignore[import-not-found]
    import advisory_portfolio  # type: ignore[import-not-found]
    import advisory_preflight  # type: ignore[import-not-found]
    import advisory_routes  # type: ignore[import-not-found]
    import managed_verify  # type: ignore[import-not-found]
    import speculative_run  # type: ignore[import-not-found]
    from advisory_diagnostics import (  # type: ignore[import-not-found]
        CHILD_FAILURE_CODES,
        CONSULT_FAILURE_CODES,
        CONSULT_FAILURE_STAGES,
        PROVIDER_CHECK_FAILURE_CODES,
        RESULT_SHAPES,
    )

    try:
        from weightclass import __version__ as _PACKAGE_VERSION
    except ImportError:
        _PACKAGE_VERSION = "source-tree"

PACKAGE_VERSION = _PACKAGE_VERSION
PACKAGE_ROOT = Path(__file__).resolve().parent.parent

SCHEMA_VERSION = 1
WORKFLOWS = ("implementation", "review", "research", "diagnosis", "design")
EVIDENCE_WORKFLOWS = WORKFLOWS[1:]
CLAUDE_EVIDENCE_GENERATION = "structured-v6"
AGY_ROUTE_GENERATION = "cli-v2"
GROK_EVIDENCE_GENERATION = "structured-v1"
PREREGISTERED_CAMPAIGN_GENERATION = "gate-v1"
PREVIOUS_CLAUDE_EVIDENCE_GENERATIONS = (
    "structured-v5",
    "structured-v4",
    "structured-v3",
    "structured-v2",
    "structured-v1",
)
ROLES = ("cheap", "advisor", "expensive")
BUILTIN_VENDORS = ("codex", "claude", "agy", "grok")
EXPECTED_BASELINE_FAILURE = 42
MAX_CONFIGURED_VENDORS = 16
MAX_PROFILE_BYTES = 131_072
SETUP_LOCK_TIMEOUT = 2.0
SETUP_LOCK_POLL_SECONDS = 0.02
RUNNER_VERSION_CHANGED_EXIT = 78
CONSULT_DEFAULT_TIMEOUT_SECONDS = 5_400.0
PROVIDER_CHECK_MAX_EXECUTABLE_GROUPS = 4
LEGACY_GATE_TARGET_RATE_BPS = 7_500
LEGACY_GATE_ALPHA_BPS = 500
_VENDOR = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_PROFILE_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RUNNER_BOOTSTRAP = """\
import sys
try:
    import importlib.metadata
    from pathlib import Path
    package_root = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(package_root.parent))
    import weightclass
    from weightclass.advisory import speculative_run
except Exception:
    raise SystemExit(78)
expected = sys.argv[2]
if Path(weightclass.__file__).resolve().parent != package_root:
    raise SystemExit(78)
installed = any(part in {"site-packages", "dist-packages"} for part in package_root.parts)
try:
    metadata_version = importlib.metadata.version("weightclass") if installed else expected
except Exception:
    raise SystemExit(78)
if weightclass.__version__ != expected or metadata_version != expected:
    raise SystemExit(78)
sys.argv = [sys.argv[0], *sys.argv[3:]]
raise SystemExit(speculative_run.main())
"""
_CONSULT_RUNNER_BOOTSTRAP = """\
import sys
try:
    import importlib.metadata
    from pathlib import Path
    package_root = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(package_root.parent))
    import weightclass
    from weightclass.advisory import advisory_consult
except Exception:
    raise SystemExit(78)
expected = sys.argv[2]
if Path(weightclass.__file__).resolve().parent != package_root:
    raise SystemExit(78)
installed = any(part in {"site-packages", "dist-packages"} for part in package_root.parts)
try:
    metadata_version = importlib.metadata.version("weightclass") if installed else expected
except Exception:
    raise SystemExit(78)
if weightclass.__version__ != expected or metadata_version != expected:
    raise SystemExit(78)
sys.argv = [sys.argv[0], *sys.argv[3:]]
raise SystemExit(advisory_consult.main())
"""


class ManagedAdvisoryError(ValueError):
    """Value-free rejection of unsafe or inconsistent managed configuration."""


class SetupUnavailableError(ValueError):
    """The bounded managed setup lock remained owned by another process."""


class RunnerVersionChangedError(RuntimeError):
    """The installed advisory package changed after managed preflight."""


class ManagedPreflightError(RuntimeError):
    """One task-free managed preflight failed with a closed reason code."""

    code: str

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ProviderConfirmationRequiredError(RuntimeError):
    """A custom provider route needs an explicit task-free provider check."""


class ProviderConformanceError(RuntimeError):
    """A confirmed custom-provider check failed before task inspection."""


class ProviderCapabilityError(ValueError):
    """One task-free vendor CLI check failed before task inspection."""

    vendor: str
    role: str
    code: str

    def __init__(self, vendor: str, role: str, code: str) -> None:
        self.vendor = vendor
        self.role = role
        self.code = code
        super().__init__(code)


class ConsultDiagnosticError(ValueError):
    """An internal consult diagnostic did not match the closed contract."""


def _replay_output(stream: BinaryIO, payload: bytes) -> None:
    """Best-effort operator output must not change a completed campaign result."""
    if not payload:
        return
    try:
        stream.write(payload)
        stream.flush()
    except (OSError, ValueError):
        pass


def _dispatch_started_receipt(
    workflow: str, leases: Sequence[advisory_orchestration.LaneLease]
) -> bytes:
    payload = {
        "schema_version": 1,
        "event": "managed_dispatch_started",
        "workflow": workflow,
        "leases": [{"vendor": lease.vendor, "lane_index": lease.lane_index} for lease in leases],
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _dispatch_progress_receipt(
    workflow: str, vendor: str, event: str, elapsed_seconds: int
) -> bytes:
    event_name = {
        "heartbeat": "managed_vendor_heartbeat",
        "completed": "managed_vendor_completed",
    }.get(event)
    if event_name is None or vendor not in BUILTIN_VENDORS and not _VENDOR.fullmatch(vendor):
        return b""
    payload = {
        "schema_version": 1,
        "event": event_name,
        "workflow": workflow,
        "vendor": vendor,
        "elapsed_seconds": max(0, elapsed_seconds),
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


class InitializationReceipt(TypedDict):
    schema_version: int
    vendor: str
    workflows: list[str]
    cost_basis: str
    already_initialized: bool
    dry_run: bool


class DoctorReceipt(TypedDict):
    schema_version: int
    ready: bool
    campaign_ready: bool
    dispatch_ready: bool
    lane_count: int
    vendors: list[str]
    workflows: list[str]
    availability: list[dict[str, object]]
    cli: list[dict[str, object]]


class EvidenceMigrationReceipt(TypedDict):
    schema_version: int
    vendor: str
    workflows: list[str]
    generation: str
    already_migrated: bool
    legacy_preserved: bool
    dry_run: bool


@dataclass(frozen=True)
class CampaignPaths:
    profile: Path
    prices: Path
    campaign: Path
    results: Path


@dataclass(frozen=True)
class ConsultConfiguration:
    paths: CampaignPaths
    routes: advisory_routes.AdvisoryRoutes
    custom: bool
    profile_sha256: str
    route_sha256: str


@dataclass(frozen=True)
class _ProviderProbe:
    vendor: str
    role: str
    command: tuple[str, ...]
    executable_group: tuple[str, int, int] | tuple[str, str]


def _fail() -> NoReturn:
    raise ManagedAdvisoryError()


def _nofollow() -> int:
    value = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(value, int):
        _fail()
    return value


def default_state_root() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "weightclass" / "advisory-v1"
    state_home = os.environ.get("XDG_STATE_HOME")
    root = (
        Path(state_home)
        if state_home and Path(state_home).is_absolute()
        else Path.home() / ".local" / "state"
    )
    return root / "weightclass" / "advisory-v1"


def campaign_paths(state_root: Path, vendor: str, workflow: str) -> CampaignPaths:
    if not state_root.is_absolute() or not _VENDOR.fullmatch(vendor) or workflow not in WORKFLOWS:
        _fail()
    if vendor == "agy":
        infix = (
            f"-{AGY_ROUTE_GENERATION}"
            if workflow == "implementation"
            else f"-{workflow}-{AGY_ROUTE_GENERATION}"
        )
    elif vendor == "claude" and workflow in EVIDENCE_WORKFLOWS:
        infix = f"-{workflow}-{CLAUDE_EVIDENCE_GENERATION}"
    elif vendor == "grok" and workflow in EVIDENCE_WORKFLOWS:
        infix = f"-{workflow}-{GROK_EVIDENCE_GENERATION}"
    else:
        infix = "" if workflow == "implementation" else f"-{workflow}"
    return CampaignPaths(
        profile=state_root / f"{vendor}-profile.json",
        prices=state_root / f"{vendor}-prices.json",
        campaign=state_root / f"{vendor}{infix}-shape-b.json",
        results=state_root / f"{vendor}{infix}-results",
    )


def preregistered_campaign_paths(state_root: Path, vendor: str, workflow: str) -> CampaignPaths:
    """Return the separate sealed-gate generation without touching old bytes."""
    selected = campaign_paths(state_root, vendor, workflow)
    return CampaignPaths(
        selected.profile,
        selected.prices,
        selected.campaign.with_name(
            f"{selected.campaign.stem}-{PREREGISTERED_CAMPAIGN_GENERATION}.json"
        ),
        selected.results.with_name(f"{selected.results.name}-{PREREGISTERED_CAMPAIGN_GENERATION}"),
    )


def _active_campaign_paths(state_root: Path, vendor: str, workflow: str) -> CampaignPaths:
    """Select a complete gated generation, otherwise retain the legacy path."""
    gated = preregistered_campaign_paths(state_root, vendor, workflow)
    if gated.campaign.exists() or gated.results.exists():
        return gated
    return campaign_paths(state_root, vendor, workflow)


def legacy_campaign_paths(state_root: Path, vendor: str, workflow: str) -> CampaignPaths:
    if not state_root.is_absolute() or not _VENDOR.fullmatch(vendor) or workflow not in WORKFLOWS:
        _fail()
    infix = "" if workflow == "implementation" else f"-{workflow}"
    return CampaignPaths(
        profile=state_root / f"{vendor}-profile.json",
        prices=state_root / f"{vendor}-prices.json",
        campaign=state_root / f"{vendor}{infix}-shape-b.json",
        results=state_root / f"{vendor}{infix}-results",
    )


def previous_evidence_campaign_paths(
    state_root: Path, vendor: str, workflow: str, generation: str = "structured-v4"
) -> CampaignPaths:
    if (
        not state_root.is_absolute()
        or vendor != "claude"
        or workflow not in EVIDENCE_WORKFLOWS
        or generation not in PREVIOUS_CLAUDE_EVIDENCE_GENERATIONS
    ):
        _fail()
    infix = f"-{workflow}-{generation}"
    return CampaignPaths(
        profile=state_root / f"{vendor}-profile.json",
        prices=state_root / f"{vendor}-prices.json",
        campaign=state_root / f"{vendor}{infix}-shape-b.json",
        results=state_root / f"{vendor}{infix}-results",
    )


def _private_directory(path: Path, *, create: bool) -> None:
    if not path.is_absolute():
        _fail()
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if not create:
            _fail()
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.mkdir(mode=0o700)
            metadata = path.lstat()
        except OSError as error:
            raise ManagedAdvisoryError() from error
    except OSError as error:
        raise ManagedAdvisoryError() from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        _fail()


def _private_regular(path: Path, *, executable: bool = False) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ManagedAdvisoryError() from error
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or mode & 0o077
        or (executable and not mode & stat.S_IXUSR)
    ):
        _fail()


def _regular_bytes(path: Path, maximum: int) -> bytes:
    nofollow = _nofollow()
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            _fail()
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError as error:
        raise ManagedAdvisoryError() from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    payload = b"".join(chunks)
    if not payload or len(payload) > maximum:
        _fail()
    return payload


def _write_private(path: Path, payload: bytes, *, executable: bool = False) -> None:
    mode = 0o700 if executable else 0o600
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    flags |= getattr(os, "O_CLOEXEC", 0) | _nofollow()
    descriptor = -1
    try:
        descriptor = os.open(path, flags, mode)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError()
            view = view[written:]
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
    except OSError as error:
        raise ManagedAdvisoryError() from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _canonical_profile(profile: Mapping[str, object]) -> tuple[dict[str, object], bytes]:
    try:
        normalized = dict(profile)
        advisory_routes.build_routes(normalized)
        advisory_routes.build_routes(normalized, read_only_executors=True)
        vendor = normalized["vendor"]
        if not isinstance(vendor, str) or not _VENDOR.fullmatch(vendor):
            _fail()
        payload = (
            json.dumps(
                normalized,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
            + b"\n"
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeError,
        advisory_routes.AdvisoryRouteError,
    ) as error:
        raise ManagedAdvisoryError() from error
    return normalized, payload


def _profile_from_path(path: Path) -> dict[str, object]:
    try:
        return advisory_routes.load_profile(path)
    except (OSError, advisory_routes.AdvisoryRouteError) as error:
        raise ManagedAdvisoryError() from error


def _price_payload(path: Path | None) -> bytes | None:
    if path is None:
        return None
    try:
        advisory_campaign.price_table_sha256(path)
        return _regular_bytes(path, advisory_campaign.MAX_PRICES_BYTES)
    except (OSError, advisory_campaign.CampaignError) as error:
        raise ManagedAdvisoryError() from error


def _setup_lock(state_root: Path) -> int:
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= _nofollow()
    descriptor = -1
    try:
        descriptor = os.open(state_root / ".setup.lock", flags, 0o600)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            _fail()
        deadline = time.monotonic() + SETUP_LOCK_TIMEOUT
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return descriptor
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise SetupUnavailableError from None
                time.sleep(SETUP_LOCK_POLL_SECONDS)
    except SetupUnavailableError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except (OSError, ManagedAdvisoryError) as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise ManagedAdvisoryError() from error


def _release_lock(descriptor: int) -> None:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _manifest_for(
    profile_path: Path,
    *,
    workflow: str,
    verifier: Path,
    prices: Path | None,
    planned_tasks: int,
    max_tasks: int,
    gate: Mapping[str, object] | None = None,
) -> advisory_campaign.CampaignManifest:
    try:
        routes = advisory_routes.routes_from_profile(
            profile_path,
            read_only_executors=workflow != "implementation",
            evidence_workflow=workflow if workflow != "implementation" else None,
        )
        return advisory_campaign.build_manifest(
            arm="shape_b",
            workflow=workflow,
            planned_tasks=planned_tasks,
            max_tasks=max_tasks,
            cost_basis="price_table" if prices is not None else "vendor",
            cheap=routes.cheap,
            advisor=routes.advisor,
            expensive=routes.expensive,
            advisor_context="prompt",
            verify=verifier,
            prices=prices,
            gate=gate,
        )
    except (OSError, advisory_campaign.CampaignError, advisory_routes.AdvisoryRouteError) as error:
        raise ManagedAdvisoryError() from error


def _existing_matches(
    state_root: Path,
    *,
    profile_payload: bytes,
    vendor: str,
    prices_payload: bytes | None,
    planned_tasks: int,
    max_tasks: int,
    gate: Mapping[str, object] | None = None,
) -> bool:
    verifier = state_root / "verify-project.py"
    try:
        _private_regular(verifier, executable=True)
        profile_path = campaign_paths(state_root, vendor, "implementation").profile
        _private_regular(profile_path)
        if _regular_bytes(profile_path, MAX_PROFILE_BYTES) != profile_payload:
            return False
        if gate is None and any(
            preregistered_campaign_paths(state_root, vendor, workflow).campaign.exists()
            or preregistered_campaign_paths(state_root, vendor, workflow).results.exists()
            for workflow in WORKFLOWS
        ):
            return False
        prices_path = campaign_paths(state_root, vendor, "implementation").prices
        if prices_payload is None:
            if prices_path.exists() or prices_path.is_symlink():
                return False
            selected_prices: Path | None = None
        else:
            _private_regular(prices_path)
            if _regular_bytes(prices_path, advisory_campaign.MAX_PRICES_BYTES) != prices_payload:
                return False
            selected_prices = prices_path
        for workflow in WORKFLOWS:
            selected = (
                preregistered_campaign_paths(state_root, vendor, workflow)
                if gate is not None
                else campaign_paths(state_root, vendor, workflow)
            )
            _private_regular(selected.campaign)
            _private_directory(selected.results, create=False)
            actual = advisory_campaign.canonical_manifest_bytes(
                advisory_campaign.load_manifest(selected.campaign)
            )
            expected = advisory_campaign.canonical_manifest_bytes(
                _manifest_for(
                    profile_path,
                    workflow=workflow,
                    verifier=verifier,
                    prices=selected_prices,
                    planned_tasks=planned_tasks,
                    max_tasks=max_tasks,
                    gate=gate,
                )
            )
            if actual != expected:
                return False
        return True
    except (OSError, ManagedAdvisoryError, advisory_campaign.CampaignError):
        return False


def _receipt(vendor: str, *, prices: bool, already: bool, dry_run: bool) -> InitializationReceipt:
    return {
        "schema_version": SCHEMA_VERSION,
        "vendor": vendor,
        "workflows": list(WORKFLOWS),
        "cost_basis": "price_table" if prices else "vendor",
        "already_initialized": already,
        "dry_run": dry_run,
    }


def _validate_prospective_campaigns(
    profile: Mapping[str, object],
    *,
    prices: Path | None,
    planned_tasks: int,
    max_tasks: int,
    gate: Mapping[str, object] | None = None,
) -> None:
    try:
        for workflow in WORKFLOWS:
            routes = advisory_routes.build_routes(
                profile, read_only_executors=workflow != "implementation"
            )
            advisory_campaign.build_manifest(
                arm="shape_b",
                workflow=workflow,
                planned_tasks=planned_tasks,
                max_tasks=max_tasks,
                cost_basis="price_table" if prices is not None else "vendor",
                cheap=routes.cheap,
                advisor=routes.advisor,
                expensive=routes.expensive,
                advisor_context="prompt",
                verify=Path(managed_verify.__file__),
                prices=prices,
                gate=gate,
            )
    except (OSError, advisory_campaign.CampaignError, advisory_routes.AdvisoryRouteError) as error:
        raise ManagedAdvisoryError() from error


def initialize_campaign_set(
    state_root: Path,
    *,
    profile: Mapping[str, object],
    prices: Path | None,
    planned_tasks: int,
    max_tasks: int,
    dry_run: bool,
    gate: Mapping[str, object] | None = None,
) -> InitializationReceipt:
    normalized, profile_payload = _canonical_profile(profile)
    vendor = normalized["vendor"]
    assert isinstance(vendor, str)
    prices_payload = _price_payload(prices)
    _validate_prospective_campaigns(
        normalized,
        prices=prices,
        planned_tasks=planned_tasks,
        max_tasks=max_tasks,
        gate=gate,
    )
    if dry_run:
        return _receipt(vendor, prices=prices_payload is not None, already=False, dry_run=True)
    _private_directory(state_root, create=True)
    descriptor = _setup_lock(state_root)
    try:
        profile_path = campaign_paths(state_root, vendor, "implementation").profile
        if profile_path.exists() or profile_path.is_symlink():
            if not _existing_matches(
                state_root,
                profile_payload=profile_payload,
                vendor=vendor,
                prices_payload=prices_payload,
                planned_tasks=planned_tasks,
                max_tasks=max_tasks,
                gate=gate,
            ):
                _fail()
            return _receipt(vendor, prices=prices_payload is not None, already=True, dry_run=False)

        created_files: list[Path] = []
        created_directories: list[Path] = []
        verifier = state_root / "verify-project.py"
        try:
            if verifier.exists() or verifier.is_symlink():
                _private_regular(verifier, executable=True)
            else:
                source = Path(managed_verify.__file__)
                _write_private(verifier, _regular_bytes(source, 1_048_576), executable=True)
                created_files.append(verifier)
            _write_private(profile_path, profile_payload)
            created_files.append(profile_path)
            selected_prices: Path | None = None
            if prices_payload is not None:
                selected_prices = campaign_paths(state_root, vendor, "implementation").prices
                _write_private(selected_prices, prices_payload)
                created_files.append(selected_prices)
            for workflow in WORKFLOWS:
                selected = (
                    preregistered_campaign_paths(state_root, vendor, workflow)
                    if gate is not None
                    else campaign_paths(state_root, vendor, workflow)
                )
                manifest = _manifest_for(
                    profile_path,
                    workflow=workflow,
                    verifier=verifier,
                    prices=selected_prices,
                    planned_tasks=planned_tasks,
                    max_tasks=max_tasks,
                    gate=gate,
                )
                advisory_campaign.write_manifest(selected.campaign, manifest)
                created_files.append(selected.campaign)
                selected.results.mkdir(mode=0o700)
                created_directories.append(selected.results)
            directory_descriptor = os.open(state_root, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except (OSError, ManagedAdvisoryError, advisory_campaign.CampaignError):
            for directory in reversed(created_directories):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            for file_path in reversed(created_files):
                try:
                    file_path.unlink()
                except OSError:
                    pass
            raise ManagedAdvisoryError() from None
    finally:
        _release_lock(descriptor)
    return _receipt(vendor, prices=prices_payload is not None, already=False, dry_run=False)


def _migration_plan(vendor: str) -> tuple[tuple[str, ...], str, tuple[str, ...]]:
    if vendor == "claude":
        return EVIDENCE_WORKFLOWS, CLAUDE_EVIDENCE_GENERATION, PREVIOUS_CLAUDE_EVIDENCE_GENERATIONS
    if vendor == "grok":
        return EVIDENCE_WORKFLOWS, GROK_EVIDENCE_GENERATION, ()
    if vendor == "agy":
        return WORKFLOWS, AGY_ROUTE_GENERATION, ()
    _fail()


def _migration_receipt(
    vendor: str,
    workflows: Sequence[str],
    generation: str,
    *,
    already: bool,
    dry_run: bool,
) -> EvidenceMigrationReceipt:
    return {
        "schema_version": SCHEMA_VERSION,
        "vendor": vendor,
        "workflows": list(workflows),
        "generation": generation,
        "already_migrated": already,
        "legacy_preserved": True,
        "dry_run": dry_run,
    }


def migrate_vendor_campaigns(
    state_root: Path, *, vendor: str, dry_run: bool
) -> EvidenceMigrationReceipt:
    """Create a new route generation without changing old populations."""
    workflows, generation, previous_generations = _migration_plan(vendor)
    _private_directory(state_root, create=False)
    descriptor = _setup_lock(state_root)
    created_files: list[Path] = []
    created_directories: list[Path] = []
    try:
        current = [campaign_paths(state_root, vendor, workflow) for workflow in workflows]
        current_exists = [
            selected.campaign.exists() or selected.results.exists() for selected in current
        ]
        if any(current_exists):
            if not all(current_exists):
                _fail()
            for workflow in workflows:
                _configuration(state_root, vendor, workflow)
            return _migration_receipt(vendor, workflows, generation, already=True, dry_run=dry_run)

        profile_path = legacy_campaign_paths(state_root, vendor, "implementation").profile
        _private_regular(profile_path)
        profile = _profile_from_path(profile_path)
        if profile.get("vendor") != vendor:
            _fail()
        verifier = state_root / "verify-project.py"
        _private_regular(verifier, executable=True)
        prices_path = legacy_campaign_paths(state_root, vendor, "implementation").prices
        selected_prices: Path | None = None
        if prices_path.exists() or prices_path.is_symlink():
            _private_regular(prices_path)
            selected_prices = prices_path

        source_manifests: dict[str, advisory_campaign.CampaignManifest] | None = None
        source_sets = tuple(
            {
                workflow: previous_evidence_campaign_paths(state_root, vendor, workflow, generation)
                for workflow in workflows
            }
            for generation in previous_generations
        ) + (
            {
                workflow: legacy_campaign_paths(state_root, vendor, workflow)
                for workflow in workflows
            },
        )
        for source_set in source_sets:
            presence = [
                selected.campaign.exists() or selected.results.exists()
                for selected in source_set.values()
            ]
            if not any(presence):
                continue
            if not all(presence):
                _fail()
            validated: dict[str, advisory_campaign.CampaignManifest] = {}
            for workflow, selected in source_set.items():
                _private_regular(selected.campaign)
                _private_directory(selected.results, create=False)
                manifest = advisory_campaign.load_manifest(selected.campaign)
                bound_workflow = manifest.get("workflow")
                if (
                    (workflow == "implementation" and bound_workflow not in (None, workflow))
                    or (workflow != "implementation" and bound_workflow != workflow)
                    or (manifest["cost_basis"] == "price_table") != (selected_prices is not None)
                ):
                    _fail()
                advisory_campaign.load_merged_lane_records(
                    manifest,
                    selected.results,
                    advisory_campaign.ANONYMOUS_LANE_COUNT,
                )
                validated[workflow] = manifest
            source_manifests = validated
            break
        if source_manifests is None:
            _fail()

        if dry_run:
            return _migration_receipt(vendor, workflows, generation, already=False, dry_run=True)

        try:
            for workflow, selected in zip(workflows, current, strict=True):
                previous = source_manifests[workflow]
                manifest = _manifest_for(
                    profile_path,
                    workflow=workflow,
                    verifier=verifier,
                    prices=selected_prices,
                    planned_tasks=previous["planned_tasks"],
                    max_tasks=previous["max_tasks"],
                )
                advisory_campaign.write_manifest(selected.campaign, manifest)
                created_files.append(selected.campaign)
                selected.results.mkdir(mode=0o700)
                created_directories.append(selected.results)
            directory_descriptor = os.open(state_root, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except (OSError, ManagedAdvisoryError, advisory_campaign.CampaignError):
            for directory in reversed(created_directories):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            for file_path in reversed(created_files):
                try:
                    file_path.unlink()
                except OSError:
                    pass
            raise ManagedAdvisoryError() from None
    finally:
        _release_lock(descriptor)
    return _migration_receipt(vendor, workflows, generation, already=False, dry_run=False)


def migrate_evidence_campaigns(
    state_root: Path, *, vendor: str, dry_run: bool
) -> EvidenceMigrationReceipt:
    if vendor not in {"claude", "grok"}:
        _fail()
    return migrate_vendor_campaigns(state_root, vendor=vendor, dry_run=dry_run)


def migrate_gate_campaigns(
    state_root: Path,
    *,
    vendor: str,
    gate: Mapping[str, object],
    dry_run: bool,
) -> EvidenceMigrationReceipt:
    """Create a sealed gate generation while preserving every prior population."""
    if vendor not in BUILTIN_VENDORS and not _VENDOR.fullmatch(vendor):
        _fail()
    validated_gate = advisory_campaign.validate_gate(gate)
    _private_directory(state_root, create=False)
    descriptor = _setup_lock(state_root)
    created_files: list[Path] = []
    created_directories: list[Path] = []
    try:
        source = [campaign_paths(state_root, vendor, workflow) for workflow in WORKFLOWS]
        destination = [
            preregistered_campaign_paths(state_root, vendor, workflow) for workflow in WORKFLOWS
        ]
        destination_presence = [
            selected.campaign.exists() or selected.results.exists() for selected in destination
        ]
        if any(destination_presence):
            if not all(destination_presence):
                _fail()
            for workflow, selected in zip(WORKFLOWS, destination, strict=True):
                configured, manifest, _ = _configuration(state_root, vendor, workflow)
                if configured != selected:
                    _fail()
                if manifest.get("gate") != dict(validated_gate):
                    _fail()
            return _migration_receipt(
                vendor,
                WORKFLOWS,
                PREREGISTERED_CAMPAIGN_GENERATION,
                already=True,
                dry_run=dry_run,
            )
        if not all(
            selected.campaign.is_file() and selected.results.is_dir() for selected in source
        ):
            _fail()
        profile_path = source[0].profile
        _private_regular(profile_path)
        profile = _profile_from_path(profile_path)
        if profile.get("vendor") != vendor:
            _fail()
        verifier = state_root / "verify-project.py"
        _private_regular(verifier, executable=True)
        prices_path = source[0].prices
        selected_prices: Path | None = None
        if prices_path.exists() or prices_path.is_symlink():
            _private_regular(prices_path)
            selected_prices = prices_path
        source_manifests: list[advisory_campaign.CampaignManifest] = []
        for workflow, selected in zip(WORKFLOWS, source, strict=True):
            configured, manifest, _ = _configuration(state_root, vendor, workflow)
            if configured != selected:
                _fail()
            source_manifests.append(manifest)
        if dry_run:
            return _migration_receipt(
                vendor,
                WORKFLOWS,
                PREREGISTERED_CAMPAIGN_GENERATION,
                already=False,
                dry_run=True,
            )
        try:
            for workflow, selected, previous in zip(
                WORKFLOWS, destination, source_manifests, strict=True
            ):
                manifest = _manifest_for(
                    profile_path,
                    workflow=workflow,
                    verifier=verifier,
                    prices=selected_prices,
                    planned_tasks=previous["planned_tasks"],
                    max_tasks=previous["max_tasks"],
                    gate=validated_gate,
                )
                advisory_campaign.write_manifest(selected.campaign, manifest)
                created_files.append(selected.campaign)
                selected.results.mkdir(mode=0o700)
                created_directories.append(selected.results)
            directory_descriptor = os.open(state_root, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except (OSError, ManagedAdvisoryError, advisory_campaign.CampaignError):
            for directory in reversed(created_directories):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            for file_path in reversed(created_files):
                try:
                    file_path.unlink()
                except OSError:
                    pass
            raise ManagedAdvisoryError() from None
    finally:
        _release_lock(descriptor)
    return _migration_receipt(
        vendor,
        WORKFLOWS,
        PREREGISTERED_CAMPAIGN_GENERATION,
        already=False,
        dry_run=False,
    )


def configured_vendors(state_root: Path) -> tuple[str, ...]:
    _private_directory(state_root, create=False)
    try:
        candidates = tuple(state_root.glob("*-profile.json"))
    except OSError as error:
        raise ManagedAdvisoryError() from error
    if not candidates or len(candidates) > MAX_CONFIGURED_VENDORS:
        _fail()
    vendors: list[str] = []
    for path in candidates:
        name = path.name.removesuffix("-profile.json")
        if not _VENDOR.fullmatch(name):
            _fail()
        _private_regular(path)
        profile = _profile_from_path(path)
        if profile.get("vendor") != name:
            _fail()
        vendors.append(name)
    legacy_order = [vendor for vendor in BUILTIN_VENDORS if vendor in vendors]
    legacy_order.extend(sorted(set(vendors) - set(legacy_order)))
    return tuple(legacy_order)


def _selected_vendors(state_root: Path, requested: str) -> tuple[str, ...]:
    available = configured_vendors(state_root)
    if requested in {"all", "both"}:
        selected = (
            tuple(vendor for vendor in ("claude", "codex") if vendor in available)
            if requested == "both"
            else available
        )
        if not selected:
            _fail()
        return selected
    if requested not in available:
        _fail()
    return (requested,)


def _selected_workflows(requested: str) -> tuple[str, ...]:
    if requested == "all":
        return WORKFLOWS
    if requested not in WORKFLOWS:
        _fail()
    return (requested,)


def _consult_route_acknowledgements(
    values: Sequence[str], vendors: Sequence[str]
) -> dict[str, str]:
    acknowledgements: dict[str, str] = {}
    for value in values:
        vendor, separator, fingerprint = value.partition("=")
        if (
            separator != "="
            or vendor not in vendors
            or vendor in acknowledgements
            or not _PROFILE_SHA256.fullmatch(fingerprint)
        ):
            _fail()
        acknowledgements[vendor] = fingerprint
    if set(acknowledgements) != set(vendors):
        _fail()
    return acknowledgements


def _route_capabilities(
    vendor: str,
    routes: advisory_routes.AdvisoryRoutes,
    *,
    required_roles: Sequence[str] | None = None,
) -> tuple[tuple[str, advisory_preflight.CapabilityResult], ...]:
    roles = _validated_required_roles(required_roles)
    cache: dict[str, advisory_preflight.CapabilityResult] = {}
    checked: list[tuple[str, advisory_preflight.CapabilityResult]] = []
    for role in roles:
        command = getattr(routes, role)
        executable = command[0]
        result = cache.get(executable)
        if result is None:
            result = advisory_preflight.check_local_capability(vendor, executable)
            cache[executable] = result
        checked.append((role, result))
    return tuple(checked)


def _validated_required_roles(required_roles: Sequence[str] | None) -> tuple[str, ...]:
    """Normalize an optional provider-check role subset in canonical order."""
    if required_roles is None:
        return ROLES
    if isinstance(required_roles, (str, bytes)):
        _fail()
    try:
        selected = tuple(required_roles)
    except (TypeError, ValueError):
        _fail()
    if (
        not selected
        or len(selected) > len(ROLES)
        or any(not isinstance(role, str) for role in selected)
        or len(set(selected)) != len(selected)
        or any(role not in ROLES for role in selected)
    ):
        _fail()
    # Receipt order is stable even when a caller supplies a different order.
    return tuple(role for role in ROLES if role in selected)


def _require_route_capabilities(
    configurations: Mapping[
        str,
        tuple[
            CampaignPaths,
            advisory_campaign.CampaignManifest,
            advisory_routes.AdvisoryRoutes,
        ],
    ],
) -> None:
    for vendor, (_, _, routes) in configurations.items():
        for role, result in _route_capabilities(vendor, routes):
            if not result.ready:
                raise ProviderCapabilityError(vendor, role, result.failure_code)


def _configuration(
    state_root: Path,
    vendor: str,
    workflow: str,
    *,
    validate_records: bool = True,
) -> tuple[CampaignPaths, advisory_campaign.CampaignManifest, advisory_routes.AdvisoryRoutes]:
    selected = _active_campaign_paths(state_root, vendor, workflow)
    verifier = state_root / "verify-project.py"
    _private_regular(verifier, executable=True)
    _private_regular(selected.profile)
    _private_regular(selected.campaign)
    _private_directory(selected.results, create=False)
    try:
        manifest = advisory_campaign.load_manifest(selected.campaign)
        routes = advisory_routes.routes_from_profile(
            selected.profile,
            read_only_executors=workflow != "implementation",
            evidence_workflow=workflow if workflow != "implementation" else None,
        )
        prices: Path | None = None
        prefer_prices = False
        if manifest["cost_basis"] == "price_table":
            _private_regular(selected.prices)
            prices = selected.prices
            prefer_prices = True
        advisory_campaign.validate_run_configuration(
            manifest,
            cheap=routes.cheap,
            advisor=routes.advisor,
            expensive=routes.expensive,
            advise_first=manifest["arm"] == "shape_a_b",
            advise_on_failure=True,
            advisor_context=str(manifest["advisor_context"]),
            verify=verifier,
            prices=prices,
            prefer_prices=prefer_prices,
            sample_ordinal=1,
            workflow=workflow,
        )
    except (OSError, advisory_campaign.CampaignError, advisory_routes.AdvisoryRouteError) as error:
        raise ManagedAdvisoryError() from error
    if validate_records:
        try:
            advisory_campaign.load_merged_lane_records(
                manifest,
                selected.results,
                advisory_campaign.ANONYMOUS_LANE_COUNT,
            )
        except advisory_campaign.CampaignError as error:
            raise advisory_orchestration.CampaignRecordsInvalidError(error) from None
    return selected, manifest, routes


def _consult_configuration(state_root: Path, vendor: str, workflow: str) -> ConsultConfiguration:
    selected = campaign_paths(state_root, vendor, workflow)
    verifier = state_root / "verify-project.py"
    _private_regular(verifier, executable=True)
    _private_regular(selected.profile)
    try:
        profile = _profile_from_path(selected.profile)
        routes = advisory_routes.build_routes(
            profile,
            read_only_executors=True,
            evidence_workflow=workflow,
        )
    except (OSError, advisory_routes.AdvisoryRouteError) as error:
        raise ManagedAdvisoryError() from error
    return ConsultConfiguration(
        selected,
        routes,
        profile.get("schema_version") == 2,
        advisory_routes.profile_digest(profile),
        advisory_routes.evidence_routes_digest(profile, routes, workflow),
    )


def _require_consult_capabilities(
    configurations: Mapping[str, ConsultConfiguration],
    *,
    role: str,
) -> None:
    for vendor, configuration in configurations.items():
        result = dict(_route_capabilities(vendor, configuration.routes, required_roles=(role,)))[
            role
        ]
        if not result.ready:
            raise ProviderCapabilityError(vendor, role, result.failure_code)


def doctor(state_root: Path, *, vendors: Sequence[str], workflows: Sequence[str]) -> DoctorReceipt:
    if not vendors or not workflows:
        _fail()
    availability: list[dict[str, object]] = []
    cli: list[dict[str, object]] = []
    dispatch_ready = True
    for vendor in vendors:
        first_routes: advisory_routes.AdvisoryRoutes | None = None
        for workflow in workflows:
            selected, _, routes = _configuration(state_root, vendor, workflow)
            if first_routes is None:
                first_routes = routes
            free, busy = advisory_orchestration.campaign_lane_availability(
                advisory_orchestration.LaneRequest(
                    vendor,
                    selected.results,
                    workflow=workflow,
                    campaign_path=selected.campaign,
                )
            )
            availability.append(
                {
                    "vendor": vendor,
                    "workflow": workflow,
                    "free": free,
                    "busy": busy,
                }
            )
        assert first_routes is not None
        for role, result in _route_capabilities(vendor, first_routes):
            value = result.receipt()
            value["role"] = role
            cli.append(value)
            dispatch_ready = dispatch_ready and result.ready
    return {
        "schema_version": SCHEMA_VERSION,
        "ready": dispatch_ready,
        "campaign_ready": True,
        "dispatch_ready": dispatch_ready,
        "lane_count": advisory_campaign.ANONYMOUS_LANE_COUNT,
        "vendors": list(vendors),
        "workflows": list(workflows),
        "availability": availability,
        "cli": cli,
    }


def _baseline_probe(workflow: str) -> bytes | None:
    if workflow == "implementation":
        return None
    common = {"schema_version": 1, "mode": workflow, "limitations": ["baseline_probe"]}
    if workflow == "review":
        common.update(summary="Prospective baseline probe.", findings=[])
    elif workflow == "research":
        common.update(
            question="Prospective baseline probe.",
            summary="No claim is established.",
            claims=[
                {
                    "claim": "No task-specific claim was evaluated.",
                    "status": "unresolved",
                    "confidence": "low",
                    "evidence": ["task-free baseline probe"],
                    "counterevidence": [],
                }
            ],
        )
    elif workflow == "diagnosis":
        common.update(
            symptom="Prospective baseline probe.",
            summary="No cause is established.",
            hypotheses=[
                {
                    "cause": "No task-specific cause was evaluated.",
                    "status": "unresolved",
                    "confidence": "low",
                    "evidence": ["task-free baseline probe"],
                    "counterevidence": [],
                }
            ],
            reproduction=["Run only the task-free baseline probe."],
        )
    else:
        common.update(
            problem="Prospective baseline probe.",
            summary="No design recommendation is established.",
            principles=["Do not infer task-specific principles."],
            options=[
                {
                    "title": "Task-free baseline probe",
                    "rationale": "Exercise the closed result contract.",
                    "evidence": ["No task-specific evidence was evaluated."],
                    "strengths": ["Exercises the contract."],
                    "risks": ["Must not be accepted."],
                    "affected_surfaces": ["baseline verifier"],
                }
            ],
            recommendation="Reject with exit code 42.",
            acceptance_criteria=["The baseline verifier returns 42."],
            validation=["Run only the task-free baseline probe."],
        )
    return json.dumps(common, separators=(",", ":")).encode("utf-8")


def _preflight_repo(repo: Path, workflow: str, verifier: Path) -> None:
    if not repo.is_absolute() or not repo.is_dir():
        raise ManagedPreflightError("managed_repo_unavailable")
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }
    try:
        status = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "status", "--porcelain=v1", "-z"],
            cwd=repo,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=30,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ManagedPreflightError("managed_repo_unavailable") from error
    if status.returncode != 0:
        raise ManagedPreflightError("managed_repo_unavailable")
    if status.stdout:
        raise ManagedPreflightError("managed_repo_dirty")
    verifier_environment = dict(environment)
    verifier_environment["WCLASS_ADVISORY_WORKFLOW"] = workflow
    try:
        completed = subprocess.run(
            [str(verifier)],
            cwd=repo,
            input=_baseline_probe(workflow),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=900,
            env=verifier_environment,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ManagedPreflightError("managed_verifier_unavailable") from error
    if completed.returncode != EXPECTED_BASELINE_FAILURE:
        raise ManagedPreflightError("managed_verifier_baseline_rejected")


def _preflight_task_file(task_file: Path) -> None:
    if not task_file.is_absolute():
        raise ManagedPreflightError("managed_task_input_rejected")
    try:
        metadata = task_file.lstat()
    except OSError as error:
        raise ManagedPreflightError("managed_task_input_rejected") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise ManagedPreflightError("managed_task_input_rejected")


def _next_ordinal(manifest: advisory_campaign.CampaignManifest, results: Path) -> int:
    try:
        records = advisory_campaign.load_bound_records(results / "runs.jsonl")
        return len(advisory_campaign.validate_record_bindings(manifest, records)) + 1
    except (OSError, advisory_campaign.CampaignError) as error:
        raise ManagedAdvisoryError() from error


def _job(
    vendor: str,
    workflow: str,
    repo: Path,
    task_file: Path,
    results: Path,
    selected: CampaignPaths,
    manifest: advisory_campaign.CampaignManifest,
    ordinal: int,
    verifier: Path,
) -> advisory_parallel.AdvisoryJob:
    runner_arguments = [
        "--workflow",
        workflow,
        "--vendor",
        vendor,
        "--repo",
        str(repo),
        "--task-file",
        str(task_file),
        "--route-profile",
        str(selected.profile),
        "--confirm-task-egress",
        "--advise-on-failure",
        "--advisor-context",
        str(manifest["advisor_context"]),
        "--verify",
        str(verifier),
        "--campaign",
        str(selected.campaign),
        "--sample-ordinal",
        str(ordinal),
        "--out-dir",
        str(results),
    ]
    if manifest["arm"] == "shape_a_b":
        runner_arguments.insert(runner_arguments.index("--advise-on-failure"), "--advise-first")
    if manifest["cost_basis"] == "price_table":
        index = runner_arguments.index("--campaign")
        runner_arguments[index:index] = ["--prices", str(selected.prices), "--prefer-prices"]
    command = [
        sys.executable,
        "-I",
        "-c",
        _RUNNER_BOOTSTRAP,
        str(PACKAGE_ROOT),
        PACKAGE_VERSION,
        *runner_arguments,
    ]
    return advisory_parallel.AdvisoryJob(vendor, tuple(command))


def _consult_job(
    vendor: str,
    workflow: str,
    role: str,
    repo: Path,
    task_file: Path,
    profile: Path,
    route_sha256: str,
    verifier: Path,
    timeout_seconds: float = CONSULT_DEFAULT_TIMEOUT_SECONDS,
) -> advisory_parallel.AdvisoryJob:
    return advisory_parallel.AdvisoryJob(
        vendor,
        (
            sys.executable,
            "-I",
            "-c",
            _CONSULT_RUNNER_BOOTSTRAP,
            str(PACKAGE_ROOT),
            PACKAGE_VERSION,
            "--expected-package-version",
            PACKAGE_VERSION,
            "--workflow",
            workflow,
            "--vendor",
            vendor,
            "--role",
            role,
            "--repo",
            str(repo),
            "--task-file",
            str(task_file),
            "--route-profile",
            str(profile),
            "--expected-route-sha256",
            route_sha256,
            "--verify",
            str(verifier),
        ),
        timeout_seconds=timeout_seconds,
    )


def _consult_result_receipt(vendor: str, workflow: str, result: Mapping[str, object]) -> bytes:
    payload = {
        "schema_version": 1,
        "event": "managed_consult_result",
        "vendor": vendor,
        "workflow": workflow,
        "content_trust": "untrusted_model_authored",
        "sample_recorded": False,
        "result": dict(result),
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


_CONSULT_DIAGNOSTIC_KEYS = frozenset(
    {
        "schema_version",
        "event",
        "failure_stage",
        "reason_code",
        "child_exit_code",
        "child_timed_out",
        "child_failure_code",
        "result_shape",
        "verify_exit_code",
        "verify_timed_out",
    }
)


def _consult_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ConsultDiagnosticError()
        result[key] = value
    return result


def _consult_exit_code(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not -255 <= value <= 255:
        raise ConsultDiagnosticError()
    return value


def _consult_child_diagnostic(payload: bytes) -> dict[str, object] | None:
    if not payload:
        return None
    selected: dict[str, object] | None = None
    for raw_line in payload.splitlines():
        if not raw_line or len(raw_line) > 4_096:
            continue
        try:
            value = json.loads(
                raw_line.decode("utf-8", errors="strict"),
                object_pairs_hook=_consult_json_object,
            )
            if (
                not isinstance(value, dict)
                or set(value) != _CONSULT_DIAGNOSTIC_KEYS
                or value["schema_version"] != 1
                or isinstance(value["schema_version"], bool)
                or value["event"] != "advisory_consult_failed"
                or not isinstance(value["failure_stage"], str)
                or value["failure_stage"] not in CONSULT_FAILURE_STAGES
                or not isinstance(value["reason_code"], str)
                or value["reason_code"] not in CONSULT_FAILURE_CODES
                or not isinstance(value["child_failure_code"], str)
                or value["child_failure_code"] not in CHILD_FAILURE_CODES
                or not isinstance(value["result_shape"], str)
                or value["result_shape"] not in RESULT_SHAPES
                or not isinstance(value["child_timed_out"], bool)
                or not isinstance(value["verify_timed_out"], bool)
            ):
                raise ConsultDiagnosticError()
            value["child_exit_code"] = _consult_exit_code(value["child_exit_code"])
            value["verify_exit_code"] = _consult_exit_code(value["verify_exit_code"])
            selected = value
        except (UnicodeError, ValueError, RecursionError):
            continue
    return selected


def _consult_failure_receipt(
    vendor: str,
    workflow: str,
    result: advisory_parallel.AdvisoryResult,
    diagnostic: Mapping[str, object] | None = None,
) -> bytes:
    fallback_stage = "execution" if result.timed_out or not result.started else "unknown"
    fallback_reason = (
        "route_execution_failed"
        if result.timed_out
        else "internal_failure"
        if not result.started
        else "internal_diagnostic_unavailable"
    )
    detail = diagnostic or {
        "failure_stage": fallback_stage,
        "reason_code": fallback_reason,
        "child_exit_code": None,
        "child_timed_out": False,
        "child_failure_code": "unknown",
        "result_shape": "unknown",
        "verify_exit_code": None,
        "verify_timed_out": False,
    }
    payload = {
        "schema_version": 1,
        "event": "managed_consult_failed",
        "vendor": vendor,
        "workflow": workflow,
        "failure_stage": detail["failure_stage"],
        "reason_code": detail["reason_code"],
        "child_exit_code": detail["child_exit_code"],
        "child_timed_out": detail["child_timed_out"],
        "child_failure_code": detail["child_failure_code"],
        "result_shape": detail["result_shape"],
        "verify_exit_code": detail["verify_exit_code"],
        "verify_timed_out": detail["verify_timed_out"],
        "returncode": result.returncode,
        "started": result.started,
        "timed_out": result.timed_out,
        "output_truncated": result.output_truncated,
        "sample_recorded": False,
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def consult(
    state_root: Path,
    *,
    repo: Path,
    task_file: Path,
    vendors: Sequence[str],
    workflow: str,
    role: str,
    acknowledged_route_sha256: Mapping[str, str],
    confirm_task_egress: bool,
    confirm_provider_egress: bool,
    timeout_seconds: float = CONSULT_DEFAULT_TIMEOUT_SECONDS,
) -> int:
    if not confirm_task_egress or not vendors or workflow not in EVIDENCE_WORKFLOWS:
        _fail()
    if role not in {"cheap", "expensive"}:
        _fail()
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not 1 <= timeout_seconds <= advisory_parallel.MAX_TIMEOUT_SECONDS
    ):
        _fail()
    configurations = {
        vendor: _consult_configuration(state_root, vendor, workflow) for vendor in vendors
    }
    if set(acknowledged_route_sha256) != set(vendors) or any(
        acknowledged_route_sha256.get(vendor) != configurations[vendor].route_sha256
        for vendor in vendors
    ):
        raise ManagedPreflightError("managed_consult_route_mismatch")
    _require_consult_capabilities(configurations, role=role)
    custom_vendors = tuple(
        vendor for vendor, configuration in configurations.items() if configuration.custom
    )
    if custom_vendors:
        if not confirm_provider_egress:
            raise ProviderConfirmationRequiredError
        readiness = provider_check(
            state_root,
            vendors=custom_vendors,
            workflow=workflow,
            confirm_provider_egress=True,
            require_campaign=False,
            expected_route_sha256=acknowledged_route_sha256,
            required_roles=(role,),
        )
        if readiness.get("ready") is not True:
            raise ProviderConformanceError
    _preflight_task_file(task_file)
    verifier = state_root / "verify-project.py"
    _private_regular(verifier, executable=True)
    _preflight_repo(repo, workflow, verifier)
    jobs = tuple(
        _consult_job(
            vendor,
            workflow,
            role,
            repo,
            task_file,
            configurations[vendor].paths.profile,
            configurations[vendor].route_sha256,
            verifier,
            float(timeout_seconds),
        )
        for vendor in vendors
    )
    outcomes: dict[str, int] = {}
    runner_version_changed = False

    def handle_result(result: advisory_parallel.AdvisoryResult) -> None:
        nonlocal runner_version_changed
        vendor = result.label
        if vendor in outcomes or vendor not in configurations:
            raise ValueError
        diagnostic = _consult_child_diagnostic(result.stderr)
        if result.returncode == RUNNER_VERSION_CHANGED_EXIT:
            outcomes[vendor] = 1
            runner_version_changed = True
            return
        if result.returncode != 0:
            _replay_output(
                sys.stderr.buffer,
                _consult_failure_receipt(vendor, workflow, result, diagnostic),
            )
            outcomes[vendor] = 1
            return
        try:
            rendered = result.stdout.decode("utf-8", errors="strict")
            parsed = advisory_evidence_contract.parse_evidence_result(rendered, workflow)
        except (UnicodeError, advisory_evidence_contract.EvidenceResultError):
            result_diagnostic = diagnostic or {
                "failure_stage": "result",
                "reason_code": "route_result_rejected",
                "child_exit_code": None,
                "child_timed_out": False,
                "child_failure_code": "result_contract",
                "result_shape": "unknown",
                "verify_exit_code": None,
                "verify_timed_out": False,
            }
            _replay_output(
                sys.stderr.buffer,
                _consult_failure_receipt(vendor, workflow, result, result_diagnostic),
            )
            outcomes[vendor] = 1
            return
        _replay_output(sys.stdout.buffer, _consult_result_receipt(vendor, workflow, parsed))
        outcomes[vendor] = 0

    results = advisory_parallel.run_parallel(
        jobs,
        progress=lambda vendor, event, elapsed: _replay_output(
            sys.stderr.buffer,
            _dispatch_progress_receipt(workflow, vendor, event, elapsed),
        ),
        result_callback=handle_result,
    )
    for result in results:
        if result.label not in outcomes:
            handle_result(result)
    if runner_version_changed:
        raise RunnerVersionChangedError
    return 0 if outcomes and all(value == 0 for value in outcomes.values()) else 1


def dispatch(
    state_root: Path,
    *,
    repo: Path,
    task_file: Path,
    vendors: Sequence[str],
    workflow: str,
    confirm_task_egress: bool,
    confirm_provider_egress: bool = False,
) -> int:
    if not confirm_task_egress:
        _fail()
    if not vendors or workflow not in WORKFLOWS:
        _fail()
    configurations = {vendor: _configuration(state_root, vendor, workflow) for vendor in vendors}
    _require_route_capabilities(configurations)
    custom_vendors = tuple(
        vendor
        for vendor, (paths, _, _) in configurations.items()
        if _profile_from_path(paths.profile).get("schema_version") == 2
    )
    if custom_vendors:
        if not confirm_provider_egress:
            raise ProviderConfirmationRequiredError
        readiness = provider_check(
            state_root,
            vendors=custom_vendors,
            workflow=workflow,
            confirm_provider_egress=True,
            routes_by_vendor={vendor: configurations[vendor][2] for vendor in custom_vendors},
        )
        if readiness.get("ready") is not True:
            raise ProviderConformanceError
    _preflight_task_file(task_file)
    verifier = state_root / "verify-project.py"
    _private_regular(verifier, executable=True)
    _preflight_repo(repo, workflow, verifier)
    requests = tuple(
        advisory_orchestration.LaneRequest(
            vendor,
            configurations[vendor][0].results,
            workflow=workflow,
            campaign_path=configurations[vendor][0].campaign,
        )
        for vendor in vendors
    )
    try:
        with advisory_orchestration.acquire_campaign_lanes(requests) as leases:
            ordinals = {
                vendor: _next_ordinal(configurations[vendor][1], lease.results_dir)
                for vendor, lease in zip(vendors, leases, strict=True)
            }
            jobs = tuple(
                _job(
                    vendor,
                    workflow,
                    repo,
                    task_file,
                    lease.results_dir,
                    configurations[vendor][0],
                    configurations[vendor][1],
                    ordinals[vendor],
                    verifier,
                )
                for vendor, lease in zip(vendors, leases, strict=True)
            )
            _replay_output(sys.stderr.buffer, _dispatch_started_receipt(workflow, leases))
            results = advisory_parallel.run_parallel(
                jobs,
                progress=lambda vendor, event, elapsed: _replay_output(
                    sys.stderr.buffer,
                    _dispatch_progress_receipt(workflow, vendor, event, elapsed),
                ),
            )
            returncode = 0
            record_error = False
            runner_version_changed = False
            for vendor, lease, result in zip(vendors, leases, results, strict=True):
                if result.stdout:
                    _replay_output(sys.stdout.buffer, result.stdout)
                if result.stderr:
                    _replay_output(sys.stderr.buffer, result.stderr)
                if result.output_truncated:
                    _replay_output(sys.stderr.buffer, b"advisory output limit exceeded\n")
                if result.returncode == RUNNER_VERSION_CHANGED_EXIT:
                    runner_version_changed = True
                if (
                    _next_ordinal(configurations[vendor][1], lease.results_dir)
                    != ordinals[vendor] + 1
                ):
                    record_error = True
                if result.returncode != 0 and returncode == 0:
                    returncode = result.returncode if result.returncode > 0 else 1
            if runner_version_changed:
                raise RunnerVersionChangedError
            if record_error:
                _fail()
            return returncode
    except (
        advisory_orchestration.LaneUnavailableError,
        advisory_orchestration.CampaignCapacityError,
        advisory_orchestration.CampaignRecordsInvalidError,
        advisory_orchestration.AllocatorUnavailableError,
    ):
        raise
    except (OSError, ValueError, advisory_campaign.CampaignError) as error:
        raise ManagedAdvisoryError() from error


def _provider_check_environment() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _provider_executable_group(executable: str) -> tuple[str, int, int] | tuple[str, str]:
    """Group aliases of the same observed executable without exposing its path."""

    resolved = shutil.which(executable)
    if resolved is None:
        return ("command", executable)
    try:
        metadata = os.stat(resolved, follow_symlinks=True)
    except OSError:
        return ("command", executable)
    return ("file", metadata.st_dev, metadata.st_ino)


def _provider_probe_failure(probe: _ProviderProbe) -> dict[str, object]:
    return {
        "vendor": probe.vendor,
        "role": probe.role,
        "ready": False,
        "child_exit_code": None,
        "child_timed_out": False,
        "child_seconds": 0.0,
        "child_failure_code": "local_probe_failed",
        "child_stdout_present": False,
        "child_stderr_present": False,
        "result_shape": "unknown",
        "envelope_extracted": False,
    }


def _run_provider_probe_inner(
    probe: _ProviderProbe, prompt: str, target_workflow: str
) -> dict[str, object]:
    """Run one task-free probe in a private workspace and retain no output."""
    with tempfile.TemporaryDirectory(prefix="wclass-provider-check-") as directory:
        workspace = Path(directory)
        try:
            initialized = subprocess.run(
                ["git", "init", "-q"],
                cwd=workspace,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=30,
                env=_provider_check_environment(),
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ManagedAdvisoryError() from error
        if initialized.returncode != 0:
            _fail()
        child: speculative_run.ChildResult | None = None
        stdout = ""
        try:
            command = list(probe.command)
            child, stdout = speculative_run.run_child(
                command,
                workspace,
                prompt,
                allowed_env=speculative_run.default_child_env(command[0]),
                timeout_seconds=120.0,
            )
            shape = speculative_run.evidence_result_shape(stdout, command)
            _, parsed = speculative_run.extract_evidence_result(stdout, command, target_workflow)
            contract_ready = isinstance(parsed, dict)
        except advisory_evidence_contract.EvidenceResultError:
            shape = speculative_run.evidence_result_shape(stdout, list(probe.command))
            contract_ready = False
        except (OSError, ValueError, speculative_run.RunFailure):
            child = None
            shape = "unknown"
            contract_ready = False
        ready = bool(
            child is not None
            and child["exit_code"] == 0
            and not child["timed_out"]
            and contract_ready
        )
        failure_code = (
            (
                child["failure_code"]
                if child["exit_code"] != 0 or contract_ready
                else "result_contract"
            )
            if child is not None
            else "local_probe_failed"
        )
        if failure_code not in PROVIDER_CHECK_FAILURE_CODES:
            failure_code = "unknown"
        return {
            "vendor": probe.vendor,
            "role": probe.role,
            "ready": ready,
            "child_exit_code": child["exit_code"] if child is not None else None,
            "child_timed_out": child["timed_out"] if child is not None else False,
            "child_seconds": child["seconds"] if child is not None else 0.0,
            "child_failure_code": failure_code,
            "child_stdout_present": child["stdout_present"] if child is not None else False,
            "child_stderr_present": child["stderr_present"] if child is not None else False,
            "result_shape": shape,
            "envelope_extracted": contract_ready,
        }


def _run_provider_probe(
    probe: _ProviderProbe, prompt: str, target_workflow: str
) -> dict[str, object]:
    """Translate local setup failures so every confirmed probe gets one receipt."""

    try:
        return _run_provider_probe_inner(probe, prompt, target_workflow)
    except (OSError, ManagedAdvisoryError):
        return _provider_probe_failure(probe)


def _run_provider_probe_group(
    probes: Sequence[_ProviderProbe], prompt: str, target_workflow: str
) -> tuple[tuple[_ProviderProbe, dict[str, object]], ...]:
    """Run one executable group serially to avoid provider-local races."""
    return tuple((probe, _run_provider_probe(probe, prompt, target_workflow)) for probe in probes)


def _run_provider_probe_groups(
    probes: Sequence[_ProviderProbe], prompt: str, target_workflow: str
) -> tuple[dict[str, object], ...]:
    """Run at most four independent executable groups and restore input order."""
    groups: dict[tuple[str, int, int] | tuple[str, str], list[_ProviderProbe]] = {}
    for probe in probes:
        groups.setdefault(probe.executable_group, []).append(probe)
    if len(groups) <= 1:
        grouped_results = tuple(
            _run_provider_probe_group(group, prompt, target_workflow) for group in groups.values()
        )
    else:
        with ThreadPoolExecutor(
            max_workers=min(PROVIDER_CHECK_MAX_EXECUTABLE_GROUPS, len(groups)),
            thread_name_prefix="wclass-provider-check",
        ) as executor:
            futures = tuple(
                executor.submit(_run_provider_probe_group, group, prompt, target_workflow)
                for group in groups.values()
            )
            # Reading futures in submission order makes exception propagation and
            # deterministic result reconstruction independent of completion order.
            grouped_results = tuple(future.result() for future in futures)
    by_probe = {
        (probe.vendor, probe.role): result
        for grouped in grouped_results
        for probe, result in grouped
    }
    return tuple(by_probe[(probe.vendor, probe.role)] for probe in probes)


def provider_check(
    state_root: Path,
    *,
    vendors: Sequence[str],
    workflow: str,
    confirm_provider_egress: bool,
    require_campaign: bool = True,
    expected_route_sha256: Mapping[str, str] | None = None,
    routes_by_vendor: Mapping[str, advisory_routes.AdvisoryRoutes] | None = None,
    required_roles: Sequence[str] | None = None,
) -> dict[str, object]:
    """Run task-free, non-persisted checks for all configured provider roles."""

    if (
        not confirm_provider_egress
        or not vendors
        or len(set(vendors)) != len(vendors)
        or workflow not in WORKFLOWS
    ):
        _fail()
    selected_roles = _validated_required_roles(required_roles)
    if routes_by_vendor is not None and (
        set(routes_by_vendor) != set(vendors) or expected_route_sha256 is not None
    ):
        _fail()
    target_workflow = "review" if workflow == "implementation" else workflow
    expected = _baseline_probe(target_workflow)
    assert expected is not None
    prompt = (
        "This is a task-free provider readiness check. Do not inspect files or use tools. "
        "Return exactly this JSON object and no other text:\n" + expected.decode("utf-8")
    )
    probes: list[_ProviderProbe] = []
    for vendor in vendors:
        if routes_by_vendor is not None:
            routes = routes_by_vendor[vendor]
            if not isinstance(routes, advisory_routes.AdvisoryRoutes):
                _fail()
        elif require_campaign:
            selected, _, _ = _configuration(state_root, vendor, workflow)
            routes = advisory_routes.routes_from_profile(
                selected.profile,
                read_only_executors=True,
                evidence_workflow=target_workflow,
            )
        else:
            configuration = _consult_configuration(state_root, vendor, target_workflow)
            if (
                expected_route_sha256 is None
                or expected_route_sha256.get(vendor) != configuration.route_sha256
            ):
                raise ManagedPreflightError("managed_consult_route_mismatch")
            routes = configuration.routes
        capabilities = _route_capabilities(vendor, routes, required_roles=selected_roles)
        for role, capability in capabilities:
            if not capability.ready:
                raise ProviderCapabilityError(vendor, role, capability.failure_code)
            command = tuple(getattr(routes, role))
            probes.append(
                _ProviderProbe(vendor, role, command, _provider_executable_group(command[0]))
            )
    results = list(_run_provider_probe_groups(probes, prompt, target_workflow))
    return {
        "schema_version": 1,
        "event": "managed_provider_check",
        "task_free": True,
        "network_used": True,
        "provider_egress_confirmed": True,
        "sample_recorded": False,
        "calls": len(results),
        "ready": all(bool(result["ready"]) for result in results),
        "workflow": workflow,
        "results": results,
    }


CAMPAIGN_GATE_METRICS = tuple(sorted(advisory_campaign.CAMPAIGN_GATE_METRICS))


def _gate_attempt_accepted(value: object, *, optional: bool = False) -> bool:
    if value is None and optional:
        return False
    if not isinstance(value, Mapping) or not isinstance(value.get("accepted"), bool):
        _fail()
    return value["accepted"] is True


def _campaign_gate_outcomes(
    records: Sequence[Mapping[str, object]], metric: str
) -> list[dict[str, object]]:
    return _campaign_gate_population(records, metric)[0]


def _campaign_gate_population(
    records: Sequence[Mapping[str, object]], metric: str
) -> tuple[list[dict[str, object]], int, int]:
    """Return outcomes plus usable and infrastructure-excluded denominators."""
    outcomes: list[dict[str, object]] = []
    usable_records = 0
    excluded_infrastructure = 0
    for record in records:
        cheap = record.get("cheap")
        if not isinstance(cheap, Mapping):
            _fail()
        if cheap.get("failure_kind") == "infrastructure":
            excluded_infrastructure += 1
            continue
        usable_records += 1
        cheap_accepted = _gate_attempt_accepted(cheap)
        if metric == "cheap_acceptance":
            accepted = cheap_accepted
        elif metric == "advised_rescue":
            if not isinstance(record.get("advice_failure"), Mapping):
                continue
            accepted = _gate_attempt_accepted(record.get("retry"), optional=True)
        elif metric == "final_acceptance":
            retry_accepted = _gate_attempt_accepted(record.get("retry"), optional=True)
            expensive_accepted = _gate_attempt_accepted(record.get("expensive"), optional=True)
            accepted = cheap_accepted or retry_accepted or expensive_accepted
        else:
            _fail()
        outcomes.append({"schema_version": 1, "experiment": "sequential", "accepted": accepted})
    return outcomes, usable_records, excluded_infrastructure


def campaign_gate(
    state_root: Path,
    *,
    vendor: str,
    workflow: str,
    metric: str | None,
    target_rate_bps: int | None,
    alpha_bps: int | None,
) -> dict[str, object]:
    selected, manifest, _ = _configuration(state_root, vendor, workflow, validate_records=False)
    sealed_gate = manifest.get("gate")
    gate_preregistered = (
        manifest.get("schema_version") == advisory_campaign.PREREGISTERED_CAMPAIGN_SCHEMA_VERSION
    )
    if gate_preregistered:
        if not isinstance(sealed_gate, Mapping):
            _fail()
        try:
            normalized_gate = advisory_campaign.validate_gate(sealed_gate)
        except advisory_campaign.CampaignError:
            _fail()
        if (
            (metric is not None and metric != normalized_gate["metric"])
            or (
                target_rate_bps is not None
                and target_rate_bps != normalized_gate["target_rate_bps"]
            )
            or (alpha_bps is not None and alpha_bps != normalized_gate["alpha_bps"])
        ):
            raise ManagedPreflightError("managed_campaign_gate_override_mismatch")
        metric = normalized_gate["metric"]
        target_rate_bps = normalized_gate["target_rate_bps"]
        alpha_bps = normalized_gate["alpha_bps"]
    else:
        metric = metric or "cheap_acceptance"
        target_rate_bps = (
            LEGACY_GATE_TARGET_RATE_BPS if target_rate_bps is None else target_rate_bps
        )
        alpha_bps = LEGACY_GATE_ALPHA_BPS if alpha_bps is None else alpha_bps
    if metric not in CAMPAIGN_GATE_METRICS:
        _fail()
    if (
        isinstance(target_rate_bps, bool)
        or not isinstance(target_rate_bps, int)
        or not 0 <= target_rate_bps <= 10_000
        or isinstance(alpha_bps, bool)
        or not isinstance(alpha_bps, int)
        or not 1 <= alpha_bps <= 5_000
    ):
        _fail()
    try:
        records = advisory_campaign.load_merged_lane_records(manifest, selected.results)
    except advisory_campaign.CampaignError as error:
        raise advisory_orchestration.CampaignRecordsInvalidError(error) from None
    try:
        progress = advisory_campaign.campaign_progress(manifest, records)
        outcomes, usable_records, excluded_infrastructure = _campaign_gate_population(
            records, metric
        )
        minimum_samples = (
            manifest["minimum_advised_failures"]
            if metric == "advised_rescue"
            else manifest["planned_tasks"]
        )
        analysis = advisory_experiments.analyze_sequential(
            outcomes,
            target_rate_bps=target_rate_bps,
            alpha_bps=alpha_bps,
            minimum_samples=minimum_samples,
            maximum_samples=manifest["max_tasks"],
        )
    except (
        advisory_experiments.ExperimentInputError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise ManagedAdvisoryError() from error
    signal = analysis["decision"]
    if not progress.decision_eligible:
        decision = "abstain"
        reason = progress.reason
    elif signal == "signal_above_target":
        decision = "eligible_for_human_review"
        reason = "statistical_target_met"
    elif signal == "signal_below_target":
        decision = "reject"
        reason = "statistical_target_missed"
    else:
        decision = "abstain"
        reason = "statistical_evidence_incomplete"
    return {
        **analysis,
        "analysis": "sealed_campaign_gate",
        "vendor": vendor,
        "workflow": workflow,
        "metric": metric,
        "gate_preregistered": gate_preregistered,
        "metric_samples": len(outcomes),
        "usable_samples": usable_records,
        "excluded_infrastructure": excluded_infrastructure,
        "decision": decision,
        "decision_reason": reason,
        "statistical_signal": signal,
        "evidence_origin": "sealed_campaign",
        "campaign_bound": True,
        "campaign_minimums_met": progress.decision_eligible,
        "promotion_eligible": gate_preregistered and decision == "eligible_for_human_review",
        "policy_decision_allowed": False,
        "core_routing_changed": False,
    }


def review_payload(
    state_root: Path,
    *,
    vendors: Sequence[str],
    workflow: str,
    require_campaign: bool = True,
) -> dict[str, object]:
    reviewed: list[dict[str, object]] = []
    for vendor in vendors:
        configuration: ConsultConfiguration | None = None
        custom = False
        if require_campaign:
            paths, _, routes = _configuration(state_root, vendor, workflow)
            custom = _profile_from_path(paths.profile).get("schema_version") == 2
        else:
            configuration = _consult_configuration(state_root, vendor, workflow)
            routes = configuration.routes
            custom = configuration.custom
        deliveries = {
            role: advisory_routes.command_task_delivery(getattr(routes, role)) for role in ROLES
        }
        distinct = set(deliveries.values())
        review: dict[str, object] = {
            "vendor": vendor,
            "workflow": workflow,
            "executor_access": ("workspace_write" if workflow == "implementation" else "read_only"),
            "task_delivery": next(iter(distinct)) if len(distinct) == 1 else deliveries,
            "task_process_exposure": "argv" in distinct,
            "task_egress": True,
            "provider_contract": (
                "custom_exact_argv_unverified_containment" if custom else "built_in_reviewed"
            ),
            "host_filesystem_isolated": False,
            "repository_write_enforcement": (
                "post_run_check_only" if custom else "vendor_read_only_flags_plus_post_run_check"
            ),
        }
        if configuration is not None:
            review["profile_sha256"] = configuration.profile_sha256
            review["route_sha256"] = configuration.route_sha256
            review["routes"] = {role: list(getattr(routes, role)) for role in ROLES}
        reviewed.append(review)
    payload: dict[str, object] = {"schema_version": SCHEMA_VERSION, "routes": reviewed}
    if not require_campaign:
        payload["campaign_bound"] = False
    return payload


def _role_values(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        role, separator, selected = value.partition("=")
        if separator != "=" or role not in ROLES or not selected or role in result:
            _fail()
        result[role] = selected
    if set(result) != set(ROLES):
        _fail()
    return result


def _gate_arguments(
    metric: str | None,
    target_rate_bps: int | None,
    alpha_bps: int | None,
) -> dict[str, object] | None:
    """Require all gate settings together; partial registration is unsafe."""
    values = (metric, target_rate_bps, alpha_bps)
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        _fail()
    if metric not in advisory_campaign.CAMPAIGN_GATE_METRICS:
        _fail()
    if (
        isinstance(target_rate_bps, bool)
        or not isinstance(target_rate_bps, int)
        or not 0 <= target_rate_bps <= 10_000
        or isinstance(alpha_bps, bool)
        or not isinstance(alpha_bps, int)
        or not 1 <= alpha_bps <= 5_000
    ):
        _fail()
    return {
        "metric": metric,
        "target_rate_bps": target_rate_bps,
        "alpha_bps": alpha_bps,
    }


def _root(value: Path | None) -> Path:
    selected = default_state_root() if value is None else value.expanduser()
    if not selected.is_absolute():
        _fail()
    return selected


def init_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="wclass-advisory init", allow_abbrev=False)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--vendor", choices=BUILTIN_VENDORS)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument("--effort", action="append", default=[])
    parser.add_argument("--prices", type=Path)
    parser.add_argument("--planned-tasks", type=int, default=60)
    parser.add_argument("--max-tasks", type=int, default=150)
    parser.add_argument(
        "--gate-metric",
        choices=tuple(sorted(advisory_campaign.CAMPAIGN_GATE_METRICS)),
    )
    parser.add_argument("--gate-target-rate-bps", type=int)
    parser.add_argument("--gate-alpha-bps", type=int)
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        gate = _gate_arguments(
            arguments.gate_metric,
            arguments.gate_target_rate_bps,
            arguments.gate_alpha_bps,
        )
        if arguments.profile is not None:
            if arguments.vendor is not None or arguments.model or arguments.effort:
                _fail()
            profile = _profile_from_path(arguments.profile.expanduser())
        else:
            if arguments.vendor is None:
                _fail()
            profile = {
                "schema_version": 1,
                "vendor": arguments.vendor,
                "models": _role_values(arguments.model),
                "efforts": _role_values(arguments.effort),
            }
        receipt = initialize_campaign_set(
            _root(arguments.state_root),
            profile=profile,
            prices=arguments.prices.expanduser() if arguments.prices is not None else None,
            planned_tasks=arguments.planned_tasks,
            max_tasks=arguments.max_tasks,
            dry_run=arguments.dry_run,
            gate=gate,
        )
    except SetupUnavailableError:
        print(json.dumps({"error": "managed_setup_busy"}), file=sys.stderr)
        return 2
    except (OSError, ManagedAdvisoryError):
        print(json.dumps({"error": "managed_configuration_invalid"}), file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


def migrate_evidence_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="wclass-advisory migrate-evidence", allow_abbrev=False)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--vendor", required=True, choices=("claude", "grok"))
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        receipt = migrate_evidence_campaigns(
            _root(arguments.state_root),
            vendor=arguments.vendor,
            dry_run=arguments.dry_run,
        )
    except SetupUnavailableError:
        print(json.dumps({"error": "managed_setup_busy"}), file=sys.stderr)
        return 2
    except (OSError, ManagedAdvisoryError, advisory_campaign.CampaignError):
        print(json.dumps({"error": "managed_evidence_migration_rejected"}), file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


def migrate_routes_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="wclass-advisory migrate-routes", allow_abbrev=False)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--vendor", required=True, choices=("agy",))
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        receipt = migrate_vendor_campaigns(
            _root(arguments.state_root),
            vendor=arguments.vendor,
            dry_run=arguments.dry_run,
        )
    except SetupUnavailableError:
        print(json.dumps({"error": "managed_setup_busy"}), file=sys.stderr)
        return 2
    except (OSError, ManagedAdvisoryError, advisory_campaign.CampaignError):
        print(json.dumps({"error": "managed_route_migration_rejected"}), file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


def migrate_gate_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="wclass-advisory migrate-gate", allow_abbrev=False)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--vendor", required=True)
    parser.add_argument(
        "--gate-metric",
        required=True,
        choices=tuple(sorted(advisory_campaign.CAMPAIGN_GATE_METRICS)),
    )
    parser.add_argument("--gate-target-rate-bps", required=True, type=int)
    parser.add_argument("--gate-alpha-bps", required=True, type=int)
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        gate = _gate_arguments(
            arguments.gate_metric,
            arguments.gate_target_rate_bps,
            arguments.gate_alpha_bps,
        )
        assert gate is not None
        receipt = migrate_gate_campaigns(
            _root(arguments.state_root),
            vendor=arguments.vendor,
            gate=gate,
            dry_run=arguments.dry_run,
        )
    except SetupUnavailableError:
        print(json.dumps({"error": "managed_setup_busy"}), file=sys.stderr)
        return 2
    except (OSError, ManagedAdvisoryError, advisory_campaign.CampaignError):
        print(json.dumps({"error": "managed_gate_migration_rejected"}), file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


def doctor_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="wclass-advisory doctor", allow_abbrev=False)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--vendor", default="all")
    parser.add_argument("--workflow", choices=("all", *WORKFLOWS), default="all")
    arguments = parser.parse_args(argv)
    try:
        root = _root(arguments.state_root)
        vendors = _selected_vendors(root, arguments.vendor)
        receipt = doctor(
            root,
            vendors=vendors,
            workflows=_selected_workflows(arguments.workflow),
        )
    except advisory_orchestration.CampaignRecordsInvalidError as error:
        print(json.dumps({"error": error.code}), file=sys.stderr)
        return 2
    except advisory_orchestration.AllocatorUnavailableError:
        print(json.dumps({"error": "managed_allocator_busy"}), file=sys.stderr)
        return 2
    except (OSError, ValueError, ManagedAdvisoryError):
        print(json.dumps({"error": "managed_configuration_unavailable"}), file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


def cli_check_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="wclass-advisory cli-check", allow_abbrev=False)
    parser.add_argument("--vendor", choices=("all", *BUILTIN_VENDORS), default="all")
    arguments = parser.parse_args(argv)
    vendors = BUILTIN_VENDORS if arguments.vendor == "all" else (arguments.vendor,)
    results = [advisory_preflight.check_local_capability(vendor, vendor) for vendor in vendors]
    payload = {
        "schema_version": 1,
        "event": "advisory_cli_check",
        "task_free": True,
        "task_bytes_sent": False,
        "provider_request_sent": False,
        "environment_policy": "minimal",
        "ready": all(result.ready for result in results),
        "results": [result.receipt() for result in results],
    }
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if payload["ready"] is True else 1


def _provider_capability_payload(error: ProviderCapabilityError) -> dict[str, object]:
    return {
        "error": "managed_provider_preflight_failed",
        "vendor": error.vendor,
        "role": error.role,
        "child_failure_code": error.code,
        "sample_recorded": False,
    }


def provider_check_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="wclass-advisory provider-check", allow_abbrev=False)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--vendor", default="all")
    parser.add_argument("--workflow", choices=WORKFLOWS, default="review")
    parser.add_argument(
        "--confirm-provider-egress",
        action="store_true",
        required=True,
        help="allow three task-free provider calls that may use quota or incur cost",
    )
    arguments = parser.parse_args(argv)
    try:
        root = _root(arguments.state_root)
        receipt = provider_check(
            root,
            vendors=_selected_vendors(root, arguments.vendor),
            workflow=arguments.workflow,
            confirm_provider_egress=arguments.confirm_provider_egress,
        )
    except ProviderCapabilityError as error:
        print(json.dumps(_provider_capability_payload(error), sort_keys=True), file=sys.stderr)
        return 2
    except (OSError, ValueError, ManagedAdvisoryError):
        print(json.dumps({"error": "managed_provider_check_rejected"}), file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0 if receipt["ready"] is True else 1


def review_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="wclass-advisory review",
        epilog=(
            "Advanced explicit-profile review remains available as: "
            "wclass-advisory review --profile PROFILE [--read-only-executors]"
        ),
        allow_abbrev=False,
    )
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--vendor", default="all")
    parser.add_argument("--workflow", choices=WORKFLOWS, default="implementation")
    parser.add_argument(
        "--consult",
        action="store_true",
        help="review a non-recording evidence route without validating campaign records",
    )
    arguments = parser.parse_args(argv)
    try:
        if arguments.consult and arguments.workflow not in EVIDENCE_WORKFLOWS:
            _fail()
        root = _root(arguments.state_root)
        payload = review_payload(
            root,
            vendors=_selected_vendors(root, arguments.vendor),
            workflow=arguments.workflow,
            require_campaign=not arguments.consult,
        )
    except (OSError, ValueError, ManagedAdvisoryError):
        print(json.dumps({"error": "managed_configuration_unavailable"}), file=sys.stderr)
        return 2
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


def consult_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="wclass-advisory consult", allow_abbrev=False)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--task-file", required=True, type=Path)
    parser.add_argument("--vendor", default="all")
    parser.add_argument("--workflow", required=True, choices=EVIDENCE_WORKFLOWS)
    parser.add_argument("--role", choices=("cheap", "expensive"), default="cheap")
    parser.add_argument(
        "--ack-route-sha256",
        action="append",
        required=True,
        help="repeat VENDOR=sha256:... for every selected reviewed consult route",
    )
    parser.add_argument("--confirm-task-egress", action="store_true", required=True)
    parser.add_argument("--confirm-provider-egress", action="store_true")
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=CONSULT_DEFAULT_TIMEOUT_SECONDS,
        help="per-vendor outer deadline from 1 through 28800 seconds",
    )
    arguments = parser.parse_args(argv)
    try:
        root = _root(arguments.state_root)
        vendors = _selected_vendors(root, arguments.vendor)
        return consult(
            root,
            repo=arguments.repo.expanduser().resolve(),
            task_file=arguments.task_file.expanduser(),
            vendors=vendors,
            workflow=arguments.workflow,
            role=arguments.role,
            acknowledged_route_sha256=_consult_route_acknowledgements(
                arguments.ack_route_sha256, vendors
            ),
            confirm_task_egress=arguments.confirm_task_egress,
            confirm_provider_egress=arguments.confirm_provider_egress,
            timeout_seconds=arguments.timeout_seconds,
        )
    except ProviderConfirmationRequiredError:
        print(json.dumps({"error": "managed_provider_confirmation_required"}), file=sys.stderr)
        return 2
    except ProviderConformanceError:
        print(json.dumps({"error": "managed_provider_preflight_failed"}), file=sys.stderr)
        return 2
    except ProviderCapabilityError as error:
        print(json.dumps(_provider_capability_payload(error), sort_keys=True), file=sys.stderr)
        return 2
    except RunnerVersionChangedError:
        print(json.dumps({"error": "managed_runner_version_changed"}), file=sys.stderr)
        return 2
    except ManagedPreflightError as error:
        print(
            json.dumps(
                {
                    "error": "managed_consult_rejected",
                    "reason_code": error.code,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except (OSError, ValueError, ManagedAdvisoryError):
        print(json.dumps({"error": "managed_consult_rejected"}), file=sys.stderr)
        return 2


def dispatch_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="wclass-advisory dispatch", allow_abbrev=False)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--task-file", required=True, type=Path)
    parser.add_argument("--vendor", default="all")
    parser.add_argument("--workflow", choices=WORKFLOWS, default="implementation")
    parser.add_argument("--confirm-task-egress", action="store_true", required=True)
    parser.add_argument("--confirm-provider-egress", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        root = _root(arguments.state_root)
        return dispatch(
            root,
            repo=arguments.repo.expanduser().resolve(),
            task_file=arguments.task_file.expanduser(),
            vendors=_selected_vendors(root, arguments.vendor),
            workflow=arguments.workflow,
            confirm_task_egress=arguments.confirm_task_egress,
            confirm_provider_egress=arguments.confirm_provider_egress,
        )
    except ProviderConfirmationRequiredError:
        print(json.dumps({"error": "managed_provider_confirmation_required"}), file=sys.stderr)
        return 2
    except ProviderConformanceError:
        print(json.dumps({"error": "managed_provider_preflight_failed"}), file=sys.stderr)
        return 2
    except ProviderCapabilityError as error:
        print(json.dumps(_provider_capability_payload(error), sort_keys=True), file=sys.stderr)
        return 2
    except advisory_orchestration.LaneUnavailableError:
        print(json.dumps({"error": "managed_lane_unavailable"}), file=sys.stderr)
        return 2
    except advisory_orchestration.CampaignCapacityError:
        print(json.dumps({"error": "managed_campaign_capacity_reached"}), file=sys.stderr)
        return 2
    except advisory_orchestration.CampaignRecordsInvalidError as error:
        print(json.dumps({"error": error.code}), file=sys.stderr)
        return 2
    except advisory_orchestration.AllocatorUnavailableError:
        print(json.dumps({"error": "managed_allocator_busy"}), file=sys.stderr)
        return 2
    except RunnerVersionChangedError:
        print(json.dumps({"error": "managed_runner_version_changed"}), file=sys.stderr)
        return 2
    except ManagedPreflightError as error:
        print(
            json.dumps(
                {
                    "error": "managed_dispatch_rejected",
                    "reason_code": error.code,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except (OSError, ManagedAdvisoryError):
        print(json.dumps({"error": "managed_dispatch_rejected"}), file=sys.stderr)
        return 2


def campaign_gate_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="wclass-advisory campaign-gate", allow_abbrev=False)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--vendor", required=True)
    parser.add_argument("--workflow", required=True, choices=WORKFLOWS)
    parser.add_argument("--metric", choices=CAMPAIGN_GATE_METRICS)
    parser.add_argument("--target-rate-bps", type=int)
    parser.add_argument("--alpha-bps", type=int)
    arguments = parser.parse_args(argv)
    try:
        if arguments.target_rate_bps is not None and not 0 <= arguments.target_rate_bps <= 10_000:
            _fail()
        if arguments.alpha_bps is not None and not 1 <= arguments.alpha_bps <= 5_000:
            _fail()
        root = _root(arguments.state_root)
        vendors = _selected_vendors(root, arguments.vendor)
        if len(vendors) != 1:
            _fail()
        receipt = campaign_gate(
            root,
            vendor=vendors[0],
            workflow=arguments.workflow,
            metric=arguments.metric,
            target_rate_bps=arguments.target_rate_bps,
            alpha_bps=arguments.alpha_bps,
        )
    except advisory_orchestration.CampaignRecordsInvalidError as error:
        print(json.dumps({"error": error.code}), file=sys.stderr)
        return 2
    except ManagedPreflightError as error:
        print(json.dumps({"error": error.code}), file=sys.stderr)
        return 2
    except (OSError, ValueError, ManagedAdvisoryError):
        print(json.dumps({"error": "managed_campaign_gate_rejected"}), file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


def status_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="wclass-advisory status", allow_abbrev=False)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--vendor", default="all")
    parser.add_argument("--workflow", choices=("all", *WORKFLOWS), default="all")
    arguments = parser.parse_args(argv)
    try:
        root = _root(arguments.state_root)
        vendors = _selected_vendors(root, arguments.vendor)
        workflows = _selected_workflows(arguments.workflow)
    except (OSError, ManagedAdvisoryError):
        print(json.dumps({"error": "managed_configuration_unavailable"}), file=sys.stderr)
        return 2
    original = sys.argv
    try:
        portfolio_arguments = ["wclass-advisory status"]
        for vendor in vendors:
            for workflow in workflows:
                selected = _active_campaign_paths(root, vendor, workflow)
                portfolio_arguments.extend(
                    (
                        "--campaign",
                        vendor,
                        workflow,
                        str(selected.campaign),
                        str(selected.results),
                    )
                )
        sys.argv = portfolio_arguments
        return advisory_portfolio.main()
    finally:
        sys.argv = original


def prune_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="wclass-advisory cleanup", allow_abbrev=False)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--vendor", default="all")
    parser.add_argument("--workflow", choices=("all", *WORKFLOWS), default="all")
    arguments = parser.parse_args(argv)
    try:
        root = _root(arguments.state_root)
        vendors = _selected_vendors(root, arguments.vendor)
        totals = {
            "populations": 0,
            "lanes_scanned": 0,
            "busy_lanes": 0,
            "registered": 0,
            "removed": 0,
            "retained": 0,
        }
        for vendor in vendors:
            for workflow in _selected_workflows(arguments.workflow):
                selected = _active_campaign_paths(root, vendor, workflow)
                result = speculative_run.prune_available_lanes(selected.results)
                totals["populations"] += 1
                for field in (
                    "lanes_scanned",
                    "busy_lanes",
                    "registered",
                    "removed",
                    "retained",
                ):
                    totals[field] += result[field]
        print(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "event": "managed_cleanup",
                    "complete": totals["busy_lanes"] == 0 and totals["retained"] == 0,
                    **totals,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except (OSError, ManagedAdvisoryError, advisory_campaign.CampaignError):
        print(json.dumps({"error": "managed_cleanup_rejected"}), file=sys.stderr)
        return 2
