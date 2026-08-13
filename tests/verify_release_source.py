#!/usr/bin/env python3
"""Fail closed unless a release tag commit is reachable from reviewed main."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
ALLOWED_MAIN_REFS = frozenset({"refs/heads/main", "refs/remotes/origin/main"})


class ReleaseSourceError(Exception):
    """Raised when repository history cannot authorize the release."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        raise ReleaseSourceError()


def _resolve_commit(repository: Path, revision: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "--verify", f"{revision}^{{commit}}"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ReleaseSourceError() from None
    resolved = result.stdout.strip()
    if result.returncode != 0 or COMMIT_PATTERN.fullmatch(resolved) is None:
        raise ReleaseSourceError()
    return resolved


def verify_release_source(repository: Path, tag_commit: str, main_ref: str) -> None:
    if COMMIT_PATTERN.fullmatch(tag_commit) is None or main_ref not in ALLOWED_MAIN_REFS:
        raise ReleaseSourceError()
    if _resolve_commit(repository, tag_commit) != tag_commit:
        raise ReleaseSourceError()
    main_commit = _resolve_commit(repository, main_ref)
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), "merge-base", "--is-ancestor", tag_commit, main_commit],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ReleaseSourceError() from None
    if result.returncode != 0:
        raise ReleaseSourceError()


def main(argv: list[str] | None = None) -> int:
    parser = _ArgumentParser(add_help=False)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--tag-commit", required=True)
    parser.add_argument("--main-ref", required=True)
    try:
        arguments = parser.parse_args(argv)
        verify_release_source(arguments.repository, arguments.tag_commit, arguments.main_ref)
    except ReleaseSourceError:
        print("release source verification failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
