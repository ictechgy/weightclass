import json
import math
import os
import subprocess
import sys
import time
import unittest
from typing import cast
from unittest.mock import patch

from tests.synthetic_descendant_containment import assess_descendant_containment
from tests.synthetic_probe_protocol import (
    MAX_MANIFEST_BYTES,
    PROBE_PROTOCOL_ID,
    PROBE_SELF_TEST_IDS,
    ProbeProtocolInvalidInputError,
    build_probe_manifest,
    canonical_probe_manifest_bytes,
    parse_probe_manifest,
)
from tests.synthetic_probe_runner import run_synthetic_probe

PROVENANCE = {
    "collector": "weightclass.synthetic-probe.runner",
    "purpose": "synthetic-self-test-only",
    "trust_boundary": "runner-direct-only",
}
CHILD_MODULE = "tests.synthetic_probe_child"


class _UnstoppableSyntheticChild:
    pid = 43_210
    returncode: int | None = None

    def wait(self, timeout: float | None = None) -> int:
        raise subprocess.TimeoutExpired(
            ("synthetic-child",), timeout if timeout is not None else 0.0
        )

    def terminate(self) -> None:
        raise OSError

    def kill(self) -> None:
        raise OSError

    def poll(self) -> int | None:
        return None


def _child_argv(mode: str) -> tuple[str, ...]:
    return (sys.executable, "-m", CHILD_MODULE, mode)


class SyntheticProbeProtocolTests(unittest.TestCase):
    def test_self_test_namespace_is_closed_to_foreign_identifiers(self) -> None:
        """Breaks if a probe manifest can carry an identifier from outside its namespace."""
        self.assertTrue(all(item.startswith("wcp-selftest/v1/") for item in PROBE_SELF_TEST_IDS))
        for foreign_id in (
            "wcd/v3/permission/workspace-write-denied",
            "wcp-selftest/v2/exit-status",
            "wcp-selftest/v1",
            "",
        ):
            with self.subTest(foreign_id=foreign_id):
                with self.assertRaises(ProbeProtocolInvalidInputError):
                    build_probe_manifest([foreign_id], provenance=PROVENANCE)

    def test_manifest_is_canonical_and_input_order_independent(self) -> None:
        forward = build_probe_manifest(PROBE_SELF_TEST_IDS, provenance=PROVENANCE)
        reverse = build_probe_manifest(reversed(PROBE_SELF_TEST_IDS), provenance=PROVENANCE)
        self.assertEqual(forward, reverse)
        encoded = canonical_probe_manifest_bytes(forward)
        self.assertEqual(encoded, canonical_probe_manifest_bytes(reverse))
        self.assertEqual(
            encoded, json.dumps(forward, sort_keys=True, separators=(",", ":")).encode()
        )
        self.assertEqual(parse_probe_manifest(encoded), forward)
        self_tests = cast(list[dict[str, object]], forward["self_tests"])
        self.assertEqual(
            [entry["self_test_id"] for entry in self_tests],
            sorted(PROBE_SELF_TEST_IDS),
        )

    def test_manifest_has_explicit_synthetic_runner_direct_provenance(self) -> None:
        manifest = build_probe_manifest(PROBE_SELF_TEST_IDS, provenance=PROVENANCE)
        self.assertEqual(manifest["probe_protocol_id"], PROBE_PROTOCOL_ID)
        self.assertEqual(manifest["provenance"], PROVENANCE)
        self.assertIs(manifest["qualification_eligible"], False)
        self.assertIs(manifest["delegation_support"], False)

    def test_manifest_never_grows_qualification_record_fields(self) -> None:
        """Breaks if the synthetic manifest starts to look like a shippable claim record."""
        manifest = build_probe_manifest(PROBE_SELF_TEST_IDS, provenance=PROVENANCE)
        candidate_fields = {
            "record_schema_version",
            "runtime_build_id",
            "adapter_id",
            "vendor_family",
            "result_matrix",
            "scenario_results",
        }
        self.assertTrue(candidate_fields.isdisjoint(manifest))

    def test_rejects_unknown_duplicate_ambiguous_or_malformed_values(self) -> None:
        valid = build_probe_manifest(PROBE_SELF_TEST_IDS, provenance=PROVENANCE)
        self_tests = cast(list[dict[str, object]], valid["self_tests"])
        cases: list[object] = [
            {**valid, "unknown": True},
            {**valid, "probe_manifest_schema_version": 1.0},
            {**valid, "delegation_support": True},
            {**valid, "qualification_eligible": True},
            {**valid, "probe_protocol_id": "delegation-conformance-v2"},
            {**valid, "self_tests": [self_tests[0], self_tests[0]]},
            {**valid, "self_tests": [{"self_test_id": 1}]},
            {**valid, "self_tests": list(reversed(self_tests))},
            {**valid, "provenance": {**PROVENANCE, "collector": "child"}},
            {**valid, "provenance": {**PROVENANCE, "unknown": "value"}},
        ]
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(ProbeProtocolInvalidInputError):
                    parse_probe_manifest(json.dumps(value).encode())

        malformed = [
            b"[]",
            b"{",
            b'{"probe_manifest_schema_version":1,"probe_manifest_schema_version":1}',
            b"\xff",
            b"{}" + (b" " * MAX_MANIFEST_BYTES),
        ]
        for encoded in malformed:
            with self.subTest(encoded=encoded[:40]):
                with self.assertRaises(ProbeProtocolInvalidInputError):
                    parse_probe_manifest(encoded)

    def test_rejects_deeply_nested_json_with_bounded_protocol_error(self) -> None:
        encoded = (b"[" * 8_000) + (b"]" * 8_000)
        self.assertLess(len(encoded), MAX_MANIFEST_BYTES)

        with self.assertRaises(ProbeProtocolInvalidInputError) as context:
            parse_probe_manifest(encoded)

        self.assertEqual(str(context.exception), "")

    def test_schema_bounds_identifiers_and_provenance(self) -> None:
        overlong = "wcp-selftest/v1/" + ("a" * 128)
        invalid_identifiers = ["", overlong, "matrix/worker", "wcp-selftest/v1/UPPER", "x"]
        for identifier in invalid_identifiers:
            with self.subTest(identifier=identifier):
                with self.assertRaises(ProbeProtocolInvalidInputError):
                    build_probe_manifest([identifier], provenance=PROVENANCE)
        with self.assertRaises(ProbeProtocolInvalidInputError):
            build_probe_manifest(PROBE_SELF_TEST_IDS * 4, provenance=PROVENANCE)

        for field in PROVENANCE:
            with self.subTest(field=field):
                incomplete = dict(PROVENANCE)
                del incomplete[field]
                with self.assertRaises(ProbeProtocolInvalidInputError):
                    build_probe_manifest(PROBE_SELF_TEST_IDS, provenance=incomplete)


class SyntheticProbeRunnerTests(unittest.TestCase):
    def test_rejects_embedded_nul_before_starting_a_process(self) -> None:
        argv = (*_child_argv("clean"), "opaque-input\x00with-nul")
        with patch(
            "tests.synthetic_probe_runner.subprocess.Popen",
            side_effect=AssertionError("Popen must not receive embedded NUL"),
        ) as popen:
            result = run_synthetic_probe(argv, timeout_seconds=1.0)

        popen.assert_not_called()
        self.assertEqual(result["diagnostic"], "probe_invalid_input")
        self.assertIs(result["child_started"], False)
        self.assertIsNone(result["child_pid"])
        self.assertIsNone(result["exit_status"])

    def test_normalizes_popen_value_error_to_bounded_start_failure(self) -> None:
        argv = _child_argv("clean")
        with patch(
            "tests.synthetic_probe_runner.subprocess.Popen",
            side_effect=ValueError("opaque child-start detail"),
        ):
            result = run_synthetic_probe(argv, timeout_seconds=1.0)

        self.assertEqual(result["diagnostic"], "probe_start_failed")
        self.assertNotIn("opaque child-start detail", cast(str, result["diagnostic"]))
        self.assertIs(result["child_started"], False)
        self.assertIsNone(result["child_pid"])
        self.assertIsNone(result["exit_status"])

    def test_stop_failure_is_redacted_and_preserves_runner_observations(self) -> None:
        argv = _child_argv("timeout")
        with patch(
            "tests.synthetic_probe_runner.subprocess.Popen",
            return_value=_UnstoppableSyntheticChild(),
        ):
            try:
                result = run_synthetic_probe(argv, timeout_seconds=0.01)
            except OSError:
                self.fail("child stop failure escaped the runner boundary")

        self.assertEqual(result["selected_argv"], list(argv))
        self.assertIs(result["child_started"], True)
        self.assertEqual(result["child_pid"], 43_210)
        self.assertIsNone(result["exit_status"])
        self.assertIs(result["timed_out"], True)
        self.assertEqual(result["diagnostic"], "probe_stop_failed")
        self.assertEqual(result["frames"], [])

    def test_pipe_creation_failure_is_redacted_and_fails_closed(self) -> None:
        argv = _child_argv("clean")
        with patch("tests.synthetic_probe_runner.os.pipe", side_effect=OSError):
            try:
                result = run_synthetic_probe(argv, timeout_seconds=1.0)
            except OSError:
                self.fail("pipe creation failure escaped the runner boundary")

        self.assertEqual(result["selected_argv"], list(argv))
        self.assertIs(result["child_started"], False)
        self.assertIsNone(result["child_pid"])
        self.assertIsNone(result["exit_status"])
        self.assertIs(result["timed_out"], False)
        self.assertEqual(result["diagnostic"], "probe_pipe_failed")

    def test_child_environment_excludes_parent_sensitive_sentinel(self) -> None:
        sentinel_name = "WCP_FAKE_SENSITIVE_SENTINEL"
        with patch.dict(os.environ, {sentinel_name: "opaque-test-value"}, clear=False):
            self.assertIn(sentinel_name, os.environ)
            with patch.object(type(os.environ), "copy", side_effect=AssertionError):
                result = run_synthetic_probe(
                    _child_argv("minimal-environment"), timeout_seconds=1.0
                )

        self.assertEqual(result["diagnostic"], "ok")
        self.assertEqual(result["exit_status"], 0)

    def test_traffic_is_accepted_only_after_writer_eof(self) -> None:
        result = run_synthetic_probe(
            _child_argv("delayed-traffic-after-direct-exit"), timeout_seconds=1.0
        )

        self.assertEqual(result["exit_status"], 0)
        self.assertEqual(result["diagnostic"], "probe_protocol_invalid")
        self.assertEqual(result["frames"], [])

    def test_retained_writer_fails_closed_within_runner_deadline(self) -> None:
        started = time.monotonic()
        result = run_synthetic_probe(_child_argv("retained-writer"), timeout_seconds=0.5)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 1.0)
        self.assertIs(result["child_started"], True)
        self.assertIsInstance(result["child_pid"], int)
        self.assertEqual(result["exit_status"], 0)
        self.assertIs(result["timed_out"], True)
        self.assertEqual(result["diagnostic"], "probe_writer_retained")
        self.assertEqual(result["frames"], [])

    def test_oversized_traffic_is_rejected_before_writer_eof(self) -> None:
        started = time.monotonic()
        result = run_synthetic_probe(_child_argv("oversized-retained-writer"), timeout_seconds=1.0)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.75)
        self.assertEqual(result["exit_status"], 0)
        self.assertIs(result["timed_out"], False)
        self.assertEqual(result["diagnostic"], "probe_protocol_invalid")
        self.assertEqual(result["frames"], [])

    def test_clean_child_records_only_runner_direct_decisive_evidence(self) -> None:
        argv = _child_argv("clean")
        result = run_synthetic_probe(argv, timeout_seconds=1.0)

        self.assertEqual(result["selected_argv"], list(argv))
        self.assertIs(result["child_started"], True)
        self.assertIsInstance(result["child_pid"], int)
        self.assertEqual(result["exit_status"], 0)
        self.assertIs(result["timed_out"], False)
        self.assertEqual(result["path_execution_identity"], "TOCTOU-UNRESOLVED")
        self.assertEqual(result["diagnostic"], "ok")
        frames = cast(list[dict[str, object]], result["frames"])
        self.assertEqual(
            [frame["self_test_id"] for frame in frames],
            ["wcp-selftest/v1/child-start", "wcp-selftest/v1/direct-child-exit"],
        )
        self.assertTrue(
            all(frame["traffic_observation_provenance"] == "runner-direct" for frame in frames)
        )
        self.assertTrue(
            all(frame["payload_assertion_trust"] == "untrusted-child" for frame in frames)
        )
        self.assertNotIn("telemetry", result)
        self.assertNotIn("stdout", result)
        self.assertNotIn("exact_executed_bytes", result)

    def test_stdout_and_child_telemetry_are_untrusted_and_non_decisive(self) -> None:
        result = run_synthetic_probe(_child_argv("stdout-spoof"), timeout_seconds=1.0)

        self.assertEqual(result["diagnostic"], "ok")
        self.assertEqual(len(cast(list[object], result["frames"])), 2)
        self.assertEqual(result["untrusted_stdout_policy"], "discarded-non-decisive")
        self.assertEqual(result["untrusted_child_assertion_policy"], "rejected-non-decisive")
        self.assertEqual(
            result["untrusted_child_channels"],
            ["frame-payload", "self-attestation", "stdout", "telemetry"],
        )
        self.assertNotIn("claimed_success", result)
        self.assertNotIn("telemetry", result)

    def test_child_self_attestation_cannot_override_runner_observed_exit(self) -> None:
        result = run_synthetic_probe(_child_argv("self-attested-success"), timeout_seconds=1.0)

        self.assertEqual(result["exit_status"], 23)
        self.assertEqual(result["diagnostic"], "probe_child_failed")
        self.assertEqual(result["frames"], [])
        self.assertNotIn("claimed_success", result)

    def test_hostile_substitution_cannot_override_selected_argv(self) -> None:
        argv = _child_argv("substitute-argv")
        result = run_synthetic_probe(argv, timeout_seconds=1.0)

        self.assertEqual(result["selected_argv"], list(argv))
        self.assertEqual(result["diagnostic"], "probe_protocol_invalid")
        self.assertEqual(result["frames"], [])

    def test_rejects_malformed_truncated_duplicate_reordered_and_unexpected_frames(self) -> None:
        for mode in (
            "malformed",
            "truncated",
            "duplicate",
            "reordered",
            "unexpected-id",
            "oversized",
            "oversized-traffic",
            "duplicate-json-key",
        ):
            with self.subTest(mode=mode):
                result = run_synthetic_probe(_child_argv(mode), timeout_seconds=1.0)
                self.assertEqual(result["diagnostic"], "probe_protocol_invalid")
                self.assertEqual(result["frames"], [])
                self.assertEqual(result["path_execution_identity"], "TOCTOU-UNRESOLVED")

    def test_timeout_is_runner_observed_and_fails_closed(self) -> None:
        result = run_synthetic_probe(_child_argv("timeout"), timeout_seconds=0.05)

        self.assertIs(result["child_started"], True)
        self.assertIs(result["timed_out"], True)
        self.assertIsInstance(result["exit_status"], int)
        self.assertEqual(result["diagnostic"], "probe_timeout")
        self.assertEqual(result["frames"], [])

    def test_clean_exit_without_complete_frames_fails_closed(self) -> None:
        result = run_synthetic_probe(_child_argv("clean-no-frames"), timeout_seconds=1.0)

        self.assertEqual(result["exit_status"], 0)
        self.assertIs(result["timed_out"], False)
        self.assertEqual(result["diagnostic"], "probe_protocol_invalid")
        self.assertEqual(result["frames"], [])

    def test_invalid_deadlines_and_unavailable_child_fail_closed(self) -> None:
        invalid_timeouts = (
            0.0,
            -1.0,
            math.inf,
            math.nan,
            5.000_001,
            cast(float, True),
            cast(float, "1.0"),
            cast(float, 1 + 0j),
        )
        for timeout in invalid_timeouts:
            with self.subTest(timeout=timeout):
                result = run_synthetic_probe(_child_argv("clean"), timeout_seconds=timeout)
                self.assertEqual(result["diagnostic"], "probe_invalid_input")
                self.assertIs(result["child_started"], False)

        bounded_result = run_synthetic_probe(_child_argv("clean"), timeout_seconds=5.0)
        self.assertEqual(bounded_result["diagnostic"], "ok")

        unavailable_argv = ("/definitely/not/a/synthetic-probe-child",)
        result = run_synthetic_probe(unavailable_argv, timeout_seconds=1.0)
        self.assertEqual(result["selected_argv"], list(unavailable_argv))
        self.assertEqual(result["diagnostic"], "probe_start_failed")
        self.assertIs(result["child_started"], False)
        self.assertIsNone(result["child_pid"])
        self.assertIsNone(result["exit_status"])
        self.assertIs(result["timed_out"], False)


class SyntheticDescendantContainmentTests(unittest.TestCase):
    def test_linux_and_darwin_are_tested_no_go_without_authoritative_primitive(self) -> None:
        probe = run_synthetic_probe(
            _child_argv("delayed-traffic-after-direct-exit"), timeout_seconds=1.0
        )
        self.assertIs(probe["child_started"], True)
        self.assertEqual(probe["exit_status"], 0)
        self.assertEqual(probe["diagnostic"], "probe_protocol_invalid")

        for target in ("linux", "darwin"):
            with self.subTest(target=target):
                result = assess_descendant_containment(target, probe)
                self.assertEqual(result["decision"], "NO-GO")
                self.assertIs(result["qualification_eligible"], False)
                self.assertIs(result["delegation_support"], False)
                self.assertEqual(result["authority"], "not-established")
                self.assertEqual(result["observation_provenance"], "runner-direct")
                self.assertEqual(
                    result["bounded_observation"],
                    "direct-child-exit-and-invalid-runner-fd-traffic-observed",
                )
                self.assertEqual(result["path_execution_identity"], "TOCTOU-UNRESOLVED")
                self.assertEqual(
                    result["rejected_non_authorities"],
                    ["child-cooperation", "child-self-report", "process-group-membership"],
                )
                conclusions = cast(list[str], result["platform_conclusions"])
                self.assertTrue(
                    any("observes-only-known-processes" in item for item in conclusions)
                )
                self.assertTrue(any("not-" in item for item in conclusions))

    def test_unavailable_ambiguous_or_child_asserted_evidence_is_no_go(self) -> None:
        cases: tuple[dict[str, object], ...] = (
            {},
            {"child_started": False, "diagnostic": "probe_start_failed"},
            {
                "child_started": True,
                "exit_status": 0,
                "diagnostic": "probe_protocol_invalid",
            },
            {
                "child_started": True,
                "exit_status": 0,
                "diagnostic": "ok",
                "claimed_containment": True,
            },
        )
        for target in ("linux", "darwin"):
            for probe in cases:
                with self.subTest(target=target, probe=probe):
                    result = assess_descendant_containment(target, probe)
                    self.assertEqual(result["decision"], "NO-GO")
                    self.assertEqual(result["authority"], "not-established")
                    self.assertEqual(result["observation_provenance"], "unavailable-or-untrusted")
                    self.assertNotIn("claimed_containment", result)

    def test_platform_label_never_implies_primitive_availability(self) -> None:
        for target in ("linux", "darwin"):
            result = assess_descendant_containment(target, {})
            self.assertEqual(result["primitive_availability"], "unavailable-or-unverified")
            self.assertEqual(result["observation_provenance"], "unavailable-or-untrusted")
            self.assertEqual(result["decision"], "NO-GO")

        with self.assertRaises(ValueError):
            assess_descendant_containment("freebsd", {})


if __name__ == "__main__":
    unittest.main()
