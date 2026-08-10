"""Compare a manifest-bound candidate with a versioned reference snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import NoReturn

if __package__ in (None, ""):
    repository_root = str(Path(__file__).resolve().parents[1])
    if repository_root not in sys.path:
        sys.path.insert(0, repository_root)

from tests.verify_distribution_isolation import IsolationError, normalized_distribution
from tests.verify_release_candidate import ReleaseCandidateError, load_release_candidate

SNAPSHOT_NAME = "release-normalized-v1.json"
EXCLUSIONS = Path(__file__).with_name("release-normalization-exclusions-v1.json")


class ComparisonError(ValueError):
    pass


def _fail(message: str) -> NoReturn:
    raise ComparisonError(message)


def load_exclusions(path: Path = EXCLUSIONS) -> tuple[str, ...]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _fail("normalization exclusion allowlist is invalid")
    if (
        type(value) is not dict
        or set(value) != {"schema_version", "exclusions"}
        or value["schema_version"] != 1
    ):
        _fail("normalization exclusion allowlist is invalid")
    exclusions = value["exclusions"]
    if type(exclusions) is not list:
        _fail("normalization exclusion allowlist is invalid")
    result = []
    for item in exclusions:
        if type(item) is not dict or set(item) != {"path", "reason"}:
            _fail("every exclusion requires one literal path and documentation")
        path_value, reason = item["path"], item["reason"]
        if (
            type(path_value) is not str
            or type(reason) is not str
            or not reason.strip()
            or any(token in path_value for token in ("*", "?", "[", "]", "\\"))
            or PurePosixPath(path_value).is_absolute()
            or ".." in PurePosixPath(path_value).parts
            or path_value != PurePosixPath(path_value).as_posix()
        ):
            _fail("every exclusion requires one literal path and documentation")
        result.append(path_value)
    if len(result) != len(set(result)):
        _fail("normalization exclusions must be unique")
    return tuple(result)


def candidate_snapshot(directory: Path, exclusions_path: Path = EXCLUSIONS) -> dict[str, object]:
    candidate = load_release_candidate(directory)
    exclusions = load_exclusions(exclusions_path)
    artifacts = []
    effective: set[str] = set()
    for name in (candidate.wheel_name, candidate.sdist_name):
        normalized = normalized_distribution(directory / name)
        members = []
        for member in normalized.members:
            key = f"{normalized.archive_kind}:{member.path}"
            if key in exclusions:
                effective.add(key)
                continue
            members.append(asdict(member))
        artifacts.append(
            {
                "archive_kind": normalized.archive_kind,
                "archive_root": normalized.archive_root,
                "core_metadata": [list(item) for item in normalized.core_metadata],
                "members": members,
            }
        )
    if effective != set(exclusions):
        _fail("normalization exclusion is ineffective")
    if load_release_candidate(directory) != candidate:
        _fail("artifact-download changed during normalized comparison")
    return {"schema_version": 1, "artifacts": artifacts}


def compare_or_record(directory: Path, reference: Path, exclusions_path: Path = EXCLUSIONS) -> str:
    candidate = load_release_candidate(directory)
    current_hashes = {name: digest for name, digest in candidate.hashes}
    if reference.is_dir():
        other = load_release_candidate(reference)
        if current_hashes == {name: digest for name, digest in other.hashes}:
            return "exact"
        expected = candidate_snapshot(reference, exclusions_path)
    elif reference.exists():
        try:
            expected = json.loads(reference.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _fail("normalized reference is invalid")
    else:
        expected = candidate_snapshot(directory, exclusions_path)
        reference.write_text(
            json.dumps(
                expected,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="ascii",
        )
        return "recorded"
    if candidate_snapshot(directory, exclusions_path) != expected:
        _fail("normalized release candidate mismatch")
    return "normalized"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-download", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--exclusions", type=Path, default=EXCLUSIONS)
    args = parser.parse_args()
    reference = args.reference or args.artifact_download.parent / SNAPSHOT_NAME
    try:
        print(compare_or_record(args.artifact_download, reference, args.exclusions))
    except (ComparisonError, ReleaseCandidateError, IsolationError) as error:
        print(f"release candidate comparison failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
