import unittest

from weightclass.delegation_protocol import encode_delegation_frame
from weightclass.delegation_v2_protocol import (
    MAX_COMPLETE_FRAME_BYTES_V2,
    MAX_DESCRIPTOR_BYTES_V2,
    MAX_TASK_BYTES_V2,
    DelegationFrameV2Error,
    encode_delegation_frame_v2,
)


class DelegationV2ProtocolTests(unittest.TestCase):
    def test_exact_wcd2_layout_and_wcd1_unchanged(self) -> None:
        self.assertEqual(encode_delegation_frame_v2(b"{}", b"task"), b"WCD2\0\0\0\2\0\0\0\4{}task")
        self.assertEqual(encode_delegation_frame(b"{}", "task"), b"WCD1\0\0\0\2{}\0\0\0\4task")

    def test_byte_only_and_every_individual_bound(self) -> None:
        for descriptor, task in (
            (b"", b"x"),
            (b"x", b""),
            (b"x" * (MAX_DESCRIPTOR_BYTES_V2 + 1), b"x"),
            (b"x", b"x" * (MAX_TASK_BYTES_V2 + 1)),
            ("x", b"x"),
            (b"x", "x"),
        ):
            with (
                self.subTest(descriptor_type=type(descriptor), task_type=type(task)),
                self.assertRaises(DelegationFrameV2Error),
            ):
                encode_delegation_frame_v2(descriptor, task)  # type: ignore[arg-type]
        self.assertEqual(len(encode_delegation_frame_v2(b"x", b"x")), 14)
        self.assertEqual(
            len(
                encode_delegation_frame_v2(b"x" * MAX_DESCRIPTOR_BYTES_V2, b"x" * MAX_TASK_BYTES_V2)
            ),
            MAX_COMPLETE_FRAME_BYTES_V2,
        )


if __name__ == "__main__":
    unittest.main()
