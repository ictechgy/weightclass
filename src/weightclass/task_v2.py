"""Opaque, byte-preserving schema-2 task input."""

from __future__ import annotations

from typing import BinaryIO, NoReturn, SupportsIndex, TextIO

from .classification import InvalidTaskError, validate_task
from .v2_validation import V2ValidationError

MAX_V2_TASK_BYTES = 80_000
_CONSTRUCTION_TOKEN = object()


class ValidatedTaskV2:
    __slots__ = ("_delivery", "_text")

    def __init__(self, delivery: bytes, text: str, token: object) -> None:
        if token is not _CONSTRUCTION_TOKEN:
            raise TypeError
        self._delivery = delivery
        self._text = text

    def __repr__(self) -> str:
        return "ValidatedTaskV2(<redacted>)"

    def __eq__(self, other: object) -> bool:
        return self is other

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError

    def classification_text(self) -> str:
        return self._text

    def delivery_bytes(self) -> bytes:
        return self._delivery


def read_validated_task_v2(stream: BinaryIO | TextIO) -> ValidatedTaskV2:
    """Perform exactly one bounded read and preserve its validated UTF-8 bytes."""
    try:
        raw = stream.read(MAX_V2_TASK_BYTES + 1)
        if isinstance(raw, str):
            delivery = raw.encode("utf-8", errors="strict")
        elif isinstance(raw, bytes):
            delivery = raw
        else:
            raise V2ValidationError()
        if len(delivery) > MAX_V2_TASK_BYTES:
            raise V2ValidationError()
        text = delivery.decode("utf-8", errors="strict")
        if text.encode("utf-8", errors="strict") != delivery:
            raise V2ValidationError()
        validate_task(text)
    except (InvalidTaskError, OSError, UnicodeError) as error:
        raise V2ValidationError() from error
    return ValidatedTaskV2(delivery, text, _CONSTRUCTION_TOKEN)
