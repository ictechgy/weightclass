from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import types
import unittest
from pathlib import Path
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
    return [
        {
            "cheap": {"accepted": True},
            "advice_failure": None,
            "retry": None,
            "escalated": False,
            "expensive": None,
        },
        {
            "cheap": {"accepted": False},
            "advice_failure": {"empty": False},
            "retry": {"accepted": True},
            "escalated": False,
            "expensive": None,
        },
        {
            "cheap": {"accepted": False},
            "advice_failure": {"empty": False},
            "retry": {"accepted": False},
            "escalated": True,
            "expensive": {"accepted": True},
        },
    ]


@unittest.skipIf(
    not PORTFOLIO.is_file() and not CAMPAIGN_ACCEPTANCE,
    "prospective portfolio implementation unavailable",
)
class AdvisoryPortfolioTests(unittest.TestCase):
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

        self.assertEqual(
            result,
            {
                "schema_version": 1,
                "campaigns": [
                    {
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
                ],
            },
        )
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


if __name__ == "__main__":
    unittest.main()
