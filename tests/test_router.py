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


class CommandSurfaceTests(unittest.TestCase):
    def test_help_lists_every_reachable_subcommand(self) -> None:
        """Breaks if a mode becomes undiscoverable from the command line."""
        result = subprocess.run(
            [sys.executable, "-m", "sar", "--help"],
            capture_output=True,
            check=False,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        for subcommand in ("classify", "route", "run", "render", "v2"):
            with self.subTest(subcommand=subcommand):
                self.assertIn(subcommand, result.stdout)

    def test_rejects_a_version_query_carrying_extra_arguments(self) -> None:
        """Breaks if --version exits successfully without validating the rest of argv."""
        accepted = subprocess.run(
            [sys.executable, "-m", "sar", "--version"],
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIn("weightclass", accepted.stdout)

        for extra in (["--definitely-invalid"], ["classify"]):
            with self.subTest(extra=extra):
                result = subprocess.run(
                    [sys.executable, "-m", "sar", "--version", *extra],
                    capture_output=True,
                    check=False,
                    input="",
                    text=True,
                )

                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})

    def test_rejects_an_abbreviated_api_egress_confirmation(self) -> None:
        """Breaks if an explicit egress gate can be satisfied by a prefix of its flag.

        정책과 런타임을 모두 유효하게 준다. 축약이 허용되면 --c 가
        --confirm-api-egress 로 해석되어 지문 검사(exit 6)까지 진행하므로,
        가드가 있을 때(exit 2)와 결과가 갈린다.
        """
        with tempfile.TemporaryDirectory() as temporary_directory:
            policy_path = Path(temporary_directory) / "api-policy.json"
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
                    "--c",
                ],
                capture_output=True,
                check=False,
                input="Fix a typo.",
                text=True,
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})


class TaskConfidentialityTests(unittest.TestCase):
    def test_no_subcommand_echoes_any_word_of_the_task(self) -> None:
        """Breaks if any mode starts placing task content in its output or diagnostics.

        개별 테스트의 assertNotIn 은 애초에 출력에 나올 수 없는 단어를 검사해서
        공허하게 통과할 수 있다. 여기서는 태스크의 모든 단어를 성공/실패 양쪽
        스트림 전체에 대해 검사한다.
        """
        task = "Zephyrine quokka authorization ledger reconciliation glimmerfast"
        distinctive_words = [word for word in task.split() if word != "authorization"]

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            worker_path = directory / "silent_worker.py"
            worker_path.write_text(
                "import sys\nsys.stdin.buffer.read()\nprint('worker-done')\n",
                encoding="utf-8",
            )
            native_policy_path = directory / "policy.json"
            native_policy_path.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": f"codex-{tier}",
                                "vendor": "codex",
                                "tier": tier,
                                "command": [sys.executable, str(worker_path)],
                            }
                            for tier in ("low", "standard", "high")
                        ]
                    }
                ),
                encoding="utf-8",
            )
            api_policy_path = directory / "api-policy.json"
            api_policy_path.write_text(
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
            api_arguments = [
                "--policy",
                str(api_policy_path),
                "--source-vendor",
                "codex",
                "--api-runtime",
                sys.executable,
            ]

            for arguments in (
                ["classify"],
                ["route"],
                ["route", "--source-vendor", "codex"],
                ["route", "--policy", "/nonexistent/policy.json"],
                ["run", "--policy", str(native_policy_path)],
                ["run", "--policy", "/nonexistent/policy.json"],
                ["render", "--policy", "/nonexistent/p.json", "--descriptor", "/nonexistent/d.json"],
                ["v2", "route", *api_arguments],
                ["v2", "run", *api_arguments],
                ["v2", "run", *api_arguments, "--confirm-api-egress"],
            ):
                with self.subTest(arguments=arguments):
                    result = subprocess.run(
                        [sys.executable, "-m", "sar", *arguments],
                        capture_output=True,
                        check=False,
                        input=task,
                        text=True,
                    )

                    streams = result.stdout + result.stderr
                    self.assertNotEqual(streams.strip(), "")
                    for word in distinctive_words:
                        self.assertNotIn(word, streams)


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

    def test_rejects_stdin_past_the_byte_bound_before_the_character_limit(self) -> None:
        """Breaks if the byte bound stops rejecting input the character limit would accept.

        분류는 strip 후의 문자 수로 판단하므로, 공백을 덧붙이면 문자 상한은
        통과하지만 바이트 상한은 넘는 입력을 만들 수 있다. 이 입력이 통과하면
        바이트 경계가 사라진 것이다.
        """
        from sar.classification import MAX_TASK_BYTES, MAX_TASK_CHARACTERS

        oversized = b"a" * MAX_TASK_CHARACTERS
        oversized += b" " * (MAX_TASK_BYTES + 1 - len(oversized))

        result = subprocess.run(
            [sys.executable, "-m", "sar", "classify"],
            capture_output=True,
            check=False,
            input=oversized,
        )

        self.assertEqual(len(oversized), MAX_TASK_BYTES + 1)
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

    def test_pins_the_vendor_to_the_first_tier_route_not_the_first_route(self) -> None:
        """Breaks if a leading workflow route makes every tier route unselectable.

        workflow 라우트는 티어 선택 후보가 아니다. 벤더 고정 기준에 포함하면
        workflow 라우트를 먼저 선언한 정책에서 어떤 티어도 매칭되지 않는다.
        """
        with tempfile.TemporaryDirectory() as temporary_directory:
            policy_path = Path(temporary_directory) / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": "claude-review",
                                "vendor": "claude",
                                "workflow": "review",
                                "command": ["claude", "review"],
                            },
                            {
                                "id": "codex-standard",
                                "vendor": "codex",
                                "tier": "standard",
                                "command": ["codex", "exec", "-"],
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
                input="Add a helper function to the parser.",
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["route"], "codex-standard")
        self.assertEqual(json.loads(result.stdout)["vendor"], "codex")

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

    def test_keeps_a_codex_request_on_its_configured_high_route_when_mixing_is_disabled(self) -> None:
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
                                "command": ["codex", "exec", "--model", "codex-high-label", "-"],
                            },
                            {
                                "id": "claude-high",
                                "vendor": "claude",
                                "tier": "high",
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
                "route": "codex-high",
                "tier": "high",
                "vendor": "codex",
            },
        )
        self.assertNotIn("authentication", result.stdout)

    def test_rejects_a_policy_carrying_a_separate_model_label(self) -> None:
        """Breaks if a route may declare a model outside the command it executes.

        model 라벨은 실행되지 않으므로 검증할 수 없다. 라벨을 허용하면 리뷰
        산출물이 실제 실행과 다른 모델을 광고할 수 있으므로 스키마에서 뺐다.
        """
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

    def test_allows_a_codex_request_to_use_a_claude_route_when_mixing_is_enabled(self) -> None:
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
                                "command": ["claude", "--print", "--model", "claude-high-label"],
                            },
                            {
                                "id": "codex-high",
                                "vendor": "codex",
                                "tier": "high",
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
                    "acceptEdits",
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
                    "acceptEdits",
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

    def test_runs_only_the_source_vendor_route_when_two_vendors_share_a_tier(self) -> None:
        """Breaks if cross-vendor containment is enforced for route but not for run."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            workers = {}
            for vendor in ("codex", "claude"):
                worker_path = directory / f"{vendor}_worker.py"
                worker_path.write_text(
                    "import sys\n"
                    "sys.stdin.read()\n"
                    f"print('{vendor}-worker-ran')\n",
                    encoding="utf-8",
                )
                workers[vendor] = worker_path
            policy_path.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": f"{vendor}-low",
                                "vendor": vendor,
                                "tier": "low",
                                "command": [sys.executable, str(workers[vendor])],
                            }
                            for vendor in ("codex", "claude")
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sar",
                    "run",
                    "--policy",
                    str(policy_path),
                    "--source-vendor",
                    "claude",
                ],
                capture_output=True,
                check=False,
                input="Fix a typo.",
                text=True,
            )

        # codex-low 가 먼저 선언되어 있으므로, 벤더 필터가 없으면 그쪽이 실행된다.
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "claude-worker-ran\n")

    def _run_worker_exiting_with(self, worker_body: str, task: str = "Fix a typo.") -> "subprocess.CompletedProcess[str]":
        """Run `wclass run` against a worker whose exit status the caller controls."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            worker_path = directory / "worker.py"
            worker_path.write_text(worker_body, encoding="utf-8")
            policy_path = directory / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": f"codex-{tier}",
                                "vendor": "codex",
                                "tier": tier,
                                "command": [sys.executable, str(worker_path)],
                            }
                            for tier in ("low", "standard", "high")
                        ]
                    }
                ),
                encoding="utf-8",
            )

            return subprocess.run(
                [sys.executable, "-m", "sar", "run", "--policy", str(policy_path)],
                capture_output=True,
                check=False,
                input=task,
                text=True,
            )

    def test_reports_a_failing_child_without_colliding_with_router_codes(self) -> None:
        """Breaks if a child's exit status can be mistaken for a router diagnostic.

        라우터는 2~6을 자신의 진단에 쓴다. 자식이 3으로 죽었을 때 그대로
        돌려주면 호출자는 unsupported_route 와 구분할 수 없다.
        """
        for child_exit_code in (1, 3, 5, 9):
            with self.subTest(child_exit_code=child_exit_code):
                result = self._run_worker_exiting_with(
                    f"import sys\nsys.stdin.buffer.read()\nraise SystemExit({child_exit_code})\n"
                )

                self.assertEqual(result.returncode, 7)
                self.assertEqual(
                    json.loads(result.stderr),
                    {"error": "executor_failed", "executor_exit_code": child_exit_code},
                )

    def test_reports_a_child_killed_by_a_signal_as_a_signal(self) -> None:
        """Breaks if a signal death is folded into an ordinary exit status."""
        result = self._run_worker_exiting_with(
            "import os, signal, sys\n"
            "sys.stdin.buffer.read()\n"
            "os.kill(os.getpid(), signal.SIGTERM)\n"
        )

        self.assertEqual(result.returncode, 7)
        self.assertEqual(
            json.loads(result.stderr),
            {"error": "executor_failed", "executor_signal": 15},
        )

    def test_passes_through_a_successful_child(self) -> None:
        """Breaks if a successful run stops reporting success."""
        result = self._run_worker_exiting_with(
            "import sys\nsys.stdin.buffer.read()\nprint('done')\n"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "done\n")
        self.assertEqual(result.stderr, "")

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
                    "render",
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
                    "render",
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
                    "render",
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
