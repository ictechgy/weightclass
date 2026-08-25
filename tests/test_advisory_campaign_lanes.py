from __future__ import annotations

import importlib.util
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "src" / "weightclass" / "advisory"
ORCHESTRATION = TOOLS / "advisory_orchestration.py"
CAMPAIGN = TOOLS / "advisory_campaign.py"
REPOSITORY_LANES_AVAILABLE = ORCHESTRATION.is_file() and CAMPAIGN.is_file()


def load_module(path: Path, name: str) -> types.ModuleType:
    tools = str(TOOLS)
    if tools not in sys.path:
        sys.path.insert(0, tools)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def manifest(max_tasks: int = 8) -> dict[str, object]:
    return {
        "schema_version": 1,
        "arm": "shape_b",
        "max_tasks": max_tasks,
        "campaign_fingerprint": "sha256:" + "a" * 64,
    }


def record(ordinal: int, *, fingerprint: str | None = None) -> dict[str, object]:
    return {
        "campaign": {
            "campaign_fingerprint": fingerprint or "sha256:" + "a" * 64,
            "arm": "shape_b",
            "sample_ordinal": ordinal,
        },
        "cheap": {"accepted": True},
        "expensive": None,
    }


def close_child(child: subprocess.Popen[str]) -> None:
    if child.poll() is None:
        child.kill()
    child.wait(timeout=5)
    for stream in (child.stdin, child.stdout, child.stderr):
        if stream is not None:
            stream.close()


@unittest.skipUnless(REPOSITORY_LANES_AVAILABLE, "repository-only anonymous campaign lanes")
class AdvisoryCampaignLaneAcceptanceTests(unittest.TestCase):
    def test_same_campaign_allocates_distinct_anonymous_lanes_concurrently(self) -> None:
        orchestration = load_module(ORCHESTRATION, "prospective_lane_orchestration")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "results"
            child_program = (
                "import sys;from pathlib import Path;"
                f"sys.path.insert(0,{str(TOOLS)!r});"
                "from advisory_orchestration import LaneRequest,acquire_campaign_lanes;"
                "r=LaneRequest('vendor',Path(sys.argv[1]),2);"
                "c=acquire_campaign_lanes((r,));leases=c.__enter__();"
                "print(leases[0].lane_index,flush=True);sys.stdin.read(1);"
                "c.__exit__(None,None,None)"
            )
            child = subprocess.Popen(
                [sys.executable, "-c", child_program, str(root)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.addCleanup(close_child, child)
            assert child.stdout is not None and child.stdin is not None
            child_lane = int(child.stdout.readline().strip())
            request = orchestration.LaneRequest("vendor", root, 2)
            with orchestration.acquire_campaign_lanes((request,)) as leases:
                self.assertNotEqual(leases[0].lane_index, child_lane)
                child_results = root if child_lane == 0 else root / ".lanes" / "lane-01"
                self.assertNotEqual(leases[0].results_dir, child_results)
                self.assertEqual(stat.S_IMODE(leases[0].results_dir.stat().st_mode), 0o700)
            child.stdin.write("x")
            child.stdin.flush()
            self.assertEqual(child.wait(timeout=5), 0, child.stderr.read() if child.stderr else "")

    def test_all_selected_vendors_are_allocated_or_none_are_held(self) -> None:
        orchestration = load_module(ORCHESTRATION, "prospective_atomic_lane_allocation")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = orchestration.LaneRequest("first", root / "first", 1)
            second = orchestration.LaneRequest("second", root / "second", 1)
            with orchestration.acquire_campaign_lanes((second,)):
                with self.assertRaisesRegex(orchestration.LaneUnavailableError, "^$"):
                    with orchestration.acquire_campaign_lanes((first, second)):
                        self.fail("partial lane allocation started")
            with orchestration.acquire_campaign_lanes((first,)) as leases:
                self.assertEqual(leases[0].lane_index, 0)

    def test_campaign_capacity_is_not_reported_as_live_lane_contention(self) -> None:
        orchestration = load_module(ORCHESTRATION, "prospective_lane_capacity_reason")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "results"
            campaign = Path(directory) / "campaign.json"
            campaign.write_text("{}", encoding="utf-8")
            request = orchestration.LaneRequest(
                "vendor",
                root,
                1,
                campaign_path=campaign,
            )
            with (
                mock.patch.object(orchestration, "load_manifest", return_value=manifest(1)),
                mock.patch.object(
                    orchestration,
                    "load_merged_lane_records",
                    return_value=[record(1)],
                ),
                self.assertRaisesRegex(orchestration.CampaignCapacityError, "^$"),
            ):
                with orchestration.acquire_campaign_lanes((request,)):
                    self.fail("capacity-reached campaign admitted another lane")

    def test_lane_records_merge_without_persisting_lane_or_project_identity(self) -> None:
        campaign = load_module(CAMPAIGN, "prospective_lane_campaign_merge")
        value = manifest()
        first = [record(1), record(2)]
        second = [record(1)]
        merged = campaign.merge_lane_records(value, (first, second))
        ordinals: list[object] = []
        for item in merged:
            binding = item["campaign"]
            assert isinstance(binding, dict)
            ordinals.append(binding["sample_ordinal"])
        self.assertEqual(ordinals, [1, 2, 3])
        original_binding = first[0]["campaign"]
        assert isinstance(original_binding, dict)
        self.assertEqual(original_binding["sample_ordinal"], 1)
        self.assertNotIn("lane", repr(merged).lower())
        self.assertNotIn("project", repr(merged).lower())

        mismatched = [record(1, fingerprint="sha256:" + "b" * 64)]
        with self.assertRaisesRegex(campaign.CampaignError, "^campaign_record_binding_mismatch$"):
            campaign.merge_lane_records(value, (first, mismatched))
        with self.assertRaisesRegex(campaign.CampaignError, "^$"):
            campaign.merge_lane_records(manifest(max_tasks=2), (first, second))

    def test_lane_record_loader_preserves_a_value_free_binding_reason(self) -> None:
        campaign = load_module(CAMPAIGN, "prospective_lane_campaign_diagnostic")
        value = manifest()
        mismatched = [record(1, fingerprint="sha256:" + "b" * 64)]
        root = Path("/private/results")
        with (
            mock.patch.object(
                campaign,
                "existing_lane_result_directories",
                return_value=(root,),
            ),
            mock.patch.object(campaign, "load_bound_records", return_value=mismatched),
            self.assertRaisesRegex(campaign.CampaignError, "^campaign_record_binding_mismatch$"),
        ):
            campaign.load_merged_lane_records(value, root)

    def test_lane_directory_mapping_preserves_the_existing_log_as_lane_zero(self) -> None:
        orchestration = load_module(ORCHESTRATION, "prospective_lane_paths")
        root = Path("/private/results")
        self.assertEqual(
            orchestration.lane_result_directories(root, 4),
            (
                root,
                root / ".lanes" / "lane-01",
                root / ".lanes" / "lane-02",
                root / ".lanes" / "lane-03",
            ),
        )


if __name__ == "__main__":
    unittest.main()
