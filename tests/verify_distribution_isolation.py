"""Test-owned distribution isolation gate; this module is never shipped in a wheel."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import unicodedata
import zipfile
import zlib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, NoReturn, Protocol, cast

WHEEL_REGISTRY_PATH = "weightclass/delegation_qualifications.json"
SDIST_REGISTRY_PATH = "src/weightclass/delegation_qualifications.json"
REQUIRED_WHEEL_ADVISORY_PATHS = (
    "weightclass/advisory/__init__.py",
    "weightclass/advisory/managed_advisory.py",
    "weightclass/advisory/managed_verify.py",
    "weightclass/advisory/wclass_advisory.py",
    "weightclass/advisory/skill/SKILL.md",
    "weightclass/advisory/skill/manifest.json",
)
REQUIRED_SDIST_ADVISORY_PATHS = tuple(f"src/{path}" for path in REQUIRED_WHEEL_ADVISORY_PATHS)
EMPTY_REGISTRY = {
    "records": [],
    "registry_schema_version": 1,
    "suite_revision": "delegation-conformance-v2",
}
MAX_WHEEL_MEMBER_BYTES = 256 * 1_024
MAX_ARCHIVE_TEXT_BYTES = MAX_WHEEL_MEMBER_BYTES
MAX_ARCHIVE_MEMBER_NAME_BYTES = 4_096
MAX_ARCHIVE_PATH_COMPONENTS = 256
MAX_ARCHIVE_MEMBERS = 4_096
MAX_SDIST_EXTENSION_BYTES = 256 * 1_024
MAX_SDIST_MEMBER_BYTES = 8 * 1_024 * 1_024
MAX_ARCHIVE_TOTAL_BYTES = 64 * 1_024 * 1_024
MAX_DISTRIBUTION_ARTIFACT_BYTES = 72 * 1_024 * 1_024
MAX_PHYSICAL_TAR_BYTES = MAX_ARCHIVE_TOTAL_BYTES + MAX_ARCHIVE_MEMBERS * 1_024 + tarfile.RECORDSIZE
MAX_PHYSICAL_ZIP_METADATA_BYTES = MAX_ARCHIVE_MEMBER_NAME_BYTES
MAX_CORE_METADATA_VALUE_BYTES = 256
ARCHIVE_MEMBER_NAME_LIMIT_ERROR = "archive member name exceeds the safety limit"
ARCHIVE_MEMBER_COUNT_LIMIT_ERROR = "archive member count exceeds the safety limit"
ARCHIVE_TOTAL_SIZE_LIMIT_ERROR = "archive total size exceeds the safety limit"
ARCHIVE_DIRECTORY_SIZE_ERROR = "archive directory has nonzero size"
WHEEL_MEMBER_SIZE_LIMIT_ERROR = "wheel member size exceeds the safety limit"
SDIST_MEMBER_SIZE_LIMIT_ERROR = "sdist member size exceeds the safety limit"
PHYSICAL_WHEEL_COUNT_LIMIT_ERROR = "physical wheel member count exceeds the safety limit"
CORE_METADATA_MISSING_ERROR = "distribution core metadata is missing or duplicated"
CORE_METADATA_MALFORMED_ERROR = "distribution core metadata is malformed"
CORE_METADATA_PROJECT_ERROR = "distribution core metadata has an invalid project name"
CORE_METADATA_VERSION_ERROR = "distribution core metadata has an invalid version"
CORE_METADATA_VERSION_MISMATCH_ERROR = "wheel and sdist core metadata versions differ"
CORE_METADATA_EXPECTED_VERSION_ERROR = (
    "distribution core metadata does not match the expected version"
)
EXPECTED_VERSION_ERROR = "expected distribution version is invalid"
DISTRIBUTION_IDENTITY_ERROR = "distribution artifact identity is invalid or inconsistent"
_CLASSIC_ZIP_EOCD_SIGNATURE = b"PK\x05\x06"
_CLASSIC_ZIP_CENTRAL_SIGNATURE = b"PK\x01\x02"
_CLASSIC_ZIP_LOCAL_SIGNATURE = b"PK\x03\x04"
_ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_ZIP64_EXTRA_FIELD_ID = 0x0001
_SUPPORTED_ZIP_FLAGS = 0x0800
_SUPPORTED_ZIP_METHODS = frozenset((zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED))
_SUPPORTED_PHYSICAL_MEMBER_TYPES = frozenset((tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE))
_CANONICAL_VERSION_PATTERN = re.compile(
    r"(?:(?:[1-9][0-9]*)!)?"
    r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))*"
    r"(?:(?:a|b|rc)(?:0|[1-9][0-9]*))?"
    r"(?:\.post(?:0|[1-9][0-9]*))?"
    r"(?:\.dev(?:0|[1-9][0-9]*))?"
    r"(?:\+[a-z0-9]+(?:\.[a-z0-9]+)*)?"
)
CANDIDATE_SCHEMA_KEYS = frozenset(
    {
        "record_schema_version",
        "artifact_sha256",
        "artifact_size_bytes",
        "runtime_build_id",
        "platform",
        "protocol_version",
        "suite_revision",
        "adapter_id",
        "vendor_family",
        "conformance_evidence_sha256",
        "result_matrix",
        "scenario_results",
    }
)
EVIDENCE_SCHEMA_KEYS = frozenset(
    {
        "evidence_schema_version",
        "artifact_sha256",
        "artifact_size_bytes",
        "suite_revision",
        "runtime_build_id",
        "platform",
        "protocol_version",
        "adapter_id",
        "vendor_family",
        "result_matrix",
        "scenario_results",
    }
)
FORBIDDEN_FUZZY_PATH_PARTS = (
    "synthetic_probe",
    "synthetic_descendant",
    "delegation_claim_map",
    "fake_conformance_driver",
)
FORBIDDEN_TOP_LEVEL_CONTENT = frozenset({"skills", "tools"})
FORBIDDEN_WHEEL_TEXT = ("wcp-selftest/v1", '"qualification_eligible"')
REQUIRED_SDIST_TEST_SUFFIXES = (
    "tests/synthetic_descendant_containment.py",
    "tests/synthetic_probe_child.py",
    "tests/synthetic_probe_protocol.py",
    "tests/synthetic_probe_runner.py",
    "tests/test_distribution_isolation.py",
    "tests/test_synthetic_probe_protocol.py",
    "tests/verify_distribution_isolation.py",
)


class IsolationError(ValueError):
    """A distribution crossed the qualification-isolation boundary."""


class _DuplicateKeyError(ValueError):
    pass


class _ReadableBytes(Protocol):
    def read(self, size: int = -1) -> bytes: ...


class _ZipSnapshotReader:
    """Expose the private snapshot contract expected by Python 3.10 zipfile."""

    def __init__(self, snapshot: BinaryIO) -> None:
        self._snapshot = snapshot

    def read(self, size: int = -1) -> bytes:
        return self._snapshot.read(size)

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        return self._snapshot.seek(offset, whence)

    def tell(self) -> int:
        return self._snapshot.tell()

    def seekable(self) -> bool:
        return True


class _ArchivePathNode:
    __slots__ = ("children", "is_file", "requires_directory", "spelling")

    def __init__(self, spelling: str) -> None:
        self.spelling = spelling
        self.children: dict[str, _ArchivePathNode] = {}
        self.is_file = False
        self.requires_directory = False


@dataclass(frozen=True)
class _ArtifactFingerprint:
    name: str
    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int
    changed_ns: int
    sha256: str


@dataclass(frozen=True)
class _DistributionSnapshot:
    wheel: Path
    sdist: Path
    fingerprints: tuple[_ArtifactFingerprint, ...]


@dataclass(frozen=True, order=True)
class NormalizedArchiveMember:
    """Security-preflighted archive member identity for reproducibility checks."""

    path: str
    kind: str
    mode: int
    size: int
    sha256: str


@dataclass(frozen=True)
class NormalizedDistribution:
    archive_kind: str
    archive_root: str
    core_metadata: tuple[tuple[str, str], ...]
    members: tuple[NormalizedArchiveMember, ...]


def _fail(message: str) -> NoReturn:
    raise IsolationError(message)


def _reject_forbidden_top_level_content(path: PurePosixPath, location: str) -> None:
    if path.parts and path.parts[0].casefold() in FORBIDDEN_TOP_LEVEL_CONTENT:
        _fail(f"{location}: forbidden top-level content: {path.as_posix()}")


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKeyError
        value[key] = item
    return value


def _load_empty_registry(raw: bytes, location: str) -> None:
    try:
        value: Any = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_object_without_duplicate_keys
        )
    except (RecursionError, ValueError):
        _fail(f"{location}: registry is not canonical JSON")
    if type(value) is not dict:
        _fail(f"{location}: registry shape is not the empty production shape")
    try:
        keys = frozenset(value)
        records = value["records"]
        registry_schema_version = value["registry_schema_version"]
        suite_revision = value["suite_revision"]
    except (KeyError, TypeError, ValueError):
        _fail(f"{location}: registry shape is not the empty production shape")
    if (
        keys != frozenset(EMPTY_REGISTRY)
        or type(records) is not list
        or records != []
        or type(registry_schema_version) is not int
        or registry_schema_version != 1
        or type(suite_revision) is not str
        or suite_revision != "delegation-conformance-v2"
    ):
        _fail(f"{location}: registry shape is not the empty production shape")


def _read_archive_member(archive: zipfile.ZipFile, member: zipfile.ZipInfo, location: str) -> bytes:
    if member.file_size < 0 or member.file_size > MAX_ARCHIVE_TEXT_BYTES:
        _fail(f"{location}: archive member exceeds the scan limit")
    try:
        with archive.open(member) as stream:
            raw = bytes(stream.read(MAX_ARCHIVE_TEXT_BYTES + 1))
    except (
        OSError,
        EOFError,
        RuntimeError,
        TypeError,
        ValueError,
        KeyError,
        zipfile.BadZipFile,
    ):
        _fail(f"{location}: archive member could not be inspected")
    if len(raw) > MAX_ARCHIVE_TEXT_BYTES:
        _fail(f"{location}: archive member exceeds the scan limit")
    return raw


def _validate_wheel_member_metadata(members: list[zipfile.ZipInfo]) -> None:
    if len(members) > MAX_ARCHIVE_MEMBERS:
        _fail(ARCHIVE_MEMBER_COUNT_LIMIT_ERROR)
    total_size = 0
    for member in members:
        size = member.file_size
        if size < 0:
            _fail(WHEEL_MEMBER_SIZE_LIMIT_ERROR)
        if member.is_dir():
            if size != 0:
                _fail(ARCHIVE_DIRECTORY_SIZE_ERROR)
        elif size > MAX_WHEEL_MEMBER_BYTES:
            _fail(WHEEL_MEMBER_SIZE_LIMIT_ERROR)
        total_size += size
        if total_size > MAX_ARCHIVE_TOTAL_BYTES:
            _fail(ARCHIVE_TOTAL_SIZE_LIMIT_ERROR)


def _validate_outer_artifact_metadata(metadata: os.stat_result) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        _fail("distribution artifacts must be nonsymlink regular files")
    if metadata.st_size < 0 or metadata.st_size > MAX_DISTRIBUTION_ARTIFACT_BYTES:
        _fail("distribution artifact exceeds the outer size safety limit")


def _validate_sdist_member_metadata(member: tarfile.TarInfo, total_size: int) -> int:
    size = member.size
    if size < 0:
        _fail(SDIST_MEMBER_SIZE_LIMIT_ERROR)
    if member.isdir():
        if size != 0:
            _fail(ARCHIVE_DIRECTORY_SIZE_ERROR)
    elif member.isfile() and size > MAX_SDIST_MEMBER_BYTES:
        _fail(SDIST_MEMBER_SIZE_LIMIT_ERROR)
    total_size += size
    if total_size > MAX_ARCHIVE_TOTAL_BYTES:
        _fail(ARCHIVE_TOTAL_SIZE_LIMIT_ERROR)
    return total_size


def _read_exact_physical(stream: _ReadableBytes, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            _fail("physical sdist is truncated")
        chunks.extend(chunk)
    return bytes(chunks)


def _copy_exact_physical(
    stream: _ReadableBytes,
    destination: BinaryIO,
    size: int,
    *,
    require_zero: bool = False,
) -> None:
    remaining = size
    while remaining:
        chunk = stream.read(min(remaining, 65_536))
        if not chunk:
            _fail("physical sdist is truncated")
        if require_zero and any(chunk):
            _fail("physical sdist padding is nonzero")
        destination.write(chunk)
        remaining -= len(chunk)


def _open_no_follow(path: str, flags: int) -> int:
    return os.open(
        path,
        flags | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )


def _artifact_fingerprint(
    path: Path,
    metadata: os.stat_result,
    digest: str,
) -> _ArtifactFingerprint:
    return _ArtifactFingerprint(
        name=path.name,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode,
        size=metadata.st_size,
        modified_ns=metadata.st_mtime_ns,
        changed_ns=metadata.st_ctime_ns,
        sha256=digest,
    )


@contextmanager
def _verified_artifact_snapshot(
    path: Path,
    expected_fingerprint: _ArtifactFingerprint | None = None,
) -> Iterator[tuple[BinaryIO, _ArtifactFingerprint]]:
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = _open_no_follow(str(path), flags)
    except OSError:
        _fail("distribution artifact could not be opened safely")

    try:
        snapshot = tempfile.SpooledTemporaryFile(
            max_size=MAX_SDIST_EXTENSION_BYTES,
            mode="w+b",
        )
    except OSError:
        os.close(descriptor)
        _fail("distribution artifact could not be snapshotted")
    snapshot_io = cast(BinaryIO, snapshot)
    try:
        try:
            before = os.fstat(descriptor)
            _validate_outer_artifact_metadata(before)
            digest = hashlib.sha256()
            copied = 0
            while chunk := os.read(descriptor, 1_048_576):
                copied += len(chunk)
                if copied > MAX_DISTRIBUTION_ARTIFACT_BYTES:
                    _fail("distribution artifact exceeds the outer size safety limit")
                snapshot_io.write(chunk)
                digest.update(chunk)
            after = os.fstat(descriptor)
        except IsolationError:
            raise
        except OSError:
            _fail("distribution artifact could not be snapshotted")
        finally:
            os.close(descriptor)

        try:
            current = path.stat(follow_symlinks=False)
        except OSError:
            _fail("distribution artifact changed while it was snapshotted")
        if (
            copied != after.st_size
            or _stat_identity(before) != _stat_identity(after)
            or _stat_identity(after) != _stat_identity(current)
        ):
            _fail("distribution artifact changed while it was snapshotted")
        fingerprint = _artifact_fingerprint(path, after, digest.hexdigest())
        if expected_fingerprint is not None and fingerprint != expected_fingerprint:
            _fail("distribution artifact does not match the reviewed fingerprint")
        snapshot_io.seek(0)
        yield snapshot_io, fingerprint
    finally:
        snapshot_io.close()


def _read_exact_zip_at(stream: BinaryIO, offset: int, size: int) -> bytes:
    try:
        stream.seek(offset)
        raw = stream.read(size)
    except (OSError, OverflowError, ValueError):
        _fail("physical wheel could not be inspected")
    if len(raw) != size:
        _fail("physical wheel is truncated")
    return raw


def _validate_zip_extra(extra: bytes) -> None:
    position = 0
    while position < len(extra):
        if len(extra) - position < 4:
            _fail("physical wheel extra metadata is malformed")
        field_id, field_size = struct.unpack_from("<HH", extra, position)
        position += 4
        if field_size > len(extra) - position:
            _fail("physical wheel extra metadata is malformed")
        if field_id == _ZIP64_EXTRA_FIELD_ID:
            _fail("physical wheel ZIP64 metadata is unsupported")
        position += field_size


def _physical_zip_eocd(stream: BinaryIO) -> tuple[int, int, int, int]:
    try:
        stream.seek(0, os.SEEK_END)
        artifact_size = stream.tell()
    except (OSError, OverflowError, ValueError):
        _fail("physical wheel could not be inspected")
    minimum_eocd_size = 22
    if artifact_size < minimum_eocd_size:
        _fail("physical wheel has no classic end record")
    tail_size = min(artifact_size, minimum_eocd_size + 65_535)
    tail_offset = artifact_size - tail_size
    tail = _read_exact_zip_at(stream, tail_offset, tail_size)
    candidates: list[int] = []
    position = 0
    while True:
        position = tail.find(_CLASSIC_ZIP_EOCD_SIGNATURE, position)
        if position < 0:
            break
        if position + minimum_eocd_size <= len(tail):
            comment_size = struct.unpack_from("<H", tail, position + 20)[0]
            absolute = tail_offset + position
            if absolute + minimum_eocd_size + comment_size == artifact_size:
                candidates.append(absolute)
        position += 1
    if len(candidates) != 1:
        _fail("physical wheel has no unique classic end record")
    eocd_offset = candidates[0]
    eocd = _read_exact_zip_at(stream, eocd_offset, minimum_eocd_size)
    (
        signature,
        disk_number,
        central_disk,
        entries_on_disk,
        entry_count,
        central_size,
        central_offset,
        comment_size,
    ) = struct.unpack("<4s4H2IH", eocd)
    if signature != _CLASSIC_ZIP_EOCD_SIGNATURE:
        _fail("physical wheel end record is invalid")
    if comment_size > MAX_PHYSICAL_ZIP_METADATA_BYTES:
        _fail("physical wheel comment exceeds the safety limit")
    if disk_number != 0 or central_disk != 0 or entries_on_disk != entry_count:
        _fail("physical wheel multidisk metadata is unsupported")
    if entry_count == 0xFFFF or central_size == 0xFFFFFFFF or central_offset == 0xFFFFFFFF:
        _fail("physical wheel ZIP64 metadata is unsupported")
    if (
        eocd_offset >= 20
        and _read_exact_zip_at(stream, eocd_offset - 20, 4) == _ZIP64_LOCATOR_SIGNATURE
    ):
        _fail("physical wheel ZIP64 metadata is unsupported")
    if central_offset + central_size != eocd_offset:
        _fail("physical wheel central directory layout is invalid")
    return eocd_offset, central_offset, central_size, entry_count


def _validate_physical_zip_name(name: bytes, flags: int) -> None:
    if not name or len(name) > MAX_ARCHIVE_MEMBER_NAME_BYTES:
        _fail(ARCHIVE_MEMBER_NAME_LIMIT_ERROR)
    try:
        decoded = name.decode("utf-8" if flags & _SUPPORTED_ZIP_FLAGS else "cp437")
    except UnicodeDecodeError:
        _fail("physical wheel member name encoding is invalid")
    if "\x00" in decoded:
        _fail("physical wheel member name is invalid")


def _validate_physical_zip_sizes(name: bytes, compressed: int, uncompressed: int) -> None:
    if name.endswith(b"/"):
        if compressed != 0 or uncompressed != 0:
            _fail(ARCHIVE_DIRECTORY_SIZE_ERROR)
    elif uncompressed > MAX_WHEEL_MEMBER_BYTES:
        _fail(WHEEL_MEMBER_SIZE_LIMIT_ERROR)
    if compressed > MAX_DISTRIBUTION_ARTIFACT_BYTES:
        _fail("physical wheel compressed member exceeds the safety limit")


def _physical_zip_payload_chunks(
    stream: BinaryIO,
    offset: int,
    size: int,
) -> Iterator[bytes]:
    try:
        stream.seek(offset)
    except (OSError, OverflowError, ValueError):
        _fail("physical wheel payload could not be inspected")
    remaining = size
    while remaining:
        try:
            chunk = stream.read(min(remaining, 65_536))
        except (OSError, OverflowError, ValueError):
            _fail("physical wheel payload could not be inspected")
        if not chunk or len(chunk) > remaining:
            _fail("physical wheel payload is truncated")
        remaining -= len(chunk)
        yield chunk


def _validate_stored_zip_payload(
    stream: BinaryIO,
    offset: int,
    compressed_size: int,
    uncompressed_size: int,
    expected_crc32: int,
) -> None:
    actual_size = 0
    actual_crc32 = 0
    for chunk in _physical_zip_payload_chunks(stream, offset, compressed_size):
        actual_size += len(chunk)
        actual_crc32 = zlib.crc32(chunk, actual_crc32)
    if actual_size != uncompressed_size or actual_crc32 != expected_crc32:
        _fail("physical wheel payload size or checksum is invalid")


def _validate_deflated_zip_payload(
    stream: BinaryIO,
    offset: int,
    compressed_size: int,
    uncompressed_size: int,
    expected_crc32: int,
) -> None:
    decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
    supplied = 0
    produced = 0
    actual_crc32 = 0
    try:
        for chunk in _physical_zip_payload_chunks(stream, offset, compressed_size):
            supplied += len(chunk)
            if decompressor.eof:
                _fail("physical wheel payload has trailing compressed data")
            output = decompressor.decompress(chunk, uncompressed_size - produced + 1)
            produced += len(output)
            actual_crc32 = zlib.crc32(output, actual_crc32)
            if produced > uncompressed_size:
                _fail("physical wheel payload exceeds its declared size")
            if decompressor.unused_data or decompressor.unconsumed_tail:
                _fail("physical wheel payload has trailing compressed data")
            if decompressor.eof and supplied != compressed_size:
                _fail("physical wheel payload has trailing compressed data")
        flushed = decompressor.flush(uncompressed_size - produced + 1)
    except zlib.error:
        _fail("physical wheel payload deflate stream is invalid")
    produced += len(flushed)
    actual_crc32 = zlib.crc32(flushed, actual_crc32)
    if (
        supplied != compressed_size
        or not decompressor.eof
        or decompressor.unused_data
        or decompressor.unconsumed_tail
    ):
        _fail("physical wheel payload deflate stream is incomplete")
    if produced != uncompressed_size or actual_crc32 != expected_crc32:
        _fail("physical wheel payload size or checksum is invalid")


def _validate_physical_zip_payload(
    stream: BinaryIO,
    offset: int,
    compressed_size: int,
    uncompressed_size: int,
    expected_crc32: int,
    method: int,
) -> None:
    if method == zipfile.ZIP_STORED:
        _validate_stored_zip_payload(
            stream,
            offset,
            compressed_size,
            uncompressed_size,
            expected_crc32,
        )
    else:
        _validate_deflated_zip_payload(
            stream,
            offset,
            compressed_size,
            uncompressed_size,
            expected_crc32,
        )


def _verify_physical_wheel(stream: BinaryIO) -> None:
    eocd_offset, central_offset, _central_size, declared_count = _physical_zip_eocd(stream)
    central_position = central_offset
    expected_local_offset = 0
    physical_count = 0
    aggregate_size = 0
    while central_position < eocd_offset:
        if physical_count >= MAX_ARCHIVE_MEMBERS:
            _fail(PHYSICAL_WHEEL_COUNT_LIMIT_ERROR)
        central = _read_exact_zip_at(stream, central_position, 46)
        (
            signature,
            _version_made_by,
            version_needed,
            flags,
            method,
            modified_time,
            modified_date,
            crc32,
            compressed_size,
            uncompressed_size,
            name_size,
            extra_size,
            comment_size,
            starting_disk,
            _internal_attributes,
            _external_attributes,
            local_offset,
        ) = struct.unpack("<4s6H3I5H2I", central)
        if signature != _CLASSIC_ZIP_CENTRAL_SIGNATURE:
            _fail("physical wheel central directory is invalid")
        physical_count += 1
        if name_size > MAX_ARCHIVE_MEMBER_NAME_BYTES:
            _fail(ARCHIVE_MEMBER_NAME_LIMIT_ERROR)
        if (
            extra_size > MAX_PHYSICAL_ZIP_METADATA_BYTES
            or comment_size > MAX_PHYSICAL_ZIP_METADATA_BYTES
        ):
            _fail("physical wheel member metadata exceeds the safety limit")
        central_body_size = name_size + extra_size + comment_size
        next_central_position = central_position + 46 + central_body_size
        if next_central_position > eocd_offset:
            _fail("physical wheel central directory is truncated")
        central_body = _read_exact_zip_at(stream, central_position + 46, central_body_size)
        name = central_body[:name_size]
        extra = central_body[name_size : name_size + extra_size]
        _validate_physical_zip_name(name, flags)
        _validate_zip_extra(extra)
        if version_needed > 20:
            _fail("physical wheel version is unsupported")
        if flags & ~_SUPPORTED_ZIP_FLAGS:
            _fail("physical wheel flags are unsupported")
        if method not in _SUPPORTED_ZIP_METHODS:
            _fail("physical wheel compression method is unsupported")
        if starting_disk != 0:
            _fail("physical wheel multidisk metadata is unsupported")
        if (
            compressed_size == 0xFFFFFFFF
            or uncompressed_size == 0xFFFFFFFF
            or local_offset == 0xFFFFFFFF
        ):
            _fail("physical wheel ZIP64 metadata is unsupported")
        if method == zipfile.ZIP_STORED and compressed_size != uncompressed_size:
            _fail("physical wheel stored member sizes are inconsistent")
        _validate_physical_zip_sizes(name, compressed_size, uncompressed_size)
        aggregate_size += uncompressed_size
        if aggregate_size > MAX_ARCHIVE_TOTAL_BYTES:
            _fail(ARCHIVE_TOTAL_SIZE_LIMIT_ERROR)
        if local_offset != expected_local_offset:
            _fail("physical wheel local member intervals are not contiguous")

        local = _read_exact_zip_at(stream, local_offset, 30)
        (
            local_signature,
            local_version_needed,
            local_flags,
            local_method,
            local_modified_time,
            local_modified_date,
            local_crc32,
            local_compressed_size,
            local_uncompressed_size,
            local_name_size,
            local_extra_size,
        ) = struct.unpack("<4s5H3I2H", local)
        if local_signature != _CLASSIC_ZIP_LOCAL_SIGNATURE:
            _fail("physical wheel local header is invalid")
        if local_name_size > MAX_ARCHIVE_MEMBER_NAME_BYTES:
            _fail(ARCHIVE_MEMBER_NAME_LIMIT_ERROR)
        if local_extra_size > MAX_PHYSICAL_ZIP_METADATA_BYTES:
            _fail("physical wheel member metadata exceeds the safety limit")
        local_body_size = local_name_size + local_extra_size
        local_body = _read_exact_zip_at(stream, local_offset + 30, local_body_size)
        local_name = local_body[:local_name_size]
        local_extra = local_body[local_name_size:]
        _validate_zip_extra(local_extra)
        if (
            local_version_needed != version_needed
            or local_flags != flags
            or local_method != method
            or local_modified_time != modified_time
            or local_modified_date != modified_date
            or local_crc32 != crc32
            or local_compressed_size != compressed_size
            or local_uncompressed_size != uncompressed_size
            or local_name != name
            or local_extra != extra
        ):
            _fail("physical wheel central and local metadata differ")
        payload_offset = local_offset + 30 + local_body_size
        expected_local_offset = payload_offset + compressed_size
        if expected_local_offset > central_offset:
            _fail("physical wheel local member intervals overlap")
        _validate_physical_zip_payload(
            stream,
            payload_offset,
            compressed_size,
            uncompressed_size,
            crc32,
            method,
        )
        central_position = next_central_position

    if physical_count != declared_count:
        _fail("physical wheel member count is inconsistent")
    if expected_local_offset != central_offset:
        _fail("physical wheel local member intervals are not contiguous")


def _canonical_tar_size(header: bytes) -> int:
    field = header[124:136]
    if field[-1:] != b"\0" or any(byte < ord("0") or byte > ord("7") for byte in field[:-1]):
        _fail("physical sdist size field is noncanonical")
    return int(field[:-1], 8)


def _validate_canonical_tar_checksum(header: bytes) -> None:
    field = header[148:156]
    if field[6:] != b"\0 " or any(byte < ord("0") or byte > ord("7") for byte in field[:6]):
        _fail("physical sdist checksum field is noncanonical")
    expected = int(field[:6], 8)
    actual = sum(header[:148]) + (ord(" ") * 8) + sum(header[156:])
    if actual != expected:
        _fail("physical sdist checksum is invalid")


def _validate_pax_payload(payload: bytes) -> None:
    position = 0
    keys: set[bytes] = set()
    while position < len(payload):
        separator = payload.find(b" ", position)
        if separator <= position:
            _fail("physical sdist PAX framing is invalid")
        length_field = payload[position:separator]
        if (
            len(length_field) > 6
            or length_field.startswith(b"0")
            or not all(ord("0") <= byte <= ord("9") for byte in length_field)
        ):
            _fail("physical sdist PAX framing is invalid")
        length = int(length_field)
        end = position + length
        if length < 5 or end > len(payload) or payload[end - 1 : end] != b"\n":
            _fail("physical sdist PAX framing is invalid")
        key_value = payload[separator + 1 : end - 1]
        key, equals, _value = key_value.partition(b"=")
        if not key or equals != b"=" or key in keys:
            _fail("physical sdist PAX framing is invalid")
        keys.add(key)
        if key == b"size" or key.startswith(b"GNU.sparse."):
            _fail("physical sdist offset-changing extension is forbidden")
        position = end


def _copy_verified_physical_sdist(stream: _ReadableBytes, snapshot: BinaryIO) -> None:
    zero_block = b"\0" * tarfile.BLOCKSIZE
    physical_members = 0
    aggregate_size = 0
    physical_size = 0
    zero_blocks = 0
    pending_local_pax = False
    while zero_blocks < 2:
        header = _read_exact_physical(stream, tarfile.BLOCKSIZE)
        snapshot.write(header)
        physical_size += tarfile.BLOCKSIZE
        if physical_size > MAX_PHYSICAL_TAR_BYTES:
            _fail("physical sdist exceeds the safety limit")
        if header == zero_block:
            zero_blocks += 1
            continue
        if zero_blocks:
            _fail("physical sdist has an invalid end marker")

        physical_members += 1
        if physical_members > MAX_ARCHIVE_MEMBERS:
            _fail("physical sdist member count exceeds the safety limit")
        size = _canonical_tar_size(header)
        _validate_canonical_tar_checksum(header)
        try:
            member = tarfile.TarInfo.frombuf(header, "utf-8", "surrogateescape")
        except (tarfile.HeaderError, ValueError):
            _fail("physical sdist header is invalid")
        if member.size != size:
            _fail("physical sdist size field is inconsistent")

        if member.type == tarfile.XHDTYPE:
            if pending_local_pax:
                _fail("physical sdist local PAX sequence is invalid")
            pending_local_pax = True
            member_size_limit = MAX_SDIST_EXTENSION_BYTES
        elif member.type in _SUPPORTED_PHYSICAL_MEMBER_TYPES:
            pending_local_pax = False
            member_size_limit = MAX_SDIST_MEMBER_BYTES
        else:
            _fail("physical sdist member type is unsupported")

        if size > member_size_limit:
            _fail("physical sdist member size exceeds the safety limit")
        if member.isdir() and size != 0:
            _fail("physical sdist directory has nonzero size")
        aggregate_size += size
        if aggregate_size > MAX_ARCHIVE_TOTAL_BYTES:
            _fail("physical sdist aggregate size exceeds the safety limit")

        padded_size = ((size + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE) * (tarfile.BLOCKSIZE)
        physical_size += padded_size
        if physical_size > MAX_PHYSICAL_TAR_BYTES:
            _fail("physical sdist exceeds the safety limit")
        if member.type == tarfile.XHDTYPE:
            payload = _read_exact_physical(stream, size)
            snapshot.write(payload)
            _validate_pax_payload(payload)
        else:
            _copy_exact_physical(stream, snapshot, size)
        _copy_exact_physical(
            stream,
            snapshot,
            padded_size - size,
            require_zero=True,
        )

    if pending_local_pax:
        _fail("physical sdist local PAX sequence is invalid")
    while trailing := stream.read(65_536):
        snapshot.write(trailing)
        physical_size += len(trailing)
        if physical_size > MAX_PHYSICAL_TAR_BYTES or any(trailing):
            _fail("physical sdist has invalid trailing data")
    if physical_size % tarfile.BLOCKSIZE:
        _fail("physical sdist has a partial trailing block")


@contextmanager
def _verified_sdist_snapshot(
    sdist: Path,
    expected_fingerprint: _ArtifactFingerprint | None = None,
) -> Iterator[BinaryIO]:
    try:
        with (
            _verified_artifact_snapshot(sdist, expected_fingerprint) as artifact,
            tempfile.SpooledTemporaryFile(
                max_size=MAX_SDIST_EXTENSION_BYTES,
                mode="w+b",
            ) as snapshot,
        ):
            compressed, _fingerprint = artifact
            snapshot_io = cast(BinaryIO, snapshot)
            compressed.seek(0)
            with gzip.GzipFile(fileobj=compressed, mode="rb") as stream:
                _copy_verified_physical_sdist(stream, snapshot_io)
            snapshot_io.seek(0)
            yield snapshot_io
    except IsolationError:
        raise
    except (EOFError, OSError, ValueError, zlib.error):
        _fail("physical sdist could not be inspected")


@contextmanager
def _open_verified_sdist(
    sdist: Path,
    expected_fingerprint: _ArtifactFingerprint | None = None,
) -> Iterator[tarfile.TarFile]:
    with _verified_sdist_snapshot(sdist, expected_fingerprint) as snapshot:
        try:
            with tarfile.open(fileobj=snapshot, mode="r:") as archive:
                yield archive
        except IsolationError:
            raise
        except (EOFError, OSError, OverflowError, ValueError, tarfile.TarError):
            _fail("physical sdist could not be parsed")


def _verify_physical_sdist(sdist: Path) -> None:
    with _verified_sdist_snapshot(sdist):
        pass


def _qualification_document_kind(raw: bytes, location: str) -> str | None:
    if not raw.lstrip().startswith(b"{"):
        return None
    try:
        value: Any = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_object_without_duplicate_keys
        )
    except _DuplicateKeyError:
        _fail(f"{location}: duplicate JSON object field")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    except (RecursionError, ValueError):
        _fail(f"{location}: JSON document exceeds the safety limit")
    if not isinstance(value, Mapping):
        return None
    keys = frozenset(value)
    if keys == CANDIDATE_SCHEMA_KEYS and value.get("record_schema_version") == 1:
        return "candidate"
    if keys == EVIDENCE_SCHEMA_KEYS and value.get("evidence_schema_version") == 2:
        return "evidence"
    return None


def _read_source_registry(registry: Path) -> bytes:
    def read_bounded(descriptor: int) -> bytes:
        raw = bytearray()
        while chunk := os.read(
            descriptor,
            min(65_536, MAX_ARCHIVE_TEXT_BYTES + 1 - len(raw)),
        ):
            raw.extend(chunk)
            if len(raw) > MAX_ARCHIVE_TEXT_BYTES:
                _fail("source registry exceeds the safety limit")
        return bytes(raw)

    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = _open_no_follow(str(registry), flags)
    except OSError:
        _fail("source registry could not be opened safely")
    try:
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                _fail("source registry must be a nonsymlink regular file")
            if before.st_size < 0 or before.st_size > MAX_ARCHIVE_TEXT_BYTES:
                _fail("source registry exceeds the safety limit")
            raw = read_bounded(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            confirmed_raw = read_bounded(descriptor)
            after = os.fstat(descriptor)
        except IsolationError:
            raise
        except OSError:
            _fail("source registry could not be inspected")
    finally:
        os.close(descriptor)
    try:
        current = registry.stat(follow_symlinks=False)
    except OSError:
        _fail("source registry changed while it was inspected")
    if (
        len(raw) != after.st_size
        or raw != confirmed_raw
        or _stat_identity(before) != _stat_identity(after)
        or _stat_identity(after) != _stat_identity(current)
    ):
        _fail("source registry changed while it was inspected")
    return confirmed_raw


def verify_source_registry(root: Path) -> None:
    registry = root / "src" / "weightclass" / "delegation_qualifications.json"
    _load_empty_registry(_read_source_registry(registry), "source registry")


def _is_tests_path(path: PurePosixPath) -> bool:
    return any(part.casefold() == "tests" for part in path.parts)


def _has_forbidden_fuzzy_path(path: PurePosixPath) -> bool:
    lowered = path.as_posix().casefold()
    return any(part in lowered for part in FORBIDDEN_FUZZY_PATH_PARTS)


def _has_bounded_archive_name(name: str) -> bool:
    try:
        name_bytes = len(name.encode("utf-8"))
    except UnicodeEncodeError:
        return False
    return name_bytes <= MAX_ARCHIVE_MEMBER_NAME_BYTES


def _has_bounded_archive_path(path: PurePosixPath) -> bool:
    return len(path.parts) <= MAX_ARCHIVE_PATH_COMPONENTS


def _register_archive_path(
    root: _ArchivePathNode,
    path: PurePosixPath,
    *,
    is_directory: bool,
    error_message: str,
) -> None:
    node = root
    for index, spelling in enumerate(path.parts):
        identity = unicodedata.normalize("NFC", spelling).casefold()
        child = node.children.get(identity)
        if child is None:
            child = _ArchivePathNode(spelling)
            node.children[identity] = child
        elif child.spelling != spelling:
            _fail(error_message)

        is_final = index == len(path.parts) - 1
        if not is_final:
            if child.is_file:
                _fail(error_message)
            child.requires_directory = True
        elif is_directory:
            if child.is_file:
                _fail(error_message)
            child.requires_directory = True
        else:
            if child.requires_directory or child.children:
                _fail(error_message)
            child.is_file = True
        node = child


def _wheel_members(archive: zipfile.ZipFile, wheel: Path) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    _validate_wheel_member_metadata(members)
    names: set[str] = set()
    casefolded_names: set[str] = set()
    install_path_names: set[str] = set()
    path_root = _ArchivePathNode("")
    for member in members:
        name = member.filename
        if not _has_bounded_archive_name(name):
            _fail(ARCHIVE_MEMBER_NAME_LIMIT_ERROR)
        path = PurePosixPath(name)
        casefolded_name = unicodedata.normalize("NFC", name).casefold()
        install_path_name = unicodedata.normalize("NFC", path.as_posix()).casefold()
        error_message = f"{wheel.name}: noncanonical or duplicate archive member: {name}"
        if (
            name in names
            or casefolded_name in casefolded_names
            or install_path_name in install_path_names
            or not path.parts
            or not _has_bounded_archive_path(path)
            or path.is_absolute()
            or ".." in path.parts
            or "." in path.parts
            or "\\" in name
            or "\x00" in name
            or name != (f"{path.as_posix()}/" if member.is_dir() else path.as_posix())
        ):
            _fail(error_message)
        _register_archive_path(
            path_root,
            path,
            is_directory=member.is_dir(),
            error_message=error_message,
        )
        names.add(name)
        casefolded_names.add(casefolded_name)
        install_path_names.add(install_path_name)
    return members


def _is_canonical_version(value: str) -> bool:
    if _CANONICAL_VERSION_PATTERN.fullmatch(value) is None:
        return False
    _, separator, local = value.partition("+")
    if not separator:
        return True
    return all(
        not (part.isdigit() and len(part) > 1 and part.startswith("0")) for part in local.split(".")
    )


def _is_bounded_printable_ascii(value: str) -> bool:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        return False
    return (
        bool(encoded)
        and len(encoded) <= MAX_CORE_METADATA_VALUE_BYTES
        and all(0x21 <= byte <= 0x7E for byte in encoded)
    )


def _metadata_identity_value(value: str) -> str:
    if not _is_bounded_printable_ascii(value):
        _fail(CORE_METADATA_MALFORMED_ERROR)
    return value


def _canonical_metadata_line_endings(raw: bytes) -> bytes:
    if b"\r" not in raw:
        return raw
    crlf_count = raw.count(b"\r\n")
    if raw.count(b"\r") != crlf_count or raw.count(b"\n") != crlf_count:
        _fail(CORE_METADATA_MALFORMED_ERROR)
    return raw.replace(b"\r\n", b"\n")


def _parse_core_metadata(raw: bytes) -> tuple[str, str]:
    if len(raw) > MAX_ARCHIVE_TEXT_BYTES or b"\x00" in raw:
        _fail(CORE_METADATA_MALFORMED_ERROR)
    canonical = _canonical_metadata_line_endings(raw)
    try:
        message = BytesParser(policy=policy.default).parsebytes(canonical, headersonly=True)
    except (LookupError, UnicodeError, ValueError):
        _fail(CORE_METADATA_MALFORMED_ERROR)
    if message.defects:
        _fail(CORE_METADATA_MALFORMED_ERROR)

    identity_fields: dict[str, list[str]] = {
        "metadata-version": [],
        "name": [],
        "version": [],
    }
    for field, value in message.raw_items():
        field_name = field.casefold()
        if field_name in identity_fields:
            identity_fields[field_name].append(value)
    if any(len(values) != 1 for values in identity_fields.values()):
        _fail(CORE_METADATA_MALFORMED_ERROR)

    metadata_version = _metadata_identity_value(identity_fields["metadata-version"][0])
    name = _metadata_identity_value(identity_fields["name"][0])
    version = _metadata_identity_value(identity_fields["version"][0])
    if re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", metadata_version) is None:
        _fail(CORE_METADATA_MALFORMED_ERROR)
    if name != "weightclass":
        _fail(CORE_METADATA_PROJECT_ERROR)
    if not _is_canonical_version(version):
        _fail(CORE_METADATA_VERSION_ERROR)
    return name, version


def _is_valid_wheel_tag(value: str) -> bool:
    if not _is_bounded_printable_ascii(value):
        return False
    parts = value.split(".")
    return all(
        part
        and part[0].isalnum()
        and part[-1].isalnum()
        and part.isascii()
        and part == part.casefold()
        and all(character.isalnum() or character == "_" for character in part)
        for part in parts
    )


def _wheel_filename_version(filename: str) -> str:
    if not filename.endswith(".whl"):
        _fail(DISTRIBUTION_IDENTITY_ERROR)
    parts = filename[:-4].split("-")
    if len(parts) != 5:
        _fail(DISTRIBUTION_IDENTITY_ERROR)
    project, version, python_tag, abi_tag, platform_tag = parts
    if (
        project != "weightclass"
        or not _is_bounded_printable_ascii(version)
        or not _is_canonical_version(version)
        or not all(_is_valid_wheel_tag(tag) for tag in (python_tag, abi_tag, platform_tag))
    ):
        _fail(DISTRIBUTION_IDENTITY_ERROR)
    return version


def _sdist_filename_version(filename: str) -> str:
    prefix = "weightclass-"
    suffix = ".tar.gz"
    if not filename.startswith(prefix) or not filename.endswith(suffix):
        _fail(DISTRIBUTION_IDENTITY_ERROR)
    version = filename[len(prefix) : -len(suffix)]
    if not _is_bounded_printable_ascii(version) or not _is_canonical_version(version):
        _fail(DISTRIBUTION_IDENTITY_ERROR)
    return version


def _read_core_metadata_member(archive: zipfile.ZipFile, member: zipfile.ZipInfo) -> bytes:
    try:
        return _read_archive_member(archive, member, "distribution core metadata")
    except IsolationError:
        _fail(CORE_METADATA_MALFORMED_ERROR)


def _wheel_core_metadata(wheel: Path, fingerprint: _ArtifactFingerprint) -> tuple[str, str]:
    with _verified_artifact_snapshot(wheel, fingerprint) as artifact:
        snapshot, _snapshot_fingerprint = artifact
        _verify_physical_wheel(snapshot)
        snapshot.seek(0)
        try:
            with zipfile.ZipFile(cast(BinaryIO, _ZipSnapshotReader(snapshot))) as archive:
                members = _wheel_members(archive, wheel)
                candidates = [
                    member
                    for member in members
                    if (
                        len(PurePosixPath(member.filename).parts) == 2
                        and PurePosixPath(member.filename).parts[0].endswith(".dist-info")
                        and PurePosixPath(member.filename).parts[1] == "METADATA"
                        and not member.is_dir()
                    )
                ]
                if len(candidates) != 1:
                    _fail(CORE_METADATA_MISSING_ERROR)
                dist_info_root = PurePosixPath(candidates[0].filename).parts[0]
                if any(
                    parts
                    and parts[0].casefold().endswith(".dist-info")
                    and parts[0] != dist_info_root
                    for member in members
                    if (parts := PurePosixPath(member.filename).parts)
                ):
                    _fail(DISTRIBUTION_IDENTITY_ERROR)
                raw = _read_core_metadata_member(archive, candidates[0])
        except IsolationError:
            raise
        except (
            EOFError,
            NotImplementedError,
            OSError,
            OverflowError,
            RuntimeError,
            UnicodeError,
            ValueError,
            zipfile.BadZipFile,
            zlib.error,
        ):
            _fail(CORE_METADATA_MALFORMED_ERROR)
    name, version = _parse_core_metadata(raw)
    if (
        _wheel_filename_version(wheel.name) != version
        or dist_info_root != f"{name}-{version}.dist-info"
    ):
        _fail(DISTRIBUTION_IDENTITY_ERROR)
    return name, version


def _sdist_core_metadata(sdist: Path, fingerprint: _ArtifactFingerprint) -> tuple[str, str]:
    with _open_verified_sdist(sdist, fingerprint) as archive:
        members = _safe_members(archive)
        root = _sdist_root(members)
        candidates = [member for member in members if member.name == f"{root}/PKG-INFO"]
        if len(candidates) != 1 or not candidates[0].isfile():
            _fail(CORE_METADATA_MISSING_ERROR)
        extracted = archive.extractfile(candidates[0])
        if extracted is None:
            _fail(CORE_METADATA_MALFORMED_ERROR)
        try:
            with extracted:
                raw = extracted.read(MAX_ARCHIVE_TEXT_BYTES + 1)
        except (OSError, EOFError, ValueError):
            _fail(CORE_METADATA_MALFORMED_ERROR)
        if len(raw) > MAX_ARCHIVE_TEXT_BYTES:
            _fail(CORE_METADATA_MALFORMED_ERROR)
    name, version = _parse_core_metadata(raw)
    if _sdist_filename_version(sdist.name) != version or root != f"{name}-{version}":
        _fail(DISTRIBUTION_IDENTITY_ERROR)
    return name, version


def _validate_expected_version(expected_version: str | None) -> None:
    if expected_version is None:
        return
    if (
        type(expected_version) is not str
        or not _is_bounded_printable_ascii(expected_version)
        or not _is_canonical_version(expected_version)
    ):
        _fail(EXPECTED_VERSION_ERROR)


def _verify_distribution_core_metadata(
    wheel: Path,
    sdist: Path,
    *,
    wheel_fingerprint: _ArtifactFingerprint,
    sdist_fingerprint: _ArtifactFingerprint,
    expected_version: str | None,
) -> None:
    _wheel_name, wheel_version = _wheel_core_metadata(wheel, wheel_fingerprint)
    _sdist_name, sdist_version = _sdist_core_metadata(sdist, sdist_fingerprint)
    if wheel_version != sdist_version:
        _fail(CORE_METADATA_VERSION_MISMATCH_ERROR)
    if expected_version is not None and wheel_version != expected_version:
        _fail(CORE_METADATA_EXPECTED_VERSION_ERROR)


def verify_wheel(
    wheel: Path,
    *,
    expected_fingerprint: _ArtifactFingerprint | None = None,
    require_advisory: bool = False,
) -> None:
    with _verified_artifact_snapshot(wheel, expected_fingerprint) as artifact:
        snapshot, _fingerprint = artifact
        _verify_physical_wheel(snapshot)
        snapshot.seek(0)
        try:
            with zipfile.ZipFile(cast(BinaryIO, _ZipSnapshotReader(snapshot))) as archive:
                members = _wheel_members(archive, wheel)
                member_names = [member.filename for member in members if not member.is_dir()]
                if require_advisory:
                    for required_path in REQUIRED_WHEEL_ADVISORY_PATHS:
                        if member_names.count(required_path) != 1:
                            _fail(
                                f"{wheel.name}: expected one installed advisory artifact: "
                                f"{required_path}"
                            )
                folded_registry_path = WHEEL_REGISTRY_PATH.casefold()
                folded_registry_suffix = f"/{WHEEL_REGISTRY_PATH}".casefold()
                registry_aliases = [
                    member
                    for member in members
                    if member.filename.casefold() == folded_registry_path
                    or member.filename.casefold().endswith(folded_registry_suffix)
                ]
                canonical_registries = [
                    member for member in registry_aliases if member.filename == WHEEL_REGISTRY_PATH
                ]
                if (
                    len(registry_aliases) != 1
                    or len(canonical_registries) != 1
                    or canonical_registries[0].is_dir()
                ):
                    _fail(f"{wheel.name}: expected exactly one production registry")
                registry = canonical_registries[0]
                _load_empty_registry(
                    _read_archive_member(
                        archive,
                        registry,
                        f"{wheel.name}:{WHEEL_REGISTRY_PATH}",
                    ),
                    f"{wheel.name}:{WHEEL_REGISTRY_PATH}",
                )
                for member in members:
                    _reject_forbidden_top_level_content(PurePosixPath(member.filename), wheel.name)
                    if member.is_dir():
                        continue
                    name = member.filename
                    if _is_tests_path(PurePosixPath(name)) or _has_forbidden_fuzzy_path(
                        PurePosixPath(name)
                    ):
                        _fail(f"{wheel.name}: test-only artifact shipped: {name}")
                    raw = _read_archive_member(archive, member, f"{wheel.name}:{name}")
                    if any(token.encode() in raw for token in FORBIDDEN_WHEEL_TEXT):
                        _fail(f"{wheel.name}: synthetic or candidate-like content shipped: {name}")
                    document_kind = _qualification_document_kind(raw, f"{wheel.name}:{name}")
                    if document_kind is not None:
                        _fail(f"{wheel.name}: qualification {document_kind} shipped: {name}")
        except IsolationError:
            raise
        except (
            EOFError,
            NotImplementedError,
            OSError,
            OverflowError,
            RuntimeError,
            UnicodeError,
            ValueError,
            zipfile.BadZipFile,
            zlib.error,
        ):
            _fail("physical wheel could not be parsed")


def _safe_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members: list[tarfile.TarInfo] = []
    total_size = 0
    for member in archive:
        if len(members) >= MAX_ARCHIVE_MEMBERS:
            _fail(ARCHIVE_MEMBER_COUNT_LIMIT_ERROR)
        total_size = _validate_sdist_member_metadata(member, total_size)
        members.append(member)
    names: set[str] = set()
    normalized_names: set[str] = set()
    path_root = _ArchivePathNode("")
    for member in members:
        name = member.name
        if not _has_bounded_archive_name(name):
            _fail(ARCHIVE_MEMBER_NAME_LIMIT_ERROR)
        path = PurePosixPath(name)
        normalized_name = unicodedata.normalize("NFC", name).casefold()
        error_message = f"unsafe sdist member: {name}"
        if (
            name in names
            or normalized_name in normalized_names
            or not path.parts
            or not _has_bounded_archive_path(path)
            or name != path.as_posix()
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in name
            or "\x00" in name
            or not (member.isdir() or member.isfile())
        ):
            _fail(error_message)
        _register_archive_path(
            path_root,
            path,
            is_directory=member.isdir(),
            error_message=error_message,
        )
        names.add(name)
        normalized_names.add(normalized_name)
    return members


def _sdist_root(members: list[tarfile.TarInfo]) -> str:
    roots = {PurePosixPath(member.name).parts[0] for member in members}
    if len(roots) != 1:
        _fail("sdist must contain exactly one archive root")
    return roots.pop()


def verify_sdist(
    sdist: Path,
    *,
    expected_fingerprint: _ArtifactFingerprint | None = None,
    require_advisory: bool = False,
) -> None:
    with _open_verified_sdist(sdist, expected_fingerprint) as archive:
        members = _safe_members(archive)
        root = _sdist_root(members)
        expected_registry = f"{root}/{SDIST_REGISTRY_PATH}"
        folded_registry_suffix = WHEEL_REGISTRY_PATH.casefold()
        registries = [
            member for member in members if member.name.casefold().endswith(folded_registry_suffix)
        ]
        if len(registries) != 1 or registries[0].name != expected_registry:
            _fail(f"{sdist.name}: expected exactly one production registry")
        extracted = archive.extractfile(registries[0])
        if extracted is None:
            _fail(f"{sdist.name}: registry is not a regular file")
        with extracted:
            registry_raw = extracted.read(MAX_ARCHIVE_TEXT_BYTES + 1)
        if len(registry_raw) > MAX_ARCHIVE_TEXT_BYTES:
            _fail(f"{sdist.name}:{registries[0].name}: registry exceeds the scan limit")
        _load_empty_registry(registry_raw, f"{sdist.name}:{registries[0].name}")
        for member in members:
            relative = PurePosixPath(member.name).relative_to(root)
            _reject_forbidden_top_level_content(relative, sdist.name)
            if _is_tests_path(relative) or _has_forbidden_fuzzy_path(relative):
                if not relative.parts or relative.parts[0] != "tests":
                    _fail(f"{sdist.name}: test-only artifact escaped tests/: {member.name}")
        regular_names = [
            PurePosixPath(member.name).relative_to(root).as_posix()
            for member in members
            if member.isfile()
        ]
        for suffix in REQUIRED_SDIST_TEST_SUFFIXES:
            if regular_names.count(suffix) != 1:
                _fail(f"{sdist.name}: expected one test-only artifact: {suffix}")
        if require_advisory:
            for suffix in REQUIRED_SDIST_ADVISORY_PATHS:
                if regular_names.count(suffix) != 1:
                    _fail(f"{sdist.name}: expected one installed advisory artifact: {suffix}")


def run_extracted_sdist_tests(
    sdist: Path,
    *,
    expected_fingerprint: _ArtifactFingerprint | None = None,
) -> None:
    with tempfile.TemporaryDirectory(prefix="wclass-sdist-isolation-") as directory:
        destination = Path(directory)
        with _open_verified_sdist(sdist, expected_fingerprint) as archive:
            members = _safe_members(archive)
            root = _sdist_root(members)
            archive.extractall(destination, members=members)
        roots = list(destination.iterdir())
        expected_root = destination / root
        if roots != [expected_root] or not expected_root.is_dir():
            _fail(f"{sdist.name}: expected one archive root")
        current_python_bin = Path(sys.executable).resolve().parent
        environment = {
            "PATH": f"{current_python_bin}{os.pathsep}{os.defpath}",
            "PYTHONPATH": str(expected_root / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        subprocess.run(
            [
                sys.executable,
                "-W",
                "error::ResourceWarning",
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
            ],
            cwd=expected_root,
            env=environment,
            check=True,
        )


def _stat_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _fingerprint_artifact(path: Path) -> _ArtifactFingerprint:
    with _verified_artifact_snapshot(path) as artifact:
        _snapshot, fingerprint = artifact
        return fingerprint


def _distribution_snapshot(dist_dir: Path) -> _DistributionSnapshot:
    try:
        with os.scandir(dist_dir) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name)
    except OSError:
        _fail("distribution directory could not be inspected")
    if len(entries) != 2:
        _fail("distribution directory must contain exactly one wheel and one sdist")

    wheels: list[Path] = []
    sdists: list[Path] = []
    for entry in entries:
        try:
            metadata = entry.stat(follow_symlinks=False)
        except OSError:
            _fail("distribution artifact could not be inspected")
        if entry.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            _fail("distribution artifacts must be nonsymlink regular files")
        path = Path(entry.path)
        if entry.name.endswith(".whl"):
            wheels.append(path)
        elif entry.name.endswith(".tar.gz"):
            sdists.append(path)
        else:
            _fail("distribution directory must contain exactly one wheel and one sdist")
    if len(wheels) != 1 or len(sdists) != 1:
        _fail("distribution directory must contain exactly one wheel and one sdist")

    fingerprints = tuple(_fingerprint_artifact(path) for path in (wheels[0], sdists[0]))
    try:
        with os.scandir(dist_dir) as iterator:
            final_names = sorted(entry.name for entry in iterator)
    except OSError:
        _fail("distribution directory changed while it was inventoried")
    if final_names != sorted(fingerprint.name for fingerprint in fingerprints):
        _fail("distribution directory changed while it was inventoried")
    return _DistributionSnapshot(
        wheel=wheels[0],
        sdist=sdists[0],
        fingerprints=fingerprints,
    )


def verify_distribution_directory(
    source: Path,
    dist_dir: Path,
    *,
    run_sdist_tests_requested: bool,
    expected_version: str | None = None,
) -> None:
    _validate_expected_version(expected_version)
    initial = _distribution_snapshot(dist_dir)
    wheel_fingerprint, sdist_fingerprint = initial.fingerprints
    verify_source_registry(source)
    verify_wheel(initial.wheel, expected_fingerprint=wheel_fingerprint, require_advisory=True)
    verify_sdist(initial.sdist, expected_fingerprint=sdist_fingerprint, require_advisory=True)
    _verify_distribution_core_metadata(
        initial.wheel,
        initial.sdist,
        wheel_fingerprint=wheel_fingerprint,
        sdist_fingerprint=sdist_fingerprint,
        expected_version=expected_version,
    )
    if _distribution_snapshot(dist_dir) != initial:
        _fail("distribution artifacts changed during verification")
    if run_sdist_tests_requested:
        try:
            run_extracted_sdist_tests(
                initial.sdist,
                expected_fingerprint=sdist_fingerprint,
            )
        finally:
            try:
                final = _distribution_snapshot(dist_dir)
            except IsolationError:
                _fail("distribution artifacts changed after extracted sdist tests")
            if final != initial:
                _fail("distribution artifacts changed after extracted sdist tests")


def normalized_distribution(path: Path) -> NormalizedDistribution:
    """Return a normalized view only after the existing security preflight passes."""

    fingerprint = _fingerprint_artifact(path)
    if path.name.endswith(".whl"):
        verify_wheel(path, expected_fingerprint=fingerprint, require_advisory=True)
        name, version = _wheel_core_metadata(path, fingerprint)
        normalized: list[NormalizedArchiveMember] = []
        with _verified_artifact_snapshot(path, fingerprint) as artifact:
            snapshot, _ = artifact
            _verify_physical_wheel(snapshot)
            snapshot.seek(0)
            with zipfile.ZipFile(cast(BinaryIO, _ZipSnapshotReader(snapshot))) as archive:
                for wheel_member in _wheel_members(archive, path):
                    raw = b"" if wheel_member.is_dir() else archive.read(wheel_member)
                    normalized.append(
                        NormalizedArchiveMember(
                            wheel_member.filename,
                            "directory" if wheel_member.is_dir() else "file",
                            (wheel_member.external_attr >> 16) & 0o7777,
                            wheel_member.file_size,
                            hashlib.sha256(raw).hexdigest(),
                        )
                    )
        roots = sorted(
            {
                PurePosixPath(member.path).parts[0]
                for member in normalized
                if PurePosixPath(member.path).parts[0].endswith(".dist-info")
            }
        )
        if len(roots) != 1:
            _fail(DISTRIBUTION_IDENTITY_ERROR)
        return NormalizedDistribution(
            "wheel", roots[0], (("Name", name), ("Version", version)), tuple(sorted(normalized))
        )
    if path.name.endswith(".tar.gz"):
        verify_sdist(path, expected_fingerprint=fingerprint, require_advisory=True)
        name, version = _sdist_core_metadata(path, fingerprint)
        normalized = []
        with _open_verified_sdist(path, fingerprint) as archive:
            members = _safe_members(archive)
            root = _sdist_root(members)
            for tar_member in members:
                relative = PurePosixPath(tar_member.name).relative_to(root).as_posix()
                if relative == ".":
                    relative = ""
                raw = b""
                if tar_member.isfile():
                    extracted = archive.extractfile(tar_member)
                    if extracted is None:
                        _fail("sdist member could not be inspected")
                    with extracted:
                        raw = extracted.read()
                normalized.append(
                    NormalizedArchiveMember(
                        relative,
                        "directory" if tar_member.isdir() else "file",
                        tar_member.mode & 0o7777,
                        tar_member.size,
                        hashlib.sha256(raw).hexdigest(),
                    )
                )
        return NormalizedDistribution(
            "sdist", root, (("Name", name), ("Version", version)), tuple(sorted(normalized))
        )
    _fail("unsupported distribution artifact")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument("--run-sdist-tests", action="store_true")
    parser.add_argument("--expected-version")
    args = parser.parse_args()
    try:
        verify_distribution_directory(
            args.source,
            args.dist_dir,
            run_sdist_tests_requested=args.run_sdist_tests,
            expected_version=args.expected_version,
        )
    except IsolationError as error:
        print(f"distribution isolation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
