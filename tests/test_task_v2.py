import io
import pickle
import unittest

from weightclass.task_v2 import MAX_V2_TASK_BYTES, ValidatedTaskV2, read_validated_task_v2
from weightclass.v2_validation import V2ValidationError


class CountingBytesIO(io.BytesIO):
    def __init__(self, value: bytes) -> None:
        super().__init__(value)
        self.read_sizes: list[int | None] = []

    def read(self, size: int | None = -1) -> bytes:
        self.read_sizes.append(size)
        return super().read(size)


class TaskV2Tests(unittest.TestCase):
    def test_binary_reader_reads_once_and_preserves_delivery_bytes(self) -> None:
        stream = CountingBytesIO("  Fix café.  ".encode())
        task = read_validated_task_v2(stream)
        self.assertEqual(stream.read_sizes, [MAX_V2_TASK_BYTES + 1])
        self.assertEqual(task.classification_text(), "  Fix café.  ")
        self.assertEqual(task.delivery_bytes(), "  Fix café.  ".encode())

    def test_stringio_compatibility_is_deterministic(self) -> None:
        task = read_validated_task_v2(io.StringIO("검토"))
        self.assertEqual(task.classification_text(), "검토")
        self.assertEqual(task.delivery_bytes(), "검토".encode())

    def test_invalid_input_is_value_free(self) -> None:
        contents = (b"", b"\xff", b"x" * (MAX_V2_TASK_BYTES + 1))
        for content in contents:
            with self.subTest(length=len(content)):
                with self.assertRaisesRegex(V2ValidationError, "^$") as caught:
                    read_validated_task_v2(CountingBytesIO(content))
                self.assertNotIn("xff", repr(caught.exception))

    def test_task_is_opaque_noncomparable_nonhashable_nonserializable(self) -> None:
        secret = "task-secret-never-report"
        task = read_validated_task_v2(io.StringIO(secret))
        self.assertEqual(repr(task), "ValidatedTaskV2(<redacted>)")
        self.assertNotIn(secret, repr(task))
        with self.assertRaises(TypeError):
            hash(task)
        same_content = read_validated_task_v2(io.StringIO(secret))
        self.assertIs(task, task)
        self.assertIsNot(task, same_content)
        self.assertFalse(hasattr(task, "__dict__"))
        with self.assertRaises(TypeError):
            pickle.dumps(task)
        self.assertEqual(
            {name for name in dir(task) if not name.startswith("_")},
            {"classification_text", "delivery_bytes"},
        )

    def test_constructor_is_not_public(self) -> None:
        with self.assertRaises(TypeError):
            ValidatedTaskV2(b"secret", "secret")  # type: ignore[call-arg]


if __name__ == "__main__":
    unittest.main()
