from __future__ import annotations

import json
import os
import subprocess
import tarfile
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch

from tests.verify_distribution_isolation import (
    MAX_ARCHIVE_TEXT_BYTES,
    IsolationError,
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
