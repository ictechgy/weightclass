#!/usr/bin/env python3
"""Test-only protocol-1 runtime. It never prints or persists task content."""

import hashlib
import json
import os
import struct
import sys
import time

EXPECTED_TASK = "Apply the reviewed change. 테스트"
MAX_DESCRIPTOR_BYTES = 262_144
MAX_TASK_BYTES = 80_000


def _read_exact(length: int) -> bytes:
    contents = bytearray()
    while len(contents) < length:
        chunk = sys.stdin.buffer.read(length - len(contents))
        if not chunk:
            raise SystemExit(20)
        contents.extend(chunk)
    return bytes(contents)


def _read_length(maximum: int) -> int:
    length = int(struct.unpack(">I", _read_exact(4))[0])
    if length > maximum:
        raise SystemExit(21)
    return length


def _validate_descriptor(contents: bytes) -> dict[str, object]:
    try:
        descriptor = json.loads(contents.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SystemExit(22) from None
    if not isinstance(descriptor, dict):
        raise SystemExit(23)
    fingerprint = descriptor.pop("route_fingerprint", None)
    canonical = json.dumps(
        descriptor,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    expected = f"sha256:{hashlib.sha256(canonical).hexdigest()}"
    if fingerprint != expected:
        raise SystemExit(24)
    descriptor["route_fingerprint"] = fingerprint
    return descriptor


def main() -> int:
    if sys.argv[1:] != ["--weightclass-delegation-protocol", "1"]:
        return 25
    mode = os.environ.get("WEIGHTCLASS_FAKE_DELEGATION_MODE", "success")
    if mode == "close-stdin-and-hang":
        os.close(sys.stdin.fileno())
        print(f"fake-runtime-pid:{os.getpid()}", flush=True)
        time.sleep(60)
        return 26

    if _read_exact(4) != b"WCD1":
        return 27
    descriptor = _validate_descriptor(_read_exact(_read_length(MAX_DESCRIPTOR_BYTES)))
    task = _read_exact(_read_length(MAX_TASK_BYTES))
    if sys.stdin.buffer.read(1) != b"":
        return 28
    try:
        decoded_task = task.decode("utf-8")
    except UnicodeDecodeError:
        return 29

    if mode == "exit-9":
        print("fake-runtime-nonzero", file=sys.stderr, flush=True)
        return 9
    if mode != "success" or decoded_task != EXPECTED_TASK:
        return 30

    print("fake-runtime-ok", flush=True)
    print(f"fake-runtime-fingerprint:{descriptor['route_fingerprint']}", flush=True)
    print("fake-runtime-stderr", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
