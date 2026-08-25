#!/usr/bin/env python3
"""Owner-private onboarding and dispatch for reusable advisory campaigns."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, TypedDict

if TYPE_CHECKING or __package__:
    from . import (
        advisory_campaign,
        advisory_orchestration,
        advisory_parallel,
        advisory_portfolio,
        advisory_routes,
        managed_verify,
        speculative_run,
    )
else:
    import advisory_campaign  # type: ignore[import-not-found]
    import advisory_orchestration  # type: ignore[import-not-found]
    import advisory_parallel  # type: ignore[import-not-found]
    import advisory_portfolio  # type: ignore[import-not-found]
    import advisory_routes  # type: ignore[import-not-found]
    import managed_verify  # type: ignore[import-not-found]
    import speculative_run  # type: ignore[import-not-found]

SCHEMA_VERSION = 1
WORKFLOWS = ("implementation", "review", "research", "diagnosis", "design")
ROLES = ("cheap", "advisor", "expensive")
BUILTIN_VENDORS = ("codex", "claude", "agy", "grok")
EXPECTED_BASELINE_FAILURE = 42
MAX_CONFIGURED_VENDORS = 16
MAX_PROFILE_BYTES = 131_072
_VENDOR = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")


class ManagedAdvisoryError(ValueError):
    """Value-free rejection of unsafe or inconsistent managed configuration."""


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
    vendors: list[str]
    workflows: list[str]


@dataclass(frozen=True)
class CampaignPaths:
    profile: Path
    prices: Path
    campaign: Path
    results: Path


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
    infix = "" if workflow == "implementation" else f"-{workflow}"
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
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
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
) -> advisory_campaign.CampaignManifest:
    try:
        routes = advisory_routes.routes_from_profile(
            profile_path, read_only_executors=workflow != "implementation"
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
) -> bool:
    verifier = state_root / "verify-project.py"
    try:
        _private_regular(verifier, executable=True)
        profile_path = campaign_paths(state_root, vendor, "implementation").profile
        _private_regular(profile_path)
        if _regular_bytes(profile_path, MAX_PROFILE_BYTES) != profile_payload:
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
            selected = campaign_paths(state_root, vendor, workflow)
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
                selected = campaign_paths(state_root, vendor, workflow)
                manifest = _manifest_for(
                    profile_path,
                    workflow=workflow,
                    verifier=verifier,
                    prices=selected_prices,
                    planned_tasks=planned_tasks,
                    max_tasks=max_tasks,
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


def _configuration(
    state_root: Path, vendor: str, workflow: str
) -> tuple[CampaignPaths, advisory_campaign.CampaignManifest, advisory_routes.AdvisoryRoutes]:
    selected = campaign_paths(state_root, vendor, workflow)
    verifier = state_root / "verify-project.py"
    _private_regular(verifier, executable=True)
    _private_regular(selected.profile)
    _private_regular(selected.campaign)
    _private_directory(selected.results, create=False)
    try:
        manifest = advisory_campaign.load_manifest(selected.campaign)
        routes = advisory_routes.routes_from_profile(
            selected.profile, read_only_executors=workflow != "implementation"
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
    return selected, manifest, routes


def doctor(state_root: Path, *, vendors: Sequence[str], workflows: Sequence[str]) -> DoctorReceipt:
    if not vendors or not workflows:
        _fail()
    for vendor in vendors:
        for workflow in workflows:
            _configuration(state_root, vendor, workflow)
    return {
        "schema_version": SCHEMA_VERSION,
        "ready": True,
        "vendors": list(vendors),
        "workflows": list(workflows),
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
        _fail()
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
        if status.returncode != 0 or status.stdout:
            _fail()
        verifier_environment = dict(environment)
        verifier_environment["WCLASS_ADVISORY_WORKFLOW"] = workflow
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
        raise ManagedAdvisoryError() from error
    if completed.returncode != EXPECTED_BASELINE_FAILURE:
        _fail()


def _preflight_task_file(task_file: Path) -> None:
    if not task_file.is_absolute():
        _fail()
    try:
        metadata = task_file.lstat()
    except OSError as error:
        raise ManagedAdvisoryError() from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        _fail()


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
    command = [
        sys.executable,
        "-m",
        "weightclass.advisory.speculative_run",
        "--workflow",
        workflow,
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
        command.insert(command.index("--advise-on-failure"), "--advise-first")
    if manifest["cost_basis"] == "price_table":
        index = command.index("--campaign")
        command[index:index] = ["--prices", str(selected.prices), "--prefer-prices"]
    return advisory_parallel.AdvisoryJob(vendor, tuple(command))


def dispatch(
    state_root: Path,
    *,
    repo: Path,
    task_file: Path,
    vendors: Sequence[str],
    workflow: str,
    confirm_task_egress: bool,
) -> int:
    if not confirm_task_egress:
        _fail()
    if not vendors or workflow not in WORKFLOWS:
        _fail()
    _preflight_task_file(task_file)
    verifier = state_root / "verify-project.py"
    _private_regular(verifier, executable=True)
    _preflight_repo(repo, workflow, verifier)
    configurations = {vendor: _configuration(state_root, vendor, workflow) for vendor in vendors}
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
            results = advisory_parallel.run_parallel(jobs)
            returncode = 0
            record_error = False
            for vendor, lease, result in zip(vendors, leases, results, strict=True):
                if result.stdout:
                    sys.stdout.buffer.write(result.stdout)
                    sys.stdout.buffer.flush()
                if result.stderr:
                    sys.stderr.buffer.write(result.stderr)
                    sys.stderr.buffer.flush()
                if result.output_truncated:
                    print("advisory output limit exceeded", file=sys.stderr)
                if (
                    _next_ordinal(configurations[vendor][1], lease.results_dir)
                    != ordinals[vendor] + 1
                ):
                    record_error = True
                if result.returncode != 0 and returncode == 0:
                    returncode = result.returncode if result.returncode > 0 else 1
            if record_error:
                _fail()
            return returncode
    except (OSError, ValueError, advisory_campaign.CampaignError) as error:
        raise ManagedAdvisoryError() from error


def review_payload(state_root: Path, *, vendors: Sequence[str], workflow: str) -> dict[str, object]:
    reviewed: list[dict[str, object]] = []
    for vendor in vendors:
        _, _, routes = _configuration(state_root, vendor, workflow)
        deliveries = {
            role: advisory_routes.command_task_delivery(getattr(routes, role)) for role in ROLES
        }
        distinct = set(deliveries.values())
        reviewed.append(
            {
                "vendor": vendor,
                "workflow": workflow,
                "executor_access": (
                    "workspace_write" if workflow == "implementation" else "read_only"
                ),
                "task_delivery": next(iter(distinct)) if len(distinct) == 1 else deliveries,
                "task_process_exposure": "argv" in distinct,
                "task_egress": True,
            }
        )
    return {"schema_version": SCHEMA_VERSION, "routes": reviewed}


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
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args(argv)
    try:
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
        )
    except (OSError, ManagedAdvisoryError):
        print(json.dumps({"error": "managed_configuration_invalid"}), file=sys.stderr)
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
    except (OSError, ManagedAdvisoryError):
        print(json.dumps({"error": "managed_configuration_unavailable"}), file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


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
    arguments = parser.parse_args(argv)
    try:
        root = _root(arguments.state_root)
        payload = review_payload(
            root,
            vendors=_selected_vendors(root, arguments.vendor),
            workflow=arguments.workflow,
        )
    except (OSError, ManagedAdvisoryError):
        print(json.dumps({"error": "managed_configuration_unavailable"}), file=sys.stderr)
        return 2
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


def dispatch_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="wclass-advisory dispatch", allow_abbrev=False)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--task-file", required=True, type=Path)
    parser.add_argument("--vendor", default="all")
    parser.add_argument("--workflow", choices=WORKFLOWS, default="implementation")
    parser.add_argument("--confirm-task-egress", action="store_true", required=True)
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
        )
    except (OSError, ManagedAdvisoryError):
        print(json.dumps({"error": "managed_dispatch_rejected"}), file=sys.stderr)
        return 2


def status_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="wclass-advisory status", allow_abbrev=False)
    parser.add_argument("--state-root", type=Path)
    arguments = parser.parse_args(argv)
    try:
        root = _root(arguments.state_root)
        configured_vendors(root)
    except (OSError, ManagedAdvisoryError):
        print(json.dumps({"error": "managed_configuration_unavailable"}), file=sys.stderr)
        return 2
    original = sys.argv
    try:
        sys.argv = ["wclass-advisory status", "--campaign-directory", str(root)]
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
        for vendor in vendors:
            for workflow in _selected_workflows(arguments.workflow):
                selected = campaign_paths(root, vendor, workflow)
                original = sys.argv
                try:
                    sys.argv = [
                        "wclass-advisory cleanup",
                        "--prune",
                        "--out-dir",
                        str(selected.results),
                    ]
                    code = speculative_run.main()
                finally:
                    sys.argv = original
                if code:
                    return code
        return 0
    except (OSError, ManagedAdvisoryError):
        print(json.dumps({"error": "managed_cleanup_rejected"}), file=sys.stderr)
        return 2
