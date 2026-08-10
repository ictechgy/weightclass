#!/usr/bin/env python3
import sys

if __name__ == "__main__":
    if sys.argv[1:] != ["--weightclass-delegation-protocol", "2"]:
        raise SystemExit(9)
    if not sys.stdin.buffer.read().startswith(b"WCD2"):
        raise SystemExit(8)
