"""Bounded text-pipe capture with owned process-group cleanup."""

from __future__ import annotations

import os
import selectors
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import BinaryIO, cast

CAPTURE_POLL_SECONDS = 0.1
CAPTURE_CLEANUP_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class CaptureResult:
    stdout: str
    stderr: str
    returncode: int | None
    timed_out: bool
    output_limited: bool


def _close_stream(selector: selectors.BaseSelector, stream: BinaryIO | None) -> None:
    if stream is None or stream.closed:
        return
    try:
        selector.unregister(stream)
    except (KeyError, ValueError):
        pass
    try:
        stream.close()
    except OSError:
        pass


def _bounded_reap(process: subprocess.Popen[str]) -> None:
    try:
        process.wait(timeout=CAPTURE_CLEANUP_SECONDS)
        return
    except (ChildProcessError, OSError):
        return
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except (OSError, ProcessLookupError):
            pass
    try:
        process.wait(timeout=CAPTURE_CLEANUP_SECONDS)
        return
    except (ChildProcessError, OSError):
        return
    except subprocess.TimeoutExpired:
        pass

    def reap() -> None:
        try:
            process.wait()
        except (ChildProcessError, OSError):
            pass

    threading.Thread(target=reap, name="wclass-capture-reaper", daemon=True).start()


def terminate_text_process(
    process: subprocess.Popen[str],
    terminate_group: Callable[[subprocess.Popen[str]], None],
) -> None:
    """Terminate one owned session and close every inherited pipe."""

    terminate_group(process)
    _bounded_reap(process)
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None and not stream.closed:
            try:
                stream.close()
            except OSError:
                pass


def capture_text_process(
    process: subprocess.Popen[str],
    input_text: str | None,
    *,
    timeout_seconds: float,
    max_output_bytes: int,
    terminate_group: Callable[[subprocess.Popen[str]], None],
) -> CaptureResult:
    """Exchange bounded UTF-8 bytes and own timeout, overflow, and interrupt cleanup."""

    if timeout_seconds <= 0 or max_output_bytes <= 0:
        raise ValueError
    if process.stdout is None or process.stderr is None:
        raise ValueError
    selector = selectors.DefaultSelector()
    stdout = bytearray()
    stderr = bytearray()
    outputs = {process.stdout.fileno(): stdout, process.stderr.fileno(): stderr}
    input_bytes = (input_text or "").encode("utf-8")
    input_offset = 0
    timed_out = False
    output_limited = False
    deadline = time.monotonic() + timeout_seconds
    streams = (process.stdin, process.stdout, process.stderr)
    try:
        for stream in (process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, "output")
        if process.stdin is None or not input_bytes:
            _close_stream(selector, cast(BinaryIO | None, process.stdin))
        else:
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, "input")

        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            events = selector.select(min(remaining, CAPTURE_POLL_SECONDS))
            for key, event_mask in events:
                stream = cast(BinaryIO, key.fileobj)
                if key.data == "input" and event_mask & selectors.EVENT_WRITE:
                    try:
                        written = os.write(stream.fileno(), input_bytes[input_offset:])
                    except (BlockingIOError, InterruptedError):
                        continue
                    except (BrokenPipeError, OSError):
                        _close_stream(selector, stream)
                        continue
                    if written <= 0:
                        _close_stream(selector, stream)
                        continue
                    input_offset += written
                    if input_offset == len(input_bytes):
                        _close_stream(selector, stream)
                    continue
                if key.data != "output" or not event_mask & selectors.EVENT_READ:
                    continue
                try:
                    chunk = os.read(stream.fileno(), 65_536)
                except (BlockingIOError, InterruptedError):
                    continue
                if not chunk:
                    _close_stream(selector, stream)
                    continue
                available = max_output_bytes - len(stdout) - len(stderr)
                if available > 0:
                    outputs[stream.fileno()].extend(chunk[:available])
                if len(chunk) > max(available, 0):
                    output_limited = True
                    break
            if output_limited:
                break

        if not timed_out and not output_limited:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
            else:
                try:
                    process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    timed_out = True
        if timed_out or output_limited:
            terminate_group(process)
            _bounded_reap(process)
        return CaptureResult(
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
            process.returncode,
            timed_out,
            output_limited,
        )
    except BaseException:
        terminate_group(process)
        _bounded_reap(process)
        raise
    finally:
        for cleanup_stream in streams:
            _close_stream(selector, cast(BinaryIO | None, cleanup_stream))
        selector.close()
