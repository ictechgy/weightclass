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
                "buckets": [],
                "coverage": "native_schema_3",
                "schema_version": 1,
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
        self.assertEqual(json.loads(stderr.getvalue()), {"error": "invalid_input"})

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
                        "relative_cost_baseline_units": "2.000000",
                        "relative_cost_savings_ratio": "0.750000",
                        "relative_cost_savings_units": "1.500000",
                        "relative_cost_units": "0.500000",
                        "rework_ratio": "0.500000",
                        "reworks": 1,
                        "runs": 2,
                        "status_counts": {"exit:0": 1, "exit:3": 1},
                        "succeeded": 1,
                        "tier": "low",
                        "unweighted_runs": 0,
                        "weighted_runs": 2,
                    }
                ],
                "claims": {
                    "pricing_verified": False,
                    "relative_cost_only": True,
                    "rework_self_reported": True,
                    "task_content_recorded": False,
                    "weights_apply_prospectively": True,
                },
                "coverage": "native_schema_3",
                "schema_version": 1,
                "totals": {
                    "escalation_ratio": "0.500000",
                    "escalations": 1,
                    "failed": 1,
                    "lower_weight_ratio": "1.000000",
                    "lower_weight_runs": 2,
                    "relative_cost_baseline_units": "2.000000",
                    "relative_cost_savings_ratio": "0.750000",
                    "relative_cost_savings_units": "1.500000",
                    "relative_cost_units": "0.500000",
                    "rework_ratio": "0.500000",
                    "reworks": 1,
                    "runs": 2,
                    "status_counts": {"exit:0": 1, "exit:3": 1},
                    "succeeded": 1,
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

        self.assertEqual(
            outputs[0],
            (
                0,
                '{"aggregate_only": true, "coverage": "native_schema_3", '
                '"enabled": true, "schema_version": 1}\n',
                "",
            ),
        )
        self.assertEqual(
            outputs[1],
            (
                0,
                '{"agent": "grok", "effort": "low", "model": "grok-mini", '
                '"relative_cost": "0.250000", "schema_version": 1}\n',
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
                    self.assertEqual(json.loads(stderr.getvalue()), {"error": "invalid_input"})
                    self.assertNotIn(relative_cost, stderr.getvalue())
                    self.assertEqual(store.read_bytes(), before)

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
                "schema_version": 1,
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
                    "relative_cost_baseline_units": "1.000000",
                    "relative_cost_savings_ratio": "0.750000",
                    "relative_cost_savings_units": "0.750000",
                    "relative_cost_units": "0.250000",
                    "rework_ratio": "1.000000",
                    "reworks": 1,
                    "runs": 1,
                    "status_counts": {"exit:19": 1},
                    "succeeded": 0,
                    "tier": "low",
                    "unweighted_runs": 0,
                    "weighted_runs": 1,
                }
            ],
        )

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
                    {"child_completed": True, "error": "usage_unavailable"},
                )
                self.assertNotIn("PRIVATE", errors.getvalue())
                self.assertEqual(stream.read_calls, 1)
                spawn.assert_called_once()

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


if __name__ == "__main__":
    unittest.main()
