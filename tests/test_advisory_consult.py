from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from weightclass import __version__
from weightclass.advisory import advisory_parallel, advisory_routes, managed_advisory

ROOT = Path(__file__).resolve().parent.parent


def research_result() -> dict[str, object]:
    return {
        "schema_version": 1,
        "mode": "research",
        "question": "What should improve?",
        "summary": "A bounded research result.",
        "claims": [
            {
                "claim": "One grounded claim.",
                "status": "supported",
                "confidence": "high",
                "evidence": ["repository evidence"],
                "counterevidence": [],
            }
        ],
        "limitations": ["Synthetic consult test."],
    }


def custom_profile(command: list[str]) -> dict[str, object]:
    roles = {role: command for role in ("cheap", "advisor", "expensive")}
    return {
        "schema_version": 2,
        "vendor": "custom",
        "commands": {"implementation": roles, "evidence": roles},
    }


class AdvisoryConsultTests(unittest.TestCase):
    def test_managed_consult_emits_verified_results_in_completion_order(self) -> None:
        routes = advisory_routes.AdvisoryRoutes(("vendor",), ("vendor",), ("vendor",))
        configurations = {
            vendor: managed_advisory.ConsultConfiguration(
                managed_advisory.CampaignPaths(
                    Path(f"/state/{vendor}-profile"),
                    Path("/state/prices"),
                    Path("/state/campaign"),
                    Path("/state/results"),
                ),
                routes,
                False,
                "sha256:" + digit * 64,
                "sha256:" + digit * 64,
            )
            for vendor, digit in (("codex", "1"), ("claude", "2"))
        }
        result_payload = json.dumps(research_result(), separators=(",", ":")).encode("utf-8")

        def run(
            jobs: tuple[advisory_parallel.AdvisoryJob, ...],
            *,
            progress: advisory_parallel.ProgressCallback | None = None,
            result_callback: advisory_parallel.ResultCallback | None = None,
            heartbeat_seconds: float = advisory_parallel.DEFAULT_HEARTBEAT_SECONDS,
        ) -> tuple[advisory_parallel.AdvisoryResult, ...]:
            self.assertGreater(heartbeat_seconds, 0)
            self.assertEqual([job.timeout_seconds for job in jobs], [123.0, 123.0])
            results = tuple(
                advisory_parallel.AdvisoryResult(job.label, 0, result_payload, b"", True)
                for job in jobs
            )
            assert result_callback is not None
            result_callback(results[1])
            result_callback(results[0])
            return results

        stdout = mock.Mock(buffer=io.BytesIO())
        stderr = mock.Mock(buffer=io.BytesIO())
        with (
            mock.patch.object(
                managed_advisory,
                "_consult_configuration",
                side_effect=lambda _root, vendor, _workflow: configurations[vendor],
            ),
            mock.patch.object(managed_advisory, "_require_consult_capabilities"),
            mock.patch.object(managed_advisory, "_preflight_task_file"),
            mock.patch.object(managed_advisory, "_private_regular"),
            mock.patch.object(managed_advisory, "_preflight_repo"),
            mock.patch.object(advisory_parallel, "run_parallel", side_effect=run),
            mock.patch("sys.stdout", stdout),
            mock.patch("sys.stderr", stderr),
        ):
            code = managed_advisory.consult(
                Path("/state"),
                repo=Path("/repo"),
                task_file=Path("/task"),
                vendors=("codex", "claude"),
                workflow="research",
                role="cheap",
                acknowledged_route_sha256={
                    vendor: configuration.route_sha256
                    for vendor, configuration in configurations.items()
                },
                confirm_task_egress=True,
                confirm_provider_egress=False,
                timeout_seconds=123,
            )

        self.assertEqual(code, 0)
        receipts = [json.loads(line) for line in stdout.buffer.getvalue().splitlines()]
        self.assertEqual([receipt["vendor"] for receipt in receipts], ["claude", "codex"])

    def test_managed_consult_bootstrap_rejects_cwd_module_shadowing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shadow = root / "weightclass" / "advisory"
            shadow.mkdir(parents=True)
            marker = root / "shadow-imported"
            (shadow.parent / "__init__.py").write_text("", encoding="utf-8")
            (shadow / "__init__.py").write_text("", encoding="utf-8")
            (shadow / "advisory_consult.py").write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n",
                encoding="utf-8",
            )
            job = managed_advisory._consult_job(
                "custom",
                "research",
                "cheap",
                Path("/missing/repo"),
                Path("/missing/PRIVATE-TASK"),
                Path("/missing/profile"),
                "sha256:" + "0" * 64,
                Path("/missing/verifier"),
            )
            completed = subprocess.run(
                job.command,
                cwd=root,
                capture_output=True,
                check=False,
                text=True,
            )
            shadow_imported = marker.exists()

        self.assertEqual(job.command[1], "-I")
        self.assertEqual(job.timeout_seconds, managed_advisory.CONSULT_DEFAULT_TIMEOUT_SECONDS)
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertFalse(shadow_imported)

    def test_consult_timeout_is_validated_before_configuration_or_task_access(self) -> None:
        with (
            mock.patch.object(
                managed_advisory,
                "_consult_configuration",
                side_effect=AssertionError("configuration inspected"),
            ),
            self.assertRaisesRegex(managed_advisory.ManagedAdvisoryError, "^$"),
        ):
            managed_advisory.consult(
                Path("/state"),
                repo=Path("/repo"),
                task_file=Path("/task"),
                vendors=("custom",),
                workflow="research",
                role="cheap",
                acknowledged_route_sha256={"custom": "sha256:" + "1" * 64},
                confirm_task_egress=True,
                confirm_provider_egress=False,
                timeout_seconds=0,
            )

    def test_internal_consult_rejects_profile_mismatch_before_task_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "profile.json"
            profile.write_text(
                json.dumps(custom_profile([sys.executable, "-c", "pass"])),
                encoding="utf-8",
            )
            profile.chmod(0o600)
            missing_task = root / "PRIVATE-TASK-MUST-NOT-BE-OPENED"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass.advisory.advisory_consult",
                    "--expected-package-version",
                    __version__,
                    "--workflow",
                    "research",
                    "--vendor",
                    "custom",
                    "--role",
                    "cheap",
                    "--repo",
                    str(root),
                    "--task-file",
                    str(missing_task),
                    "--route-profile",
                    str(profile),
                    "--expected-route-sha256",
                    "sha256:" + "0" * 64,
                    "--verify",
                    str(root / "verify"),
                ],
                cwd=ROOT,
                capture_output=True,
                check=False,
                text=True,
            )

        self.assertEqual(completed.returncode, 2)
        self.assertNotIn("PRIVATE-TASK-MUST-NOT-BE-OPENED", completed.stdout + completed.stderr)
        diagnostics = [json.loads(line) for line in completed.stderr.splitlines() if line]
        self.assertEqual(diagnostics[-1]["event"], "advisory_consult_failed")
        self.assertEqual(diagnostics[-1]["failure_stage"], "configuration")
        self.assertEqual(diagnostics[-1]["reason_code"], "route_digest_mismatch")

    def test_internal_consult_prints_only_canonical_json_and_records_no_sample(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            (repo / "tracked").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked"], cwd=repo, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.invalid",
                    "commit",
                    "-qm",
                    "baseline",
                ],
                cwd=repo,
                check=True,
            )
            result = json.dumps(research_result(), sort_keys=True, separators=(",", ":"))
            program = f"import sys;sys.stdin.read();print({result!r})"
            profile = root / "profile.json"
            profile.write_text(
                json.dumps(custom_profile([sys.executable, "-c", program])),
                encoding="utf-8",
            )
            profile.chmod(0o600)
            loaded_profile = advisory_routes.load_profile(profile)
            read_only_routes = advisory_routes.build_routes(
                loaded_profile, read_only_executors=True, evidence_workflow="research"
            )
            route_sha256 = advisory_routes.evidence_routes_digest(
                loaded_profile, read_only_routes, "research"
            )
            task = root / "task"
            task.write_text("PRIVATE-CONSULT-TASK", encoding="utf-8")
            task.chmod(0o600)
            verifier = root / "verify"
            verifier.write_text(
                "#!/usr/bin/env python3\n"
                "import json,sys\n"
                "value=json.load(sys.stdin)\n"
                "raise SystemExit(0 if value.get('mode') == 'research' else 1)\n",
                encoding="utf-8",
            )
            verifier.chmod(0o700)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass.advisory.advisory_consult",
                    "--expected-package-version",
                    __version__,
                    "--workflow",
                    "research",
                    "--vendor",
                    "custom",
                    "--role",
                    "cheap",
                    "--repo",
                    str(repo),
                    "--task-file",
                    str(task),
                    "--route-profile",
                    str(profile),
                    "--expected-route-sha256",
                    route_sha256,
                    "--verify",
                    str(verifier),
                ],
                cwd=ROOT,
                capture_output=True,
                check=False,
                text=True,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), research_result())
        self.assertNotIn("PRIVATE-CONSULT-TASK", completed.stdout + completed.stderr)

    def test_custom_profile_requires_provider_confirmation_before_task_inspection(self) -> None:
        selected = managed_advisory.CampaignPaths(
            Path("/state/profile"),
            Path("/state/prices"),
            Path("/state/campaign"),
            Path("/state/results"),
        )
        routes = advisory_routes.AdvisoryRoutes(("custom",), ("custom",), ("custom",))
        profile_sha256 = "sha256:" + "a" * 64
        route_sha256 = "sha256:" + "1" * 64
        with (
            mock.patch.object(
                managed_advisory,
                "_consult_configuration",
                return_value=managed_advisory.ConsultConfiguration(
                    selected, routes, True, profile_sha256, route_sha256
                ),
            ),
            mock.patch.object(managed_advisory, "_require_consult_capabilities"),
            mock.patch.object(
                managed_advisory,
                "_preflight_task_file",
                side_effect=AssertionError("task inspected"),
            ),
            self.assertRaises(managed_advisory.ProviderConfirmationRequiredError),
        ):
            managed_advisory.consult(
                Path("/state"),
                repo=Path("/repo"),
                task_file=Path("/task"),
                vendors=("custom",),
                workflow="research",
                role="cheap",
                acknowledged_route_sha256={"custom": route_sha256},
                confirm_task_egress=True,
                confirm_provider_egress=False,
            )

    def test_acknowledged_route_mismatch_stops_before_capability_or_task_checks(self) -> None:
        selected = managed_advisory.CampaignPaths(
            Path("/state/profile"),
            Path("/state/prices"),
            Path("/state/campaign"),
            Path("/state/results"),
        )
        routes = advisory_routes.AdvisoryRoutes(("custom",), ("custom",), ("custom",))
        profile_sha256 = "sha256:" + "a" * 64
        route_sha256 = "sha256:" + "2" * 64
        with (
            mock.patch.object(
                managed_advisory,
                "_consult_configuration",
                return_value=managed_advisory.ConsultConfiguration(
                    selected, routes, False, profile_sha256, route_sha256
                ),
            ),
            mock.patch.object(
                managed_advisory,
                "_require_consult_capabilities",
                side_effect=AssertionError("capability inspected"),
            ),
            self.assertRaisesRegex(
                managed_advisory.ManagedPreflightError,
                "managed_consult_route_mismatch",
            ),
        ):
            managed_advisory.consult(
                Path("/state"),
                repo=Path("/repo"),
                task_file=Path("/task"),
                vendors=("custom",),
                workflow="research",
                role="cheap",
                acknowledged_route_sha256={"custom": "sha256:" + "3" * 64},
                confirm_task_egress=True,
                confirm_provider_egress=False,
            )

    def test_custom_provider_check_precedes_task_preflight(self) -> None:
        events: list[str] = []
        selected = managed_advisory.CampaignPaths(
            Path("/state/profile"),
            Path("/state/prices"),
            Path("/state/campaign"),
            Path("/state/results"),
        )
        routes = advisory_routes.AdvisoryRoutes(("custom",), ("custom",), ("custom",))
        profile_sha256 = "sha256:" + "b" * 64
        route_sha256 = "sha256:" + "4" * 64

        def provider(*args: object, **kwargs: object) -> dict[str, object]:
            self.assertIs(kwargs.get("require_campaign"), False)
            self.assertEqual(kwargs.get("expected_route_sha256"), {"custom": route_sha256})
            events.append("provider")
            return {"ready": True}

        def task(_path: Path) -> None:
            events.append("task")
            raise managed_advisory.ManagedPreflightError("managed_task_input_rejected")

        with (
            mock.patch.object(
                managed_advisory,
                "_consult_configuration",
                return_value=managed_advisory.ConsultConfiguration(
                    selected, routes, True, profile_sha256, route_sha256
                ),
            ),
            mock.patch.object(managed_advisory, "_require_consult_capabilities"),
            mock.patch.object(managed_advisory, "provider_check", side_effect=provider),
            mock.patch.object(managed_advisory, "_preflight_task_file", side_effect=task),
            self.assertRaises(managed_advisory.ManagedPreflightError),
        ):
            managed_advisory.consult(
                Path("/state"),
                repo=Path("/repo"),
                task_file=Path("/task"),
                vendors=("custom",),
                workflow="research",
                role="cheap",
                acknowledged_route_sha256={"custom": route_sha256},
                confirm_task_egress=True,
                confirm_provider_egress=True,
            )

        self.assertEqual(events, ["provider", "task"])

    def test_failed_custom_provider_check_stops_before_task_inspection(self) -> None:
        selected = managed_advisory.CampaignPaths(
            Path("/state/profile"),
            Path("/state/prices"),
            Path("/state/campaign"),
            Path("/state/results"),
        )
        routes = advisory_routes.AdvisoryRoutes(("custom",), ("custom",), ("custom",))
        profile_sha256 = "sha256:" + "c" * 64
        route_sha256 = "sha256:" + "5" * 64
        with (
            mock.patch.object(
                managed_advisory,
                "_consult_configuration",
                return_value=managed_advisory.ConsultConfiguration(
                    selected, routes, True, profile_sha256, route_sha256
                ),
            ),
            mock.patch.object(managed_advisory, "_require_consult_capabilities"),
            mock.patch.object(
                managed_advisory,
                "provider_check",
                return_value={"ready": False},
            ),
            mock.patch.object(
                managed_advisory,
                "_preflight_task_file",
                side_effect=AssertionError("task inspected"),
            ),
            self.assertRaises(managed_advisory.ProviderConformanceError),
        ):
            managed_advisory.consult(
                Path("/state"),
                repo=Path("/repo"),
                task_file=Path("/task"),
                vendors=("custom",),
                workflow="research",
                role="cheap",
                acknowledged_route_sha256={"custom": route_sha256},
                confirm_task_egress=True,
                confirm_provider_egress=True,
            )

    def test_consult_receipts_are_tagged_non_recording_and_untrusted(self) -> None:
        payload = managed_advisory._consult_result_receipt("claude", "research", research_result())
        value = json.loads(payload)
        self.assertEqual(value["event"], "managed_consult_result")
        self.assertEqual(value["content_trust"], "untrusted_model_authored")
        self.assertFalse(value["sample_recorded"])
        failed = managed_advisory._consult_failure_receipt(
            "grok",
            "research",
            advisory_parallel.AdvisoryResult("grok", 1, b"PRIVATE", b"", True),
        )
        self.assertNotIn(b"PRIVATE", failed)
        self.assertEqual(json.loads(failed)["reason_code"], "internal_diagnostic_unavailable")

    def test_managed_consult_accepts_only_the_closed_internal_failure_receipt(self) -> None:
        diagnostic = {
            "schema_version": 1,
            "event": "advisory_consult_failed",
            "failure_stage": "verification",
            "reason_code": "verification_rejected",
            "child_exit_code": 0,
            "child_timed_out": False,
            "child_failure_code": "none",
            "result_shape": "structured_output",
            "verify_exit_code": 1,
            "verify_timed_out": False,
        }
        payload = (
            b"PRIVATE RAW PROVIDER STDERR\n"
            + json.dumps(diagnostic, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )

        parsed = managed_advisory._consult_child_diagnostic(payload)

        self.assertEqual(parsed, diagnostic)
        receipt = managed_advisory._consult_failure_receipt(
            "claude",
            "design",
            advisory_parallel.AdvisoryResult("claude", 1, b"", payload, True),
            parsed,
        )
        self.assertNotIn(b"PRIVATE", receipt)
        self.assertEqual(json.loads(receipt)["reason_code"], "verification_rejected")

        poisoned = json.dumps({**diagnostic, "reason_code": "PRIVATE"}).encode("utf-8")
        self.assertIsNone(managed_advisory._consult_child_diagnostic(poisoned))


if __name__ == "__main__":
    unittest.main()
