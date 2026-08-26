from __future__ import annotations

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
    managed_advisory,
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

    def test_only_claude_evidence_paths_are_generation_versioned(self) -> None:
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
            self.assertTrue(status["ready"])
            self.assertEqual(status["lane_count"], 10)


class ManagedAdvisoryOperationTests(unittest.TestCase):
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

            status = managed_advisory.doctor(state_root, vendors=("codex",), workflows=WORKFLOWS)
            encoded = json.dumps(status, sort_keys=True)

            self.assertTrue(status["ready"])
            self.assertNotIn(str(state_root), encoded)
            self.assertNotIn("cheap", encoded)
            self.assertNotIn("advisor", encoded)
            self.assertNotIn("expensive", encoded)
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
            ) -> tuple[advisory_parallel.AdvisoryResult, ...]:
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
                return tuple(results)

            with mock.patch(
                "weightclass.advisory.managed_advisory.advisory_parallel.run_parallel",
                side_effect=complete_jobs,
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
                    "managed_lane_unavailable",
                ),
                (
                    advisory_orchestration.CampaignCapacityError(),
                    "managed_campaign_capacity_reached",
                ),
                (
                    advisory_orchestration.AllocatorUnavailableError(),
                    "managed_allocator_busy",
                ),
            )
            for error, expected in cases:
                with self.subTest(expected=expected):
                    stderr = io.StringIO()
                    with (
                        mock.patch.object(managed_advisory, "dispatch", side_effect=error),
                        mock.patch("sys.stderr", stderr),
                    ):
                        code = managed_advisory.dispatch_main(base_arguments)
                    self.assertEqual(code, 2)
                    self.assertEqual(json.loads(stderr.getvalue())["error"], expected)


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
        for command in ("init", "doctor", "dispatch", "status", "review", "run"):
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
