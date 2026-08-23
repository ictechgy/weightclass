import contextlib
import io
import json
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest import mock

from weightclass import cli
from weightclass.process_context import ChildStatusLostError
from weightclass.router import RouteSelectionError
from weightclass.v2 import (
    API_SOURCE_VENDORS,
    SOURCE_PROVIDER,
    ApiRoute,
    ApiRoutingPolicy,
    load_api_policy,
    observe_api_runtime,
    route_fingerprint,
    select_api_route,
)


def _api_policy(**overrides: object) -> dict[str, object]:
    """Build a minimal single-route V2 policy for one test."""
    policy: dict[str, object] = {
        "schema_version": 2,
        "allow_cross_provider": False,
        "allow_api": True,
        "routes": [
            {
                "id": "openai-low-api",
                "tier": "low",
                "eligible_source_vendors": ["codex"],
                "provider": "openai",
                "transport": "api",
                "model": "opaque-openai-low-model",
                "effort": "low",
                "intended_recipient": "OpenAI API",
                "intended_billing_boundary": "user OpenAI API account",
            }
        ],
    }
    policy.update(overrides)
    return policy


def _private_runtime(directory: Path) -> Path:
    runtime = directory / "private-api-runtime"
    runtime.write_text(
        f"#!{sys.executable}\nimport sys\nsys.stdin.buffer.read()\n",
        encoding="utf-8",
    )
    runtime.chmod(0o700)
    return runtime


class V2EgressGateTests(unittest.TestCase):
    """Egress를 막는 각 게이트가 실제로 단독으로 동작하는지 고정한다."""

    def _review(self, policy: dict[str, object], runtime: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            reviewed_runtime = (
                str(_private_runtime(directory)) if runtime == sys.executable else runtime
            )
            return subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "v2",
                    "route",
                    "--policy",
                    str(policy_path),
                    "--source-vendor",
                    "codex",
                    "--api-runtime",
                    reviewed_runtime,
                ],
                capture_output=True,
                check=False,
                input="Fix a typo.",
                text=True,
            )

    def test_allow_api_false_blocks_every_route(self) -> None:
        """Breaks if the policy-wide API kill switch stops being consulted."""
        result = self._review(_api_policy(allow_api=False), sys.executable)

        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stderr), {"error": "unsupported_route"})

    def test_rejects_a_label_carrying_invisible_characters(self) -> None:
        """Breaks if a V2 label may hold a character the review descriptor cannot show.

        model 과 effort 는 런타임에 argv 로 전달된다. 서로게이트는 exec 단계에서
        UnicodeEncodeError 로 터지고, 서식 문자는 검토 출력에 드러나지 않는다.
        """
        invisible_labels = (
            "\ud800",  # lone surrogate
            "a​b",  # zero-width space
            "a‮b",  # RTL override
            "ab",  # C1 control
            "a b",  # NBSP
        )
        for label in invisible_labels:
            with self.subTest(label=ascii(label)):
                policy = _api_policy()
                routes = policy["routes"]
                assert isinstance(routes, list)
                routes[0]["model"] = label

                result = self._review(policy, sys.executable)

                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})
                self.assertNotIn("Traceback", result.stderr)

    def test_rejects_a_relative_api_runtime_path(self) -> None:
        """Breaks if a runtime can be resolved through the caller's working directory."""
        result = self._review(_api_policy(), "python3")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})

    def test_rejects_a_non_executable_api_runtime(self) -> None:
        """Breaks if an ordinary data file can be named as the provider runtime."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            runtime_path = Path(temporary_directory) / "not-executable"
            runtime_path.write_text("#!/bin/sh\n", encoding="utf-8")

            result = self._review(_api_policy(), str(runtime_path))

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})

    def test_rejects_a_cyclic_api_runtime_without_leaking_its_path(self) -> None:
        """Breaks if a supported Python runtime prints a symlink-loop traceback."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            first_link = directory / "runtime-loop-a"
            second_link = directory / "runtime-loop-b"
            first_link.symlink_to(second_link.name)
            second_link.symlink_to(first_link.name)

            result = self._review(_api_policy(), str(first_link))

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})
        self.assertNotIn(str(first_link), result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_hides_runtime_startup_details_when_the_runtime_cannot_execute(self) -> None:
        """Breaks if a failed runtime launch leaks its path or a traceback."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            policy_path.write_text(json.dumps(_api_policy()), encoding="utf-8")
            # 실행 비트는 있지만 실행 가능한 형식이 아니므로 spawn 단계에서 OSError 가 난다.
            runtime_path = directory / "unlaunchable-runtime"
            runtime_path.write_bytes(b"\x7fnot-a-real-executable\n")
            runtime_path.chmod(runtime_path.stat().st_mode | stat.S_IXUSR)
            arguments = [
                sys.executable,
                "-m",
                "weightclass",
                "v2",
                "run",
                "--policy",
                str(policy_path),
                "--source-vendor",
                "codex",
                "--api-runtime",
                str(runtime_path),
            ]
            review = subprocess.run(
                arguments[:4] + ["route"] + arguments[5:],
                capture_output=True,
                check=False,
                input="Fix a typo.",
                text=True,
            )
            self.assertEqual(review.returncode, 0, review.stderr)
            result = subprocess.run(
                arguments
                + [
                    "--confirm-api-egress",
                    "--ack-route-fingerprint",
                    json.loads(review.stdout)["route_fingerprint"],
                ],
                capture_output=True,
                check=False,
                input="Fix a typo.",
                text=True,
            )

        self.assertEqual(result.returncode, 4)
        self.assertEqual(json.loads(result.stderr), {"error": "executor_unavailable"})
        self.assertNotIn("unlaunchable-runtime", result.stderr)

    def test_missing_egress_confirmation_does_not_consume_the_task(self) -> None:
        """Breaks if an unconfirmed API run drains stdin before it fails closed."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            policy_path.write_text(json.dumps(_api_policy()), encoding="utf-8")
            errors = io.StringIO()
            with (
                mock.patch(
                    "weightclass.cli.read_task_from_standard_input", return_value="Fix a typo."
                ) as reader,
                contextlib.redirect_stderr(errors),
            ):
                exit_code = cli.v2_run_from_standard_input(
                    policy_path,
                    "codex",
                    Path(sys.executable),
                    False,
                    None,
                )

        self.assertEqual(exit_code, 5)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "api_confirmation_required"})
        reader.assert_not_called()

    def test_missing_egress_confirmation_precedes_runtime_validation(self) -> None:
        """Breaks if an unconfirmed run observes a user-supplied runtime first."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            policy_path.write_text(json.dumps(_api_policy()), encoding="utf-8")
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                exit_code = cli.v2_run_from_standard_input(
                    policy_path,
                    "codex",
                    directory / "missing-runtime",
                    False,
                    None,
                )

        self.assertEqual(exit_code, 5)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "api_confirmation_required"})

    def test_missing_fingerprint_precedes_process_context_runtime_and_task_access(self) -> None:
        """Breaks if a run that cannot be bound touches runtime state or task input."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            policy_path.write_text(json.dumps(_api_policy()), encoding="utf-8")
            runtime_path = _private_runtime(directory)
            observed = observe_api_runtime(runtime_path)
            errors = io.StringIO()
            with (
                mock.patch("weightclass.cli.validate_runtime_process_context") as context_check,
                mock.patch("weightclass.cli.observe_api_runtime", return_value=observed) as inspect,
                mock.patch(
                    "weightclass.cli.read_task_from_standard_input", return_value="Fix a typo."
                ) as reader,
                contextlib.redirect_stderr(errors),
            ):
                exit_code = cli.v2_run_from_standard_input(
                    policy_path,
                    "codex",
                    runtime_path,
                    True,
                    None,
                )

        self.assertEqual(exit_code, 6)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "route_fingerprint_mismatch"})
        context_check.assert_not_called()
        inspect.assert_not_called()
        reader.assert_not_called()

    @unittest.skipUnless(hasattr(signal, "SIGCHLD"), "requires SIGCHLD")
    def test_auto_reaping_context_stops_api_run_before_task_input(self) -> None:
        """Breaks if a failed API runtime can be reported as a successful run."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            policy_path.write_text(json.dumps(_api_policy()), encoding="utf-8")
            runtime_path = directory / "failing-runtime"
            runtime_path.write_text(f"#!{sys.executable}\nraise SystemExit(17)\n", encoding="utf-8")
            runtime_path.chmod(runtime_path.stat().st_mode | stat.S_IXUSR)
            review = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "v2",
                    "route",
                    "--policy",
                    str(policy_path),
                    "--source-vendor",
                    "codex",
                    "--api-runtime",
                    str(runtime_path),
                ],
                capture_output=True,
                check=False,
                input="Fix a typo.",
                text=True,
            )
            self.assertEqual(review.returncode, 0, review.stderr)
            errors = io.StringIO()
            previous_sigchld = signal.signal(signal.SIGCHLD, signal.SIG_IGN)
            try:
                with (
                    mock.patch(
                        "weightclass.cli.read_task_from_standard_input", return_value="Fix a typo."
                    ) as reader,
                    contextlib.redirect_stderr(errors),
                ):
                    exit_code = cli.v2_run_from_standard_input(
                        policy_path,
                        "codex",
                        runtime_path,
                        True,
                        json.loads(review.stdout)["route_fingerprint"],
                    )
            finally:
                signal.signal(signal.SIGCHLD, previous_sigchld)

        self.assertEqual(exit_code, 4)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "executor_unavailable"})
        reader.assert_not_called()

    def test_refuses_a_runtime_replaced_after_review(self) -> None:
        """Breaks if an acknowledged API route can start replacement runtime bytes."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            policy_path.write_text(json.dumps(_api_policy()), encoding="utf-8")
            runtime_path = directory / "runtime"
            runtime_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            runtime_path.chmod(runtime_path.stat().st_mode | stat.S_IXUSR)
            review_arguments = [
                sys.executable,
                "-m",
                "weightclass",
                "v2",
                "route",
                "--policy",
                str(policy_path),
                "--source-vendor",
                "codex",
                "--api-runtime",
                str(runtime_path),
            ]
            review = subprocess.run(
                review_arguments,
                capture_output=True,
                check=False,
                input="Fix a typo.",
                text=True,
            )
            self.assertEqual(review.returncode, 0, review.stderr)
            replacement_marker = directory / "replacement-ran"
            runtime_path.write_text(
                f"#!{sys.executable}\n"
                "from pathlib import Path\n"
                f"Path({str(replacement_marker)!r}).write_text('ran', encoding='utf-8')\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                review_arguments[:4]
                + ["run"]
                + review_arguments[5:]
                + [
                    "--confirm-api-egress",
                    "--ack-route-fingerprint",
                    json.loads(review.stdout)["route_fingerprint"],
                ],
                capture_output=True,
                check=False,
                input="Fix a typo.",
                text=True,
            )

            replacement_started = replacement_marker.exists()

        self.assertEqual(result.returncode, 6)
        self.assertEqual(json.loads(result.stderr), {"error": "route_fingerprint_mismatch"})
        self.assertFalse(replacement_started)

    def test_refuses_a_runtime_changed_after_fingerprint_comparison(self) -> None:
        """Breaks if the final identity check can launch bytes changed mid-run."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            policy_path.write_text(json.dumps(_api_policy()), encoding="utf-8")
            runtime_path = directory / "runtime"
            replacement_marker = directory / "replacement-ran"
            runtime_path.write_text(
                f"#!{sys.executable}\n"
                "from pathlib import Path\n"
                f"Path({str(replacement_marker)!r}).write_text('ran', encoding='utf-8')\n",
                encoding="utf-8",
            )
            runtime_path.chmod(runtime_path.stat().st_mode | stat.S_IXUSR)
            review_arguments = [
                sys.executable,
                "-m",
                "weightclass",
                "v2",
                "route",
                "--policy",
                str(policy_path),
                "--source-vendor",
                "codex",
                "--api-runtime",
                str(runtime_path),
            ]
            review = subprocess.run(
                review_arguments,
                capture_output=True,
                check=False,
                input="Fix a typo.",
                text=True,
            )
            self.assertEqual(review.returncode, 0, review.stderr)
            original_observation = observe_api_runtime(runtime_path)
            changed_observation = replace(
                original_observation,
                size=original_observation.size + 1,
            )
            errors = io.StringIO()
            with (
                mock.patch(
                    "weightclass.cli.observe_api_runtime",
                    side_effect=(original_observation, changed_observation),
                ),
                mock.patch(
                    "weightclass.cli.read_task_from_standard_input",
                    return_value="Fix a typo.",
                ) as reader,
                contextlib.redirect_stderr(errors),
            ):
                exit_code = cli.v2_run_from_standard_input(
                    policy_path,
                    "codex",
                    runtime_path,
                    True,
                    json.loads(review.stdout)["route_fingerprint"],
                )

            replacement_started = replacement_marker.exists()

        self.assertEqual(exit_code, 6)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "route_fingerprint_mismatch"})
        reader.assert_called_once_with()
        self.assertFalse(replacement_started)


class V2ExecutorResultTests(unittest.TestCase):
    """V2 도 자식의 종료 상태를 라우터 진단 코드와 섞지 않아야 한다."""

    def _run_acknowledged_route(
        self, runtime_body: str, task: str = "Fix a typo."
    ) -> subprocess.CompletedProcess[str]:
        """Review and then run a V2 route served by a caller-supplied fake runtime."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            policy_path.write_text(json.dumps(_api_policy()), encoding="utf-8")
            runtime_path = directory / "fake-runtime"
            runtime_path.write_text(f"#!{sys.executable}\n{runtime_body}", encoding="utf-8")
            runtime_path.chmod(runtime_path.stat().st_mode | stat.S_IXUSR)
            arguments = [
                sys.executable,
                "-m",
                "weightclass",
                "v2",
                "run",
                "--policy",
                str(policy_path),
                "--source-vendor",
                "codex",
                "--api-runtime",
                str(runtime_path),
            ]
            review = subprocess.run(
                arguments[:4] + ["route"] + arguments[5:],
                capture_output=True,
                check=False,
                input=task,
                text=True,
            )
            self.assertEqual(review.returncode, 0, review.stderr)
            return subprocess.run(
                arguments
                + [
                    "--confirm-api-egress",
                    "--ack-route-fingerprint",
                    json.loads(review.stdout)["route_fingerprint"],
                ],
                capture_output=True,
                check=False,
                input=task,
                text=True,
            )

    def test_lost_child_status_is_executor_failed_not_unavailable(self) -> None:
        """Breaks if ECHILD after API spawn is mapped to launch failure or success."""
        task = "Fix a typo."
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            policy_path.write_text(json.dumps(_api_policy()), encoding="utf-8")
            runtime_path = _private_runtime(directory)
            observed = observe_api_runtime(runtime_path)
            reviewed_runtime_path = Path(observed.lexical_path)
            policy = load_api_policy(policy_path)
            tier, route = select_api_route(task, policy, "codex")
            fingerprint = route_fingerprint(
                route,
                policy,
                tier,
                "codex",
                reviewed_runtime_path,
                observed,
            )
            errors = io.StringIO()
            raised = ChildStatusLostError()
            with (
                mock.patch("weightclass.cli.observe_api_runtime", return_value=observed),
                mock.patch.object(cli, "run_owned_foreground", side_effect=raised),
                mock.patch("weightclass.cli.read_task_from_standard_input", return_value=task),
                contextlib.redirect_stderr(errors),
            ):
                exit_code = cli.v2_run_from_standard_input(
                    policy_path,
                    "codex",
                    runtime_path,
                    True,
                    fingerprint,
                )

        self.assertEqual(exit_code, 7)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "executor_failed"})

    def test_reports_a_failing_runtime_without_colliding_with_router_codes(self) -> None:
        """Breaks if a runtime's exit status can be mistaken for a router diagnostic."""
        task = "Fix a typo in the glimmerfast heading."
        for runtime_exit_code in (1, 3, 6):
            with self.subTest(runtime_exit_code=runtime_exit_code):
                result = self._run_acknowledged_route(
                    f"import sys\nsys.stdin.buffer.read()\nraise SystemExit({runtime_exit_code})\n",
                    task=task,
                )

                self.assertEqual(result.returncode, 7)
                self.assertEqual(
                    json.loads(result.stderr),
                    {"error": "executor_failed", "executor_exit_code": runtime_exit_code},
                )
                self.assertNotIn("glimmerfast", result.stderr)

    def test_reports_a_runtime_killed_by_a_signal_as_a_signal(self) -> None:
        """Breaks if a signal death is folded into an ordinary exit status."""
        result = self._run_acknowledged_route(
            "import os, signal, sys\n"
            "sys.stdin.buffer.read()\n"
            "os.kill(os.getpid(), signal.SIGTERM)\n"
        )

        self.assertEqual(result.returncode, 7)
        self.assertEqual(
            json.loads(result.stderr),
            {"error": "executor_failed", "executor_signal": 15},
        )

    def test_passes_through_a_successful_runtime(self) -> None:
        """Breaks if a successful V2 run stops reporting success."""
        result = self._run_acknowledged_route(
            "import sys\nsys.stdin.buffer.read()\nprint('runtime-done')\n"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "runtime-done\n")
        self.assertEqual(result.stderr, "")


class V2CommandLineTests(unittest.TestCase):
    def test_renders_a_reviewable_api_route_without_echoing_the_task(self) -> None:
        """Breaks if V2 no longer exposes the selected API destination safely."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            runtime_path = _private_runtime(directory)
            policy_path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "allow_cross_provider": False,
                        "allow_api": True,
                        "routes": [
                            {
                                "id": "openai-high-api",
                                "tier": "high",
                                "eligible_source_vendors": ["codex"],
                                "provider": "openai",
                                "transport": "api",
                                "model": "opaque-openai-high-model",
                                "effort": "high",
                                "intended_recipient": "OpenAI API",
                                "intended_billing_boundary": "user OpenAI API account",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            task = "Review the authorization boundary for this service."
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "v2",
                    "route",
                    "--policy",
                    str(policy_path),
                    "--source-vendor",
                    "codex",
                    "--api-runtime",
                    str(runtime_path),
                ],
                capture_output=True,
                check=False,
                input=task,
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["route"], "openai-high-api")
        self.assertEqual(rendered["tier"], "high")
        self.assertEqual(rendered["source_vendor"], "codex")
        self.assertEqual(
            rendered["destination"],
            {
                "provider": "openai",
                "transport": "api",
                "model": "opaque-openai-high-model",
                "effort": "high",
                "intended_recipient": "OpenAI API",
                "intended_billing_boundary": "user OpenAI API account",
            },
        )
        self.assertTrue(rendered["route_fingerprint"].startswith("sha256:"))
        self.assertNotIn(task, result.stdout)

    def test_refuses_an_api_run_without_explicit_egress_confirmation(self) -> None:
        """Breaks if an API route can start a runtime without user confirmation."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "allow_cross_provider": False,
                        "allow_api": True,
                        "routes": [
                            {
                                "id": "openai-low-api",
                                "tier": "low",
                                "eligible_source_vendors": ["codex"],
                                "provider": "openai",
                                "transport": "api",
                                "model": "opaque-openai-low-model",
                                "effort": "low",
                                "intended_recipient": "OpenAI API",
                                "intended_billing_boundary": "user OpenAI API account",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            task = "Fix a typo."
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "v2",
                    "run",
                    "--policy",
                    str(policy_path),
                    "--source-vendor",
                    "codex",
                    "--api-runtime",
                    sys.executable,
                ],
                capture_output=True,
                check=False,
                input=task,
                text=True,
            )

        self.assertEqual(result.returncode, 5)
        self.assertEqual(json.loads(result.stderr), {"error": "api_confirmation_required"})
        self.assertNotIn(task, result.stderr)

    def test_rejects_a_task_over_the_classifier_limit_as_invalid_input(self) -> None:
        """Breaks if an oversized task reaches route selection or an API runtime."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            runtime_path = _private_runtime(directory)
            policy_path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "allow_cross_provider": False,
                        "allow_api": True,
                        "routes": [
                            {
                                "id": "openai-high-api",
                                "tier": "high",
                                "eligible_source_vendors": ["codex"],
                                "provider": "openai",
                                "transport": "api",
                                "model": "opaque-openai-high-model",
                                "effort": "high",
                                "intended_recipient": "OpenAI API",
                                "intended_billing_boundary": "user OpenAI API account",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "v2",
                    "route",
                    "--policy",
                    str(policy_path),
                    "--source-vendor",
                    "codex",
                    "--api-runtime",
                    str(runtime_path),
                ],
                capture_output=True,
                check=False,
                input="x" * 20_001,
                text=True,
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_task"})

    def test_runs_one_acknowledged_api_runtime_with_fixed_route_arguments(self) -> None:
        """Breaks if an acknowledged route changes its runtime contract or input handoff."""
        runtime_path = Path(__file__).parent / "fixtures" / "fake_api_runtime.py"
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "allow_cross_provider": False,
                        "allow_api": True,
                        "routes": [
                            {
                                "id": "openai-low-api",
                                "tier": "low",
                                "eligible_source_vendors": ["codex"],
                                "provider": "openai",
                                "transport": "api",
                                "model": "opaque-openai-low-model",
                                "effort": "low",
                                "intended_recipient": "OpenAI API",
                                "intended_billing_boundary": "user OpenAI API account",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            task = "Fix a typo."
            review = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "v2",
                    "route",
                    "--policy",
                    str(policy_path),
                    "--source-vendor",
                    "codex",
                    "--api-runtime",
                    str(runtime_path),
                ],
                capture_output=True,
                check=False,
                input=task,
                text=True,
            )
            fingerprint = json.loads(review.stdout)["route_fingerprint"]
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "v2",
                    "run",
                    "--policy",
                    str(policy_path),
                    "--source-vendor",
                    "codex",
                    "--api-runtime",
                    str(runtime_path),
                    "--confirm-api-egress",
                    "--ack-route-fingerprint",
                    fingerprint,
                ],
                capture_output=True,
                check=False,
                input=task,
                text=True,
            )

        self.assertEqual(review.returncode, 0, review.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "runtime-received-task\n")
        self.assertNotIn(task, result.stdout)

    def test_rejects_an_acknowledgement_after_route_semantics_change(self) -> None:
        """Breaks if a review acknowledgement is reusable after a model change."""
        runtime_path = Path(__file__).parent / "fixtures" / "fake_api_runtime.py"
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            original_policy: dict[str, Any] = {
                "schema_version": 2,
                "allow_cross_provider": False,
                "allow_api": True,
                "routes": [
                    {
                        "id": "openai-low-api",
                        "tier": "low",
                        "eligible_source_vendors": ["codex"],
                        "provider": "openai",
                        "transport": "api",
                        "model": "opaque-openai-low-model",
                        "effort": "low",
                        "intended_recipient": "OpenAI API",
                        "intended_billing_boundary": "user OpenAI API account",
                    }
                ],
            }
            policy_path.write_text(json.dumps(original_policy), encoding="utf-8")
            task = "Fix a typo."
            review = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "v2",
                    "route",
                    "--policy",
                    str(policy_path),
                    "--source-vendor",
                    "codex",
                    "--api-runtime",
                    str(runtime_path),
                ],
                capture_output=True,
                check=False,
                input=task,
                text=True,
            )
            original_policy["routes"][0]["model"] = "opaque-openai-replaced-model"
            policy_path.write_text(json.dumps(original_policy), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "v2",
                    "run",
                    "--policy",
                    str(policy_path),
                    "--source-vendor",
                    "codex",
                    "--api-runtime",
                    str(runtime_path),
                    "--confirm-api-egress",
                    "--ack-route-fingerprint",
                    json.loads(review.stdout)["route_fingerprint"],
                ],
                capture_output=True,
                check=False,
                input=task,
                text=True,
            )

        self.assertEqual(review.returncode, 0, review.stderr)
        self.assertEqual(result.returncode, 6)
        self.assertEqual(json.loads(result.stderr), {"error": "route_fingerprint_mismatch"})
        self.assertNotIn(task, result.stderr)

    def test_requires_cross_provider_opt_in_for_a_codex_api_request(self) -> None:
        """Breaks if a Codex request can select an Anthropic API route by default."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            runtime_path = _private_runtime(directory)
            policy = {
                "schema_version": 2,
                "allow_cross_provider": False,
                "allow_api": True,
                "routes": [
                    {
                        "id": "anthropic-low-api",
                        "tier": "low",
                        "eligible_source_vendors": ["codex"],
                        "provider": "anthropic",
                        "transport": "api",
                        "model": "opaque-anthropic-low-model",
                        "effort": "low",
                        "intended_recipient": "Anthropic API",
                        "intended_billing_boundary": "user Anthropic API account",
                    }
                ],
            }
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            common_arguments = [
                sys.executable,
                "-m",
                "weightclass",
                "v2",
                "route",
                "--policy",
                str(policy_path),
                "--source-vendor",
                "codex",
                "--api-runtime",
                str(runtime_path),
            ]
            blocked = subprocess.run(
                common_arguments,
                capture_output=True,
                check=False,
                input="Fix a typo.",
                text=True,
            )
            policy["allow_cross_provider"] = True
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            allowed = subprocess.run(
                common_arguments,
                capture_output=True,
                check=False,
                input="Fix a typo.",
                text=True,
            )

        self.assertEqual(blocked.returncode, 3)
        self.assertEqual(json.loads(blocked.stderr), {"error": "unsupported_route"})
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertEqual(json.loads(allowed.stdout)["destination"]["provider"], "anthropic")

    def test_rejects_an_acknowledgement_after_cross_provider_policy_changes(self) -> None:
        """Breaks if a root policy permission can change after route review."""
        runtime_path = Path(__file__).parent / "fixtures" / "fake_api_runtime.py"
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            policy = {
                "schema_version": 2,
                "allow_cross_provider": False,
                "allow_api": True,
                "routes": [
                    {
                        "id": "openai-low-api",
                        "tier": "low",
                        "eligible_source_vendors": ["codex"],
                        "provider": "openai",
                        "transport": "api",
                        "model": "opaque-openai-low-model",
                        "effort": "low",
                        "intended_recipient": "OpenAI API",
                        "intended_billing_boundary": "user OpenAI API account",
                    }
                ],
            }
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            task = "Fix a typo."
            review = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "v2",
                    "route",
                    "--policy",
                    str(policy_path),
                    "--source-vendor",
                    "codex",
                    "--api-runtime",
                    str(runtime_path),
                ],
                capture_output=True,
                check=False,
                input=task,
                text=True,
            )
            policy["allow_cross_provider"] = True
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "v2",
                    "run",
                    "--policy",
                    str(policy_path),
                    "--source-vendor",
                    "codex",
                    "--api-runtime",
                    str(runtime_path),
                    "--confirm-api-egress",
                    "--ack-route-fingerprint",
                    json.loads(review.stdout)["route_fingerprint"],
                ],
                capture_output=True,
                check=False,
                input=task,
                text=True,
            )

        self.assertEqual(review.returncode, 0, review.stderr)
        self.assertEqual(result.returncode, 6)
        self.assertEqual(json.loads(result.stderr), {"error": "route_fingerprint_mismatch"})


class ApiVendorScopeTests(unittest.TestCase):
    def test_api_vendor_set_derives_from_provider_map(self) -> None:
        """Breaks if the gate drifts from the provider map.

        API_SOURCE_VENDORS = frozenset(SOURCE_PROVIDER) ensures this equality by
        construction. This is what makes SOURCE_PROVIDER[source_vendor] unreachable
        from select_api_route: the gate always rejects any vendor not in the map
        before indexing it. A hand-written gate that drifts from the provider map
        (e.g., someone adding "agy" to the set without updating SOURCE_PROVIDER)
        brings back a KeyError traceback instead of a RouteSelectionError diagnostic.
        """
        self.assertEqual(API_SOURCE_VENDORS, frozenset(SOURCE_PROVIDER))

    def test_a_vendor_without_a_known_provider_is_an_unsupported_route(self) -> None:
        """Breaks if the API path rejects unmapped vendors with a traceback.

        교차-provider 차단은 벤더에서 provider 를 유도해 성립한다. provider 를
        모르는 벤더는 그 차단을 통과시킬 근거가 없으므로 닫는다.

        This test holds before and after the refactor: the user-visible contract
        is RouteSelectionError, not KeyError. The regression guard for divergence
        is test_api_vendor_set_derives_from_provider_map, above.
        """
        policy = ApiRoutingPolicy(
            routes=(
                ApiRoute(
                    route_id="r",
                    tier="low",
                    eligible_source_vendors=("codex", "agy"),
                    provider="openai",
                    transport="api",
                    model="m",
                    effort="low",
                    intended_recipient="OpenAI API",
                    intended_billing_boundary="user OpenAI API account",
                ),
            ),
            allow_cross_provider=False,
            allow_api=True,
        )

        with self.assertRaises(RouteSelectionError):
            select_api_route("Fix a typo.", policy, "agy")

    def test_cli_rejects_a_native_only_vendor_before_route_selection(self) -> None:
        """Breaks if the CLI's own gate on --source-vendor stops matching the vendor set.

        위 test_a_vendor_without_a_known_provider_is_an_unsupported_route 는
        select_api_route 내부 게이트를 직접 부른다(그 결과는 unsupported_route,
        exit 3). 이 테스트는 그보다 앞선 층을 고정한다: `v2 route`/`v2 run` 의
        --source-vendor 는 argparse choices 로 API_SOURCE_VENDORS 에 닫혀 있어,
        native 경로에는 있지만 API 경로에는 없는 `agy` 는 파싱 단계에서 막혀
        select_api_route 까지 내려가지 않는다(invalid_input, exit 2). 정책을 실존
        하고 유효하게, 그리고 그 안의 eligible_source_vendors 에 "agy"를 넣지
        않은 채로 줘야 한다. 그렇지 않으면 이 gate 가 아니라 다른 이유로 같은
        exit 2가 나와, choices gate 가 사라져도 테스트가 계속 통과하는 거짓
        안전감을 준다.
        """
        with tempfile.TemporaryDirectory() as temporary_directory:
            policy_path = Path(temporary_directory) / "api-policy.json"
            policy_path.write_text(json.dumps(_api_policy()), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "v2",
                    "route",
                    "--policy",
                    str(policy_path),
                    "--source-vendor",
                    "agy",
                    "--api-runtime",
                    sys.executable,
                ],
                capture_output=True,
                check=False,
                input="Fix a typo.",
                text=True,
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})


if __name__ == "__main__":
    unittest.main()
