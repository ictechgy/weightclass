from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import cast
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "src" / "weightclass" / "advisory"
PORTFOLIO = TOOLS / "advisory_portfolio.py"
CAMPAIGN_ACCEPTANCE = os.environ.get("WCLASS_CAMPAIGN_ACCEPTANCE") == "1"


def load_portfolio() -> types.ModuleType:
    if not PORTFOLIO.is_file():
        raise AssertionError("repository portfolio tool is missing")
    tools = str(TOOLS)
    if tools not in sys.path:
        sys.path.insert(0, tools)
    spec = importlib.util.spec_from_file_location("prospective_advisory_portfolio", PORTFOLIO)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load advisory portfolio")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sample_records() -> list[dict[str, object]]:
    def child(tokens: int, seconds: float, cost: float) -> dict[str, object]:
        return {"tokens": tokens, "seconds": seconds, "usage": {"cost_usd": cost}}

    return [
        {
            "cheap": {"accepted": True, "child": child(100, 10.0, 1.0)},
            "advice_failure": None,
            "retry": None,
            "escalated": False,
            "expensive": None,
        },
        {
            "cheap": {"accepted": False, "child": child(200, 20.0, 2.0)},
            "advice_failure": {
                "empty": False,
                "chars": 100,
                "route_failed": False,
                "child": child(50, 5.0, 0.5),
            },
            "retry": {"accepted": True, "child": child(250, 25.0, 2.5)},
            "escalated": False,
            "expensive": None,
        },
        {
            "cheap": {"accepted": False, "child": child(300, 30.0, 3.0)},
            "advice_failure": {
                "empty": False,
                "chars": 100,
                "route_failed": False,
                "child": child(50, 5.0, 0.5),
            },
            "retry": {"accepted": False, "child": child(350, 35.0, 3.5)},
            "escalated": True,
            "expensive": {"accepted": True, "child": child(1_000, 100.0, 10.0)},
        },
    ]


def first_campaign(result: dict[str, object]) -> dict[str, object]:
    campaigns = result.get("campaigns")
    if not isinstance(campaigns, list) or not campaigns or not isinstance(campaigns[0], dict):
        raise AssertionError("portfolio has no campaign status")
    return cast(dict[str, object], campaigns[0])


@unittest.skipIf(
    not PORTFOLIO.is_file() and not CAMPAIGN_ACCEPTANCE,
    "prospective portfolio implementation unavailable",
)
class AdvisoryPortfolioTests(unittest.TestCase):
    def build_result(
        self,
        records: list[dict[str, object]],
        *,
        arm: str = "shape_b",
        decision_eligible: bool = False,
        reason: str = "planned_tasks_not_reached",
    ) -> dict[str, object]:
        portfolio = load_portfolio()
        entry = portfolio.PortfolioEntry(
            "claude",
            "review",
            Path("/private/PRIVATE-MANIFEST.json"),
            Path("/private/PRIVATE-RESULTS"),
        )
        manifest = {
            "arm": arm,
            "planned_tasks": 60,
            "max_tasks": 150,
            "minimum_advised_failures": 12,
        }
        progress = types.SimpleNamespace(
            usable_tasks=3,
            advised_failures=2,
            decision_eligible=decision_eligible,
            reached_cap=False,
            reason=reason,
        )
        with (
            mock.patch.object(portfolio, "load_manifest", return_value=manifest),
            mock.patch.object(portfolio, "load_merged_lane_records", return_value=records),
            mock.patch.object(portfolio, "campaign_progress", return_value=progress),
        ):
            return cast(dict[str, object], portfolio.build_portfolio((entry,)))

    def test_builds_task_free_campaign_status(self) -> None:
        portfolio = load_portfolio()
        entry = portfolio.PortfolioEntry(
            "claude",
            "review",
            Path("/private/PRIVATE-MANIFEST.json"),
            Path("/private/PRIVATE-RESULTS"),
        )
        manifest = {
            "arm": "shape_b",
            "planned_tasks": 60,
            "max_tasks": 150,
            "minimum_advised_failures": 12,
        }
        progress = types.SimpleNamespace(
            usable_tasks=3,
            advised_failures=2,
            decision_eligible=False,
            reached_cap=False,
            reason="planned_tasks_not_reached",
        )
        with (
            mock.patch.object(portfolio, "load_manifest", return_value=manifest),
            mock.patch.object(portfolio, "load_merged_lane_records", return_value=sample_records()),
            mock.patch.object(portfolio, "campaign_progress", return_value=progress),
        ):
            result = portfolio.build_portfolio((entry,))

        expected = {
            "vendor": "claude",
            "workflow": "review",
            "tasks": 3,
            "planned_tasks": 60,
            "max_tasks": 150,
            "advised_failures": 2,
            "minimum_advised_failures": 12,
            "advice_attempted": 2,
            "advice_delivered": 2,
            "retry_attempted": 2,
            "cheap_passes": 1,
            "cheap_failures": 2,
            "advised_rescues": 1,
            "escalations": 1,
            "both_failed": 0,
            "decision_eligible": False,
            "reached_cap": False,
            "abstention_reason": "planned_tasks_not_reached",
            "next_action": "collect_tasks",
        }
        self.assertEqual(result["schema_version"], 1)
        campaign = first_campaign(cast(dict[str, object], result))
        self.assertEqual({key: campaign[key] for key in expected}, expected)
        rendered = json.dumps(result, sort_keys=True)
        self.assertNotIn("PRIVATE-MANIFEST", rendered)
        self.assertNotIn("PRIVATE-RESULTS", rendered)
        self.assertNotIn("task", rendered.casefold().replace("tasks", ""))

    def test_aggregates_only_fixed_failure_stages_and_result_shapes(self) -> None:
        records = sample_records()
        first_cheap = records[0]["cheap"]
        cheap = records[1]["cheap"]
        retry = records[2]["retry"]
        second_retry = records[1]["retry"]
        third_cheap = records[2]["cheap"]
        expensive = records[2]["expensive"]
        assert all(
            isinstance(attempt, dict)
            for attempt in (first_cheap, cheap, second_retry, third_cheap, retry, expensive)
        )
        assert isinstance(first_cheap, dict)
        assert isinstance(cheap, dict)
        assert isinstance(second_retry, dict)
        assert isinstance(third_cheap, dict)
        assert isinstance(retry, dict)
        assert isinstance(expensive, dict)
        cheap["failure_stage"] = "result"
        cheap["result_shape"] = "prose"
        retry["failure_stage"] = "PRIVATE-TASK-MATERIAL"
        retry["result_shape"] = "PRIVATE-TASK-MATERIAL"
        first_child = first_cheap["child"]
        cheap_child = cheap["child"]
        second_retry_child = second_retry["child"]
        third_cheap_child = third_cheap["child"]
        retry_child = retry["child"]
        expensive_child = expensive["child"]
        assert all(
            isinstance(child, dict)
            for child in (
                first_child,
                cheap_child,
                second_retry_child,
                third_cheap_child,
                retry_child,
                expensive_child,
            )
        )
        assert isinstance(first_child, dict)
        assert isinstance(cheap_child, dict)
        assert isinstance(second_retry_child, dict)
        assert isinstance(third_cheap_child, dict)
        assert isinstance(retry_child, dict)
        assert isinstance(expensive_child, dict)
        first_child["failure_code"] = "model_unavailable"
        cheap_child["failure_code"] = "permission_or_approval"
        second_retry_child["failure_code"] = "account_limit"
        third_cheap_child["failure_code"] = "configuration"
        retry_child["failure_code"] = "result_contract"
        expensive_child["failure_code"] = "PRIVATE-TASK-MATERIAL"

        campaign = first_campaign(self.build_result(records))

        self.assertEqual(campaign["failure_stages"], {"result": 1, "unknown": 1})
        self.assertEqual(campaign["result_shapes"], {"prose": 1, "unknown": 1})
        self.assertEqual(
            campaign["child_failure_codes"],
            {
                "account_limit": 1,
                "configuration": 1,
                "model_unavailable": 1,
                "permission_or_approval": 1,
                "result_contract": 1,
                "unknown": 1,
            },
        )
        self.assertNotIn("PRIVATE", json.dumps(campaign, sort_keys=True))

    def test_reports_metric_and_attempt_denominators_without_changing_the_gate(self) -> None:
        records = sample_records()
        campaign = first_campaign(self.build_result(records))

        self.assertEqual(campaign["metric_records"], 3)
        self.assertEqual(campaign["usable_metric_records"], 3)
        self.assertEqual(
            campaign["attempt_counts"],
            {
                "cheap": 3,
                "advice_first": 0,
                "advice_failure": 2,
                "retry": 2,
                "expensive": 1,
            },
        )
        self.assertEqual(campaign["advised_failures"], 2)

    def test_reports_advice_coverage_and_retry_stage_transitions(self) -> None:
        records = sample_records()
        second_advice = records[1]["advice_failure"]
        third_advice = records[2]["advice_failure"]
        second_cheap = records[1]["cheap"]
        third_cheap = records[2]["cheap"]
        second_retry = records[1]["retry"]
        third_retry = records[2]["retry"]
        assert all(
            isinstance(value, dict)
            for value in (
                second_advice,
                third_advice,
                second_cheap,
                third_cheap,
                second_retry,
                third_retry,
            )
        )
        assert isinstance(second_advice, dict)
        assert isinstance(third_advice, dict)
        assert isinstance(second_cheap, dict)
        assert isinstance(third_cheap, dict)
        assert isinstance(second_retry, dict)
        assert isinstance(third_retry, dict)
        for advice in (second_advice, third_advice):
            advice.update(
                {
                    "empty": False,
                    "truncated": False,
                    "route_failed": False,
                    "envelope_only": False,
                }
            )
        third_advice["chars"] = 80
        third_advice["truncated"] = True
        second_cheap["failure_stage"] = "verification"
        third_cheap["failure_stage"] = "verification"
        third_retry["failure_stage"] = "result"

        campaign = first_campaign(self.build_result(records))

        self.assertEqual(campaign["arm"], "shape_b")
        self.assertEqual(
            campaign["advice_diagnostics"],
            {
                "first": {
                    "records": 0,
                    "chars": {"recorded": 0, "complete": True, "total": 0, "max": 0},
                    "flags": {
                        "empty": {"recorded": 0, "true": 0},
                        "truncated": {"recorded": 0, "true": 0},
                        "route_failed": {"recorded": 0, "true": 0},
                        "envelope_only": {"recorded": 0, "true": 0},
                    },
                },
                "failure": {
                    "records": 2,
                    "chars": {"recorded": 2, "complete": True, "total": 180, "max": 100},
                    "flags": {
                        "empty": {"recorded": 2, "true": 0},
                        "truncated": {"recorded": 2, "true": 1},
                        "route_failed": {"recorded": 2, "true": 0},
                        "envelope_only": {"recorded": 2, "true": 0},
                    },
                },
            },
        )
        self.assertEqual(
            campaign["failure_stages_by_attempt"],
            {"cheap": {"verification": 2}, "retry": {"result": 1}, "expensive": {}},
        )
        self.assertEqual(
            campaign["retry_diagnostics"],
            {
                "attempted": 2,
                "accepted": 1,
                "same_failure_stage": 0,
                "changed_failure_stage": 1,
                "unknown_failure_stage": 0,
                "transitions": {"verification": {"accepted": 1, "result": 1}},
            },
        )
        self.assertEqual(
            campaign["operating_recommendation"],
            {
                "action": "repair_advice_delivery",
                "basis": ["advice_delivery_failure_observed"],
                "diagnostic_only": True,
                "existing_campaign_contract_changed": False,
                "policy_decision_allowed": False,
            },
        )

    def test_healthy_shape_b_delivery_with_retry_rejection_suggests_design_review(self) -> None:
        records = sample_records()
        for record in records:
            advice = record.get("advice_failure")
            if isinstance(advice, dict):
                advice.update(
                    {
                        "empty": False,
                        "truncated": False,
                        "route_failed": False,
                        "envelope_only": False,
                    }
                )

        campaign = first_campaign(self.build_result(records))

        self.assertEqual(
            campaign["operating_recommendation"],
            {
                "action": "review_shape_a_b_design",
                "basis": ["advice_delivery_healthy", "retry_rejections_observed"],
                "diagnostic_only": True,
                "existing_campaign_contract_changed": False,
                "policy_decision_allowed": False,
            },
        )

    def test_advice_diagnostics_do_not_invent_missing_legacy_fields(self) -> None:
        campaign = first_campaign(self.build_result(sample_records()))
        diagnostics = cast(dict[str, object], campaign["advice_diagnostics"])
        failure = cast(dict[str, object], diagnostics["failure"])
        flags = cast(dict[str, object], failure["flags"])

        self.assertEqual(failure["records"], 2)
        self.assertEqual(
            failure["chars"],
            {"recorded": 2, "complete": True, "total": 200, "max": 100},
        )
        self.assertEqual(flags["empty"], {"recorded": 2, "true": 0})
        self.assertEqual(flags["route_failed"], {"recorded": 2, "true": 0})
        self.assertEqual(flags["truncated"], {"recorded": 0, "true": 0})
        self.assertEqual(flags["envelope_only"], {"recorded": 0, "true": 0})

    def test_shape_a_b_reports_first_advice_separately(self) -> None:
        records = sample_records()
        records.append(
            {
                "cheap": {
                    "accepted": False,
                    "failure_kind": "infrastructure",
                    "failure_stage": "execution",
                    "child": {
                        "tokens": 1,
                        "seconds": 1.0,
                        "usage": {"cost_usd": 0.1},
                    },
                },
                "advice_first": {
                    "chars": 42,
                    "empty": False,
                    "truncated": False,
                    "route_failed": False,
                    "envelope_only": False,
                },
                "advice_failure": None,
                "retry": None,
                "escalated": False,
                "expensive": None,
            }
        )

        campaign = first_campaign(self.build_result(records, arm="shape_a_b"))
        diagnostics = cast(dict[str, object], campaign["advice_diagnostics"])
        first = cast(dict[str, object], diagnostics["first"])
        stages = cast(dict[str, object], campaign["failure_stages_by_attempt"])

        self.assertEqual(campaign["arm"], "shape_a_b")
        self.assertEqual(first["records"], 1)
        self.assertEqual(
            first["chars"],
            {"recorded": 1, "complete": True, "total": 42, "max": 42},
        )
        self.assertEqual(stages["cheap"], {"execution": 1, "unknown": 2})

    def test_rejects_duplicate_population_without_value_bearing_error(self) -> None:
        portfolio = load_portfolio()
        entry = portfolio.PortfolioEntry(
            "codex", "design", Path("/private/a.json"), Path("/private/a-results")
        )
        with self.assertRaisesRegex(ValueError, "^$"):
            portfolio.build_portfolio((entry, entry))

    def test_orders_populations_deterministically(self) -> None:
        portfolio = load_portfolio()
        entries = (
            portfolio.PortfolioEntry(
                "codex", "review", Path("/private/c.json"), Path("/private/c-results")
            ),
            portfolio.PortfolioEntry(
                "claude", "design", Path("/private/a.json"), Path("/private/a-results")
            ),
        )
        progress = types.SimpleNamespace(
            usable_tasks=0,
            advised_failures=0,
            decision_eligible=False,
            reached_cap=False,
            reason="planned_tasks_not_reached",
        )
        manifest = {
            "arm": "shape_b",
            "planned_tasks": 60,
            "max_tasks": 150,
            "minimum_advised_failures": 12,
        }
        with (
            mock.patch.object(portfolio, "load_manifest", return_value=manifest),
            mock.patch.object(portfolio, "load_merged_lane_records", return_value=[]),
            mock.patch.object(portfolio, "campaign_progress", return_value=progress),
        ):
            result = portfolio.build_portfolio(entries)
        populations = result["campaigns"]
        self.assertEqual(
            [(item["vendor"], item["workflow"]) for item in populations],
            [("claude", "design"), ("codex", "review")],
        )

    def test_cli_exposes_repeatable_campaign_input(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(PORTFOLIO), "--help"],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--campaign", completed.stdout)

    def test_separates_money_tokens_and_latency(self) -> None:
        result = self.build_result(sample_records())
        campaign = first_campaign(result)
        self.assertEqual(
            campaign["metrics"],
            {
                "money_cost": {
                    "complete": True,
                    "total": 23.0,
                    "by_stage": {
                        "cheap": 6.0,
                        "advice_first": 0.0,
                        "advice_failure": 1.0,
                        "retry": 6.0,
                        "expensive": 10.0,
                    },
                },
                "tokens": {
                    "complete": True,
                    "total": 2_300,
                    "by_stage": {
                        "cheap": 600,
                        "advice_first": 0,
                        "advice_failure": 100,
                        "retry": 600,
                        "expensive": 1_000,
                    },
                },
                "latency_seconds": {
                    "complete": True,
                    "total": 230.0,
                    "by_stage": {
                        "cheap": 60.0,
                        "advice_first": 0.0,
                        "advice_failure": 10.0,
                        "retry": 60.0,
                        "expensive": 100.0,
                    },
                },
            },
        )
        self.assertEqual(campaign["decision_state"], "abstain")
        self.assertEqual(campaign["abstention_reasons"], ["planned_tasks_not_reached"])

    def test_incomplete_metrics_force_abstention_after_sample_floors(self) -> None:
        records = sample_records()
        first = records[0]["cheap"]
        assert isinstance(first, dict)
        child = first["child"]
        assert isinstance(child, dict)
        usage = child["usage"]
        assert isinstance(usage, dict)
        usage.pop("cost_usd")
        child.pop("tokens")
        child.pop("seconds")

        result = self.build_result(records, decision_eligible=True, reason="minimums_met")
        campaign = first_campaign(result)
        self.assertEqual(campaign["decision_state"], "abstain")
        self.assertEqual(
            campaign["abstention_reasons"],
            ["incomplete_cost", "incomplete_tokens", "incomplete_latency"],
        )
        self.assertEqual(campaign["next_action"], "repair_measurement")
        self.assertEqual(campaign["policy_decision_reason"], "portfolio_abstained")
        metrics = campaign.get("metrics")
        assert isinstance(metrics, dict)
        for name in ("money_cost", "tokens", "latency_seconds"):
            metric = metrics.get(name)
            assert isinstance(metric, dict)
            self.assertIsNone(metric.get("total"))

    def test_complete_sample_only_allows_separate_statistical_evaluation(self) -> None:
        result = self.build_result(sample_records(), decision_eligible=True, reason="minimums_met")
        campaign = first_campaign(result)
        self.assertEqual(campaign["decision_state"], "evaluate")
        self.assertEqual(campaign["next_action"], "run_statistical_gate")
        self.assertIs(campaign["policy_decision_allowed"], False)
        self.assertEqual(campaign["policy_decision_reason"], "statistical_gate_required")

    def test_discovers_existing_campaign_populations_without_persisted_config(self) -> None:
        portfolio = load_portfolio()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "claude-profile.json").write_text("{}", encoding="utf-8")
            (root / "codex-profile.json").write_text("{}", encoding="utf-8")
            expected = []
            for vendor, workflow, infix in (
                ("claude", "implementation", ""),
                ("claude", "review", "-review"),
                ("codex", "review", "-review"),
            ):
                manifest = root / f"{vendor}{infix}-shape-b.json"
                results = root / f"{vendor}{infix}-results"
                manifest.write_text("{}", encoding="utf-8")
                manifest.chmod(0o600)
                results.mkdir(mode=0o700)
                expected.append(portfolio.PortfolioEntry(vendor, workflow, manifest, results))
            self.assertEqual(portfolio.discover_campaigns(root), tuple(expected))

    def test_discovery_rejects_half_configured_population(self) -> None:
        portfolio = load_portfolio()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "claude-profile.json").write_text("{}", encoding="utf-8")
            manifest = root / "claude-review-shape-b.json"
            manifest.write_text("{}", encoding="utf-8")
            manifest.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "^$"):
                portfolio.discover_campaigns(root)

    def test_cli_exposes_campaign_directory_discovery(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(PORTFOLIO), "--help"],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--campaign-directory", completed.stdout)


if __name__ == "__main__":
    unittest.main()
