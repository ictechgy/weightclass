"""Bounded binary framing for one external delegation runtime."""

import struct
from typing import Final

FRAME_MAGIC: Final = b"WCD1"
MAX_DESCRIPTOR_BYTES: Final = 262_144
MAX_TASK_BYTES: Final = 80_000
MAX_COMPLETE_FRAME_BYTES: Final = 342_156


class DelegationFrameError(ValueError):
    """Raised without frame or task data when protocol encoding fails."""


def encode_delegation_frame(descriptor: bytes, task: str) -> bytes:
    """Build and bound the complete protocol-1 frame before process creation."""
    if not isinstance(descriptor, bytes) or not 1 <= len(descriptor) <= MAX_DESCRIPTOR_BYTES:
        raise DelegationFrameError()
    try:
        task_bytes = task.encode("utf-8")
    except (AttributeError, UnicodeEncodeError):
        raise DelegationFrameError() from None
    if not 1 <= len(task_bytes) <= MAX_TASK_BYTES:
        raise DelegationFrameError()
    frame = b"".join(
        (
            FRAME_MAGIC,
            struct.pack(">I", len(descriptor)),
            descriptor,
            struct.pack(">I", len(task_bytes)),
            task_bytes,
        )
    )
    if len(frame) > MAX_COMPLETE_FRAME_BYTES:
        raise DelegationFrameError()
    return frame
