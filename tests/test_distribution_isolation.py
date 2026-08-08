from __future__ import annotations

import gzip
import io
import json
import os
import struct
import subprocess
import sys
import tarfile
import tempfile
import unicodedata
import unittest
import warnings
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast
from unittest.mock import patch

from tests.verify_distribution_isolation import (
    ARCHIVE_MEMBER_NAME_LIMIT_ERROR,
    MAX_ARCHIVE_MEMBER_NAME_BYTES,
    MAX_ARCHIVE_TEXT_BYTES,
    MAX_SDIST_EXTENSION_BYTES,
    IsolationError,
    _fingerprint_artifact,
    _safe_members,
    _verify_physical_sdist,
    _wheel_members,
    run_extracted_sdist_tests,
    verify_sdist,
    verify_source_registry,
    verify_wheel,
)


def _candidate_like_record() -> dict[str, object]:
    return {
        "record_schema_version": 1,
        "artifact_sha256": "a" * 64,
        "artifact_size_bytes": 24,
        "runtime_build_id": "opaque-runtime-build",
        "platform": {"os": "linux", "architecture": "x86_64"},
        "protocol_version": 1,
        "suite_revision": "delegation-conformance-v2",
        "adapter_id": "claude-native-v1",
        "vendor_family": "claude",
        "conformance_evidence_sha256": "b" * 64,
        "result_matrix": [
            {
                "role": role,
                "category": category,
                "action": action,
                "mode": mode,
                "passed": True,
            }
            for role in ("orchestrator", "worker", "reviewer")
            for category in ("implementation", "tests", "documentation")
            for action in ("workspace_read", "workspace_write", "command_execution")
            for mode in ("allow", "deny")
        ],
        "scenario_results": [
            {"id": scenario_id, "passed": True}
            for scenario_id in (
                "action_attribution",
                "artifact_integrity_and_substitution",
                "descendant_cleanup",
                "descendant_leakage",
                "distinct_enforcement_contexts",
                "integration_restriction",
                "integration_verification_commands",
                "output_channel_separation",
                "process_creation_attribution",
                "reviewer_rejection",
                "runtime_deadline",
                "stage_order",
                "worker_concurrency_bound",
            )
        ],
    }


def _evidence_like_document() -> dict[str, object]:
    candidate = _candidate_like_record()
    return {
        "evidence_schema_version": 2,
        **{
            key: value
            for key, value in candidate.items()
            if key
            not in {
                "record_schema_version",
                "conformance_evidence_sha256",
            }
        },
    }


def _write_sdist_fixture(
    directory: str,
    extra_members: tuple[str, ...] = (),
    extra_files: tuple[tuple[str, str], ...] = (),
) -> Path:
    sdist = Path(directory) / "weightclass-0.tar.gz"
    root = Path(directory) / "payload/weightclass-0"
    registry = root / "src/weightclass/delegation_qualifications.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        '{"records":[],"registry_schema_version":1,"suite_revision":"delegation-conformance-v2"}',
        encoding="utf-8",
    )
    for relative_path in (
        "tests/synthetic_descendant_containment.py",
        "tests/synthetic_probe_child.py",
        "tests/synthetic_probe_protocol.py",
        "tests/synthetic_probe_runner.py",
        "tests/test_distribution_isolation.py",
        "tests/test_synthetic_probe_protocol.py",
        "tests/verify_distribution_isolation.py",
    ):
        asset = root / relative_path
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_text("pass\n", encoding="utf-8")
    for relative_path, contents in extra_files:
        asset = root / relative_path
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_text(contents, encoding="utf-8")
    with tarfile.open(sdist, "w:gz") as archive:
        archive.add(root, arcname="weightclass-0")
        for member_name in extra_members:
            raw = b"test-only\n"
            member = tarfile.TarInfo(member_name)
            member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))
    return sdist


def _write_distribution_fixture(
    directory: str,
    *,
    extracted_test: str | None = None,
) -> tuple[Path, Path, Path]:
    base = Path(directory)
    source = base / "source"
    source_registry = source / "src/weightclass/delegation_qualifications.json"
    source_registry.parent.mkdir(parents=True)
    source_registry.write_text(
        '{"records":[],"registry_schema_version":1,"suite_revision":"delegation-conformance-v2"}',
        encoding="utf-8",
    )

    dist = base / "dist"
    dist.mkdir()
    wheel = dist / "weightclass-0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "weightclass/delegation_qualifications.json",
            '{"records":[],"registry_schema_version":1,'
            '"suite_revision":"delegation-conformance-v2"}',
        )
        archive.writestr("weightclass/module.py", "VALUE = 1\n")

    extra_files: tuple[tuple[str, str], ...] = ()
    if extracted_test is not None:
        extra_files = (("tests/test_artifact_mutation.py", extracted_test),)
    build_directory = base / "build"
    build_directory.mkdir()
    built_sdist = _write_sdist_fixture(str(build_directory), extra_files=extra_files)
    sdist = dist / built_sdist.name
    built_sdist.replace(sdist)
    return source, wheel, sdist


def _tar_physical_record(name: str, payload: bytes, type_flag: bytes) -> bytes:
    member = tarfile.TarInfo(name)
    member.size = len(payload)
    member.type = type_flag
    header = member.tobuf(format=tarfile.GNU_FORMAT)
    if len(header) != 512:
        raise AssertionError("test fixture name unexpectedly required an extension record")
    padding = b"\0" * (-len(payload) % 512)
    return header + payload + padding


def _pax_record(key: str, value: str) -> bytes:
    body = f" {key}={value}\n"
    length = len(body) + 1
    while True:
        record = f"{length}{body}".encode()
        if len(record) == length:
            return record
        length = len(record)


def _pax_record_with_size(key: str, size: int) -> bytes:
    value_size = size
    while True:
        record = _pax_record(key, "x" * value_size)
        difference = size - len(record)
        if difference == 0:
            return record
        value_size += difference
        if value_size < 0:
            raise AssertionError("requested PAX record size is too small")


def _append_physical_tar_records(sdist: Path, records: bytes) -> None:
    raw = gzip.decompress(sdist.read_bytes())
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        members = archive.getmembers()
    last = members[-1]
    body_end = last.offset_data + ((last.size + 511) // 512) * 512
    modified = raw[:body_end] + records + (b"\0" * 1_024)
    modified += b"\0" * (-len(modified) % 10_240)
    sdist.write_bytes(gzip.compress(modified, mtime=0))


def _replace_tar_checksum(header: bytearray) -> None:
    header[148:156] = b"        "
    checksum = sum(header)
    header[148:156] = f"{checksum:06o}\0 ".encode()


def _run_distribution_verifier(
    source: Path,
    dist: Path,
    *,
    run_sdist_tests: bool = False,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(Path(__file__).with_name("verify_distribution_isolation.py")),
        "--source",
        str(source),
        "--dist-dir",
        str(dist),
    ]
    if run_sdist_tests:
        command.append("--run-sdist-tests")
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _write_wheel_with_member_count(path: Path, member_count: int) -> None:
    if member_count < 1:
        raise AssertionError("a test wheel must contain its production registry")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "weightclass/delegation_qualifications.json",
            '{"records":[],"registry_schema_version":1,'
            '"suite_revision":"delegation-conformance-v2"}',
        )
        for index in range(member_count - 1):
            archive.writestr(f"weightclass/bounded_{index:04d}.py", b"")


def _classic_zip_offsets(raw: bytes | bytearray) -> tuple[int, int]:
    eocd_offset = raw.rfind(b"PK\x05\x06")
    if eocd_offset < 0:
        raise AssertionError("test fixture has no classic ZIP EOCD")
    central_offset = struct.unpack_from("<I", raw, eocd_offset + 16)[0]
    return eocd_offset, central_offset


def _first_zip_member_layout(
    raw: bytes | bytearray,
) -> tuple[int, int, int, int, int, int]:
    eocd_offset, central_offset = _classic_zip_offsets(raw)
    local_offset = struct.unpack_from("<I", raw, central_offset + 42)[0]
    compressed_size = struct.unpack_from("<I", raw, central_offset + 20)[0]
    uncompressed_size = struct.unpack_from("<I", raw, central_offset + 24)[0]
    name_size, extra_size = struct.unpack_from("<HH", raw, local_offset + 26)
    payload_offset = local_offset + 30 + name_size + extra_size
    return (
        eocd_offset,
        central_offset,
        local_offset,
        payload_offset,
        compressed_size,
        uncompressed_size,
    )


class _WheelMemberArchive:
    def __init__(self, members: list[zipfile.ZipInfo]) -> None:
        self._members = members

    def infolist(self) -> list[zipfile.ZipInfo]:
        return self._members


class _SdistMemberArchive:
    def __init__(self, members: list[tarfile.TarInfo]) -> None:
        self._members = members

    def __iter__(self) -> Iterator[tarfile.TarInfo]:
        return iter(self._members)

    def getmembers(self) -> list[tarfile.TarInfo]:
        raise AssertionError("sdist inventory was eagerly loaded")


def _workflow_step_blocks(path: Path, job: str) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    job_start = lines.index(f"  {job}:")
    job_end = len(lines)
    for index in range(job_start + 1, len(lines)):
        line = lines[index]
        if line.startswith("  ") and not line.startswith("    ") and line.endswith(":"):
            job_end = index
            break
    steps_start = lines.index("    steps:", job_start, job_end)
    blocks: list[list[str]] = []
    for line in lines[steps_start + 1 : job_end]:
        if line.startswith("      - "):
            blocks.append([line])
        elif blocks:
            blocks[-1].append(line)
    return ["\n".join(block) for block in blocks]


def _workflow_job_block(path: Path, job: str) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    job_start = lines.index(f"  {job}:")
    job_end = len(lines)
    for index in range(job_start + 1, len(lines)):
        line = lines[index]
        if line.startswith("  ") and not line.startswith("    ") and line.endswith(":"):
            job_end = index
            break
    return "\n".join(lines[job_start:job_end])


def _workflow_step_name(block: str) -> str:
    first_line = block.splitlines()[0]
    prefix = "      - name: "
    if not first_line.startswith(prefix):
        return ""
    return first_line.removeprefix(prefix)


class DistributionIsolationTests(unittest.TestCase):
    def test_outer_artifact_size_is_rejected_before_hashing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for suffix in (".whl", ".tar.gz"):
                with self.subTest(suffix=suffix):
                    artifact = Path(directory) / f"private-artifact{suffix}"
                    with artifact.open("wb") as stream:
                        stream.seek(72 * 1_024 * 1_024)
                        stream.write(b"x")
                    with patch(
                        "tests.verify_distribution_isolation.os.read",
                        side_effect=AssertionError("oversized artifact was hashed"),
                    ):
                        with self.assertRaises(IsolationError) as context:
                            _fingerprint_artifact(artifact)
                    message = str(context.exception)
                    self.assertLess(len(message), 256)
                    self.assertNotIn(artifact.name, message)

    def test_physical_wheel_rejects_4097_entries_before_zipfile(self) -> None:
        with tempfile.TemporaryDirectory() as root_directory:
            for eocd_count in (4_097, 1):
                with (
                    self.subTest(eocd_count=eocd_count),
                    tempfile.TemporaryDirectory(dir=root_directory) as directory,
                ):
                    wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
                    _write_wheel_with_member_count(wheel, 4_097)
                    if eocd_count != 4_097:
                        raw = bytearray(wheel.read_bytes())
                        eocd_offset, _central_offset = _classic_zip_offsets(raw)
                        struct.pack_into("<HH", raw, eocd_offset + 8, eocd_count, eocd_count)
                        wheel.write_bytes(raw)
                    with patch(
                        "tests.verify_distribution_isolation.zipfile.ZipFile",
                        side_effect=AssertionError("ZipFile eagerly loaded the central directory"),
                    ):
                        with self.assertRaisesRegex(
                            IsolationError,
                            "physical wheel member count exceeds",
                        ):
                            verify_wheel(wheel)

    def test_physical_wheel_accepts_exact_4096_entry_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
            _write_wheel_with_member_count(wheel, 4_096)
            verify_wheel(wheel)

    def test_physical_wheel_rejects_zip64_flags_methods_and_multidisk_before_zipfile(
        self,
    ) -> None:
        def zip64_locator(raw: bytearray) -> bytearray:
            eocd_offset, _central_offset = _classic_zip_offsets(raw)
            return raw[:eocd_offset] + bytearray(b"PK\x06\x07" + b"\0" * 16) + raw[eocd_offset:]

        def multidisk(raw: bytearray) -> bytearray:
            eocd_offset, _central_offset = _classic_zip_offsets(raw)
            struct.pack_into("<H", raw, eocd_offset + 4, 1)
            return raw

        def flags(raw: bytearray, value: int) -> bytearray:
            _eocd_offset, central_offset = _classic_zip_offsets(raw)
            local_offset = struct.unpack_from("<I", raw, central_offset + 42)[0]
            struct.pack_into("<H", raw, local_offset + 6, value)
            struct.pack_into("<H", raw, central_offset + 8, value)
            return raw

        def method(raw: bytearray) -> bytearray:
            _eocd_offset, central_offset = _classic_zip_offsets(raw)
            local_offset = struct.unpack_from("<I", raw, central_offset + 42)[0]
            struct.pack_into("<H", raw, local_offset + 8, 99)
            struct.pack_into("<H", raw, central_offset + 10, 99)
            return raw

        cases: tuple[tuple[str, Callable[[bytearray], bytearray]], ...] = (
            ("zip64-locator", zip64_locator),
            ("multidisk", multidisk),
            ("encryption", lambda raw: flags(raw, 0x0001)),
            ("data-descriptor", lambda raw: flags(raw, 0x0008)),
            ("unsupported-flag", lambda raw: flags(raw, 0x0010)),
            ("unsupported-method", method),
        )
        with tempfile.TemporaryDirectory() as root_directory:
            for case_name, mutate in cases:
                with (
                    self.subTest(case=case_name),
                    tempfile.TemporaryDirectory(dir=root_directory) as directory,
                ):
                    wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
                    _write_wheel_with_member_count(wheel, 1)
                    wheel.write_bytes(mutate(bytearray(wheel.read_bytes())))
                    with patch(
                        "tests.verify_distribution_isolation.zipfile.ZipFile",
                        side_effect=AssertionError("unsafe ZIP reached ZipFile"),
                    ):
                        with self.assertRaisesRegex(IsolationError, "physical wheel"):
                            verify_wheel(wheel)

    def test_physical_wheel_rejects_local_mismatch_gap_and_overlap_before_zipfile(
        self,
    ) -> None:
        def local_mismatch(raw: bytearray) -> bytearray:
            _eocd_offset, central_offset = _classic_zip_offsets(raw)
            local_offset = struct.unpack_from("<I", raw, central_offset + 42)[0]
            crc = struct.unpack_from("<I", raw, local_offset + 14)[0]
            struct.pack_into("<I", raw, local_offset + 14, crc ^ 1)
            return raw

        def local_gap(raw: bytearray) -> bytearray:
            eocd_offset, central_offset = _classic_zip_offsets(raw)
            raw[central_offset:central_offset] = b"x"
            struct.pack_into("<I", raw, eocd_offset + 1 + 16, central_offset + 1)
            return raw

        def local_overlap(raw: bytearray) -> bytearray:
            _eocd_offset, central_offset = _classic_zip_offsets(raw)
            local_offset = struct.unpack_from("<I", raw, central_offset + 42)[0]
            compressed_size = struct.unpack_from("<I", raw, central_offset + 20)[0]
            uncompressed_size = struct.unpack_from("<I", raw, central_offset + 24)[0]
            struct.pack_into("<I", raw, central_offset + 20, compressed_size + 1)
            struct.pack_into("<I", raw, central_offset + 24, uncompressed_size + 1)
            struct.pack_into("<I", raw, local_offset + 18, compressed_size + 1)
            struct.pack_into("<I", raw, local_offset + 22, uncompressed_size + 1)
            return raw

        cases: tuple[tuple[str, Callable[[bytearray], bytearray], int], ...] = (
            ("mismatch", local_mismatch, 1),
            ("gap", local_gap, 2),
            ("overlap", local_overlap, 2),
        )
        with tempfile.TemporaryDirectory() as root_directory:
            for case_name, mutate, member_count in cases:
                with (
                    self.subTest(case=case_name),
                    tempfile.TemporaryDirectory(dir=root_directory) as directory,
                ):
                    wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
                    _write_wheel_with_member_count(wheel, member_count)
                    wheel.write_bytes(mutate(bytearray(wheel.read_bytes())))
                    with patch(
                        "tests.verify_distribution_isolation.zipfile.ZipFile",
                        side_effect=AssertionError("invalid local layout reached ZipFile"),
                    ):
                        with self.assertRaisesRegex(IsolationError, "physical wheel"):
                            verify_wheel(wheel)

    def test_physical_wheel_rejects_invalid_deflate_payloads_before_zipfile(self) -> None:
        private_junk = b"private-trailing-deflate-junk" + b"x" * 3

        def trailing_junk(raw: bytearray) -> bytearray:
            (
                eocd_offset,
                central_offset,
                local_offset,
                payload_offset,
                compressed_size,
                _uncompressed_size,
            ) = _first_zip_member_layout(raw)
            payload_end = payload_offset + compressed_size
            if payload_end != central_offset:
                raise AssertionError("single-member wheel layout changed")
            raw[payload_end:payload_end] = private_junk
            new_central_offset = central_offset + len(private_junk)
            new_eocd_offset = eocd_offset + len(private_junk)
            struct.pack_into(
                "<I",
                raw,
                local_offset + 18,
                compressed_size + len(private_junk),
            )
            struct.pack_into(
                "<I",
                raw,
                new_central_offset + 20,
                compressed_size + len(private_junk),
            )
            struct.pack_into("<I", raw, new_eocd_offset + 16, new_central_offset)
            return raw

        def corrupt_stream(raw: bytearray) -> bytearray:
            (
                _eocd_offset,
                _central_offset,
                _local_offset,
                payload_offset,
                _compressed_size,
                _uncompressed_size,
            ) = _first_zip_member_layout(raw)
            raw[payload_offset] = (raw[payload_offset] & 0xF8) | 0x07
            return raw

        def truncated_stream(raw: bytearray) -> bytearray:
            (
                eocd_offset,
                central_offset,
                local_offset,
                payload_offset,
                compressed_size,
                _uncompressed_size,
            ) = _first_zip_member_layout(raw)
            if compressed_size < 2 or payload_offset + compressed_size != central_offset:
                raise AssertionError("single-member wheel layout changed")
            del raw[payload_offset + compressed_size - 1]
            new_central_offset = central_offset - 1
            new_eocd_offset = eocd_offset - 1
            struct.pack_into("<I", raw, local_offset + 18, compressed_size - 1)
            struct.pack_into("<I", raw, new_central_offset + 20, compressed_size - 1)
            struct.pack_into("<I", raw, new_eocd_offset + 16, new_central_offset)
            return raw

        def wrong_crc(raw: bytearray) -> bytearray:
            (
                _eocd_offset,
                central_offset,
                local_offset,
                _payload_offset,
                _compressed_size,
                _uncompressed_size,
            ) = _first_zip_member_layout(raw)
            crc32 = struct.unpack_from("<I", raw, central_offset + 16)[0] ^ 1
            struct.pack_into("<I", raw, local_offset + 14, crc32)
            struct.pack_into("<I", raw, central_offset + 16, crc32)
            return raw

        def wrong_uncompressed_size(raw: bytearray) -> bytearray:
            (
                _eocd_offset,
                central_offset,
                local_offset,
                _payload_offset,
                _compressed_size,
                uncompressed_size,
            ) = _first_zip_member_layout(raw)
            struct.pack_into("<I", raw, local_offset + 22, uncompressed_size + 1)
            struct.pack_into("<I", raw, central_offset + 24, uncompressed_size + 1)
            return raw

        cases: tuple[tuple[str, Callable[[bytearray], bytearray]], ...] = (
            ("trailing-junk-and-declared-compressed-size", trailing_junk),
            ("corrupt-stream", corrupt_stream),
            ("truncated-and-declared-compressed-size", truncated_stream),
            ("declared-crc", wrong_crc),
            ("declared-uncompressed-size", wrong_uncompressed_size),
        )
        with tempfile.TemporaryDirectory() as root_directory:
            for case_name, mutate in cases:
                with (
                    self.subTest(case=case_name),
                    tempfile.TemporaryDirectory(dir=root_directory) as directory,
                ):
                    wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
                    _write_wheel_with_member_count(wheel, 1)
                    wheel.write_bytes(mutate(bytearray(wheel.read_bytes())))
                    with patch(
                        "tests.verify_distribution_isolation.zipfile.ZipFile",
                        side_effect=AssertionError("invalid deflate payload reached ZipFile"),
                    ):
                        with self.assertRaises(IsolationError) as context:
                            verify_wheel(wheel)
                    message = str(context.exception)
                    self.assertIn("physical wheel payload", message)
                    self.assertNotIn(private_junk.decode(), message)
                    self.assertLess(len(message), 256)

    def test_physical_wheel_rejects_stored_payload_crc_before_zipfile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr(
                    "weightclass/delegation_qualifications.json",
                    '{"records":[],"registry_schema_version":1,'
                    '"suite_revision":"delegation-conformance-v2"}',
                )
            raw = bytearray(wheel.read_bytes())
            (
                _eocd_offset,
                _central_offset,
                _local_offset,
                payload_offset,
                compressed_size,
                _uncompressed_size,
            ) = _first_zip_member_layout(raw)
            if compressed_size == 0:
                raise AssertionError("stored test payload is unexpectedly empty")
            raw[payload_offset] ^= 1
            wheel.write_bytes(raw)
            with patch(
                "tests.verify_distribution_isolation.zipfile.ZipFile",
                side_effect=AssertionError("invalid stored payload reached ZipFile"),
            ):
                with self.assertRaisesRegex(IsolationError, "physical wheel payload"):
                    verify_wheel(wheel)

    def test_wheel_snapshot_must_match_expected_fingerprint_before_zipfile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
            _write_wheel_with_member_count(wheel, 1)
            expected = _fingerprint_artifact(wheel)
            replacement = Path(directory) / "replacement.whl"
            _write_wheel_with_member_count(replacement, 2)
            replacement.replace(wheel)
            with patch(
                "tests.verify_distribution_isolation.zipfile.ZipFile",
                side_effect=AssertionError("mismatched wheel reached ZipFile"),
            ):
                with self.assertRaisesRegex(IsolationError, "reviewed fingerprint"):
                    verify_wheel(wheel, expected_fingerprint=expected)

    def test_wheel_zipfile_uses_private_snapshot_after_path_swap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
            _write_wheel_with_member_count(wheel, 1)
            replacement = Path(directory) / "replacement.whl"
            replacement.write_bytes(b"replacement must never be parsed")
            real_zip_file = cast(Callable[..., zipfile.ZipFile], zipfile.ZipFile)

            def swap_before_zipfile(
                file: object,
                *args: object,
                **kwargs: object,
            ) -> zipfile.ZipFile:
                replacement.replace(wheel)
                if isinstance(file, (str, os.PathLike)):
                    raise AssertionError("ZipFile reopened the wheel path")
                return real_zip_file(file, *args, **kwargs)

            with patch(
                "tests.verify_distribution_isolation.zipfile.ZipFile",
                side_effect=swap_before_zipfile,
            ):
                verify_wheel(wheel)
            self.assertEqual(wheel.read_bytes(), b"replacement must never be parsed")

    def test_sdist_snapshot_must_match_expected_fingerprint_before_gzip_or_tar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sdist = _write_sdist_fixture(directory)
            expected = _fingerprint_artifact(sdist)
            replacement_directory = Path(directory) / "replacement"
            replacement_directory.mkdir()
            replacement = _write_sdist_fixture(str(replacement_directory))
            replacement.replace(sdist)
            with patch(
                "tests.verify_distribution_isolation.gzip.GzipFile",
                side_effect=AssertionError("mismatched sdist reached gzip"),
            ):
                with self.assertRaisesRegex(IsolationError, "reviewed fingerprint"):
                    verify_sdist(sdist, expected_fingerprint=expected)

    def test_sdist_gzip_uses_private_snapshot_after_path_swap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sdist = _write_sdist_fixture(directory)
            replacement = Path(directory) / "replacement.tar.gz"
            replacement.write_bytes(b"replacement must never be parsed")
            real_gzip_file = cast(Callable[..., gzip.GzipFile], gzip.GzipFile)

            def swap_before_gzip(*args: object, **kwargs: object) -> gzip.GzipFile:
                replacement.replace(sdist)
                return real_gzip_file(*args, **kwargs)

            with patch(
                "tests.verify_distribution_isolation.gzip.GzipFile",
                side_effect=swap_before_gzip,
            ):
                verify_sdist(sdist)
            self.assertEqual(sdist.read_bytes(), b"replacement must never be parsed")

    def test_physical_sdist_extensions_cannot_bypass_archive_caps(self) -> None:
        oversized_size = 9_437_201
        pax_global_record = _pax_record("comment", "bounded")
        oversized_local_pax = _pax_record_with_size(
            "comment",
            MAX_SDIST_EXTENSION_BYTES + 1,
        )
        gnu_name = b"weightclass-0/tests/physical-gnu-longname.txt\0"
        cases = (
            (
                "oversized-pax-global",
                _tar_physical_record(
                    "pax_global_header",
                    pax_global_record + b"\0" * (oversized_size - len(pax_global_record)),
                    tarfile.XGLTYPE,
                ),
            ),
            (
                "repeated-pax-global",
                _tar_physical_record("pax_global_header", b"", tarfile.XGLTYPE) * 4_097,
            ),
            (
                "oversized-pax-extended",
                _tar_physical_record(
                    "pax_extended_header",
                    oversized_local_pax,
                    tarfile.XHDTYPE,
                )
                + _tar_physical_record("placeholder", b"", tarfile.REGTYPE),
            ),
            (
                "oversized-gnu-longname",
                _tar_physical_record(
                    "././@LongLink",
                    gnu_name + b"\0" * (oversized_size - len(gnu_name)),
                    tarfile.GNUTYPE_LONGNAME,
                )
                + _tar_physical_record("placeholder", b"", tarfile.REGTYPE),
            ),
        )
        with tempfile.TemporaryDirectory() as root_directory:
            for case_name, records in cases:
                with (
                    self.subTest(case=case_name),
                    tempfile.TemporaryDirectory(dir=root_directory) as directory,
                ):
                    source, wheel, sdist = _write_distribution_fixture(directory)
                    _append_physical_tar_records(sdist, records)
                    result = _run_distribution_verifier(source, wheel.parent)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("physical sdist", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertNotIn("physical-pax-extension", result.stderr)
                    self.assertNotIn("physical-gnu-longname", result.stderr)
                    self.assertLess(len(result.stderr), 512)

    def test_oversized_json_integer_failures_are_bounded_and_value_free(self) -> None:
        private_integer = "7" * 5_000
        with tempfile.TemporaryDirectory() as root_directory:
            for location in ("source-registry", "wheel-member"):
                with (
                    self.subTest(location=location),
                    tempfile.TemporaryDirectory(dir=root_directory) as directory,
                ):
                    source, wheel, _sdist = _write_distribution_fixture(directory)
                    if location == "source-registry":
                        registry = source / "src/weightclass/delegation_qualifications.json"
                        registry.write_text(
                            '{"records":[' + private_integer + '],"registry_schema_version":1,'
                            '"suite_revision":"delegation-conformance-v2"}',
                            encoding="utf-8",
                        )
                    else:
                        with zipfile.ZipFile(wheel, "a") as archive:
                            archive.writestr(
                                "weightclass/private-integer.json",
                                '{"private":' + private_integer + "}",
                            )
                    result = _run_distribution_verifier(source, wheel.parent)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("distribution isolation failed", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertNotIn(private_integer, result.stderr)
                    self.assertLess(len(result.stderr), 512)

    def test_deeply_nested_json_failures_are_bounded_and_value_free(self) -> None:
        private_nested_value = "[" * 2_000 + "0" + "]" * 2_000
        candidate = _candidate_like_record()
        candidate["result_matrix"] = "private-nested-value"
        candidate_json = json.dumps(candidate, separators=(",", ":")).replace(
            '"private-nested-value"',
            private_nested_value,
        )
        with tempfile.TemporaryDirectory() as root_directory:
            for location in ("source-registry", "wheel-member"):
                with (
                    self.subTest(location=location),
                    tempfile.TemporaryDirectory(dir=root_directory) as directory,
                ):
                    source, wheel, _sdist = _write_distribution_fixture(directory)
                    if location == "source-registry":
                        registry = source / "src/weightclass/delegation_qualifications.json"
                        registry.write_text(
                            '{"records":' + private_nested_value + ',"registry_schema_version":1,'
                            '"suite_revision":"delegation-conformance-v2"}',
                            encoding="utf-8",
                        )
                    else:
                        with zipfile.ZipFile(wheel, "a") as archive:
                            archive.writestr("weightclass/private-nested.json", candidate_json)
                    result = _run_distribution_verifier(source, wheel.parent)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("distribution isolation failed", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertNotIn(private_nested_value, result.stderr)
                    self.assertLess(len(result.stderr), 512)

    def test_physical_sdist_rejects_unsafe_extensions(self) -> None:
        target = _tar_physical_record("placeholder", b"", tarfile.REGTYPE)
        cases = (
            (
                "pax-size",
                _tar_physical_record(
                    "pax_extended_header",
                    _pax_record("size", "0"),
                    tarfile.XHDTYPE,
                )
                + target,
            ),
            (
                "pax-gnu-sparse",
                _tar_physical_record(
                    "pax_extended_header",
                    _pax_record("GNU.sparse.map", "0,0"),
                    tarfile.XHDTYPE,
                )
                + target,
            ),
            (
                "old-gnu-sparse",
                _tar_physical_record("sparse", b"", tarfile.GNUTYPE_SPARSE),
            ),
            (
                "pax-length-overflow",
                _tar_physical_record(
                    "pax_extended_header",
                    b"9" * 5_000 + b" path=safe\n",
                    tarfile.XHDTYPE,
                )
                + target,
            ),
        )
        with tempfile.TemporaryDirectory() as root_directory:
            for case_name, records in cases:
                with (
                    self.subTest(case=case_name),
                    tempfile.TemporaryDirectory(dir=root_directory) as directory,
                ):
                    source, wheel, sdist = _write_distribution_fixture(directory)
                    _append_physical_tar_records(sdist, records)
                    result = _run_distribution_verifier(source, wheel.parent)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("physical sdist", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertLess(len(result.stderr), 512)

    def test_physical_sdist_rejects_noncanonical_or_truncated_streams(self) -> None:
        with tempfile.TemporaryDirectory() as root_directory:
            for case_name in (
                "bad-checksum",
                "base-256-checksum",
                "base-256-size",
                "missing-end-markers",
                "truncated-payload",
            ):
                with (
                    self.subTest(case=case_name),
                    tempfile.TemporaryDirectory(dir=root_directory) as directory,
                ):
                    source, wheel, sdist = _write_distribution_fixture(directory)
                    raw = gzip.decompress(sdist.read_bytes())
                    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
                        members = archive.getmembers()
                    last = members[-1]
                    body_end = last.offset_data + ((last.size + 511) // 512) * 512
                    if case_name == "bad-checksum":
                        header = bytearray(raw[:512])
                        header[0] ^= 1
                        modified = bytes(header) + raw[512:]
                    elif case_name == "base-256-checksum":
                        header = bytearray(raw[:512])
                        header[148:156] = b"        "
                        checksum = sum(header)
                        header[148:156] = b"\x80" + checksum.to_bytes(7, "big")
                        modified = bytes(header) + raw[512:]
                    elif case_name == "base-256-size":
                        header = bytearray(raw[:512])
                        header[124:136] = b"\x80" + b"\0" * 11
                        _replace_tar_checksum(header)
                        modified = bytes(header) + raw[512:]
                    elif case_name == "missing-end-markers":
                        modified = raw[:body_end]
                    else:
                        member = tarfile.TarInfo("weightclass-0/tests/truncated.txt")
                        member.size = 1
                        modified = raw[:body_end] + member.tobuf(format=tarfile.GNU_FORMAT)
                    sdist.write_bytes(gzip.compress(modified, mtime=0))
                    result = _run_distribution_verifier(source, wheel.parent)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("physical sdist", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertLess(len(result.stderr), 512)

    def test_physical_sdist_rejects_nonzero_padding_and_partial_zero_tail(self) -> None:
        with tempfile.TemporaryDirectory() as root_directory:
            for case_name in ("nonzero-padding", "partial-zero-tail"):
                with (
                    self.subTest(case=case_name),
                    tempfile.TemporaryDirectory(dir=root_directory) as directory,
                ):
                    sdist = _write_sdist_fixture(directory)
                    raw = bytearray(gzip.decompress(sdist.read_bytes()))
                    if case_name == "nonzero-padding":
                        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
                            member = next(
                                item
                                for item in archive
                                if item.isfile() and item.size % tarfile.BLOCKSIZE
                            )
                        raw[member.offset_data + member.size] = 1
                    else:
                        raw.extend(b"\0")
                    sdist.write_bytes(gzip.compress(bytes(raw), mtime=0))
                    with self.assertRaisesRegex(IsolationError, "physical sdist"):
                        _verify_physical_sdist(sdist)

    def test_physical_sdist_rejects_unsupported_types_and_local_pax_sequences(self) -> None:
        pax = _pax_record("path", "weightclass-0/tests/bounded-pax.txt")
        cases = (
            ("global-pax", _tar_physical_record("global", pax, tarfile.XGLTYPE)),
            ("solaris-pax", _tar_physical_record("solaris", pax, tarfile.SOLARIS_XHDTYPE)),
            (
                "gnu-longname",
                _tar_physical_record("././@LongLink", b"bounded\0", tarfile.GNUTYPE_LONGNAME),
            ),
            (
                "gnu-longlink",
                _tar_physical_record("././@LongLink", b"bounded\0", tarfile.GNUTYPE_LONGLINK),
            ),
            ("special", _tar_physical_record("fifo", b"", tarfile.FIFOTYPE)),
            ("unknown", _tar_physical_record("unknown", b"", b"Z")),
            ("dangling-local-pax", _tar_physical_record("local", pax, tarfile.XHDTYPE)),
            (
                "consecutive-local-pax",
                _tar_physical_record("local-one", pax, tarfile.XHDTYPE)
                + _tar_physical_record("local-two", pax, tarfile.XHDTYPE)
                + _tar_physical_record("target", b"", tarfile.REGTYPE),
            ),
        )
        with tempfile.TemporaryDirectory() as root_directory:
            for case_name, records in cases:
                with (
                    self.subTest(case=case_name),
                    tempfile.TemporaryDirectory(dir=root_directory) as directory,
                ):
                    sdist = Path(directory) / "physical-types.tar.gz"
                    sdist.write_bytes(gzip.compress(records + b"\0" * 1_024, mtime=0))
                    with self.assertRaisesRegex(IsolationError, "physical sdist"):
                        _verify_physical_sdist(sdist)

    def test_sdist_parse_and_extract_use_the_preflighted_snapshot_after_path_swap(self) -> None:
        for verifier in (verify_sdist, run_extracted_sdist_tests):
            with (
                self.subTest(verifier=verifier.__name__),
                tempfile.TemporaryDirectory() as directory,
            ):
                sdist = _write_sdist_fixture(
                    directory,
                    extra_files=(
                        (
                            "tests/test_distribution_isolation.py",
                            "import unittest\n\n"
                            "class SnapshotTests(unittest.TestCase):\n"
                            "    def test_snapshot(self):\n"
                            "        pass\n",
                        ),
                    ),
                )
                replacement = Path(directory) / "replacement.tar.gz"
                replacement.write_bytes(b"replacement must never be parsed")
                real_tar_open = cast(Callable[..., tarfile.TarFile], tarfile.open)
                swapped = False

                def swap_before_parse(
                    *args: object,
                    replacement_path: Path = replacement,
                    sdist_path: Path = sdist,
                    tar_open: Callable[..., tarfile.TarFile] = real_tar_open,
                    **kwargs: object,
                ) -> tarfile.TarFile:
                    nonlocal swapped
                    if not swapped:
                        replacement_path.replace(sdist_path)
                        swapped = True
                    return tar_open(*args, **kwargs)

                with patch(
                    "tests.verify_distribution_isolation.tarfile.open",
                    side_effect=swap_before_parse,
                ):
                    verifier(sdist)
                self.assertTrue(swapped)
                self.assertEqual(sdist.read_bytes(), b"replacement must never be parsed")

    def test_physical_sdist_turns_deflate_errors_into_isolation_errors(self) -> None:
        invalid_deflate = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff\x06" + b"\0" * 8
        with tempfile.TemporaryDirectory() as directory:
            sdist = Path(directory) / "invalid-deflate.tar.gz"
            sdist.write_bytes(invalid_deflate)
            with self.assertRaisesRegex(IsolationError, "physical sdist"):
                _verify_physical_sdist(sdist)

    def test_physical_sdist_accepts_bounded_pax_extension(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source, wheel, sdist = _write_distribution_fixture(directory)
            records = _tar_physical_record(
                "pax_extended_header",
                _pax_record_with_size("comment", MAX_SDIST_EXTENSION_BYTES),
                tarfile.XHDTYPE,
            ) + _tar_physical_record(
                "weightclass-0/tests/bounded-pax.txt",
                b"",
                tarfile.REGTYPE,
            )
            _append_physical_tar_records(sdist, records)
            result = _run_distribution_verifier(source, wheel.parent)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_physical_sdist_local_pax_cap_is_the_only_rejection_reason(self) -> None:
        oversized_local_pax = _pax_record_with_size(
            "comment",
            MAX_SDIST_EXTENSION_BYTES + 1,
        )
        records = _tar_physical_record(
            "pax_extended_header",
            oversized_local_pax,
            tarfile.XHDTYPE,
        ) + _tar_physical_record("placeholder", b"", tarfile.REGTYPE)
        with tempfile.TemporaryDirectory() as directory:
            sdist = Path(directory) / "local-pax-cap.tar.gz"
            sdist.write_bytes(gzip.compress(records + b"\0" * 1_024, mtime=0))
            with self.assertRaisesRegex(IsolationError, "member size exceeds"):
                _verify_physical_sdist(sdist)
            with patch(
                "tests.verify_distribution_isolation.MAX_SDIST_EXTENSION_BYTES",
                MAX_SDIST_EXTENSION_BYTES + 1,
            ):
                _verify_physical_sdist(sdist)

    def test_physical_sdist_aggregate_includes_pax_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sdist = Path(directory) / "pax-aggregate.tar.gz"
            pax_record = _tar_physical_record(
                "pax_extended_header",
                _pax_record("x", ""),
                tarfile.XHDTYPE,
            )
            target = _tar_physical_record("target", b"", tarfile.REGTYPE)
            raw = (pax_record + target) * 3 + b"\0" * 1_024
            sdist.write_bytes(gzip.compress(raw, mtime=0))
            with patch(
                "tests.verify_distribution_isolation.MAX_ARCHIVE_TOTAL_BYTES",
                10,
            ):
                with self.assertRaisesRegex(IsolationError, "physical sdist aggregate"):
                    _verify_physical_sdist(sdist)

    def test_distribution_workflows_make_isolation_the_final_exact_validation(self) -> None:
        root = Path(__file__).resolve().parents[1]
        if not (root / ".github/workflows").is_dir():
            self.skipTest("workflow sources are intentionally absent from the sdist")
        workflow_jobs = ((root / ".github/workflows/ci.yml", "quality"),)
        for workflow, job in workflow_jobs:
            with self.subTest(workflow=workflow.name):
                steps = _workflow_step_blocks(workflow, job)
                names = [_workflow_step_name(step) for step in steps]
                metadata_index = names.index("Verify distribution metadata")
                isolation_index = names.index("Verify qualification isolation in distributions")
                run_indexes = [
                    index for index, step in enumerate(steps) if "\n        run:" in step
                ]
                self.assertLess(metadata_index, isolation_index)
                self.assertEqual(isolation_index, run_indexes[-1])
                metadata_step = steps[metadata_index]
                self.assertIn(
                    "run: twine check --strict dist/*.whl dist/*.tar.gz",
                    metadata_step,
                )
                isolation_step = steps[isolation_index]
                self.assertIn("--source . --dist-dir dist", isolation_step)
                self.assertNotIn("--wheel", isolation_step)
                self.assertNotIn("--sdist", isolation_step)
                self.assertIn("--run-sdist-tests", isolation_step)

        release_steps = _workflow_step_blocks(root / ".github/workflows/release.yml", "verify")
        release_names = [_workflow_step_name(step) for step in release_steps]
        metadata_index = release_names.index("Verify distribution metadata")
        isolation_index = release_names.index("Verify distribution isolation before tests")
        upload_index = release_names.index("Upload unverified distributions")
        tests_index = release_names.index("Run extracted sdist tests against local artifacts")
        self.assertLess(metadata_index, isolation_index)
        self.assertLess(isolation_index, upload_index)
        self.assertLess(upload_index, tests_index)
        release_run_indexes = [
            index for index, step in enumerate(release_steps) if "\n        run:" in step
        ]
        self.assertEqual(tests_index, release_run_indexes[-1])
        self.assertNotIn("--run-sdist-tests", release_steps[isolation_index])
        self.assertIn("--run-sdist-tests", release_steps[tests_index])
        upload_step = release_steps[upload_index]
        self.assertIn("name: unverified-distributions", upload_step)
        self.assertIn(
            "          path: |\n            dist/*.whl\n            dist/*.tar.gz\n"
            "          if-no-files-found: error",
            upload_step,
        )
        self.assertNotIn("          path: dist/", upload_step)

        validate_steps = _workflow_step_blocks(root / ".github/workflows/release.yml", "validate")
        validate_names = [_workflow_step_name(step) for step in validate_steps]
        download_index = validate_names.index("Download unverified distributions")
        isolation_index = validate_names.index("Final distribution isolation verification")
        self.assertLess(download_index, isolation_index)
        self.assertEqual(isolation_index, len(validate_steps) - 1)
        self.assertNotIn("Install metadata verifier", validate_names)
        self.assertNotIn("Verify distribution metadata", validate_names)
        self.assertNotIn("Upload validated distributions", validate_names)
        validate_run_indexes = [
            index for index, step in enumerate(validate_steps) if "\n        run:" in step
        ]
        self.assertEqual(validate_run_indexes, [isolation_index])
        final_isolation_step = validate_steps[isolation_index]
        self.assertIn("--source . --dist-dir dist", final_isolation_step)
        self.assertNotIn("--run-sdist-tests", final_isolation_step)
        self.assertIn("name: unverified-distributions", validate_steps[download_index])
        release_workflow = root / ".github/workflows/release.yml"
        self.assertIn("    needs: verify", _workflow_job_block(release_workflow, "validate"))
        self.assertIn(
            "    needs: [validate, macos-routing-boundaries]",
            _workflow_job_block(release_workflow, "publish"),
        )
        publish_steps = _workflow_step_blocks(release_workflow, "publish")
        publish_download = publish_steps[
            [_workflow_step_name(step) for step in publish_steps].index("Download distributions")
        ]
        self.assertIn("name: unverified-distributions", publish_download)
        self.assertNotIn(
            "          name: distributions",
            release_workflow.read_text(encoding="utf-8"),
        )

    def test_distribution_directory_requires_exact_regular_artifact_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source, wheel, sdist = _write_distribution_fixture(directory)
            result = _run_distribution_verifier(source, wheel.parent)
            self.assertEqual(result.returncode, 0, result.stderr)

            extra = wheel.parent / "private-extra.txt"
            extra.write_text("not distributable\n", encoding="utf-8")
            result = _run_distribution_verifier(source, wheel.parent)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(extra.name, result.stderr)
            extra.unlink()

            target = Path(directory) / "outside-wheel.whl"
            wheel.replace(target)
            wheel.symlink_to(target)
            result = _run_distribution_verifier(source, sdist.parent)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(str(target), result.stderr)

    def test_distribution_directory_rechecks_inventory_and_hash_after_extracted_tests(self) -> None:
        with tempfile.TemporaryDirectory() as root_directory:
            for mutation in ("replace", "delete", "add", "hash"):
                with (
                    self.subTest(mutation=mutation),
                    tempfile.TemporaryDirectory(dir=root_directory) as directory,
                ):
                    base = Path(directory)
                    wheel = base / "dist/weightclass-0-py3-none-any.whl"
                    if mutation == "replace":
                        mutation_source = (
                            "import os\n"
                            "from pathlib import Path\n"
                            f"artifact = Path({str(wheel)!r})\n"
                            "replacement = artifact.with_name('replacement.tmp')\n"
                            "replacement.write_bytes(artifact.read_bytes())\n"
                            "os.replace(replacement, artifact)\n"
                        )
                    elif mutation == "delete":
                        mutation_source = (
                            f"from pathlib import Path\nPath({str(wheel)!r}).unlink()\n"
                        )
                    elif mutation == "add":
                        mutation_source = (
                            "from pathlib import Path\n"
                            f"Path({str(wheel.parent / 'private-added.txt')!r}).write_bytes(b'x')\n"
                        )
                    else:
                        mutation_source = (
                            "from pathlib import Path\n"
                            f"artifact = Path({str(wheel)!r})\n"
                            "artifact.write_bytes(artifact.read_bytes() + b'mutation')\n"
                        )

                    source, actual_wheel, _sdist = _write_distribution_fixture(
                        directory,
                        extracted_test=mutation_source,
                    )
                    self.assertEqual(actual_wheel, wheel)
                    result = _run_distribution_verifier(
                        source,
                        actual_wheel.parent,
                        run_sdist_tests=True,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(
                        "distribution artifacts changed after extracted sdist tests",
                        result.stderr,
                    )
                    self.assertLess(len(result.stderr), 2_048)

    def test_archives_reject_member_count_before_names_or_contents(self) -> None:
        wheel_members = [zipfile.ZipInfo(f"private-{index}") for index in range(4_097)]
        sdist_members = [tarfile.TarInfo(f"private-{index}") for index in range(4_097)]
        for verifier in (
            lambda: _wheel_members(
                cast(zipfile.ZipFile, _WheelMemberArchive(wheel_members)),
                Path("private.whl"),
            ),
            lambda: _safe_members(cast(tarfile.TarFile, _SdistMemberArchive(sdist_members))),
        ):
            with self.subTest(verifier=verifier):
                with patch(
                    "tests.verify_distribution_isolation.unicodedata.normalize",
                    side_effect=AssertionError("member name was normalized"),
                ):
                    with self.assertRaises(IsolationError) as context:
                        verifier()
                message = str(context.exception)
                self.assertLess(len(message), 256)
                self.assertNotIn("private-", message)

    def test_archives_reject_per_file_size_before_names_or_contents(self) -> None:
        wheel_member = zipfile.ZipInfo("private-wheel-value")
        wheel_member.file_size = 262_145
        sdist_member = tarfile.TarInfo("private-sdist-value")
        sdist_member.size = 8 * 1_024 * 1_024 + 1
        for verifier in (
            lambda: _wheel_members(
                cast(zipfile.ZipFile, _WheelMemberArchive([wheel_member])),
                Path("private.whl"),
            ),
            lambda: _safe_members(cast(tarfile.TarFile, _SdistMemberArchive([sdist_member]))),
        ):
            with self.subTest(verifier=verifier):
                with patch(
                    "tests.verify_distribution_isolation.unicodedata.normalize",
                    side_effect=AssertionError("member name was normalized"),
                ):
                    with self.assertRaises(IsolationError) as context:
                        verifier()
                message = str(context.exception)
                self.assertLess(len(message), 256)
                self.assertNotIn("private-", message)

    def test_archives_reject_aggregate_size_before_names_or_contents(self) -> None:
        wheel_members = [zipfile.ZipInfo(f"private-wheel-{index}") for index in range(257)]
        for wheel_metadata in wheel_members:
            wheel_metadata.file_size = 262_144
        sdist_members = [tarfile.TarInfo(f"private-sdist-{index}") for index in range(9)]
        for sdist_metadata in sdist_members:
            sdist_metadata.size = 8 * 1_024 * 1_024
        for verifier in (
            lambda: _wheel_members(
                cast(zipfile.ZipFile, _WheelMemberArchive(wheel_members)),
                Path("private.whl"),
            ),
            lambda: _safe_members(cast(tarfile.TarFile, _SdistMemberArchive(sdist_members))),
        ):
            with self.subTest(verifier=verifier):
                with patch(
                    "tests.verify_distribution_isolation.unicodedata.normalize",
                    side_effect=AssertionError("member name was normalized"),
                ):
                    with self.assertRaises(IsolationError) as context:
                        verifier()
                message = str(context.exception)
                self.assertLess(len(message), 256)
                self.assertNotIn("private-", message)

    def test_archives_reject_nonempty_directory_before_names_or_contents(self) -> None:
        wheel_member = zipfile.ZipInfo("private-wheel-directory/")
        wheel_member.file_size = 1
        sdist_member = tarfile.TarInfo("private-sdist-directory")
        sdist_member.type = tarfile.DIRTYPE
        sdist_member.size = 1
        for verifier in (
            lambda: _wheel_members(
                cast(zipfile.ZipFile, _WheelMemberArchive([wheel_member])),
                Path("private.whl"),
            ),
            lambda: _safe_members(cast(tarfile.TarFile, _SdistMemberArchive([sdist_member]))),
        ):
            with self.subTest(verifier=verifier):
                with patch(
                    "tests.verify_distribution_isolation.unicodedata.normalize",
                    side_effect=AssertionError("member name was normalized"),
                ):
                    with self.assertRaises(IsolationError) as context:
                        verifier()
                message = str(context.exception)
                self.assertLess(len(message), 256)
                self.assertNotIn("private-", message)

    def test_archive_metadata_caps_accept_the_exact_boundaries(self) -> None:
        wheel_count_members = [zipfile.ZipInfo(f"wheel-count-{index}") for index in range(4_096)]
        sdist_count_members = [tarfile.TarInfo(f"sdist-count-{index}") for index in range(4_096)]
        _wheel_members(
            cast(zipfile.ZipFile, _WheelMemberArchive(wheel_count_members)),
            Path("boundary.whl"),
        )
        _safe_members(cast(tarfile.TarFile, _SdistMemberArchive(sdist_count_members)))

        wheel_size_members = [zipfile.ZipInfo(f"wheel-size-{index}") for index in range(256)]
        for wheel_metadata in wheel_size_members:
            wheel_metadata.file_size = 256 * 1_024
        sdist_size_members = [tarfile.TarInfo(f"sdist-size-{index}") for index in range(8)]
        for sdist_metadata in sdist_size_members:
            sdist_metadata.size = 8 * 1_024 * 1_024
        _wheel_members(
            cast(zipfile.ZipFile, _WheelMemberArchive(wheel_size_members)),
            Path("boundary.whl"),
        )
        _safe_members(cast(tarfile.TarFile, _SdistMemberArchive(sdist_size_members)))

    def test_source_registry_must_remain_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "src/weightclass/delegation_qualifications.json"
            registry.parent.mkdir(parents=True)
            registry.write_text(
                '{"records":[],"registry_schema_version":1,'
                '"suite_revision":"delegation-conformance-v2"}',
                encoding="utf-8",
            )
            verify_source_registry(root)

            registry.write_text(
                json.dumps(
                    {
                        "records": [{"adapter_id": "forbidden"}],
                        "registry_schema_version": 1,
                        "suite_revision": "delegation-conformance-v2",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(IsolationError):
                verify_source_registry(root)

            registry.write_text(
                '{"records":[{"adapter_id":"forbidden"}],"records":[],'
                '"registry_schema_version":1,'
                '"suite_revision":"delegation-conformance-v2"}',
                encoding="utf-8",
            )
            with self.assertRaises(IsolationError):
                verify_source_registry(root)

    def test_source_registry_rejects_symlink_without_reading_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "private-target.json"
            target.write_text(
                '{"records":[],"registry_schema_version":1,'
                '"suite_revision":"delegation-conformance-v2"}',
                encoding="utf-8",
            )
            registry = root / "src/weightclass/delegation_qualifications.json"
            registry.parent.mkdir(parents=True)
            registry.symlink_to(target)
            with self.assertRaises(IsolationError) as context:
                verify_source_registry(root)
            message = str(context.exception)
            self.assertLess(len(message), 256)
            self.assertNotIn(target.name, message)
            self.assertNotIn(registry.name, message)

    def test_source_registry_rejects_fifo_promptly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source, wheel, _sdist = _write_distribution_fixture(directory)
            registry = source / "src/weightclass/delegation_qualifications.json"
            registry.unlink()
            os.mkfifo(registry)
            result = _run_distribution_verifier(source, wheel.parent, timeout=2.0)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Traceback", result.stderr)
            self.assertLess(len(result.stderr), 512)

    def test_source_registry_rejects_oversize_before_reading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "src/weightclass/delegation_qualifications.json"
            registry.parent.mkdir(parents=True)
            with registry.open("wb") as stream:
                stream.seek(MAX_ARCHIVE_TEXT_BYTES)
                stream.write(b"x")
            with patch(
                "tests.verify_distribution_isolation.os.read",
                side_effect=AssertionError("oversized source registry was read"),
            ):
                with self.assertRaisesRegex(IsolationError, "source registry exceeds"):
                    verify_source_registry(root)

    def test_source_registry_rejects_mutation_during_bounded_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "src/weightclass/delegation_qualifications.json"
            registry.parent.mkdir(parents=True)
            registry.write_text(
                '{"records":[],"registry_schema_version":1,'
                '"suite_revision":"delegation-conformance-v2"}',
                encoding="utf-8",
            )
            real_read = os.read
            mutated = False

            def mutate_after_read(descriptor: int, size: int) -> bytes:
                nonlocal mutated
                raw = real_read(descriptor, size)
                if raw and not mutated:
                    mutated = True
                    registry.write_text(
                        '{"records":[],"registry_schema_version":1,'
                        '"suite_revision":"delegation-conformance-x2"}',
                        encoding="utf-8",
                    )
                return raw

            with (
                patch(
                    "tests.verify_distribution_isolation._stat_identity",
                    return_value=(0, 0, 0, 0, 0, 0),
                ),
                patch(
                    "tests.verify_distribution_isolation.os.read",
                    side_effect=mutate_after_read,
                ),
            ):
                with self.assertRaisesRegex(IsolationError, "source registry changed"):
                    verify_source_registry(root)
            self.assertTrue(mutated)

    def test_malformed_distribution_cli_failures_are_redacted_without_tracebacks(self) -> None:
        with tempfile.TemporaryDirectory() as root_directory:
            for malformed in ("wheel", "sdist"):
                with (
                    self.subTest(malformed=malformed),
                    tempfile.TemporaryDirectory(dir=root_directory) as directory,
                ):
                    source, wheel, sdist = _write_distribution_fixture(directory)
                    artifact = wheel if malformed == "wheel" else sdist
                    artifact.write_bytes(b"private malformed distribution bytes")
                    result = _run_distribution_verifier(source, wheel.parent)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("distribution isolation failed", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertNotIn("private malformed", result.stderr)
                    self.assertLess(len(result.stderr), 512)

    def test_wheel_rejects_test_assets_and_candidate_like_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "weightclass/delegation_qualifications.json",
                    '{"records":[],"registry_schema_version":1,'
                    '"suite_revision":"delegation-conformance-v2"}',
                )
                archive.writestr("weightclass/module.py", "VALUE = 1\n")
            verify_wheel(wheel)

            with zipfile.ZipFile(wheel, "a") as archive:
                archive.writestr("tests/synthetic_probe_protocol.py", "wcp-selftest/v1")
            with self.assertRaises(IsolationError):
                verify_wheel(wheel)

    def test_wheel_rejects_unicode_normalization_collision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "weightclass/delegation_qualifications.json",
                    '{"records":[],"registry_schema_version":1,'
                    '"suite_revision":"delegation-conformance-v2"}',
                )
                archive.writestr("weightclass/café.py", "VALUE = 1\n")
                archive.writestr("weightclass/cafe\u0301.py", "VALUE = 2\n")

            with self.assertRaisesRegex(IsolationError, "noncanonical or duplicate archive member"):
                verify_wheel(wheel)

    def test_wheel_rejects_file_directory_install_path_collision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "weightclass/delegation_qualifications.json",
                    '{"records":[],"registry_schema_version":1,'
                    '"suite_revision":"delegation-conformance-v2"}',
                )
                archive.writestr("weightclass/pkg/", "")
                archive.writestr("weightclass/pkg", "VALUE = 1\n")

            with self.assertRaisesRegex(IsolationError, "noncanonical or duplicate archive member"):
                verify_wheel(wheel)

    def test_wheel_rejects_file_as_implicit_parent_in_either_order(self) -> None:
        collisions = (
            ("weightclass/pkg", "weightclass/pkg/module.py"),
            ("weightclass/Pkg", "weightclass/pkg/module.py"),
            ("weightclass/café", "weightclass/cafe\u0301/module.py"),
        )
        for parent_name, child_name in collisions:
            for parent_first in (True, False):
                with (
                    self.subTest(
                        parent_name=parent_name,
                        child_name=child_name,
                        parent_first=parent_first,
                    ),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
                    conflicting_members = (
                        (parent_name, "not a directory"),
                        (child_name, "VALUE = 1\n"),
                    )
                    ordered_members: tuple[tuple[str, str], ...] = conflicting_members
                    if not parent_first:
                        ordered_members = tuple(reversed(conflicting_members))
                    with zipfile.ZipFile(wheel, "w") as archive:
                        archive.writestr(
                            "weightclass/delegation_qualifications.json",
                            '{"records":[],"registry_schema_version":1,'
                            '"suite_revision":"delegation-conformance-v2"}',
                        )
                        for member_name, contents in ordered_members:
                            archive.writestr(member_name, contents)

                    with self.assertRaisesRegex(
                        IsolationError,
                        "noncanonical or duplicate archive member",
                    ):
                        verify_wheel(wheel)

    def test_wheel_accepts_explicit_directory_with_child(self) -> None:
        for directory_first in (True, False):
            with (
                self.subTest(directory_first=directory_first),
                tempfile.TemporaryDirectory() as directory,
            ):
                wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
                members: tuple[tuple[str, str], ...] = (
                    ("weightclass/", ""),
                    (
                        "weightclass/delegation_qualifications.json",
                        '{"records":[],"registry_schema_version":1,'
                        '"suite_revision":"delegation-conformance-v2"}',
                    ),
                    ("weightclass/module.py", "VALUE = 1\n"),
                )
                if not directory_first:
                    members = (members[1], members[2], members[0])
                with zipfile.ZipFile(wheel, "w") as archive:
                    for member_name, contents in members:
                        archive.writestr(member_name, contents)

                verify_wheel(wheel)

    def test_wheel_rejects_directory_spelling_aliases(self) -> None:
        alias_pairs = (
            ("weightclass/Pkg/", "weightclass/pkg/module.py"),
            ("weightclass/café/", "weightclass/cafe\u0301/module.py"),
            ("weightclass/Pkg/one.py", "weightclass/pkg/two.py"),
        )
        for first_name, second_name in alias_pairs:
            for reverse_order in (False, True):
                with (
                    self.subTest(
                        first_name=first_name,
                        second_name=second_name,
                        reverse_order=reverse_order,
                    ),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
                    aliases: tuple[tuple[str, str], ...] = (
                        (first_name, ""),
                        (second_name, "VALUE = 1\n"),
                    )
                    if reverse_order:
                        aliases = tuple(reversed(aliases))
                    with zipfile.ZipFile(wheel, "w") as archive:
                        archive.writestr(
                            "weightclass/delegation_qualifications.json",
                            '{"records":[],"registry_schema_version":1,'
                            '"suite_revision":"delegation-conformance-v2"}',
                        )
                        for member_name, contents in aliases:
                            archive.writestr(member_name, contents)

                    with self.assertRaisesRegex(
                        IsolationError,
                        "noncanonical or duplicate archive member",
                    ):
                        verify_wheel(wheel)

    def test_sdist_rejects_file_as_implicit_parent_in_either_order(self) -> None:
        collisions = (
            ("weightclass-0/pkg", "weightclass-0/pkg/module.py"),
            ("weightclass-0/Pkg", "weightclass-0/pkg/module.py"),
            ("weightclass-0/café", "weightclass-0/cafe\u0301/module.py"),
        )
        for parent_name, child_name in collisions:
            for parent_first in (True, False):
                with (
                    self.subTest(
                        parent_name=parent_name,
                        child_name=child_name,
                        parent_first=parent_first,
                    ),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    conflicting_members = (parent_name, child_name)
                    ordered_members: tuple[str, ...] = conflicting_members
                    if not parent_first:
                        ordered_members = tuple(reversed(conflicting_members))
                    sdist = _write_sdist_fixture(directory, ordered_members)

                    with self.assertRaisesRegex(IsolationError, "unsafe sdist member"):
                        verify_sdist(sdist)

    def test_sdist_rejects_directory_spelling_aliases(self) -> None:
        alias_pairs = (
            ("weightclass-0/Pkg/one.py", "weightclass-0/pkg/two.py"),
            ("weightclass-0/café/one.py", "weightclass-0/cafe\u0301/two.py"),
        )
        for first_name, second_name in alias_pairs:
            for reverse_order in (False, True):
                with (
                    self.subTest(
                        first_name=first_name,
                        second_name=second_name,
                        reverse_order=reverse_order,
                    ),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    aliases: tuple[str, ...] = (first_name, second_name)
                    if reverse_order:
                        aliases = tuple(reversed(aliases))
                    sdist = _write_sdist_fixture(directory, aliases)

                    with self.assertRaisesRegex(IsolationError, "unsafe sdist member"):
                        verify_sdist(sdist)

    def test_archives_reject_excessive_member_depth(self) -> None:
        deep_suffix = "/".join(["part"] * 257) + "/module.py"
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "weightclass/delegation_qualifications.json",
                    '{"records":[],"registry_schema_version":1,'
                    '"suite_revision":"delegation-conformance-v2"}',
                )
                archive.writestr(f"weightclass/{deep_suffix}", "VALUE = 1\n")
            with self.assertRaisesRegex(
                IsolationError,
                "noncanonical or duplicate archive member",
            ):
                verify_wheel(wheel)

            sdist = _write_sdist_fixture(
                directory,
                (f"weightclass-0/{deep_suffix}",),
            )
            with self.assertRaisesRegex(IsolationError, "unsafe sdist member"):
                verify_sdist(sdist)

    def test_archives_reject_excessive_member_name(self) -> None:
        long_suffix = "x" * MAX_ARCHIVE_MEMBER_NAME_BYTES
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "weightclass/delegation_qualifications.json",
                    '{"records":[],"registry_schema_version":1,'
                    '"suite_revision":"delegation-conformance-v2"}',
                )
                archive.writestr(f"weightclass/{long_suffix}", "VALUE = 1\n")
            with self.assertRaisesRegex(
                IsolationError,
                ARCHIVE_MEMBER_NAME_LIMIT_ERROR,
            ):
                verify_wheel(wheel)

            sdist = _write_sdist_fixture(
                directory,
                (f"weightclass-0/{long_suffix}",),
            )
            with self.assertRaisesRegex(IsolationError, ARCHIVE_MEMBER_NAME_LIMIT_ERROR):
                verify_sdist(sdist)

    def test_archives_bound_overlong_name_diagnostics_before_normalization(self) -> None:
        long_suffix = "x" * MAX_ARCHIVE_MEMBER_NAME_BYTES
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "weightclass/delegation_qualifications.json",
                    '{"records":[],"registry_schema_version":1,'
                    '"suite_revision":"delegation-conformance-v2"}',
                )
                archive.writestr(f"weightclass/{long_suffix}", "VALUE = 1\n")

            sdist = _write_sdist_fixture(
                directory,
                (f"weightclass-0/{long_suffix}",),
            )
            for verifier, artifact in (
                (verify_wheel, wheel),
                (verify_sdist, sdist),
            ):
                with self.subTest(artifact=artifact.name):
                    real_normalize = cast(Callable[[str, str], str], unicodedata.normalize)

                    def guarded_normalize(
                        form: str,
                        value: str,
                        *,
                        normalize: Callable[[str, str], str] = real_normalize,
                    ) -> str:
                        if len(value.encode("utf-8")) > MAX_ARCHIVE_MEMBER_NAME_BYTES:
                            raise AssertionError("overlong member was normalized")
                        return normalize(form, value)

                    with patch(
                        "tests.verify_distribution_isolation.unicodedata.normalize",
                        side_effect=guarded_normalize,
                    ):
                        with self.assertRaises(IsolationError) as context:
                            verifier(artifact)
                    message = str(context.exception)
                    self.assertLess(len(message), 256)
                    self.assertNotIn(long_suffix, message)

    def test_wheel_uses_exact_tests_path_detection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "weightclass/delegation_qualifications.json",
                    '{"records":[],"registry_schema_version":1,'
                    '"suite_revision":"delegation-conformance-v2"}',
                )
                archive.writestr("weightclass/contests/example.py", "VALUE = 1\n")

            verify_wheel(wheel)

            for member_name in (
                "tests/example.py",
                "weightclass/tests/example.py",
                "weightclass/synthetic_probe_report.py",
                "weightclass/delegation_claim_map_v3.json",
            ):
                with zipfile.ZipFile(wheel, "w") as archive:
                    archive.writestr(
                        "weightclass/delegation_qualifications.json",
                        '{"records":[],"registry_schema_version":1,'
                        '"suite_revision":"delegation-conformance-v2"}',
                    )
                    archive.writestr(member_name, "test-only\n")
                with self.assertRaises(IsolationError):
                    verify_wheel(wheel)

    def test_wheel_rejects_nonempty_or_candidate_compatible_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "weightclass/delegation_qualifications.json",
                    json.dumps(
                        {
                            "records": [],
                            "registry_schema_version": 1,
                            "suite_revision": "delegation-conformance-v2",
                            "runtime_build_id": "candidate-shaped",
                        }
                    ),
                )
            with self.assertRaises(IsolationError):
                verify_wheel(wheel)

    def test_wheel_rejects_candidate_record_at_non_registry_package_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "weightclass/delegation_qualifications.json",
                    '{"records":[],"registry_schema_version":1,'
                    '"suite_revision":"delegation-conformance-v2"}',
                )
                archive.writestr(
                    "weightclass/_reviewed_candidate.json",
                    json.dumps(_candidate_like_record()),
                )

            with self.assertRaises(IsolationError):
                verify_wheel(wheel)

    def test_wheel_rejects_populated_data_purelib_registry_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "weightclass/delegation_qualifications.json",
                    '{"records":[],"registry_schema_version":1,'
                    '"suite_revision":"delegation-conformance-v2"}',
                )
                archive.writestr(
                    "weightclass-0.data/purelib/weightclass/delegation_qualifications.json",
                    json.dumps(
                        {
                            "records": [{"adapter_id": "decoy"}],
                            "registry_schema_version": 1,
                            "suite_revision": "delegation-conformance-v2",
                        }
                    ),
                )

            with self.assertRaises(IsolationError):
                verify_wheel(wheel)

    def test_wheel_rejects_case_variant_registry_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "weightclass/delegation_qualifications.json",
                    '{"records":[],"registry_schema_version":1,'
                    '"suite_revision":"delegation-conformance-v2"}',
                )
                archive.writestr(
                    "WeightClass/delegation_qualifications.json",
                    json.dumps(
                        {
                            "records": [{"adapter_id": "case-decoy"}],
                            "registry_schema_version": 1,
                            "suite_revision": "delegation-conformance-v2",
                        }
                    ),
                )

            with self.assertRaises(IsolationError):
                verify_wheel(wheel)

    def test_wheel_rejects_candidate_hidden_by_duplicate_member_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "weightclass/delegation_qualifications.json",
                    '{"records":[],"registry_schema_version":1,'
                    '"suite_revision":"delegation-conformance-v2"}',
                )
                archive.writestr(
                    "weightclass/_reviewed_candidate.json",
                    json.dumps(_candidate_like_record()),
                )
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    archive.writestr("weightclass/_reviewed_candidate.json", "not JSON")

            with self.assertRaises(IsolationError):
                verify_wheel(wheel)

    def test_wheel_rejects_noncanonical_member_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "weightclass/delegation_qualifications.json",
                    '{"records":[],"registry_schema_version":1,'
                    '"suite_revision":"delegation-conformance-v2"}',
                )
                archive.writestr("weightclass/./module.py", "VALUE = 1\n")

            with self.assertRaises(IsolationError):
                verify_wheel(wheel)

    def test_wheel_rejects_candidate_at_extensionless_package_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "weightclass/delegation_qualifications.json",
                    '{"records":[],"registry_schema_version":1,'
                    '"suite_revision":"delegation-conformance-v2"}',
                )
                archive.writestr(
                    "weightclass/_reviewed_candidate",
                    json.dumps(_candidate_like_record()),
                )

            with self.assertRaises(IsolationError):
                verify_wheel(wheel)

    def test_wheel_bounds_production_registry_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
            oversized_registry = b"{" + b" " * MAX_ARCHIVE_TEXT_BYTES + b"}"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("weightclass/delegation_qualifications.json", oversized_registry)

            with patch.object(zipfile.ZipFile, "read", side_effect=AssertionError):
                with self.assertRaises(IsolationError):
                    verify_wheel(wheel)

    def test_wheel_rejects_evidence_document_at_non_registry_package_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "weightclass/delegation_qualifications.json",
                    '{"records":[],"registry_schema_version":1,'
                    '"suite_revision":"delegation-conformance-v2"}',
                )
                archive.writestr(
                    "weightclass/_conformance_evidence.json",
                    json.dumps(_evidence_like_document()),
                )

            with self.assertRaises(IsolationError):
                verify_wheel(wheel)

    def test_wheel_allows_unrelated_text_that_mentions_schema_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "weightclass/delegation_qualifications.json",
                    '{"records":[],"registry_schema_version":1,'
                    '"suite_revision":"delegation-conformance-v2"}',
                )
                archive.writestr(
                    "weightclass/README.md",
                    "The record_schema_version and evidence_schema_version fields "
                    "are reserved for test-owned qualification documents.\n",
                )

            verify_wheel(wheel)

    def test_wheel_rejects_duplicate_fields_in_qualification_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "weightclass/delegation_qualifications.json",
                    '{"records":[],"registry_schema_version":1,'
                    '"suite_revision":"delegation-conformance-v2"}',
                )
                archive.writestr(
                    "weightclass/_candidate.json",
                    '{"record_schema_version":1,"record_schema_version":1}',
                )

            with self.assertRaises(IsolationError):
                verify_wheel(wheel)

    def test_wheel_rejects_decoy_registry_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "weightclass-0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "decoy/weightclass/delegation_qualifications.json",
                    '{"records":[],"registry_schema_version":1,'
                    '"suite_revision":"delegation-conformance-v2"}',
                )

            with self.assertRaises(IsolationError):
                verify_wheel(wheel)

    def test_sdist_requires_empty_registry_and_keeps_synthetic_assets_test_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sdist = Path(directory) / "weightclass-0.tar.gz"
            with tarfile.open(sdist, "w:gz") as archive:
                root = Path(directory) / "payload"
                registry = root / "weightclass-0/src/weightclass/delegation_qualifications.json"
                registry.parent.mkdir(parents=True)
                registry.write_text(
                    '{"records":[],"registry_schema_version":1,'
                    '"suite_revision":"delegation-conformance-v2"}',
                    encoding="utf-8",
                )
                for relative_path in (
                    "tests/synthetic_descendant_containment.py",
                    "tests/synthetic_probe_child.py",
                    "tests/synthetic_probe_protocol.py",
                    "tests/synthetic_probe_runner.py",
                    "tests/test_distribution_isolation.py",
                    "tests/test_synthetic_probe_protocol.py",
                    "tests/verify_distribution_isolation.py",
                ):
                    asset = root / "weightclass-0" / relative_path
                    asset.parent.mkdir(parents=True, exist_ok=True)
                    asset.write_text("test-only\n", encoding="utf-8")
                archive.add(root / "weightclass-0", arcname="weightclass-0")
            verify_sdist(sdist)

    def test_sdist_rejects_missing_synthetic_regression_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sdist = Path(directory) / "weightclass-0.tar.gz"
            with tarfile.open(sdist, "w:gz") as archive:
                root = Path(directory) / "payload"
                registry = root / "weightclass-0/src/weightclass/delegation_qualifications.json"
                registry.parent.mkdir(parents=True)
                registry.write_text(
                    '{"records":[],"registry_schema_version":1,'
                    '"suite_revision":"delegation-conformance-v2"}',
                    encoding="utf-8",
                )
                archive.add(root / "weightclass-0", arcname="weightclass-0")

            with self.assertRaises(IsolationError):
                verify_sdist(sdist)

    def test_sdist_rejects_required_assets_under_nested_decoy_tests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sdist = Path(directory) / "weightclass-0.tar.gz"
            root = Path(directory) / "payload/weightclass-0"
            registry = root / "src/weightclass/delegation_qualifications.json"
            registry.parent.mkdir(parents=True)
            registry.write_text(
                '{"records":[],"registry_schema_version":1,'
                '"suite_revision":"delegation-conformance-v2"}',
                encoding="utf-8",
            )
            for relative_path in (
                "tests/synthetic_descendant_containment.py",
                "tests/synthetic_probe_child.py",
                "tests/synthetic_probe_protocol.py",
                "tests/synthetic_probe_runner.py",
                "tests/test_distribution_isolation.py",
                "tests/test_synthetic_probe_protocol.py",
                "tests/verify_distribution_isolation.py",
            ):
                asset = root / "not" / relative_path
                asset.parent.mkdir(parents=True, exist_ok=True)
                asset.write_text("test-only\n", encoding="utf-8")
            with tarfile.open(sdist, "w:gz") as archive:
                archive.add(root, arcname="weightclass-0")

            with self.assertRaises(IsolationError):
                verify_sdist(sdist)

    def test_sdist_rejects_casefold_registry_alias_with_populated_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sdist = Path(directory) / "weightclass-0.tar.gz"
            root = Path(directory) / "payload/weightclass-0"
            registry = root / "src/weightclass/delegation_qualifications.json"
            registry.parent.mkdir(parents=True)
            registry.write_text(
                '{"records":[],"registry_schema_version":1,'
                '"suite_revision":"delegation-conformance-v2"}',
                encoding="utf-8",
            )
            for relative_path in (
                "tests/synthetic_descendant_containment.py",
                "tests/synthetic_probe_child.py",
                "tests/synthetic_probe_protocol.py",
                "tests/synthetic_probe_runner.py",
                "tests/test_distribution_isolation.py",
                "tests/test_synthetic_probe_protocol.py",
                "tests/verify_distribution_isolation.py",
            ):
                asset = root / relative_path
                asset.parent.mkdir(parents=True, exist_ok=True)
                asset.write_text("test-only\n", encoding="utf-8")
            with tarfile.open(sdist, "w:gz") as archive:
                archive.add(root, arcname="weightclass-0")
                alias_name = "weightclass-0/src/WeightClass/delegation_qualifications.json"
                alias_raw = json.dumps(_candidate_like_record()).encode("utf-8")
                alias = tarfile.TarInfo(alias_name)
                alias.size = len(alias_raw)
                archive.addfile(alias, io.BytesIO(alias_raw))

            with self.assertRaises(IsolationError):
                verify_sdist(sdist)

    def test_sdist_rejects_registry_suffix_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sdist = _write_sdist_fixture(
                directory,
                ("weightclass-0/decoy/weightclass/delegation_qualifications.json",),
            )

            with self.assertRaisesRegex(IsolationError, "expected exactly one production registry"):
                verify_sdist(sdist)

    def test_sdist_rejects_casefold_collision_in_required_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sdist = Path(directory) / "weightclass-0.tar.gz"
            root = Path(directory) / "payload/weightclass-0"
            registry = root / "src/weightclass/delegation_qualifications.json"
            registry.parent.mkdir(parents=True)
            registry.write_text(
                '{"records":[],"registry_schema_version":1,'
                '"suite_revision":"delegation-conformance-v2"}',
                encoding="utf-8",
            )
            for relative_path in (
                "tests/synthetic_descendant_containment.py",
                "tests/synthetic_probe_child.py",
                "tests/synthetic_probe_protocol.py",
                "tests/synthetic_probe_runner.py",
                "tests/test_distribution_isolation.py",
                "tests/test_synthetic_probe_protocol.py",
                "tests/verify_distribution_isolation.py",
            ):
                asset = root / relative_path
                asset.parent.mkdir(parents=True, exist_ok=True)
                asset.write_text("test-only\n", encoding="utf-8")
            with tarfile.open(sdist, "w:gz") as archive:
                archive.add(root, arcname="weightclass-0")
                alias_name = "weightclass-0/tests/Synthetic_Probe_Child.py"
                alias_raw = b"test-only\n"
                alias = tarfile.TarInfo(alias_name)
                alias.size = len(alias_raw)
                archive.addfile(alias, io.BytesIO(alias_raw))

            with self.assertRaises(IsolationError):
                verify_sdist(sdist)

    def test_sdist_rejects_unicode_normalization_collision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sdist = Path(directory) / "weightclass-0.tar.gz"
            root = Path(directory) / "payload/weightclass-0"
            registry = root / "src/weightclass/delegation_qualifications.json"
            registry.parent.mkdir(parents=True)
            registry.write_text(
                '{"records":[],"registry_schema_version":1,'
                '"suite_revision":"delegation-conformance-v2"}',
                encoding="utf-8",
            )
            for relative_path in (
                "tests/synthetic_descendant_containment.py",
                "tests/synthetic_probe_child.py",
                "tests/synthetic_probe_protocol.py",
                "tests/synthetic_probe_runner.py",
                "tests/test_distribution_isolation.py",
                "tests/test_synthetic_probe_protocol.py",
                "tests/verify_distribution_isolation.py",
            ):
                asset = root / relative_path
                asset.parent.mkdir(parents=True, exist_ok=True)
                asset.write_text("test-only\n", encoding="utf-8")
            with tarfile.open(sdist, "w:gz") as archive:
                archive.add(root, arcname="weightclass-0")
                for alias_name in (
                    "weightclass-0/tests/café.txt",
                    "weightclass-0/tests/cafe\u0301.txt",
                ):
                    alias_raw = b"test-only\n"
                    alias = tarfile.TarInfo(alias_name)
                    alias.size = len(alias_raw)
                    archive.addfile(alias, io.BytesIO(alias_raw))

            with self.assertRaises(IsolationError):
                verify_sdist(sdist)

    def test_sdist_uses_exact_tests_path_detection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sdist = _write_sdist_fixture(directory)
            root = Path(directory) / "payload/weightclass-0"
            contests = root / "weightclass/contests/example.py"
            contests.parent.mkdir(parents=True)
            contests.write_text("test-only\n", encoding="utf-8")
            with tarfile.open(sdist, "w:gz") as archive:
                archive.add(root, arcname="weightclass-0")
            verify_sdist(sdist)

            for member_name in (
                "weightclass-0/not/tests/example.py",
                "weightclass-0/weightclass/synthetic_probe_report.py",
                "weightclass-0/weightclass/delegation_claim_map_v3.json",
            ):
                sdist = _write_sdist_fixture(directory, (member_name,))
                with self.assertRaises(IsolationError):
                    verify_sdist(sdist)

    def test_sdist_rejects_case_variant_test_root(self) -> None:
        for root_name in ("Tests", "TESTS", "tEsTs"):
            with (
                self.subTest(root_name=root_name),
                tempfile.TemporaryDirectory() as directory,
            ):
                sdist = _write_sdist_fixture(directory, (f"weightclass-0/{root_name}/example.py",))
                with self.assertRaisesRegex(IsolationError, "unsafe sdist member"):
                    verify_sdist(sdist)

    def test_sdist_rejects_backslash_and_nul_member_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sdist = _write_sdist_fixture(
                directory, ("weightclass-0/weightclass\\synthetic_probe.py",)
            )
            with self.assertRaises(IsolationError):
                verify_sdist(sdist)

        member = tarfile.TarInfo("weightclass-0/weightclass/synthetic\x00probe.py")

        class ArchiveWithNulMember:
            def __iter__(self) -> Iterator[tarfile.TarInfo]:
                yield member

        with self.assertRaises(IsolationError):
            _safe_members(cast(tarfile.TarFile, ArchiveWithNulMember()))

    def test_sdist_rejects_special_files_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sdist = Path(directory) / "weightclass-0.tar.gz"
            root = Path(directory) / "payload/weightclass-0"
            registry = root / "src/weightclass/delegation_qualifications.json"
            registry.parent.mkdir(parents=True)
            registry.write_text(
                '{"records":[],"registry_schema_version":1,'
                '"suite_revision":"delegation-conformance-v2"}',
                encoding="utf-8",
            )
            for relative_path in (
                "tests/synthetic_descendant_containment.py",
                "tests/synthetic_probe_child.py",
                "tests/synthetic_probe_protocol.py",
                "tests/synthetic_probe_runner.py",
                "tests/test_distribution_isolation.py",
                "tests/test_synthetic_probe_protocol.py",
                "tests/verify_distribution_isolation.py",
            ):
                asset = root / relative_path
                asset.parent.mkdir(parents=True, exist_ok=True)
                asset.write_text("test-only\n", encoding="utf-8")
            with tarfile.open(sdist, "w:gz") as archive:
                archive.add(root, arcname="weightclass-0")
                fifo = tarfile.TarInfo("weightclass-0/tests/synthetic-probe-fifo")
                fifo.type = tarfile.FIFOTYPE
                archive.addfile(fifo)

            with self.assertRaises(IsolationError):
                verify_sdist(sdist)

    def test_extracted_sdist_tests_do_not_inherit_parent_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sdist = Path(directory) / "weightclass-0.tar.gz"
            root = Path(directory) / "payload/weightclass-0"
            smoke_test = root / "tests/test_environment_isolation.py"
            smoke_test.parent.mkdir(parents=True)
            smoke_test.write_text(
                "import os\n"
                "import shutil\n"
                "import sys\n"
                "import unittest\n"
                "from pathlib import Path\n"
                "\n"
                "class EnvironmentIsolationTests(unittest.TestCase):\n"
                "    def test_parent_sentinel_is_absent(self):\n"
                "        self.assertNotIn('WCP_FAKE_SENSITIVE_SENTINEL', os.environ)\n"
                "        python3 = shutil.which('python3')\n"
                "        self.assertIsNotNone(python3)\n"
                "        self.assertEqual(\n"
                "            Path(python3).resolve(), Path(sys.executable).resolve()\n"
                "        )\n",
                encoding="utf-8",
            )
            with tarfile.open(sdist, "w:gz") as archive:
                archive.add(root, arcname="weightclass-0")

            with patch.dict(
                os.environ, {"WCP_FAKE_SENSITIVE_SENTINEL": "opaque-test-value"}, clear=False
            ):
                with patch.object(type(os.environ), "copy", side_effect=AssertionError):
                    try:
                        run_extracted_sdist_tests(sdist)
                    except (AssertionError, subprocess.CalledProcessError):
                        self.fail("extracted-sdist test runner did not use its minimal environment")


if __name__ == "__main__":
    unittest.main()
