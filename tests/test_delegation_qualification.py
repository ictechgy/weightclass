import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.test_delegation import _manifest, _policy
from weightclass.delegation_compile import compile_delegation_descriptor
from weightclass.delegation_qualification import (
    QualificationInvalidInputError,
    QualificationRegistry,
    QualificationUnsupportedError,
    QualifiedRuntimeUnavailableError,
    attach_qualification_requirement,
    build_qualification_candidate,
    load_qualification_registry,
    select_qualification_for_descriptor,
    verify_qualified_runtime,
)
from weightclass.delegation_schema import (
    current_platform_contract,
    load_delegation_manifest,
    load_delegation_policy,
)

SUITE_REVISION = "delegation-conformance-v2"
REQUIRED_SCENARIOS = (
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


def _evidence(
    vendor: str = "claude",
    runtime_build_id: str = "opaque-runtime-build",
    artifact_contents: bytes = b"qualified-runtime-v1\n",
) -> dict[str, object]:
    observations = [
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
    ]
    return {
        "evidence_schema_version": 2,
        "artifact_sha256": hashlib.sha256(artifact_contents).hexdigest(),
        "artifact_size_bytes": len(artifact_contents),
        "suite_revision": SUITE_REVISION,
        "runtime_build_id": runtime_build_id,
        "platform": {
            "os": current_platform_contract().os,
            "architecture": current_platform_contract().architecture,
        },
        "protocol_version": 1,
        "adapter_id": f"{vendor}-native-v1",
        "vendor_family": vendor,
        "result_matrix": observations,
        "scenario_results": [
            {"id": scenario_id, "passed": True} for scenario_id in REQUIRED_SCENARIOS
        ],
    }


def _write_executable(path: Path, contents: bytes = b"qualified-runtime-v1\n") -> None:
    path.write_bytes(contents)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _registry_value(record: dict[str, object]) -> dict[str, object]:
    return {
        "registry_schema_version": 1,
        "suite_revision": SUITE_REVISION,
        "records": [record],
    }


def _load_descriptor(directory: Path, runtime_path: Path) -> dict[str, object]:
    policy_path = directory / "policy.json"
    manifest_path = directory / "manifest.json"
    policy_path.write_text(json.dumps(_policy()), encoding="utf-8")
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
    return compile_delegation_descriptor(
        load_delegation_policy(policy_path),
        load_delegation_manifest(manifest_path),
        runtime_path=str(runtime_path),
        source_vendor="claude",
        tier="standard",
        target_platform=current_platform_contract(),
    )


class QualificationCandidateTests(unittest.TestCase):
    def test_cli_emits_candidate_without_reading_task_or_mutating_registry(self) -> None:
        """Breaks if candidate generation becomes a trust override or task-data path."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            evidence_path = directory / "evidence.json"
            _write_executable(runtime_path)
            evidence_path.write_text(json.dumps(_evidence()), encoding="utf-8")
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "delegate",
                    "qualification-candidate",
                    "--evidence",
                    str(evidence_path),
                    "--delegation-runtime",
                    str(runtime_path),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                return_code = process.wait(timeout=5)
                stdout, stderr = process.communicate(timeout=1)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()

        self.assertEqual(return_code, 0, stderr)
        candidate = json.loads(stdout)
        self.assertEqual(candidate["record_schema_version"], 1)
        self.assertEqual(stderr, "")

    def test_candidate_binds_exact_artifact_and_complete_task_free_evidence(self) -> None:
        """Breaks if a partial report or explicit task field can become a record."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            runtime_path = Path(temporary_directory) / "runtime"
            contents = b"qualified-runtime-v1\n"
            _write_executable(runtime_path, contents)

            candidate = build_qualification_candidate(_evidence(), runtime_path)

        self.assertEqual(candidate["artifact_size_bytes"], len(contents))
        self.assertEqual(candidate["artifact_sha256"], hashlib.sha256(contents).hexdigest())
        result_matrix = candidate["result_matrix"]
        scenario_results = candidate["scenario_results"]
        assert isinstance(result_matrix, list)
        assert isinstance(scenario_results, list)
        self.assertEqual(len(result_matrix), 54)
        self.assertEqual(len(scenario_results), len(REQUIRED_SCENARIOS))
        self.assertNotIn("runtime_path", candidate)
        self.assertNotIn("task", json.dumps(candidate))

    def test_candidate_preserves_manifest_compatible_opaque_build_id(self) -> None:
        """Breaks if qualification narrows an already valid opaque manifest label."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            runtime_path = Path(temporary_directory) / "runtime"
            _write_executable(runtime_path)

            candidate = build_qualification_candidate(
                _evidence(runtime_build_id="opaque runtime build"),
                runtime_path,
            )

        self.assertEqual(candidate["runtime_build_id"], "opaque runtime build")

    def test_candidate_rejects_incomplete_duplicate_or_failed_evidence(self) -> None:
        """Breaks if all required independent observations are not mandatory."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            runtime_path = Path(temporary_directory) / "runtime"
            _write_executable(runtime_path)
            incomplete = _evidence()
            incomplete_matrix = incomplete["result_matrix"]
            assert isinstance(incomplete_matrix, list)
            incomplete_matrix.pop()
            duplicate = _evidence()
            duplicate_matrix = duplicate["result_matrix"]
            assert isinstance(duplicate_matrix, list)
            duplicate_matrix[-1] = deepcopy(duplicate_matrix[0])
            failed = _evidence()
            failed_scenarios = failed["scenario_results"]
            assert isinstance(failed_scenarios, list)
            failed_scenario = failed_scenarios[0]
            assert isinstance(failed_scenario, dict)
            failed_scenario["passed"] = False
            task_bearing = _evidence()
            task_bearing["task"] = "must never be accepted"

            for evidence in (incomplete, duplicate, failed, task_bearing):
                with self.subTest(case=list(evidence)):
                    with self.assertRaises(QualificationInvalidInputError):
                        build_qualification_candidate(evidence, runtime_path)


class QualificationRegistryTests(unittest.TestCase):
    def _candidate(self, directory: Path, vendor: str = "claude") -> dict[str, object]:
        runtime_path = directory / f"{vendor}-runtime"
        contents = f"{vendor}-runtime\n".encode()
        _write_executable(runtime_path, contents)
        return build_qualification_candidate(
            _evidence(vendor, artifact_contents=contents), runtime_path
        )

    def _load_value(self, directory: Path, value: dict[str, object]) -> QualificationRegistry:
        registry_path = directory / "registry.json"
        registry_path.write_text(json.dumps(value), encoding="utf-8")
        return load_qualification_registry(registry_path)

    def test_registry_rejects_boolean_size_and_duplicate_selector(self) -> None:
        """Breaks if ambiguous or Python bool-as-int records enter the trust root."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            record = self._candidate(directory)
            boolean_size = deepcopy(record)
            boolean_size["artifact_size_bytes"] = True
            duplicate = deepcopy(record)

            with self.assertRaises(QualificationInvalidInputError):
                self._load_value(directory, _registry_value(boolean_size))
            with self.assertRaises(QualificationInvalidInputError):
                self._load_value(
                    directory,
                    {
                        "registry_schema_version": 1,
                        "suite_revision": SUITE_REVISION,
                        "records": [record, duplicate],
                    },
                )

    def test_selection_attaches_exact_requirement_and_rebinds_fingerprint(self) -> None:
        """Breaks if an unreviewed qualification input is hidden from the route digest."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "claude-runtime"
            _write_executable(runtime_path)
            record = build_qualification_candidate(_evidence(), runtime_path)
            registry = self._load_value(directory, _registry_value(record))
            descriptor = _load_descriptor(directory, runtime_path)

            selected = select_qualification_for_descriptor(descriptor, registry)
            qualified = attach_qualification_requirement(descriptor, selected)

        self.assertEqual(qualified["assurance"], "declared_enforcement")
        requirement = qualified["run_requirement"]
        assert isinstance(requirement, dict)
        self.assertEqual(requirement["kind"], "exact_artifact_conformance")
        self.assertEqual(requirement["artifact_sha256"], record["artifact_sha256"])
        self.assertEqual(
            requirement["conformance_evidence_sha256"],
            record["conformance_evidence_sha256"],
        )
        self.assertNotEqual(qualified["route_fingerprint"], descriptor["route_fingerprint"])
        fingerprint_input = dict(qualified)
        fingerprint = fingerprint_input.pop("route_fingerprint")
        payload = json.dumps(
            fingerprint_input,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertEqual(fingerprint, f"sha256:{hashlib.sha256(payload).hexdigest()}")

    def test_selection_fails_closed_for_nonmatching_build(self) -> None:
        """Breaks if a digest record can silently qualify another manifest build."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            _write_executable(runtime_path)
            record = build_qualification_candidate(
                _evidence(runtime_build_id="different-build"), runtime_path
            )
            registry = self._load_value(directory, _registry_value(record))
            descriptor = _load_descriptor(directory, runtime_path)

            with self.assertRaises(QualificationUnsupportedError):
                select_qualification_for_descriptor(descriptor, registry)


class QualifiedRuntimeTests(unittest.TestCase):
    def test_verifier_accepts_exact_bytes_and_rejects_one_byte_change(self) -> None:
        """Breaks if execution is not gated on the package-recorded artifact bytes."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            _write_executable(runtime_path)
            record = build_qualification_candidate(_evidence(), runtime_path)
            registry_path = directory / "registry.json"
            registry_path.write_text(json.dumps(_registry_value(record)), encoding="utf-8")
            selected = load_qualification_registry(registry_path).records[0]

            verify_qualified_runtime(runtime_path, selected)
            _write_executable(runtime_path, b"Qualified-runtime-v1\n")

            with self.assertRaises(QualifiedRuntimeUnavailableError):
                verify_qualified_runtime(runtime_path, selected)

    def test_verifier_rejects_size_mismatch_before_reading_artifact(self) -> None:
        """Breaks if an obvious mismatch triggers avoidable large-file hashing."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            _write_executable(runtime_path)
            record = build_qualification_candidate(_evidence(), runtime_path)
            registry_path = directory / "registry.json"
            registry_path.write_text(json.dumps(_registry_value(record)), encoding="utf-8")
            selected = load_qualification_registry(registry_path).records[0]
            _write_executable(runtime_path, b"x")

            with (
                mock.patch("weightclass.delegation_qualification.os.read") as read,
                self.assertRaises(QualifiedRuntimeUnavailableError),
            ):
                verify_qualified_runtime(runtime_path, selected)

        read.assert_not_called()

    def test_verifier_rejects_non_executable_artifact(self) -> None:
        """Breaks if digest equality bypasses the runtime executability boundary."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            _write_executable(runtime_path)
            record = build_qualification_candidate(_evidence(), runtime_path)
            registry_path = directory / "registry.json"
            registry_path.write_text(json.dumps(_registry_value(record)), encoding="utf-8")
            selected = load_qualification_registry(registry_path).records[0]
            runtime_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

            with self.assertRaises(QualifiedRuntimeUnavailableError):
                verify_qualified_runtime(runtime_path, selected)

    def test_verifier_rejects_final_symlink(self) -> None:
        """Breaks if the final runtime path is a symlink at verification time."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            replacement_path = directory / "replacement"
            _write_executable(runtime_path)
            record = build_qualification_candidate(_evidence(), runtime_path)
            registry_path = directory / "registry.json"
            registry_path.write_text(json.dumps(_registry_value(record)), encoding="utf-8")
            selected = load_qualification_registry(registry_path).records[0]
            _write_executable(replacement_path)
            runtime_path.unlink()
            runtime_path.symlink_to(replacement_path)

            with self.assertRaises(QualifiedRuntimeUnavailableError):
                verify_qualified_runtime(runtime_path, selected)

    def test_verifier_rejects_post_read_metadata_divergence(self) -> None:
        """Breaks if bytes are hashed without binding the post-read metadata."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            _write_executable(runtime_path)
            record = build_qualification_candidate(_evidence(), runtime_path)
            registry_path = directory / "registry.json"
            registry_path.write_text(json.dumps(_registry_value(record)), encoding="utf-8")
            selected = load_qualification_registry(registry_path).records[0]
            real_fstat = os.fstat
            fstat_calls = 0

            def diverge_after_read(file_descriptor: int) -> object:
                nonlocal fstat_calls
                fstat_calls += 1
                result = real_fstat(file_descriptor)
                if fstat_calls == 2:
                    return SimpleNamespace(
                        st_dev=result.st_dev,
                        st_ino=result.st_ino,
                        st_mode=result.st_mode,
                        st_size=result.st_size,
                        st_mtime_ns=result.st_mtime_ns + 1,
                        st_ctime_ns=result.st_ctime_ns,
                    )
                return result

            with (
                mock.patch(
                    "weightclass.delegation_qualification.os.fstat",
                    side_effect=diverge_after_read,
                ),
                self.assertRaises(QualifiedRuntimeUnavailableError),
            ):
                verify_qualified_runtime(runtime_path, selected)

        self.assertEqual(fstat_calls, 2)


if __name__ == "__main__":
    unittest.main()
