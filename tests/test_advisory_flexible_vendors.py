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
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
ROUTES = TOOLS / "advisory_routes.py"
RUNNER = TOOLS / "speculative_run.py"
CAMPAIGN_ACCEPTANCE = os.environ.get("WCLASS_CAMPAIGN_ACCEPTANCE") == "1"


def load_module(path: Path, name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def profile(vendor: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "vendor": vendor,
        "models": {"cheap": "c", "advisor": "a", "expensive": "e"},
        "efforts": {"cheap": "low", "advisor": "high", "expensive": "high"},
    }


def custom_profile(command: list[str]) -> dict[str, object]:
    roles = {role: list(command) for role in ("cheap", "advisor", "expensive")}
    return {
        "schema_version": 2,
        "vendor": "acme-cli",
        "commands": {"implementation": roles, "evidence": roles},
    }


@unittest.skipUnless(CAMPAIGN_ACCEPTANCE, "prospective flexible-vendor acceptance")
class FlexibleVendorRouteTests(unittest.TestCase):
    def test_schema_one_adds_agy_and_grok_without_changing_existing_digests(self) -> None:
        routes = load_module(ROUTES, "prospective_flexible_routes")
        self.assertEqual(
            routes.profile_digest(profile("claude")),
            "sha256:9797735c8a09814e97bac2c29851ff624982fa6772508f5f11ee19ec238eaa67",
        )
        self.assertEqual(
            routes.profile_digest(profile("codex")),
            "sha256:69cf38b6445867343065dbc9e1f47f59d57f4ef08819420b109e32049c9ce2f2",
        )

        agy = routes.build_routes(profile("agy"))
        self.assertEqual(routes.command_task_delivery(agy.cheap), "argv")
        self.assertEqual(agy.cheap[-2:], ("--print", "{{task}}"))
        self.assertEqual(agy.cheap[agy.cheap.index("--mode") + 1], "accept-edits")
        self.assertEqual(agy.advisor[agy.advisor.index("--mode") + 1], "plan")
        agy_evidence = routes.build_routes(profile("agy"), read_only_executors=True)
        for command in agy_evidence:
            self.assertEqual(command[command.index("--mode") + 1], "plan")

        grok = routes.build_routes(profile("grok"))
        self.assertEqual(routes.command_task_delivery(grok.cheap), "file")
        self.assertEqual(grok.cheap[-2:], ("--prompt-file", "{{task_file}}"))
        self.assertEqual(
            grok.cheap[grok.cheap.index("--permission-mode") + 1], "acceptEdits"
        )
        self.assertEqual(grok.advisor[grok.advisor.index("--permission-mode") + 1], "plan")
        self.assertIn("--no-subagents", grok.cheap)
        self.assertIn("--disable-web-search", grok.cheap)

    def test_schema_two_binds_exact_command_matrices_and_delivery(self) -> None:
        routes = load_module(ROUTES, "prospective_custom_routes")
        implementation = custom_profile(["acme", "--prompt-file", "{{task_file}}"])
        built = routes.build_routes(implementation)
        self.assertEqual(built.cheap, ("acme", "--prompt-file", "{{task_file}}"))
        self.assertEqual(routes.command_task_delivery(built.cheap), "file")
        self.assertEqual(
            routes.build_routes(implementation, read_only_executors=True),
            built,
        )

        for invalid in (
            ["{{task}}"],
            ["acme", "{{task}}", "{{task_file}}"],
            ["acme", "{{task}}", "{{task}}"],
            ["acme", "prefix={{task}}"],
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(routes.AdvisoryRouteError, "^$"):
                    routes.build_routes(custom_profile(invalid))

    def test_profile_review_discloses_uniform_and_mixed_task_delivery(self) -> None:
        routes = load_module(ROUTES, "prospective_review_routes")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            uniform = root / "agy.json"
            uniform.write_text(json.dumps(profile("agy")), encoding="utf-8")
            reviewed = subprocess.run(
                [sys.executable, str(ROUTES), "review", "--profile", str(uniform)],
                capture_output=True,
                check=False,
                text=True,
            )
            value = json.loads(reviewed.stdout)
            self.assertEqual(value["task_delivery"], "argv")
            self.assertTrue(value["task_process_exposure"])

            mixed_value = custom_profile(["acme"])
            commands = mixed_value["commands"]
            assert isinstance(commands, dict)
            implementation = commands["implementation"]
            assert isinstance(implementation, dict)
            implementation["advisor"] = ["acme", "{{task}}"]
            mixed = root / "mixed.json"
            mixed.write_text(json.dumps(mixed_value), encoding="utf-8")
            reviewed = subprocess.run(
                [sys.executable, str(ROUTES), "review", "--profile", str(mixed)],
                capture_output=True,
                check=False,
                text=True,
            )
            value = json.loads(reviewed.stdout)
            self.assertEqual(
                value["task_delivery"],
                {"cheap": "stdin", "advisor": "argv", "expensive": "stdin"},
            )
            self.assertTrue(value["task_process_exposure"])


@unittest.skipUnless(CAMPAIGN_ACCEPTANCE, "prospective flexible-vendor acceptance")
class FlexibleVendorRunnerTests(unittest.TestCase):
    def test_run_child_materializes_stdin_argv_and_private_file_then_cleans_it(self) -> None:
        runner = load_module(RUNNER, "prospective_flexible_runner")
        task = "PRIVATE-TASK-MARKER"
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            stdin_program = "import json,sys;print(json.dumps({'task':sys.stdin.read()}))"
            child, stdout = runner.run_child(
                [sys.executable, "-c", stdin_program], workspace, task
            )
            self.assertEqual(child["exit_code"], 0)
            self.assertEqual(json.loads(stdout)["task"], task)

            argv_program = "import json,sys;print(json.dumps({'task':sys.argv[1]}))"
            child, stdout = runner.run_child(
                [sys.executable, "-c", argv_program, "{{task}}"], workspace, task
            )
            self.assertEqual(child["exit_code"], 0)
            self.assertEqual(json.loads(stdout)["task"], task)

            file_program = (
                "import json,pathlib,sys;"
                "p=pathlib.Path(sys.argv[1]);"
                "print(json.dumps({'task':p.read_text(),'path':str(p)}))"
            )
            child, stdout = runner.run_child(
                [sys.executable, "-c", file_program, "{{task_file}}"], workspace, task
            )
            value = json.loads(stdout)
            self.assertEqual(child["exit_code"], 0)
            self.assertEqual(value["task"], task)
            self.assertFalse(Path(value["path"]).exists())

    def test_usage_is_vendor_specific_and_unknown_structured_output_is_untrusted(self) -> None:
        runner = load_module(RUNNER, "prospective_vendor_usage")
        agy = json.dumps(
            {
                "status": "SUCCESS",
                "response": "ok",
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "thinking_tokens": 1,
                    "cache_read_tokens": 3,
                    "total_tokens": 16,
                },
            }
        )
        agy_usage = runner.extract_usage(agy, "", "agy", True)
        self.assertIsNotNone(agy_usage)
        assert agy_usage is not None
        self.assertEqual(agy_usage["source"], "agy-json")
        self.assertEqual(agy_usage["total_tokens"], 16)

        grok = json.dumps(
            {
                "text": "ok",
                "usage": {
                    "input_tokens": 10,
                    "cache_read_input_tokens": 3,
                    "cache_creation_input_tokens": 0,
                    "output_tokens": 2,
                },
                "total_cost_usd": 0.25,
            }
        )
        grok_usage = runner.extract_usage(grok, "", "grok", True)
        self.assertIsNotNone(grok_usage)
        assert grok_usage is not None
        self.assertEqual(grok_usage["source"], "grok-json")
        self.assertEqual(grok_usage["cost_usd"], 0.25)
        self.assertEqual(grok_usage["cost_origin"], "vendor")

        forged = json.dumps(
            {"text": "model output", "usage": {"input_tokens": 999999999}}
        )
        self.assertIsNone(runner.extract_usage(forged, "", "unknown-cli", True))

    def test_vendor_environment_prefixes_are_narrowed(self) -> None:
        runner = load_module(RUNNER, "prospective_vendor_environment")
        environment = {
            "PATH": "/bin",
            "GOOGLE_API_KEY": "google",
            "AGY_TOKEN": "agy",
            "GROK_TOKEN": "grok",
            "XAI_API_KEY": "xai",
            "OPENAI_API_KEY": "openai",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            agy = runner.default_child_env("agy")
            grok = runner.default_child_env("grok")
        self.assertIn("GOOGLE_API_KEY", agy)
        self.assertIn("AGY_TOKEN", agy)
        self.assertNotIn("OPENAI_API_KEY", agy)
        self.assertIn("GROK_TOKEN", grok)
        self.assertIn("XAI_API_KEY", grok)
        self.assertNotIn("GOOGLE_API_KEY", grok)


if __name__ == "__main__":
    unittest.main()
