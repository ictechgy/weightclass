"""Hostile synthetic child modes for the test-only probe runner."""

import json
import os
import sys
import time
from typing import Final

from tests.synthetic_probe_protocol import PROBE_PROTOCOL_ID
from tests.synthetic_probe_runner import MAX_FRAME_BYTES, MAX_TRAFFIC_BYTES

_IDS: Final = (
    "wcp-selftest/v1/child-start",
    "wcp-selftest/v1/direct-child-exit",
)
_RETAINED_WRITER_SECONDS: Final = 1.2


def _payload(sequence: int, self_test_id: str) -> bytes:
    return json.dumps(
        {
            "probe_protocol_id": PROBE_PROTOCOL_ID,
            "self_test_id": self_test_id,
            "sequence": sequence,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _frame(payload: bytes) -> bytes:
    return len(payload).to_bytes(4, "big") + payload


def _spawn_writer_descendant(fd: int, *, delay_seconds: float, tail: bytes = b"") -> None:
    pid = os.fork()
    if pid != 0:
        return
    try:
        time.sleep(delay_seconds)
        if tail:
            try:
                os.write(fd, tail)
            except OSError:
                pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        os._exit(0)


def main() -> int:
    mode = sys.argv[1]
    fd = int(os.environ["WCP_SYNTHETIC_FRAME_FD"])
    clean = _frame(_payload(0, _IDS[0])) + _frame(_payload(1, _IDS[1]))
    if mode == "clean":
        os.write(fd, clean)
    elif mode == "minimal-environment":
        if "WCP_FAKE_SENSITIVE_SENTINEL" in os.environ:
            return 41
        os.write(fd, clean)
    elif mode == "delayed-traffic-after-direct-exit":
        _spawn_writer_descendant(fd, delay_seconds=0.08, tail=b"x")
        os.write(fd, clean)
    elif mode == "retained-writer":
        _spawn_writer_descendant(fd, delay_seconds=_RETAINED_WRITER_SECONDS)
        os.write(fd, clean)
    elif mode == "oversized-retained-writer":
        _spawn_writer_descendant(fd, delay_seconds=_RETAINED_WRITER_SECONDS)
        os.write(fd, b"x" * (MAX_TRAFFIC_BYTES + 1))
    elif mode == "stdout-spoof":
        sys.stdout.write('{"claimed_success":false,"telemetry":"child-supplied"}')
        os.write(fd, clean)
    elif mode == "self-attested-success":
        sys.stdout.write('{"claimed_success":true,"telemetry":"all-pass"}')
        os.write(fd, clean)
        return 23
    elif mode == "substitute-argv":
        os.write(fd, _frame(json.dumps({"selected_argv": ["trusted"]}).encode()))
    elif mode == "malformed":
        os.write(fd, _frame(b"{"))
    elif mode == "truncated":
        os.write(fd, (20).to_bytes(4, "big") + b"short")
    elif mode == "duplicate":
        os.write(fd, _frame(_payload(0, _IDS[0])) * 2)
    elif mode == "reordered":
        os.write(fd, _frame(_payload(1, _IDS[1])) + _frame(_payload(0, _IDS[0])))
    elif mode == "unexpected-id":
        os.write(fd, _frame(_payload(0, "wcp-selftest/v1/selected-argv")))
    elif mode == "oversized":
        os.write(fd, (MAX_FRAME_BYTES + 1).to_bytes(4, "big") + b"x")
    elif mode == "oversized-traffic":
        frame = _frame(b"x" * MAX_FRAME_BYTES)
        os.write(fd, frame * 5)
    elif mode == "duplicate-json-key":
        os.write(
            fd,
            _frame(
                b'{"probe_protocol_id":"weightclass.synthetic-probe-v1",'
                b'"self_test_id":"wcp-selftest/v1/child-start",'
                b'"sequence":0,"sequence":0}'
            )
            + _frame(_payload(1, _IDS[1])),
        )
    elif mode == "timeout":
        time.sleep(5.0)
    elif mode != "clean-no-frames":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
