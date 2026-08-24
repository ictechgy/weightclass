from __future__ import annotations

import importlib
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from typing import cast
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
PARALLEL = TOOLS / "advisory_parallel.py"
WRAPPER = TOOLS / "wclass_advisory.py"
REPOSITORY_HARDENING_AVAILABLE = PARALLEL.is_file() and WRAPPER.is_file()


def load_module(path: Path, name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(REPOSITORY_HARDENING_AVAILABLE, "repository-only hardening unavailable")
class AdvisoryHardeningAcceptanceTests(unittest.TestCase):
    def test_parallel_jobs_have_deadlines_and_combined_output_bounds(self) -> None:
        parallel = load_module(PARALLEL, "prospective_bounded_parallel")
        started = time.monotonic()
        results = parallel.run_parallel(
            (
                parallel.AdvisoryJob(
                    "timeout",
                    (sys.executable, "-c", "import time;time.sleep(5)"),
                    timeout_seconds=0.2,
                    max_output_bytes=64,
                ),
                parallel.AdvisoryJob(
                    "output",
                    (sys.executable, "-c", "print('x'*1000)"),
                    timeout_seconds=2.0,
                    max_output_bytes=64,
                ),
                parallel.AdvisoryJob(
                    "peer",
                    (sys.executable, "-c", "print('peer-complete')"),
                    timeout_seconds=2.0,
                    max_output_bytes=64,
                ),
            )
        )
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertEqual([result.label for result in results], ["timeout", "output", "peer"])
        self.assertEqual(results[0].returncode, 124)
        self.assertTrue(results[0].timed_out)
        self.assertEqual(results[0].stderr, b"advisory child timed out\n")
        self.assertTrue(results[1].output_truncated)
        self.assertLessEqual(len(results[1].stdout) + len(results[1].stderr), 64)
        self.assertEqual(results[2].returncode, 0)
        self.assertEqual(results[2].stdout, b"peer-complete\n")

    def test_usage_store_transaction_is_parent_descriptor_relative(self) -> None:
        usage = importlib.import_module("weightclass.usage_aggregation")
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "usage-v1.json"
            usage.ensure_usage_store(store)
            real_open = usage.os.open
            real_replace = usage.os.replace
            opened: list[tuple[str, int | None]] = []
            replaced: list[tuple[int | None, int | None]] = []

            def observed_open(
                path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                opened.append((os.fsdecode(path), dir_fd))
                return cast(int, real_open(path, flags, mode, dir_fd=dir_fd))

            def observed_replace(
                source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                *,
                src_dir_fd: int | None = None,
                dst_dir_fd: int | None = None,
            ) -> None:
                replaced.append((src_dir_fd, dst_dir_fd))
                real_replace(
                    source,
                    destination,
                    src_dir_fd=src_dir_fd,
                    dst_dir_fd=dst_dir_fd,
                )

            dimensions = usage.UsageDimensions("codex", None, "low", "low")
            with (
                mock.patch.object(usage.os, "open", side_effect=observed_open),
                mock.patch.object(usage.os, "replace", side_effect=observed_replace),
            ):
                usage.record_usage(
                    store,
                    dimensions,
                    child_returncode=0,
                    rework=False,
                    escalation=False,
                )

        target_names = {store.name, f"{store.name}.lock"}
        target_opens = [(path, descriptor) for path, descriptor in opened if path in target_names]
        self.assertTrue(target_opens)
        self.assertTrue(all(descriptor is not None for _, descriptor in target_opens))
        self.assertTrue(any(path.startswith(".weightclass-usage-") for path, _ in opened))
        self.assertTrue(replaced)
        self.assertTrue(
            all(source is not None and source == destination for source, destination in replaced)
        )

    def test_executable_observation_rejects_mutable_file_and_public_ancestor(self) -> None:
        observation = importlib.import_module("weightclass.executable_observation")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mutable = root / "mutable"
            mutable.write_bytes(b"x")
            mutable.chmod(0o770)
            with (
                mock.patch.object(observation.os, "getuid", return_value=os.getuid() + 1),
                self.assertRaisesRegex(observation.V2ValidationError, "^$"),
            ):
                observation.observe_executable(str(mutable))
            self.assertTrue(observation.observe_executable(str(mutable)).executable_bit)

            public = root / "public"
            public.mkdir()
            public.chmod(0o777)
            public_executable = public / "tool"
            public_executable.write_bytes(b"x")
            public_executable.chmod(0o700)
            with self.assertRaisesRegex(observation.V2ValidationError, "^$"):
                observation.observe_executable(str(public_executable))

            public.chmod(0o1777)
            self.assertTrue(observation.observe_executable(str(public_executable)).executable_bit)

    def test_repository_owned_wrapper_has_no_machine_paths(self) -> None:
        source = WRAPPER.read_text(encoding="utf-8")
        self.assertNotIn("/Users/", source)
        completed = subprocess.run(
            [sys.executable, str(WRAPPER), "--help"],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("--router-root", completed.stdout)
        self.assertIn("--campaign-root", completed.stdout)

    def test_campaign_option_is_forwarded_without_abbreviating_campaign_root(self) -> None:
        wrapper = load_module(WRAPPER, "exact_campaign_option_boundary")
        campaign_root = Path("/private/results")
        manifest = Path("/private/campaign.json")
        arguments, forwarded = wrapper._parser().parse_known_args(
            [
                "--router-root",
                str(ROOT),
                "--campaign-root",
                str(campaign_root),
                "--campaign",
                str(manifest),
            ]
        )
        self.assertEqual(arguments.campaign_root, campaign_root)
        self.assertEqual(forwarded, ["--campaign", str(manifest)])


if __name__ == "__main__":
    unittest.main()
