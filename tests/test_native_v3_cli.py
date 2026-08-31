import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_native_v2_cli import policy as valid_v2_policy
from tests.test_native_v3_schema import valid_policy
from weightclass import cli
from weightclass.delegation_runtime import DelegationRuntimeUnavailableError
from weightclass.executable_observation import ExecutableObservation
from weightclass.native_v3_compile import (
    bind_native_observation_v3,
    compile_static_native_policy_v3,
)
from weightclass.native_v3_schema import parse_native_policy_v3
from weightclass.v2_validation import V2ValidationError


class HostileOneReadStream:
    def __init__(self, payload: bytes = b"PRIVATE TASK") -> None:
        self.payload = payload
        self.read_calls = 0

    @property
    def buffer(self) -> "HostileOneReadStream":
        return self

    def read(self, size: int = -1) -> bytes:
        self.read_calls += 1
        if self.read_calls > 1:
            raise AssertionError("task stream read more than once")
        if size >= 0:
            return self.payload[:size]
        return self.payload


def observation(path: str, *, inode: int = 2) -> ExecutableObservation:
    return ExecutableObservation(path, 1, inode, 0o100000, 0o100755, 100, 3, 4, True)


class NativeV3CliTests(unittest.TestCase):
    def write_policy(self, directory: str, value: dict[str, object] | None = None) -> Path:
        path = Path(directory) / "policy.json"
        path.write_text(json.dumps(value or valid_policy()), encoding="utf-8")
        return path

    def run_arguments(self, path: Path, *extra: str) -> list[str]:
        return [
            "run",
            "--policy",
            str(path),
            "--source-vendor",
            "codex",
            "--source-profile",
            "source",
            "--tier",
            "low",
            *extra,
        ]

    def fingerprint(self, first: ExecutableObservation) -> str:
        selected = compile_static_native_policy_v3(
            parse_native_policy_v3(valid_policy()),
            source_vendor="codex",
            source_profile_id="source",
            tier="low",
            purpose="native_route",
        )
        value = bind_native_observation_v3(selected, first)["route_fingerprint"]
        assert isinstance(value, str)
        return value

    def test_transition_confirmation_precedes_ack_context_observation_task_and_spawn(self) -> None:
        """Breaks if a cross-endpoint schema-3 run can pass without explicit consent."""
        stream = HostileOneReadStream()
        errors = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            with (
                patch.object(sys, "stdin", stream),
                patch.object(sys, "stderr", errors),
                patch("weightclass.cli.validate_runtime_process_context") as context,
                patch("weightclass.cli.observe_executable") as observe,
                patch("weightclass.native_v3_runtime.run_owned_foreground_redacted") as spawn,
            ):
                result = cli.main(self.run_arguments(path, "--ack-route-fingerprint", "reviewed"))

        self.assertEqual(result, 5)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "confirmation_required"})
        self.assertEqual(stream.read_calls, 0)
        context.assert_not_called()
        observe.assert_not_called()
        spawn.assert_not_called()

    def test_missing_or_empty_ack_precedes_context_observation_task_and_spawn(self) -> None:
        """Breaks if schema-3 execution accepts an absent reviewed fingerprint."""
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            for ack_arguments in ((), ("--ack-route-fingerprint", "")):
                with self.subTest(ack_arguments=ack_arguments):
                    stream = HostileOneReadStream()
                    errors = io.StringIO()
                    with (
                        patch.object(sys, "stdin", stream),
                        patch.object(sys, "stderr", errors),
                        patch("weightclass.cli.validate_runtime_process_context") as context,
                        patch("weightclass.cli.observe_executable") as observe,
                        patch(
                            "weightclass.native_v3_runtime.run_owned_foreground_redacted"
                        ) as spawn,
                    ):
                        result = cli.main(
                            self.run_arguments(
                                path,
                                "--confirm-endpoint-transition",
                                *ack_arguments,
                            )
                        )

                    self.assertEqual(result, 6)
                    self.assertEqual(
                        json.loads(errors.getvalue()),
                        {"error": "route_fingerprint_mismatch"},
                    )
                    self.assertEqual(stream.read_calls, 0)
                    context.assert_not_called()
                    observe.assert_not_called()
                    spawn.assert_not_called()

    def test_unsafe_process_context_precedes_observation_task_and_spawn(self) -> None:
        """Breaks if schema-3 reads a task when direct-child status is unsafe."""
        stream = HostileOneReadStream()
        errors = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            with (
                patch.object(sys, "stdin", stream),
                patch.object(sys, "stderr", errors),
                patch(
                    "weightclass.cli.validate_runtime_process_context",
                    side_effect=DelegationRuntimeUnavailableError,
                ),
                patch("weightclass.cli.observe_executable") as observe,
                patch("weightclass.native_v3_runtime.run_owned_foreground_redacted") as spawn,
            ):
                result = cli.main(
                    self.run_arguments(
                        path,
                        "--confirm-endpoint-transition",
                        "--ack-route-fingerprint",
                        "reviewed",
                    )
                )

        self.assertEqual(result, 4)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "executor_unavailable"})
        self.assertEqual(stream.read_calls, 0)
        observe.assert_not_called()
        spawn.assert_not_called()

    def test_unavailable_first_observation_precedes_task_and_spawn(self) -> None:
        """Breaks if a missing executable is observed only after task access."""
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            for unavailable in (
                V2ValidationError("PRIVATE PATH"),
                OSError("PRIVATE PATH"),
                ValueError("PRIVATE PATH"),
            ):
                with self.subTest(error_type=type(unavailable).__name__):
                    stream = HostileOneReadStream()
                    errors = io.StringIO()
                    actual_error: BaseException | None = None
                    result: int | None = None
                    with (
                        patch.object(sys, "stdin", stream),
                        patch.object(sys, "stderr", errors),
                        patch("weightclass.cli.validate_runtime_process_context"),
                        patch(
                            "weightclass.cli.observe_executable",
                            side_effect=unavailable,
                        ) as observe,
                        patch(
                            "weightclass.native_v3_runtime.run_owned_foreground_redacted"
                        ) as spawn,
                    ):
                        try:
                            result = cli.main(
                                self.run_arguments(
                                    path,
                                    "--confirm-endpoint-transition",
                                    "--ack-route-fingerprint",
                                    "reviewed",
                                )
                            )
                        except BaseException as error:
                            actual_error = error

                    self.assertIsNone(actual_error)
                    self.assertEqual(result, 4)
                    self.assertEqual(
                        json.loads(errors.getvalue()), {"error": "executor_unavailable"}
                    )
                    self.assertEqual(stream.read_calls, 0)
                    observe.assert_called_once_with("/opt/grok")
                    spawn.assert_not_called()

    def test_mismatched_ack_follows_first_observation_but_precedes_task_and_spawn(self) -> None:
        """Breaks if fingerprint comparison happens against an unobserved template."""
        stream = HostileOneReadStream()
        errors = io.StringIO()
        first = observation("/opt/grok")
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            with (
                patch.object(sys, "stdin", stream),
                patch.object(sys, "stderr", errors),
                patch("weightclass.cli.validate_runtime_process_context"),
                patch("weightclass.cli.observe_executable", return_value=first) as observe,
                patch("weightclass.native_v3_runtime.run_owned_foreground_redacted") as spawn,
            ):
                result = cli.main(
                    self.run_arguments(
                        path,
                        "--confirm-endpoint-transition",
                        "--ack-route-fingerprint",
                        "sha256:wrong",
                    )
                )

        self.assertEqual(result, 6)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "route_fingerprint_mismatch"})
        self.assertEqual(stream.read_calls, 0)
        observe.assert_called_once_with("/opt/grok")
        spawn.assert_not_called()

    def test_guided_run_confirms_observation_bound_route_without_copied_ack(self) -> None:
        stream = HostileOneReadStream()
        first = observation("/opt/grok")
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            with (
                patch.object(sys, "stdin", stream),
                patch("weightclass.cli.validate_runtime_process_context"),
                patch("weightclass.cli.observe_executable", return_value=first),
                patch(
                    "weightclass.cli._confirm_native_descriptor_on_console",
                    return_value=True,
                ) as confirmation,
                patch("weightclass.native_v3_runtime.observe_executable", return_value=first),
                patch(
                    "weightclass.native_v3_runtime.run_owned_foreground_redacted",
                    return_value=0,
                ) as spawn,
            ):
                result = cli.main(
                    self.run_arguments(
                        path,
                        "--confirm-endpoint-transition",
                        "--review",
                    )
                )

        self.assertEqual(result, 0)
        confirmation.assert_called_once()
        self.assertEqual(stream.read_calls, 1)
        spawn.assert_called_once()

    def test_invalid_task_is_read_once_after_exact_ack_and_never_spawned(self) -> None:
        """Breaks if V2 task validation is bypassed or retried for schema 3."""
        stream = HostileOneReadStream(b"\xffPRIVATE")
        errors = io.StringIO()
        first = observation("/opt/grok")
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            with (
                patch.object(sys, "stdin", stream),
                patch.object(sys, "stderr", errors),
                patch("weightclass.cli.validate_runtime_process_context"),
                patch("weightclass.cli.observe_executable", return_value=first),
                patch("weightclass.native_v3_runtime.run_owned_foreground_redacted") as spawn,
            ):
                result = cli.main(
                    self.run_arguments(
                        path,
                        "--confirm-endpoint-transition",
                        "--ack-route-fingerprint",
                        self.fingerprint(first),
                    )
                )

        self.assertEqual(result, 2)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "invalid_task"})
        self.assertEqual(stream.read_calls, 1)
        spawn.assert_not_called()

    def test_unavailable_final_observation_maps_after_one_task_read(self) -> None:
        """Breaks if a final lstat failure is mislabeled or starts a child."""
        stream = HostileOneReadStream()
        errors = io.StringIO()
        first = observation("/opt/grok")
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            with (
                patch.object(sys, "stdin", stream),
                patch.object(sys, "stderr", errors),
                patch("weightclass.cli.validate_runtime_process_context"),
                patch("weightclass.cli.observe_executable", return_value=first),
                patch(
                    "weightclass.native_v3_runtime.observe_executable",
                    side_effect=V2ValidationError("PRIVATE PATH"),
                ),
                patch("weightclass.native_v3_runtime.run_owned_foreground_redacted") as spawn,
            ):
                result = cli.main(
                    self.run_arguments(
                        path,
                        "--confirm-endpoint-transition",
                        "--ack-route-fingerprint",
                        self.fingerprint(first),
                    )
                )

        self.assertEqual(result, 4)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "executor_unavailable"})
        self.assertEqual(stream.read_calls, 1)
        spawn.assert_not_called()

    def test_final_observation_drift_is_fingerprint_mismatch_after_one_task_read(self) -> None:
        """Breaks if last-moment executable replacement is mislabeled or spawned."""
        stream = HostileOneReadStream()
        errors = io.StringIO()
        first = observation("/opt/grok")
        final = observation("/opt/grok", inode=9)
        actual_error: BaseException | None = None
        result: int | None = None
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            with (
                patch.object(sys, "stdin", stream),
                patch.object(sys, "stderr", errors),
                patch("weightclass.cli.validate_runtime_process_context"),
                patch("weightclass.cli.observe_executable", return_value=first),
                patch("weightclass.native_v3_runtime.observe_executable", return_value=final),
                patch("weightclass.native_v3_runtime.run_owned_foreground_redacted") as spawn,
            ):
                try:
                    result = cli.main(
                        self.run_arguments(
                            path,
                            "--confirm-endpoint-transition",
                            "--ack-route-fingerprint",
                            self.fingerprint(first),
                        )
                    )
                except BaseException as error:
                    actual_error = error

        self.assertIsNone(actual_error)
        self.assertEqual(result, 6)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "route_fingerprint_mismatch"})
        self.assertEqual(stream.read_calls, 1)
        spawn.assert_not_called()

    def test_argv_materialization_failure_is_invalid_task_after_one_read(self) -> None:
        """Breaks if task-dependent argv rejection is reported as executor failure."""
        stream = HostileOneReadStream(b"PRIVATE\x00TASK")
        errors = io.StringIO()
        first = observation("/opt/grok")
        actual_error: BaseException | None = None
        result: int | None = None
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            with (
                patch.object(sys, "stdin", stream),
                patch.object(sys, "stderr", errors),
                patch("weightclass.cli.validate_runtime_process_context"),
                patch("weightclass.cli.observe_executable", return_value=first),
                patch("weightclass.native_v3_runtime.observe_executable", return_value=first),
                patch("weightclass.native_v3_runtime.run_owned_foreground_redacted") as spawn,
            ):
                try:
                    result = cli.main(
                        self.run_arguments(
                            path,
                            "--confirm-endpoint-transition",
                            "--ack-route-fingerprint",
                            self.fingerprint(first),
                        )
                    )
                except BaseException as error:
                    actual_error = error

        self.assertIsNone(actual_error)
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "invalid_task"})
        self.assertEqual(stream.read_calls, 1)
        spawn.assert_not_called()

    def test_spawn_exception_and_nonzero_status_are_redacted_executor_failures(self) -> None:
        """Breaks if post-spawn failures escape or reuse a child status as router status."""
        first = observation("/opt/grok")
        cases = (
            (OSError("PRIVATE TASK"), {"error": "executor_failed"}),
            (19, {"error": "executor_failed", "executor_exit_code": 19}),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            for outcome, wanted_error in cases:
                with self.subTest(outcome_type=type(outcome).__name__):
                    stream = HostileOneReadStream()
                    errors = io.StringIO()
                    actual_error: BaseException | None = None
                    result: int | None = None
                    with (
                        patch.object(sys, "stdin", stream),
                        patch.object(sys, "stderr", errors),
                        patch("weightclass.cli.validate_runtime_process_context"),
                        patch("weightclass.cli.observe_executable", return_value=first),
                        patch(
                            "weightclass.native_v3_runtime.observe_executable",
                            return_value=first,
                        ),
                        patch(
                            "weightclass.native_v3_runtime.run_owned_foreground_redacted",
                            side_effect=outcome if isinstance(outcome, BaseException) else None,
                            return_value=outcome if isinstance(outcome, int) else None,
                        ) as spawn,
                    ):
                        try:
                            result = cli.main(
                                self.run_arguments(
                                    path,
                                    "--confirm-endpoint-transition",
                                    "--ack-route-fingerprint",
                                    self.fingerprint(first),
                                )
                            )
                        except BaseException as error:
                            actual_error = error

                    self.assertIsNone(actual_error)
                    self.assertEqual(result, 7)
                    self.assertEqual(json.loads(errors.getvalue()), wanted_error)
                    self.assertNotIn("PRIVATE TASK", errors.getvalue())
                    self.assertEqual(stream.read_calls, 1)
                    spawn.assert_called_once()

    def test_endpoint_transition_flag_is_rejected_for_schema_one_and_two(self) -> None:
        """Breaks if the new consent flag changes legacy runtime semantics."""
        schema_one = {
            "schema_version": 1,
            "routes": [
                {
                    "id": "r",
                    "vendor": "codex",
                    "tier": "low",
                    "command": ["/opt/codex", "-"],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            cases = (
                (schema_one, ["--source-vendor", "codex", "--tier", "low"]),
                (
                    valid_v2_policy(),
                    [
                        "--source-vendor",
                        "codex",
                        "--source-profile",
                        "p",
                        "--tier",
                        "low",
                    ],
                ),
            )
            for index, (raw_policy, selector) in enumerate(cases):
                with self.subTest(schema=index + 1):
                    path = self.write_policy(directory, raw_policy)
                    stream = HostileOneReadStream()
                    errors = io.StringIO()
                    with (
                        patch.object(sys, "stdin", stream),
                        patch.object(sys, "stderr", errors),
                        patch(
                            "weightclass.native_v3_runtime.run_owned_foreground_redacted"
                        ) as spawn,
                    ):
                        result = cli.main(
                            [
                                "run",
                                "--policy",
                                str(path),
                                *selector,
                                "--confirm-endpoint-transition",
                            ]
                        )
                    self.assertEqual(result, 2)
                    self.assertEqual(json.loads(errors.getvalue()), {"error": "invalid_input"})
                    self.assertEqual(stream.read_calls, 0)
                    spawn.assert_not_called()

    def test_transition_consent_succeeds_and_same_endpoint_needs_no_consent(self) -> None:
        """Breaks if confirmation parity differs from the reviewed artifact."""
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            cases = (
                ("low", "/opt/grok", ("--confirm-endpoint-transition",)),
                ("high", "/opt/codex", ()),
            )
            for tier, executable, confirmation in cases:
                with self.subTest(tier=tier):
                    stream = HostileOneReadStream()
                    errors = io.StringIO()
                    first = observation(executable)
                    selected = compile_static_native_policy_v3(
                        parse_native_policy_v3(valid_policy()),
                        source_vendor="codex",
                        source_profile_id="source",
                        tier=tier,  # type: ignore[arg-type]
                        purpose="native_route",
                    )
                    fingerprint = bind_native_observation_v3(selected, first)["route_fingerprint"]
                    assert isinstance(fingerprint, str)
                    with (
                        patch.object(sys, "stdin", stream),
                        patch.object(sys, "stderr", errors),
                        patch("weightclass.cli.validate_runtime_process_context"),
                        patch("weightclass.cli.observe_executable", return_value=first),
                        patch(
                            "weightclass.native_v3_runtime.observe_executable",
                            return_value=first,
                        ),
                        patch(
                            "weightclass.native_v3_runtime.run_owned_foreground_redacted",
                            return_value=0,
                        ) as spawn,
                    ):
                        result = cli.main(
                            [
                                "run",
                                "--policy",
                                str(path),
                                "--source-vendor",
                                "codex",
                                "--source-profile",
                                "source",
                                "--tier",
                                tier,
                                *confirmation,
                                "--ack-route-fingerprint",
                                fingerprint,
                            ]
                        )
                    self.assertEqual(result, 0)
                    self.assertEqual(errors.getvalue(), "")
                    self.assertEqual(stream.read_calls, 1)
                    spawn.assert_called_once()

    def test_schema_selector_and_static_selection_fail_before_task_or_spawn(self) -> None:
        """Breaks if invalid input or an unsupported selector reaches runtime gates."""
        cases: tuple[tuple[dict[str, object], list[str], int, str], ...] = (
            (
                {"schema_version": 3},
                [
                    "--source-vendor",
                    "codex",
                    "--source-profile",
                    "source",
                    "--tier",
                    "low",
                ],
                2,
                "invalid_input",
            ),
            (
                valid_policy(),
                [
                    "--source-vendor",
                    "unknown",
                    "--source-profile",
                    "source",
                    "--tier",
                    "low",
                ],
                2,
                "invalid_input",
            ),
            (
                valid_policy(),
                ["--source-vendor", "codex", "--source-profile", "source"],
                2,
                "invalid_input",
            ),
            (
                valid_policy(),
                [
                    "--source-vendor",
                    "codex",
                    "--source-profile",
                    "source",
                    "--tier",
                    "standard",
                ],
                3,
                "unsupported_route",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, (raw_policy, selector, wanted_status, wanted_error) in enumerate(cases):
                with self.subTest(case=index):
                    path = self.write_policy(directory, raw_policy)
                    stream = HostileOneReadStream()
                    errors = io.StringIO()
                    with (
                        patch.object(sys, "stdin", stream),
                        patch.object(sys, "stderr", errors),
                        patch("weightclass.cli.validate_runtime_process_context") as context,
                        patch("weightclass.cli.observe_executable") as observe,
                        patch(
                            "weightclass.native_v3_runtime.run_owned_foreground_redacted"
                        ) as spawn,
                    ):
                        result = cli.main(["run", "--policy", str(path), *selector])
                    self.assertEqual(result, wanted_status)
                    self.assertEqual(json.loads(errors.getvalue()), {"error": wanted_error})
                    self.assertEqual(stream.read_calls, 0)
                    context.assert_not_called()
                    observe.assert_not_called()
                    spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
