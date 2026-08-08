"""Test-owned distribution isolation gate; this module is never shipped in a wheel."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import stat
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
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, NoReturn, Protocol, cast

WHEEL_REGISTRY_PATH = "weightclass/delegation_qualifications.json"
SDIST_REGISTRY_PATH = "src/weightclass/delegation_qualifications.json"
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
ARCHIVE_MEMBER_NAME_LIMIT_ERROR = "archive member name exceeds the safety limit"
ARCHIVE_MEMBER_COUNT_LIMIT_ERROR = "archive member count exceeds the safety limit"
ARCHIVE_TOTAL_SIZE_LIMIT_ERROR = "archive total size exceeds the safety limit"
ARCHIVE_DIRECTORY_SIZE_ERROR = "archive directory has nonzero size"
WHEEL_MEMBER_SIZE_LIMIT_ERROR = "wheel member size exceeds the safety limit"
SDIST_MEMBER_SIZE_LIMIT_ERROR = "sdist member size exceeds the safety limit"
_SUPPORTED_PHYSICAL_MEMBER_TYPES = frozenset((tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE))
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


def _fail(message: str) -> NoReturn:
    raise IsolationError(message)


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
    if not isinstance(value, Mapping) or value != EMPTY_REGISTRY:
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


def _require_bounded_outer_artifact(path: Path) -> None:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError:
        _fail("distribution artifact could not be inspected")
    _validate_outer_artifact_metadata(metadata)


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
def _verified_sdist_snapshot(sdist: Path) -> Iterator[BinaryIO]:
    try:
        with (
            open(sdist, "rb", opener=_open_no_follow) as compressed,
            tempfile.SpooledTemporaryFile(
                max_size=MAX_SDIST_EXTENSION_BYTES,
                mode="w+b",
            ) as snapshot,
        ):
            snapshot_io = cast(BinaryIO, snapshot)
            before = os.fstat(compressed.fileno())
            _validate_outer_artifact_metadata(before)
            with gzip.GzipFile(fileobj=compressed, mode="rb") as stream:
                _copy_verified_physical_sdist(stream, snapshot_io)
            after = os.fstat(compressed.fileno())
            try:
                current = sdist.stat(follow_symlinks=False)
            except OSError:
                _fail("distribution artifact changed while it was inspected")
            if _stat_identity(before) != _stat_identity(after) or _stat_identity(
                after
            ) != _stat_identity(current):
                _fail("distribution artifact changed while it was inspected")
            snapshot_io.seek(0)
            yield snapshot_io
    except IsolationError:
        raise
    except (EOFError, OSError, ValueError, zlib.error):
        _fail("physical sdist could not be inspected")


@contextmanager
def _open_verified_sdist(sdist: Path) -> Iterator[tarfile.TarFile]:
    with _verified_sdist_snapshot(sdist) as snapshot:
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


def verify_source_registry(root: Path) -> None:
    registry = root / "src" / "weightclass" / "delegation_qualifications.json"
    _load_empty_registry(registry.read_bytes(), str(registry))


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


def verify_wheel(wheel: Path) -> None:
    _require_bounded_outer_artifact(wheel)
    with zipfile.ZipFile(wheel) as archive:
        members = _wheel_members(archive, wheel)
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
            _read_archive_member(archive, registry, f"{wheel.name}:{WHEEL_REGISTRY_PATH}"),
            f"{wheel.name}:{WHEEL_REGISTRY_PATH}",
        )
        for member in members:
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


def verify_sdist(sdist: Path) -> None:
    with _open_verified_sdist(sdist) as archive:
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


def run_extracted_sdist_tests(sdist: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="wclass-sdist-isolation-") as directory:
        destination = Path(directory)
        with _open_verified_sdist(sdist) as archive:
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
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _fail("distribution artifact could not be opened safely")
    try:
        before = os.fstat(descriptor)
        _validate_outer_artifact_metadata(before)
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1_048_576):
            digest.update(chunk)
        after = os.fstat(descriptor)
    except OSError:
        _fail("distribution artifact could not be fingerprinted")
    finally:
        os.close(descriptor)
    try:
        current = path.stat(follow_symlinks=False)
    except OSError:
        _fail("distribution artifact changed while it was fingerprinted")
    if _stat_identity(before) != _stat_identity(after) or _stat_identity(after) != _stat_identity(
        current
    ):
        _fail("distribution artifact changed while it was fingerprinted")
    return _ArtifactFingerprint(
        name=path.name,
        device=after.st_dev,
        inode=after.st_ino,
        mode=after.st_mode,
        size=after.st_size,
        modified_ns=after.st_mtime_ns,
        changed_ns=after.st_ctime_ns,
        sha256=digest.hexdigest(),
    )


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
) -> None:
    initial = _distribution_snapshot(dist_dir)
    verify_source_registry(source)
    verify_wheel(initial.wheel)
    verify_sdist(initial.sdist)
    if _distribution_snapshot(dist_dir) != initial:
        _fail("distribution artifacts changed during verification")
    if run_sdist_tests_requested:
        try:
            run_extracted_sdist_tests(initial.sdist)
        finally:
            try:
                final = _distribution_snapshot(dist_dir)
            except IsolationError:
                _fail("distribution artifacts changed after extracted sdist tests")
            if final != initial:
                _fail("distribution artifacts changed after extracted sdist tests")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument("--run-sdist-tests", action="store_true")
    args = parser.parse_args()
    try:
        verify_distribution_directory(
            args.source,
            args.dist_dir,
            run_sdist_tests_requested=args.run_sdist_tests,
        )
    except IsolationError as error:
        print(f"distribution isolation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
