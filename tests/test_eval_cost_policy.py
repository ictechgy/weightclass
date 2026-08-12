import unittest
from pathlib import Path

from weightclass import cli
from weightclass.router import native_route_fingerprint, uses_argv_task_delivery

EVAL_DIRECTORY = Path(__file__).parent / "eval"
EXAMPLES_DIRECTORY = Path(__file__).parent.parent / "examples"
BASELINE_POLICY = EVAL_DIRECTORY / "claude_cost_baseline_policy.json"
CANDIDATE_POLICY = EXAMPLES_DIRECTORY / "claude_cost_focused_policy.json"

COMMON_PREFIX = (
    "claude",
    "--print",
    "--no-session-persistence",
    "--safe-mode",
    "--permission-mode",
    "acceptEdits",
    "--tools",
    "Read,Edit,Glob,Grep",
    "--output-format",
    "json",
)


class CostExperimentPolicyTests(unittest.TestCase):
    def test_policies_bind_only_the_reviewed_low_tier_model_and_effort_change(self) -> None:
        self.assertTrue(BASELINE_POLICY.is_file())
        self.assertTrue(CANDIDATE_POLICY.is_file())

        baseline = cli.load_routing_policy(BASELINE_POLICY)
        candidate = cli.load_routing_policy(CANDIDATE_POLICY)
        baseline_routes = {route.tier: route for route in baseline.routes}
        candidate_routes = {route.tier: route for route in candidate.routes}

        self.assertEqual(baseline_routes["low"].command, COMMON_PREFIX + ("--effort", "medium"))
        self.assertEqual(
            candidate_routes["low"].command,
            COMMON_PREFIX + ("--model", "haiku", "--effort", "low"),
        )
        for tier in ("standard", "high"):
            with self.subTest(tier=tier):
                self.assertEqual(baseline_routes[tier], candidate_routes[tier])
                self.assertEqual(
                    native_route_fingerprint(
                        baseline_routes[tier], baseline.allow_mixed_vendors, baseline.posture
                    ),
                    native_route_fingerprint(
                        candidate_routes[tier], candidate.allow_mixed_vendors, candidate.posture
                    ),
                )

        self.assertNotEqual(
            native_route_fingerprint(
                baseline_routes["low"], baseline.allow_mixed_vendors, baseline.posture
            ),
            native_route_fingerprint(
                candidate_routes["low"], candidate.allow_mixed_vendors, candidate.posture
            ),
        )
        self.assertFalse(uses_argv_task_delivery(baseline_routes["low"].command))
        self.assertFalse(uses_argv_task_delivery(candidate_routes["low"].command))


if __name__ == "__main__":
    unittest.main()
