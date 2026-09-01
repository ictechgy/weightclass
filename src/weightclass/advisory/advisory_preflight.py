#!/usr/bin/env python3
"""Task-free local capability checks for advisory vendor CLIs."""

from __future__ import annotations

import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
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

from weightclass.executable_observation import observe_executable
from weightclass.process_context import (
    ProcessGroupAnchor,
    has_safe_child_status_context,
    wait_owned_child,
)
from weightclass.process_errors import ChildStatusLostError
from weightclass.v2_validation import V2ValidationError

MAX_PROBE_BYTES = 262_144
PROBE_TIMEOUT_SECONDS = 10.0
TASK_FREE_PROBE_CHILDREN = 2
_VERSION = re.compile(r"[ -~]{1,120}\Z")


@dataclass(frozen=True)
class CapabilitySpec:
    help_command: tuple[str, ...]
    version_command: tuple[str, ...]
    required_help_tokens: tuple[str, ...]


@dataclass(frozen=True)
class CapabilityResult:
    vendor: str
    status: str
    failure_code: str
    version: str | None

    @property
    def ready(self) -> bool:
        return self.status in {"ready", "custom_unverified"}

    def receipt(self) -> dict[str, object]:
        return {
            "vendor": self.vendor,
            "status": self.status,
            "failure_code": self.failure_code,
            "version": self.version,
        }


_SPECS: Mapping[str, CapabilitySpec] = {
    "claude": CapabilitySpec(
        ("claude", "--help"),
        ("claude", "--version"),
        (
            "--print",
            "--no-session-persistence",
            "--safe-mode",
            "--permission-mode",
            "--tools",
            "--output-format",
            "--json-schema",
            "--model",
            "--effort",
        ),
    ),
    "codex": CapabilitySpec(
        ("codex", "exec", "--help"),
        ("codex", "--version"),
        (
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "--json",
            "--model",
            "--config",
        ),
    ),
    "agy": CapabilitySpec(
        ("agy", "--help"),
        ("agy", "--version"),
        (
            "--sandbox",
            "--mode",
            "--disable-slash-commands",
            # 라우트가 stdin NDJSON 전달에 의존하므로 이 플래그가 없는 구버전
            # agy 는 태스크를 읽기 **전에** 닫아야 한다. 빼면 버전 하한이 조용해져
            # 실행 중 실패로만 드러난다.
            "--input-format",
            "--output-format",
            "--model",
            "--effort",
            "--print",
        ),
    ),
    "grok": CapabilitySpec(
        ("grok", "--help"),
        ("grok", "--version"),
        (
            "--permission-mode",
            "--reasoning-effort",
            "--no-subagents",
            "--disable-web-search",
            "--output-format",
            "--model",
            "--verbatim",
            "--prompt-file",
        ),
    ),
}


def _bounded_command(command: Sequence[str]) -> tuple[int, bytes]:
    if not has_safe_child_status_context():
        raise OSError
    with tempfile.TemporaryDirectory(prefix="wclass-cli-check-") as directory:
        environment = {
            name: value
            for name, value in os.environ.items()
            if name
            in {
                "PATH",
                "USER",
                "LOGNAME",
                "SHELL",
                "TERM",
                "LANG",
                "LC_ALL",
                "LC_CTYPE",
                "TZ",
            }
        }
        environment["HOME"] = os.environ.get("HOME", directory)
        environment["TMPDIR"] = directory
        process = subprocess.Popen(
            tuple(command),
            cwd=directory,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        return _capture_bounded_command(process)


def _capture_bounded_command(process: subprocess.Popen[bytes]) -> tuple[int, bytes]:
    assert process.stdout is not None and process.stderr is not None
    anchor = ProcessGroupAnchor.open(process)
    selector: selectors.BaseSelector | None = None
    payload = bytearray()
    deadline = time.monotonic() + PROBE_TIMEOUT_SECONDS

    def stop() -> None:
        anchor.signal(signal.SIGKILL)
        try:
            wait_owned_child(process, timeout=1.0)
        except ChildStatusLostError:
            anchor.release_after_status_loss()
            raise
        except subprocess.TimeoutExpired:

            def reap() -> None:
                try:
                    wait_owned_child(process)
                except OSError:
                    pass
                finally:
                    anchor.close()

            threading.Thread(target=reap, name="wclass-preflight-reaper", daemon=True).start()

    try:
        selector = selectors.DefaultSelector()
        for stream in (process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                stop()
                return 124, b""
            for key, _ in selector.select(remaining):
                chunk = os.read(key.fd, 65_536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    cast(BinaryIO, key.fileobj).close()
                    continue
                available = MAX_PROBE_BYTES - len(payload)
                payload.extend(chunk[:available])
                if len(chunk) > available:
                    stop()
                    return 125, b""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            stop()
            return 124, b""
        try:
            return wait_owned_child(process, timeout=remaining), bytes(payload)
        except ChildStatusLostError:
            anchor.release_after_status_loss()
            raise
    except ChildStatusLostError:
        # Status loss releases the numeric PID/PGID; never signal it again.
        raise
    except BaseException:
        stop()
        raise
    finally:
        anchor.close()
        if selector is not None:
            selector.close()
        for stream in (process.stdout, process.stderr):
            if not stream.closed:
                stream.close()


def _safe_version(payload: bytes) -> str | None:
    try:
        first = payload.decode("utf-8", errors="strict").splitlines()[0].strip()
    except (IndexError, UnicodeDecodeError):
        return None
    return first if _VERSION.fullmatch(first) else None


def _workspace_root(current: Path) -> Path | None:
    """Return the nearest repository boundary without invoking repository code."""

    for candidate in (current, *current.parents):
        marker = candidate / ".git"
        if marker.is_dir() or marker.is_file():
            return candidate
    return None


def check_local_capability(vendor: str, executable: str) -> CapabilityResult:
    """Check one executable without task bytes, provider calls, or persisted output."""

    if not vendor or not executable or "\x00" in executable:
        return CapabilityResult(vendor, "invalid", "invalid_invocation", None)
    resolved = shutil.which(executable)
    if resolved is None:
        return CapabilityResult(vendor, "missing", "executable_missing", None)
    try:
        if not Path(resolved).is_absolute():
            return CapabilityResult(vendor, "unsafe", "unsafe_executable", None)
        resolved_path = Path(resolved).resolve(strict=True)
        current = Path.cwd().resolve()
        workspace_root = _workspace_root(current)
        if resolved_path.parent == current or (
            workspace_root is not None and resolved_path.is_relative_to(workspace_root)
        ):
            return CapabilityResult(vendor, "unsafe", "unsafe_executable", None)
        observe_executable(os.fspath(resolved_path))
    except (OSError, V2ValidationError):
        return CapabilityResult(vendor, "unsafe", "unsafe_executable", None)
    spec = _SPECS.get(vendor)
    if spec is None:
        return CapabilityResult(vendor, "custom_unverified", "none", None)
    try:
        help_code, help_payload = _bounded_command(
            (os.fspath(resolved_path), *spec.help_command[1:])
        )
        version_code, version_payload = _bounded_command(
            (os.fspath(resolved_path), *spec.version_command[1:])
        )
    except (OSError, subprocess.SubprocessError):
        return CapabilityResult(vendor, "failed", "local_probe_failed", None)
    version = _safe_version(version_payload) if version_code == 0 else None
    if help_code != 0:
        return CapabilityResult(vendor, "failed", "local_probe_failed", version)
    help_text = help_payload.decode("utf-8", errors="replace")
    if any(token not in help_text for token in spec.required_help_tokens):
        return CapabilityResult(vendor, "incompatible", "cli_incompatible", version)
    return CapabilityResult(vendor, "ready", "none", version)
