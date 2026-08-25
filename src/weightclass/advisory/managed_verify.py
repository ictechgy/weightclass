#!/usr/bin/env python3
"""Run the workflow verifier committed at HEAD, never a candidate-edited copy."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_VERIFIER_BYTES = 1_048_576
MAX_RESULT_BYTES = 131_072
TIMEOUT_SECONDS = 840
VERIFIER_PATHS = {
    "implementation": ".weightclass/verify",
    "review": ".weightclass/verify-review",
    "research": ".weightclass/verify-research",
    "diagnosis": ".weightclass/verify-diagnosis",
    "design": ".weightclass/verify-design",
}


def _git_environment() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _git(*arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=30,
        env=_git_environment(),
    )


def main() -> int:
    root = Path.cwd()
    workflow = os.environ.get("WCLASS_ADVISORY_WORKFLOW", "implementation")
    verifier_path = VERIFIER_PATHS.get(workflow)
    if verifier_path is None:
        print("verification failed: invalid advisory workflow")
        return 1
    cached_unchanged = _git("diff", "--cached", "--quiet", "--", verifier_path)
    worktree_unchanged = _git("diff", "--quiet", "--", verifier_path)
    if cached_unchanged.returncode != 0 or worktree_unchanged.returncode != 0:
        print("verification failed: candidate changed the protected verifier")
        return 1

    baseline = _git("show", f"HEAD:{verifier_path}")
    if baseline.returncode != 0 or not baseline.stdout or len(baseline.stdout) > MAX_VERIFIER_BYTES:
        print("verification failed: committed verifier is missing or invalid")
        return 1

    descriptor, temporary_name = tempfile.mkstemp(prefix="wclass-verify-")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            handle.write(baseline.stdout)
            handle.flush()
            os.fsync(handle.fileno())
        verifier_input: bytes | None = None
        if workflow != "implementation":
            verifier_input = sys.stdin.buffer.read(MAX_RESULT_BYTES + 1)
            if len(verifier_input) > MAX_RESULT_BYTES:
                print("verification failed: evidence result is too large")
                return 1
        completed = subprocess.run(
            [str(temporary)],
            cwd=root,
            input=verifier_input if verifier_input is not None else b"",
            check=False,
            timeout=TIMEOUT_SECONDS,
        )
        return completed.returncode
    except (OSError, subprocess.SubprocessError):
        print("verification failed: committed verifier could not run")
        return 1
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
