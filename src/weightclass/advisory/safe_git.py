"""Bounded, configuration-hardened Git execution for advisory-owned calls."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, cast

if not __package__:
    source_root = str(Path(__file__).resolve().parents[2])
    if source_root not in sys.path:
        sys.path.insert(0, source_root)

from weightclass.process_context import ProcessGroupAnchor, wait_owned_child
from weightclass.process_errors import ChildStatusLostError

SAFE_GIT_OPTIONS = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")
READ_CHUNK_BYTES = 65_536
REAP_SECONDS = 1.0


class SafeGitError(ValueError):
    """Value-free failure of one bounded Git invocation."""

    code: str

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__()


@dataclass(frozen=True, slots=True)
class GitResult:
    returncode: int
    stdout: bytes
    stderr: bytes


def hardened_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Remove inherited Git routing and disable executable repository config."""

    environment = {name: value for name, value in source.items() if not name.startswith("GIT_")}
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return environment


def _reap_in_background(process: subprocess.Popen[bytes], anchor: ProcessGroupAnchor) -> None:
    def reap() -> None:
        try:
            wait_owned_child(process)
        except OSError:
            pass
        finally:
            anchor.close()

    threading.Thread(target=reap, name="wclass-git-reaper", daemon=True).start()


def _terminate(process: subprocess.Popen[bytes], anchor: ProcessGroupAnchor) -> None:
    try:
        anchor.signal(signal.SIGKILL)
    except ChildStatusLostError as error:
        raise SafeGitError("status_lost") from error
    except OSError as error:
        raise SafeGitError("termination_failed") from error
    try:
        wait_owned_child(process, timeout=REAP_SECONDS)
    except ChildStatusLostError as error:
        anchor.release_after_status_loss()
        raise SafeGitError("status_lost") from error
    except subprocess.TimeoutExpired:
        _reap_in_background(process, anchor)
    except OSError:
        pass


def run(
    arguments: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: float,
    max_stdout_bytes: int,
    max_stderr_bytes: int,
) -> GitResult:
    """Run one shell-free Git command with independent bounded output lanes."""

    if (
        not arguments
        or not 0 < timeout_seconds
        or not 0 < max_stdout_bytes
        or not 0 < max_stderr_bytes
        or any(not isinstance(argument, str) or "\x00" in argument for argument in arguments)
    ):
        raise SafeGitError("invalid_invocation")
    try:
        process = subprocess.Popen(
            ("git", *SAFE_GIT_OPTIONS, *arguments),
            cwd=cwd,
            env=hardened_environment(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            start_new_session=True,
        )
    except OSError as error:
        raise SafeGitError("start_failed") from error
    try:
        anchor = ProcessGroupAnchor.open(process)
    except ChildStatusLostError as error:
        raise SafeGitError("status_lost") from error
    assert process.stdout is not None and process.stderr is not None
    selector: selectors.BaseSelector | None = None
    stdout = bytearray()
    stderr = bytearray()
    outputs = {
        process.stdout.fileno(): (stdout, max_stdout_bytes),
        process.stderr.fileno(): (stderr, max_stderr_bytes),
    }
    deadline = time.monotonic() + timeout_seconds
    try:
        selector = selectors.DefaultSelector()
        for stream in (process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate(process, anchor)
                raise SafeGitError("timed_out")
            for key, _ in selector.select(remaining):
                try:
                    chunk = os.read(key.fd, READ_CHUNK_BYTES)
                except (BlockingIOError, InterruptedError):
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    cast(BinaryIO, key.fileobj).close()
                    continue
                output, maximum = outputs[key.fd]
                available = maximum - len(output)
                if len(chunk) > available:
                    _terminate(process, anchor)
                    raise SafeGitError("output_limited")
                output.extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate(process, anchor)
            raise SafeGitError("timed_out")
        try:
            returncode = wait_owned_child(process, timeout=remaining)
        except ChildStatusLostError as error:
            anchor.release_after_status_loss()
            raise SafeGitError("status_lost") from error
        except subprocess.TimeoutExpired:
            _terminate(process, anchor)
            raise SafeGitError("timed_out") from None
        return GitResult(returncode, bytes(stdout), bytes(stderr))
    except SafeGitError:
        raise
    except BaseException:
        try:
            _terminate(process, anchor)
        except SafeGitError:
            pass
        raise
    finally:
        anchor.close()
        if selector is not None:
            selector.close()
        for stream in (process.stdout, process.stderr):
            if not stream.closed:
                stream.close()
