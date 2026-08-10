import stat
import tempfile
import unittest
from pathlib import Path

from weightclass.executable_observation import observe_executable
from weightclass.v2_validation import V2ValidationError


def make_executable(path: Path, content: bytes = b"") -> None:
    path.write_bytes(content)
    path.chmod(0o700)


class ExecutableObservationTests(unittest.TestCase):
    def test_observation_records_stable_lstat_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "tool"
            make_executable(executable, b"x")
            lexical = str(root / "." / "tool")
            first = observe_executable(lexical)
            second = observe_executable(lexical)
            self.assertEqual(first, second)
            self.assertEqual(first.lexical_path, lexical)
            self.assertEqual(first.size, 1)
            self.assertEqual(first.file_type, stat.S_IFREG)
            self.assertTrue(stat.S_ISREG(first.mode))
            self.assertTrue(first.executable_bit)

    def test_zero_size_executable_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "empty"
            make_executable(executable)
            self.assertEqual(observe_executable(str(executable)).size, 0)

    def test_posix_executable_bits_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for mode in (0o600, 0o400, 0o000):
                with self.subTest(mode=mode):
                    path = Path(directory) / f"tool-{mode}"
                    path.write_bytes(b"x")
                    path.chmod(mode)
                    with self.assertRaisesRegex(V2ValidationError, "^$"):
                        observe_executable(str(path))

    def test_any_posix_executable_bit_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for index, bit in enumerate((stat.S_IXUSR, stat.S_IXGRP, stat.S_IXOTH)):
                path = Path(directory) / f"tool-{index}"
                path.write_bytes(b"x")
                path.chmod(bit)
                self.assertTrue(observe_executable(str(path)).executable_bit)

    def test_final_symlink_and_nonregular_are_rejected_value_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            target = directory / "target"
            make_executable(target, b"secret-content")
            symlink = directory / "link"
            symlink.symlink_to(target)
            nonregular = directory / "nested"
            nonregular.mkdir()
            nonregular.chmod(0o700)
            for path in (symlink, nonregular):
                with self.subTest(kind=path.name):
                    with self.assertRaisesRegex(V2ValidationError, "^$") as caught:
                        observe_executable(str(path))
                    self.assertNotIn(str(path), repr(caught.exception))

    def test_observation_changes_when_file_metadata_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tool"
            make_executable(path, b"a")
            before = observe_executable(str(path))
            path.write_bytes(b"longer")
            path.chmod(0o700)
            after = observe_executable(str(path))
            self.assertNotEqual(before, after)

    def test_path_must_be_nonempty_and_bounded(self) -> None:
        for path in ("", "relative/tool", "/" + "x" * 4097):
            with self.subTest(length=len(path)):
                with self.assertRaisesRegex(V2ValidationError, "^$"):
                    observe_executable(path)


if __name__ == "__main__":
    unittest.main()
