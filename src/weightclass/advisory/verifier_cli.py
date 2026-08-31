#!/usr/bin/env python3
"""Scaffold and inspect explicit campaign verifier contracts."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import stat
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING or __package__:
    from . import bounded_io, managed_verify, safe_git
else:  # pragma: no cover - packaged direct-script boundary
    import bounded_io  # type: ignore[import-not-found,no-redef]
    import managed_verify  # type: ignore[import-not-found,no-redef]
    import safe_git  # type: ignore[import-not-found,no-redef]

WORKFLOWS = tuple(managed_verify.VERIFIER_PATHS)
SCAFFOLD_MARKER = b"WCLASS_SCAFFOLD_REJECT_ALL = True"


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        super().error("invalid arguments")


_CRITERIA = {
    "implementation": (
        "Run the project's focused tests and static checks against the candidate tree.",
        "Reject generated files, unrelated edits, and any failed required check.",
    ),
    "review": (
        "Require cited repository locations and concrete evidence for reportable findings.",
        "Reject unsupported critical/high claims and define how false positives are checked.",
    ),
    "research": (
        "Require traceable evidence for each supported claim and preserve unresolved claims.",
        "Reject conclusions that exceed the supplied repository evidence.",
    ),
    "diagnosis": (
        "Require a reproducible symptom and evidence that discriminates competing causes.",
        "Reject a confirmed cause when counterevidence remains unexplained.",
    ),
    "design": (
        "Require alternatives, affected surfaces, risks, and testable acceptance criteria.",
        "Reject recommendations whose validation plan cannot falsify the chosen design.",
    ),
}


def _template(workflow: str) -> bytes:
    criteria = "\n".join(f"# - {line}" for line in _CRITERIA[workflow])
    return (
        "#!/usr/bin/env python3\n"
        "# weightclass campaign verifier scaffold\n"
        f"# Workflow: {workflow}\n"
        "# Replace the reject-all branch only after implementing these project-specific gates:\n"
        f"{criteria}\n"
        "# Exit 0 accepts a candidate. Exit 42 must reject the package baseline probe.\n"
        "WCLASS_SCAFFOLD_REJECT_ALL = True\n"
        "raise SystemExit(42 if WCLASS_SCAFFOLD_REJECT_ALL else 1)\n"
    ).encode()


def _repo(path: Path) -> Path:
    try:
        selected = path.expanduser().resolve(strict=True)
        if not selected.is_dir():
            raise OSError
        return selected
    except (OSError, ValueError):
        raise ValueError() from None


def _verifier_relative(workflow: str) -> str:
    try:
        return managed_verify.VERIFIER_PATHS[workflow]
    except KeyError:
        raise ValueError() from None


def scaffold(repo: Path, workflow: str) -> dict[str, object]:
    root = _repo(repo)
    relative = _verifier_relative(workflow)
    directory_name, file_name = Path(relative).parts
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    if not nofollow or not directory_flag or not cloexec:
        raise ValueError()
    root_fd = os.open(root, os.O_RDONLY | directory_flag | nofollow | cloexec)
    directory_fd = -1
    file_fd = -1
    try:
        try:
            os.mkdir(directory_name, mode=0o755, dir_fd=root_fd)
        except FileExistsError:
            pass
        directory_fd = os.open(
            directory_name,
            os.O_RDONLY | directory_flag | nofollow | cloexec,
            dir_fd=root_fd,
        )
        file_fd = os.open(
            file_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow | cloexec,
            0o700,
            dir_fd=directory_fd,
        )
        payload = _template(workflow)
        view = memoryview(payload)
        while view:
            written = os.write(file_fd, view)
            if written <= 0:
                raise OSError
            view = view[written:]
        os.fsync(file_fd)
        os.fsync(directory_fd)
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        if directory_fd >= 0:
            os.close(directory_fd)
        os.close(root_fd)
    return {
        "schema_version": 1,
        "event": "campaign_verifier_scaffolded",
        "workflow": workflow,
        "path": relative,
        "ready": False,
        "rejects_all": True,
        "next_action": (
            "Implement the listed project criteria, commit the file, then run verifier check."
        ),
    }


def check(repo: Path, workflow: str) -> dict[str, object]:
    root = _repo(repo)
    relative = _verifier_relative(workflow)
    path = root / relative
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or not metadata.st_mode & stat.S_IXUSR
            or metadata.st_mode & 0o022
            or not 0 < metadata.st_size <= managed_verify.MAX_VERIFIER_BYTES
        ):
            raise ValueError()
        payload = bounded_io.read_regular_bytes(
            path,
            managed_verify.MAX_VERIFIER_BYTES,
            require_current_owner=True,
        )
    except (OSError, ValueError, bounded_io.BoundedFileError):
        raise ValueError() from None
    environment = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
    try:
        tracked = safe_git.run(
            ["ls-files", "--error-unmatch", "--", relative],
            cwd=root,
            environment=environment,
            timeout_seconds=30,
            max_stdout_bytes=4096,
            max_stderr_bytes=4096,
        )
        changed = safe_git.run(
            ["status", "--porcelain=v1", "--", relative],
            cwd=root,
            environment=environment,
            timeout_seconds=30,
            max_stdout_bytes=4096,
            max_stderr_bytes=4096,
        )
    except safe_git.SafeGitError:
        raise ValueError() from None
    committed = tracked.returncode == 0 and changed.returncode == 0 and not changed.stdout
    scaffold_only = SCAFFOLD_MARKER in payload
    baseline_rejected = False
    if committed and not scaffold_only:
        backend = importlib.import_module(
            f"{__package__}.managed_advisory" if __package__ else "managed_advisory"
        )
        try:
            backend.preflight_project_verifier(root, workflow, path)
            baseline_rejected = True
        except backend.ManagedPreflightError:
            pass
    ready = committed and not scaffold_only and baseline_rejected
    return {
        "schema_version": 1,
        "event": "campaign_verifier_check",
        "workflow": workflow,
        "path": relative,
        "committed": committed,
        "scaffold_only": scaffold_only,
        "baseline_rejected": baseline_rejected,
        "ready": ready,
        "next_action": (
            "Verifier is ready for campaign preflight."
            if ready
            else "Implement project criteria and commit the verifier, then check again."
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = _ArgumentParser(
        prog="wclass-advisory campaign verifier",
        description="Scaffold or inspect a project-owned campaign verifier.",
        allow_abbrev=False,
    )
    parser.add_argument("command", choices=("scaffold", "check"))
    parser.add_argument("--workflow", required=True, choices=WORKFLOWS)
    parser.add_argument("--repo", type=Path, default=Path("."))
    arguments = parser.parse_args(argv)
    try:
        receipt = (
            scaffold(arguments.repo, arguments.workflow)
            if arguments.command == "scaffold"
            else check(arguments.repo, arguments.workflow)
        )
    except (OSError, ValueError):
        print(json.dumps({"error": "campaign_verifier_rejected"}), file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0 if receipt["ready"] is True or arguments.command == "scaffold" else 1


if __name__ == "__main__":
    raise SystemExit(main())
