from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from weightclass.advisory import bounded_io


class AdvisoryBoundedIoTests(unittest.TestCase):
    def test_regular_file_limits_and_private_policy_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input"
            path.write_bytes(b"bounded")
            path.chmod(0o600)
            self.assertEqual(
                bounded_io.read_regular_bytes(
                    path,
                    7,
                    require_nonempty=True,
                    require_current_owner=True,
                    require_private=True,
                ),
                b"bounded",
            )
            with self.assertRaisesRegex(bounded_io.BoundedFileError, "^$"):
                bounded_io.read_regular_bytes(path, 6)
            path.write_bytes(b"")
            with self.assertRaisesRegex(bounded_io.BoundedFileError, "^$"):
                bounded_io.read_regular_bytes(path, 7, require_nonempty=True)
            path.write_bytes(b"bounded")
            path.chmod(0o644)
            with self.assertRaisesRegex(bounded_io.BoundedFileError, "^$"):
                bounded_io.read_regular_bytes(path, 7, require_private=True)

    def test_symlink_and_fifo_fail_closed_without_touching_the_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.write_bytes(b"outside sentinel")
            link = root / "link"
            link.symlink_to(target)
            fifo = root / "fifo"
            os.mkfifo(fifo, 0o600)
            for candidate in (link, fifo):
                with self.subTest(candidate=candidate.name):
                    with self.assertRaisesRegex(bounded_io.BoundedFileError, "^$"):
                        bounded_io.read_regular_bytes(candidate, 1024)
            self.assertEqual(target.read_bytes(), b"outside sentinel")

    def test_reader_keeps_the_opened_file_after_path_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "input"
            replacement = root / "replacement"
            path.write_bytes(b"reviewed bytes")
            replacement.write_bytes(b"PRIVATE-REPLACEMENT")
            original_fstat = os.fstat
            swapped = False

            def replace_after_open(descriptor: int) -> os.stat_result:
                nonlocal swapped
                metadata = original_fstat(descriptor)
                if not swapped:
                    path.unlink()
                    path.symlink_to(replacement)
                    swapped = True
                return metadata

            with mock.patch(
                "weightclass.advisory.bounded_io.os.fstat",
                side_effect=replace_after_open,
            ):
                payload = bounded_io.read_regular_bytes(path, 1024)

        self.assertTrue(swapped)
        self.assertEqual(payload, b"reviewed bytes")


if __name__ == "__main__":
    unittest.main()
