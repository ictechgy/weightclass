#!/usr/bin/env python3
"""Fixed test-only runtime used to characterize conformance protocol v2."""

import sys
from pathlib import Path

MARKER_BYTES = b"weightclass-v2-marker-v1\n"
SENTINEL_ARGUMENTS = ["--weightclass-test-sentinel", "1"]


def main() -> int:
    if sys.argv[1:] != SENTINEL_ARGUMENTS:
        return 20
    try:
        Path(__file__).with_suffix(".marker").write_bytes(MARKER_BYTES)
    except OSError:
        return 21
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
