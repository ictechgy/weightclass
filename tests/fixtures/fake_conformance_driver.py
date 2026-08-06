#!/usr/bin/env python3
"""Test-only conformance driver. It is not independent qualification evidence."""

import json
import os
import subprocess
import sys
import time


def main() -> int:
    if sys.argv[1:] != ["--weightclass-conformance-driver", "1"]:
        return 20
    try:
        request = json.load(sys.stdin)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 21
    if not isinstance(request, dict) or set(request) != {
        "case",
        "case_id",
        "driver_protocol_version",
        "runtime_path",
        "workspace_path",
    }:
        return 22
    if request["driver_protocol_version"] != 1:
        return 23
    case_id = request["case_id"]
    if not isinstance(case_id, str):
        return 24
    mode = os.environ.get("WEIGHTCLASS_FAKE_CONFORMANCE_MODE", "pass")
    target = os.environ.get("WEIGHTCLASS_FAKE_CONFORMANCE_TARGET", "")
    if case_id == target and mode == "hang":
        pid_path = os.environ.get("WEIGHTCLASS_FAKE_CONFORMANCE_PID_PATH")
        if pid_path is None:
            return 25
        with open(pid_path, "w", encoding="ascii") as pid_file:
            pid_file.write(str(os.getpid()))
        time.sleep(60)
    if case_id == target and mode == "mutate-runtime":
        runtime_path = request["runtime_path"]
        if not isinstance(runtime_path, str):
            return 26
        with open(runtime_path, "r+b") as runtime_file:
            first_byte = runtime_file.read(1)
            if not first_byte:
                return 27
            runtime_file.seek(0)
            runtime_file.write(bytes([first_byte[0] ^ 1]))
    if case_id == target and mode == "leak":
        descendant = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        pid_path = os.environ.get("WEIGHTCLASS_FAKE_CONFORMANCE_PID_PATH")
        if pid_path is None:
            return 28
        with open(pid_path, "w", encoding="ascii") as pid_file:
            pid_file.write(str(descendant.pid))
    response_case_id = f"{case_id}-spoofed" if case_id == target and mode == "spoof" else case_id
    passed = not (case_id == target and mode == "fail")
    suffix = " " * 5_000 if case_id == target and mode == "oversized" else ""
    print(
        json.dumps(
            {"case_id": response_case_id, "passed": passed},
            sort_keys=True,
            separators=(",", ":"),
        )
        + suffix
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
