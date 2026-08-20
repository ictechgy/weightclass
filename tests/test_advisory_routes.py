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
TOOLS = REPO_ROOT / "tools"
ROUTES_TOOL = TOOLS / "advisory_routes.py"
CAMPAIGN_TOOL = TOOLS / "advisory_campaign.py"
RUNNER = TOOLS / "speculative_run.py"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

if TYPE_CHECKING:
    from tools.advisory_routes import (
        AdvisoryRouteError,
        build_routes,
        load_profile,
        profile_sha256,
    )
    from tools.speculative_run import Usage, price_from_tokens
elif ROUTES_TOOL.is_file() and RUNNER.is_file():
    from advisory_routes import (  # noqa: E402
        AdvisoryRouteError,
        build_routes,
        load_profile,
        profile_sha256,
    )
    from speculative_run import Usage, price_from_tokens  # noqa: E402


@unittest.skipUnless(ROUTES_TOOL.is_file(), "repository-only advisory routes unavailable")
class AdvisoryRouteProfileTests(unittest.TestCase):
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
            self.assertEqual(route[-1], "-")
        self.assertEqual(routes.cheap[routes.cheap.index("--sandbox") + 1], "workspace-write")
        self.assertEqual(routes.advisor[routes.advisor.index("--sandbox") + 1], "read-only")
        self.assertEqual(
            routes.expensive[routes.expensive.index("--sandbox") + 1],
            "workspace-write",
        )

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
            self.assertEqual(descriptor["attempt_bound"]["advisor"], 1)

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
        invalid_usage: Usage = {
            "breakdown": {
                "input_tokens": 10,
                "cached_input_tokens": 11,
            },
            "source": "codex-json",
        }
        self.assertIsNone(price_from_tokens(invalid_usage, rates))

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
