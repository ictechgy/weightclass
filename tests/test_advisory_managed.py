from __future__ import annotations

import fcntl
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from weightclass.advisory import (
    advisory_campaign,
    advisory_orchestration,
    advisory_parallel,
    advisory_preflight,
    advisory_routes,
    managed_advisory,
    speculative_run,
)

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ("implementation", "review", "research", "diagnosis", "design")


def codex_profile() -> dict[str, object]:
    return {
        "schema_version": 1,
        "vendor": "codex",
        "models": {"cheap": "cheap", "advisor": "advisor", "expensive": "expensive"},
        "efforts": {"cheap": "low", "advisor": "high", "expensive": "high"},
    }


def custom_profile(vendor: str) -> dict[str, object]:
    return {
        "schema_version": 2,
        "vendor": vendor,
        "commands": {
            workflow: {
                role: [f"{vendor}-cli", "--role", role]
                for role in ("cheap", "advisor", "expensive")
            }
            for workflow in ("implementation", "evidence")
        },
    }


class ManagedAdvisoryInitializationTests(unittest.TestCase):
    def test_setup_lock_has_a_bounded_task_free_busy_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "advisory-v1"
            state_root.mkdir(mode=0o700)
            descriptor = os.open(state_root / ".setup.lock", os.O_RDWR | os.O_CREAT, 0o600)
            self.addCleanup(os.close, descriptor)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with (
                mock.patch.object(managed_advisory, "SETUP_LOCK_TIMEOUT", 0.01),
                mock.patch.object(managed_advisory, "SETUP_LOCK_POLL_SECONDS", 0.001),
                self.assertRaisesRegex(managed_advisory.SetupUnavailableError, "^$"),
            ):
                managed_advisory._setup_lock(state_root)

    def test_init_reports_setup_contention_without_configuration_details(self) -> None:
        arguments = [
            "--vendor",
            "codex",
            "--model",
            "cheap=c",
            "--model",
            "advisor=a",
            "--model",
            "expensive=e",
            "--effort",
            "cheap=low",
            "--effort",
            "advisor=high",
            "--effort",
            "expensive=high",
        ]
        stderr = io.StringIO()
        with (
            mock.patch.object(
                managed_advisory,
                "initialize_campaign_set",
                side_effect=managed_advisory.SetupUnavailableError(),
            ),
            mock.patch("sys.stderr", stderr),
        ):
            code = managed_advisory.init_main(arguments)

        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stderr.getvalue()), {"error": "managed_setup_busy"})

    def test_runner_bootstrap_rejects_an_installed_version_change_before_runner_start(self) -> None:
        accepted = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                managed_advisory._RUNNER_BOOTSTRAP,
                str(managed_advisory.PACKAGE_ROOT),
                managed_advisory.PACKAGE_VERSION,
                "--help",
            ],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                managed_advisory._RUNNER_BOOTSTRAP,
                str(managed_advisory.PACKAGE_ROOT),
                "definitely-not-the-loaded-version",
                "--help",
            ],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
        )

        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIn("--task-file", accepted.stdout)
        self.assertEqual(completed.returncode, managed_advisory.RUNNER_VERSION_CHANGED_EXIT)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "")

    def test_claude_evidence_migration_preserves_legacy_population(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "advisory-v1"
            with mock.patch.object(
                managed_advisory,
                "campaign_paths",
                side_effect=managed_advisory.legacy_campaign_paths,
            ):
                managed_advisory.initialize_campaign_set(
                    state_root,
                    profile=codex_profile() | {"vendor": "claude"},
                    prices=None,
                    planned_tasks=60,
                    max_tasks=150,
                    dry_run=False,
                )
            legacy = managed_advisory.legacy_campaign_paths(state_root, "claude", "review")
            legacy_manifest = advisory_campaign.load_manifest(legacy.campaign)
            record = {
                "campaign": advisory_campaign.record_binding(legacy_manifest, 1),
                "workflow": "review",
            }
            log = legacy.results / "runs.jsonl"
            log.write_text(json.dumps(record) + "\n", encoding="utf-8")
            log.chmod(0o600)
            campaign_before = legacy.campaign.read_bytes()
            log_before = log.read_bytes()

            preview = managed_advisory.migrate_evidence_campaigns(
                state_root, vendor="claude", dry_run=True
            )
            receipt = managed_advisory.migrate_evidence_campaigns(
                state_root, vendor="claude", dry_run=False
            )
            repeated = managed_advisory.migrate_evidence_campaigns(
                state_root, vendor="claude", dry_run=False
            )

            self.assertFalse(preview["already_migrated"])
            self.assertFalse(receipt["already_migrated"])
            self.assertTrue(repeated["already_migrated"])
            self.assertTrue(receipt["legacy_preserved"])
            self.assertEqual(legacy.campaign.read_bytes(), campaign_before)
            self.assertEqual(log.read_bytes(), log_before)
            current = managed_advisory.campaign_paths(state_root, "claude", "review")
            self.assertIn(managed_advisory.CLAUDE_EVIDENCE_GENERATION, current.campaign.name)
            self.assertTrue(current.campaign.is_file())
            self.assertTrue(current.results.is_dir())
            self.assertFalse((current.results / "runs.jsonl").exists())
            _, _, routes = managed_advisory._configuration(state_root, "claude", "review")
            self.assertEqual(
                routes.cheap[routes.cheap.index("--permission-mode") + 1],
                "dontAsk",
            )
            self.assertIn("--json-schema", routes.cheap)
            reviewed = managed_advisory.review_payload(
                state_root, vendors=("claude",), workflow="review"
            )
            reviewed_routes = reviewed["routes"]
            assert isinstance(reviewed_routes, list)
            reviewed_route = reviewed_routes[0]
            assert isinstance(reviewed_route, dict)
            self.assertEqual(reviewed_route["executor_access"], "read_only")
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                self.assertEqual(
                    managed_advisory.status_main(["--state-root", str(state_root)]),
                    0,
                )
            status = json.loads(stdout.getvalue())
            review_status = next(
                item
                for item in status["campaigns"]
                if item["vendor"] == "claude" and item["workflow"] == "review"
            )
            self.assertEqual(review_status["tasks"], 0)

    def test_changed_builtin_routes_use_separate_generations(self) -> None:
        root = Path("/private/advisory-v1")
        self.assertIn(
            managed_advisory.CLAUDE_EVIDENCE_GENERATION,
            managed_advisory.campaign_paths(root, "claude", "review").campaign.name,
        )
        self.assertEqual(
            managed_advisory.campaign_paths(root, "claude", "implementation"),
            managed_advisory.legacy_campaign_paths(root, "claude", "implementation"),
        )
        self.assertEqual(
            managed_advisory.campaign_paths(root, "codex", "review"),
            managed_advisory.legacy_campaign_paths(root, "codex", "review"),
        )
        self.assertIn(
            managed_advisory.AGY_ROUTE_GENERATION,
            managed_advisory.campaign_paths(root, "agy", "implementation").campaign.name,
        )
        self.assertIn(
            managed_advisory.GROK_EVIDENCE_GENERATION,
            managed_advisory.campaign_paths(root, "grok", "review").campaign.name,
        )
        self.assertEqual(
            managed_advisory.campaign_paths(root, "grok", "implementation"),
            managed_advisory.legacy_campaign_paths(root, "grok", "implementation"),
        )

    def test_agy_and_grok_migrations_preserve_legacy_campaigns(self) -> None:
        cases = (("agy", WORKFLOWS), ("grok", WORKFLOWS[1:]))
        for vendor, expected_workflows in cases:
            with self.subTest(vendor=vendor), tempfile.TemporaryDirectory() as directory:
                state_root = Path(directory) / "advisory-v1"
                with mock.patch.object(
                    managed_advisory,
                    "campaign_paths",
                    side_effect=managed_advisory.legacy_campaign_paths,
                ):
                    managed_advisory.initialize_campaign_set(
                        state_root,
                        profile=codex_profile() | {"vendor": vendor},
                        prices=None,
                        planned_tasks=12,
                        max_tasks=24,
                        dry_run=False,
                    )
                legacy = managed_advisory.legacy_campaign_paths(
                    state_root, vendor, expected_workflows[0]
                )
                before = legacy.campaign.read_bytes()

                preview = managed_advisory.migrate_vendor_campaigns(
                    state_root, vendor=vendor, dry_run=True
                )
                receipt = managed_advisory.migrate_vendor_campaigns(
                    state_root, vendor=vendor, dry_run=False
                )

                self.assertEqual(tuple(receipt["workflows"]), expected_workflows)
                self.assertFalse(preview["already_migrated"])
                self.assertFalse(receipt["already_migrated"])
                self.assertEqual(legacy.campaign.read_bytes(), before)
                for workflow in expected_workflows:
                    current = managed_advisory.campaign_paths(state_root, vendor, workflow)
                    self.assertTrue(current.campaign.is_file())
                    self.assertTrue(current.results.is_dir())
                    self.assertFalse((current.results / "runs.jsonl").exists())

    def test_migration_accepts_and_preserves_a_structured_v1_only_install(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "advisory-v1"
            with mock.patch.object(managed_advisory, "CLAUDE_EVIDENCE_GENERATION", "structured-v1"):
                managed_advisory.initialize_campaign_set(
                    state_root,
                    profile=codex_profile() | {"vendor": "claude"},
                    prices=None,
                    planned_tasks=24,
                    max_tasks=48,
                    dry_run=False,
                )
            previous = managed_advisory.previous_evidence_campaign_paths(
                state_root, "claude", "review", "structured-v1"
            )
            previous_manifest = advisory_campaign.load_manifest(previous.campaign)
            previous_log = previous.results / "runs.jsonl"
            previous_log.write_text(
                json.dumps(
                    {
                        "campaign": advisory_campaign.record_binding(previous_manifest, 1),
                        "workflow": "review",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            previous_log.chmod(0o600)
            campaign_before = previous.campaign.read_bytes()
            log_before = previous_log.read_bytes()

            receipt = managed_advisory.migrate_evidence_campaigns(
                state_root, vendor="claude", dry_run=False
            )

            self.assertFalse(receipt["already_migrated"])
            self.assertEqual(receipt["generation"], "structured-v6")
            self.assertEqual(previous.campaign.read_bytes(), campaign_before)
            self.assertEqual(previous_log.read_bytes(), log_before)
            current = managed_advisory.campaign_paths(state_root, "claude", "review")
            self.assertNotEqual(current, previous)
            current_manifest = advisory_campaign.load_manifest(current.campaign)
            self.assertEqual(current_manifest["planned_tasks"], 24)
            self.assertEqual(current_manifest["max_tasks"], 48)
            self.assertFalse((current.results / "runs.jsonl").exists())

    def test_initialization_creates_one_private_cross_project_campaign_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "advisory-v1"
            receipt = managed_advisory.initialize_campaign_set(
                state_root,
                profile=codex_profile(),
                prices=None,
                planned_tasks=60,
                max_tasks=150,
                dry_run=False,
            )

            self.assertEqual(
                receipt,
                {
                    "already_initialized": False,
                    "cost_basis": "vendor",
                    "dry_run": False,
                    "schema_version": 1,
                    "vendor": "codex",
                    "workflows": list(WORKFLOWS),
                },
            )
            self.assertEqual(stat.S_IMODE(state_root.stat().st_mode), 0o700)
            self.assertEqual(
                stat.S_IMODE((state_root / "codex-profile.json").stat().st_mode), 0o600
            )
            self.assertEqual(stat.S_IMODE((state_root / "verify-project.py").stat().st_mode), 0o700)
            for workflow in WORKFLOWS:
                selected = managed_advisory.campaign_paths(state_root, "codex", workflow)
                self.assertEqual(stat.S_IMODE(selected.results.stat().st_mode), 0o700)
                manifest = advisory_campaign.load_manifest(selected.campaign)
                self.assertEqual(manifest.get("workflow", "implementation"), workflow)
                self.assertEqual(manifest["cost_basis"], "vendor")
                self.assertIsNone(manifest["prices_sha256"])

    def test_managed_cleanup_reports_partial_lane_progress_without_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "advisory-v1"
            managed_advisory.initialize_campaign_set(
                state_root,
                profile=codex_profile(),
                prices=None,
                planned_tasks=60,
                max_tasks=150,
                dry_run=False,
            )
            stdout = io.StringIO()
            with (
                mock.patch(
                    "weightclass.advisory.managed_advisory.speculative_run.prune_available_lanes",
                    return_value={
                        "lanes_scanned": 10,
                        "busy_lanes": 1,
                        "registered": 2,
                        "removed": 1,
                        "retained": 0,
                    },
                ),
                mock.patch("sys.stdout", stdout),
            ):
                code = managed_advisory.prune_main(
                    [
                        "--state-root",
                        str(state_root),
                        "--vendor",
                        "codex",
                        "--workflow",
                        "implementation",
                    ]
                )

            self.assertEqual(code, 0)
            self.assertEqual(
                json.loads(stdout.getvalue()),
                {
                    "schema_version": 1,
                    "event": "managed_cleanup",
                    "complete": False,
                    "populations": 1,
                    "lanes_scanned": 10,
                    "busy_lanes": 1,
                    "registered": 2,
                    "removed": 1,
                    "retained": 0,
                },
            )
            self.assertNotIn(str(state_root), stdout.getvalue())

    def test_identical_initialization_is_idempotent_and_does_not_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "advisory-v1"
            first = managed_advisory.initialize_campaign_set(
                state_root,
                profile=codex_profile(),
                prices=None,
                planned_tasks=60,
                max_tasks=150,
                dry_run=False,
            )
            profile_path = state_root / "codex-profile.json"
            before = profile_path.stat().st_mtime_ns
            second = managed_advisory.initialize_campaign_set(
                state_root,
                profile=codex_profile(),
                prices=None,
                planned_tasks=60,
                max_tasks=150,
                dry_run=False,
            )

            self.assertFalse(first["already_initialized"])
            self.assertTrue(second["already_initialized"])
            self.assertEqual(profile_path.stat().st_mtime_ns, before)

    def test_conflicting_profile_fails_without_changing_existing_campaigns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "advisory-v1"
            managed_advisory.initialize_campaign_set(
                state_root,
                profile=codex_profile(),
                prices=None,
                planned_tasks=60,
                max_tasks=150,
                dry_run=False,
            )
            profile_path = state_root / "codex-profile.json"
            before = profile_path.read_bytes()
            changed = codex_profile()
            models = changed["models"]
            assert isinstance(models, dict)
            models["cheap"] = "different"

            with self.assertRaisesRegex(managed_advisory.ManagedAdvisoryError, "^$"):
                managed_advisory.initialize_campaign_set(
                    state_root,
                    profile=changed,
                    prices=None,
                    planned_tasks=60,
                    max_tasks=150,
                    dry_run=False,
                )

            self.assertEqual(profile_path.read_bytes(), before)

    def test_symlink_state_root_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "private"
            private.mkdir(mode=0o700)
            linked = root / "advisory-v1"
            linked.symlink_to(private, target_is_directory=True)

            with self.assertRaisesRegex(managed_advisory.ManagedAdvisoryError, "^$"):
                managed_advisory.initialize_campaign_set(
                    linked,
                    profile=codex_profile(),
                    prices=None,
                    planned_tasks=60,
                    max_tasks=150,
                    dry_run=False,
                )

    def test_dry_run_validates_without_creating_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "advisory-v1"
            receipt = managed_advisory.initialize_campaign_set(
                state_root,
                profile=codex_profile(),
                prices=None,
                planned_tasks=60,
                max_tasks=150,
                dry_run=True,
            )

            self.assertTrue(receipt["dry_run"])
            self.assertFalse(state_root.exists())
            with self.assertRaisesRegex(managed_advisory.ManagedAdvisoryError, "^$"):
                managed_advisory.initialize_campaign_set(
                    state_root,
                    profile=codex_profile(),
                    prices=None,
                    planned_tasks=0,
                    max_tasks=150,
                    dry_run=True,
                )

    def test_price_table_and_custom_vendor_are_validated_and_reusable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "advisory-v1"
            prices = root / "prices.json"
            prices.write_text(
                json.dumps(
                    {
                        role: {"input_tokens": 1.0, "output_tokens": 2.0}
                        for role in ("cheap", "advisor", "expensive")
                    }
                ),
                encoding="utf-8",
            )
            receipt = managed_advisory.initialize_campaign_set(
                state_root,
                profile=custom_profile("vendor-x"),
                prices=prices,
                planned_tasks=60,
                max_tasks=150,
                dry_run=False,
            )

            self.assertEqual(receipt["cost_basis"], "price_table")
            status = managed_advisory.doctor(
                state_root,
                vendors=("vendor-x",),
                workflows=WORKFLOWS,
            )
            self.assertTrue(status["campaign_ready"])
            self.assertFalse(status["dispatch_ready"])
            self.assertFalse(status["ready"])
            self.assertEqual(status["lane_count"], 10)


class ManagedAdvisoryOperationTests(unittest.TestCase):
    def test_consult_route_review_does_not_require_campaign_records(self) -> None:
        selected = managed_advisory.CampaignPaths(
            Path("/state/profile"),
            Path("/state/prices"),
            Path("/state/campaign"),
            Path("/state/results"),
        )
        routes = advisory_routes.AdvisoryRoutes(
            ("vendor", "--cheap"),
            ("vendor", "--advisor"),
            ("vendor", "--expensive"),
        )
        profile_sha256 = "sha256:" + "d" * 64
        route_sha256 = "sha256:" + "6" * 64
        with (
            mock.patch.object(
                managed_advisory,
                "_consult_configuration",
                return_value=managed_advisory.ConsultConfiguration(
                    selected, routes, False, profile_sha256, route_sha256
                ),
            ) as consult_configuration,
            mock.patch.object(
                managed_advisory,
                "_configuration",
                side_effect=AssertionError("campaign inspected"),
            ),
        ):
            receipt = managed_advisory.review_payload(
                Path("/state"),
                vendors=("vendor",),
                workflow="review",
                require_campaign=False,
            )

        self.assertFalse(receipt["campaign_bound"])
        reviewed = receipt["routes"]
        assert isinstance(reviewed, list) and isinstance(reviewed[0], dict)
        self.assertEqual(reviewed[0]["profile_sha256"], profile_sha256)
        self.assertEqual(reviewed[0]["route_sha256"], route_sha256)
        rendered_routes = reviewed[0]["routes"]
        assert isinstance(rendered_routes, dict)
        self.assertEqual(rendered_routes["cheap"], ["vendor", "--cheap"])
        consult_configuration.assert_called_once_with(Path("/state"), "vendor", "review")

    def test_output_replay_failure_does_not_change_a_completed_result(self) -> None:
        buffer = io.BytesIO()
        with mock.patch.object(buffer, "write", side_effect=BrokenPipeError):
            managed_advisory._replay_output(buffer, b"bounded receipt")

    def test_doctor_is_task_free_and_returns_no_paths_or_models(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "advisory-v1"
            managed_advisory.initialize_campaign_set(
                state_root,
                profile=codex_profile(),
                prices=None,
                planned_tasks=60,
                max_tasks=150,
                dry_run=False,
            )

            capability = advisory_preflight.CapabilityResult(
                "codex", "ready", "none", "codex-cli test"
            )
            with mock.patch.object(
                advisory_preflight,
                "check_local_capability",
                return_value=capability,
            ):
                status = managed_advisory.doctor(
                    state_root, vendors=("codex",), workflows=WORKFLOWS
                )
            encoded = json.dumps(status, sort_keys=True)

            self.assertTrue(status["ready"])
            self.assertNotIn(str(state_root), encoded)
            self.assertNotIn('"models"', encoded)
            self.assertEqual(
                [item["role"] for item in status["cli"]],
                ["cheap", "advisor", "expensive"],
            )
            self.assertEqual(len(status["availability"]), len(WORKFLOWS))
            self.assertTrue(
                all(item["free"] == 10 and item["busy"] == 0 for item in status["availability"])
            )

    def test_doctor_reports_a_point_in_time_busy_lane(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "advisory-v1"
            managed_advisory.initialize_campaign_set(
                state_root,
                profile=codex_profile(),
                prices=None,
                planned_tasks=60,
                max_tasks=150,
                dry_run=False,
            )
            selected = managed_advisory.campaign_paths(state_root, "codex", "implementation")
            request = advisory_orchestration.LaneRequest(
                "codex",
                selected.results,
                workflow="implementation",
                campaign_path=selected.campaign,
            )
            with advisory_orchestration.acquire_campaign_lanes((request,)):
                status = managed_advisory.doctor(
                    state_root,
                    vendors=("codex",),
                    workflows=("implementation",),
                )

            self.assertEqual(
                status["availability"],
                [
                    {
                        "vendor": "codex",
                        "workflow": "implementation",
                        "free": 9,
                        "busy": 1,
                    }
                ],
            )

    def test_dispatch_started_receipt_contains_no_result_path(self) -> None:
        private_path = Path("/private/PRIVATE-PROJECT-MATERIAL")
        lease = advisory_orchestration.LaneLease(
            "codex",
            "implementation",
            7,
            private_path,
            (),
        )
        receipt = managed_advisory._dispatch_started_receipt("implementation", (lease,))

        self.assertEqual(
            json.loads(receipt),
            {
                "schema_version": 1,
                "event": "managed_dispatch_started",
                "workflow": "implementation",
                "leases": [{"vendor": "codex", "lane_index": 7}],
            },
        )
        self.assertNotIn(str(private_path).encode(), receipt)

    def test_dispatch_progress_receipts_are_closed_and_task_free(self) -> None:
        heartbeat = managed_advisory._dispatch_progress_receipt(
            "research", "claude", "heartbeat", 61
        )
        completed = managed_advisory._dispatch_progress_receipt(
            "research", "claude", "completed", 125
        )

        self.assertEqual(
            json.loads(heartbeat),
            {
                "schema_version": 1,
                "event": "managed_vendor_heartbeat",
                "workflow": "research",
                "vendor": "claude",
                "elapsed_seconds": 61,
            },
        )
        self.assertEqual(json.loads(completed)["event"], "managed_vendor_completed")
        self.assertEqual(
            managed_advisory._dispatch_progress_receipt("research", "PRIVATE TASK", "heartbeat", 1),
            b"",
        )

    def test_preflight_reasons_are_distinct_and_value_free(self) -> None:
        with self.assertRaises(managed_advisory.ManagedPreflightError) as task_error:
            managed_advisory._preflight_task_file(Path("relative-task"))
        self.assertEqual(task_error.exception.code, "managed_task_input_rejected")

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            repo.mkdir()
            verifier = Path(directory) / "verify"
            verifier.write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
            verifier.chmod(0o700)
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
            (repo / "untracked").write_text("dirty\n", encoding="utf-8")
            with self.assertRaises(managed_advisory.ManagedPreflightError) as dirty:
                managed_advisory._preflight_repo(repo, "research", verifier)
            self.assertEqual(dirty.exception.code, "managed_repo_dirty")
            (repo / "untracked").unlink()
            verifier.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            with self.assertRaises(managed_advisory.ManagedPreflightError) as baseline:
                managed_advisory._preflight_repo(repo, "research", verifier)
            self.assertEqual(
                baseline.exception.code,
                "managed_verifier_baseline_rejected",
            )

    def test_dispatch_rejects_missing_confirmation_before_touching_task_or_state(self) -> None:
        secret_named_task = Path("/never/read/PRIVATE-TASK-CONTENT")
        with (
            mock.patch.object(Path, "lstat", side_effect=AssertionError("task was inspected")),
            self.assertRaisesRegex(managed_advisory.ManagedAdvisoryError, "^$"),
        ):
            managed_advisory.dispatch(
                Path("/never/read/state"),
                repo=Path("/never/read/repo"),
                task_file=secret_named_task,
                vendors=("codex",),
                workflow="implementation",
                confirm_task_egress=False,
            )

    def test_dispatch_checks_all_local_roles_before_task_inspection(self) -> None:
        routes = advisory_routes.AdvisoryRoutes(("missing",), ("missing",), ("missing",))
        selected = managed_advisory.CampaignPaths(
            Path("/state/profile"),
            Path("/state/prices"),
            Path("/state/campaign"),
            Path("/state/results"),
        )
        unavailable = advisory_preflight.CapabilityResult(
            "codex", "missing", "executable_missing", None
        )
        with (
            mock.patch.object(
                managed_advisory, "_configuration", return_value=(selected, {}, routes)
            ),
            mock.patch.object(
                advisory_preflight,
                "check_local_capability",
                return_value=unavailable,
            ),
            mock.patch.object(
                managed_advisory,
                "_preflight_task_file",
                side_effect=AssertionError("task was inspected"),
            ),
            self.assertRaises(managed_advisory.ProviderCapabilityError) as raised,
        ):
            managed_advisory.dispatch(
                Path("/state"),
                repo=Path("/repo"),
                task_file=Path("/private/task"),
                vendors=("codex",),
                workflow="review",
                confirm_task_egress=True,
            )

        self.assertEqual(
            (raised.exception.vendor, raised.exception.role, raised.exception.code),
            ("codex", "cheap", "executable_missing"),
        )

    def test_provider_check_is_task_free_and_never_records_a_campaign_sample(self) -> None:
        routes = advisory_routes.AdvisoryRoutes(("fake",), ("fake",), ("fake",))
        selected = managed_advisory.CampaignPaths(
            Path("/state/profile"),
            Path("/state/prices"),
            Path("/state/campaign"),
            Path("/state/results"),
        )
        capability = advisory_preflight.CapabilityResult("codex", "ready", "none", "fake 1")
        child = {
            "exit_code": 0,
            "timed_out": False,
            "seconds": 0.1,
            "tokens": None,
            "usage": None,
            "failure_code": "none",
            "stdout_present": True,
            "stderr_present": False,
        }
        response = managed_advisory._baseline_probe("review")
        assert response is not None
        with (
            mock.patch.object(
                managed_advisory, "_configuration", return_value=(selected, {}, routes)
            ),
            mock.patch.object(advisory_routes, "routes_from_profile", return_value=routes),
            mock.patch.object(
                managed_advisory,
                "_route_capabilities",
                return_value=tuple(
                    (role, capability) for role in ("cheap", "advisor", "expensive")
                ),
            ),
            mock.patch.object(
                speculative_run,
                "run_child",
                return_value=(child, response.decode("utf-8")),
            ) as run_child,
        ):
            receipt = managed_advisory.provider_check(
                Path("/state"),
                vendors=("codex",),
                workflow="review",
                confirm_provider_egress=True,
            )

        self.assertTrue(receipt["ready"])
        self.assertTrue(receipt["task_free"])
        self.assertTrue(receipt["network_used"])
        self.assertTrue(receipt["provider_egress_confirmed"])
        self.assertFalse(receipt["sample_recorded"])
        self.assertEqual(receipt["calls"], 3)
        result_rows = receipt["results"]
        self.assertIsInstance(result_rows, list)
        assert isinstance(result_rows, list)
        self.assertEqual(len(result_rows), 3)
        self.assertEqual(run_child.call_count, 3)

    def test_provider_check_requires_confirmation_before_state_or_network(self) -> None:
        with (
            mock.patch.object(
                managed_advisory,
                "_configuration",
                side_effect=AssertionError("state was inspected"),
            ),
            mock.patch.object(
                speculative_run,
                "run_child",
                side_effect=AssertionError("provider was called"),
            ),
            self.assertRaisesRegex(managed_advisory.ManagedAdvisoryError, "^$"),
        ):
            managed_advisory.provider_check(
                Path("/state"),
                vendors=("codex",),
                workflow="review",
                confirm_provider_egress=False,
            )

    def test_default_state_root_is_platform_local_and_contains_no_profile_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with (
                mock.patch.object(Path, "home", return_value=home),
                mock.patch.object(sys, "platform", "darwin"),
            ):
                selected = managed_advisory.default_state_root()

            self.assertEqual(
                selected,
                home / "Library" / "Application Support" / "weightclass" / "advisory-v1",
            )

    def test_dispatch_allocates_vendors_together_and_never_places_task_content_in_argv(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "advisory-v1"
            for vendor in ("vendor-a", "vendor-b"):
                managed_advisory.initialize_campaign_set(
                    state_root,
                    profile=custom_profile(vendor),
                    prices=None,
                    planned_tasks=60,
                    max_tasks=150,
                    dry_run=False,
                )
            repo = root / "repo"
            repo.mkdir()
            (repo / ".weightclass").mkdir()
            verifier = repo / ".weightclass" / "verify"
            verifier.write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
            verifier.chmod(0o700)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
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
            task_file = root / "task.txt"
            task_content = "PRIVATE-TASK-CONTENT-MUST-NOT-ENTER-ARGV"
            task_file.write_text(task_content, encoding="utf-8")
            task_file.chmod(0o600)
            captured_jobs: list[advisory_parallel.AdvisoryJob] = []

            def complete_jobs(
                jobs: tuple[advisory_parallel.AdvisoryJob, ...],
                *,
                progress: advisory_parallel.ProgressCallback | None = None,
                heartbeat_seconds: float = advisory_parallel.DEFAULT_HEARTBEAT_SECONDS,
            ) -> tuple[advisory_parallel.AdvisoryResult, ...]:
                self.assertGreater(heartbeat_seconds, 0)
                captured_jobs.extend(jobs)
                results: list[advisory_parallel.AdvisoryResult] = []
                for job in jobs:
                    command = list(job.command)
                    output = Path(command[command.index("--out-dir") + 1])
                    campaign = Path(command[command.index("--campaign") + 1])
                    ordinal = int(command[command.index("--sample-ordinal") + 1])
                    manifest = advisory_campaign.load_manifest(campaign)
                    record: dict[str, object] = {
                        "campaign": advisory_campaign.record_binding(manifest, ordinal),
                    }
                    if manifest["schema_version"] == 2:
                        record["workflow"] = manifest["workflow"]
                    with (output / "runs.jsonl").open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(record) + "\n")
                    results.append(advisory_parallel.AdvisoryResult(job.label, 0, b"", b"", True))
                    if progress is not None:
                        progress(job.label, "completed", 1)
                return tuple(results)

            with (
                mock.patch(
                    "weightclass.advisory.managed_advisory.advisory_parallel.run_parallel",
                    side_effect=complete_jobs,
                ),
                mock.patch.object(managed_advisory, "_require_route_capabilities"),
            ):
                code = managed_advisory.dispatch(
                    state_root,
                    repo=repo,
                    task_file=task_file,
                    vendors=("vendor-a", "vendor-b"),
                    workflow="implementation",
                    confirm_task_egress=True,
                )

            self.assertEqual(code, 0)
            self.assertEqual([job.label for job in captured_jobs], ["vendor-a", "vendor-b"])
            self.assertTrue(
                all(
                    job.command[:6]
                    == (
                        sys.executable,
                        "-I",
                        "-c",
                        managed_advisory._RUNNER_BOOTSTRAP,
                        str(managed_advisory.PACKAGE_ROOT),
                        managed_advisory.PACKAGE_VERSION,
                    )
                    for job in captured_jobs
                )
            )
            flattened = "\0".join(argument for job in captured_jobs for argument in job.command)
            self.assertNotIn(task_content, flattened)
            self.assertIn(str(task_file), flattened)

    def test_dispatch_cli_distinguishes_lane_exhaustion_from_campaign_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "advisory-v1"
            managed_advisory.initialize_campaign_set(
                state_root,
                profile=codex_profile(),
                prices=None,
                planned_tasks=60,
                max_tasks=150,
                dry_run=False,
            )
            base_arguments = [
                "--state-root",
                str(state_root),
                "--repo",
                str(Path(directory) / "repo"),
                "--task-file",
                str(Path(directory) / "task"),
                "--vendor",
                "codex",
                "--confirm-task-egress",
            ]
            cases = (
                (
                    advisory_orchestration.LaneUnavailableError(),
                    {"error": "managed_lane_unavailable"},
                ),
                (
                    advisory_orchestration.CampaignCapacityError(),
                    {"error": "managed_campaign_capacity_reached"},
                ),
                (
                    advisory_orchestration.AllocatorUnavailableError(),
                    {"error": "managed_allocator_busy"},
                ),
                (
                    managed_advisory.RunnerVersionChangedError(),
                    {"error": "managed_runner_version_changed"},
                ),
                (
                    managed_advisory.ManagedPreflightError("managed_repo_dirty"),
                    {
                        "error": "managed_dispatch_rejected",
                        "reason_code": "managed_repo_dirty",
                    },
                ),
                (
                    managed_advisory.ManagedPreflightError("managed_verifier_baseline_rejected"),
                    {
                        "error": "managed_dispatch_rejected",
                        "reason_code": "managed_verifier_baseline_rejected",
                    },
                ),
            )
            for error, expected in cases:
                with self.subTest(expected=expected.get("reason_code", expected["error"])):
                    stderr = io.StringIO()
                    with (
                        mock.patch.object(managed_advisory, "dispatch", side_effect=error),
                        mock.patch("sys.stderr", stderr),
                    ):
                        code = managed_advisory.dispatch_main(base_arguments)
                    self.assertEqual(code, 2)
                    self.assertEqual(json.loads(stderr.getvalue()), expected)


class ManagedVerifierTests(unittest.TestCase):
    def test_package_verifier_runs_only_the_committed_workflow_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / ".weightclass").mkdir()
            verifier = repo / ".weightclass" / "verify-review"
            verifier.write_text(
                '#!/bin/sh\nread value\n[ -n "$value" ] && exit 42\nexit 1\n',
                encoding="utf-8",
            )
            verifier.chmod(0o700)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
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
            environment = dict(os.environ)
            environment["WCLASS_ADVISORY_WORKFLOW"] = "review"
            environment["PYTHONPATH"] = str(ROOT / "src")
            completed = subprocess.run(
                [sys.executable, "-m", "weightclass.advisory.managed_verify"],
                cwd=repo,
                input=b"baseline probe\n",
                capture_output=True,
                check=False,
                env=environment,
            )

            self.assertEqual(completed.returncode, 42, completed.stderr.decode())
            verifier.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            changed = subprocess.run(
                [sys.executable, "-m", "weightclass.advisory.managed_verify"],
                cwd=repo,
                input=b"baseline probe\n",
                capture_output=True,
                check=False,
                env=environment,
            )
            self.assertEqual(changed.returncode, 1)


class ManagedAdvisoryCliTests(unittest.TestCase):
    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "weightclass.advisory", *arguments],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
        )

    def test_public_help_exposes_onboarding_without_removing_explicit_run(self) -> None:
        completed = self._run("--help")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        for command in (
            "init",
            "doctor",
            "cli-check",
            "provider-check",
            "consult",
            "dispatch",
            "status",
            "review",
            "run",
        ):
            self.assertIn(command, completed.stdout)

    def test_init_builds_builtin_profile_from_opaque_role_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "advisory-v1"
            completed = self._run(
                "init",
                "--state-root",
                str(state_root),
                "--vendor",
                "codex",
                "--model",
                "cheap=cheap-model",
                "--model",
                "advisor=advisor-model",
                "--model",
                "expensive=expensive-model",
                "--effort",
                "cheap=low",
                "--effort",
                "advisor=high",
                "--effort",
                "expensive=high",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            receipt = json.loads(completed.stdout)
            self.assertEqual(receipt["vendor"], "codex")
            self.assertNotIn(str(state_root), completed.stdout)
            self.assertNotIn("cheap-model", completed.stdout)
            self.assertTrue((state_root / "codex-profile.json").is_file())


if __name__ == "__main__":
    unittest.main()
