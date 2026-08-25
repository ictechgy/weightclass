from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

RUNNER = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "weightclass"
    / "advisory"
    / "speculative_run.py"
)
if str(RUNNER.parent) not in sys.path:
    sys.path.insert(0, str(RUNNER.parent))


def load_runner() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("speculative_task_input", RUNNER)
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
class SpeculativeTaskInputTests(unittest.TestCase):
    def test_valid_private_regular_utf8_file_is_read(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory) / "task.txt"
            task.write_text("bounded task", encoding="utf-8")
            task.chmod(0o600)
            observed = runner.read_task_file(task, require_private=True)
        self.assertEqual(observed, "bounded task")

    def test_symlink_fifo_oversize_and_invalid_utf8_fail_closed(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.txt"
            target.write_text("PRIVATE-SENTINEL", encoding="utf-8")
            target.chmod(0o600)
            link = root / "task.txt"
            link.symlink_to(target)
            fifo = root / "task.fifo"
            os.mkfifo(fifo, 0o600)
            oversized = root / "oversized.txt"
            oversized.write_bytes(b"x" * (runner.MAX_TASK_FILE_BYTES + 1))
            oversized.chmod(0o600)
            invalid = root / "invalid.txt"
            invalid.write_bytes(b"\xff")
            invalid.chmod(0o600)
            for candidate in (link, fifo, oversized, invalid):
                with self.subTest(kind=candidate.name):
                    with self.assertRaisesRegex(runner.TaskInputError, "^$"):
                        runner.read_task_file(candidate, require_private=True)

    def test_campaign_mode_requires_current_user_private_file(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory) / "task.txt"
            task.write_text("task", encoding="utf-8")
            task.chmod(0o644)
            with self.assertRaisesRegex(runner.TaskInputError, "^$"):
                runner.read_task_file(task, require_private=True)
            self.assertEqual(runner.read_task_file(task, require_private=False), "task")

    def test_reader_uses_the_opened_descriptor_after_path_replacement(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = root / "task.txt"
            replacement = root / "replacement.txt"
            task.write_text("reviewed bytes", encoding="utf-8")
            replacement.write_text("PRIVATE-SENTINEL", encoding="utf-8")
            task.chmod(0o600)
            replacement.chmod(0o600)
            original_fstat = os.fstat
            swapped = False

            def replace_after_open(descriptor: int) -> os.stat_result:
                nonlocal swapped
                metadata = original_fstat(descriptor)
                if not swapped:
                    task.unlink()
                    task.symlink_to(replacement)
                    swapped = True
                return metadata

            with mock.patch("os.fstat", side_effect=replace_after_open):
                observed = runner.read_task_file(task, require_private=True)

        self.assertTrue(swapped)
        self.assertEqual(observed, "reviewed bytes")
        self.assertNotIn("PRIVATE-SENTINEL", observed)


if __name__ == "__main__":
    unittest.main()
