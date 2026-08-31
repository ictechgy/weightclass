from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS = REPO_ROOT / "src" / "weightclass" / "advisory"
ROUTES_TOOL = TOOLS / "advisory_routes.py"
CAMPAIGN_TOOL = TOOLS / "advisory_campaign.py"
RUNNER = TOOLS / "speculative_run.py"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

if TYPE_CHECKING:
    from weightclass.advisory.advisory_campaign import CampaignError, price_table_sha256
    from weightclass.advisory.advisory_routes import (
        AdvisoryRouteError,
        build_routes,
        evidence_routes_digest,
        load_profile,
        profile_sha256,
    )
    from weightclass.advisory.speculative_run import Usage, price_from_tokens
elif ROUTES_TOOL.is_file() and CAMPAIGN_TOOL.is_file() and RUNNER.is_file():
    from advisory_campaign import CampaignError, price_table_sha256  # noqa: E402
    from advisory_routes import (  # noqa: E402
        AdvisoryRouteError,
        build_routes,
        evidence_routes_digest,
        load_profile,
        profile_sha256,
    )
    from speculative_run import Usage, price_from_tokens  # noqa: E402


@unittest.skipUnless(
    ROUTES_TOOL.is_file() and CAMPAIGN_TOOL.is_file() and RUNNER.is_file(),
    "repository-only advisory tools unavailable",
)
class AdvisoryRouteProfileTests(unittest.TestCase):
    def test_evidence_route_digest_binds_profile_workflow_and_exact_argv(self) -> None:
        profile = {
            "schema_version": 2,
            "vendor": "custom",
            "commands": {
                workflow: {
                    role: ["custom", "--role", role] for role in ("cheap", "advisor", "expensive")
                }
                for workflow in ("implementation", "evidence")
            },
        }
        routes = build_routes(profile, read_only_executors=True, evidence_workflow="review")
        review_digest = evidence_routes_digest(profile, routes, "review")
        research_digest = evidence_routes_digest(profile, routes, "research")
        changed_routes = type(routes)(
            (*routes.cheap, "--changed"), routes.advisor, routes.expensive
        )

        self.assertRegex(review_digest, r"^sha256:[0-9a-f]{64}$")
        self.assertNotEqual(review_digest, research_digest)
        self.assertNotEqual(
            review_digest,
            evidence_routes_digest(profile, changed_routes, "review"),
        )

    def write_profile(self, directory: str, vendor: str) -> Path:
        path = Path(directory) / f"{vendor}.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "vendor": vendor,
                    "models": {
                        "cheap": "cheap-model",
                        "advisor": "advisor-model",
                        "expensive": "expensive-model",
                    },
                    "efforts": {
                        "cheap": "high",
                        "advisor": "high",
                        "expensive": "high",
                    },
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_claude_profile_compiles_narrower_advisor_and_json_usage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = load_profile(self.write_profile(directory, "claude"))
            routes = build_routes(profile)

        self.assertEqual(routes.cheap[0], "claude")
        self.assertIn("--no-session-persistence", routes.cheap)
        self.assertIn("--safe-mode", routes.cheap)
        self.assertEqual(routes.cheap[routes.cheap.index("--permission-mode") + 1], "acceptEdits")
        self.assertEqual(routes.advisor[routes.advisor.index("--permission-mode") + 1], "plan")
        self.assertEqual(routes.advisor[routes.advisor.index("--tools") + 1], "Read,Glob,Grep")
        self.assertEqual(routes.expensive[routes.expensive.index("--model") + 1], "expensive-model")
        self.assertEqual(routes.cheap[-2:], ("--effort", "high"))

    def test_codex_profile_compiles_read_only_advisor_and_ephemeral_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = load_profile(self.write_profile(directory, "codex"))
            routes = build_routes(profile)

        for route in routes:
            self.assertEqual(route[:2], ("codex", "exec"))
            self.assertIn("--ephemeral", route)
            self.assertIn("--ignore-user-config", route)
            self.assertIn("--ignore-rules", route)
            self.assertIn("--json", route)
            self.assertIn('model_reasoning_effort="high"', route)
            self.assertEqual(route[-1], "-")
        self.assertEqual(routes.cheap[routes.cheap.index("--sandbox") + 1], "workspace-write")
        self.assertEqual(routes.advisor[routes.advisor.index("--sandbox") + 1], "read-only")
        self.assertEqual(
            routes.expensive[routes.expensive.index("--sandbox") + 1],
            "workspace-write",
        )

        profile["efforts"] = {
            "cheap": "🧠",
            "advisor": "high",
            "expensive": "high",
        }
        unicode_routes = build_routes(profile)
        self.assertIn('model_reasoning_effort="🧠"', unicode_routes.cheap)
        self.assertNotIn("\\ud83e", unicode_routes.cheap[-2])

    def test_profile_is_strict_bounded_duplicate_safe_and_redacts_invalid_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write_profile(directory, "claude")
            digest = profile_sha256(path)
            self.assertRegex(digest, r"^sha256:[0-9a-f]{64}$")

            payload = path.read_text(encoding="utf-8")
            path.write_text(payload.replace('"high"', '"-PRIVATE-MODEL"', 1), encoding="utf-8")
            with self.assertRaisesRegex(AdvisoryRouteError, "^$"):
                load_profile(path)

            original = json.loads(payload)
            for invalid_label in ("-option", "edge\u00a0space", "zero\u200bwidth", "x" * 241):
                with self.subTest(invalid_label=invalid_label[:20]):
                    changed = dict(original)
                    changed["models"] = dict(original["models"])
                    changed["models"]["cheap"] = invalid_label
                    path.write_text(json.dumps(changed), encoding="utf-8")
                    with self.assertRaisesRegex(AdvisoryRouteError, "^$"):
                        load_profile(path)

            path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
            with self.assertRaisesRegex(AdvisoryRouteError, "^$"):
                load_profile(path)

            path.write_text(
                payload.replace('"schema_version": 1', '"schema_version": true'),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(AdvisoryRouteError, "^$"):
                load_profile(path)

            path.write_bytes(b"{" + b"x" * 16_384 + b"}")
            with self.assertRaisesRegex(AdvisoryRouteError, "^$"):
                load_profile(path)

            target = self.write_profile(directory, "codex")
            link = root / "linked.json"
            link.symlink_to(target)
            with self.assertRaisesRegex(AdvisoryRouteError, "^$"):
                load_profile(link)

            reviewed = subprocess.run(
                [sys.executable, str(ROUTES_TOOL), "review", "--profile", str(link)],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(reviewed.returncode, 2)
            self.assertNotIn("codex", reviewed.stderr)

    def test_review_is_task_free_and_campaign_accepts_the_same_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = self.write_profile(directory, "claude")
            reviewed = subprocess.run(
                [sys.executable, str(ROUTES_TOOL), "review", "--profile", str(profile)],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
            descriptor = json.loads(reviewed.stdout)
            self.assertTrue(descriptor["task_egress"])
            self.assertEqual(descriptor["task_delivery"], "stdin")
            self.assertEqual(descriptor["attempt_bound"]["advisor"], 2)
            self.assertEqual(descriptor["attempt_bound"]["total_vendor_children"], 5)

            verify = root / "verify.sh"
            verify.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            verify.chmod(0o700)
            manifest = root / "campaign.json"
            sealed = subprocess.run(
                [
                    sys.executable,
                    str(CAMPAIGN_TOOL),
                    "--arm",
                    "shape_b",
                    "--planned-tasks",
                    "60",
                    "--max-tasks",
                    "150",
                    "--cost-basis",
                    "vendor",
                    "--route-profile",
                    str(profile),
                    "--verify",
                    str(verify),
                    "--output",
                    str(manifest),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(sealed.returncode, 0, sealed.stderr)
            self.assertTrue(manifest.is_file())

            mixed = subprocess.run(
                [
                    sys.executable,
                    str(CAMPAIGN_TOOL),
                    "--arm",
                    "shape_b",
                    "--planned-tasks",
                    "60",
                    "--max-tasks",
                    "150",
                    "--cost-basis",
                    "vendor",
                    "--route-profile",
                    str(profile),
                    "--cheap",
                    "false",
                    "--verify",
                    str(verify),
                    "--output",
                    str(root / "mixed.json"),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(mixed.returncode, 2)
            self.assertIn("cannot be mixed", mixed.stderr)

    def test_sealed_profile_and_runner_compile_identical_routes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = self.write_profile(directory, "claude")
            verify = root / "verify.sh"
            verify.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            verify.chmod(0o700)
            manifest = root / "campaign.json"
            sealed = subprocess.run(
                [
                    sys.executable,
                    str(CAMPAIGN_TOOL),
                    "--arm",
                    "shape_b",
                    "--planned-tasks",
                    "60",
                    "--max-tasks",
                    "150",
                    "--cost-basis",
                    "vendor",
                    "--route-profile",
                    str(profile),
                    "--verify",
                    str(verify),
                    "--output",
                    str(manifest),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(sealed.returncode, 0, sealed.stderr)

            repository = root / "repository"
            repository.mkdir()
            subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
            tracked = repository / "tracked.txt"
            tracked.write_text("baseline\n", encoding="utf-8")
            git_environment = dict(os.environ)
            git_environment.update(
                {
                    "GIT_AUTHOR_NAME": "Test",
                    "GIT_AUTHOR_EMAIL": "test@example.invalid",
                    "GIT_COMMITTER_NAME": "Test",
                    "GIT_COMMITTER_EMAIL": "test@example.invalid",
                }
            )
            subprocess.run(
                ["git", "-C", str(repository), "add", "tracked.txt"],
                check=True,
                env=git_environment,
            )
            subprocess.run(
                ["git", "-C", str(repository), "commit", "--quiet", "-m", "baseline"],
                check=True,
                env=git_environment,
            )

            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_claude = fake_bin / "claude"
            fake_result = json.dumps(
                {
                    "type": "result",
                    "total_cost_usd": 0.01,
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                    "result": "ok",
                },
                separators=(",", ":"),
            )
            fake_claude.write_text(
                "#!/bin/sh\n"
                "sed -n '1,$p' >/dev/null\n"
                "printf '%s\\n' changed > tracked.txt\n"
                f"printf '%s\\n' '{fake_result}'\n",
                encoding="utf-8",
            )
            fake_claude.chmod(0o700)
            task = root / "task.txt"
            task.write_text("PRIVATE-TASK-MATERIAL", encoding="utf-8")
            task.chmod(0o600)
            out_dir = root / "results"
            environment = dict(os.environ)
            environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "--out-dir",
                    str(out_dir),
                    "--repo",
                    str(repository),
                    "--task-file",
                    str(task),
                    "--route-profile",
                    str(profile),
                    "--confirm-task-egress",
                    "--advise-on-failure",
                    "--verify",
                    str(verify),
                    "--campaign",
                    str(manifest),
                    "--sample-ordinal",
                    "1",
                ],
                capture_output=True,
                check=False,
                env=environment,
                text=True,
                timeout=30,
            )

            self.assertEqual(
                completed.returncode,
                0,
                f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
            )
            log = (out_dir / "runs.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("PRIVATE-TASK-MATERIAL", log)
            record = json.loads(log)
            self.assertEqual(record["campaign"]["sample_ordinal"], 1)
            self.assertTrue(record["cheap"]["accepted"])
            self.assertTrue(record["cheap"]["verify"]["passed"])

    def test_profile_execution_requires_explicit_egress_confirmation_before_task_access(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = self.write_profile(directory, "codex")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "--out-dir",
                    str(Path(directory) / "out"),
                    "--route-profile",
                    str(profile),
                    "--advise-on-failure",
                ],
                capture_output=True,
                check=False,
                text=True,
            )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("--confirm-task-egress", completed.stderr)
        self.assertNotIn("cheap-model", completed.stderr)

        with tempfile.TemporaryDirectory() as directory:
            profile = self.write_profile(directory, "codex")
            baseline = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "--out-dir",
                    str(Path(directory) / "out"),
                    "--route-profile",
                    str(profile),
                    "--confirm-task-egress",
                ],
                capture_output=True,
                check=False,
                text=True,
            )
        self.assertEqual(baseline.returncode, 2)
        self.assertIn("required unless --prune", baseline.stderr)
        self.assertNotIn("--advisor does nothing", baseline.stderr)

        with tempfile.TemporaryDirectory() as directory:
            profile = self.write_profile(directory, "codex")
            mixed = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "--out-dir",
                    str(Path(directory) / "out"),
                    "--route-profile",
                    str(profile),
                    "--cheap",
                    "false",
                    "--confirm-task-egress",
                ],
                capture_output=True,
                check=False,
                text=True,
            )
        self.assertEqual(mixed.returncode, 2)
        self.assertIn("cannot be mixed", mixed.stderr)

        with tempfile.TemporaryDirectory() as directory:
            confirm_without_execution_inputs = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "--out-dir",
                    str(Path(directory) / "out"),
                    "--confirm-task-egress",
                ],
                capture_output=True,
                check=False,
                text=True,
            )
        self.assertEqual(confirm_without_execution_inputs.returncode, 2)
        self.assertIn("required unless --prune", confirm_without_execution_inputs.stderr)

    def test_codex_price_table_can_name_disjoint_cached_input_components(self) -> None:
        usage: Usage = {
            "breakdown": {
                "input_tokens": 1_000_000,
                "cached_input_tokens": 600_000,
                "cache_write_input_tokens": 100_000,
                "output_tokens": 200_000,
            },
            "total_tokens": 1_200_000,
            "source": "codex-json",
        }
        rates = {
            "uncached_input_tokens": 2.0,
            "cached_input_tokens": 0.2,
            "output_tokens": 12.0,
        }

        cost = price_from_tokens(usage, rates)
        self.assertIsNotNone(cost)
        assert cost is not None
        self.assertAlmostEqual(cost, 3.32)
        overlapping_usage: Usage = {
            "breakdown": dict(usage["breakdown"]),
            "total_tokens": usage["total_tokens"],
            "source": usage["source"],
        }
        self.assertIsNone(
            price_from_tokens(
                overlapping_usage,
                {"input_tokens": 2.0, "cached_input_tokens": 0.2},
            )
        )
        self.assertEqual(overlapping_usage["pricing_error"], "overlapping_rate_fields")
        invalid_usage: Usage = {
            "breakdown": {
                "input_tokens": 10,
                "cached_input_tokens": 11,
            },
            "source": "codex-json",
        }
        self.assertIsNone(price_from_tokens(invalid_usage, rates))
        self.assertEqual(invalid_usage["pricing_error"], "invalid_input_partition")

        with tempfile.TemporaryDirectory() as directory:
            overlapping_prices = Path(directory) / "prices.json"
            overlapping_prices.write_text(
                json.dumps(
                    {
                        role: {"input_tokens": 2.0, "cached_input_tokens": 0.2}
                        for role in ("cheap", "advisor", "expensive")
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CampaignError, "^$"):
                price_table_sha256(overlapping_prices)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO unavailable")
    def test_fifo_profile_fails_closed_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory) / "profile.json"
            os.mkfifo(fifo, 0o600)
            child = (
                "import sys;"
                f"sys.path.insert(0,{str(TOOLS)!r});"
                "from advisory_routes import AdvisoryRouteError,load_profile;"
                "from pathlib import Path;"
                "\ntry:load_profile(Path(sys.argv[1]))"
                "\nexcept AdvisoryRouteError:sys.exit(0)"
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


if __name__ == "__main__":
    unittest.main()
