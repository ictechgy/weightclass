from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER = REPO_ROOT / "tools" / "speculative_run.py"


def load_runner() -> types.ModuleType:
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
            self.assertEqual(workspace.parent, output / ".work")
            self.assertTrue(workspace.name.startswith("spec-advice-"))
            self.assertFalse(workspace.exists())


class RetryWorkspaceCleanupTests(unittest.TestCase):
    def test_retry_workspace_is_recognized_as_runner_owned(self) -> None:
        """Breaks if retry cleanup rejects the prefix that attempt() creates."""
        runner = load_runner()

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            work = output / ".work"
            work.mkdir()
            retry = work / "spec-retry-owned"
            retry.mkdir()
            foreign = work / "unrelated-owned"
            foreign.mkdir()

            self.assertEqual(runner.resolved_own_workspace(retry, output), retry.resolve())
            self.assertIsNone(runner.resolved_own_workspace(foreign, output))


if __name__ == "__main__":
    unittest.main()
