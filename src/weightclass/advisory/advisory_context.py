"""Bounded, transient context assembly for one-shot advisory calls."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

from weightclass.executable_observation import observe_executable
from weightclass.v2_validation import V2ValidationError

if __package__:
    from . import readonly_snapshot, safe_git
else:  # pragma: no cover - packaged direct-script boundary
    import readonly_snapshot  # type: ignore[import-not-found,no-redef]
    import safe_git  # type: ignore[import-not-found,no-redef]

CONTEXT_MODES = ("task", "diff", "files", "repo")
MAX_CONTEXT_FILES = 64
MAX_CONTEXT_FILE_BYTES = 32 * 1024
MAX_CONTEXT_BYTES = 128 * 1024
CONTEXT_GIT_TIMEOUT = 30.0
MAX_CONTEXT_GIT_STDERR_BYTES = 64 * 1024


class AdvisoryContextError(ValueError):
    """Value-free rejection of unsafe or unsupported advisory context."""

    def __init__(self, code: str) -> None:
        super().__init__()
        self.code = code


def preflight_git(repo: Path) -> tuple[str, object]:
    """Observe one absolute non-repository Git executable without starting it."""

    selected = shutil.which("git")
    if selected is None:
        raise AdvisoryContextError("ask_context_unsupported")
    try:
        executable = Path(selected).resolve(strict=True)
        if not executable.is_absolute() or executable.is_relative_to(repo):
            raise AdvisoryContextError("ask_context_unsupported")
        return os.fspath(executable), observe_executable(os.fspath(executable))
    except AdvisoryContextError:
        raise
    except (OSError, V2ValidationError):
        raise AdvisoryContextError("ask_context_unsupported") from None


def validate_context_request(mode: str, files: Sequence[str]) -> tuple[str, ...]:
    """Validate only task-free selector syntax; do not inspect repository content."""

    if mode not in CONTEXT_MODES:
        raise AdvisoryContextError("ask_context_invalid")
    selected = tuple(files)
    if (mode == "files") != bool(selected) or len(selected) > MAX_CONTEXT_FILES:
        raise AdvisoryContextError("ask_context_invalid")
    for value in selected:
        if not _safe_relative_parts(value):
            raise AdvisoryContextError("ask_context_invalid")
    return selected


def _safe_relative_parts(value: str) -> tuple[str, ...]:
    if not isinstance(value, str):
        return ()
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError:
        return ()
    if (
        not encoded
        or len(encoded) > 4_096
        or "\\" in value
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        return ()
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix():
        return ()
    parts = path.parts
    if (
        not parts
        or any(part.casefold() == ".git" for part in parts)
        or any(part in {"", ".", ".."} for part in parts)
    ):
        return ()
    return parts


def read_relative_regular(root: Path, relative: str, maximum: int) -> bytes:
    """Read one descendant regular file without following any path component."""

    parts = _safe_relative_parts(relative)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    if (
        not parts
        or not all(isinstance(value, int) and value for value in (nofollow, directory, cloexec))
        or isinstance(maximum, bool)
        or not isinstance(maximum, int)
        or maximum <= 0
    ):
        raise AdvisoryContextError("ask_context_invalid")
    descriptors: list[int] = []
    try:
        parent = os.open(root, os.O_RDONLY | directory | nofollow | cloexec)
        descriptors.append(parent)
        for component in parts[:-1]:
            parent = os.open(
                component,
                os.O_RDONLY | directory | nofollow | cloexec,
                dir_fd=parent,
            )
            descriptors.append(parent)
        descriptor = os.open(
            parts[-1],
            os.O_RDONLY | nofollow | cloexec | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent,
        )
        descriptors.append(descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
            raise AdvisoryContextError("ask_context_unsupported")
        payload = bytearray()
        while len(payload) <= maximum:
            try:
                chunk = os.read(descriptor, min(65_536, maximum + 1 - len(payload)))
            except InterruptedError:
                continue
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > maximum:
            raise AdvisoryContextError("ask_context_unsupported")
        return bytes(payload)
    except AdvisoryContextError:
        raise
    except (OSError, TypeError, ValueError):
        raise AdvisoryContextError("ask_context_unsupported") from None
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _decode_context(payload: bytes) -> str:
    if b"\x00" in payload:
        raise AdvisoryContextError("ask_context_unsupported")
    try:
        return payload.decode("utf-8", errors="strict")
    except UnicodeError:
        raise AdvisoryContextError("ask_context_unsupported") from None


def _neutralize_prompt_markers(value: str) -> str:
    """Keep repository text from impersonating the prompt's reserved boundaries."""

    return "\n".join(
        f"> {line}" if line.strip().startswith("-----") else line for line in value.split("\n")
    )


def _files_context(
    repo: Path,
    files: Sequence[str],
    snapshot: readonly_snapshot.TreeSnapshot,
) -> str:
    sections: list[str] = []
    aggregate = 0
    expected_entries = dict(snapshot.entries)
    for relative in files:
        payload = read_relative_regular(repo, relative, MAX_CONTEXT_FILE_BYTES)
        expected = expected_entries.get(relative)
        if (
            expected is None
            or expected.kind != "file"
            or expected.size != len(payload)
            or expected.digest != hashlib.sha256(payload).digest()
        ):
            raise AdvisoryContextError("ask_context_unsupported")
        aggregate += len(payload)
        if aggregate > MAX_CONTEXT_BYTES:
            raise AdvisoryContextError("ask_context_unsupported")
        sections.append(
            f"----- FILE {relative} -----\n"
            f"{_neutralize_prompt_markers(_decode_context(payload))}\n"
            "----- END FILE -----"
        )
    return "\n".join(sections)


def _diff_context(
    repo: Path,
    environment: Mapping[str, str],
    git_preflight: tuple[str, object],
) -> str:
    executable, observation = git_preflight
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    if not all(isinstance(value, int) and value for value in (nofollow, directory, cloexec)):
        raise AdvisoryContextError("ask_context_unsupported")
    root_descriptor = -1
    git_descriptor = -1
    try:
        if observe_executable(executable) != observation:
            raise AdvisoryContextError("ask_context_unsupported")
        root_descriptor = os.open(repo, os.O_RDONLY | directory | nofollow | cloexec)
        git_descriptor = os.open(
            ".git",
            os.O_RDONLY | directory | nofollow | cloexec,
            dir_fd=root_descriptor,
        )
        root_identity = (os.fstat(root_descriptor).st_dev, os.fstat(root_descriptor).st_ino)
        git_identity = (os.fstat(git_descriptor).st_dev, os.fstat(git_descriptor).st_ino)
        result = safe_git.run(
            (
                "--no-pager",
                f"--git-dir={repo / '.git'}",
                f"--work-tree={repo}",
                "diff",
                "--no-color",
                "--no-ext-diff",
                "--no-textconv",
                "--no-renames",
                "--full-index",
                "HEAD",
                "--",
            ),
            cwd=repo,
            environment=environment,
            timeout_seconds=CONTEXT_GIT_TIMEOUT,
            max_stdout_bytes=MAX_CONTEXT_BYTES,
            max_stderr_bytes=MAX_CONTEXT_GIT_STDERR_BYTES,
            executable=executable,
        )
        current_root = os.stat(repo, follow_symlinks=False)
        current_git = os.stat(repo / ".git", follow_symlinks=False)
        if (
            root_identity != (current_root.st_dev, current_root.st_ino)
            or git_identity != (current_git.st_dev, current_git.st_ino)
            or not stat.S_ISDIR(current_root.st_mode)
            or not stat.S_ISDIR(current_git.st_mode)
        ):
            raise AdvisoryContextError("ask_context_unsupported")
    except AdvisoryContextError:
        raise
    except (OSError, ValueError, V2ValidationError, safe_git.SafeGitError):
        raise AdvisoryContextError("ask_context_unsupported") from None
    finally:
        for descriptor in (git_descriptor, root_descriptor):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
    if result.returncode != 0:
        raise AdvisoryContextError("ask_context_unsupported")
    payload = _neutralize_prompt_markers(_decode_context(result.stdout))
    return f"----- TRACKED WORKTREE DIFF -----\n{payload}\n----- END DIFF -----"


def build_context(
    mode: str,
    *,
    repo: Path,
    files: Sequence[str],
    environment: Mapping[str, str],
    snapshot: readonly_snapshot.TreeSnapshot | None = None,
    git_preflight: tuple[str, object] | None = None,
) -> str:
    """Build bounded untrusted context after egress consent and task input."""

    selected = validate_context_request(mode, files)
    if mode in {"task", "repo"}:
        return ""
    if mode == "files":
        if snapshot is None:
            raise AdvisoryContextError("ask_context_unsupported")
        return _files_context(repo, selected, snapshot)
    if git_preflight is None:
        raise AdvisoryContextError("ask_context_unsupported")
    return _diff_context(repo, environment, git_preflight)
