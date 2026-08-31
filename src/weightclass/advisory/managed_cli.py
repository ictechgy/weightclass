#!/usr/bin/env python3
"""Command-line parsers for the explicit managed advisory workflows.

The managed service implementation is intentionally imported only after a
subcommand's arguments have parsed.  In particular, ``status --help`` must be
able to render its help without importing the campaign runners or any vendor
execution code.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

SCHEMA_VERSION = 1
WORKFLOWS = ("implementation", "review", "research", "diagnosis", "design")
EVIDENCE_WORKFLOWS = WORKFLOWS[1:]
BUILTIN_VENDORS = ("codex", "claude", "agy", "grok")
ROLES = ("cheap", "advisor", "expensive")
CAMPAIGN_GATE_METRICS = ("advised_rescue", "cheap_acceptance", "final_acceptance")
CONSULT_DEFAULT_TIMEOUT_SECONDS = 5_400.0


def _load_backend(backend: Any | None) -> Any:
    if backend is not None:
        return backend
    module_name = f"{__package__}.managed_advisory" if __package__ else "managed_advisory"
    return importlib.import_module(module_name)


def _compat_entrypoint(backend: Any, name: str) -> object | None:
    """Honor a caller-patched legacy ``managed_advisory.*_main`` seam."""
    implementation = backend.__dict__.get(name)
    if implementation is None or getattr(implementation, "_managed_cli_wrapper", False):
        return None
    return cast(object, implementation)


def _call(module_main: object, arguments: Sequence[str]) -> int:
    """Call one argv entry point and validate its exit status."""
    if not callable(module_main):
        raise ValueError()
    result = module_main(arguments)
    if isinstance(result, bool) or not isinstance(result, int):
        raise ValueError()
    return result


def _fail(backend: Any) -> None:
    backend._fail()


def _exit_code(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError()
    return value


def _error_code(error: BaseException) -> str:
    return cast(str, vars(error).get("code"))


def _error_type(backend: Any, name: str) -> type[BaseException]:
    value = getattr(backend, name)
    if not isinstance(value, type) or not issubclass(value, BaseException):
        raise TypeError()
    return value


def _campaign_error_type(backend: Any) -> type[BaseException]:
    return _error_type(backend.advisory_campaign, "CampaignError")


def init_main(argv: Sequence[str], *, _backend: Any | None = None) -> int:
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
    backend = _load_backend(_backend)
    compatibility = _compat_entrypoint(backend, "init_main")
    if compatibility is not None:
        return _call(compatibility, argv)
    setup_unavailable = _error_type(backend, "SetupUnavailableError")
    managed_error = _error_type(backend, "ManagedAdvisoryError")
    try:
        if arguments.profile is not None:
            if arguments.vendor is not None or arguments.model or arguments.effort:
                _fail(backend)
            profile = backend._profile_from_path(arguments.profile.expanduser())
        else:
            if arguments.vendor is None:
                _fail(backend)
            profile = {
                "schema_version": 1,
                "vendor": arguments.vendor,
                "models": backend._role_values(arguments.model),
                "efforts": backend._role_values(arguments.effort),
            }
        receipt = backend.initialize_campaign_set(
            backend._root(arguments.state_root),
            profile=profile,
            prices=arguments.prices.expanduser() if arguments.prices is not None else None,
            planned_tasks=arguments.planned_tasks,
            max_tasks=arguments.max_tasks,
            dry_run=arguments.dry_run,
        )
    except setup_unavailable:
        print(json.dumps({"error": "managed_setup_busy"}), file=sys.stderr)
        return 2
    except (OSError, managed_error):
        print(json.dumps({"error": "managed_configuration_invalid"}), file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


def migrate_evidence_main(argv: Sequence[str], *, _backend: Any | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wclass-advisory migrate-evidence", allow_abbrev=False)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--vendor", required=True, choices=("claude", "grok"))
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args(argv)
    backend = _load_backend(_backend)
    compatibility = _compat_entrypoint(backend, "migrate_evidence_main")
    if compatibility is not None:
        return _call(compatibility, argv)
    setup_unavailable = _error_type(backend, "SetupUnavailableError")
    managed_error = _error_type(backend, "ManagedAdvisoryError")
    campaign_error = _campaign_error_type(backend)
    try:
        receipt = backend.migrate_evidence_campaigns(
            backend._root(arguments.state_root),
            vendor=arguments.vendor,
            dry_run=arguments.dry_run,
        )
    except setup_unavailable:
        print(json.dumps({"error": "managed_setup_busy"}), file=sys.stderr)
        return 2
    except (OSError, managed_error, campaign_error):
        print(json.dumps({"error": "managed_evidence_migration_rejected"}), file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


def migrate_routes_main(argv: Sequence[str], *, _backend: Any | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wclass-advisory migrate-routes", allow_abbrev=False)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--vendor", required=True, choices=("agy",))
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args(argv)
    backend = _load_backend(_backend)
    compatibility = _compat_entrypoint(backend, "migrate_routes_main")
    if compatibility is not None:
        return _call(compatibility, argv)
    setup_unavailable = _error_type(backend, "SetupUnavailableError")
    managed_error = _error_type(backend, "ManagedAdvisoryError")
    campaign_error = _campaign_error_type(backend)
    try:
        receipt = backend.migrate_vendor_campaigns(
            backend._root(arguments.state_root),
            vendor=arguments.vendor,
            dry_run=arguments.dry_run,
        )
    except setup_unavailable:
        print(json.dumps({"error": "managed_setup_busy"}), file=sys.stderr)
        return 2
    except (OSError, managed_error, campaign_error):
        print(json.dumps({"error": "managed_route_migration_rejected"}), file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


def migrate_gate_main(argv: Sequence[str], *, _backend: Any | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wclass-advisory migrate-gate", allow_abbrev=False)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--vendor", required=True)
    parser.add_argument("--workflow", required=True, choices=WORKFLOWS)
    parser.add_argument(
        "--gate-metric",
        required=True,
        choices=CAMPAIGN_GATE_METRICS,
    )
    parser.add_argument("--gate-target-rate-bps", required=True, type=int)
    parser.add_argument("--gate-alpha-bps", required=True, type=int)
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args(argv)
    backend = _load_backend(_backend)
    compatibility = _compat_entrypoint(backend, "migrate_gate_main")
    if compatibility is not None:
        return _call(compatibility, argv)
    setup_unavailable = _error_type(backend, "SetupUnavailableError")
    managed_error = _error_type(backend, "ManagedAdvisoryError")
    campaign_error = _campaign_error_type(backend)
    try:
        gate = backend._gate_arguments(
            arguments.gate_metric,
            arguments.gate_target_rate_bps,
            arguments.gate_alpha_bps,
        )
        assert gate is not None
        receipt = backend.migrate_gate_campaigns(
            backend._root(arguments.state_root),
            vendor=arguments.vendor,
            workflow=arguments.workflow,
            gate=gate,
            dry_run=arguments.dry_run,
        )
    except setup_unavailable:
        print(json.dumps({"error": "managed_setup_busy"}), file=sys.stderr)
        return 2
    except (OSError, managed_error, campaign_error):
        print(json.dumps({"error": "managed_gate_migration_rejected"}), file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


def doctor_main(argv: Sequence[str], *, _backend: Any | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wclass-advisory doctor", allow_abbrev=False)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--vendor", default="all")
    parser.add_argument("--workflow", choices=("all", *WORKFLOWS), default="all")
    arguments = parser.parse_args(argv)
    backend = _load_backend(_backend)
    compatibility = _compat_entrypoint(backend, "doctor_main")
    if compatibility is not None:
        return _call(compatibility, argv)
    records_invalid = _error_type(backend.advisory_orchestration, "CampaignRecordsInvalidError")
    allocator_unavailable = _error_type(backend.advisory_orchestration, "AllocatorUnavailableError")
    managed_error = _error_type(backend, "ManagedAdvisoryError")
    try:
        root = backend._root(arguments.state_root)
        vendors = backend._selected_vendors(root, arguments.vendor)
        receipt = backend.doctor(
            root,
            vendors=vendors,
            workflows=backend._selected_workflows(arguments.workflow),
        )
    except records_invalid as error:
        print(json.dumps({"error": _error_code(error)}), file=sys.stderr)
        return 2
    except allocator_unavailable:
        print(json.dumps({"error": "managed_allocator_busy"}), file=sys.stderr)
        return 2
    except (OSError, ValueError, managed_error):
        print(json.dumps({"error": "managed_configuration_unavailable"}), file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


def cli_check_main(argv: Sequence[str], *, _backend: Any | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wclass-advisory cli-check", allow_abbrev=False)
    parser.add_argument("--vendor", choices=("all", *BUILTIN_VENDORS), default="all")
    arguments = parser.parse_args(argv)
    backend = _load_backend(_backend)
    compatibility = _compat_entrypoint(backend, "cli_check_main")
    if compatibility is not None:
        return _call(compatibility, argv)
    vendors = BUILTIN_VENDORS if arguments.vendor == "all" else (arguments.vendor,)
    results = [
        backend.advisory_preflight.check_local_capability(vendor, vendor) for vendor in vendors
    ]
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


def _provider_capability_payload(error: Any) -> dict[str, object]:
    return {
        "error": "managed_provider_preflight_failed",
        "vendor": error.vendor,
        "role": error.role,
        "child_failure_code": error.code,
        "sample_recorded": False,
    }


def provider_check_main(argv: Sequence[str], *, _backend: Any | None = None) -> int:
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
    backend = _load_backend(_backend)
    compatibility = _compat_entrypoint(backend, "provider_check_main")
    if compatibility is not None:
        return _call(compatibility, argv)
    provider_capability = _error_type(backend, "ProviderCapabilityError")
    managed_error = _error_type(backend, "ManagedAdvisoryError")
    try:
        root = backend._root(arguments.state_root)
        receipt = backend.provider_check(
            root,
            vendors=backend._selected_vendors(root, arguments.vendor),
            workflow=arguments.workflow,
            confirm_provider_egress=arguments.confirm_provider_egress,
        )
    except provider_capability as error:
        print(json.dumps(_provider_capability_payload(error), sort_keys=True), file=sys.stderr)
        return 2
    except (OSError, ValueError, managed_error):
        print(json.dumps({"error": "managed_provider_check_rejected"}), file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0 if receipt["ready"] is True else 1


def review_main(argv: Sequence[str], *, _backend: Any | None = None) -> int:
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
    backend = _load_backend(_backend)
    compatibility = _compat_entrypoint(backend, "review_main")
    if compatibility is not None:
        return _call(compatibility, argv)
    managed_error = _error_type(backend, "ManagedAdvisoryError")
    try:
        if arguments.consult and arguments.workflow not in EVIDENCE_WORKFLOWS:
            _fail(backend)
        root = backend._root(arguments.state_root)
        payload = backend.review_payload(
            root,
            vendors=backend._selected_vendors(root, arguments.vendor),
            workflow=arguments.workflow,
            require_campaign=not arguments.consult,
        )
    except (OSError, ValueError, managed_error):
        print(json.dumps({"error": "managed_configuration_unavailable"}), file=sys.stderr)
        return 2
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


def consult_main(argv: Sequence[str], *, _backend: Any | None = None) -> int:
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
    backend = _load_backend(_backend)
    compatibility = _compat_entrypoint(backend, "consult_main")
    if compatibility is not None:
        return _call(compatibility, argv)
    provider_confirmation = _error_type(backend, "ProviderConfirmationRequiredError")
    provider_conformance = _error_type(backend, "ProviderConformanceError")
    provider_capability = _error_type(backend, "ProviderCapabilityError")
    runner_changed = _error_type(backend, "RunnerVersionChangedError")
    managed_preflight = _error_type(backend, "ManagedPreflightError")
    managed_error = _error_type(backend, "ManagedAdvisoryError")
    try:
        root = backend._root(arguments.state_root)
        vendors = backend._selected_vendors(root, arguments.vendor)
        return _exit_code(
            backend.consult(
                root,
                repo=arguments.repo.expanduser().resolve(),
                task_file=arguments.task_file.expanduser(),
                vendors=vendors,
                workflow=arguments.workflow,
                role=arguments.role,
                acknowledged_route_sha256=backend._consult_route_acknowledgements(
                    arguments.ack_route_sha256, vendors
                ),
                confirm_task_egress=arguments.confirm_task_egress,
                confirm_provider_egress=arguments.confirm_provider_egress,
                timeout_seconds=arguments.timeout_seconds,
            )
        )
    except provider_confirmation:
        print(json.dumps({"error": "managed_provider_confirmation_required"}), file=sys.stderr)
        return 2
    except provider_conformance:
        print(json.dumps({"error": "managed_provider_preflight_failed"}), file=sys.stderr)
        return 2
    except provider_capability as error:
        print(json.dumps(_provider_capability_payload(error), sort_keys=True), file=sys.stderr)
        return 2
    except runner_changed:
        print(json.dumps({"error": "managed_runner_version_changed"}), file=sys.stderr)
        return 2
    except managed_preflight as error:
        print(
            json.dumps(
                {
                    "error": "managed_consult_rejected",
                    "reason_code": _error_code(error),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except (OSError, ValueError, managed_error):
        print(json.dumps({"error": "managed_consult_rejected"}), file=sys.stderr)
        return 2


def dispatch_main(argv: Sequence[str], *, _backend: Any | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wclass-advisory dispatch", allow_abbrev=False)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--task-file", required=True, type=Path)
    parser.add_argument("--vendor", default="all")
    parser.add_argument("--workflow", choices=WORKFLOWS, default="implementation")
    parser.add_argument("--confirm-task-egress", action="store_true", required=True)
    parser.add_argument("--confirm-provider-egress", action="store_true")
    arguments = parser.parse_args(argv)
    backend = _load_backend(_backend)
    compatibility = _compat_entrypoint(backend, "dispatch_main")
    if compatibility is not None:
        return _call(compatibility, argv)
    provider_confirmation = _error_type(backend, "ProviderConfirmationRequiredError")
    provider_conformance = _error_type(backend, "ProviderConformanceError")
    provider_capability = _error_type(backend, "ProviderCapabilityError")
    campaign_lane = _error_type(backend.advisory_orchestration, "CampaignLaneError")
    runner_changed = _error_type(backend, "RunnerVersionChangedError")
    managed_preflight = _error_type(backend, "ManagedPreflightError")
    managed_error = _error_type(backend, "ManagedAdvisoryError")
    try:
        root = backend._root(arguments.state_root)
        return _exit_code(
            backend.dispatch(
                root,
                repo=arguments.repo.expanduser().resolve(),
                task_file=arguments.task_file.expanduser(),
                vendors=backend._selected_vendors(root, arguments.vendor),
                workflow=arguments.workflow,
                confirm_task_egress=arguments.confirm_task_egress,
                confirm_provider_egress=arguments.confirm_provider_egress,
            )
        )
    except provider_confirmation:
        print(json.dumps({"error": "managed_provider_confirmation_required"}), file=sys.stderr)
        return 2
    except provider_conformance:
        print(json.dumps({"error": "managed_provider_preflight_failed"}), file=sys.stderr)
        return 2
    except provider_capability as error:
        print(json.dumps(_provider_capability_payload(error), sort_keys=True), file=sys.stderr)
        return 2
    except campaign_lane as error:
        print(
            json.dumps({"error": backend.advisory_orchestration.campaign_lane_error_code(error)}),
            file=sys.stderr,
        )
        return 2
    except runner_changed:
        print(json.dumps({"error": "managed_runner_version_changed"}), file=sys.stderr)
        return 2
    except managed_preflight as error:
        print(
            json.dumps(
                {
                    "error": "managed_dispatch_rejected",
                    "reason_code": _error_code(error),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except (OSError, managed_error):
        print(json.dumps({"error": "managed_dispatch_rejected"}), file=sys.stderr)
        return 2


def campaign_gate_main(argv: Sequence[str], *, _backend: Any | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wclass-advisory campaign-gate", allow_abbrev=False)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--vendor", required=True)
    parser.add_argument("--workflow", required=True, choices=WORKFLOWS)
    parser.add_argument("--generation", choices=("active", "source"), default="active")
    parser.add_argument("--metric", choices=CAMPAIGN_GATE_METRICS)
    parser.add_argument("--target-rate-bps", type=int)
    parser.add_argument("--alpha-bps", type=int)
    arguments = parser.parse_args(argv)
    backend = _load_backend(_backend)
    compatibility = _compat_entrypoint(backend, "campaign_gate_main")
    if compatibility is not None:
        return _call(compatibility, argv)
    records_invalid = _error_type(backend.advisory_orchestration, "CampaignRecordsInvalidError")
    managed_preflight = _error_type(backend, "ManagedPreflightError")
    managed_error = _error_type(backend, "ManagedAdvisoryError")
    try:
        if arguments.target_rate_bps is not None and not 0 <= arguments.target_rate_bps <= 10_000:
            _fail(backend)
        if arguments.alpha_bps is not None and not 1 <= arguments.alpha_bps <= 5_000:
            _fail(backend)
        root = backend._root(arguments.state_root)
        vendors = backend._selected_vendors(root, arguments.vendor)
        if len(vendors) != 1:
            _fail(backend)
        receipt = backend.campaign_gate(
            root,
            vendor=vendors[0],
            workflow=arguments.workflow,
            metric=arguments.metric,
            target_rate_bps=arguments.target_rate_bps,
            alpha_bps=arguments.alpha_bps,
            source_generation=arguments.generation == "source",
        )
    except records_invalid as error:
        print(json.dumps({"error": _error_code(error)}), file=sys.stderr)
        return 2
    except managed_preflight as error:
        print(json.dumps({"error": _error_code(error)}), file=sys.stderr)
        return 2
    except (OSError, ValueError, managed_error):
        print(json.dumps({"error": "managed_campaign_gate_rejected"}), file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


def status_main(argv: Sequence[str], *, _backend: Any | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wclass-advisory status", allow_abbrev=False)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--vendor", default="all")
    parser.add_argument("--workflow", choices=("all", *WORKFLOWS), default="all")
    arguments = parser.parse_args(argv)
    backend = _load_backend(_backend)
    compatibility = _compat_entrypoint(backend, "status_main")
    if compatibility is not None:
        return _call(compatibility, argv)
    managed_error = _error_type(backend, "ManagedAdvisoryError")
    try:
        root = backend._root(arguments.state_root)
        vendors = backend._selected_vendors(root, arguments.vendor)
        workflows = backend._selected_workflows(arguments.workflow)
    except (OSError, managed_error):
        print(json.dumps({"error": "managed_configuration_unavailable"}), file=sys.stderr)
        return 2
    portfolio_arguments: list[str] = []
    for vendor in vendors:
        for workflow in workflows:
            selected = backend._active_campaign_paths(root, vendor, workflow)
            portfolio_arguments.extend(
                (
                    "--campaign",
                    vendor,
                    workflow,
                    str(selected.campaign),
                    str(selected.results),
                )
            )
    return _exit_code(backend.advisory_portfolio.main(portfolio_arguments))


def prune_main(argv: Sequence[str], *, _backend: Any | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wclass-advisory cleanup", allow_abbrev=False)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--vendor", default="all")
    parser.add_argument("--workflow", choices=("all", *WORKFLOWS), default="all")
    arguments = parser.parse_args(argv)
    backend = _load_backend(_backend)
    compatibility = _compat_entrypoint(backend, "prune_main")
    if compatibility is not None:
        return _call(compatibility, argv)
    managed_error = _error_type(backend, "ManagedAdvisoryError")
    campaign_error = _campaign_error_type(backend)
    try:
        root = backend._root(arguments.state_root)
        vendors = backend._selected_vendors(root, arguments.vendor)
        totals = {
            "populations": 0,
            "lanes_scanned": 0,
            "busy_lanes": 0,
            "registered": 0,
            "removed": 0,
            "retained": 0,
        }
        for vendor in vendors:
            for workflow in backend._selected_workflows(arguments.workflow):
                selected = backend._active_campaign_paths(root, vendor, workflow)
                result = backend.speculative_run.prune_available_lanes(selected.results)
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
    except (OSError, managed_error, campaign_error):
        print(json.dumps({"error": "managed_cleanup_rejected"}), file=sys.stderr)
        return 2


for _entrypoint_name in (
    "init_main",
    "migrate_evidence_main",
    "migrate_routes_main",
    "migrate_gate_main",
    "doctor_main",
    "cli_check_main",
    "provider_check_main",
    "review_main",
    "consult_main",
    "dispatch_main",
    "campaign_gate_main",
    "status_main",
    "prune_main",
):
    globals()[_entrypoint_name]._managed_cli_entrypoint = True
