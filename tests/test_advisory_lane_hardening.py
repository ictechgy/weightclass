from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import cast
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
ORCHESTRATION = TOOLS / "advisory_orchestration.py"
RUNNER = TOOLS / "speculative_run.py"
REPOSITORY_TOOLS_AVAILABLE = ORCHESTRATION.is_file() and RUNNER.is_file()


def load_orchestration(name: str) -> types.ModuleType:
    tools = str(TOOLS)
    if tools not in sys.path:
        sys.path.insert(0, tools)
    spec = importlib.util.spec_from_file_location(name, ORCHESTRATION)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load advisory orchestration")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_runner(name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, RUNNER)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load speculative runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def close_child(child: subprocess.Popen[str]) -> None:
    if child.poll() is None:
        child.kill()
    child.wait(timeout=5)
    for stream in (child.stdin, child.stdout, child.stderr):
        if stream is not None:
            stream.close()


@unittest.skipUnless(REPOSITORY_TOOLS_AVAILABLE, "repository-only advisory tools unavailable")
class AdvisoryLaneHardeningTests(unittest.TestCase):
    def test_prune_fails_closed_while_any_lane_campaign_is_active(self) -> None:
        runner = load_runner("lane_prune_hardening")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "results"
            root.mkdir(mode=0o700)
            lock_path = root / "campaign.lock"
            program = (
                "import fcntl,os,sys;"
                "fd=os.open(sys.argv[1],os.O_CREAT|os.O_RDWR,0o600);"
                "fcntl.flock(fd,fcntl.LOCK_EX);print('locked',flush=True);sys.stdin.read()"
            )
            child = subprocess.Popen(
                [sys.executable, "-c", program, str(lock_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.addCleanup(close_child, child)
            assert child.stdout is not None
            self.assertEqual(child.stdout.readline().strip(), "locked")
            with self.assertRaisesRegex(runner.CampaignError, "^$"):
                runner.prune_all_lanes(root)

    def test_results_root_and_lane_symlinks_fail_without_chmod_following(self) -> None:
        orchestration = load_orchestration("lane_symlink_hardening")
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            target = parent / "target"
            target.mkdir(mode=0o700)
            linked_root = parent / "results"
            linked_root.symlink_to(target, target_is_directory=True)
            request = orchestration.LaneRequest("vendor", linked_root, 2)
            with self.assertRaisesRegex(ValueError, "^$"):
                with orchestration.acquire_campaign_lanes((request,)):
                    self.fail("symlinked results root was admitted")
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)

            linked_root.unlink()
            linked_root.mkdir(mode=0o700)
            lanes = linked_root / ".lanes"
            lanes.mkdir(mode=0o700)
            (lanes / "lane-01").symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "^$"):
                with orchestration.acquire_campaign_lanes((request,)):
                    self.fail("symlinked lane was admitted")

    def test_busy_lane_counts_against_the_global_campaign_maximum(self) -> None:
        orchestration = load_orchestration("lane_capacity_hardening")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "results"
            request = orchestration.LaneRequest(
                "vendor",
                root,
                2,
                campaign_path=Path(directory) / "campaign.json",
            )
            real_open = orchestration._open_lane_lock

            def one_busy(path: Path, *, blocking: bool = False) -> int:
                if path == root / ".lane.lock":
                    raise ValueError
                return cast(int, real_open(path, blocking=blocking))

            with (
                mock.patch.object(orchestration, "_open_lane_lock", side_effect=one_busy),
                mock.patch.object(orchestration, "load_manifest", return_value={"max_tasks": 1}),
                mock.patch.object(orchestration, "load_merged_lane_records", return_value=[]),
                self.assertRaisesRegex(ValueError, "^$"),
            ):
                with orchestration.acquire_campaign_lanes((request,)):
                    self.fail("global campaign maximum was exceeded")

    def test_crashed_lane_owner_is_recoverable_without_a_reservation_file(self) -> None:
        orchestration = load_orchestration("lane_crash_hardening")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "results"
            program = (
                "import sys;from pathlib import Path;"
                f"sys.path.insert(0,{str(TOOLS)!r});"
                "from advisory_orchestration import LaneRequest,acquire_campaign_lanes;"
                "c=acquire_campaign_lanes((LaneRequest('vendor',Path(sys.argv[1]),1),));"
                "c.__enter__();print('locked',flush=True);sys.stdin.read()"
            )
            child = subprocess.Popen(
                [sys.executable, "-c", program, str(root)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.addCleanup(close_child, child)
            assert child.stdout is not None
            self.assertEqual(child.stdout.readline().strip(), "locked")
            child.kill()
            child.wait(timeout=5)

            request = orchestration.LaneRequest("vendor", root, 1)
            with orchestration.acquire_campaign_lanes((request,)) as leases:
                self.assertEqual(leases[0].lane_index, 0)
            self.assertFalse((root / "reservations.json").exists())


if __name__ == "__main__":
    unittest.main()
