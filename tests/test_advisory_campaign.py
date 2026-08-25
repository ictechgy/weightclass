from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS = REPO_ROOT / "src" / "weightclass" / "advisory"
CAMPAIGN_TOOL = TOOLS / "advisory_campaign.py"
RUNNER = TOOLS / "speculative_run.py"
REPORT = TOOLS / "speculative_report.py"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

if TYPE_CHECKING:
    from weightclass.advisory.advisory_campaign import (
        CampaignError,
        CampaignManifest,
        build_manifest,
        campaign_progress,
        canonical_manifest_bytes,
        file_sha256,
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
        file_sha256,
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

    def test_descriptor_bound_reader_rejects_symlinks_and_survives_path_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = root / "selected.bin"
            replacement = root / "replacement.bin"
            symlink = root / "linked.bin"
            selected.write_bytes(b"reviewed-bytes")
            replacement.write_bytes(b"replacement-bytes")
            symlink.symlink_to(replacement)

            with self.assertRaisesRegex(CampaignError, "^$"):
                file_sha256(symlink, 1024)

            original_fstat = os.fstat
            swapped = False

            def replace_after_open(descriptor: int) -> os.stat_result:
                nonlocal swapped
                metadata = original_fstat(descriptor)
                if not swapped:
                    selected.unlink()
                    selected.symlink_to(replacement)
                    swapped = True
                return metadata

            with mock.patch("os.fstat", side_effect=replace_after_open):
                observed = file_sha256(selected, 1024)

        expected = "sha256:" + hashlib.sha256(b"reviewed-bytes").hexdigest()
        self.assertTrue(swapped)
        self.assertEqual(observed, expected)

    def test_fifo_input_fails_closed_without_blocking_before_fstat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory) / "campaign-input"
            os.mkfifo(fifo, 0o600)
            child = (
                "import sys;"
                f"sys.path.insert(0,{str(TOOLS)!r});"
                "from advisory_campaign import CampaignError,file_sha256;"
                "from pathlib import Path;"
                "\ntry:file_sha256(Path(sys.argv[1]),1024)"
                "\nexcept CampaignError:sys.exit(0)"
                "\nsys.exit(1)"
            )

            completed = subprocess.run(
                [sys.executable, "-c", child, str(fifo)],
                capture_output=True,
                check=False,
                text=True,
                timeout=2,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)

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
        with self.assertRaisesRegex(CampaignError, "^campaign_record_ordinal_duplicate$"):
            validate_record_bindings(manifest, duplicate)

        gap = [dict(record) for record in records]
        gap[-1]["campaign"] = record_binding(manifest, 61)
        with self.assertRaisesRegex(CampaignError, "^campaign_record_ordinal_gap$"):
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

    def test_report_blocks_decision_when_campaign_pricing_is_incomplete(self) -> None:
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
            for ordinal in range(1, 13):
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
            infrastructure = json.loads(json.dumps(records[-1]))
            infrastructure["campaign"] = record_binding(manifest, 13)
            infrastructure["cheap"]["accepted"] = False
            infrastructure["cheap"]["failure_kind"] = "infrastructure"
            infrastructure["cheap"]["child"]["usage"] = None
            infrastructure["advice_failure"] = None
            infrastructure["retry"] = None
            infrastructure["escalated"] = False
            infrastructure["expensive"] = None
            records.append(infrastructure)
            first_cheap = records[0]["cheap"]
            assert isinstance(first_cheap, dict)
            first_child = first_cheap["child"]
            assert isinstance(first_child, dict)
            first_usage = first_child["usage"]
            assert isinstance(first_usage, dict)
            first_usage["pricing_error"] = "missing_rate_fields"
            first_usage["priced_fields_missing"] = "cache_read_input_tokens"
            second_cheap = records[1]["cheap"]
            assert isinstance(second_cheap, dict)
            second_child = second_cheap["child"]
            assert isinstance(second_child, dict)
            second_usage = second_child["usage"]
            assert isinstance(second_usage, dict)
            second_usage["pricing_error"] = "\x1b[31mPRIVATE-CONTROL"
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
            legacy = subprocess.run(
                [sys.executable, str(REPORT), "--log", str(log)],
                capture_output=True,
                check=False,
                text=True,
            )
            first_usage.pop("pricing_error")
            first_usage.pop("priced_fields_missing")
            second_usage.pop("pricing_error")
            log.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            clean_infrastructure = subprocess.run(
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
            third_cheap = records[2]["cheap"]
            assert isinstance(third_cheap, dict)
            third_child = third_cheap["child"]
            assert isinstance(third_child, dict)
            third_child["usage"] = None
            log.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            missing_price = subprocess.run(
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
            third_child["usage"] = child(0.3)["usage"]
            third_child["timed_out"] = True
            log.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            campaign_timeout = subprocess.run(
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
            timeout_records = json.loads(json.dumps(records[:2]))
            timeout_cheap = timeout_records[0]["cheap"]
            timeout_cheap["child"]["timed_out"] = True
            for record in timeout_records:
                advisor_config = record.get("advisor")
                if isinstance(advisor_config, dict):
                    advisor_config["advise_on_failure"] = False
                record["advice_failure"] = None
                record["retry"] = None
            log.write_text(
                "".join(json.dumps(record) + "\n" for record in timeout_records),
                encoding="utf-8",
            )
            timeout_only = subprocess.run(
                [sys.executable, str(REPORT), "--log", str(log)],
                capture_output=True,
                check=False,
                text=True,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(legacy.returncode, 0, legacy.stderr)
        self.assertEqual(clean_infrastructure.returncode, 0, clean_infrastructure.stderr)
        self.assertEqual(missing_price.returncode, 0, missing_price.stderr)
        self.assertEqual(campaign_timeout.returncode, 0, campaign_timeout.stderr)
        self.assertEqual(timeout_only.returncode, 0, timeout_only.stderr)
        self.assertIn("캠페인: shape_b", completed.stdout)
        self.assertIn("불완전하거나 유효하지 않은 가격 계산", completed.stdout)
        self.assertIn("missing_rate_fields", completed.stdout)
        self.assertIn("unknown_pricing_error", completed.stdout)
        self.assertNotIn("PRIVATE-CONTROL", completed.stdout)
        self.assertNotIn("\x1b", completed.stdout)
        self.assertNotIn("구간 전체가 손익분기 위", completed.stdout)
        self.assertNotIn("구간 전체가 손익분기 아래", completed.stdout)
        self.assertNotIn("구간 전체가 손익분기 위", legacy.stdout)
        self.assertNotIn("구간 전체가 손익분기 아래", legacy.stdout)
        self.assertIn("불완전하거나 유효하지 않은 가격 계산", legacy.stdout)
        self.assertNotIn("불완전하거나 유효하지 않은 가격 계산", clean_infrastructure.stdout)
        self.assertNotIn("가격이 없는 campaign 실행", clean_infrastructure.stdout)
        self.assertIn("구간 전체가 손익분기 아래", clean_infrastructure.stdout)
        self.assertIn("가격이 없는 campaign 실행", missing_price.stdout)
        self.assertNotIn("구간 전체가 손익분기 아래", missing_price.stdout)
        self.assertIn("타임아웃 campaign 실행", campaign_timeout.stdout)
        self.assertNotIn("구간 전체가 손익분기 아래", campaign_timeout.stdout)
        self.assertIn("CHILD_TIMEOUT", timeout_only.stdout)
        self.assertNotIn("양쪽 비용이 있는 승급 과제를 더 모아야 한다", timeout_only.stdout)


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
            command = [
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
            ]
            unconfirmed = subprocess.run(
                command,
                capture_output=True,
                check=False,
                text=True,
            )
            command.insert(command.index("--verify"), "--confirm-task-egress")
            completed = subprocess.run(command, capture_output=True, check=False, text=True)

        self.assertEqual(unconfirmed.returncode, 2)
        self.assertIn("requires --confirm-task-egress", unconfirmed.stderr)
        self.assertNotIn(private_task_name, unconfirmed.stderr)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("campaign contract mismatch", completed.stderr)
        self.assertNotIn(private_task_name, completed.stderr)

    def test_overlapping_runner_prices_fail_before_task_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.repository(root)
            verify = root / "verify.sh"
            verify.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            verify.chmod(0o700)
            prices = root / "prices.json"
            prices.write_text(
                json.dumps(
                    {
                        "cheap": {"input_tokens": 1.0, "cached_input_tokens": 0.1},
                        "expensive": {"input_tokens": 1.0},
                        "advisor": {"input_tokens": 1.0},
                    }
                ),
                encoding="utf-8",
            )
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
                    "--verify",
                    str(verify),
                    "--prices",
                    str(prices),
                    "--prefer-prices",
                    "--out-dir",
                    str(root / "out"),
                ],
                capture_output=True,
                check=False,
                text=True,
            )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("overlapping token fields", completed.stderr)
        self.assertNotIn(private_task_name, completed.stderr)

    def test_successful_run_pins_contract_and_refuses_duplicate_ordinal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.repository(root)
            verify = root / "verify.sh"
            verify.write_text("#!/bin/sh\ngrep -q changed README.md\n", encoding="utf-8")
            verify.chmod(0o700)
            expected_verify_bytes = verify.read_bytes()
            task = root / "task.txt"
            task.write_text("make the reviewed change", encoding="utf-8")
            task.chmod(0o600)
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
                "--confirm-task-egress",
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
            legacy.remove("--confirm-task-egress")
            unbound = subprocess.run(legacy, capture_output=True, check=False, text=True)
            records = load_bound_records(out / "runs.jsonl")
            pinned = load_manifest(out / "campaign.json")
            staged_verify = out / "campaign-verify"
            staged_verify_bytes = staged_verify.read_bytes()
            staged_verify_mode = staged_verify.stat().st_mode & 0o777

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(pinned, manifest)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["campaign"], record_binding(manifest, 1))
        self.assertEqual(staged_verify_bytes, expected_verify_bytes)
        self.assertEqual(staged_verify_mode, 0o700)
        self.assertEqual(duplicate.returncode, 2)
        self.assertIn("campaign contract mismatch", duplicate.stderr)
        self.assertNotIn("task.txt", duplicate.stderr)
        self.assertEqual(unbound.returncode, 2)
        self.assertIn("campaign-bound", unbound.stderr)
        self.assertNotIn("task.txt", unbound.stderr)


if __name__ == "__main__":
    unittest.main()
