#!/usr/bin/env python3
"""Local test-only stand-in for a separately installed provider runtime."""

import sys


EXPECTED_ARGUMENTS = [
    "--provider",
    "openai",
    "--model",
    "opaque-openai-low-model",
    "--effort",
    "low",
]


if sys.argv[1:] != EXPECTED_ARGUMENTS or sys.stdin.read() != "Fix a typo.":
    raise SystemExit(9)

print("runtime-received-task")
