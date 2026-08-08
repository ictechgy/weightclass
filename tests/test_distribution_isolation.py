from __future__ import annotations

import io
import json
import os
import subprocess
import tarfile
import tempfile
import unicodedata
import unittest
import warnings
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import cast
from unittest.mock import patch

from tests.verify_distribution_isolation import (
    ARCHIVE_MEMBER_NAME_LIMIT_ERROR,
    MAX_ARCHIVE_MEMBER_NAME_BYTES,
    MAX_ARCHIVE_TEXT_BYTES,
    IsolationError,
    _safe_members,
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


def _write_sdist_fixture(directory: str, extra_members: tuple[str, ...] = ()) -> Path:
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
        asset.write_text("test-only\n", encoding="utf-8")
    with tarfile.open(sdist, "w:gz") as archive:
        archive.add(root, arcname="weightclass-0")
        for member_name in extra_members:
            raw = b"test-only\n"
            member = tarfile.TarInfo(member_name)
            member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))
    return sdist


class DistributionIsolationTests(unittest.TestCase):
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
            def getmembers(self) -> list[tarfile.TarInfo]:
                return [member]

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
