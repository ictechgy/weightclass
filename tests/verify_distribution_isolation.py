"""Test-owned distribution isolation gate; this module is never shipped in a wheel."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unicodedata
import zipfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

WHEEL_REGISTRY_PATH = "weightclass/delegation_qualifications.json"
SDIST_REGISTRY_PATH = "src/weightclass/delegation_qualifications.json"
EMPTY_REGISTRY = {
    "records": [],
    "registry_schema_version": 1,
    "suite_revision": "delegation-conformance-v2",
}
MAX_ARCHIVE_TEXT_BYTES = 262_144
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
FORBIDDEN_WHEEL_PATH_PARTS = (
    "tests/",
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
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateKeyError):
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


def _wheel_members(archive: zipfile.ZipFile, wheel: Path) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    names: set[str] = set()
    casefolded_names: set[str] = set()
    for member in members:
        name = member.filename
        path = PurePosixPath(name)
        casefolded_name = name.casefold()
        if (
            name in names
            or casefolded_name in casefolded_names
            or not path.parts
            or path.is_absolute()
            or ".." in path.parts
            or "." in path.parts
            or "\\" in name
            or "\x00" in name
            or name != (f"{path.as_posix()}/" if member.is_dir() else path.as_posix())
        ):
            _fail(f"{wheel.name}: noncanonical or duplicate archive member: {name}")
        names.add(name)
        casefolded_names.add(casefolded_name)
    return members


def verify_wheel(wheel: Path) -> None:
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
            lowered = name.lower()
            if any(part in lowered for part in FORBIDDEN_WHEEL_PATH_PARTS):
                _fail(f"{wheel.name}: test-only artifact shipped: {name}")
            raw = _read_archive_member(archive, member, f"{wheel.name}:{name}")
            if any(token.encode() in raw for token in FORBIDDEN_WHEEL_TEXT):
                _fail(f"{wheel.name}: synthetic or candidate-like content shipped: {name}")
            document_kind = _qualification_document_kind(raw, f"{wheel.name}:{name}")
            if document_kind is not None:
                _fail(f"{wheel.name}: qualification {document_kind} shipped: {name}")


def _safe_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    names: set[str] = set()
    normalized_names: set[str] = set()
    for member in members:
        path = PurePosixPath(member.name)
        normalized_name = unicodedata.normalize("NFC", member.name).casefold()
        if (
            member.name in names
            or normalized_name in normalized_names
            or not path.parts
            or member.name != path.as_posix()
            or path.is_absolute()
            or ".." in path.parts
            or not (member.isdir() or member.isfile())
        ):
            _fail(f"unsafe sdist member: {member.name}")
        names.add(member.name)
        normalized_names.add(normalized_name)
    return members


def _sdist_root(members: list[tarfile.TarInfo]) -> str:
    roots = {PurePosixPath(member.name).parts[0] for member in members}
    if len(roots) != 1:
        _fail("sdist must contain exactly one archive root")
    return roots.pop()


def verify_sdist(sdist: Path) -> None:
    with tarfile.open(sdist, "r:gz") as archive:
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
        registry_raw = extracted.read(MAX_ARCHIVE_TEXT_BYTES + 1)
        if len(registry_raw) > MAX_ARCHIVE_TEXT_BYTES:
            _fail(f"{sdist.name}:{registries[0].name}: registry exceeds the scan limit")
        _load_empty_registry(registry_raw, f"{sdist.name}:{registries[0].name}")
        for member in members:
            relative = PurePosixPath(member.name).relative_to(root)
            lowered = relative.as_posix().lower()
            if any(part in lowered for part in FORBIDDEN_WHEEL_PATH_PARTS):
                if not relative.parts or relative.parts[0].lower() != "tests":
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
        with tarfile.open(sdist, "r:gz") as archive:
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--sdist", type=Path, required=True)
    parser.add_argument("--run-sdist-tests", action="store_true")
    args = parser.parse_args()
    verify_source_registry(args.source)
    verify_wheel(args.wheel)
    verify_sdist(args.sdist)
    if args.run_sdist_tests:
        run_extracted_sdist_tests(args.sdist)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
