"""Independent byte-only WCD2 framing."""

import struct
from typing import Final

FRAME_MAGIC_V2: Final = b"WCD2"
MAX_DESCRIPTOR_BYTES_V2: Final = 262_144
MAX_TASK_BYTES_V2: Final = 80_000
MAX_COMPLETE_FRAME_BYTES_V2: Final = 342_156


class DelegationFrameV2Error(ValueError):
    """Value-free WCD2 encoding failure."""


def encode_delegation_frame_v2(descriptor: bytes, task: bytes) -> bytes:
    """Encode one complete WCD2 frame without accepting text values."""
    if (
        not isinstance(descriptor, bytes)
        or not isinstance(task, bytes)
        or not 1 <= len(descriptor) <= MAX_DESCRIPTOR_BYTES_V2
        or not 1 <= len(task) <= MAX_TASK_BYTES_V2
    ):
        raise DelegationFrameV2Error()
    frame = b"".join(
        (
            FRAME_MAGIC_V2,
            struct.pack(">I", len(descriptor)),
            struct.pack(">I", len(task)),
            descriptor,
            task,
        )
    )
    if len(frame) > MAX_COMPLETE_FRAME_BYTES_V2:
        raise DelegationFrameV2Error()
    return frame
