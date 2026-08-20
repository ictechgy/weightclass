from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING, Any

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS = REPO_ROOT / "tools"
CAMPAIGN_TOOL = TOOLS / "advisory_campaign.py"
RUNNER = TOOLS / "speculative_run.py"
REPORT = TOOLS / "speculative_report.py"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

if TYPE_CHECKING:
    from tools.advisory_campaign import (
        CampaignError,
        CampaignManifest,
        build_manifest,
        campaign_progress,
        canonical_manifest_bytes,
        load_bound_records,
        load_manifest,
        record_binding,
        validate_record_bindings,
        validate_run_configuration,
        write_manifest,
    )
elif CAMPAIGN_TOOL.is_file():
    from advisory_campaign import (
        CampaignError,
        CampaignManifest,
        build_manifest,
        campaign_progress,
        canonical_manifest_bytes,
        load_bound_records,
        load_manifest,
        record_binding,
        validate_record_bindings,
        validate_run_configuration,
        write_manifest,
    )


@unittest.skipUnless(CAMPAIGN_TOOL.is_file(), "repository-only campaign tool unavailable")
class AdvisoryCampaignContractTests(unittest.TestCase):
    def files(self, directory: str) -> tuple[Path, Path]:
        root = Path(directory)
        verify = root / "verify.sh"
        verify.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        verify.chmod(0o700)
        prices = root / "prices.json"
        prices.write_text(
            json.dumps(
                {
                    "cheap": {"input_tokens": 0.1},
                    "expensive": {"input_tokens": 1.0},
                    "advisor": {"input_tokens": 1.0},
                }
            ),
            encoding="utf-8",
        )
        return verify, prices

    def manifest(self, directory: str, *, arm: str = "shape_b") -> CampaignManifest:
        verify, prices = self.files(directory)
        return build_manifest(
            arm=arm,
            planned_tasks=60,
            max_tasks=150,
            cost_basis="price_table",
            cheap=["/opt/agent/codex", "--model", "cheap-model"],
            expensive=["/opt/agent/codex", "--model", "strong-model"],
            advisor=["/opt/agent/codex", "--model", "advisor-model"],
            advisor_context="prompt",
            verify=verify,
            prices=prices,
        )

    def test_sealed_manifest_round_trips_without_commands_paths_or_task_material(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.manifest(directory)
            path = Path(directory) / "campaign.json"

            write_manifest(path, manifest)
            loaded = load_manifest(path)
            mode = path.stat().st_mode & 0o777

        self.assertEqual(loaded, manifest)
        self.assertEqual(mode, 0o600)
        encoded = canonical_manifest_bytes(manifest)
        self.assertNotIn(b"cheap-model", encoded)
        self.assertNotIn(os.fsencode(directory), encoded)
        self.assertNotIn(b"PRIVATE-TASK-CONTENT", encoded)

    def test_tampering_duplicates_unknown_fields_and_nonfinite_values_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.manifest(directory)
            path = Path(directory) / "campaign.json"
            write_manifest(path, manifest)

            tampered = dict(manifest)
            tampered["planned_tasks"] = 61
            path.unlink()
            path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(CampaignError, "^$"):
                load_manifest(path)

            path.write_text(
                '{"schema_version":1,"schema_version":1}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CampaignError, "^$"):
                load_manifest(path)

            invalid = dict(manifest)
            invalid["unknown"] = True
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaisesRegex(CampaignError, "^$"):
                load_manifest(path)

            verify = Path(directory) / "verify.sh"
            prices = Path(directory) / "prices.json"
            for malformed_prices in (
                '{"cheap":{"input_tokens":1,"input_tokens":2},'
                '"expensive":{"input_tokens":1},"advisor":{"input_tokens":1}}',
                '{"cheap":{"input_tokens":NaN},'
                '"expensive":{"input_tokens":1},"advisor":{"input_tokens":1}}',
                '{"cheap":{"input_tokens":1},"expensive":{"input_tokens":1}}',
            ):
                with self.subTest(malformed_prices=malformed_prices):
                    prices.write_text(malformed_prices, encoding="utf-8")
                    with self.assertRaisesRegex(CampaignError, "^$"):
                        build_manifest(
                            arm="shape_b",
                            planned_tasks=12,
                            max_tasks=20,
                            cost_basis="price_table",
                            cheap=["codex", "cheap"],
                            expensive=["codex", "strong"],
                            advisor=["codex", "advisor"],
                            advisor_context="prompt",
                            verify=verify,
                            prices=prices,
                        )

            with self.assertRaisesRegex(CampaignError, "^$"):
                build_manifest(
                    arm="shape_b",
                    planned_tasks=12,
                    max_tasks=20,
                    cost_basis="vendor",
                    cheap=["codex", "cheap"],
                    expensive=["claude", "strong"],
                    advisor=["codex", "advisor"],
                    advisor_context="prompt",
                    verify=verify,
                    prices=None,
                )

    def test_run_binding_checks_every_task_free_input_and_preserves_valid_control(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.manifest(directory)
            verify = Path(directory) / "verify.sh"
            prices = Path(directory) / "prices.json"
            common: dict[str, Any] = {
                "cheap": ["/opt/agent/codex", "--model", "cheap-model"],
                "expensive": ["/opt/agent/codex", "--model", "strong-model"],
                "advisor": ["/opt/agent/codex", "--model", "advisor-model"],
                "advise_first": False,
                "advise_on_failure": True,
                "advisor_context": "prompt",
                "verify": verify,
                "prices": prices,
                "prefer_prices": True,
                "sample_ordinal": 1,
            }
            validate_run_configuration(manifest, **common)

            mutations = (
                {"cheap": ["/opt/agent/codex", "--model", "changed"]},
                {"advise_first": True},
                {"advisor_context": "repo"},
                {"prefer_prices": False},
                {"sample_ordinal": 151},
            )
            for mutation in mutations:
                with self.subTest(mutation=mutation):
                    changed = dict(common)
                    changed.update(mutation)
                    with self.assertRaisesRegex(CampaignError, "^$"):
                        validate_run_configuration(manifest, **changed)

            verify.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            with self.assertRaisesRegex(CampaignError, "^$"):
                validate_run_configuration(manifest, **common)

    def test_progress_requires_both_preregistered_minimums_and_unique_ordinals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.manifest(directory)

        records = []
        for ordinal in range(1, 61):
            record: dict[str, object] = {
                "campaign": record_binding(manifest, ordinal),
                "advice_failure": {} if ordinal <= 12 else None,
            }
            records.append(record)
        progress = campaign_progress(manifest, records)
        self.assertTrue(progress.decision_eligible)
        self.assertEqual((progress.usable_tasks, progress.advised_failures), (60, 12))

        self.assertFalse(campaign_progress(manifest, records[:59]).decision_eligible)
        too_few_failures = [dict(record) for record in records]
        too_few_failures[11]["advice_failure"] = None
        self.assertFalse(campaign_progress(manifest, too_few_failures).decision_eligible)

        duplicate = [dict(record) for record in records]
        duplicate[-1]["campaign"] = record_binding(manifest, 1)
        with self.assertRaisesRegex(CampaignError, "^$"):
            validate_record_bindings(manifest, duplicate)

        gap = [dict(record) for record in records]
        gap[-1]["campaign"] = record_binding(manifest, 61)
        with self.assertRaisesRegex(CampaignError, "^$"):
            validate_record_bindings(manifest, gap)

    def test_cli_seals_once_and_refuses_to_overwrite_the_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            verify, prices = self.files(directory)
            output = Path(directory) / "campaign.json"
            command = [
                sys.executable,
                str(CAMPAIGN_TOOL),
                "--arm",
                "shape_b",
                "--planned-tasks",
                "60",
                "--max-tasks",
                "150",
                "--cost-basis",
                "price_table",
                "--cheap",
                "codex --model cheap",
                "--expensive",
                "codex --model strong",
                "--advisor",
                "codex --model advisor",
                "--verify",
                str(verify),
                "--prices",
                str(prices),
                "--output",
                str(output),
            ]

            first = subprocess.run(command, capture_output=True, check=False, text=True)
            second = subprocess.run(command, capture_output=True, check=False, text=True)

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertRegex(first.stdout.strip(), r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(second.returncode, 2)
        self.assertNotIn("codex --model cheap", second.stderr)
        self.assertNotIn(str(verify), second.stderr)

    def test_report_withholds_a_decision_until_the_sealed_minimums_are_met(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            verify = Path(directory) / "verify.sh"
            verify.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            verify.chmod(0o700)
            manifest = build_manifest(
                arm="shape_b",
                planned_tasks=12,
                max_tasks=20,
                cost_basis="vendor",
                cheap=["codex", "--model", "cheap"],
                expensive=["codex", "--model", "strong"],
                advisor=["codex", "--model", "advisor"],
                advisor_context="prompt",
                verify=verify,
                prices=None,
            )
            campaign = Path(directory) / "campaign.json"
            write_manifest(campaign, manifest)
            log = Path(directory) / "runs.jsonl"

            def child(cost: float) -> dict[str, object]:
                return {
                    "exit_code": 0,
                    "timed_out": False,
                    "seconds": 1.0,
                    "tokens": 1,
                    "usage": {
                        "cost_usd": cost,
                        "total_tokens": 1,
                        "breakdown": {"input_tokens": 1},
                        "source": "test-vendor",
                        "cost_origin": "vendor",
                    },
                }

            def attempt(accepted: bool, cost: float) -> dict[str, object]:
                return {
                    "accepted": accepted,
                    "failure_kind": "route",
                    "child_failed_without_changes": False,
                    "child": child(cost),
                    "verify": {
                        "passed": accepted,
                        "exit_code": 0 if accepted else 1,
                        "timed_out": False,
                        "seconds": 1.0,
                    },
                }

            records = []
            for ordinal in range(1, 12):
                records.append(
                    {
                        "campaign": record_binding(manifest, ordinal),
                        "routes": {
                            "cheap": {"executable": "codex", "argv_digest": "c"},
                            "expensive": {"executable": "codex", "argv_digest": "e"},
                        },
                        "advisor": {
                            "route": {"executable": "codex", "argv_digest": "a"},
                            "advise_first": False,
                            "advise_first_applied": False,
                            "advise_on_failure": True,
                            "context": "prompt",
                        },
                        "cheap": attempt(False, 0.3),
                        "advice_first": None,
                        "advice_failure": {"child": child(0.1)},
                        "retry": attempt(False, 0.3),
                        "escalated": True,
                        "expensive": attempt(True, 1.0),
                    }
                )
            log.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(REPORT),
                    "--log",
                    str(log),
                    "--campaign",
                    str(campaign),
                ],
                capture_output=True,
                check=False,
                text=True,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("사전등록 gate 미충족", completed.stdout)
        self.assertIn("sealed campaign", completed.stdout)
        self.assertNotIn("구간 전체가 손익분기 위", completed.stdout)


@unittest.skipUnless(RUNNER.is_file(), "repository-only speculative runner unavailable")
class CampaignRunnerBoundaryTests(unittest.TestCase):
    def repository(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"],
            check=True,
        )
        (repo / "README.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
        return repo

    def test_campaign_mismatch_stops_before_task_access_or_child_launch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.repository(root)
            verify = root / "verify.sh"
            verify.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            verify.chmod(0o700)
            manifest = build_manifest(
                arm="shape_b",
                planned_tasks=12,
                max_tasks=12,
                cost_basis="vendor",
                cheap=["/usr/bin/false"],
                expensive=["/usr/bin/false"],
                advisor=["/usr/bin/false"],
                advisor_context="prompt",
                verify=verify,
                prices=None,
            )
            campaign = root / "campaign.json"
            write_manifest(campaign, manifest)
            private_task_name = "PRIVATE-TASK-MUST-NOT-BE-READ"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "--repo",
                    str(repo),
                    "--task-file",
                    str(root / private_task_name),
                    "--cheap",
                    "/usr/bin/true",
                    "--expensive",
                    "/usr/bin/true",
                    "--advisor",
                    "/usr/bin/true",
                    "--advise-on-failure",
                    "--verify",
                    str(verify),
                    "--campaign",
                    str(campaign),
                    "--sample-ordinal",
                    "1",
                    "--out-dir",
                    str(root / "out"),
                ],
                capture_output=True,
                check=False,
                text=True,
            )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("campaign contract mismatch", completed.stderr)
        self.assertNotIn(private_task_name, completed.stderr)

    def test_successful_run_pins_contract_and_refuses_duplicate_ordinal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.repository(root)
            verify = root / "verify.sh"
            verify.write_text("#!/bin/sh\ngrep -q changed README.md\n", encoding="utf-8")
            verify.chmod(0o700)
            task = root / "task.txt"
            task.write_text("make the reviewed change", encoding="utf-8")
            child = shlex.join(
                [
                    sys.executable,
                    "-c",
                    (
                        "from pathlib import Path;"
                        "path=Path('README.md');"
                        "path.write_text(path.read_text(encoding='utf-8')+'changed\\n',"
                        "encoding='utf-8')"
                    ),
                ]
            )
            advisor = shlex.join([sys.executable, "-c", "print('advice')"])
            manifest = build_manifest(
                arm="shape_b",
                planned_tasks=12,
                max_tasks=12,
                cost_basis="vendor",
                cheap=shlex.split(child),
                expensive=shlex.split(child),
                advisor=shlex.split(advisor),
                advisor_context="prompt",
                verify=verify,
                prices=None,
            )
            campaign = root / "campaign.json"
            write_manifest(campaign, manifest)
            out = root / "out"
            command = [
                sys.executable,
                str(RUNNER),
                "--repo",
                str(repo),
                "--task-file",
                str(task),
                "--cheap",
                child,
                "--expensive",
                child,
                "--advisor",
                advisor,
                "--advise-on-failure",
                "--verify",
                str(verify),
                "--campaign",
                str(campaign),
                "--sample-ordinal",
                "1",
                "--out-dir",
                str(out),
            ]

            first = subprocess.run(command, capture_output=True, check=False, text=True)
            task.unlink()
            duplicate = subprocess.run(command, capture_output=True, check=False, text=True)
            legacy = list(command)
            for flag in ("--campaign", "--sample-ordinal"):
                index = legacy.index(flag)
                del legacy[index : index + 2]
            unbound = subprocess.run(legacy, capture_output=True, check=False, text=True)
            records = load_bound_records(out / "runs.jsonl")
            pinned = load_manifest(out / "campaign.json")

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(pinned, manifest)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["campaign"], record_binding(manifest, 1))
        self.assertEqual(duplicate.returncode, 2)
        self.assertIn("campaign contract mismatch", duplicate.stderr)
        self.assertNotIn("task.txt", duplicate.stderr)
        self.assertEqual(unbound.returncode, 2)
        self.assertIn("campaign-bound", unbound.stderr)
        self.assertNotIn("task.txt", unbound.stderr)


if __name__ == "__main__":
    unittest.main()
