#!/usr/bin/env python3
"""Verify that the previous released Skill bundle is an admitted upgrade source."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

from weightclass.advisory import install_advisory_skill


def _fail() -> None:
    print("advisory Skill ledger verification failed", file=sys.stderr)
    raise SystemExit(1)


def _git_blob(repository: Path, revision: str, relative: str) -> bytes:
    try:
        result = subprocess.run(
            ("git", "-C", str(repository), "show", f"{revision}:{relative}"),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        _fail()
    if result.returncode != 0 or not result.stdout:
        _fail()
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--previous-ref", required=True)
    arguments = parser.parse_args()
    repository = arguments.repository.resolve()
    prefix = "src/weightclass/advisory/skill"
    previous_hashes = {
        relative: hashlib.sha256(
            _git_blob(repository, arguments.previous_ref, f"{prefix}/{relative}")
        ).hexdigest()
        for relative in install_advisory_skill.EXPECTED_FILES
    }
    if not any(
        files == install_advisory_skill.EXPECTED_FILES and hashes == previous_hashes
        for files, hashes in install_advisory_skill.historical_bundle_file_sha256()
    ):
        _fail()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
