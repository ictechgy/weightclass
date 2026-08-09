"""Immutable execution truth shared by future schema-2 compilers and runtimes."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class FrozenCleanupV2:
    grace_seconds: int
    terminate_grace_seconds: int


@dataclass(frozen=True, slots=True)
class CompiledExecutionV2:
    canonical_descriptor_bytes: bytes
    fingerprint_payload_bytes: bytes
    route_fingerprint: str
    argv: tuple[str, ...]
    executable: str
    transport: Literal["native_stdin", "wcd2_stdin"]
    transport_version: Literal[1, 2]
    cleanup: FrozenCleanupV2
