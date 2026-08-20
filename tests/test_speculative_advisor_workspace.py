from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER = REPO_ROOT / "tools" / "speculative_run.py"
if str(RUNNER.parent) not in sys.path:
    sys.path.insert(0, str(RUNNER.parent))


def load_runner() -> types.ModuleType:
    if not RUNNER.is_file():
        raise unittest.SkipTest("repository-only speculative runner unavailable")
    spec = importlib.util.spec_from_file_location("speculative_run_workspace", RUNNER)
    if spec is None or spec.loader is None:
        raise unittest.SkipTest("speculative runner unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules["speculative_run_workspace"] = module
    spec.loader.exec_module(module)
    return module


class PromptOnlyAdvisorWorkspaceTests(unittest.TestCase):
    def test_prompt_only_advisor_gets_an_isolated_empty_repository(self) -> None:
        """Breaks if repository-requiring CLIs fail or discover a parent repository."""
        runner = load_runner()
        child = (
            "import json,pathlib,subprocess,sys;"
            "result=subprocess.run(['git','rev-parse','--show-toplevel'],"
            "capture_output=True,text=True);"
            "result.returncode and sys.exit(result.returncode);"
            "root=pathlib.Path(result.stdout.strip());"
            "commits=subprocess.run(['git','rev-list','--all','--count'],"
            "capture_output=True,text=True,check=True).stdout.strip();"
            "print(json.dumps({'root':str(root),'entries':sorted(p.name for p in root.iterdir()),"
            "'commits':commits}))"
        )

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            record, advice = runner.ask_advisor(
                [sys.executable, "-c", child],
                "failure",
                "bounded prompt",
                REPO_ROOT,
                "0" * 40,
                output / "workspaces.txt",
                None,
                None,
                None,
                False,
                False,
            )
            self.assertFalse(record["route_failed"])
            self.assertTrue(advice)
            payload = json.loads(advice)
            workspace = Path(payload["root"])

            self.assertEqual(payload["entries"], [".git"])
            self.assertEqual(payload["commits"], "0")
            self.assertEqual(workspace.parent, (output / ".work").resolve())
            self.assertTrue(workspace.name.startswith("spec-advice-"))
            self.assertFalse(workspace.exists())

    def test_prompt_only_advisor_strips_git_routing_environment_in_env_all_mode(self) -> None:
        """Breaks if inherited GIT_* variables bypass the empty repository anchor."""
        runner = load_runner()
        child = (
            "import json,os;"
            "print(json.dumps({'git_names':sorted("
            "name for name in os.environ if name.startswith('GIT_'))}))"
        )

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            inherited = {
                "GIT_DIR": str(REPO_ROOT / ".git"),
                "GIT_WORK_TREE": str(REPO_ROOT),
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(REPO_ROOT / ".git" / "objects"),
            }
            with mock.patch.dict(os.environ, inherited, clear=False):
                record, advice = runner.ask_advisor(
                    [sys.executable, "-c", child],
                    "failure",
                    "bounded prompt",
                    REPO_ROOT,
                    "0" * 40,
                    output / "workspaces.txt",
                    None,
                    None,
                    None,
                    False,
                    False,
                )

            self.assertFalse(record["route_failed"])
            self.assertEqual(json.loads(advice), {"git_names": []})


class OwnedWorkspaceCleanupTests(unittest.TestCase):
    def test_retry_and_advice_workspaces_are_recognized_as_runner_owned(self) -> None:
        """Breaks if crash cleanup rejects a prefix that the runner creates."""
        runner = load_runner()

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            work = output / ".work"
            work.mkdir()
            retry = work / "spec-retry-owned"
            retry.mkdir()
            advice = work / "spec-advice-owned"
            advice.mkdir()
            foreign = work / "unrelated-owned"
            foreign.mkdir()

            self.assertEqual(runner.resolved_own_workspace(retry, output), retry.resolve())
            self.assertEqual(runner.resolved_own_workspace(advice, output), advice.resolve())
            self.assertIsNone(runner.resolved_own_workspace(foreign, output))

    def test_partial_registration_failure_leaves_no_untracked_workspace(self) -> None:
        """Breaks if attempt setup fails between its two workspace registrations."""
        runner = load_runner()

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            registry = output / "workspaces.txt"
            original_register = runner.register
            add_calls = 0

            def fail_second_add(registry_path: Path, workspace: Path, add: bool) -> None:
                nonlocal add_calls
                if add:
                    add_calls += 1
                    if add_calls == 2:
                        raise OSError("simulated registry failure")
                original_register(registry_path, workspace, add)

            with mock.patch.object(runner, "register", side_effect=fail_second_add):
                record, verify_output, patch = runner.attempt(
                    "retry",
                    [sys.executable, "-c", "raise SystemExit(0)"],
                    REPO_ROOT,
                    runner.head_commit(REPO_ROOT),
                    "bounded task",
                    output / "unused-verify",
                    output,
                    registry,
                    frozenset(),
                    None,
                    None,
                    None,
                    False,
                )

            self.assertEqual(add_calls, 2)
            self.assertEqual(record["failure_kind"], "infrastructure")
            self.assertEqual(record["workspace"], None)
            self.assertEqual(verify_output, "")
            self.assertEqual(patch, b"")
            self.assertEqual(list((output / ".work").iterdir()), [])
            self.assertEqual(registry.read_text(encoding="utf-8"), "")

    def test_verifier_home_registration_failure_is_also_cleaned(self) -> None:
        """Breaks if the verifier HOME bypasses exception-safe registration."""
        runner = load_runner()

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            registry = output / "workspaces.txt"
            original_register = runner.register
            add_calls = 0

            def fail_third_add(registry_path: Path, workspace: Path, add: bool) -> None:
                nonlocal add_calls
                if add:
                    add_calls += 1
                    if add_calls == 3:
                        raise OSError("simulated verifier registry failure")
                original_register(registry_path, workspace, add)

            child = (
                "from pathlib import Path;"
                "path=Path('README.md');"
                "path.write_text(path.read_text(encoding='utf-8')+'\\n',encoding='utf-8')"
            )
            with mock.patch.object(runner, "register", side_effect=fail_third_add):
                record, verify_output, patch = runner.attempt(
                    "cheap",
                    [sys.executable, "-c", child],
                    REPO_ROOT,
                    runner.head_commit(REPO_ROOT),
                    "bounded task",
                    output / "unused-verify",
                    output,
                    registry,
                    frozenset(),
                    None,
                    None,
                    None,
                    False,
                )

            self.assertEqual(add_calls, 3)
            self.assertEqual(record["failure_kind"], "infrastructure")
            self.assertEqual(record["workspace"], None)
            self.assertEqual(verify_output, "")
            self.assertNotEqual(patch, b"")
            self.assertEqual(list((output / ".work").iterdir()), [])
            self.assertEqual(registry.read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
