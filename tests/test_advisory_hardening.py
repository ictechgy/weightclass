from __future__ import annotations

import fcntl
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from weightclass import usage_aggregation
from weightclass.executable_observation import observe_executable
from weightclass.v2_validation import V2ValidationError

ROOT = Path(__file__).resolve().parent.parent
PARALLEL = ROOT / "tools" / "advisory_parallel.py"
WRAPPER = ROOT / "tools" / "wclass_advisory.py"
ORCHESTRATION = ROOT / "tools" / "advisory_orchestration.py"
REPOSITORY_TOOLS_AVAILABLE = all(path.is_file() for path in (PARALLEL, WRAPPER, ORCHESTRATION))


def load_parallel() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("advisory_hardening_parallel", PARALLEL)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load advisory_parallel.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_orchestration() -> types.ModuleType:
    tools = str(ROOT / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    spec = importlib.util.spec_from_file_location("advisory_hardening_orchestration", ORCHESTRATION)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load advisory_orchestration.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(REPOSITORY_TOOLS_AVAILABLE, "repository-only advisory tools unavailable")
class AdvisoryOrchestrationHardeningTests(unittest.TestCase):
    def test_campaign_dispatch_lock_rejects_overlap_before_start(self) -> None:
        parallel = load_parallel()
        orchestration = load_orchestration()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock_path = root / "dispatch.lock"
            marker = root / "started"
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            self.addCleanup(os.close, descriptor)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            job = orchestration.CampaignJob(
                parallel.AdvisoryJob(
                    "codex",
                    (
                        sys.executable,
                        "-c",
                        f"from pathlib import Path;Path({str(marker)!r}).touch()",
                    ),
                ),
                lock_path,
            )
            with self.assertRaisesRegex(ValueError, "^$"):
                orchestration.run_campaign_jobs((job,))
            self.assertFalse(marker.exists())

    def test_repository_wrapper_delegates_campaign_prune(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "wclass_advisory.py"),
                    "--router-root",
                    str(ROOT),
                    "--campaign-root",
                    directory,
                    "--prune",
                ],
                cwd=ROOT,
                capture_output=True,
                check=False,
                text=True,
            )
        self.assertEqual(completed.returncode, 0)

    def test_timeout_does_not_leave_a_descendant_running(self) -> None:
        parallel = load_parallel()
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "finished"
            program = (
                "import pathlib,subprocess,sys,time;"
                "p=subprocess.Popen([sys.executable,'-c',"
                "'import pathlib,time;time.sleep(0.8);pathlib.Path(sys.argv[1]).touch()',"
                "sys.argv[1]]);"
                "time.sleep(5)"
            )
            result = parallel.run_parallel(
                (
                    parallel.AdvisoryJob(
                        "descendant",
                        (sys.executable, "-c", program, str(marker)),
                        timeout_seconds=0.1,
                        max_output_bytes=128,
                    ),
                )
            )[0]
            self.assertTrue(result.timed_out)
            self.assertEqual(result.stderr, b"advisory child timed out\n")
            time.sleep(1.0)
            self.assertFalse(marker.exists())

    def test_combined_cap_drains_stdout_and_stderr(self) -> None:
        parallel = load_parallel()
        program = "import sys;sys.stdout.write('o'*100000);sys.stderr.write('e'*100000)"
        result = parallel.run_parallel(
            (
                parallel.AdvisoryJob(
                    "both",
                    (sys.executable, "-c", program),
                    timeout_seconds=2.0,
                    max_output_bytes=73,
                ),
            )
        )[0]
        self.assertTrue(result.output_truncated)
        self.assertLessEqual(len(result.stdout) + len(result.stderr), 73)
        self.assertEqual(result.returncode, 0)


class SecurityBoundaryHardeningTests(unittest.TestCase):
    def test_failed_replacement_cleans_descriptor_relative_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "usage-v1.json"
            usage_aggregation.ensure_usage_store(store)
            with (
                mock.patch("weightclass.usage_aggregation.os.replace", side_effect=OSError),
                self.assertRaisesRegex(usage_aggregation.UsageAggregationError, "^$"),
            ):
                usage_aggregation.set_relative_cost_weight(store, "codex", None, "low", "0.5")
            self.assertEqual(list(Path(directory).glob(".weightclass-usage-*")), [])

    def test_parent_swap_fails_without_writing_the_replacement_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "private"
            parent.mkdir(mode=0o700)
            store = parent / "usage-v1.json"
            usage_aggregation.ensure_usage_store(store)
            moved = root / "moved"
            real_write = usage_aggregation._write_descriptor

            def write_then_swap(descriptor: int, payload: bytes) -> None:
                real_write(descriptor, payload)
                parent.rename(moved)
                parent.mkdir(mode=0o700)

            with (
                mock.patch.object(
                    usage_aggregation,
                    "_write_descriptor",
                    side_effect=write_then_swap,
                ),
                self.assertRaisesRegex(usage_aggregation.UsageAggregationError, "^$"),
            ):
                usage_aggregation.set_relative_cost_weight(store, "codex", None, "low", "0.5")

            self.assertFalse((parent / store.name).exists())
            self.assertTrue((moved / store.name).is_file())
            self.assertEqual(list(moved.glob(".weightclass-usage-*")), [])

    def test_user_owned_group_writable_ancestor_remains_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "group"
            parent.mkdir()
            parent.chmod(0o770)
            executable = parent / "tool"
            executable.write_bytes(b"x")
            executable.chmod(0o700)
            self.assertTrue(observe_executable(str(executable)).executable_bit)

    def test_nonsticky_world_writable_ancestor_is_value_free_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "public"
            parent.mkdir()
            parent.chmod(0o777)
            executable = parent / "tool"
            executable.write_bytes(b"x")
            executable.chmod(0o700)
            with self.assertRaisesRegex(V2ValidationError, "^$"):
                observe_executable(str(executable))

    def test_public_lexical_ancestor_is_checked_before_intermediate_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir(mode=0o700)
            executable = target / "tool"
            executable.write_bytes(b"x")
            executable.chmod(0o700)
            public = root / "public"
            public.mkdir()
            public.chmod(0o777)
            (public / "link").symlink_to(target, target_is_directory=True)
            lexical = public / "link" / executable.name
            with self.assertRaisesRegex(V2ValidationError, "^$"):
                observe_executable(str(lexical))

            public.chmod(0o1777)
            self.assertTrue(observe_executable(str(lexical)).executable_bit)


if __name__ == "__main__":
    unittest.main()
