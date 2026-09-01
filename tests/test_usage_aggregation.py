from __future__ import annotations

import importlib
import importlib.util
import io
import json
import multiprocessing
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tests.test_native_v3_cli import HostileOneReadStream, observation
from tests.test_native_v3_schema import valid_policy
from weightclass import cli, entrypoint
from weightclass.native_v3_compile import (
    bind_native_observation_v3,
    compile_static_native_policy_v3,
)
from weightclass.native_v3_schema import parse_native_policy_v3


def _record_usage_worker(store: str, count: int) -> None:
    usage = importlib.import_module("weightclass.usage_aggregation")
    dimensions = usage.UsageDimensions("grok", None, "low", "low")
    for _ in range(count):
        usage.record_usage(
            Path(store),
            dimensions,
            child_returncode=0,
            rework=False,
            escalation=False,
        )


class _UnreadableInput(io.StringIO):
    def read(self, *args: object, **kwargs: object) -> str:
        del args, kwargs
        raise AssertionError("usage command read task stdin")


class UsageAggregationTests(unittest.TestCase):
    def test_enable_creates_a_private_empty_aggregate_store(self) -> None:
        """Breaks if opt-in initialization leaks events or creates a shared file."""
        self.assertIsNotNone(importlib.util.find_spec("weightclass.usage_aggregation"))
        usage = importlib.import_module("weightclass.usage_aggregation")

        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "usage-v1.json"
            usage.ensure_usage_store(store)
            payload = json.loads(store.read_text(encoding="ascii"))
            mode = stat.S_IMODE(store.stat().st_mode)

        self.assertEqual(mode, 0o600)
        self.assertEqual(
            payload,
            {
                "aggregate_only": True,
                "baseline": {"counted_tasks": 0, "relative_cost_micros_total": 0, "tasks": 0},
                "buckets": [],
                "coverage": "native_schema_3",
                "schema_version": 2,
                "weights": [],
            },
        )

    def test_enable_rejects_a_shared_parent_directory(self) -> None:
        """Breaks if another local user could replace aggregate state during an update."""
        usage = importlib.import_module("weightclass.usage_aggregation")

        with tempfile.TemporaryDirectory() as directory:
            shared = Path(directory) / "shared"
            shared.mkdir(mode=0o755)
            store = shared / "usage-v1.json"
            with self.assertRaises(usage.UsageAggregationError):
                usage.ensure_usage_store(store)

        self.assertFalse(store.exists())

    def test_interrupted_enable_never_leaves_a_partial_store(self) -> None:
        """Breaks if an initialization failure enables a corrupt zero-byte store."""
        usage = importlib.import_module("weightclass.usage_aggregation")

        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "usage-v1.json"
            with (
                patch("weightclass.usage_aggregation.os.write", side_effect=OSError),
                self.assertRaises(usage.UsageAggregationError),
            ):
                usage.ensure_usage_store(store)

            self.assertFalse(store.exists())

    def test_enable_rejects_even_a_dangling_store_symlink(self) -> None:
        """Breaks if opt-in silently replaces a caller-visible link with router state."""
        usage = importlib.import_module("weightclass.usage_aggregation")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "usage-v1.json"
            store.symlink_to(root / "missing-target.json")
            with self.assertRaises(usage.UsageAggregationError):
                usage.ensure_usage_store(store)

            self.assertTrue(store.is_symlink())

    def test_deeply_nested_store_is_a_redacted_validation_error(self) -> None:
        """Breaks if bounded hostile JSON escapes through a recursion traceback."""
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "usage-v1.json"
            store.write_text("[" * 2_000 + "0" + "]" * 2_000, encoding="ascii")
            store.chmod(0o600)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = cli.main(["usage", "report", "--store", str(store)])

        self.assertEqual(status, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            json.loads(stderr.getvalue()),
            {"error": "invalid_input", "reason_code": "usage_report_failed"},
        )

    def test_over_limit_integer_store_is_a_redacted_validation_error(self) -> None:
        """Breaks if CPython's integer digit limit escapes as a traceback."""
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "usage-v1.json"
            store.write_text('{"schema_version":' + "9" * 10_000 + "}", encoding="ascii")
            store.chmod(0o600)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = cli.main(["usage", "report", "--store", str(store)])

        self.assertEqual(status, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            json.loads(stderr.getvalue()),
            {"error": "invalid_input", "reason_code": "usage_report_failed"},
        )

    def test_duplicate_store_key_is_a_redacted_validation_error(self) -> None:
        """Breaks if aggregate state uses JSON's last-key-wins behavior."""
        usage = importlib.import_module("weightclass.usage_aggregation")
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "usage-v1.json"
            usage.ensure_usage_store(store)
            payload = store.read_text(encoding="ascii")
            duplicates = {
                "top-level": payload.replace(
                    '"schema_version":2',
                    '"schema_version":2,"schema_version":2',
                    1,
                ),
                "nested": payload.replace(
                    '"tasks":0',
                    '"tasks":0,"tasks":0',
                    1,
                ),
            }
            for name, duplicated in duplicates.items():
                with self.subTest(name=name):
                    self.assertNotEqual(duplicated, payload)
                    store.write_text(duplicated, encoding="ascii")
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        status = cli.main(["usage", "report", "--store", str(store)])

                    self.assertEqual(status, 2)
                    self.assertEqual(stdout.getvalue(), "")
                    self.assertEqual(
                        json.loads(stderr.getvalue()),
                        {"error": "invalid_input", "reason_code": "usage_report_failed"},
                    )

    def test_report_aggregates_relative_cost_status_and_self_reported_rework(self) -> None:
        """Breaks if one run becomes an event log or aggregate ratios use inferred pricing."""
        usage = importlib.import_module("weightclass.usage_aggregation")

        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "usage-v1.json"
            usage.ensure_usage_store(store)
            dimensions = usage.UsageDimensions("grok", "grok-mini", "low", "low")
            usage.set_relative_cost_weight(store, "grok", "grok-mini", "low", "0.25")
            usage.record_usage(
                store,
                dimensions,
                child_returncode=0,
                rework=False,
                escalation=False,
            )
            usage.record_usage(
                store,
                dimensions,
                child_returncode=3,
                rework=True,
                escalation=True,
            )
            report = usage.render_usage_report(store)

        capacity = report.pop("capacity")
        onboarding = report.pop("onboarding")
        self.assertEqual(capacity["bucket_count"], 1)
        self.assertEqual(capacity["bucket_limit"], usage.MAX_BUCKETS)
        self.assertEqual(capacity["store_limit_bytes"], usage.MAX_STORE_BYTES)
        self.assertEqual(capacity["status"], "available")
        self.assertGreater(capacity["store_bytes"], 0)
        self.assertEqual(
            onboarding,
            {
                "baseline_weight_pattern": {"effort": "medium", "model": None},
                "configured_baseline_agents": [],
                "historical_unweighted_bucket_count": 0,
                "historical_baseline_evidence": "incomplete",
                "missing_execution_weight_bucket_count": 0,
                "next_action": "configure_baseline_weight",
                "reason_codes": ["no_baseline_weights", "historical_baseline_gap"],
            },
        )

        self.assertEqual(
            report,
            {
                "aggregate_only": True,
                "buckets": [
                    {
                        "agent": "grok",
                        "effort": "low",
                        "escalation_ratio": "0.500000",
                        "escalations": 1,
                        "failed": 1,
                        "lower_weight_ratio": "1.000000",
                        "lower_weight_runs": 2,
                        "model": "grok-mini",
                        "relative_cost_units": "0.500000",
                        "rework_ratio": "0.500000",
                        "reworks": 1,
                        "runs": 2,
                        "runs_per_task": "2.000000",
                        "status_counts": {"exit:0": 1, "exit:3": 1},
                        "succeeded": 1,
                        "tasks": 1,
                        "tier": "low",
                        "unweighted_runs": 0,
                        "weighted_runs": 2,
                    }
                ],
                "claims": {
                    "baseline_is_counterfactual": True,
                    "first_attempts_self_reported": True,
                    "pricing_verified": False,
                    "relative_cost_only": True,
                    "relative_weight_unit_consistency_verified": False,
                    "relative_weights_require_one_common_unit": True,
                    "task_content_recorded": False,
                    "weights_apply_prospectively": True,
                },
                "coverage": "native_schema_3",
                "schema_version": 2,
                "totals": {
                    "baseline_effort": "medium",
                    "baseline_tasks": 0,
                    "escalation_ratio": "0.500000",
                    "escalations": 1,
                    "failed": 1,
                    "lower_weight_ratio": "1.000000",
                    "lower_weight_runs": 2,
                    # 재작업 한 번이 곧 태스크 하나의 두 번째 실행이다. 스키마 1 은
                    # 이 상황을 75% 절감으로 보고했다. 기준선이 실행 수를 따라
                    # 부풀었기 때문이다. 이제는 기준선 가중치가 없으면 기권한다.
                    "relative_cost_baseline_units": None,
                    "relative_cost_savings_ratio": None,
                    "relative_cost_savings_units": None,
                    "relative_cost_units": "0.500000",
                    "rework_ratio": "0.500000",
                    "reworks": 1,
                    "runs": 2,
                    "runs_per_task": "2.000000",
                    "savings_reason_code": "missing_baseline_weight",
                    "status_counts": {"exit:0": 1, "exit:3": 1},
                    "succeeded": 1,
                    "tasks": 1,
                    "unweighted_runs": 0,
                    "weighted_runs": 2,
                },
                "weights": [
                    {
                        "agent": "grok",
                        "effort": "low",
                        "model": "grok-mini",
                        "relative_cost": "0.250000",
                    }
                ],
            },
        )

    def test_usage_cli_enables_reviews_weight_and_reports_without_reading_task(self) -> None:
        """Breaks if local accounting needs task input or hides configured cost assertions."""
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "usage-v1.json"
            original_stdin = sys.stdin
            sys.stdin = _UnreadableInput("PRIVATE TASK")
            try:
                outputs: list[tuple[int, str, str]] = []
                for arguments in (
                    ["usage", "enable", "--store", str(store)],
                    [
                        "usage",
                        "weight",
                        "--store",
                        str(store),
                        "--agent",
                        "grok",
                        "--model",
                        "grok-mini",
                        "--effort",
                        "low",
                        "--relative-cost",
                        "0.25",
                    ],
                    ["usage", "report", "--store", str(store)],
                ):
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        status = cli.main(arguments)
                    outputs.append((status, stdout.getvalue(), stderr.getvalue()))
            finally:
                sys.stdin = original_stdin

        enabled = json.loads(outputs[0][1])
        self.assertEqual(outputs[0][0], 0)
        self.assertEqual(outputs[0][2], "")
        self.assertEqual(
            {
                key: enabled[key]
                for key in ("aggregate_only", "coverage", "enabled", "schema_version")
            },
            {
                "aggregate_only": True,
                "coverage": "native_schema_3",
                "enabled": True,
                "schema_version": 2,
            },
        )
        self.assertEqual(enabled["onboarding"]["next_action"], "configure_baseline_weight")
        self.assertEqual(enabled["capacity"]["status"], "available")
        self.assertEqual(enabled["capacity"]["measurement"], "canonical_current_state")
        self.assertEqual(
            outputs[1],
            (
                0,
                '{"agent": "grok", "effort": "low", "model": "grok-mini", '
                '"relative_cost": "0.250000", "schema_version": 2}\n',
                "",
            ),
        )
        report = json.loads(outputs[2][1])
        self.assertEqual(outputs[2][0], 0)
        self.assertEqual(outputs[2][2], "")
        self.assertEqual(report["totals"]["runs"], 0)
        self.assertEqual(report["totals"]["relative_cost_savings_ratio"], None)
        self.assertEqual(report["totals"]["relative_cost_units"], None)
        self.assertEqual(
            report["weights"],
            [
                {
                    "agent": "grok",
                    "effort": "low",
                    "model": "grok-mini",
                    "relative_cost": "0.250000",
                }
            ],
        )

    def test_invalid_explicit_store_fails_before_task_observation_and_spawn(self) -> None:
        """Breaks if corrupt accounting state can be ignored after a child starts."""
        stream = HostileOneReadStream(b"PRIVATE TASK")
        errors = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = root / "policy.json"
            policy.write_text(json.dumps(valid_policy()), encoding="utf-8")
            store = root / "usage-v1.json"
            store.write_text("PRIVATE CORRUPT STATE", encoding="utf-8")
            store.chmod(0o600)
            with (
                patch.object(sys, "stdin", stream),
                patch.object(sys, "stderr", errors),
                patch("weightclass.cli.validate_runtime_process_context") as context,
                patch("weightclass.cli.observe_executable") as observe,
                patch("weightclass.native_v3_runtime.run_owned_foreground_redacted") as spawn,
            ):
                status = cli.main(
                    [
                        "run",
                        "--policy",
                        str(policy),
                        "--source-vendor",
                        "codex",
                        "--source-profile",
                        "source",
                        "--tier",
                        "low",
                        "--confirm-endpoint-transition",
                        "--ack-route-fingerprint",
                        "reviewed",
                        "--usage-store",
                        str(store),
                    ]
                )

        self.assertEqual(status, 9)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "usage_unavailable"})
        self.assertNotIn("PRIVATE", errors.getvalue())
        self.assertEqual(stream.read_calls, 0)
        context.assert_not_called()
        observe.assert_not_called()
        spawn.assert_not_called()

    def test_invalid_relative_cost_is_value_free_and_does_not_change_store(self) -> None:
        """Breaks if non-finite or over-precise user weights corrupt prior state."""
        usage = importlib.import_module("weightclass.usage_aggregation")
        invalid_values = ("0", "-1", "nan", "inf", "0.1234567", "1000.000001")

        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "usage-v1.json"
            usage.ensure_usage_store(store)
            before = store.read_bytes()
            for relative_cost in invalid_values:
                with self.subTest(relative_cost=relative_cost):
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        status = cli.main(
                            [
                                "usage",
                                "weight",
                                "--store",
                                str(store),
                                "--agent",
                                "grok",
                                "--model",
                                "default",
                                "--effort",
                                "low",
                                "--relative-cost",
                                relative_cost,
                            ]
                        )
                    self.assertEqual(status, 2)
                    self.assertEqual(stdout.getvalue(), "")
                    self.assertEqual(
                        json.loads(stderr.getvalue()),
                        {"error": "invalid_input", "reason_code": "usage_weight_failed"},
                    )
                    self.assertNotIn(relative_cost, stderr.getvalue())
                    self.assertEqual(store.read_bytes(), before)

    def test_onboarding_separates_current_weight_gaps_from_historical_gaps(self) -> None:
        """Breaks if prospective weights are presented as repairing earlier evidence."""
        usage = importlib.import_module("weightclass.usage_aggregation")
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "usage-v1.json"
            usage.ensure_usage_store(store)
            usage.set_relative_cost_weight(store, "grok", None, "medium", "1.0")
            dimensions = usage.UsageDimensions("grok", "grok-mini", "low", "low")
            usage.record_usage(
                store,
                dimensions,
                child_returncode=0,
                rework=False,
                escalation=False,
            )
            before_weight = usage.render_usage_report(store)["onboarding"]
            usage.set_relative_cost_weight(store, "grok", "grok-mini", "low", "0.25")
            after_weight = usage.render_usage_report(store)["onboarding"]

        self.assertEqual(before_weight["next_action"], "configure_execution_weights")
        self.assertEqual(before_weight["missing_execution_weight_bucket_count"], 1)
        self.assertEqual(after_weight["missing_execution_weight_bucket_count"], 0)
        self.assertEqual(after_weight["historical_unweighted_bucket_count"], 1)
        self.assertEqual(after_weight["next_action"], "collect_new_weighted_tasks")
        self.assertIn("historical_unweighted_runs", after_weight["reason_codes"])

    def test_report_warns_before_a_configured_capacity_limit(self) -> None:
        """Breaks if bounded store exhaustion remains invisible until an update fails."""
        usage = importlib.import_module("weightclass.usage_aggregation")
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "usage-v1.json"
            usage.ensure_usage_store(store)
            usage.record_usage(
                store,
                usage.UsageDimensions("grok", None, "low", "low"),
                child_returncode=0,
                rework=False,
                escalation=False,
            )
            with patch.object(usage, "MAX_BUCKETS", 1):
                capacity = usage.render_usage_report(store)["capacity"]

        self.assertEqual(capacity["bucket_count"], 1)
        self.assertEqual(capacity["bucket_utilization_basis_points"], 10_000)
        self.assertEqual(capacity["status"], "near_limit")

    def test_report_warns_at_the_store_byte_threshold(self) -> None:
        """Breaks if the documented 90% canonical-byte boundary is off by one."""
        usage = importlib.import_module("weightclass.usage_aggregation")
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "usage-v1.json"
            usage.ensure_usage_store(store)
            current_bytes = store.stat().st_size
            warning_limit = current_bytes * 10_000 // usage.CAPACITY_WARNING_BASIS_POINTS
            with patch.object(usage, "MAX_STORE_BYTES", warning_limit):
                capacity = usage.render_usage_report(store)["capacity"]

        self.assertGreaterEqual(
            capacity["store_utilization_basis_points"],
            usage.CAPACITY_WARNING_BASIS_POINTS,
        )
        self.assertEqual(capacity["status"], "near_limit")

    def test_onboarding_reaches_collect_and_review_states(self) -> None:
        """Breaks if a complete prospective setup never reaches usable guidance."""
        usage = importlib.import_module("weightclass.usage_aggregation")
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "usage-v1.json"
            usage.ensure_usage_store(store)
            usage.set_relative_cost_weight(store, "grok", None, "medium", "1.0")
            before = usage.render_usage_report(store)["onboarding"]
            usage.record_usage(
                store,
                usage.UsageDimensions("grok", None, "medium", "standard"),
                child_returncode=0,
                rework=False,
                escalation=False,
            )
            after = usage.render_usage_report(store)["onboarding"]

        self.assertEqual(before["configured_baseline_agents"], ["grok"])
        self.assertEqual(before["reason_codes"], ["no_recorded_tasks"])
        self.assertEqual(before["next_action"], "collect_usage")
        self.assertEqual(after["historical_baseline_evidence"], "complete")
        self.assertEqual(after["reason_codes"], [])
        self.assertEqual(after["next_action"], "review_metrics")

    def test_usage_enable_failure_has_a_value_free_operation_reason(self) -> None:
        """Breaks if setup failures require scraping generic prose."""
        usage = importlib.import_module("weightclass.usage_aggregation")
        errors = io.StringIO()
        with (
            patch.object(cli, "ensure_usage_store", side_effect=usage.UsageAggregationError()),
            redirect_stderr(errors),
        ):
            status = cli.main(["usage", "enable", "--store", "/private/usage.json"])

        self.assertEqual(status, 2)
        self.assertEqual(
            json.loads(errors.getvalue()),
            {"error": "invalid_input", "reason_code": "usage_enable_failed"},
        )
        self.assertNotIn("/private", errors.getvalue())

    def test_omitted_weight_model_targets_the_native_default_without_a_sentinel(self) -> None:
        """Breaks if an opaque model literally named default cannot be distinguished."""
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "usage-v1.json"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["usage", "enable", "--store", str(store)]), 0)
            output = io.StringIO()
            with redirect_stdout(output):
                status = cli.main(
                    [
                        "usage",
                        "weight",
                        "--store",
                        str(store),
                        "--agent",
                        "grok",
                        "--effort",
                        "low",
                        "--relative-cost",
                        "0.25",
                    ]
                )

        self.assertEqual(status, 0)
        self.assertEqual(
            json.loads(output.getvalue()),
            {
                "agent": "grok",
                "effort": "low",
                "model": None,
                "relative_cost": "0.250000",
                "schema_version": 2,
            },
        )

    def test_preexecution_task_failure_does_not_increment_aggregates(self) -> None:
        """Breaks if attempted rather than completed schema-3 work is counted."""
        usage = importlib.import_module("weightclass.usage_aggregation")
        first = observation("/opt/grok")
        selected = compile_static_native_policy_v3(
            parse_native_policy_v3(valid_policy()),
            source_vendor="codex",
            source_profile_id="source",
            tier="low",
            purpose="native_route",
        )
        fingerprint = bind_native_observation_v3(selected, first)["route_fingerprint"]
        assert isinstance(fingerprint, str)
        stream = HostileOneReadStream(b"\xffPRIVATE TASK")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = root / "policy.json"
            policy.write_text(json.dumps(valid_policy()), encoding="utf-8")
            store = root / "usage-v1.json"
            usage.ensure_usage_store(store)
            errors = io.StringIO()
            with (
                patch.object(sys, "stdin", stream),
                patch.object(sys, "stderr", errors),
                patch("weightclass.cli.validate_runtime_process_context"),
                patch("weightclass.cli.observe_executable", return_value=first),
                patch("weightclass.native_v3_runtime.run_owned_foreground_redacted") as spawn,
            ):
                status = cli.main(
                    [
                        "run",
                        "--policy",
                        str(policy),
                        "--source-vendor",
                        "codex",
                        "--source-profile",
                        "source",
                        "--tier",
                        "low",
                        "--confirm-endpoint-transition",
                        "--ack-route-fingerprint",
                        fingerprint,
                        "--usage-store",
                        str(store),
                    ]
                )
            report = usage.render_usage_report(store)

        self.assertEqual(status, 2)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "invalid_task"})
        self.assertEqual(report["totals"]["runs"], 0)
        spawn.assert_not_called()

    def test_native_delegation_run_uses_the_same_aggregate_contract(self) -> None:
        """Breaks if lower-agent delegation escapes accounting available to ordinary runs."""
        usage = importlib.import_module("weightclass.usage_aggregation")
        first = observation("/opt/grok")
        selected = compile_static_native_policy_v3(
            parse_native_policy_v3(valid_policy()),
            source_vendor="codex",
            source_profile_id="source",
            tier="low",
            purpose="native_delegation",
        )
        fingerprint = bind_native_observation_v3(selected, first)["route_fingerprint"]
        assert isinstance(fingerprint, str)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = root / "policy.json"
            policy.write_text(json.dumps(valid_policy()), encoding="utf-8")
            store = root / "usage-v1.json"
            usage.ensure_usage_store(store)
            with (
                patch.object(sys, "stdin", HostileOneReadStream()),
                patch("weightclass.cli.validate_runtime_process_context"),
                patch("weightclass.cli.observe_executable", return_value=first),
                patch("weightclass.native_v3_runtime.observe_executable", return_value=first),
                patch(
                    "weightclass.native_v3_runtime.run_owned_foreground_redacted",
                    return_value=0,
                ),
            ):
                status = cli.main(
                    [
                        "delegate",
                        "native",
                        "run",
                        "--policy",
                        str(policy),
                        "--source-vendor",
                        "codex",
                        "--source-profile",
                        "source",
                        "--tier",
                        "low",
                        "--confirm-native-delegation",
                        "--confirm-endpoint-transition",
                        "--ack-route-fingerprint",
                        fingerprint,
                        "--usage-store",
                        str(store),
                        "--usage-escalation",
                    ]
                )
            report = usage.render_usage_report(store)

        self.assertEqual(status, 0)
        self.assertEqual(report["totals"]["runs"], 1)
        self.assertEqual(report["totals"]["escalations"], 1)
        self.assertEqual(report["buckets"][0]["agent"], "grok")

    def test_concurrent_completed_runs_are_not_lost(self) -> None:
        """Breaks if independent foreground invocations overwrite aggregate counters."""
        usage = importlib.import_module("weightclass.usage_aggregation")
        worker_count = 4
        records_per_worker = 20

        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "usage-v1.json"
            usage.ensure_usage_store(store)
            context = multiprocessing.get_context("spawn")
            workers = [
                context.Process(
                    target=_record_usage_worker,
                    args=(str(store), records_per_worker),
                )
                for _ in range(worker_count)
            ]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=20)
            exit_codes = [worker.exitcode for worker in workers]
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                    worker.join(timeout=5)
                worker.close()
            report = usage.render_usage_report(store)

        self.assertEqual(exit_codes, [0] * worker_count)
        self.assertEqual(report["totals"]["runs"], worker_count * records_per_worker)
        self.assertEqual(report["totals"]["succeeded"], worker_count * records_per_worker)

    def test_schema_three_run_records_only_aggregate_dimensions_after_child_finishes(self) -> None:
        """Breaks if accounting stores task data or misses a completed native child."""
        usage = importlib.import_module("weightclass.usage_aggregation")
        first = observation("/opt/grok")
        selected = compile_static_native_policy_v3(
            parse_native_policy_v3(valid_policy()),
            source_vendor="codex",
            source_profile_id="source",
            tier="low",
            purpose="native_route",
        )
        reviewed = bind_native_observation_v3(selected, first)
        fingerprint = reviewed["route_fingerprint"]
        assert isinstance(fingerprint, str)
        task_marker = b"PRIVATE TASK MUST NEVER REACH USAGE STATE"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = root / "policy.json"
            policy.write_text(json.dumps(valid_policy()), encoding="utf-8")
            store = root / "usage-v1.json"
            usage.ensure_usage_store(store)
            usage.set_relative_cost_weight(store, "grok", None, "low", "0.25")
            stream = HostileOneReadStream(task_marker)
            errors = io.StringIO()
            with (
                patch.object(sys, "stdin", stream),
                patch.object(sys, "stderr", errors),
                patch("weightclass.cli.validate_runtime_process_context"),
                patch("weightclass.cli.observe_executable", return_value=first),
                patch("weightclass.native_v3_runtime.observe_executable", return_value=first),
                patch(
                    "weightclass.native_v3_runtime.run_owned_foreground_redacted",
                    return_value=19,
                ),
            ):
                status = cli.main(
                    [
                        "run",
                        "--policy",
                        str(policy),
                        "--source-vendor",
                        "codex",
                        "--source-profile",
                        "source",
                        "--tier",
                        "low",
                        "--confirm-endpoint-transition",
                        "--ack-route-fingerprint",
                        fingerprint,
                        "--usage-store",
                        str(store),
                        "--usage-rework",
                    ]
                )
            raw_state = store.read_bytes()
            report = usage.render_usage_report(store)

        self.assertEqual(status, 7)
        self.assertEqual(stream.read_calls, 1)
        self.assertEqual(
            json.loads(errors.getvalue()),
            {"error": "executor_failed", "executor_exit_code": 19},
        )
        self.assertNotIn(task_marker, raw_state)
        self.assertEqual(
            report["buckets"],
            [
                {
                    "agent": "grok",
                    "effort": "low",
                    "escalation_ratio": "0.000000",
                    "escalations": 0,
                    "failed": 1,
                    "lower_weight_ratio": "1.000000",
                    "lower_weight_runs": 1,
                    "model": None,
                    "relative_cost_units": "0.250000",
                    "rework_ratio": "1.000000",
                    "reworks": 1,
                    "runs": 1,
                    # 재작업만 있고 첫 시도가 없으면 이 저장소가 아는 태스크는 없다.
                    "runs_per_task": None,
                    "status_counts": {"exit:19": 1},
                    "succeeded": 0,
                    "tasks": 0,
                    "tier": "low",
                    "unweighted_runs": 0,
                    "weighted_runs": 1,
                }
            ],
        )
        # 실패한 재작업 한 건이다. 스키마 1 은 이것을 75% 절감으로 보고했다.
        self.assertEqual(report["totals"]["savings_reason_code"], "no_tasks")
        self.assertIsNone(report["totals"]["relative_cost_savings_ratio"])

    def test_cross_vendor_run_prices_the_baseline_on_the_source_vendor(self) -> None:
        """Breaks if a cross-vendor route compares itself with its destination."""
        usage = importlib.import_module("weightclass.usage_aggregation")
        first = observation("/opt/grok")
        selected = compile_static_native_policy_v3(
            parse_native_policy_v3(valid_policy()),
            source_vendor="codex",
            source_profile_id="source",
            tier="low",
            purpose="native_route",
        )
        fingerprint = bind_native_observation_v3(selected, first)["route_fingerprint"]
        assert isinstance(fingerprint, str)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = root / "policy.json"
            policy.write_text(json.dumps(valid_policy()), encoding="utf-8")
            store = root / "usage-v1.json"
            usage.ensure_usage_store(store)
            usage.set_relative_cost_weight(store, "grok", None, "low", "0.25")
            # The old implementation incorrectly chose this destination baseline.
            usage.set_relative_cost_weight(store, "grok", None, "medium", "0.4")
            usage.set_relative_cost_weight(store, "codex", None, "medium", "1.0")
            with (
                patch.object(sys, "stdin", HostileOneReadStream(b"task")),
                patch("weightclass.cli.validate_runtime_process_context"),
                patch("weightclass.cli.observe_executable", return_value=first),
                patch("weightclass.native_v3_runtime.observe_executable", return_value=first),
                patch(
                    "weightclass.native_v3_runtime.run_owned_foreground_redacted",
                    return_value=0,
                ),
            ):
                status = cli.main(
                    [
                        "run",
                        "--policy",
                        str(policy),
                        "--source-vendor",
                        "codex",
                        "--source-profile",
                        "source",
                        "--tier",
                        "low",
                        "--confirm-endpoint-transition",
                        "--ack-route-fingerprint",
                        fingerprint,
                        "--usage-store",
                        str(store),
                    ]
                )
            report = usage.render_usage_report(store)

        self.assertEqual(status, 0)
        self.assertEqual(report["buckets"][0]["agent"], "grok")
        self.assertEqual(report["totals"]["relative_cost_units"], "0.250000")
        self.assertEqual(report["totals"]["relative_cost_baseline_units"], "1.000000")
        self.assertEqual(report["totals"]["relative_cost_savings_ratio"], "0.750000")

    def test_accounting_write_failure_reports_that_the_child_already_completed(self) -> None:
        """Breaks if an accounting error makes callers retry an already executed task."""
        usage = importlib.import_module("weightclass.usage_aggregation")
        first = observation("/opt/grok")
        selected = compile_static_native_policy_v3(
            parse_native_policy_v3(valid_policy()),
            source_vendor="codex",
            source_profile_id="source",
            tier="low",
            purpose="native_route",
        )
        fingerprint = bind_native_observation_v3(selected, first)["route_fingerprint"]
        assert isinstance(fingerprint, str)

        for failure in (usage.UsageAggregationError(), OSError("PRIVATE TASK")):
            with self.subTest(failure_type=type(failure).__name__):
                stream = HostileOneReadStream()
                errors = io.StringIO()
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    policy = root / "policy.json"
                    policy.write_text(json.dumps(valid_policy()), encoding="utf-8")
                    store = root / "usage-v1.json"
                    usage.ensure_usage_store(store)
                    with (
                        patch.object(sys, "stdin", stream),
                        patch.object(sys, "stderr", errors),
                        patch("weightclass.cli.validate_runtime_process_context"),
                        patch("weightclass.cli.observe_executable", return_value=first),
                        patch(
                            "weightclass.native_v3_runtime.observe_executable",
                            return_value=first,
                        ),
                        patch(
                            "weightclass.native_v3_runtime.run_owned_foreground_redacted",
                            return_value=0,
                        ) as spawn,
                        patch("weightclass.cli.record_usage", side_effect=failure),
                    ):
                        status = cli.main(
                            [
                                "run",
                                "--policy",
                                str(policy),
                                "--source-vendor",
                                "codex",
                                "--source-profile",
                                "source",
                                "--tier",
                                "low",
                                "--confirm-endpoint-transition",
                                "--ack-route-fingerprint",
                                fingerprint,
                                "--usage-store",
                                str(store),
                            ]
                        )

                self.assertEqual(status, 9)
                self.assertEqual(
                    json.loads(errors.getvalue()),
                    {
                        "child_completed": True,
                        "child_returncode": 0,
                        "error": "usage_unavailable",
                    },
                )
                self.assertNotIn("PRIVATE", errors.getvalue())
                self.assertEqual(stream.read_calls, 1)
                spawn.assert_called_once()

    def test_usage_default_path_failure_is_redacted(self) -> None:
        cli._load_usage_family()
        errors = io.StringIO()
        with (
            patch.object(sys, "stderr", errors),
            patch.object(
                cli,
                "default_usage_store_path",
                side_effect=RuntimeError("PRIVATE HOME LOOKUP"),
            ),
        ):
            status = cli.main(["usage", "report"])

        self.assertEqual(status, 2)
        self.assertEqual(
            json.loads(errors.getvalue()),
            {"error": "invalid_input", "reason_code": "usage_report_failed"},
        )
        self.assertNotIn("PRIVATE", errors.getvalue())

    def test_schema_three_default_path_failure_is_redacted_before_task_access(self) -> None:
        stream = HostileOneReadStream(b"PRIVATE TASK")
        errors = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            policy = Path(directory) / "policy.json"
            policy.write_text(json.dumps(valid_policy()), encoding="utf-8")
            with (
                patch.object(sys, "stdin", stream),
                patch.object(sys, "stderr", errors),
                patch(
                    "weightclass.usage_aggregation.default_usage_store_path",
                    side_effect=RuntimeError("PRIVATE HOME LOOKUP"),
                ),
                patch("weightclass.cli.validate_runtime_process_context") as context,
                patch("weightclass.cli.observe_executable") as observe,
            ):
                status = cli.main(
                    [
                        "run",
                        "--policy",
                        str(policy),
                        "--source-vendor",
                        "codex",
                        "--source-profile",
                        "source",
                        "--tier",
                        "low",
                        "--confirm-endpoint-transition",
                        "--ack-route-fingerprint",
                        "reviewed",
                    ],
                    use_default_usage_store=True,
                )

        self.assertEqual(status, 9)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "usage_unavailable"})
        self.assertNotIn("PRIVATE", errors.getvalue())
        self.assertEqual(stream.read_calls, 0)
        context.assert_not_called()
        observe.assert_not_called()

    def test_installed_entrypoint_uses_enabled_default_without_library_side_effects(
        self,
    ) -> None:
        """Breaks if normal CLI use is not automatic or in-process tests mutate user state."""
        usage = importlib.import_module("weightclass.usage_aggregation")
        first = observation("/opt/grok")
        selected = compile_static_native_policy_v3(
            parse_native_policy_v3(valid_policy()),
            source_vendor="codex",
            source_profile_id="source",
            tier="low",
            purpose="native_route",
        )
        fingerprint = bind_native_observation_v3(selected, first)["route_fingerprint"]
        assert isinstance(fingerprint, str)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = root / "policy.json"
            policy.write_text(json.dumps(valid_policy()), encoding="utf-8")
            store = root / "usage-v1.json"
            usage.ensure_usage_store(store)
            arguments = [
                "run",
                "--policy",
                str(policy),
                "--source-vendor",
                "codex",
                "--source-profile",
                "source",
                "--tier",
                "low",
                "--confirm-endpoint-transition",
                "--ack-route-fingerprint",
                fingerprint,
            ]
            with (
                patch(
                    "weightclass.usage_aggregation.default_usage_store_path",
                    return_value=store,
                ),
                patch("weightclass.cli.validate_runtime_process_context"),
                patch("weightclass.cli.observe_executable", return_value=first),
                patch("weightclass.native_v3_runtime.observe_executable", return_value=first),
                patch(
                    "weightclass.native_v3_runtime.run_owned_foreground_redacted",
                    return_value=0,
                ),
            ):
                with patch.object(sys, "stdin", HostileOneReadStream()):
                    self.assertEqual(cli.main(arguments), 0)
                self.assertEqual(usage.render_usage_report(store)["totals"]["runs"], 0)
                with patch.object(sys, "stdin", HostileOneReadStream()):
                    self.assertEqual(entrypoint.main(arguments), 0)
                report = usage.render_usage_report(store)

        self.assertEqual(report["totals"]["runs"], 1)
        self.assertEqual(report["buckets"][0]["agent"], "grok")


class RetryHintTests(unittest.TestCase):
    """재시도를 재작업으로 기록하게 만들지 못하면 기준선이 다시 부풀어 오른다."""

    def run_failing_child(self, *extra_arguments: str) -> str:
        usage = importlib.import_module("weightclass.usage_aggregation")
        first = observation("/opt/grok")
        selected = compile_static_native_policy_v3(
            parse_native_policy_v3(valid_policy()),
            source_vendor="codex",
            source_profile_id="source",
            tier="low",
            purpose="native_route",
        )
        reviewed = bind_native_observation_v3(selected, first)
        fingerprint = reviewed["route_fingerprint"]
        assert isinstance(fingerprint, str)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = root / "policy.json"
            policy.write_text(json.dumps(valid_policy()), encoding="utf-8")
            store = root / "usage-v1.json"
            usage.ensure_usage_store(store)
            errors = io.StringIO()
            with (
                patch.object(sys, "stdin", HostileOneReadStream(b"task")),
                patch.object(sys, "stderr", errors),
                patch("weightclass.cli.validate_runtime_process_context"),
                patch("weightclass.cli.observe_executable", return_value=first),
                patch("weightclass.native_v3_runtime.observe_executable", return_value=first),
                patch(
                    "weightclass.native_v3_runtime.run_owned_foreground_redacted",
                    return_value=19,
                ),
            ):
                cli.main(
                    [
                        "run",
                        "--policy",
                        str(policy),
                        "--source-vendor",
                        "codex",
                        "--source-profile",
                        "source",
                        "--tier",
                        "low",
                        "--confirm-endpoint-transition",
                        "--ack-route-fingerprint",
                        fingerprint,
                        "--usage-store",
                        str(store),
                        *extra_arguments,
                    ]
                )
        return errors.getvalue()

    def test_a_failed_first_attempt_asks_for_the_retry_to_be_marked(self) -> None:
        """Breaks if a failed first attempt stops warning about the next invocation."""
        self.assertIn('{"usage_hint": "record_retry_with_usage_rework"}', self.run_failing_child())

    def test_a_failed_rework_does_not_repeat_the_hint(self) -> None:
        """Breaks if the hint fires when the caller already recorded the retry."""
        self.assertNotIn("usage_hint", self.run_failing_child("--usage-rework"))


class CounterfactualBaselineTests(unittest.TestCase):
    """절감은 "라우팅을 안 했다면" 과 비교해야 한다.

    스키마 1 은 기준선을 "실행 1건당 1.0" 으로 두었다. 그래서 절감률이 사용자가
    입력한 가중치의 항등식이었고, 재작업이 기준선까지 부풀려 실패한 저비용
    라우팅이 절감으로 보였다. 여기 있는 사례들이 그 두 결함을 고정한다.
    """

    def setUp(self) -> None:
        self.usage = importlib.import_module("weightclass.usage_aggregation")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Path(self.directory.name) / "usage-v1.json"
        self.usage.ensure_usage_store(self.store)

    def weigh(self, effort: str, relative_cost: str) -> None:
        self.usage.set_relative_cost_weight(self.store, "grok", None, effort, relative_cost)

    def record(self, effort: str, tier: str, *, returncode: int = 0, rework: bool = False) -> None:
        self.usage.record_usage(
            self.store,
            self.usage.UsageDimensions("grok", None, effort, tier),
            child_returncode=returncode,
            rework=rework,
            escalation=False,
        )

    def totals(self) -> dict[str, object]:
        totals = self.usage.render_usage_report(self.store)["totals"]
        assert isinstance(totals, dict)
        return totals

    def test_rework_is_an_overrun_and_not_a_saving(self) -> None:
        """Breaks if retry cost is absorbed into the baseline again.

        10개 태스크를 low(0.3) 로 보내 5개가 실패하고 high(2.0) 로 다시 돌면 실제
        지출은 13.0 단위다. 기본 경로는 10.0 단위이므로 30% 초과다. 스키마 1 은
        같은 이력을 13.3% 절감으로 보고했다.
        """
        self.weigh("low", "0.3")
        self.weigh("medium", "1.0")
        self.weigh("high", "2.0")
        for index in range(10):
            self.record("low", "low", returncode=1 if index < 5 else 0)
        for _ in range(5):
            self.record("high", "high", rework=True)

        totals = self.totals()

        self.assertEqual(totals["runs"], 15)
        self.assertEqual(totals["tasks"], 10)
        self.assertEqual(totals["relative_cost_units"], "13.000000")
        self.assertEqual(totals["relative_cost_baseline_units"], "10.000000")
        self.assertEqual(totals["relative_cost_savings_ratio"], "-0.300000")
        self.assertEqual(totals["savings_reason_code"], "computed")

    def test_a_model_routed_task_is_priced_against_the_model_free_baseline(self) -> None:
        """Breaks if the counterfactual reuses the routed model instead of the default.

        내장 standard 라우트는 모델을 고정하지 않는다. 라우팅된 모델을 기준선에도
        쓰면 모델 라우팅이 개입한 바로 그 경우에 존재한 적 없는 반사실의 가격을
        매기게 된다. 싼 모델로 보냈다면 기준선까지 같이 싸져 절감이 사라진다.
        """
        self.weigh("medium", "1.0")
        self.usage.set_relative_cost_weight(self.store, "grok", "cheap-model", "low", "0.25")
        # 같은 라벨에 medium 가중치도 있어야 예전 조회가 성공한다. 그래야 이 테스트가
        # "기준선 미설정" 이 아니라 "잘못된 기준선" 을 잡는다.
        self.usage.set_relative_cost_weight(self.store, "grok", "cheap-model", "medium", "0.4")
        self.usage.record_usage(
            self.store,
            self.usage.UsageDimensions("grok", "cheap-model", "low", "low"),
            child_returncode=0,
            rework=False,
            escalation=False,
        )

        totals = self.totals()

        self.assertEqual(totals["savings_reason_code"], "computed")
        self.assertEqual(totals["relative_cost_units"], "0.250000")
        # 라우팅된 모델의 medium(0.4) 이 아니라 벤더 기본 경로(1.0) 와 비교해야 한다.
        self.assertEqual(totals["relative_cost_baseline_units"], "1.000000")
        self.assertEqual(totals["relative_cost_savings_ratio"], "0.750000")

    def test_running_the_baseline_route_saves_nothing(self) -> None:
        """Breaks if the fixed route can report a saving against itself."""
        self.weigh("medium", "1.0")
        for _ in range(10):
            self.record("medium", "standard")

        totals = self.totals()

        self.assertEqual(totals["relative_cost_savings_ratio"], "0.000000")
        self.assertEqual(totals["relative_cost_savings_units"], "0.000000")

    def test_abstains_when_the_baseline_weight_is_missing(self) -> None:
        """Breaks if a saving is claimed without a stated alternative to compare against."""
        self.weigh("low", "0.3")
        self.record("low", "low")

        totals = self.totals()

        self.assertEqual(totals["savings_reason_code"], "missing_baseline_weight")
        self.assertIsNone(totals["relative_cost_savings_ratio"])
        self.assertIsNone(totals["relative_cost_baseline_units"])

    def test_abstains_when_any_run_carries_no_weight(self) -> None:
        """Breaks if partial cost evidence is presented as a complete comparison."""
        self.weigh("medium", "1.0")
        self.record("medium", "standard")
        self.record("high", "high")

        totals = self.totals()

        self.assertEqual(totals["savings_reason_code"], "unweighted_runs")
        self.assertIsNone(totals["relative_cost_savings_ratio"])

    def test_an_empty_store_abstains_rather_than_reporting_zero(self) -> None:
        """Breaks if no evidence starts looking like a measured result."""
        self.assertEqual(self.totals()["savings_reason_code"], "no_tasks")

    def test_a_schema_one_store_is_promoted_without_inventing_baseline_evidence(self) -> None:
        """Breaks if migration fabricates a counterfactual the old schema never recorded.

        스키마 1 은 기준선을 기록하지 않았다. 태스크 수만 되살리고 기준선 증거는
        비운다. 그래야 승격된 저장소가 절감을 계산하지 않고 기권한다.
        """
        legacy = {
            "aggregate_only": True,
            "buckets": [
                {
                    "agent": "grok",
                    "effort": "low",
                    "escalations": 0,
                    "failed": 0,
                    "lower_weight_runs": 0,
                    "model": None,
                    "relative_cost_micros_total": 0,
                    "reworks": 1,
                    "runs": 3,
                    "status_counts": {"exit:0": 3},
                    "succeeded": 3,
                    "tier": "low",
                    "unweighted_runs": 3,
                    "weighted_runs": 0,
                }
            ],
            "coverage": "native_schema_3",
            "schema_version": 1,
            "weights": [],
        }
        self.store.write_text(json.dumps(legacy), encoding="ascii")
        self.store.chmod(0o600)

        report = self.usage.render_usage_report(self.store)
        totals = report["totals"]
        assert isinstance(totals, dict)

        self.assertEqual(report["schema_version"], 2)
        self.assertEqual(totals["runs"], 3)
        self.assertEqual(totals["tasks"], 2)
        self.assertEqual(totals["savings_reason_code"], "unweighted_runs")

    def test_a_promoted_store_is_written_back_in_the_current_schema(self) -> None:
        """Breaks if a promoted store keeps reporting under the superseded schema."""
        self.record("low", "low")
        payload = json.loads(self.store.read_text(encoding="ascii"))

        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(
            payload["baseline"],
            {"counted_tasks": 0, "relative_cost_micros_total": 0, "tasks": 1},
        )


if __name__ == "__main__":
    unittest.main()
