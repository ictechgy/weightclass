from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from tests.test_native_v2_cli import policy as valid_v2_policy
from tests.test_native_v3_runtime import policy as builder_policy
from tests.test_native_v3_schema import valid_policy
from weightclass import cli
from weightclass.delegation_runtime import DelegationRuntimeUnavailableError
from weightclass.executable_observation import ExecutableObservation
from weightclass.native_v3_compile import (
    bind_native_observation_v3,
    compile_static_native_policy_v3,
)
from weightclass.native_v3_schema import parse_native_policy_v3
from weightclass.process_context import ChildStatusLostError
from weightclass.v2_validation import V2ValidationError


class HostileOneReadStream:
    def __init__(self, payload: bytes = b"PRIVATE SUBTASK") -> None:
        self.payload = payload
        self.read_calls = 0

    @property
    def buffer(self) -> HostileOneReadStream:
        return self

    def read(self, size: int = -1) -> bytes:
        self.read_calls += 1
        if self.read_calls > 1:
            raise AssertionError("subtask stream read more than once")
        return self.payload if size < 0 else self.payload[:size]


def observation(path: str, *, inode: int = 2) -> ExecutableObservation:
    return ExecutableObservation(path, 1, inode, 0o100000, 0o100755, 100, 3, 4, True)


class NativeV3DelegationCliTests(unittest.TestCase):
    def write_policy(self, directory: str, value: dict[str, object] | None = None) -> Path:
        path = Path(directory) / "policy.json"
        path.write_text(json.dumps(value or valid_policy()), encoding="utf-8")
        return path

    @staticmethod
    def arguments(path: Path, command: str, *extra: str) -> list[str]:
        return NativeV3DelegationCliTests.selector_arguments(
            path, command, "codex", "source", "low", *extra
        )

    @staticmethod
    def selector_arguments(
        path: Path,
        command: str,
        source_vendor: str,
        source_profile: str,
        tier: str,
        *extra: str,
    ) -> list[str]:
        return [
            "delegate",
            "native",
            command,
            "--policy",
            str(path),
            "--source-vendor",
            source_vendor,
            "--source-profile",
            source_profile,
            "--tier",
            tier,
            *extra,
        ]

    @staticmethod
    def fingerprint(
        first: ExecutableObservation,
        *,
        raw_policy: dict[str, object] | None = None,
        source_vendor: str = "codex",
        tier: str = "low",
        purpose: str = "native_delegation",
    ) -> str:
        selected = compile_static_native_policy_v3(
            parse_native_policy_v3(raw_policy or valid_policy()),
            source_vendor=source_vendor,  # type: ignore[arg-type]
            source_profile_id="source",
            tier=tier,  # type: ignore[arg-type]
            purpose=purpose,  # type: ignore[arg-type]
        )
        value = bind_native_observation_v3(selected, first)["route_fingerprint"]
        assert isinstance(value, str)
        return value

    def test_nested_native_route_and_run_reach_schema_three_dispatch(self) -> None:
        """Breaks if the additive nested command is absent or wired to legacy delegation."""
        stream = HostileOneReadStream()
        first = observation("/opt/grok")
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            route_output = io.StringIO()
            route_errors = io.StringIO()
            with (
                patch.object(sys, "stdin", stream),
                patch.object(sys, "stdout", route_output),
                patch.object(sys, "stderr", route_errors),
                patch("weightclass.cli.observe_executable", return_value=first),
            ):
                route_status = cli.main(self.arguments(path, "route"))

            run_errors = io.StringIO()
            with (
                patch.object(sys, "stdin", stream),
                patch.object(sys, "stderr", run_errors),
                patch("weightclass.cli.validate_runtime_process_context") as context,
                patch("weightclass.cli.observe_executable") as observe,
            ):
                run_status = cli.main(self.arguments(path, "run"))

        self.assertEqual(route_status, 0)
        descriptor = json.loads(route_output.getvalue())
        self.assertEqual(descriptor["purpose"], "native_delegation")
        self.assertEqual(
            descriptor["required_confirmations"],
            ["native_delegation", "endpoint_transition"],
        )
        self.assertEqual(descriptor["executable_observation"]["lexical_path"], "/opt/grok")
        self.assertEqual(descriptor["route_fingerprint"], self.fingerprint(first))
        self.assertNotIn("PRIVATE SUBTASK", route_output.getvalue())
        self.assertEqual(route_errors.getvalue(), "")
        self.assertEqual(run_status, 5)
        self.assertEqual(json.loads(run_errors.getvalue()), {"error": "confirmation_required"})
        self.assertEqual(stream.read_calls, 0)
        context.assert_not_called()
        observe.assert_not_called()

    def test_confirmation_then_ack_gates_precede_context_observation_task_and_spawn(self) -> None:
        """Breaks if either consent or exact acknowledgement can be deferred past task access."""
        cases = (
            ((), 5),
            (("--confirm-native-delegation",), 5),
            (
                ("--confirm-native-delegation", "--confirm-endpoint-transition"),
                6,
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            for extra, wanted_status in cases:
                with self.subTest(extra=extra):
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
                        status = cli.main(self.arguments(path, "run", *extra))

                    self.assertEqual(status, wanted_status)
                    self.assertEqual(
                        json.loads(errors.getvalue()),
                        {
                            "error": (
                                "confirmation_required"
                                if wanted_status == 5
                                else "route_fingerprint_mismatch"
                            )
                        },
                    )
                    self.assertEqual(stream.read_calls, 0)
                    context.assert_not_called()
                    observe.assert_not_called()
                    spawn.assert_not_called()

    def test_safe_context_and_first_observation_precede_ack_comparison_and_task(self) -> None:
        """Breaks if unsafe or unavailable execution reaches private subtask input."""
        first = observation("/opt/grok")
        cases = (
            (DelegationRuntimeUnavailableError(), None, 4, "executor_unavailable", 0),
            (None, OSError("PRIVATE PATH"), 4, "executor_unavailable", 1),
            (None, first, 6, "route_fingerprint_mismatch", 1),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            for context_error, observed, wanted_status, wanted_error, observe_calls in cases:
                with self.subTest(wanted_status=wanted_status, observe_calls=observe_calls):
                    stream = HostileOneReadStream()
                    errors = io.StringIO()
                    with (
                        patch.object(sys, "stdin", stream),
                        patch.object(sys, "stderr", errors),
                        patch(
                            "weightclass.cli.validate_runtime_process_context",
                            side_effect=context_error,
                        ),
                        patch(
                            "weightclass.cli.observe_executable",
                            side_effect=observed if isinstance(observed, BaseException) else None,
                            return_value=(
                                observed if not isinstance(observed, BaseException) else None
                            ),
                        ) as observe,
                        patch(
                            "weightclass.native_v3_runtime.run_owned_foreground_redacted"
                        ) as spawn,
                    ):
                        status = cli.main(
                            self.arguments(
                                path,
                                "run",
                                "--confirm-native-delegation",
                                "--confirm-endpoint-transition",
                                "--ack-route-fingerprint",
                                "sha256:wrong",
                            )
                        )

                    self.assertEqual(status, wanted_status)
                    self.assertEqual(json.loads(errors.getvalue()), {"error": wanted_error})
                    self.assertEqual(stream.read_calls, 0)
                    self.assertEqual(observe.call_count, observe_calls)
                    spawn.assert_not_called()

    def test_ordinary_native_route_fingerprint_cannot_authorize_delegation(self) -> None:
        """Breaks if the purpose field is omitted from the reviewed fingerprint."""
        stream = HostileOneReadStream()
        errors = io.StringIO()
        first = observation("/opt/grok")
        ordinary_fingerprint = self.fingerprint(first, purpose="native_route")
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            with (
                patch.object(sys, "stdin", stream),
                patch.object(sys, "stderr", errors),
                patch("weightclass.cli.validate_runtime_process_context"),
                patch("weightclass.cli.observe_executable", return_value=first),
                patch("weightclass.native_v3_runtime.run_owned_foreground_redacted") as spawn,
            ):
                status = cli.main(
                    self.arguments(
                        path,
                        "run",
                        "--confirm-native-delegation",
                        "--confirm-endpoint-transition",
                        "--ack-route-fingerprint",
                        ordinary_fingerprint,
                    )
                )

        self.assertEqual(status, 6)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "route_fingerprint_mismatch"})
        self.assertEqual(stream.read_calls, 0)
        spawn.assert_not_called()

    def test_four_builders_deliver_one_exact_subtask_to_one_redacted_spawn(self) -> None:
        """Breaks if nested dispatch changes builder delivery or starts multiple children."""
        task_bytes = "검토 exact".encode()
        expected: dict[str, tuple[tuple[str, ...], bytes]] = {
            "codex": (
                (
                    "/opt/codex",
                    "exec",
                    "--ephemeral",
                    "--sandbox",
                    "workspace-write",
                    "--model",
                    "opaque-model",
                    "-c",
                    "model_reasoning_effort=low",
                    "-",
                ),
                task_bytes,
            ),
            "claude": (
                (
                    "/opt/claude",
                    "--print",
                    "--no-session-persistence",
                    "--permission-mode",
                    "acceptEdits",
                    "--model",
                    "opaque-model",
                    "--effort",
                    "low",
                ),
                task_bytes,
            ),
            "agy": (
                (
                    "/opt/agy",
                    "--print",
                    "검토 exact",
                    "--mode",
                    "accept-edits",
                    "--effort",
                    "low",
                ),
                b"",
            ),
            "grok": (
                (
                    "/opt/grok",
                    "-p",
                    "검토 exact",
                    "--permission-mode",
                    "acceptEdits",
                    "--model",
                    "opaque-model",
                    "--reasoning-effort",
                    "low",
                ),
                b"",
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            for vendor, wanted in expected.items():
                with self.subTest(vendor=vendor):
                    raw_policy = builder_policy(vendor)
                    path = self.write_policy(directory, raw_policy)
                    stream = HostileOneReadStream(task_bytes)
                    errors = io.StringIO()
                    first = observation(f"/opt/{vendor}")
                    captured: list[Any] = []

                    def spawn(invocation: Any, sink: list[Any] = captured) -> int:
                        sink.append(invocation)
                        return 0

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
                            side_effect=spawn,
                        ),
                    ):
                        status = cli.main(
                            self.selector_arguments(
                                path,
                                "run",
                                vendor,
                                "source",
                                "low",
                                "--confirm-native-delegation",
                                "--ack-route-fingerprint",
                                self.fingerprint(
                                    first,
                                    raw_policy=raw_policy,
                                    source_vendor=vendor,
                                ),
                            )
                        )

                    self.assertEqual(status, 0)
                    self.assertEqual(errors.getvalue(), "")
                    self.assertEqual(stream.read_calls, 1)
                    self.assertEqual(len(captured), 1)
                    self.assertEqual(captured[0]._arguments, wanted[0])
                    self.assertEqual(captured[0]._input_bytes, wanted[1])

    def test_invalid_task_and_final_observation_fail_after_exactly_one_read(self) -> None:
        """Breaks if post-ack failures reread a subtask or start an unreviewed child."""
        first = observation("/opt/codex")
        cases = (
            (b"\xffPRIVATE", None, 2, "invalid_task"),
            (b"PRIVATE SUBTASK", OSError("PRIVATE PATH"), 4, "executor_unavailable"),
            (
                b"PRIVATE SUBTASK",
                observation("/opt/codex", inode=9),
                6,
                "route_fingerprint_mismatch",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            for payload, final, wanted_status, wanted_error in cases:
                with self.subTest(wanted_error=wanted_error):
                    stream = HostileOneReadStream(payload)
                    errors = io.StringIO()
                    final_side_effect = final if isinstance(final, BaseException) else None
                    final_value = final if isinstance(final, ExecutableObservation) else first
                    with (
                        patch.object(sys, "stdin", stream),
                        patch.object(sys, "stderr", errors),
                        patch("weightclass.cli.validate_runtime_process_context"),
                        patch("weightclass.cli.observe_executable", return_value=first),
                        patch(
                            "weightclass.native_v3_runtime.observe_executable",
                            side_effect=final_side_effect,
                            return_value=final_value,
                        ),
                        patch(
                            "weightclass.native_v3_runtime.run_owned_foreground_redacted"
                        ) as spawn,
                    ):
                        status = cli.main(
                            self.selector_arguments(
                                path,
                                "run",
                                "codex",
                                "source",
                                "high",
                                "--confirm-native-delegation",
                                "--ack-route-fingerprint",
                                self.fingerprint(first, tier="high"),
                            )
                        )

                    self.assertEqual(status, wanted_status)
                    self.assertEqual(json.loads(errors.getvalue()), {"error": wanted_error})
                    self.assertNotIn("PRIVATE", errors.getvalue())
                    self.assertEqual(stream.read_calls, 1)
                    spawn.assert_not_called()

    def test_argv_subtasks_reject_nul_utf8_overflow_and_grok_leading_dash(self) -> None:
        """Breaks if argv-only delegated tasks bypass the reviewed materialization bounds."""
        cases = (
            ("agy", b"has\x00nul"),
            ("agy", ("🙂" * 8_193).encode()),
            ("grok", b"-option-like"),
        )
        with tempfile.TemporaryDirectory() as directory:
            for vendor, task_bytes in cases:
                with self.subTest(vendor=vendor, size=len(task_bytes)):
                    raw_policy = builder_policy(vendor)
                    path = self.write_policy(directory, raw_policy)
                    stream = HostileOneReadStream(task_bytes)
                    errors = io.StringIO()
                    first = observation(f"/opt/{vendor}")
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
                            "weightclass.native_v3_runtime.run_owned_foreground_redacted"
                        ) as spawn,
                    ):
                        status = cli.main(
                            self.selector_arguments(
                                path,
                                "run",
                                vendor,
                                "source",
                                "low",
                                "--confirm-native-delegation",
                                "--ack-route-fingerprint",
                                self.fingerprint(
                                    first,
                                    raw_policy=raw_policy,
                                    source_vendor=vendor,
                                ),
                            )
                        )

                    self.assertEqual(status, 2)
                    self.assertEqual(json.loads(errors.getvalue()), {"error": "invalid_task"})
                    self.assertEqual(stream.read_calls, 1)
                    spawn.assert_not_called()

    def test_schema_and_selection_fail_closed_before_subtask_or_process_access(self) -> None:
        """Breaks if nested native delegation accepts another schema or ambiguous route."""
        cases: tuple[tuple[dict[str, object], str, int, str], ...] = (
            ({"routes": []}, "low", 2, "invalid_input"),
            (valid_v2_policy(), "low", 2, "invalid_input"),
            ({"schema_version": 3}, "low", 2, "invalid_input"),
            (valid_policy(), "standard", 3, "unsupported_route"),
        )
        with tempfile.TemporaryDirectory() as directory:
            for raw_policy, tier, wanted_status, wanted_error in cases:
                for command in ("route", "run"):
                    with self.subTest(
                        command=command,
                        wanted_error=wanted_error,
                        tier=tier,
                    ):
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
                            status = cli.main(
                                self.selector_arguments(
                                    path,
                                    command,
                                    "codex",
                                    "source",
                                    tier,
                                )
                            )

                        self.assertEqual(status, wanted_status)
                        self.assertEqual(json.loads(errors.getvalue()), {"error": wanted_error})
                        self.assertEqual(stream.read_calls, 0)
                        context.assert_not_called()
                        observe.assert_not_called()
                        spawn.assert_not_called()

    def test_route_observation_unavailable_is_exit_four_without_subtask_access(self) -> None:
        """Breaks if review pretends a missing executable is a supported bound route."""
        stream = HostileOneReadStream()
        errors = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            with (
                patch.object(sys, "stdin", stream),
                patch.object(sys, "stderr", errors),
                patch("weightclass.cli.observe_executable", side_effect=V2ValidationError()),
            ):
                status = cli.main(self.arguments(path, "route"))

        self.assertEqual(status, 4)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "executor_unavailable"})
        self.assertEqual(stream.read_calls, 0)

    def test_lost_child_status_is_a_redacted_executor_failure(self) -> None:
        """Breaks if direct-child status loss escapes instead of mapping to exit 7."""
        stream = HostileOneReadStream()
        errors = io.StringIO()
        first = observation("/opt/codex")
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            with (
                patch.object(sys, "stdin", stream),
                patch.object(sys, "stderr", errors),
                patch("weightclass.cli.validate_runtime_process_context"),
                patch("weightclass.cli.observe_executable", return_value=first),
                patch("weightclass.native_v3_runtime.observe_executable", return_value=first),
                patch(
                    "weightclass.native_v3_runtime.run_owned_foreground_redacted",
                    side_effect=ChildStatusLostError("PRIVATE SUBTASK"),
                ),
            ):
                status = cli.main(
                    self.arguments(
                        path,
                        "run",
                        "--tier",
                        "high",
                        "--confirm-native-delegation",
                        "--ack-route-fingerprint",
                        self.fingerprint(first, tier="high"),
                    )
                )

        self.assertEqual(status, 7)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "executor_failed"})
        self.assertNotIn("PRIVATE SUBTASK", errors.getvalue())
        self.assertEqual(stream.read_calls, 1)

    def test_spawn_exception_and_nonzero_status_are_redacted_exit_seven(self) -> None:
        """Breaks if one-child spawn/status failures escape or reuse child status."""
        first = observation("/opt/codex")
        cases = (
            (OSError("PRIVATE SUBTASK"), {"error": "executor_failed"}),
            (19, {"error": "executor_failed", "executor_exit_code": 19}),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            for outcome, wanted_error in cases:
                with self.subTest(outcome_type=type(outcome).__name__):
                    stream = HostileOneReadStream()
                    errors = io.StringIO()
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
                        status = cli.main(
                            self.selector_arguments(
                                path,
                                "run",
                                "codex",
                                "source",
                                "high",
                                "--confirm-native-delegation",
                                "--ack-route-fingerprint",
                                self.fingerprint(first, tier="high"),
                            )
                        )

                    self.assertEqual(status, 7)
                    self.assertEqual(json.loads(errors.getvalue()), wanted_error)
                    self.assertNotIn("PRIVATE SUBTASK", errors.getvalue())
                    self.assertEqual(stream.read_calls, 1)
                    spawn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
