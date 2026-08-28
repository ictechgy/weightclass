from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import cast
from unittest import mock

from weightclass.advisory import (
    advisory_preflight,
    advisory_routes,
    managed_advisory,
    speculative_run,
)


def _ready_capabilities(
    _vendor: str,
    _routes: advisory_routes.AdvisoryRoutes,
    *,
    required_roles: tuple[str, ...] | None = None,
) -> tuple[tuple[str, advisory_preflight.CapabilityResult], ...]:
    ready = advisory_preflight.CapabilityResult("custom", "custom_unverified", "none", None)
    roles = managed_advisory._validated_required_roles(required_roles)
    return tuple((role, ready) for role in roles)


def _baseline_response() -> str:
    payload = managed_advisory._baseline_probe("review")
    assert payload is not None
    return payload.decode("utf-8")


def _child_result() -> dict[str, object]:
    return {
        "exit_code": 0,
        "timed_out": False,
        "seconds": 0.1,
        "tokens": None,
        "usage": None,
        "failure_code": "none",
        "stdout_present": True,
        "stderr_present": False,
    }


class AdvisoryProviderRoleTests(unittest.TestCase):
    def test_provider_check_rejects_duplicate_vendors(self) -> None:
        with self.assertRaises(managed_advisory.ManagedAdvisoryError):
            managed_advisory.provider_check(
                Path("/state"),
                vendors=("duplicate", "duplicate"),
                workflow="review",
                confirm_provider_egress=True,
            )

    def test_provider_group_identity_collapses_executable_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "provider"
            alias = Path(directory) / "provider-alias"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o700)
            alias.symlink_to(executable)

            self.assertEqual(
                managed_advisory._provider_executable_group(str(executable)),
                managed_advisory._provider_executable_group(str(alias)),
            )

    def test_local_probe_setup_failure_becomes_a_closed_receipt(self) -> None:
        probe = managed_advisory._ProviderProbe(
            "vendor", "cheap", ("provider",), ("command", "provider")
        )
        with mock.patch.object(
            managed_advisory,
            "_run_provider_probe_inner",
            side_effect=managed_advisory.ManagedAdvisoryError(),
        ):
            result = managed_advisory._run_provider_probe(probe, "prompt", "review")

        self.assertEqual(result["child_failure_code"], "local_probe_failed")
        self.assertIs(result["ready"], False)

    def test_route_capabilities_only_checks_requested_roles(self) -> None:
        routes = advisory_routes.AdvisoryRoutes(
            ("cheap-cli",),
            ("advisor-cli",),
            ("expensive-cli",),
        )
        ready = advisory_preflight.CapabilityResult("custom", "custom_unverified", "none", None)
        with mock.patch.object(
            advisory_preflight,
            "check_local_capability",
            return_value=ready,
        ) as check:
            result = managed_advisory._route_capabilities(
                "custom", routes, required_roles=("expensive",)
            )

        self.assertEqual(result, (("expensive", ready),))
        check.assert_called_once_with("custom", "expensive-cli")

    def test_required_roles_are_validated_and_returned_in_stable_order(self) -> None:
        self.assertEqual(
            managed_advisory._validated_required_roles(("expensive", "cheap")),
            ("cheap", "expensive"),
        )
        for invalid in ((), ("cheap", "cheap"), ("unknown",), (1,), 1, "cheap"):
            with (
                self.subTest(invalid=invalid),
                self.assertRaises(managed_advisory.ManagedAdvisoryError),
            ):
                managed_advisory._validated_required_roles(invalid)  # type: ignore[arg-type]

    def test_consult_scopes_custom_provider_check_to_selected_role(self) -> None:
        routes = advisory_routes.AdvisoryRoutes(
            ("cheap-cli",),
            ("advisor-cli",),
            ("expensive-cli",),
        )
        digest = "sha256:" + "1" * 64
        configuration = managed_advisory.ConsultConfiguration(
            managed_advisory.CampaignPaths(
                Path("/state/profile"),
                Path("/state/prices"),
                Path("/state/campaign"),
                Path("/state/results"),
            ),
            routes,
            True,
            digest,
            digest,
        )
        calls: list[dict[str, object]] = []

        def provider_check(*_args: object, **kwargs: object) -> dict[str, object]:
            calls.append(kwargs)
            raise managed_advisory.ProviderConformanceError

        with (
            mock.patch.object(
                managed_advisory,
                "_consult_configuration",
                return_value=configuration,
            ),
            mock.patch.object(managed_advisory, "_require_consult_capabilities"),
            mock.patch.object(managed_advisory, "provider_check", side_effect=provider_check),
            self.assertRaises(managed_advisory.ProviderConformanceError),
        ):
            managed_advisory.consult(
                Path("/state"),
                repo=Path("/repo"),
                task_file=Path("/task"),
                vendors=("custom",),
                workflow="review",
                role="expensive",
                acknowledged_route_sha256={"custom": digest},
                confirm_task_egress=True,
                confirm_provider_egress=True,
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["required_roles"], ("expensive",))
        self.assertEqual(calls[0]["require_campaign"], False)

    def test_distinct_executable_groups_overlap_but_same_executable_is_serial(self) -> None:
        vendors = ("vendor-a", "vendor-b")
        routes = {
            "vendor-a": advisory_routes.AdvisoryRoutes(
                ("exec-a", "cheap"),
                ("exec-a", "advisor"),
                ("exec-b", "expensive"),
            ),
            "vendor-b": advisory_routes.AdvisoryRoutes(
                ("exec-c", "cheap"),
                ("exec-c", "advisor"),
                ("exec-d", "expensive"),
            ),
        }
        active_by_executable: dict[str, int] = {}
        max_by_executable: dict[str, int] = {}
        active = 0
        max_active = 0
        workspaces: list[Path] = []
        lock = threading.Lock()

        def run_child(
            command: list[str], workspace: Path, _prompt: str, **_kwargs: object
        ) -> tuple[dict[str, object], str]:
            nonlocal active, max_active
            executable = command[0]
            with lock:
                active += 1
                max_active = max(max_active, active)
                active_by_executable[executable] = active_by_executable.get(executable, 0) + 1
                max_by_executable[executable] = max(
                    max_by_executable.get(executable, 0), active_by_executable[executable]
                )
                workspaces.append(workspace)
            time.sleep(0.05)
            with lock:
                active -= 1
                active_by_executable[executable] -= 1
            return _child_result(), _baseline_response()

        with (
            mock.patch.object(
                managed_advisory,
                "_route_capabilities",
                side_effect=_ready_capabilities,
            ),
            mock.patch.object(speculative_run, "run_child", side_effect=run_child),
        ):
            receipt = managed_advisory.provider_check(
                Path("/state"),
                vendors=vendors,
                workflow="review",
                confirm_provider_egress=True,
                routes_by_vendor=routes,
            )

        self.assertTrue(receipt["ready"])
        self.assertEqual(
            [
                (row["vendor"], row["role"])
                for row in cast(list[dict[str, object]], receipt["results"])
            ],
            [(vendor, role) for vendor in vendors for role in managed_advisory.ROLES],
        )
        self.assertGreater(max_active, 1)
        self.assertEqual(
            max_by_executable,
            {executable: 1 for executable in ("exec-a", "exec-b", "exec-c", "exec-d")},
        )
        self.assertEqual(len(workspaces), len(set(workspaces)))

    def test_provider_check_caps_distinct_executable_groups_at_four_workers(self) -> None:
        vendors = tuple(f"vendor-{index}" for index in range(6))
        routes = {
            vendor: advisory_routes.AdvisoryRoutes(
                (f"exec-{index}", "cheap"),
                (f"exec-{index}", "advisor"),
                (f"exec-{index}", "expensive"),
            )
            for index, vendor in enumerate(vendors)
        }
        active = 0
        max_active = 0
        lock = threading.Lock()

        def run_child(
            _command: list[str], _workspace: Path, _prompt: str, **_kwargs: object
        ) -> tuple[dict[str, object], str]:
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return _child_result(), _baseline_response()

        with (
            mock.patch.object(
                managed_advisory,
                "_route_capabilities",
                side_effect=_ready_capabilities,
            ),
            mock.patch.object(speculative_run, "run_child", side_effect=run_child),
        ):
            receipt = managed_advisory.provider_check(
                Path("/state"),
                vendors=vendors,
                workflow="review",
                confirm_provider_egress=True,
                routes_by_vendor=routes,
                required_roles=("cheap",),
            )

        self.assertTrue(receipt["ready"])
        self.assertEqual(receipt["calls"], len(vendors))
        self.assertGreater(max_active, 1)
        self.assertLessEqual(max_active, managed_advisory.PROVIDER_CHECK_MAX_EXECUTABLE_GROUPS)


if __name__ == "__main__":
    unittest.main()
