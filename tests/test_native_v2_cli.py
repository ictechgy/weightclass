import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.runtime_guard import guarded_launch
from weightclass import cli
from weightclass.delegation_runtime import DelegationRuntimeUnavailableError
from weightclass.executable_observation import ExecutableObservation
from weightclass.native_v2_compile import compile_native_v2
from weightclass.native_v2_schema import parse_native_policy_v2
from weightclass.process_context import ChildStatusLostError
from weightclass.task_v2 import read_validated_task_v2

FIXTURE = Path(__file__).parent / "fixtures/fake_native_runtime.py"


def policy(executable: str = "/owned/fake") -> dict[str, object]:
    return {
        "schema_version": 2,
        "profiles": [{"id": "p", "vendor": "codex", "account_profile": "opaque"}],
        "execution_targets": [
            {
                "id": "t",
                "profile_id": "p",
                "vendor": "codex",
                "executable": executable,
                "builder": {"kind": "codex-exec-v1", "version": 1},
                "allowed_model_effort_pairs": [{"model": "m", "effort": "e"}],
            }
        ],
        "routes": [
            {
                "id": "r",
                "eligibility": [
                    {"source_vendor": "codex", "source_profile_id": "p", "tier": "low"}
                ],
                "target_id": "t",
                "model": "m",
                "effort": "e",
            }
        ],
        "profile_grants": [],
        "vendor_grants": [],
    }


class NativeV2CliTests(unittest.TestCase):
    def write_policy(self, directory: str, value: dict[str, object] | None = None) -> Path:
        path = Path(directory) / "policy.json"
        path.write_text(json.dumps(policy() if value is None else value), encoding="utf-8")
        return path

    def test_route_reads_binary_task_and_classifies_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            stdin = io.TextIOWrapper(io.BytesIO(b"rename file"), encoding="utf-8")
            with (
                patch.object(sys, "stdin", stdin),
                patch("weightclass.cli.classify_task", return_value="low") as classify,
                patch(
                    "weightclass.cli.read_validated_task_v2", wraps=read_validated_task_v2
                ) as reader,
            ):
                self.assertEqual(
                    cli.main(
                        [
                            "route",
                            "--suggest-tier",
                            "--policy",
                            str(path),
                            "--source-vendor",
                            "codex",
                            "--source-profile",
                            "p",
                        ]
                    ),
                    0,
                )
            reader.assert_called_once_with(stdin.buffer)
            classify.assert_called_once_with("rename file")

    @guarded_launch("native_v2")
    def test_route_then_run_owned_fixture_end_to_end(self) -> None:
        secret = b"PRIVATE-NATIVE-V2-TASK"
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory, policy(str(FIXTURE.resolve())))
            common = [
                "--policy",
                str(path),
                "--source-vendor",
                "codex",
                "--source-profile",
                "p",
                "--tier",
                "low",
            ]
            routed = subprocess.run(
                [sys.executable, "-m", "weightclass", "route", *common],
                input=secret,
                capture_output=True,
                check=False,
            )
            self.assertEqual((routed.returncode, routed.stderr), (0, b""))
            fingerprint = json.loads(routed.stdout)["route_fingerprint"]
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "run",
                    *common,
                    "--ack-route-fingerprint",
                    fingerprint,
                ],
                input=secret,
                capture_output=True,
                check=False,
            )
        self.assertEqual((completed.returncode, completed.stdout, completed.stderr), (0, b"", b""))
        self.assertNotIn(
            secret, routed.stdout + routed.stderr + completed.stdout + completed.stderr
        )

    def test_explicit_tier_reads_and_validates_but_never_classifies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            with (
                patch.object(sys, "stdin", io.StringIO("task")),
                patch("weightclass.cli.classify_task") as classify,
            ):
                self.assertEqual(
                    cli.main(
                        [
                            "route",
                            "--policy",
                            str(path),
                            "--source-vendor",
                            "codex",
                            "--source-profile",
                            "p",
                            "--tier",
                            "low",
                        ]
                    ),
                    0,
                )
            classify.assert_not_called()

    def test_run_missing_ack_stops_before_task_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            with (
                patch("weightclass.cli.read_validated_task_v2") as reader,
                patch("weightclass.cli.observe_executable") as inspect,
            ):
                self.assertEqual(
                    cli.main(
                        [
                            "run",
                            "--tier",
                            "low",
                            "--policy",
                            str(path),
                            "--source-vendor",
                            "codex",
                            "--source-profile",
                            "p",
                        ]
                    ),
                    6,
                )
            reader.assert_not_called()
            inspect.assert_not_called()

    def test_run_unsafe_process_context_stops_before_task_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            with (
                patch(
                    "weightclass.cli.validate_runtime_process_context",
                    side_effect=DelegationRuntimeUnavailableError,
                ),
                patch("weightclass.cli.read_validated_task_v2") as reader,
                patch("weightclass.cli.observe_executable") as inspect,
            ):
                self.assertEqual(
                    cli.main(
                        [
                            "run",
                            "--tier",
                            "low",
                            "--policy",
                            str(path),
                            "--source-vendor",
                            "codex",
                            "--source-profile",
                            "p",
                            "--ack-route-fingerprint",
                            "reviewed",
                        ]
                    ),
                    4,
                )
            reader.assert_not_called()
            inspect.assert_not_called()

    def test_run_mismatch_stops_before_task_access_and_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            with (
                patch.object(sys, "stdin", io.StringIO("task")),
                patch("weightclass.cli.read_validated_task_v2") as reader,
                patch("weightclass.cli.observe_executable") as inspect,
                patch("weightclass.cli.run_native_v2") as spawn,
            ):
                self.assertEqual(
                    cli.main(
                        [
                            "run",
                            "--policy",
                            str(path),
                            "--source-vendor",
                            "codex",
                            "--source-profile",
                            "p",
                            "--tier",
                            "low",
                            "--ack-route-fingerprint",
                            "wrong",
                        ]
                    ),
                    6,
                )
            inspect.assert_not_called()
            reader.assert_not_called()
            spawn.assert_not_called()

    def test_run_passes_one_compiled_truth_from_ack_through_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            expected = compile_native_v2(
                parse_native_policy_v2(policy()),
                source_vendor="codex",
                source_profile_id="p",
                tier="low",
            )
            observed = ExecutableObservation("/owned/fake", 1, 1, 0o100000, 0o100700, 0, 1, 1, True)
            completed = Mock(returncode=0)
            with (
                patch.object(sys, "stdin", io.StringIO("task")),
                patch("weightclass.cli.observe_executable", return_value=observed) as inspect,
                patch("weightclass.cli.run_native_v2", return_value=completed) as spawn,
            ):
                exit_code = cli.main(
                    [
                        "run",
                        "--policy",
                        str(path),
                        "--source-vendor",
                        "codex",
                        "--source-profile",
                        "p",
                        "--tier",
                        "low",
                        "--ack-route-fingerprint",
                        expected.route_fingerprint,
                    ]
                )
            self.assertEqual(exit_code, 0)
            inspect.assert_called_once_with(expected.executable)
            actual_compiled, delivered, actual_observation = spawn.call_args.args
            self.assertEqual(actual_compiled, expected)
            self.assertEqual(delivered, b"task")
            self.assertIs(actual_observation, observed)

    def test_guided_run_confirms_compiled_route_without_copied_ack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            observed = ExecutableObservation("/owned/fake", 1, 1, 0o100000, 0o100700, 0, 1, 1, True)
            completed = Mock(returncode=0)
            with (
                patch.object(sys, "stdin", io.StringIO("task")),
                patch("weightclass.cli.observe_executable", return_value=observed),
                patch(
                    "weightclass.cli._confirm_native_descriptor_on_console",
                    return_value=True,
                ) as confirmation,
                patch("weightclass.cli.run_native_v2", return_value=completed) as spawn,
            ):
                exit_code = cli.main(
                    [
                        "run",
                        "--review",
                        "--policy",
                        str(path),
                        "--source-vendor",
                        "codex",
                        "--source-profile",
                        "p",
                        "--tier",
                        "low",
                    ]
                )

        self.assertEqual(exit_code, 0)
        confirmation.assert_called_once()
        spawn.assert_called_once()

    def test_lost_child_status_is_executor_failed_not_unavailable(self) -> None:
        """Breaks if a post-spawn wait-status loss is reported as a spawn failure."""
        expected = compile_native_v2(
            parse_native_policy_v2(policy()),
            source_vendor="codex",
            source_profile_id="p",
            tier="low",
        )
        observed = ExecutableObservation("/owned/fake", 1, 1, 0o100000, 0o100700, 0, 1, 1, True)
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            errors = io.StringIO()
            with (
                patch.object(sys, "stdin", io.StringIO("task")),
                patch.object(sys, "stderr", errors),
                patch("weightclass.cli.observe_executable", return_value=observed),
                patch(
                    "weightclass.cli.run_native_v2",
                    side_effect=ChildStatusLostError(),
                ),
            ):
                exit_code = cli.main(
                    [
                        "run",
                        "--policy",
                        str(path),
                        "--source-vendor",
                        "codex",
                        "--source-profile",
                        "p",
                        "--tier",
                        "low",
                        "--ack-route-fingerprint",
                        expected.route_fingerprint,
                    ]
                )

        self.assertEqual(exit_code, 7)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "executor_failed"})

    def test_schema_one_and_builtins_reject_source_profile_semantically(self) -> None:
        for arguments in (["route", "--suggest-tier", "--source-profile", "p"],):
            with (
                self.subTest(arguments=arguments),
                patch("weightclass.cli.read_task_from_standard_input") as reader,
            ):
                self.assertEqual(cli.main(arguments), 2)
                reader.assert_not_called()
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(
                directory,
                {
                    "schema_version": 1,
                    "routes": [{"id": "r", "vendor": "codex", "tier": "low", "command": ["x"]}],
                },
            )
            with patch("weightclass.cli.read_task_from_standard_input") as reader:
                self.assertEqual(
                    cli.main(
                        ["route", "--suggest-tier", "--policy", str(path), "--source-profile", "p"]
                    ),
                    2,
                )
                reader.assert_not_called()

    def test_schema_two_requires_both_source_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            for omitted in (["--source-vendor", "codex"], ["--source-profile", "p"]):
                with (
                    self.subTest(omitted=omitted),
                    patch("weightclass.cli.read_validated_task_v2") as reader,
                ):
                    self.assertEqual(
                        cli.main(["route", "--suggest-tier", "--policy", str(path), *omitted]), 2
                    )
                    reader.assert_not_called()

    def test_source_profile_is_validated_before_task_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            for source_profile in ("x" * 65, "has space", "invisible\u200b"):
                with (
                    self.subTest(source_profile=source_profile),
                    patch("weightclass.cli.read_validated_task_v2") as reader,
                ):
                    self.assertEqual(
                        cli.main(
                            [
                                "route",
                                "--policy",
                                str(path),
                                "--source-vendor",
                                "codex",
                                "--source-profile",
                                source_profile,
                                "--tier",
                                "low",
                            ]
                        ),
                        2,
                    )
                    reader.assert_not_called()

    def test_model_edge_whitespace_is_redacted_before_task_access(self) -> None:
        invalid_policy = policy()
        targets = invalid_policy["execution_targets"]
        routes = invalid_policy["routes"]
        assert isinstance(targets, list) and isinstance(targets[0], dict)
        pairs = targets[0]["allowed_model_effort_pairs"]
        assert isinstance(pairs, list) and isinstance(pairs[0], dict)
        assert isinstance(routes, list) and isinstance(routes[0], dict)
        pairs[0]["model"] = " private-model"
        routes[0]["model"] = " private-model"
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory, invalid_policy)
            output = io.StringIO()
            errors = io.StringIO()
            with (
                patch.object(sys, "stdout", output),
                patch.object(sys, "stderr", errors),
                patch("weightclass.cli.read_validated_task_v2") as reader,
            ):
                exit_code = cli.main(
                    [
                        "route",
                        "--policy",
                        str(path),
                        "--source-vendor",
                        "codex",
                        "--source-profile",
                        "p",
                        "--tier",
                        "low",
                    ]
                )

        self.assertEqual(exit_code, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(json.loads(errors.getvalue()), {"error": "invalid_input"})
        self.assertNotIn("private-model", errors.getvalue())
        reader.assert_not_called()

    def test_render_does_not_accept_source_profile(self) -> None:
        self.assertEqual(
            cli.main(["render", "--policy", "x", "--descriptor", "y", "--source-profile", "p"]), 2
        )


if __name__ == "__main__":
    unittest.main()
