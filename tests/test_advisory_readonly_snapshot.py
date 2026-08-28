from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "src" / "weightclass" / "advisory" / "readonly_snapshot.py"
RUNNER = ROOT / "src" / "weightclass" / "advisory" / "speculative_run.py"
if str(SNAPSHOT.parent) not in sys.path:
    sys.path.insert(0, str(SNAPSHOT.parent))


def load_module(path: Path, name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def review_result() -> dict[str, object]:
    return {
        "schema_version": 1,
        "mode": "review",
        "summary": "bounded summary",
        "findings": [],
        "limitations": [],
    }


def recording_clone(
    calls: list[str], clone: Callable[[Path, str, Path], None]
) -> Callable[[Path, str, Path], None]:
    def run(repo: Path, commit: str, destination: Path) -> None:
        calls.append("full")
        clone(repo, commit, destination)

    return run


class ReadonlySnapshotTests(unittest.TestCase):
    def test_clean_tree_and_adversarial_path_changes_are_detected(self) -> None:
        snapshot = load_module(SNAPSHOT, "readonly_snapshot_contract")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tracked.txt").write_bytes(b"base")
            (root / "twin.txt").write_bytes(b"base")
            (root / "nested").mkdir()
            (root / "nested" / "link").symlink_to("../tracked.txt")
            baseline = snapshot.snapshot_tree(root)
            self.assertFalse(snapshot.compare_tree(root, baseline, frozenset()).changed)

            before = (root / "tracked.txt").stat()
            (root / "tracked.txt").unlink()
            os.link(root / "twin.txt", root / "tracked.txt")
            os.utime(root / "tracked.txt", ns=(before.st_atime_ns, before.st_mtime_ns))
            self.assertTrue(snapshot.compare_tree(root, baseline, frozenset()).changed)
            (root / "tracked.txt").unlink()
            (root / "tracked.txt").write_bytes(b"base")
            os.utime(root / "tracked.txt", ns=(before.st_atime_ns, before.st_mtime_ns))

            (root / "tracked.txt").write_bytes(b"changed")
            changed = snapshot.compare_tree(root, baseline, frozenset())
            self.assertTrue(changed.changed)

            (root / "tracked.txt").write_bytes(b"base")
            (root / "tracked.txt").chmod(stat.S_IRUSR)
            self.assertTrue(snapshot.compare_tree(root, baseline, frozenset()).changed)

            (root / "tracked.txt").chmod(stat.S_IRUSR | stat.S_IWUSR)
            (root / "nested" / "link").unlink()
            (root / "nested" / "link").symlink_to("../missing")
            self.assertTrue(snapshot.compare_tree(root, baseline, frozenset()).changed)

            (root / "nested" / "link").unlink()
            (root / "nested" / "link").symlink_to("../tracked.txt")
            (root / "added.txt").write_text("new", encoding="utf-8")
            (root / ".claude").mkdir()
            (root / ".claude" / "state").write_text("scaffold", encoding="utf-8")
            comparison = snapshot.compare_tree(root, baseline, frozenset({".claude"}))
            self.assertTrue(comparison.changed)
            self.assertEqual(comparison.scaffolding, (".claude",))

            (root / "service" / ".claude").mkdir(parents=True)
            nested_scaffolding = snapshot.compare_tree(root, baseline, frozenset({".claude"}))
            self.assertIn(".claude", nested_scaffolding.scaffolding)

            (root / "nested" / ".git").mkdir()
            with self.assertRaises(snapshot.SnapshotRejected):
                snapshot.compare_tree(root, baseline, frozenset())

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO is unavailable")
    def test_special_file_is_rejected_before_it_can_be_accepted(self) -> None:
        snapshot = load_module(SNAPSHOT, "readonly_snapshot_special")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = snapshot.snapshot_tree(root)
            os.mkfifo(root / "unexpected.pipe")
            with self.assertRaises(snapshot.SnapshotRejected):
                snapshot.compare_tree(root, baseline, frozenset())

    def test_symlink_root_and_oversized_file_fail_closed(self) -> None:
        snapshot = load_module(SNAPSHOT, "readonly_snapshot_bounds")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaises(snapshot.SnapshotUnsupported):
                snapshot.snapshot_tree(link)

            (root / "large").write_bytes(b"12345")
            with mock.patch.object(snapshot, "MAX_SNAPSHOT_FILE_BYTES", 4):
                with self.assertRaises(snapshot.SnapshotUnsupported):
                    snapshot.snapshot_tree(root)


class ReadonlyClonePathTests(unittest.TestCase):
    def test_clean_read_only_attempt_keeps_trusted_full_handover(self) -> None:
        runner = load_module(RUNNER, "readonly_snapshot_runner_clean")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            (repo / "README.md").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
            verify = root / "verify"
            verify.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "value = json.load(sys.stdin)\n"
                "raise SystemExit(0 if os.getcwd() not in value['summary'] else 1)\n",
                encoding="utf-8",
            )
            verify.chmod(0o700)
            child = (
                "import json,os,sys;sys.stdin.read();"
                "open('.git/config','a').write('[poisoned]\\nvalue = child\\n');"
                f"value={review_result()!r};value['summary']='child='+os.getcwd();"
                "print(json.dumps(value))"
            )
            output = root / "out"
            output.mkdir()
            registry = output / "workspaces.txt"
            source_head = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            clone_calls: list[str] = []
            real_clone = runner.clone_at
            with (
                mock.patch.object(
                    runner,
                    "clone_at",
                    side_effect=recording_clone(clone_calls, real_clone),
                ),
            ):
                record, _, _ = runner.attempt(
                    "cheap",
                    [sys.executable, "-c", child],
                    repo,
                    runner.head_commit(repo),
                    "bounded task",
                    verify,
                    output,
                    registry,
                    frozenset(),
                    None,
                    None,
                    None,
                    False,
                    workflow="review",
                )
            self.assertTrue(record["accepted"])
            self.assertEqual(clone_calls, ["full", "full"])
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", str(repo), "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
                source_head,
            )

    def test_failed_read_only_route_skips_handover_clone(self) -> None:
        runner = load_module(RUNNER, "readonly_snapshot_runner_fallback")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            (repo / "README.md").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
            verify = root / "verify"
            verify.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            verify.chmod(0o700)
            child = "import sys;sys.stdin.read();raise SystemExit(1)"
            output = root / "out"
            output.mkdir()
            registry = output / "workspaces.txt"
            clone_calls: list[str] = []
            real_clone = runner.clone_at
            with mock.patch.object(
                runner,
                "clone_at",
                side_effect=recording_clone(clone_calls, real_clone),
            ):
                record, _, _ = runner.attempt(
                    "cheap",
                    [sys.executable, "-c", child],
                    repo,
                    runner.head_commit(repo),
                    "bounded task",
                    verify,
                    output,
                    registry,
                    frozenset(),
                    None,
                    None,
                    None,
                    False,
                    workflow="review",
                )
            self.assertFalse(record["accepted"])
            self.assertEqual(record["failure_stage"], "execution")
            self.assertEqual(clone_calls, ["full"])

    def test_unsupported_snapshot_falls_back_to_full_handover(self) -> None:
        runner = load_module(RUNNER, "readonly_snapshot_runner_unsupported")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            (repo / "README.md").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
            verify = root / "verify"
            verify.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            verify.chmod(0o700)
            child = f"import json,sys;sys.stdin.read();print(json.dumps({review_result()!r}))"
            output = root / "out"
            output.mkdir()
            registry = output / "workspaces.txt"
            clone_calls: list[str] = []
            real_clone = runner.clone_at
            with (
                mock.patch.object(
                    runner.readonly_snapshot,
                    "snapshot_tree",
                    side_effect=runner.readonly_snapshot.SnapshotUnsupported(),
                ),
                mock.patch.object(
                    runner,
                    "clone_at",
                    side_effect=recording_clone(clone_calls, real_clone),
                ),
            ):
                record, _, _ = runner.attempt(
                    "cheap",
                    [sys.executable, "-c", child],
                    repo,
                    runner.head_commit(repo),
                    "bounded task",
                    verify,
                    output,
                    registry,
                    frozenset(),
                    None,
                    None,
                    None,
                    False,
                    workflow="review",
                )
            self.assertTrue(record["accepted"])
            self.assertEqual(clone_calls, ["full", "full"])

    def test_snapshot_mutation_skips_handover_and_verifier(self) -> None:
        runner = load_module(RUNNER, "readonly_snapshot_runner_mutation")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            (repo / "README.md").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
            verify = root / "verify"
            verify.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
            verify.chmod(0o700)
            child = (
                "import json,sys;sys.stdin.read();"
                "open('CHANGED.txt','w').write('changed');"
                f"print(json.dumps({review_result()!r}))"
            )
            output = root / "out"
            output.mkdir()
            registry = output / "workspaces.txt"
            clone_calls: list[str] = []
            real_clone = runner.clone_at
            with mock.patch.object(
                runner,
                "clone_at",
                side_effect=recording_clone(clone_calls, real_clone),
            ):
                record, _, _ = runner.attempt(
                    "cheap",
                    [sys.executable, "-c", child],
                    repo,
                    runner.head_commit(repo),
                    "bounded task",
                    verify,
                    output,
                    registry,
                    frozenset(),
                    None,
                    None,
                    None,
                    False,
                    workflow="review",
                )
            self.assertFalse(record["accepted"])
            self.assertEqual(record["failure_stage"], "handover")
            self.assertEqual(record["verify"]["exit_code"], None)
            self.assertEqual(clone_calls, ["full"])


if __name__ == "__main__":
    unittest.main()
