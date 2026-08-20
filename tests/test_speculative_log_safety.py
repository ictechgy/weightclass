from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path

RUNNER = Path(__file__).resolve().parent.parent / "tools" / "speculative_run.py"
if str(RUNNER.parent) not in sys.path:
    sys.path.insert(0, str(RUNNER.parent))


def load_runner() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("speculative_log_safety", RUNNER)
    if spec is None or spec.loader is None:
        raise unittest.SkipTest("repository-only speculative runner unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(
    os.environ.get("WCLASS_CAMPAIGN_ACCEPTANCE") == "1",
    "prospective campaign acceptance only",
)
class SpeculativeLogSafetyTests(unittest.TestCase):
    def test_private_regular_log_is_created_and_appended(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "runs.jsonl"
            runner.append_run_record(log, {"ordinal": 1})
            runner.append_run_record(log, {"ordinal": 2})
            lines = log.read_text(encoding="utf-8").splitlines()
            mode = stat.S_IMODE(log.stat().st_mode)

        self.assertEqual([json.loads(line) for line in lines], [{"ordinal": 1}, {"ordinal": 2}])
        self.assertEqual(mode, 0o600)

    def test_symlink_is_rejected_without_touching_target_content_or_mode(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "outside.txt"
            target.write_text("OUTSIDE-SENTINEL\n", encoding="utf-8")
            target.chmod(0o644)
            before = target.read_bytes()
            mode_before = stat.S_IMODE(target.stat().st_mode)
            log = root / "runs.jsonl"
            log.symlink_to(target)

            with self.assertRaisesRegex(runner.RunLogError, "^$"):
                runner.append_run_record(log, {"ordinal": 1})

            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), mode_before)

    def test_fifo_directory_and_shared_regular_file_fail_closed(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fifo = root / "fifo"
            os.mkfifo(fifo, 0o600)
            other_directory = root / "other-directory"
            other_directory.mkdir()
            shared = root / "shared.jsonl"
            shared.write_text("UNCHANGED\n", encoding="utf-8")
            shared.chmod(0o644)
            for candidate in (fifo, other_directory, shared):
                with self.subTest(candidate=candidate.name):
                    before = candidate.read_bytes() if candidate.is_file() else None
                    with self.assertRaisesRegex(runner.RunLogError, "^$"):
                        runner.append_run_record(candidate, {"ordinal": 1})
                    if before is not None:
                        self.assertEqual(candidate.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
