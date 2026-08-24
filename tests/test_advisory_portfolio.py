from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import types
import unittest
from pathlib import Path
from typing import cast
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
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
            "advice_failure": {"empty": False, "child": child(50, 5.0, 0.5)},
            "retry": {"accepted": True, "child": child(250, 25.0, 2.5)},
            "escalated": False,
            "expensive": None,
        },
        {
            "cheap": {"accepted": False, "child": child(300, 30.0, 3.0)},
            "advice_failure": {"empty": False, "child": child(50, 5.0, 0.5)},
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
        manifest = {"planned_tasks": 60, "max_tasks": 150, "minimum_advised_failures": 12}
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
        manifest = {"planned_tasks": 60, "max_tasks": 150, "minimum_advised_failures": 12}
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
        manifest = {"planned_tasks": 60, "max_tasks": 150, "minimum_advised_failures": 12}
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
        metrics = campaign.get("metrics")
        assert isinstance(metrics, dict)
        for name in ("money_cost", "tokens", "latency_seconds"):
            metric = metrics.get(name)
            assert isinstance(metric, dict)
            self.assertIsNone(metric.get("total"))


if __name__ == "__main__":
    unittest.main()
