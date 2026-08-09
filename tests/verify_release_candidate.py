"""Verify and stage one immutable, manifest-bound release candidate."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

if __package__ in (None, ""):
    repository_root = str(Path(__file__).resolve().parents[1])
    if repository_root not in sys.path:
        sys.path.insert(0, repository_root)

from tests.verify_distribution_isolation import (
    MAX_DISTRIBUTION_ARTIFACT_BYTES,
    IsolationError,
    verify_distribution_directory,
)

MANIFEST_NAME = "SHA256SUMS"
MAX_RELEASE_ARTIFACT_BYTES = MAX_DISTRIBUTION_ARTIFACT_BYTES
MAX_RELEASE_MANIFEST_BYTES = 16 * 1024
_READ_CHUNK_BYTES = 64 * 1024
_LINE = re.compile(r"([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._+-]*)\n")


class ReleaseCandidateError(ValueError):
    pass


@dataclass(frozen=True)
class ReleaseCandidate:
    wheel_name: str
    sdist_name: str
    hashes: tuple[tuple[str, str], ...]


def _fail(message: str) -> NoReturn:
    raise ReleaseCandidateError(message)


def _stat_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _regular_bytes(path: Path, *, max_bytes: int = MAX_RELEASE_ARTIFACT_BYTES) -> bytes:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    nonblock = getattr(os, "O_NONBLOCK", 0)
    if nofollow == 0 or cloexec == 0 or nonblock == 0:
        _fail("release candidate safe file access is unavailable")
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | nofollow | cloexec | nonblock)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            _fail("release candidate entries must be nonsymlink regular files")
        if before.st_size > max_bytes:
            _fail("release candidate entry exceeds the size limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(_READ_CHUNK_BYTES, max_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                _fail("release candidate entry exceeds the size limit")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        path_after = path.lstat()
        if (
            total != before.st_size
            or _stat_identity(before) != _stat_identity(after)
            or _stat_identity(before) != _stat_identity(path_after)
        ):
            _fail("release candidate entry changed during the bounded read")
        return b"".join(chunks)
    except OSError:
        _fail("release candidate entry could not be read")
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                _fail("release candidate entry could not be closed")


def _snapshot(directory: Path) -> tuple[tuple[str, int, int, int, str], ...]:
    try:
        entries = sorted(directory.iterdir(), key=lambda path: path.name)
    except OSError:
        _fail("artifact-download could not be inventoried")
    result = []
    for path in entries:
        limit = (
            MAX_RELEASE_MANIFEST_BYTES if path.name == MANIFEST_NAME else MAX_RELEASE_ARTIFACT_BYTES
        )
        raw = _regular_bytes(path, max_bytes=limit)
        metadata = path.lstat()
        result.append(
            (
                path.name,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
                hashlib.sha256(raw).hexdigest(),
            )
        )
    return tuple(result)


def load_release_candidate(directory: Path) -> ReleaseCandidate:
    before = _snapshot(directory)
    if len(before) != 3 or {item[0] for item in before} == {MANIFEST_NAME}:
        _fail("artifact-download must contain exactly one wheel, one sdist, and SHA256SUMS")
    names = [item[0] for item in before]
    if names.count(MANIFEST_NAME) != 1:
        _fail("artifact-download must contain exactly one wheel, one sdist, and SHA256SUMS")
    raw = _regular_bytes(
        directory / MANIFEST_NAME,
        max_bytes=MAX_RELEASE_MANIFEST_BYTES,
    )
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        _fail("release manifest is not canonical ASCII")
    matches = list(_LINE.finditer(text))
    if not matches or "".join(match.group(0) for match in matches) != text:
        _fail("release manifest is not canonical")
    rows = tuple((match.group(2), match.group(1)) for match in matches)
    if rows != tuple(sorted(rows)) or len(rows) != 2 or len({name for name, _ in rows}) != 2:
        _fail("release manifest inventory is invalid")
    wheel = [name for name, _ in rows if name.endswith(".whl")]
    sdist = [name for name, _ in rows if name.endswith(".tar.gz")]
    if len(wheel) != 1 or len(sdist) != 1 or set(names) != {MANIFEST_NAME, wheel[0], sdist[0]}:
        _fail("artifact-download inventory does not match the manifest")
    for name, expected in rows:
        if hashlib.sha256(_regular_bytes(directory / name)).hexdigest() != expected:
            _fail("release candidate hash mismatch")
    if _snapshot(directory) != before:
        _fail("artifact-download changed during verification")
    return ReleaseCandidate(wheel[0], sdist[0], rows)


def write_manifest(build_output: Path, artifact_download: Path) -> ReleaseCandidate:
    if artifact_download.exists() and any(artifact_download.iterdir()):
        _fail("artifact-download must start empty")
    artifact_download.mkdir(parents=True, exist_ok=True)
    artifacts = sorted(build_output.iterdir(), key=lambda path: path.name)
    wheels = [path for path in artifacts if path.name.endswith(".whl")]
    sdists = [path for path in artifacts if path.name.endswith(".tar.gz")]
    if len(artifacts) != 2 or len(wheels) != 1 or len(sdists) != 1:
        _fail("build output must contain exactly one wheel and one sdist")
    rows = []
    for source in sorted((wheels[0], sdists[0]), key=lambda path: path.name):
        raw = _regular_bytes(source)
        destination = artifact_download / source.name
        destination.write_bytes(raw)
        rows.append((source.name, hashlib.sha256(raw).hexdigest()))
    (artifact_download / MANIFEST_NAME).write_text(
        "".join(f"{digest}  {name}\n" for name, digest in rows), encoding="ascii", newline="\n"
    )
    return load_release_candidate(artifact_download)


def create_staging(
    directory: Path, staging: Path, candidate: ReleaseCandidate
) -> tuple[Path, Path]:
    try:
        staging.mkdir(parents=True, exist_ok=False)
    except OSError:
        _fail("staging directory must not already exist")
    paths = []
    for name in (candidate.wheel_name, candidate.sdist_name):
        destination = staging / name
        try:
            with destination.open("xb") as stream:
                stream.write(_regular_bytes(directory / name))
        except OSError:
            _fail("staging entry could not be created")
        paths.append(destination)
    return paths[0], paths[1]


def verify_staging(staging: Path, candidate: ReleaseCandidate) -> tuple[Path, Path]:
    expected = (candidate.wheel_name, candidate.sdist_name)
    try:
        names = tuple(sorted(path.name for path in staging.iterdir()))
    except OSError:
        _fail("staging directory could not be inspected")
    if names != tuple(sorted(expected)):
        _fail("staging inventory does not match the manifest")
    paths = tuple(staging / name for name in expected)
    hashes = {name: digest for name, digest in candidate.hashes}
    for path in paths:
        if hashlib.sha256(_regular_bytes(path)).hexdigest() != hashes[path.name]:
            _fail("staging hash does not match the manifest")
    return paths[0], paths[1]


def verify_and_stage(
    directory: Path,
    staging: Path,
    *,
    source: Path,
    expected_version: str | None,
    run_sdist_tests: bool = True,
) -> tuple[Path, Path]:
    before = _snapshot(directory)
    candidate = load_release_candidate(directory)
    paths = create_staging(directory, staging, candidate)
    verify_staging(staging, candidate)
    try:
        verify_distribution_directory(
            source,
            staging,
            run_sdist_tests_requested=run_sdist_tests,
            expected_version=expected_version,
        )
    except IsolationError as error:
        _fail(str(error))
    verify_staging(staging, candidate)
    if _snapshot(directory) != before:
        _fail("artifact-download changed during validation")
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-download", type=Path, required=True)
    parser.add_argument("--create-manifest-from", type=Path)
    parser.add_argument("--create-staging", type=Path)
    parser.add_argument("--create-publish-staging", type=Path)
    parser.add_argument("--print-staging-paths", type=Path)
    parser.add_argument("--source", type=Path, default=Path("."))
    parser.add_argument("--expected-version")
    args = parser.parse_args()
    try:
        if args.create_manifest_from is not None:
            write_manifest(args.create_manifest_from, args.artifact_download)
        candidate = load_release_candidate(args.artifact_download)
        if args.create_staging is not None:
            verify_and_stage(
                args.artifact_download,
                args.create_staging,
                source=args.source,
                expected_version=args.expected_version,
            )
        if args.create_publish_staging is not None:
            paths = verify_and_stage(
                args.artifact_download,
                args.create_publish_staging,
                source=args.source,
                expected_version=args.expected_version,
                run_sdist_tests=False,
            )
            if load_release_candidate(args.artifact_download) != candidate:
                _fail("artifact-download changed during publish staging")
            for path in paths:
                print(path)
        if args.print_staging_paths is not None:
            for path in verify_staging(args.print_staging_paths, candidate):
                print(path)
    except ReleaseCandidateError as error:
        print(f"release candidate verification failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
