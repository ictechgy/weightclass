import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


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


class V2EgressGateTests(unittest.TestCase):
    """Egress를 막는 각 게이트가 실제로 단독으로 동작하는지 고정한다."""

    def _review(self, policy: dict[str, object], runtime: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            policy_path = Path(temporary_directory) / "policy.json"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            return subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sar",
                    "v2",
                    "route",
                    "--policy",
                    str(policy_path),
                    "--source-vendor",
                    "codex",
                    "--api-runtime",
                    runtime,
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
                "sar",
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
                "sar",
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
            runtime_path = Path(sys.executable)
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
                    "sar",
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
                    "sar",
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
                    "sar",
                    "v2",
                    "route",
                    "--policy",
                    str(policy_path),
                    "--source-vendor",
                    "codex",
                    "--api-runtime",
                    sys.executable,
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
                    "sar",
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
                    "sar",
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
                    "sar",
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
                    "sar",
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
                "sar",
                "v2",
                "route",
                "--policy",
                str(policy_path),
                "--source-vendor",
                "codex",
                "--api-runtime",
                sys.executable,
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
                    "sar",
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
                    "sar",
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


if __name__ == "__main__":
    unittest.main()
