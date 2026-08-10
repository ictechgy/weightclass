import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.test_delegation_v2_compile import compilable_inputs
from weightclass import cli
from weightclass.delegation_v2_compile import compile_delegation_v2
from weightclass.delegation_v2_protocol import DelegationFrameV2Error
from weightclass.delegation_v2_schema import (
    parse_delegation_manifest_v2,
    parse_delegation_policy_v2,
)
from weightclass.executable_observation import ExecutableObservation
from weightclass.task_v2 import read_validated_task_v2


class DelegationV2CliTests(unittest.TestCase):
    def write_inputs(self, directory: str) -> tuple[Path, Path]:
        policy, manifest = compilable_inputs()
        policy_path = Path(directory) / "policy.json"
        manifest_path = Path(directory) / "manifest.json"
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return policy_path, manifest_path

    @staticmethod
    def arguments(policy: Path, manifest: Path, command: str) -> list[str]:
        return [
            "delegate",
            command,
            "--policy",
            str(policy),
            "--runtime-manifest",
            str(manifest),
            "--delegation-runtime",
            "/owned/runtime",
            "--source-vendor",
            "codex",
            "--source-profile",
            "source",
            "--tier",
            "low",
        ]

    def test_route_prints_the_single_compiled_review_descriptor_without_task_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            policy_path, manifest_path = self.write_inputs(directory)
            with patch("weightclass.cli.read_validated_task_v2") as reader:
                with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    result = cli.main(self.arguments(policy_path, manifest_path, "route"))
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(stdout.getvalue())["runtime_protocol_version"], 2)
        reader.assert_not_called()

    def test_run_confirmation_and_missing_ack_stop_before_task_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            policy_path, manifest_path = self.write_inputs(directory)
            base = self.arguments(policy_path, manifest_path, "run")
            for extra, expected in (
                ([], 5),
                (["--confirm-trusted-delegation-runtime"], 6),
            ):
                with (
                    self.subTest(extra=extra),
                    patch("weightclass.cli.read_validated_task_v2") as reader,
                    patch("weightclass.cli.observe_executable") as inspect,
                ):
                    self.assertEqual(cli.main([*base, *extra]), expected)
                    reader.assert_not_called()
                    inspect.assert_not_called()

    def test_supplied_mismatch_reads_task_and_compiles_before_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            policy_path, manifest_path = self.write_inputs(directory)
            with (
                patch.object(sys, "stdin", io.StringIO("private task")),
                patch(
                    "weightclass.cli.read_validated_task_v2",
                    wraps=read_validated_task_v2,
                ) as reader,
                patch("weightclass.cli.observe_executable") as inspect,
                patch("weightclass.cli.run_delegation_v2_runtime") as spawn,
            ):
                result = cli.main(
                    [
                        *self.arguments(policy_path, manifest_path, "run"),
                        "--confirm-trusted-delegation-runtime",
                        "--ack-route-fingerprint",
                        "wrong",
                    ]
                )
        self.assertEqual(result, 6)
        reader.assert_called_once()
        inspect.assert_not_called()
        spawn.assert_not_called()

    def test_run_builds_wcd2_from_exact_task_bytes_and_one_compiled_truth(self) -> None:
        policy, manifest = compilable_inputs()
        expected = compile_delegation_v2(
            parse_delegation_policy_v2(policy),
            parse_delegation_manifest_v2(manifest),
            source_vendor_family="codex",
            source_profile_id="source",
            tier="low",
            runtime_path="/owned/runtime",
        )
        observed = ExecutableObservation("/owned/runtime", 1, 1, 0o100000, 0o100700, 1, 1, 1, True)
        completed = Mock(returncode=0)
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "policy.json"
            manifest_path = Path(directory) / "manifest.json"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            stdin = io.TextIOWrapper(io.BytesIO("비밀 task".encode()), encoding="utf-8")
            with (
                patch.object(sys, "stdin", stdin),
                patch("weightclass.cli.observe_executable", return_value=observed) as inspect,
                patch("weightclass.cli.encode_delegation_frame_v2", return_value=b"frame") as frame,
                patch("weightclass.cli.run_delegation_v2_runtime", return_value=completed) as spawn,
            ):
                result = cli.main(
                    [
                        *self.arguments(policy_path, manifest_path, "run"),
                        "--confirm-trusted-delegation-runtime",
                        "--ack-route-fingerprint",
                        expected.route_fingerprint,
                    ]
                )
        self.assertEqual(result, 0)
        inspect.assert_called_once_with(expected.executable)
        frame.assert_called_once_with(expected.canonical_descriptor_bytes, "비밀 task".encode())
        actual_compiled, actual_frame, actual_observation = spawn.call_args.args
        self.assertEqual(actual_compiled, expected)
        self.assertEqual(actual_frame, b"frame")
        self.assertIs(actual_observation, observed)

    def test_frame_failure_is_invalid_input_and_stops_before_spawn(self) -> None:
        policy, manifest = compilable_inputs()
        expected = compile_delegation_v2(
            parse_delegation_policy_v2(policy),
            parse_delegation_manifest_v2(manifest),
            source_vendor_family="codex",
            source_profile_id="source",
            tier="low",
            runtime_path="/owned/runtime",
        )
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "policy.json"
            manifest_path = Path(directory) / "manifest.json"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with (
                patch.object(sys, "stdin", io.StringIO("task")),
                patch("weightclass.cli.observe_executable"),
                patch(
                    "weightclass.cli.encode_delegation_frame_v2",
                    side_effect=DelegationFrameV2Error,
                ),
                patch("weightclass.cli.run_delegation_v2_runtime") as spawn,
            ):
                result = cli.main(
                    [
                        *self.arguments(policy_path, manifest_path, "run"),
                        "--confirm-trusted-delegation-runtime",
                        "--ack-route-fingerprint",
                        expected.route_fingerprint,
                    ]
                )
        self.assertEqual(result, 2)
        spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
