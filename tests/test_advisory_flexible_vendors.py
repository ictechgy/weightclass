from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "src" / "weightclass" / "advisory"
ROUTES = TOOLS / "advisory_routes.py"
RUNNER = TOOLS / "speculative_run.py"
REPOSITORY_TOOLS_AVAILABLE = ROUTES.is_file() and RUNNER.is_file()
for directory in (str(ROOT), str(TOOLS)):
    if directory not in sys.path:
        sys.path.insert(0, directory)


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
    def roles() -> dict[str, list[str]]:
        return {role: list(command) for role in ("cheap", "advisor", "expensive")}

    return {
        "schema_version": 2,
        "vendor": "acme-cli",
        "commands": {"implementation": roles(), "evidence": roles()},
    }


@unittest.skipUnless(REPOSITORY_TOOLS_AVAILABLE, "repository-only advisory tools unavailable")
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
        reserved = profile("claude")
        models = reserved["models"]
        assert isinstance(models, dict)
        models["cheap"] = "{{task}}"
        with self.assertRaisesRegex(routes.AdvisoryRouteError, "^$"):
            routes.build_routes(reserved)

        agy = routes.build_routes(profile("agy"))
        # agy 는 `--input-format stream-json` 에서 프롬프트를 stdin 에서 읽고,
        # 같은 호출의 argv 프롬프트를 CLI 자신이 거부한다. 그래서 이 라우트에는
        # 태스크 자리가 없고 로컬 프로세스 인자 노출도 없다.
        self.assertEqual(routes.command_task_delivery(agy.cheap), "stdin")
        self.assertNotIn("{{task}}", agy.cheap)
        self.assertNotIn("--print", agy.cheap)
        self.assertIn("--sandbox", agy.cheap)
        self.assertIn("--disable-slash-commands", agy.cheap)
        self.assertEqual(agy.cheap[agy.cheap.index("--input-format") + 1], "stream-json")
        self.assertEqual(agy.cheap[agy.cheap.index("--output-format") + 1], "stream-json")
        self.assertEqual(agy.cheap[agy.cheap.index("--model") + 1], "c")
        self.assertEqual(agy.cheap[agy.cheap.index("--mode") + 1], "accept-edits")
        self.assertEqual(agy.advisor[agy.advisor.index("--mode") + 1], "plan")
        agy_evidence = routes.build_routes(profile("agy"), read_only_executors=True)
        for role, command in zip(("cheap", "advisor", "expensive"), agy_evidence, strict=True):
            self.assertEqual(command[command.index("--mode") + 1], "plan")
            self.assertNotIn("--effort", command)
            self.assertNotIn("--disable-slash-commands", command)
            self.assertEqual("--json-schema" in command, role != "advisor")

        grok = routes.build_routes(profile("grok"))
        self.assertEqual(routes.command_task_delivery(grok.cheap), "file")
        self.assertEqual(grok.cheap[-2:], ("--prompt-file", "{{task_file}}"))
        self.assertEqual(grok.cheap[grok.cheap.index("--permission-mode") + 1], "acceptEdits")
        self.assertEqual(grok.advisor[grok.advisor.index("--permission-mode") + 1], "plan")
        self.assertIn("--no-subagents", grok.cheap)
        self.assertIn("--disable-web-search", grok.cheap)
        self.assertIn("--verbatim", grok.cheap)
        self.assertEqual(grok.cheap[grok.cheap.index("--output-format") + 1], "json")
        self.assertEqual(grok.cheap[grok.cheap.index("--model") + 1], "c")
        grok_evidence = routes.build_routes(profile("grok"), read_only_executors=True)
        for role, command in zip(("cheap", "advisor", "expensive"), grok_evidence, strict=True):
            self.assertEqual("--json-schema" in command, role != "advisor")

    def test_schema_two_binds_exact_command_matrices_and_delivery(self) -> None:
        routes = load_module(ROUTES, "prospective_custom_routes")
        implementation = custom_profile(["acme", "--prompt-file", "{{task_file}}"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "custom.json"
            path.write_text(json.dumps(implementation), encoding="utf-8")
            loaded = routes.load_profile(path)
            self.assertEqual(loaded["schema_version"], 2)
            self.assertRegex(routes.profile_sha256(path), r"^sha256:[0-9a-f]{64}$")
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
            self.assertEqual(value["task_delivery"], "stdin")
            self.assertFalse(value["task_process_exposure"])

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


@unittest.skipUnless(REPOSITORY_TOOLS_AVAILABLE, "repository-only advisory tools unavailable")
class FlexibleVendorRunnerTests(unittest.TestCase):
    def test_run_child_materializes_stdin_argv_and_private_file_then_cleans_it(self) -> None:
        runner = load_module(RUNNER, "prospective_flexible_runner")
        task = "PRIVATE-TASK-MARKER"
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            subprocess.run(["git", "-C", str(workspace), "config", "user.name", "Test"], check=True)
            subprocess.run(
                ["git", "-C", str(workspace), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            (workspace / "README.md").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(workspace), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(workspace), "commit", "-qm", "base"], check=True)
            stdin_program = "import json,sys;print(json.dumps({'task':sys.stdin.read()}))"
            child, stdout = runner.run_child([sys.executable, "-c", stdin_program], workspace, task)
            self.assertEqual(child["exit_code"], 0)
            self.assertEqual(json.loads(stdout)["task"], task)

            argv_program = "import json,sys;print(json.dumps({'task':sys.argv[1]}))"
            child, stdout = runner.run_child(
                [sys.executable, "-c", argv_program, "{{task}}"], workspace, task
            )
            self.assertEqual(child["exit_code"], 0)
            self.assertEqual(json.loads(stdout)["task"], task)

            file_program = (
                "import json,pathlib,subprocess,sys;"
                "p=pathlib.Path(sys.argv[1]);"
                "subprocess.run(['git','add','-A'],check=True);"
                "print(json.dumps({'task':p.read_text(),'path':str(p)}))"
            )
            child, stdout = runner.run_child(
                [sys.executable, "-c", file_program, "{{task_file}}"], workspace, task
            )
            value = json.loads(stdout)
            self.assertEqual(child["exit_code"], 0)
            self.assertEqual(value["task"], task)
            self.assertTrue(value["path"].startswith("/dev/fd/"))
            self.assertNotEqual(Path(value["path"]).parent, workspace)
            self.assertFalse(Path(value["path"]).exists())
            cached = subprocess.run(
                ["git", "-C", str(workspace), "diff", "--cached", "--binary"],
                capture_output=True,
                check=True,
                text=True,
            ).stdout
            self.assertNotIn(task, cached)

    def test_task_file_delivery_never_materializes_a_named_file(self) -> None:
        runner = load_module(RUNNER, "prospective_task_file_cleanup")

        def fail_after_observing_anonymous_delivery(
            command: list[str], *args: object, **kwargs: object
        ) -> None:
            self.assertTrue(command[-1].startswith("/dev/fd/"))
            self.assertGreater(int(command[-1].rsplit("/", 1)[1]), 2)
            raise OSError()

        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(
                runner.tempfile,
                "mkstemp",
                side_effect=AssertionError("named task file created"),
            ),
            mock.patch.object(
                runner.subprocess,
                "Popen",
                side_effect=fail_after_observing_anonymous_delivery,
            ),
            self.assertRaisesRegex(runner.RunFailure, "could not start the route"),
        ):
            runner.run_child(
                ["unavailable", "{{task_file}}"],
                Path(directory),
                "PRIVATE-TASK-MARKER",
            )

    def test_task_file_delivery_survives_stdio_descriptor_reuse(self) -> None:
        program = """
import json
import os
import sys
from pathlib import Path
from weightclass.advisory import speculative_run

os.close(0)
reader = "import pathlib,sys;print(pathlib.Path(sys.argv[1]).read_text())"
child, output = speculative_run.run_child(
    [sys.executable, "-c", reader, "{{task_file}}"],
    Path(sys.argv[1]),
    "FDZERO",
)
print(json.dumps({"code": child["exit_code"], "output": output}))
"""
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [sys.executable, "-c", program, directory],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                text=True,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        value = json.loads(completed.stdout)
        self.assertEqual(value, {"code": 0, "output": "FDZERO\n"})

    def test_task_pipe_streams_the_maximum_bounded_task_without_blocking(self) -> None:
        runner = load_module(RUNNER, "prospective_maximum_task_pipe")
        reader = "import pathlib,sys;print(len(pathlib.Path(sys.argv[1]).read_text()))"
        task = "x" * runner.MAX_TASK_FILE_BYTES
        with tempfile.TemporaryDirectory() as directory:
            child, output = runner.run_child(
                [sys.executable, "-c", reader, "{{task_file}}"],
                Path(directory),
                task,
            )

        self.assertEqual(child["exit_code"], 0)
        self.assertEqual(output, f"{runner.MAX_TASK_FILE_BYTES}\n")

    def test_task_pipe_writer_start_failure_kills_the_waiting_child(self) -> None:
        runner = load_module(RUNNER, "prospective_task_pipe_writer_failure")
        reader = "import pathlib,sys;pathlib.Path(sys.argv[1]).read_text()"
        started = time.monotonic()
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(runner.threading, "Thread") as thread,
            self.assertRaisesRegex(runner.RunFailure, "could not deliver task file"),
        ):
            thread.return_value.start.side_effect = RuntimeError()
            runner.run_child(
                [sys.executable, "-c", reader, "{{task_file}}"],
                Path(directory),
                "PRIVATE",
            )

        self.assertLess(time.monotonic() - started, 2.0)

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

        forged = json.dumps({"text": "model output", "usage": {"input_tokens": 999999999}})
        self.assertIsNone(runner.extract_usage(forged, "", "unknown-cli", True))
        for output_format in ("json", "stream-json", "streaming-json"):
            self.assertTrue(
                runner.wants_structured_output(["vendor", "--output-format", output_format])
            )

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
            unknown = runner.default_child_env("acme-cli")
            substring = runner.default_child_env("magy-cli")
        self.assertIn("GOOGLE_API_KEY", agy)
        self.assertIn("AGY_TOKEN", agy)
        self.assertNotIn("OPENAI_API_KEY", agy)
        self.assertIn("GROK_TOKEN", grok)
        self.assertIn("XAI_API_KEY", grok)
        self.assertNotIn("GOOGLE_API_KEY", grok)
        self.assertEqual(unknown, frozenset({"PATH"}))
        self.assertEqual(substring, frozenset({"PATH"}))


if __name__ == "__main__":
    unittest.main()
