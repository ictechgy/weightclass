#!/usr/bin/env python3
"""Run the workflow verifier committed at HEAD, never a candidate-edited copy."""

from __future__ import annotations

import os
import selectors
import stat
import subprocess
import sys
import tempfile
import time
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
_SAFE_GIT_OPTIONS = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")


def _git_environment() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _git_quiet(*arguments: str) -> int:
    completed = subprocess.run(
        ["git", *_SAFE_GIT_OPTIONS, *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=30,
        env=_git_environment(),
    )
    return completed.returncode


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=1)
    except (OSError, subprocess.SubprocessError):
        pass


def _git_bounded_stdout(max_stdout_bytes: int, *arguments: str) -> bytes | None:
    try:
        process = subprocess.Popen(
            ["git", *_SAFE_GIT_OPTIONS, *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_git_environment(),
            close_fds=True,
        )
    except OSError:
        return None
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    output = bytearray()
    deadline = time.monotonic() + 30
    try:
        os.set_blocking(process.stdout.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            ready = selector.select(remaining)
            if not ready:
                return None
            for key, _ in ready:
                try:
                    chunk = os.read(key.fd, 65_536)
                except (BlockingIOError, InterruptedError):
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    process.stdout.close()
                    continue
                available = max_stdout_bytes - len(output)
                if len(chunk) > available:
                    return None
                output.extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        if process.wait(timeout=remaining) != 0:
            return None
        return bytes(output)
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        selector.close()
        if not process.stdout.closed:
            process.stdout.close()
        if process.poll() is None:
            _stop_process(process)


def _git_blob(specification: str) -> bytes | None:
    object_id_output = _git_bounded_stdout(
        65, "rev-parse", "--verify", "--end-of-options", specification
    )
    if object_id_output is None:
        return None
    object_id = object_id_output[:-1] if object_id_output.endswith(b"\n") else object_id_output
    if len(object_id) not in {40, 64} or any(byte not in b"0123456789abcdef" for byte in object_id):
        return None
    object_name = object_id.decode("ascii")
    object_type = _git_bounded_stdout(5, "cat-file", "-t", object_name)
    if object_type not in {b"blob", b"blob\n"}:
        return None
    size_output = _git_bounded_stdout(21, "cat-file", "-s", object_name)
    if size_output is None:
        return None
    size_text = size_output[:-1] if size_output.endswith(b"\n") else size_output
    if (
        not size_text.isdigit()
        or len(size_text) > 20
        or not 0 < int(size_text) <= MAX_VERIFIER_BYTES
    ):
        return None
    blob = _git_bounded_stdout(MAX_VERIFIER_BYTES, "cat-file", "blob", object_name)
    if blob is None or len(blob) != int(size_text):
        return None
    return blob


def main() -> int:
    root = Path.cwd()
    workflow = os.environ.get("WCLASS_ADVISORY_WORKFLOW", "implementation")
    verifier_path = VERIFIER_PATHS.get(workflow)
    if verifier_path is None:
        print("verification failed: invalid advisory workflow")
        return 1
    cached_unchanged = _git_quiet(
        "diff", "--cached", "--quiet", "--no-ext-diff", "--no-textconv", "--", verifier_path
    )
    worktree_unchanged = _git_quiet(
        "diff", "--quiet", "--no-ext-diff", "--no-textconv", "--", verifier_path
    )
    if cached_unchanged != 0 or worktree_unchanged != 0:
        print("verification failed: candidate changed the protected verifier")
        return 1

    baseline = _git_blob(f"HEAD:{verifier_path}")
    if baseline is None:
        print("verification failed: committed verifier is missing or invalid")
        return 1

    descriptor, temporary_name = tempfile.mkstemp(prefix="wclass-verify-")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            handle.write(baseline)
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
