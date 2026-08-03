import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from sar.router import DEFAULT_ROUTES, Route, RouteRequest, select_route


class DefaultRouteTests(unittest.TestCase):
    def test_every_vendor_differentiates_all_three_tiers(self) -> None:
        """Breaks if a vendor's tiers collapse back to one indistinguishable command."""
        for vendor in ("codex", "claude"):
            with self.subTest(vendor=vendor):
                commands = [
                    route.command for route in DEFAULT_ROUTES if route.vendor == vendor
                ]

                self.assertEqual(len(commands), 3)
                self.assertEqual(len(set(commands)), 3)


class SelectRouteTests(unittest.TestCase):
    def test_returns_the_first_route_matching_vendor_and_workflow(self) -> None:
        routes = (
            Route(
                route_id="codex-review",
                vendor="codex",
                workflow="review",
                command=("codex", "review", "--model", "opaque-model-a"),
            ),
            Route(
                route_id="codex-review-fallback",
                vendor="codex",
                workflow="review",
                command=("codex", "review", "--model", "opaque-model-b"),
            ),
        )

        selected_route = select_route(
            routes, RouteRequest(vendor="codex", workflow="review")
        )

        self.assertEqual(selected_route.route_id, "codex-review")
        self.assertEqual(
            selected_route.command,
            ("codex", "review", "--model", "opaque-model-a"),
        )


class CommandLineTests(unittest.TestCase):
    def test_classifies_a_short_spelling_fix_as_low_effort(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "sar", "classify"],
            capture_output=True,
            check=False,
            input="Fix a spelling typo in the README heading.",
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"tier": "low"})
        self.assertNotIn("spelling", result.stdout)

    def test_rejects_an_empty_task_with_a_redacted_diagnostic(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "sar", "classify"],
            capture_output=True,
            check=False,
            input="",
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_task"})

    def test_rejects_invalid_utf8_stdin_with_a_redacted_diagnostic(self) -> None:
        """Breaks if undecodable input produces a traceback instead of failing closed."""
        for arguments in (["classify"], ["route"], ["run"]):
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    [sys.executable, "-m", "sar", *arguments],
                    capture_output=True,
                    check=False,
                    input=b"\x80\x81",
                )

                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stderr), {"error": "invalid_task"})
                self.assertNotIn(b"Traceback", result.stderr)

    def test_rejects_oversized_stdin_without_buffering_the_whole_stream(self) -> None:
        """Breaks if the byte bound stops guarding the classifier's character limit."""
        from sar.classification import MAX_TASK_BYTES

        result = subprocess.run(
            [sys.executable, "-m", "sar", "classify"],
            capture_output=True,
            check=False,
            input=b"a" * (MAX_TASK_BYTES + 1),
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_task"})

    def test_classifies_a_security_task_as_high_effort(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "sar", "classify"],
            capture_output=True,
            check=False,
            input="Review the authorization boundary for this endpoint.",
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"tier": "high"})
        self.assertNotIn("authorization", result.stdout)

    def test_routes_a_high_effort_task_to_the_matching_policy_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": "claude-low",
                                "vendor": "claude",
                                "tier": "low",
                                "command": ["claude", "--print", "--effort", "low"],
                            },
                            {
                                "id": "claude-high",
                                "vendor": "claude",
                                "tier": "high",
                                "command": ["claude", "--print", "--effort", "high"],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, "-m", "sar", "route", "--policy", str(policy_path)],
                capture_output=True,
                check=False,
                input="Review the security implications of this authorization change.",
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "command": ["claude", "--print", "--effort", "high"],
                "route": "claude-high",
                "tier": "high",
                "vendor": "claude",
            },
        )
        self.assertNotIn("authorization", result.stdout)

    def test_refuses_to_leave_the_policy_vendor_when_no_source_vendor_is_given(self) -> None:
        """Breaks if a tier can silently move a task to a second vendor without opt-in."""
        mixed_vendor_policy = {
            "routes": [
                {
                    "id": "codex-low",
                    "vendor": "codex",
                    "tier": "low",
                    "command": ["codex", "exec", "-"],
                },
                {
                    "id": "claude-high",
                    "vendor": "claude",
                    "tier": "high",
                    "command": ["claude", "--print", "--effort", "high"],
                },
            ]
        }
        high_effort_task = "Review the security implications of this authorization change."

        with tempfile.TemporaryDirectory() as temporary_directory:
            policy_path = Path(temporary_directory) / "policy.json"
            policy_path.write_text(json.dumps(mixed_vendor_policy), encoding="utf-8")
            arguments = [sys.executable, "-m", "sar", "route", "--policy", str(policy_path)]
            blocked = subprocess.run(
                arguments,
                capture_output=True,
                check=False,
                input=high_effort_task,
                text=True,
            )
            mixed_vendor_policy["allow_mixed_vendors"] = True
            policy_path.write_text(json.dumps(mixed_vendor_policy), encoding="utf-8")
            allowed = subprocess.run(
                arguments,
                capture_output=True,
                check=False,
                input=high_effort_task,
                text=True,
            )

        self.assertEqual(blocked.returncode, 3)
        self.assertEqual(json.loads(blocked.stderr), {"error": "unsupported_route"})
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertEqual(json.loads(allowed.stdout)["vendor"], "claude")

    def test_keeps_a_codex_request_on_its_configured_high_model_when_mixing_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "allow_mixed_vendors": False,
                        "routes": [
                            {
                                "id": "codex-high",
                                "vendor": "codex",
                                "tier": "high",
                                "model": "codex-high-label",
                                "command": ["codex", "exec", "--model", "codex-high-label", "-"],
                            },
                            {
                                "id": "claude-high",
                                "vendor": "claude",
                                "tier": "high",
                                "model": "claude-high-label",
                                "command": ["claude", "--print", "--model", "claude-high-label"],
                            },
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
                    "route",
                    "--policy",
                    str(policy_path),
                    "--source-vendor",
                    "codex",
                ],
                capture_output=True,
                check=False,
                input="Review the authentication architecture for this service.",
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "command": ["codex", "exec", "--model", "codex-high-label", "-"],
                "model": "codex-high-label",
                "route": "codex-high",
                "tier": "high",
                "vendor": "codex",
            },
        )
        self.assertNotIn("authentication", result.stdout)

    def test_rejects_a_policy_whose_model_label_is_absent_from_its_command(self) -> None:
        """Breaks if a reviewed descriptor can advertise a model the command contradicts."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": "claude-high",
                                "vendor": "claude",
                                "tier": "high",
                                "model": "reviewed-expensive-label",
                                "command": [
                                    "claude",
                                    "--print",
                                    "--model",
                                    "actually-cheap-label",
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, "-m", "sar", "route", "--policy", str(policy_path)],
                capture_output=True,
                check=False,
                input="Review the authorization boundary.",
                text=True,
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})
        self.assertNotIn("reviewed-expensive-label", result.stderr)

    def test_allows_a_codex_request_to_use_a_claude_model_when_mixing_is_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "allow_mixed_vendors": True,
                        "routes": [
                            {
                                "id": "claude-high",
                                "vendor": "claude",
                                "tier": "high",
                                "model": "claude-high-label",
                                "command": ["claude", "--print", "--model", "claude-high-label"],
                            },
                            {
                                "id": "codex-high",
                                "vendor": "codex",
                                "tier": "high",
                                "model": "codex-high-label",
                                "command": ["codex", "exec", "--model", "codex-high-label", "-"],
                            },
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
                    "route",
                    "--policy",
                    str(policy_path),
                    "--source-vendor",
                    "codex",
                ],
                capture_output=True,
                check=False,
                input="Review the security boundary for this service.",
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "command": ["claude", "--print", "--model", "claude-high-label"],
                "model": "claude-high-label",
                "route": "claude-high",
                "tier": "high",
                "vendor": "claude",
            },
        )
        self.assertNotIn("security boundary", result.stdout)

    def test_routes_a_codex_high_task_to_the_built_in_codex_high_route(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "sar", "route", "--source-vendor", "codex"],
            capture_output=True,
            check=False,
            input="Review the security implications of this authorization change.",
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "command": [
                    "codex",
                    "exec",
                    "--ephemeral",
                    "--sandbox",
                    "workspace-write",
                    "-c",
                    "model_reasoning_effort=high",
                    "-",
                ],
                "route": "codex-high",
                "tier": "high",
                "vendor": "codex",
            },
        )

    def test_routes_a_claude_low_task_to_the_built_in_claude_low_route(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "sar", "route", "--source-vendor", "claude"],
            capture_output=True,
            check=False,
            input="Fix a typo.",
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "command": [
                    "claude",
                    "--print",
                    "--no-session-persistence",
                    "--permission-mode",
                    "manual",
                    "--effort",
                    "low",
                ],
                "route": "claude-low",
                "tier": "low",
                "vendor": "claude",
            },
        )

    def test_routes_a_claude_standard_task_to_the_built_in_claude_standard_route(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "sar", "route", "--source-vendor", "claude"],
            capture_output=True,
            check=False,
            input="Add a focused unit test for this formatter.",
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "command": [
                    "claude",
                    "--print",
                    "--no-session-persistence",
                    "--permission-mode",
                    "manual",
                    "--effort",
                    "medium",
                ],
                "route": "claude-standard",
                "tier": "standard",
                "vendor": "claude",
            },
        )

    def test_keeps_a_high_effort_task_on_the_default_policy_vendor(self) -> None:
        """Breaks if omitting --source-vendor lets the high tier switch vendors."""
        result = subprocess.run(
            [sys.executable, "-m", "sar", "route"],
            capture_output=True,
            check=False,
            input="Assess the security boundary for the new authorization flow.",
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["tier"], "high")
        self.assertEqual(rendered["vendor"], "codex")
        self.assertEqual(rendered["route"], "codex-high")
        self.assertNotIn("claude", result.stdout)

    def test_routes_a_short_spelling_fix_to_the_built_in_workspace_codex_command(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "sar", "route"],
            capture_output=True,
            check=False,
            input="Fix a typo.",
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "command": [
                    "codex",
                    "exec",
                    "--ephemeral",
                    "--sandbox",
                    "workspace-write",
                    "-c",
                    "model_reasoning_effort=low",
                    "-",
                ],
                "route": "codex-low",
                "tier": "low",
                "vendor": "codex",
            },
        )
        self.assertNotIn("typo", result.stdout)
        self.assertIn("model_reasoning_effort=low", result.stdout)

    def test_routes_a_general_task_to_the_built_in_workspace_codex_command(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "sar", "route"],
            capture_output=True,
            check=False,
            input="Add a focused unit test for this formatter.",
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "command": [
                    "codex",
                    "exec",
                    "--ephemeral",
                    "--sandbox",
                    "workspace-write",
                    "-c",
                    "model_reasoning_effort=medium",
                    "-",
                ],
                "route": "codex-standard",
                "tier": "standard",
                "vendor": "codex",
            },
        )
        self.assertNotIn("formatter", result.stdout)

    def test_runs_the_selected_command_with_the_task_on_standard_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            worker_path = directory / "worker.py"
            worker_path.write_text(
                "import sys\n"
                "task = sys.stdin.read()\n"
                "if task == 'Fix a typo.':\n"
                "    print('worker-received-task')\n"
                "else:\n"
                "    raise SystemExit(9)\n",
                encoding="utf-8",
            )
            policy_path.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": "codex-low",
                                "vendor": "codex",
                                "tier": "low",
                                "command": [sys.executable, str(worker_path)],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, "-m", "sar", "run", "--policy", str(policy_path)],
                capture_output=True,
                check=False,
                input="Fix a typo.",
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "worker-received-task\n")
        self.assertNotIn("Fix a typo.", result.stdout)

    def test_passes_a_non_ascii_task_to_the_child_under_a_non_utf8_locale(self) -> None:
        """Breaks if a task's characters can leak through a locale encoding error."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            worker_path = directory / "worker.py"
            worker_path.write_text(
                "import sys\n"
                "task = sys.stdin.buffer.read().decode('utf-8')\n"
                "if task == '개인정보 처리 방침 오타 수정':\n"
                "    print('worker-received-task')\n"
                "else:\n"
                "    raise SystemExit(9)\n",
                encoding="utf-8",
            )
            policy_path.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": "codex-high",
                                "vendor": "codex",
                                "tier": "high",
                                "command": [sys.executable, str(worker_path)],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            # LC_ALL=C 는 cron, systemd, Docker, CI 러너에서 흔한 기본값이다.
            ascii_only_environment = dict(os.environ, LC_ALL="C", PYTHONUTF8="0")
            result = subprocess.run(
                [sys.executable, "-m", "sar", "run", "--policy", str(policy_path)],
                capture_output=True,
                check=False,
                env=ascii_only_environment,
                input="개인정보 처리 방침 오타 수정".encode("utf-8"),
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"worker-received-task\n")
        self.assertNotIn(b"UnicodeEncodeError", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)

    def test_hides_executor_startup_details_when_a_route_command_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            missing_command = directory / "not-available"
            policy_path.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": "codex-low",
                                "vendor": "codex",
                                "tier": "low",
                                "command": [str(missing_command)],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, "-m", "sar", "run", "--policy", str(policy_path)],
                capture_output=True,
                check=False,
                input="Fix a typo.",
                text=True,
            )

        self.assertEqual(result.returncode, 4)
        self.assertEqual(json.loads(result.stderr), {"error": "executor_unavailable"})
        self.assertNotIn("not-available", result.stderr)

    def test_renders_the_selected_route_as_a_reviewable_command_array(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            descriptor_path = directory / "descriptor.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": "claude-plan",
                                "vendor": "claude",
                                "workflow": "plan",
                                "command": ["claude", "--model", "opaque-model"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            descriptor_path.write_text(
                json.dumps({"vendor": "claude", "workflow": "plan"}),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sar",
                    "--policy",
                    str(policy_path),
                    "--descriptor",
                    str(descriptor_path),
                ],
                capture_output=True,
                check=False,
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "command": ["claude", "--model", "opaque-model"],
                "route": "claude-plan",
            },
        )

    def test_rejects_an_unsupported_route_with_a_redacted_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            descriptor_path = directory / "descriptor.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": "claude-plan",
                                "vendor": "claude",
                                "workflow": "plan",
                                "command": ["claude", "--model", "opaque-model"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            descriptor_path.write_text(
                json.dumps({"vendor": "claude", "workflow": "private-workflow"}),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sar",
                    "--policy",
                    str(policy_path),
                    "--descriptor",
                    str(descriptor_path),
                ],
                capture_output=True,
                check=False,
                text=True,
            )

        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stderr), {"error": "unsupported_route"})
        self.assertNotIn("private-workflow", result.stderr)

    def test_rejects_unknown_descriptor_fields_without_echoing_them(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            descriptor_path = directory / "descriptor.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": "codex-review",
                                "vendor": "codex",
                                "workflow": "review",
                                "command": ["codex", "review"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            descriptor_path.write_text(
                json.dumps(
                    {
                        "vendor": "codex",
                        "workflow": "review",
                        "untrusted": "must-not-be-echoed",
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sar",
                    "--policy",
                    str(policy_path),
                    "--descriptor",
                    str(descriptor_path),
                ],
                capture_output=True,
                check=False,
                text=True,
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})
        self.assertNotIn("must-not-be-echoed", result.stderr)


if __name__ == "__main__":
    unittest.main()
