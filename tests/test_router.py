import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.runtime_guard import guarded_launch
from weightclass import cli, router
from weightclass.router import (
    DEFAULT_ROUTES,
    Route,
    RouteRequest,
    native_route_fingerprint,
    select_route,
)


def _weightclass(*arguments: str, task: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "weightclass", *arguments],
        capture_output=True,
        check=False,
        input=task,
        text=True,
    )


def reviewed_run(
    policy_path: Path,
    task: str,
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    """Review a policy, then run exactly what that review bound.

    `run --policy` 는 검토한 지문을 요구하므로 실제 사용 흐름은 언제나 두 단계다.
    정책 자체가 거부되면 검토 결과가 곧 답이므로 그것을 돌려준다. 그래야 정책
    거부를 확인하는 테스트와 실행 결과를 확인하는 테스트가 같은 헬퍼를 쓴다.
    """
    review = _weightclass("route", "--policy", str(policy_path), *extra, task=task)
    if review.returncode != 0:
        return review
    fingerprint = json.loads(review.stdout)["route_fingerprint"]
    return _weightclass(
        "run",
        "--policy",
        str(policy_path),
        "--ack-route-fingerprint",
        fingerprint,
        *extra,
        task=task,
    )


class DefaultRouteTests(unittest.TestCase):
    def test_explicit_schema_one_policy_preserves_legacy_route_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "routes": [
                            {
                                "id": "legacy",
                                "vendor": "codex",
                                "tier": "low",
                                "command": ["owned-fake"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            loaded = cli.load_routing_policy(policy_path)
        self.assertEqual(loaded.routes[0].route_id, "legacy")

    def test_every_vendor_differentiates_all_three_tiers(self) -> None:
        """Breaks if a vendor's tiers collapse back to one indistinguishable command."""
        for vendor in ("codex", "claude"):
            with self.subTest(vendor=vendor):
                commands = [route.command for route in DEFAULT_ROUTES if route.vendor == vendor]

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

        selected_route = select_route(routes, RouteRequest(vendor="codex", workflow="review"))

        self.assertEqual(selected_route.route_id, "codex-review")
        self.assertEqual(
            selected_route.command,
            ("codex", "review", "--model", "opaque-model-a"),
        )


class ExecutorSpawnFailureTests(unittest.TestCase):
    """spawn 단계 방어선은 검증기가 이미 막고 있어 CLI 로는 도달할 수 없다.

    검증 규칙에 빈틈이 생기면 그때 이 경로가 트레이스백 대신 진단을 내야 하므로,
    subprocess 를 직접 실패시켜 단위로 확인한다.
    """

    def _assert_maps_to_executor_unavailable(self, raised: Exception) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            policy_path = Path(temporary_directory) / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": "codex-low",
                                "vendor": "codex",
                                "tier": "low",
                                "command": ["/bin/echo", "ok"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            # 정책으로 실행하려면 검토한 지문이 필요하다. spawn 방어선을 보려면
            # 그 앞의 결합을 정상적으로 통과해야 하므로 지문을 실제로 계산한다.
            fingerprint = native_route_fingerprint(
                Route(
                    route_id="codex-low",
                    vendor="codex",
                    workflow="",
                    command=("/bin/echo", "ok"),
                    tier="low",
                ),
                False,
            )
            errors = io.StringIO()
            with (
                mock.patch("weightclass.cli.subprocess.run", side_effect=raised),
                mock.patch(
                    "weightclass.cli.read_task_from_standard_input", return_value="Fix a typo."
                ),
                contextlib.redirect_stderr(errors),
            ):
                exit_code = cli.run_from_standard_input(policy_path, None, fingerprint)

        self.assertEqual(exit_code, 4)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "executor_unavailable"})

    def test_maps_an_argv_encoding_failure_to_a_redacted_diagnostic(self) -> None:
        """Breaks if a validator gap can reach exec and raise instead of failing closed."""
        self._assert_maps_to_executor_unavailable(ValueError("embedded null byte"))

    def test_maps_a_missing_executable_to_a_redacted_diagnostic(self) -> None:
        """Breaks if the pre-existing OSError path stops being handled."""
        self._assert_maps_to_executor_unavailable(FileNotFoundError("no such file"))


class PolicyRunBindingTests(unittest.TestCase):
    """정책으로 실행하려면 검토한 지문이 반드시 있어야 한다.

    파일 권한 검사로는 route 와 run 사이의 교체를 막을 수 없다. 부모 디렉터리에
    쓸 수 있는 쪽은 모드와 무관하게 rename 으로 파일을 갈아치울 수 있고, 두 번째
    읽기는 애초에 첫 번째와 다른 파일일 수 있다. 지문만이 선택된 명령까지 묶는다.
    """

    def _policy(self, directory: Path) -> Path:
        policy_path = directory / "policy.json"
        policy_path.write_text(
            json.dumps(
                {
                    "routes": [
                        {
                            "id": "codex-low",
                            "vendor": "codex",
                            "tier": "low",
                            "command": ["/bin/echo", "ok"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return policy_path

    def test_a_policy_run_without_an_acknowledgement_is_refused(self) -> None:
        """Breaks if an unreviewed policy can still start a command."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            result = _weightclass(
                "run",
                "--policy",
                str(self._policy(Path(temporary_directory))),
                task="Fix a typo.",
            )

        self.assertEqual(result.returncode, 6)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(result.stderr), {"error": "route_fingerprint_mismatch"})

    def test_the_refusal_happens_before_the_task_is_read(self) -> None:
        """Breaks if a doomed run consumes the task before failing closed."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            policy_path = self._policy(Path(temporary_directory))
            errors = io.StringIO()
            with (
                mock.patch("weightclass.cli.read_task_from_standard_input") as reader,
                mock.patch("weightclass.cli.subprocess.run") as spawn,
                contextlib.redirect_stderr(errors),
            ):
                exit_code = cli.run_from_standard_input(policy_path, None)

        self.assertEqual(exit_code, 6)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "route_fingerprint_mismatch"})
        reader.assert_not_called()
        spawn.assert_not_called()

    def test_built_in_routes_still_run_without_an_acknowledgement(self) -> None:
        """Breaks if the requirement spreads to routes that live in code.

        기본 라우트는 코드에 고정되어 교체할 수 없으므로 묶을 대상이 없다.
        여기까지 지문을 요구하면 검토와 무관한 마찰만 생긴다.
        """
        errors = io.StringIO()
        with (
            mock.patch("weightclass.cli.read_task_from_standard_input", return_value="Fix a typo."),
            mock.patch(
                "weightclass.cli.subprocess.run",
                return_value=subprocess.CompletedProcess((), 0),
            ) as spawn,
            contextlib.redirect_stderr(errors),
        ):
            exit_code = cli.run_from_standard_input(None, "codex")

        self.assertEqual(exit_code, 0, errors.getvalue())
        spawn.assert_called_once()

    def test_a_stale_acknowledgement_is_still_refused(self) -> None:
        """Breaks if requiring the flag replaced verifying its value."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            result = _weightclass(
                "run",
                "--policy",
                str(self._policy(Path(temporary_directory))),
                "--ack-route-fingerprint",
                "sha256:" + "0" * 64,
                task="Fix a typo.",
            )

        self.assertEqual(result.returncode, 6)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(result.stderr), {"error": "route_fingerprint_mismatch"})


class TaskPlaceholderTests(unittest.TestCase):
    """{{task}} 는 명령 안에서 태스크가 들어갈 자리를 표시한다.

    stdin 을 읽지 않고 프롬프트를 인자로만 받는 CLI 가 있기 때문이다. 자리를
    잘못 쓴 정책은 파싱 단계에서 닫는다. 실행 직전에 발견하면 이미 늦다.
    """

    def _policy(self, directory: Path, command: list[str]) -> Path:
        policy_path = directory / "policy.json"
        policy_path.write_text(
            json.dumps(
                {"routes": [{"id": "r", "vendor": "codex", "tier": "low", "command": command}]}
            ),
            encoding="utf-8",
        )
        return policy_path

    def test_one_whole_token_is_accepted(self) -> None:
        """Breaks if the reserved slot cannot be declared at all."""
        with tempfile.TemporaryDirectory() as directory:
            path = self._policy(Path(directory), ["/bin/echo", "{{task}}"])
            routes = cli.load_routes(path)

        self.assertEqual(routes[0].command, ("/bin/echo", "{{task}}"))
        self.assertTrue(router.uses_argv_task_delivery(routes[0].command))

    def test_no_token_means_stdin_delivery(self) -> None:
        """Breaks if existing policies silently change delivery mode."""
        with tempfile.TemporaryDirectory() as directory:
            path = self._policy(Path(directory), ["/bin/echo", "ok"])
            routes = cli.load_routes(path)

        self.assertFalse(router.uses_argv_task_delivery(routes[0].command))

    def test_two_tokens_are_rejected(self) -> None:
        """Breaks if a task could be delivered twice with no defined meaning."""
        with tempfile.TemporaryDirectory() as directory:
            path = self._policy(Path(directory), ["/bin/echo", "{{task}}", "{{task}}"])
            with self.assertRaises(cli.InvalidInputError):
                cli.load_routes(path)

    def test_the_token_inside_a_larger_argument_is_rejected(self) -> None:
        """Breaks if how the task and a flag were joined becomes ambiguous."""
        for argument in ("--prompt={{task}}", "prefix{{task}}", "{{task}}suffix"):
            with self.subTest(argument=argument), tempfile.TemporaryDirectory() as directory:
                path = self._policy(Path(directory), ["/bin/echo", argument])
                with self.assertRaises(cli.InvalidInputError):
                    cli.load_routes(path)

    def test_substitution_fills_exactly_the_reserved_slot(self) -> None:
        """Breaks if substitution touches an argument it was not given."""
        filled = router.substitute_task(("agy", "--print", "{{task}}", "--effort"), "긴 태스크")

        self.assertEqual(filled, ("agy", "--print", "긴 태스크", "--effort"))


class CommandSurfaceTests(unittest.TestCase):
    def test_help_lists_every_reachable_subcommand(self) -> None:
        """Breaks if a mode becomes undiscoverable from the command line."""
        result = subprocess.run(
            [sys.executable, "-m", "weightclass", "--help"],
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
            [sys.executable, "-m", "weightclass", "--version"],
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIn("weightclass", accepted.stdout)

        for extra in (["--definitely-invalid"], ["classify"]):
            with self.subTest(extra=extra):
                result = subprocess.run(
                    [sys.executable, "-m", "weightclass", "--version", *extra],
                    capture_output=True,
                    check=False,
                    input="",
                    text=True,
                )

                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})

    def test_unexpected_parser_state_fails_closed_without_v2_dispatch(self) -> None:
        """Breaks if an unknown command implicitly falls through to a V2 handler."""
        arguments = SimpleNamespace(
            version=False,
            command="future-command",
            api_command="route",
            policy=None,
            source_vendor=None,
            api_runtime=None,
        )
        v2_was_called = False

        def unexpected_v2_call(*_arguments: object) -> int:
            nonlocal v2_was_called
            v2_was_called = True
            return 91

        parser = SimpleNamespace(parse_args=lambda _arguments: arguments)
        errors = io.StringIO()
        with (
            mock.patch("weightclass.cli.build_parser", return_value=parser),
            mock.patch(
                "weightclass.cli.v2_route_from_standard_input",
                side_effect=unexpected_v2_call,
            ),
            contextlib.redirect_stderr(errors),
        ):
            exit_code = cli.main([])

        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "invalid_input"})
        self.assertFalse(v2_was_called)

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
                    "weightclass",
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
                [
                    "render",
                    "--policy",
                    "/nonexistent/p.json",
                    "--descriptor",
                    "/nonexistent/d.json",
                ],
                ["v2", "route", *api_arguments],
                ["v2", "run", *api_arguments],
                ["v2", "run", *api_arguments, "--confirm-api-egress"],
            ):
                with self.subTest(arguments=arguments):
                    result = subprocess.run(
                        [sys.executable, "-m", "weightclass", *arguments],
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
    def test_reason_only_changes_do_not_change_the_reviewed_route_fingerprint(self) -> None:
        """Breaks if explanation metadata becomes an undocumented fingerprint input."""
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "posture": "balanced",
                        "routes": [
                            {
                                "id": "codex-high",
                                "vendor": "codex",
                                "tier": "high",
                                "command": ["codex", "exec"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            base = [
                sys.executable,
                "-m",
                "weightclass",
                "route",
                "--policy",
                str(policy_path),
                "--source-vendor",
                "codex",
            ]
            classified = subprocess.run(
                base,
                capture_output=True,
                check=False,
                input="Review the security boundary.",
                text=True,
            )
            explicit = subprocess.run(
                [*base, "--tier", "high"],
                capture_output=True,
                check=False,
                input="Review the security boundary.",
                text=True,
            )

        self.assertEqual(classified.returncode, 0, classified.stderr)
        self.assertEqual(explicit.returncode, 0, explicit.stderr)
        classified_route = json.loads(classified.stdout)
        explicit_route = json.loads(explicit.stdout)
        self.assertEqual(classified_route["reason_code"], "high.complexity_signal")
        self.assertEqual(explicit_route["reason_code"], "explicit.requested_tier")
        self.assertEqual(
            classified_route["route_fingerprint"],
            explicit_route["route_fingerprint"],
        )

    def _rendered_route(self, result: "subprocess.CompletedProcess[str]") -> dict[str, object]:
        """Parse a `wclass route` descriptor, checking and removing its fingerprint.

        지문 값 자체는 명령·티어·벤더에서 유도되므로 개별 테스트가 리터럴로
        고정할 필요가 없다. 형태만 확인하고 나머지 필드를 비교하게 한다.
        """
        rendered: dict[str, object] = json.loads(result.stdout)
        fingerprint = str(rendered.pop("route_fingerprint"))
        self.assertTrue(fingerprint.startswith("sha256:"), fingerprint)
        self.assertEqual(len(fingerprint), len("sha256:") + 64)
        return rendered

    def test_classifies_a_short_spelling_fix_as_low_effort(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "weightclass", "classify"],
            capture_output=True,
            check=False,
            input="Fix a spelling typo in the README heading.",
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"tier": "low"})
        self.assertNotIn("spelling", result.stdout)

    def test_explains_a_local_classification_with_static_policy_metadata(self) -> None:
        task = "Fix a spelling typo in sentinel-private-heading-9482."

        result = subprocess.run(
            [sys.executable, "-m", "weightclass", "classify", "--explain"],
            capture_output=True,
            check=False,
            input=task,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "tier": "low",
                "reason_code": "low.mechanical",
                "policy_version": "2",
            },
        )
        self.assertNotIn("sentinel-private-heading-9482", result.stdout)
        self.assertNotIn("spelling", result.stdout)

    def test_rejects_vendor_explanation_as_not_a_local_policy_decision(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "weightclass",
                "classify",
                "--source-vendor",
                "codex",
                "--ask-vendor",
                "--explain",
            ],
            capture_output=True,
            check=False,
            input="sentinel-private-task-5721",
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})
        self.assertNotIn("sentinel-private-task-5721", result.stderr)

    def test_explanation_rejects_an_empty_task_with_a_redacted_diagnostic(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "weightclass", "classify", "--explain"],
            capture_output=True,
            check=False,
            input="",
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_task"})
        self.assertEqual(result.stdout, "")

    def test_default_outputs_remain_byte_for_byte_compatible_without_explain(self) -> None:
        classify = subprocess.run(
            [sys.executable, "-m", "weightclass", "classify"],
            capture_output=True,
            check=False,
            input="Fix the typo.",
            text=True,
        )
        route = subprocess.run(
            [sys.executable, "-m", "weightclass", "route", "--source-vendor", "codex"],
            capture_output=True,
            check=False,
            input="Fix the typo.",
            text=True,
        )

        self.assertEqual(classify.stdout, '{"tier": "low"}\n')
        self.assertEqual(
            route.stdout,
            '{"command": ["codex", "exec", "--ephemeral", "--sandbox", "workspace-write", "-c", '
            '"model_reasoning_effort=low", "-"], "route": "codex-low", "tier": "low", '
            '"vendor": "codex", "route_fingerprint": '
            f'"{json.loads(route.stdout)["route_fingerprint"]}"}}\n',
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            policy_path = Path(temporary_directory, "policy.json")
            policy_path.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": "quiet-low",
                                "vendor": "codex",
                                "tier": "low",
                                "command": [sys.executable, "-c", "pass"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            run = reviewed_run(policy_path, "Fix the typo.")

        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout, "")
        self.assertEqual(run.stderr, "")

    def test_rejects_an_empty_task_with_a_redacted_diagnostic(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "weightclass", "classify"],
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
                    [sys.executable, "-m", "weightclass", *arguments],
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
        from weightclass.classification import MAX_TASK_BYTES, MAX_TASK_CHARACTERS

        oversized = b"a" * MAX_TASK_CHARACTERS
        oversized += b" " * (MAX_TASK_BYTES + 1 - len(oversized))

        result = subprocess.run(
            [sys.executable, "-m", "weightclass", "classify"],
            capture_output=True,
            check=False,
            input=oversized,
        )

        self.assertEqual(len(oversized), MAX_TASK_BYTES + 1)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_task"})

    def test_classifies_a_security_task_as_high_effort(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "weightclass", "classify"],
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
                [sys.executable, "-m", "weightclass", "route", "--policy", str(policy_path)],
                capture_output=True,
                check=False,
                input="Review the security implications of this authorization change.",
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._rendered_route(result),
            {
                "command": ["claude", "--print", "--effort", "high"],
                "route": "claude-high",
                "tier": "high",
                "vendor": "claude",
            },
        )
        self.assertNotIn("authorization", result.stdout)

    def test_cautious_policy_raises_only_an_ambiguous_standard_route(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            policy_path = Path(temporary_directory) / "policy.json"
            policy = {
                "posture": "balanced",
                "routes": [
                    {
                        "id": f"codex-{tier}",
                        "vendor": "codex",
                        "tier": tier,
                        "command": ["codex", tier],
                    }
                    for tier in ("low", "standard", "high")
                ],
            }
            policy_path.write_text(json.dumps(policy), encoding="utf-8")

            balanced = subprocess.run(
                [sys.executable, "-m", "weightclass", "route", "--policy", str(policy_path)],
                capture_output=True,
                check=False,
                input="Implement the requested feature.",
                text=True,
            )
            policy["posture"] = "cautious"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            cautious = subprocess.run(
                [sys.executable, "-m", "weightclass", "route", "--policy", str(policy_path)],
                capture_output=True,
                check=False,
                input="Implement the requested feature.",
                text=True,
            )

        self.assertEqual(balanced.returncode, 0, balanced.stderr)
        self.assertEqual(cautious.returncode, 0, cautious.stderr)
        self.assertEqual(json.loads(balanced.stdout)["tier"], "standard")
        self.assertEqual(json.loads(cautious.stdout)["tier"], "high")
        self.assertEqual(json.loads(cautious.stdout)["vendor"], "codex")
        self.assertEqual(json.loads(cautious.stdout)["posture"], "cautious")
        self.assertEqual(json.loads(cautious.stdout)["reason_code"], "high.cautious_ambiguity")

    def test_cautious_policy_does_not_raise_a_mechanical_low_route(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            policy_path = Path(temporary_directory) / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "posture": "cautious",
                        "routes": [
                            {
                                "id": "codex-low",
                                "vendor": "codex",
                                "tier": "low",
                                "command": ["codex", "opaque-model-label"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, "-m", "weightclass", "route", "--policy", str(policy_path)],
                capture_output=True,
                check=False,
                input="Fix the typo.",
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout)
        self.assertEqual((rendered["tier"], rendered["vendor"]), ("low", "codex"))
        self.assertEqual(rendered["command"], ["codex", "opaque-model-label"])

    def test_cautious_raise_never_crosses_the_source_vendor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            policy_path = Path(temporary_directory) / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "posture": "cautious",
                        "routes": [
                            {
                                "id": "codex-standard",
                                "vendor": "codex",
                                "tier": "standard",
                                "command": ["codex", "standard"],
                            },
                            {
                                "id": "claude-high",
                                "vendor": "claude",
                                "tier": "high",
                                "command": ["claude", "high"],
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
                    "weightclass",
                    "route",
                    "--policy",
                    str(policy_path),
                    "--source-vendor",
                    "codex",
                ],
                capture_output=True,
                check=False,
                input="Implement the requested feature.",
                text=True,
            )

        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stderr), {"error": "unsupported_route"})

    def test_explicit_tier_is_not_changed_by_cautious_posture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            policy_path = Path(temporary_directory) / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "posture": "cautious",
                        "routes": [
                            {
                                "id": "codex-standard",
                                "vendor": "codex",
                                "tier": "standard",
                                "command": ["codex", "standard"],
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
                    "route",
                    "--policy",
                    str(policy_path),
                    "--tier",
                    "standard",
                ],
                capture_output=True,
                check=False,
                input="Implement the requested feature.",
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["tier"], "standard")
        self.assertEqual(rendered["reason_code"], "explicit.requested_tier")

    def test_rejects_unsupported_or_malformed_posture_without_echoing_it(self) -> None:
        sentinel = "sentinel-secret-posture-9237"
        invalid_postures: tuple[object, ...] = (sentinel, True, None, {"mode": "cautious"})
        for position, posture in enumerate(invalid_postures):
            with self.subTest(position=position), tempfile.TemporaryDirectory() as directory:
                policy_path = Path(directory) / "policy.json"
                policy_path.write_text(
                    json.dumps(
                        {
                            "posture": posture,
                            "routes": [
                                {
                                    "id": "codex-high",
                                    "vendor": "codex",
                                    "tier": "high",
                                    "command": ["codex", "high"],
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                result = subprocess.run(
                    [sys.executable, "-m", "weightclass", "route", "--policy", str(policy_path)],
                    capture_output=True,
                    check=False,
                    input="Review authorization.",
                    text=True,
                )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})
            self.assertNotIn(sentinel, result.stderr)

    def test_native_fingerprint_binds_posture_even_when_selection_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            policy_path = Path(temporary_directory) / "policy.json"
            policy = {
                "posture": "balanced",
                "routes": [
                    {
                        "id": "codex-high",
                        "vendor": "codex",
                        "tier": "high",
                        "command": ["codex", "high"],
                    }
                ],
            }
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            balanced = subprocess.run(
                [sys.executable, "-m", "weightclass", "route", "--policy", str(policy_path)],
                capture_output=True,
                check=False,
                input="Review authorization.",
                text=True,
            )
            policy["posture"] = "cautious"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            cautious = subprocess.run(
                [sys.executable, "-m", "weightclass", "route", "--policy", str(policy_path)],
                capture_output=True,
                check=False,
                input="Review authorization.",
                text=True,
            )
            rejected = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "run",
                    "--policy",
                    str(policy_path),
                    "--ack-route-fingerprint",
                    json.loads(balanced.stdout)["route_fingerprint"],
                ],
                capture_output=True,
                check=False,
                input="Review authorization.",
                text=True,
            )

        self.assertEqual(balanced.returncode, 0, balanced.stderr)
        self.assertEqual(cautious.returncode, 0, cautious.stderr)
        self.assertNotEqual(
            json.loads(balanced.stdout)["route_fingerprint"],
            json.loads(cautious.stdout)["route_fingerprint"],
        )
        self.assertEqual(rejected.returncode, 6)
        self.assertEqual(json.loads(rejected.stderr), {"error": "route_fingerprint_mismatch"})

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
                [sys.executable, "-m", "weightclass", "route", "--policy", str(policy_path)],
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
        mixed_vendor_policy: dict[str, object] = {
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
            arguments = [sys.executable, "-m", "weightclass", "route", "--policy", str(policy_path)]
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

    def test_keeps_a_codex_request_on_its_configured_high_route_when_mixing_is_disabled(
        self,
    ) -> None:
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
                    "weightclass",
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
            self._rendered_route(result),
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
                [sys.executable, "-m", "weightclass", "route", "--policy", str(policy_path)],
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
                    "weightclass",
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
            self._rendered_route(result),
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
            [sys.executable, "-m", "weightclass", "route", "--source-vendor", "codex"],
            capture_output=True,
            check=False,
            input="Review the security implications of this authorization change.",
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._rendered_route(result),
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
            [sys.executable, "-m", "weightclass", "route", "--source-vendor", "claude"],
            capture_output=True,
            check=False,
            input="Fix a typo.",
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._rendered_route(result),
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
            [sys.executable, "-m", "weightclass", "route", "--source-vendor", "claude"],
            capture_output=True,
            check=False,
            input="Add a focused unit test for this formatter.",
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._rendered_route(result),
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
            [sys.executable, "-m", "weightclass", "route"],
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
            [sys.executable, "-m", "weightclass", "route"],
            capture_output=True,
            check=False,
            input="Fix a typo.",
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._rendered_route(result),
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
            [sys.executable, "-m", "weightclass", "route"],
            capture_output=True,
            check=False,
            input="Add a focused unit test for this formatter.",
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._rendered_route(result),
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

            result = reviewed_run(policy_path, "Fix a typo.")

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
            task = "개인정보 처리 방침 오타 수정"
            review = subprocess.run(
                [sys.executable, "-m", "weightclass", "route", "--policy", str(policy_path)],
                capture_output=True,
                check=False,
                env=ascii_only_environment,
                input=task.encode(),
            )
            self.assertEqual(review.returncode, 0, review.stderr)
            fingerprint = json.loads(review.stdout)["route_fingerprint"]
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "run",
                    "--policy",
                    str(policy_path),
                    "--ack-route-fingerprint",
                    fingerprint,
                ],
                capture_output=True,
                check=False,
                env=ascii_only_environment,
                input=task.encode(),
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
                    f"import sys\nsys.stdin.read()\nprint('{vendor}-worker-ran')\n",
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

            result = reviewed_run(policy_path, "Fix a typo.", "--source-vendor", "claude")

        # codex-low 가 먼저 선언되어 있으므로, 벤더 필터가 없으면 그쪽이 실행된다.
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "claude-worker-ran\n")

    def _run_worker_exiting_with(self, worker_body: str) -> "subprocess.CompletedProcess[str]":
        """Run `wclass run` against a worker whose exit status the caller controls."""
        task = "Fix a typo."
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

            return reviewed_run(policy_path, task)

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

    def test_keeps_the_failure_diagnostic_parseable_after_unterminated_child_output(
        self,
    ) -> None:
        """Breaks if the diagnostic can be concatenated onto the child's own stderr.

        자식은 stderr 를 상속받는다. 진행 표시처럼 개행 없이 끝나는 출력 뒤에
        진단이 그대로 이어붙으면 어떤 파싱으로도 종료 코드를 복구할 수 없다.
        """
        result = self._run_worker_exiting_with(
            "import sys\n"
            "sys.stdin.buffer.read()\n"
            "sys.stderr.write('Working...')\n"
            "raise SystemExit(2)\n"
        )

        self.assertEqual(result.returncode, 7)
        self.assertIn("Working...", result.stderr)
        self.assertEqual(
            json.loads(result.stderr.splitlines()[-1]),
            {"error": "executor_failed", "executor_exit_code": 2},
        )

    @guarded_launch("native_v1")
    def test_passes_through_a_successful_child(self) -> None:
        """Breaks if a successful run stops reporting success."""
        result = self._run_worker_exiting_with(
            "import sys\nsys.stdin.buffer.read()\nprint('done')\n"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "done\n")
        self.assertEqual(result.stderr, "")

    def test_refuses_to_run_a_route_that_changed_since_it_was_reviewed(self) -> None:
        """Breaks if an acknowledged native route can be swapped before it runs.

        route 와 run 은 정책을 각각 따로 읽는다. 지문을 제시하면 실행 직전에
        다시 계산해 비교하므로, 사이에 정책이 바뀌면 실행되지 않아야 한다.
        """
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "policy.json"
            for name in ("reviewed", "swapped"):
                (directory / f"{name}.py").write_text(
                    f"import sys\nsys.stdin.buffer.read()\nprint('{name}-worker')\n",
                    encoding="utf-8",
                )

            def write_policy(worker: str) -> None:
                policy_path.write_text(
                    json.dumps(
                        {
                            "routes": [
                                {
                                    "id": f"codex-{tier}",
                                    "vendor": "codex",
                                    "tier": tier,
                                    "command": [sys.executable, str(directory / worker)],
                                }
                                for tier in ("low", "standard", "high")
                            ]
                        }
                    ),
                    encoding="utf-8",
                )

            write_policy("reviewed.py")
            review = subprocess.run(
                [sys.executable, "-m", "weightclass", "route", "--policy", str(policy_path)],
                capture_output=True,
                check=False,
                input="Fix a typo.",
                text=True,
            )
            self.assertEqual(review.returncode, 0, review.stderr)
            fingerprint = json.loads(review.stdout)["route_fingerprint"]

            accepted = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "run",
                    "--policy",
                    str(policy_path),
                    "--ack-route-fingerprint",
                    fingerprint,
                ],
                capture_output=True,
                check=False,
                input="Fix a typo.",
                text=True,
            )

            write_policy("swapped.py")
            refused = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "run",
                    "--policy",
                    str(policy_path),
                    "--ack-route-fingerprint",
                    fingerprint,
                ],
                capture_output=True,
                check=False,
                input="Fix a typo.",
                text=True,
            )
            # 여기서만은 헬퍼를 쓰지 않는다. 지문을 아예 제시하지 않는 호출이
            # 무엇을 하는지가 이 단언의 대상이다.
            unbound = _weightclass("run", "--policy", str(policy_path), task="Fix a typo.")

        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(accepted.stdout, "reviewed-worker\n")
        self.assertEqual(refused.returncode, 6)
        self.assertEqual(json.loads(refused.stderr), {"error": "route_fingerprint_mismatch"})
        self.assertEqual(refused.stdout, "")
        # 지문을 생략하면 실행 자체가 성립하지 않는다. 정책이 바뀌었는지와 무관하게,
        # 검토를 거치지 않은 실행 경로가 남아 있으면 이 결합 전체가 선택 사항이 된다.
        self.assertEqual(unbound.returncode, 6)
        self.assertEqual(json.loads(unbound.stderr), {"error": "route_fingerprint_mismatch"})
        self.assertEqual(unbound.stdout, "")

    def test_binds_every_field_the_fingerprint_claims_to_cover(self) -> None:
        """Breaks if a bound field is dropped from the fingerprint.

        명령만 바꿔 검증하면 route id 나 allow_mixed_vendors 가 지문에서 빠져도
        테스트가 통과한다. 명령을 동일하게 둔 채 나머지 필드를 하나씩 바꾼다.
        """
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            worker_path = directory / "worker.py"
            worker_path.write_text(
                "import sys\nsys.stdin.buffer.read()\nprint('ran')\n", encoding="utf-8"
            )
            shared_command = [sys.executable, str(worker_path)]
            policy_path = directory / "policy.json"

            def write_policy(prefix: str, allow_mixed_vendors: bool) -> None:
                policy_path.write_text(
                    json.dumps(
                        {
                            "allow_mixed_vendors": allow_mixed_vendors,
                            "routes": [
                                {
                                    "id": f"{prefix}-{tier}",
                                    "vendor": "codex",
                                    "tier": tier,
                                    "command": shared_command,
                                }
                                for tier in ("low", "standard", "high")
                            ],
                        }
                    ),
                    encoding="utf-8",
                )

            def route_fingerprint_for(task: str) -> str:
                review = subprocess.run(
                    [sys.executable, "-m", "weightclass", "route", "--policy", str(policy_path)],
                    capture_output=True,
                    check=False,
                    input=task,
                    text=True,
                )
                self.assertEqual(review.returncode, 0, review.stderr)
                return str(json.loads(review.stdout)["route_fingerprint"])

            def run_with(task: str, fingerprint: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "weightclass",
                        "run",
                        "--policy",
                        str(policy_path),
                        "--ack-route-fingerprint",
                        fingerprint,
                    ],
                    capture_output=True,
                    check=False,
                    input=task,
                    text=True,
                )

            def write_single_vendor_policy(vendor: str) -> None:
                policy_path.write_text(
                    json.dumps(
                        {
                            "routes": [
                                {
                                    "id": "shared-low",
                                    "vendor": vendor,
                                    "tier": "low",
                                    "command": shared_command,
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )

            write_policy("codex", allow_mixed_vendors=False)
            baseline = route_fingerprint_for("Fix a typo.")

            # 0. 벤더만 다르다. route id 와 명령은 글자 그대로 같다.
            write_single_vendor_policy("codex")
            vendor_baseline = route_fingerprint_for("Fix a typo.")
            write_single_vendor_policy("claude")
            other_vendor = run_with("Fix a typo.", vendor_baseline)

            # 1. route id 만 다르다. 명령은 글자 그대로 같다.
            write_policy("renamed", allow_mixed_vendors=False)
            renamed_id = run_with("Fix a typo.", baseline)

            # 2. allow_mixed_vendors 만 다르다.
            write_policy("codex", allow_mixed_vendors=True)
            flipped_mixing = run_with("Fix a typo.", baseline)

            # 3. 티어만 다르다. 세 티어의 명령이 모두 같으므로 명령은 동일하다.
            write_policy("codex", allow_mixed_vendors=False)
            other_tier = run_with("Review the authorization boundary.", baseline)

            unchanged = run_with("Fix a typo.", baseline)

        for label, result in (
            ("vendor", other_vendor),
            ("route id", renamed_id),
            ("allow_mixed_vendors", flipped_mixing),
            ("tier", other_tier),
        ):
            with self.subTest(changed=label):
                self.assertEqual(result.returncode, 6)
                self.assertEqual(json.loads(result.stderr), {"error": "route_fingerprint_mismatch"})
        self.assertEqual(unchanged.returncode, 0, unchanged.stderr)

    def test_reproduces_its_fingerprint_from_the_rendered_route_alone(self) -> None:
        """Breaks if the fingerprint binds an input the review descriptor never shows.

        검토자가 route 출력만으로 지문을 재현할 수 없으면 감사할 수 없고,
        동일한 선택이 거부되는 오탐이 생긴다.
        """
        task = "Fix a typo."
        without_vendor = subprocess.run(
            [sys.executable, "-m", "weightclass", "route"],
            capture_output=True,
            check=False,
            input=task,
            text=True,
        )
        with_vendor = subprocess.run(
            [sys.executable, "-m", "weightclass", "route", "--source-vendor", "codex"],
            capture_output=True,
            check=False,
            input=task,
            text=True,
        )

        self.assertEqual(without_vendor.returncode, 0, without_vendor.stderr)
        self.assertEqual(with_vendor.returncode, 0, with_vendor.stderr)
        self.assertEqual(json.loads(without_vendor.stdout), json.loads(with_vendor.stdout))

    def test_accepts_a_command_argument_containing_spaces(self) -> None:
        """Breaks if an install path with spaces or a multi-word flag value is refused."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            spaced_directory = directory / "My Tools"
            spaced_directory.mkdir()
            worker_path = spaced_directory / "worker.py"
            worker_path.write_text(
                "import sys\nsys.stdin.buffer.read()\nprint(sys.argv[1])\n",
                encoding="utf-8",
            )
            policy_path = directory / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": "codex-low",
                                "vendor": "codex",
                                "tier": "low",
                                "command": [sys.executable, str(worker_path), "be terse"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = reviewed_run(policy_path, "Fix a typo.")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "be terse\n")

    def test_rejects_a_command_argument_with_invisible_characters(self) -> None:
        """Breaks if a token that a reviewer cannot see reaches the executor.

        NUL 은 검증을 통과하면 exec 단계에서 ValueError 로 터져 진단 없이
        트레이스백을 남긴다. 개행과 앞뒤 공백은 route 출력에서 드러나지 않아
        검토를 무력화한다.
        """
        invisible_arguments = (
            "a\x00b",  # NUL: 검증을 통과하면 exec 에서 ValueError 로 터진다
            "a\nb",  # 개행
            "a\tb",  # 탭
            "a\x1bb",  # ESC (C0)
            "a\x9bb",  # CSI (C1)
            "\ud800",  # lone surrogate: exec 에서 UnicodeEncodeError
            "a\u200bb",  # zero-width space
            "a\u202eb",  # RTL override
            "a\u00a0b",  # NBSP: 스페이스로 보이지만 다른 인자
            " /bin/echo",  # 앞 공백
            "/bin/echo ",  # 뒤 공백
        )
        for argument in invisible_arguments:
            with self.subTest(argument=argument):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    policy_path = Path(temporary_directory) / "policy.json"
                    policy_path.write_text(
                        json.dumps(
                            {
                                "routes": [
                                    {
                                        "id": "codex-low",
                                        "vendor": "codex",
                                        "tier": "low",
                                        "command": ["/bin/echo", argument],
                                    }
                                ]
                            }
                        ),
                        encoding="utf-8",
                    )

                    result = reviewed_run(policy_path, "Fix a typo.")

                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})
                self.assertNotIn("Traceback", result.stderr)

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

            result = reviewed_run(policy_path, "Fix a typo.")

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
                    "weightclass",
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
        # render 는 태스크를 읽지 않으므로 티어도 지문도 없다.
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
                    "weightclass",
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
                    "weightclass",
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
