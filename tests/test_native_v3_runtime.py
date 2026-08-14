import dataclasses
import importlib
import importlib.util
import io
import unittest
from typing import Any
from unittest.mock import patch

from weightclass.executable_observation import ExecutableObservation
from weightclass.native_v3_compile import compile_static_native_policy_v3
from weightclass.native_v3_schema import parse_native_policy_v3
from weightclass.task_v2 import read_validated_task_v2
from weightclass.v2_validation import V2ValidationError


def policy(vendor: str) -> dict[str, object]:
    model = None if vendor == "agy" else "opaque-model"
    builder = {
        "agy": "agy-print-v1",
        "claude": "claude-print-v1",
        "codex": "codex-exec-v1",
        "grok": "grok-print-v1",
    }[vendor]
    return {
        "schema_version": 3,
        "profiles": [{"id": "source", "vendor": vendor, "account_profile": "account"}],
        "execution_targets": [
            {
                "id": "target",
                "profile_id": "source",
                "vendor": vendor,
                "executable": f"/opt/{vendor}",
                "builder": {"kind": builder, "version": 1},
                "allowed_model_effort_pairs": [{"model": model, "effort": "low"}],
            }
        ],
        "routes": [
            {
                "id": "route",
                "source_profile_id": "source",
                "tier": "low",
                "target_id": "target",
                "model": model,
                "effort": "low",
            }
        ],
        "profile_grants": [],
        "vendor_grants": [],
    }


def observation(path: str, *, inode: int = 2) -> ExecutableObservation:
    return ExecutableObservation(path, 1, inode, 0o100000, 0o100755, 100, 3, 4, True)


class NativeV3RuntimeTests(unittest.TestCase):
    def test_four_builders_deliver_exact_task_only_at_the_final_spawn_seam(self) -> None:
        """Breaks if a builder changes stdin/argv delivery or exact task bytes."""
        self.assertIsNotNone(importlib.util.find_spec("weightclass.native_v3_runtime"))
        runtime = importlib.import_module("weightclass.native_v3_runtime")
        run_native = getattr(runtime, "run_native_v3", None)
        self.assertIsNotNone(run_native)
        assert run_native is not None
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
        for vendor, wanted in expected.items():
            with self.subTest(vendor=vendor):
                selected = compile_static_native_policy_v3(
                    parse_native_policy_v3(policy(vendor)),
                    source_vendor=vendor,  # type: ignore[arg-type]
                    source_profile_id="source",
                    tier="low",
                    purpose="native_route",
                )
                first = observation(f"/opt/{vendor}")
                captured: list[Any] = []

                def spawn(invocation: Any, sink: list[Any] = captured) -> int:
                    sink.append(invocation)
                    return 0

                with (
                    patch.object(runtime, "observe_executable", return_value=first),
                    patch.object(runtime, "run_owned_foreground_redacted", side_effect=spawn),
                ):
                    status = run_native(
                        selected,
                        read_validated_task_v2(io.BytesIO(task_bytes)),
                        first,
                    )

                self.assertEqual(status, 0)
                self.assertEqual(len(captured), 1)
                invocation = captured[0]
                self.assertEqual(invocation._arguments, wanted[0])
                self.assertEqual(invocation._input_bytes, wanted[1])

    def test_final_executable_drift_stops_before_materialization_and_spawn(self) -> None:
        """Breaks if a changed lstat identity can reach the child boundary."""
        runtime = importlib.import_module("weightclass.native_v3_runtime")
        selected = compile_static_native_policy_v3(
            parse_native_policy_v3(policy("codex")),
            source_vendor="codex",
            source_profile_id="source",
            tier="low",
            purpose="native_route",
        )
        first = observation("/opt/codex")
        with (
            patch.object(
                runtime,
                "observe_executable",
                return_value=observation("/opt/codex", inode=9),
            ),
            patch.object(runtime, "run_owned_foreground_redacted") as spawn,
            self.assertRaises(runtime.NativeV3FingerprintMismatchError),
        ):
            runtime.run_native_v3(
                selected,
                read_validated_task_v2(io.BytesIO(b"PRIVATE TASK")),
                first,
            )
        spawn.assert_not_called()

    def test_unavailable_final_observation_stops_before_spawn_with_redacted_error(self) -> None:
        """Breaks if a final lstat failure is mistaken for drift or starts a child."""
        runtime = importlib.import_module("weightclass.native_v3_runtime")
        selected = compile_static_native_policy_v3(
            parse_native_policy_v3(policy("codex")),
            source_vendor="codex",
            source_profile_id="source",
            tier="low",
            purpose="native_route",
        )
        for unavailable in (
            V2ValidationError("PRIVATE PATH"),
            OSError("PRIVATE PATH"),
            ValueError("PRIVATE PATH"),
        ):
            with self.subTest(error_type=type(unavailable).__name__):
                actual_error: BaseException | None = None
                with (
                    patch.object(runtime, "observe_executable", side_effect=unavailable),
                    patch.object(runtime, "run_owned_foreground_redacted") as spawn,
                ):
                    try:
                        runtime.run_native_v3(
                            selected,
                            read_validated_task_v2(io.BytesIO(b"PRIVATE TASK")),
                            observation("/opt/codex"),
                        )
                    except BaseException as error:
                        actual_error = error
                self.assertIs(type(actual_error), runtime.NativeV3ExecutorUnavailableError)
                self.assertEqual(str(actual_error), "")
                spawn.assert_not_called()

    def test_argv_tasks_reject_nul_utf8_overflow_and_grok_leading_dash(self) -> None:
        """Breaks if argv-only builders materialize an unsafe prompt token."""
        runtime = importlib.import_module("weightclass.native_v3_runtime")
        cases = (
            ("agy", b"has\x00nul"),
            ("agy", ("\U0001f642" * 8_193).encode()),
            ("grok", b"-option-like"),
        )
        for vendor, task_bytes in cases:
            with self.subTest(vendor=vendor, task_size=len(task_bytes)):
                selected = compile_static_native_policy_v3(
                    parse_native_policy_v3(policy(vendor)),
                    source_vendor=vendor,  # type: ignore[arg-type]
                    source_profile_id="source",
                    tier="low",
                    purpose="native_route",
                )
                first = observation(f"/opt/{vendor}")
                with (
                    patch.object(runtime, "observe_executable", return_value=first),
                    patch.object(runtime, "run_owned_foreground_redacted") as spawn,
                    self.assertRaisesRegex(V2ValidationError, "^$"),
                ):
                    runtime.run_native_v3(
                        selected,
                        read_validated_task_v2(io.BytesIO(task_bytes)),
                        first,
                    )
                spawn.assert_not_called()

    def test_materialized_argv_enforces_token_count_token_bytes_and_aggregate_bytes(self) -> None:
        """Breaks if a forged or regressed template can exceed exec bounds."""
        runtime = importlib.import_module("weightclass.native_v3_runtime")
        selected = compile_static_native_policy_v3(
            parse_native_policy_v3(policy("codex")),
            source_vendor="codex",
            source_profile_id="source",
            tier="low",
            purpose="native_route",
        )
        invalid_templates = (
            tuple("x" for _ in range(33)),
            ("x" * 32_769,),
            ("x" * 25_000, "y" * 25_000),
        )
        first = observation("/opt/codex")
        for template in invalid_templates:
            with self.subTest(tokens=len(template)):
                forged = dataclasses.replace(selected, argv_template=template)
                with (
                    patch.object(runtime, "observe_executable", return_value=first),
                    patch.object(runtime, "run_owned_foreground_redacted") as spawn,
                    self.assertRaisesRegex(V2ValidationError, "^$"),
                ):
                    runtime.run_native_v3(
                        forged,
                        read_validated_task_v2(io.BytesIO(b"PRIVATE TASK")),
                        first,
                    )
                spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
