from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.compare_release_candidates import (
    ComparisonError,
    candidate_snapshot,
    compare_or_record,
    load_exclusions,
)
from tests.test_distribution_isolation import _write_distribution_fixture
from tests.verify_release_candidate import (
    MANIFEST_NAME,
    MAX_RELEASE_ARTIFACT_BYTES,
    ReleaseCandidateError,
    _regular_bytes,
    create_staging,
    load_release_candidate,
    verify_and_stage,
)


def _candidate(directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    source, wheel, sdist = _write_distribution_fixture(str(directory))
    download = directory / "artifact-download"
    download.mkdir()
    rows = []
    for artifact in sorted((wheel, sdist), key=lambda path: path.name):
        raw = artifact.read_bytes()
        (download / artifact.name).write_bytes(raw)
        rows.append((artifact.name, hashlib.sha256(raw).hexdigest()))
    (download / MANIFEST_NAME).write_text(
        "".join(f"{digest}  {name}\n" for name, digest in rows), encoding="ascii"
    )
    return source, download


class ReleaseCandidateTests(unittest.TestCase):
    def test_release_helpers_run_directly_without_pythonpath(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        for helper in (
            "tests/verify_release_candidate.py",
            "tests/compare_release_candidates.py",
        ):
            with self.subTest(helper=helper):
                result = subprocess.run(
                    [sys.executable, helper, "--help"],
                    cwd=repository,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual((result.returncode, result.stderr), (0, b""))

    def test_bounded_reader_rejects_fifo_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "artifact.whl"
            os.mkfifo(path)
            script = (
                "from pathlib import Path\n"
                "from tests.verify_release_candidate import "
                "ReleaseCandidateError, _regular_bytes\n"
                "try:\n"
                "    _regular_bytes(Path(__import__('sys').argv[1]))\n"
                "except ReleaseCandidateError:\n"
                "    raise SystemExit(0)\n"
                "raise SystemExit(1)\n"
            )
            process = subprocess.Popen(
                [sys.executable, "-c", script, str(path)],
                cwd=Path(__file__).resolve().parents[1],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, 9)
                stdout, stderr = process.communicate()
                self.fail("release candidate FIFO read blocked")
            self.assertEqual((process.returncode, stdout, stderr), (0, b"", b""))

    def test_bounded_reader_rejects_replacement_during_same_fd_read(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "artifact.whl"
            path.write_bytes(b"original")
            original_read = os.read
            replaced = False

            def replace_then_read(descriptor: int, count: int) -> bytes:
                nonlocal replaced
                if not replaced:
                    replaced = True
                    path.unlink()
                    path.write_bytes(b"replacement")
                return original_read(descriptor, count)

            with (
                mock.patch("tests.verify_release_candidate.os.read", replace_then_read),
                self.assertRaises(ReleaseCandidateError),
            ):
                _regular_bytes(path)

    def test_bounded_reader_rejects_oversize_before_reading_contents(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "artifact.whl"
            with path.open("wb") as stream:
                stream.truncate(MAX_RELEASE_ARTIFACT_BYTES + 1)
            with (
                mock.patch("tests.verify_release_candidate.os.read") as read,
                self.assertRaises(ReleaseCandidateError),
            ):
                _regular_bytes(path)
            read.assert_not_called()

    def test_publish_staging_preflights_without_executing_sdist_tests(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            source, download = _candidate(Path(raw))
            staging = Path(raw) / "publish-staging"
            with mock.patch(
                "tests.verify_release_candidate.verify_distribution_directory"
            ) as verify:
                verify_and_stage(
                    download,
                    staging,
                    source=source,
                    expected_version="0",
                    run_sdist_tests=False,
                )
            verify.assert_called_once_with(
                source,
                staging,
                run_sdist_tests_requested=False,
                expected_version="0",
            )

    def test_versioned_production_exclusion_allowlist_starts_empty(self) -> None:
        self.assertEqual(load_exclusions(), ())

    def test_three_file_download_stages_only_manifest_named_distributions(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            _source, download = _candidate(Path(raw))
            candidate = load_release_candidate(download)
            staging = Path(raw) / "dist-under-test"
            create_staging(download, staging, candidate)
            self.assertEqual(
                sorted(path.name for path in staging.iterdir()),
                sorted((candidate.wheel_name, candidate.sdist_name)),
            )

    def test_inventory_hash_manifest_and_staging_fail_closed(self) -> None:
        for mutation in ("extra", "missing", "hash", "manifest"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as raw:
                _source, download = _candidate(Path(raw))
                candidate = load_release_candidate(download)
                if mutation == "extra":
                    (download / "extra").write_text("x", encoding="ascii")
                elif mutation == "missing":
                    (download / candidate.wheel_name).unlink()
                elif mutation == "hash":
                    (download / candidate.wheel_name).write_bytes(b"changed")
                else:
                    (download / MANIFEST_NAME).write_text(
                        "0" * 64 + f"  {candidate.wheel_name}\n", encoding="ascii"
                    )
                with self.assertRaises(ReleaseCandidateError):
                    load_release_candidate(download)
        with tempfile.TemporaryDirectory() as raw:
            _source, download = _candidate(Path(raw))
            staging = Path(raw) / "staging"
            staging.mkdir()
            (staging / "contamination").write_text("x", encoding="ascii")
            with self.assertRaises(ReleaseCandidateError):
                create_staging(download, staging, load_release_candidate(download))

    def test_normalized_fallback_and_mismatch(self) -> None:
        with (
            tempfile.TemporaryDirectory() as first_raw,
            tempfile.TemporaryDirectory() as second_raw,
        ):
            _source, first = _candidate(Path(first_raw))
            _source, second = _candidate(Path(second_raw))
            self.assertEqual(compare_or_record(first, second), "normalized")
            reference = Path(first_raw) / "reference.json"
            value = candidate_snapshot(first)
            value["artifacts"][0]["members"][0]["mode"] ^= 1  # type: ignore[index]
            reference.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(ComparisonError):
                compare_or_record(first, reference)

    def test_exclusions_are_literal_documented_and_effective(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "exclusions.json"
            for exclusions in (
                [{"path": "wheel:*", "reason": "broad"}],
                [{"path": "wheel:a"}],
            ):
                path.write_text(
                    json.dumps({"schema_version": 1, "exclusions": exclusions}),
                    encoding="utf-8",
                )
                with self.assertRaises(ComparisonError):
                    load_exclusions(path)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "exclusions": [{"path": "wheel:missing", "reason": "documented"}],
                    }
                ),
                encoding="utf-8",
            )
            _source, download = _candidate(Path(raw) / "candidate")
            with self.assertRaises(ComparisonError):
                candidate_snapshot(download, path)


if __name__ == "__main__":
    unittest.main()
