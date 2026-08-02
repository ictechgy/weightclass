import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


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
            original_policy = {
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
