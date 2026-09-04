import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from tests.runtime_guard import guarded_launch
from weightclass import cli, router
from weightclass.agent_discovery import resolve_builtin_executable
from weightclass.classification import Tier
from weightclass.process_context import ChildStatusLostError
from weightclass.router import (
    BUILT_IN_VENDORS,
    DEFAULT_ROUTES,
    Route,
    RouteRequest,
    RouteSelectionError,
    native_route_fingerprint,
    select_route,
    select_tier_route,
)

_ROUTER_TEST_BIN: Path | None = None
_ORIGINAL_PATH: str | None = None


def setUpModule() -> None:
    """Keep built-in route tests independent of locally installed vendor CLIs."""
    global _ORIGINAL_PATH, _ROUTER_TEST_BIN
    _ORIGINAL_PATH = os.environ.get("PATH")
    _ROUTER_TEST_BIN = Path(tempfile.mkdtemp(prefix="weightclass-router-bin-"))
    for vendor in BUILT_IN_VENDORS:
        executable = _ROUTER_TEST_BIN / vendor
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o700)
    suffix = _ORIGINAL_PATH if _ORIGINAL_PATH is not None else os.defpath
    os.environ["PATH"] = f"{_ROUTER_TEST_BIN}{os.pathsep}{suffix}"


def tearDownModule() -> None:
    global _ORIGINAL_PATH, _ROUTER_TEST_BIN
    if _ORIGINAL_PATH is None:
        os.environ.pop("PATH", None)
    else:
        os.environ["PATH"] = _ORIGINAL_PATH
    if _ROUTER_TEST_BIN is not None:
        for vendor in BUILT_IN_VENDORS:
            (_ROUTER_TEST_BIN / vendor).unlink(missing_ok=True)
        _ROUTER_TEST_BIN.rmdir()
    _ORIGINAL_PATH = None
    _ROUTER_TEST_BIN = None


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
    tier_arguments = () if "--tier" in extra else ("--suggest-tier",)
    review = _weightclass(
        "route", "--suggest-tier", "--policy", str(policy_path), *tier_arguments, *extra, task=task
    )
    if review.returncode != 0:
        return review
    reviewed = json.loads(review.stdout)
    # 검토가 제안을 받았다면 실행은 그 값을 고정한다. 다시 분류하게 두면 검토한
    # 것과 다른 티어로 시작될 수 있고, 그때 지문 확인만으로는 잡히지 않는다.
    run_tier = () if "--tier" in extra else ("--tier", str(reviewed["tier"]))
    return _weightclass(
        "run",
        "--tier",
        "low",
        "--policy",
        str(policy_path),
        "--ack-route-fingerprint",
        reviewed["route_fingerprint"],
        *run_tier,
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
                mock.patch.object(cli, "run_owned_foreground", side_effect=raised),
                mock.patch(
                    "weightclass.cli.read_task_from_standard_input", return_value="Fix a typo."
                ),
                contextlib.redirect_stderr(errors),
            ):
                exit_code = cli.run_from_standard_input(
                    policy_path, None, fingerprint, explicit_tier="low"
                )

        self.assertEqual(exit_code, 4)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "executor_unavailable"})

    def test_maps_an_argv_encoding_failure_to_a_redacted_diagnostic(self) -> None:
        """Breaks if a validator gap can reach exec and raise instead of failing closed."""
        self._assert_maps_to_executor_unavailable(ValueError("embedded null byte"))

    def test_maps_a_missing_executable_to_a_redacted_diagnostic(self) -> None:
        """Breaks if the pre-existing OSError path stops being handled."""
        self._assert_maps_to_executor_unavailable(FileNotFoundError("no such file"))

    def test_maps_lost_child_status_to_executor_failed(self) -> None:
        """Breaks if ECHILD after spawn is mislabeled as a launch failure or success."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            policy_path = Path(temporary_directory) / "policy.json"
            route = Route(
                route_id="codex-low",
                vendor="codex",
                workflow="",
                command=(sys.executable, "-c", "pass"),
                tier="low",
            )
            policy_path.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": route.route_id,
                                "vendor": route.vendor,
                                "tier": route.tier,
                                "command": list(route.command),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            fingerprint = native_route_fingerprint(route, False)
            errors = io.StringIO()
            raised = ChildStatusLostError()
            with (
                mock.patch.object(cli, "run_owned_foreground", side_effect=raised),
                mock.patch(
                    "weightclass.cli.read_task_from_standard_input", return_value="Fix a typo."
                ),
                contextlib.redirect_stderr(errors),
            ):
                exit_code = cli.run_from_standard_input(
                    policy_path, None, fingerprint, explicit_tier="low"
                )

        self.assertEqual(exit_code, 7)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "executor_failed"})


class LegacyProcessContextTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(signal, "SIGCHLD"), "requires SIGCHLD")
    def test_auto_reaping_context_stops_legacy_run_before_task_input(self) -> None:
        """Breaks if auto-reaping folds a failed legacy child into exit zero."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            policy_path = Path(temporary_directory) / "policy.json"
            command = (sys.executable, "-c", "raise SystemExit(17)")
            policy_path.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": "codex-low",
                                "vendor": "codex",
                                "tier": "low",
                                "command": list(command),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            fingerprint = native_route_fingerprint(
                Route(
                    route_id="codex-low",
                    vendor="codex",
                    workflow="",
                    command=command,
                    tier="low",
                ),
                False,
            )
            errors = io.StringIO()
            previous_sigchld = signal.signal(signal.SIGCHLD, signal.SIG_IGN)
            try:
                with (
                    mock.patch(
                        "weightclass.cli.read_task_from_standard_input", return_value="Fix a typo."
                    ) as reader,
                    contextlib.redirect_stderr(errors),
                ):
                    exit_code = cli.run_from_standard_input(
                        policy_path, None, fingerprint, explicit_tier="low"
                    )
            finally:
                signal.signal(signal.SIGCHLD, previous_sigchld)

        self.assertEqual(exit_code, 4)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "executor_unavailable"})
        reader.assert_not_called()


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
                "--tier",
                "low",
                "--policy",
                str(self._policy(Path(temporary_directory))),
                task="Fix a typo.",
            )

        self.assertEqual(result.returncode, 6)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(result.stderr), {"error": "route_fingerprint_mismatch"})

    def test_an_automatic_cost_policy_still_requires_an_acknowledgement(self) -> None:
        """Breaks if enabling a packaged experiment also authorizes execution."""
        result = _weightclass(
            "run",
            "--cost-focused",
            "--source-vendor",
            "codex",
            "--tier",
            "standard",
            task="",
        )

        self.assertEqual(result.returncode, 6)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(result.stderr), {"error": "route_fingerprint_mismatch"})

    def test_a_preset_run_still_requires_an_acknowledgement(self) -> None:
        """Breaks if the preset shorthand accidentally authorizes execution."""
        result = _weightclass(
            "run",
            "--preset",
            "codex-cost-focused",
            "--tier",
            "standard",
            task="",
        )

        self.assertEqual(result.returncode, 6)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(result.stderr), {"error": "route_fingerprint_mismatch"})

    def test_an_acknowledged_automatic_codex_policy_runs_its_bound_model(self) -> None:
        """Breaks if run selects a different automatic command than route reviewed."""
        errors = io.StringIO()
        completed = subprocess.CompletedProcess[bytes]((), 0)
        task_input = io.TextIOWrapper(io.BytesIO(b"Fix a typo."), encoding="utf-8")
        expected_command = (
            resolve_builtin_executable("codex"),
            "exec",
            "--ephemeral",
            "--sandbox",
            "workspace-write",
            "--model",
            "reviewed-codex-model",
            "-c",
            "model_reasoning_effort=medium",
            "-",
        )
        expected_route = Route(
            route_id="codex-cost-experiment-standard",
            vendor="codex",
            workflow="",
            command=expected_command,
            tier="standard",
        )
        with (
            mock.patch.object(sys, "stdin", task_input),
            mock.patch.object(cli, "run_owned_foreground", return_value=completed) as spawn,
            contextlib.redirect_stderr(errors),
        ):
            exit_code = cli.main(
                [
                    "run",
                    "--cost-focused",
                    "--source-vendor",
                    "codex",
                    "--model",
                    "reviewed-codex-model",
                    "--tier",
                    "standard",
                    "--ack-route-fingerprint",
                    native_route_fingerprint(expected_route, False, "balanced"),
                ]
            )

        self.assertEqual(exit_code, 0, errors.getvalue())
        spawn.assert_called_once_with(
            expected_command,
            b"Fix a typo.",
            cleanup_grace_seconds=0,
            terminate_grace_seconds=0,
        )

    def test_an_acknowledged_custom_preset_runs_the_exact_reviewed_tier(self) -> None:
        """Breaks if custom preset review and execution build different argv."""
        task = "Fix a typo."
        options = (
            "--preset",
            "codex-cost-focused",
            "--tier",
            "standard",
            "--standard-model",
            "codex-standard-model",
            "--standard-effort",
            "medium",
        )
        review = _weightclass("route", *options, task=task)
        self.assertEqual(review.returncode, 0, review.stderr)
        descriptor = json.loads(review.stdout)
        self.assertEqual(descriptor["configuration_status"], "unqualified_custom")

        errors = io.StringIO()
        completed = subprocess.CompletedProcess[bytes]((), 0)
        task_input = io.TextIOWrapper(io.BytesIO(task.encode("utf-8")), encoding="utf-8")
        with (
            mock.patch.object(sys, "stdin", task_input),
            mock.patch.object(cli, "run_owned_foreground", return_value=completed) as spawn,
            contextlib.redirect_stderr(errors),
        ):
            exit_code = cli.main(
                [
                    "run",
                    "--tier",
                    "low",
                    *options,
                    "--ack-route-fingerprint",
                    descriptor["route_fingerprint"],
                ]
            )

        self.assertEqual(exit_code, 0, errors.getvalue())
        spawn.assert_called_once_with(
            tuple(descriptor["command"]),
            task.encode("utf-8"),
            cleanup_grace_seconds=0,
            terminate_grace_seconds=0,
        )

    def test_an_acknowledged_grok_model_preset_runs_the_reviewed_argv_route(self) -> None:
        """Breaks if Grok model review drifts from task substitution at execution."""
        task = "Fix a typo."
        options = (
            "--preset",
            "grok-cost-focused",
            "--tier",
            "standard",
            "--standard-model",
            "grok-standard-model",
        )
        review = _weightclass("route", *options, task=task)
        self.assertEqual(review.returncode, 0, review.stderr)
        descriptor = json.loads(review.stdout)
        self.assertEqual(
            descriptor,
            {
                "command": [
                    resolve_builtin_executable("grok"),
                    "-p",
                    "{{task}}",
                    "--permission-mode",
                    "acceptEdits",
                    "--model",
                    "grok-standard-model",
                    "--reasoning-effort",
                    "medium",
                ],
                "route": "grok-cost-experiment-standard",
                "tier": "standard",
                "vendor": "grok",
                "route_fingerprint": descriptor["route_fingerprint"],
                "posture": "balanced",
                "reason_code": "explicit.requested_tier",
                "configuration_status": "unqualified_custom",
                "task_delivery": "argv",
            },
        )
        self.assertTrue(descriptor["route_fingerprint"].startswith("sha256:"))
        self.assertNotIn(task, review.stdout)

        errors = io.StringIO()
        completed = subprocess.CompletedProcess[bytes]((), 0)
        task_input = io.TextIOWrapper(io.BytesIO(task.encode("utf-8")), encoding="utf-8")
        with (
            mock.patch.object(sys, "stdin", task_input),
            mock.patch.object(cli, "run_owned_foreground", return_value=completed) as spawn,
            contextlib.redirect_stderr(errors),
        ):
            exit_code = cli.main(
                [
                    "run",
                    "--tier",
                    "low",
                    *options,
                    "--ack-route-fingerprint",
                    descriptor["route_fingerprint"],
                ]
            )

        self.assertEqual(exit_code, 0, errors.getvalue())
        spawn.assert_called_once_with(
            (
                resolve_builtin_executable("grok"),
                "-p",
                task,
                "--permission-mode",
                "acceptEdits",
                "--model",
                "grok-standard-model",
                "--reasoning-effort",
                "medium",
            ),
            b"",
            cleanup_grace_seconds=0,
            terminate_grace_seconds=0,
        )

    def test_the_refusal_happens_before_the_task_is_read(self) -> None:
        """Breaks if a doomed run consumes the task before failing closed."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            policy_path = self._policy(Path(temporary_directory))
            errors = io.StringIO()
            with (
                mock.patch("weightclass.cli.read_task_from_standard_input") as reader,
                mock.patch.object(cli, "run_owned_foreground") as spawn,
                contextlib.redirect_stderr(errors),
            ):
                exit_code = cli.run_from_standard_input(policy_path, None, explicit_tier="low")

        self.assertEqual(exit_code, 6)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "route_fingerprint_mismatch"})
        reader.assert_not_called()
        spawn.assert_not_called()

    def test_interactive_review_runs_a_policy_without_a_copied_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            policy_path = self._policy(Path(temporary_directory))
            completed = subprocess.CompletedProcess[bytes]((), 0)
            with (
                mock.patch(
                    "weightclass.cli.read_task_from_standard_input",
                    return_value="Fix a typo.",
                ),
                mock.patch.object(
                    cli,
                    "_confirm_legacy_route_on_console",
                    return_value=True,
                ) as confirmation,
                mock.patch.object(
                    cli,
                    "run_owned_foreground",
                    return_value=completed,
                ) as spawn,
            ):
                exit_code = cli.run_from_standard_input(
                    policy_path,
                    "codex",
                    interactive_review=True,
                )

        self.assertEqual(exit_code, 0)
        confirmation.assert_called_once()
        spawn.assert_called_once_with(
            ("/bin/echo", "ok"),
            b"Fix a typo.",
            cleanup_grace_seconds=0,
            terminate_grace_seconds=0,
        )

    def test_interactive_review_cancellation_starts_no_vendor(self) -> None:
        errors = io.StringIO()
        with (
            mock.patch(
                "weightclass.cli.read_task_from_standard_input",
                return_value="private task",
            ),
            mock.patch.object(
                cli,
                "_confirm_legacy_route_on_console",
                return_value=False,
            ),
            mock.patch.object(cli, "run_owned_foreground") as spawn,
            contextlib.redirect_stderr(errors),
        ):
            exit_code = cli.run_from_standard_input(
                None,
                "codex",
                interactive_review=True,
            )

        self.assertEqual(exit_code, 1)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "execution_cancelled"})
        spawn.assert_not_called()

    def test_interactive_review_and_copied_fingerprint_are_mutually_exclusive(self) -> None:
        errors = io.StringIO()
        with (
            mock.patch("weightclass.cli.read_task_from_standard_input") as reader,
            contextlib.redirect_stderr(errors),
        ):
            exit_code = cli.run_from_standard_input(
                None,
                "codex",
                "sha256:" + "0" * 64,
                interactive_review=True,
            )

        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "invalid_input"})
        reader.assert_not_called()

    def test_guided_builtin_run_rechecks_the_resolved_path_after_confirmation(self) -> None:
        errors = io.StringIO()
        first = Route("codex-low", "codex", "", ("/first/codex", "-"), "low")
        changed = Route("codex-low", "codex", "", ("/changed/codex", "-"), "low")
        with (
            mock.patch(
                "weightclass.cli.read_task_from_standard_input",
                return_value="Fix a typo.",
            ),
            mock.patch.object(
                cli,
                "_resolve_default_route_executable",
                side_effect=(first, changed),
            ),
            mock.patch.object(
                cli,
                "_confirm_legacy_route_on_console",
                return_value=True,
            ),
            mock.patch.object(cli, "run_owned_foreground") as spawn,
            contextlib.redirect_stderr(errors),
        ):
            exit_code = cli.run_from_standard_input(
                None,
                "codex",
                interactive_review=True,
            )

        self.assertEqual(exit_code, 6)
        self.assertEqual(
            json.loads(errors.getvalue()),
            {"error": "route_fingerprint_mismatch"},
        )
        spawn.assert_not_called()

    def test_built_in_routes_still_run_without_an_acknowledgement(self) -> None:
        """Breaks if the requirement spreads to routes that live in code.

        기본 라우트는 코드에 고정되어 교체할 수 없으므로 묶을 대상이 없다.
        여기까지 지문을 요구하면 검토와 무관한 마찰만 생긴다.
        """
        errors = io.StringIO()
        with (
            mock.patch("weightclass.cli.read_task_from_standard_input", return_value="Fix a typo."),
            mock.patch.object(
                cli,
                "run_owned_foreground",
                return_value=subprocess.CompletedProcess((), 0),
            ) as spawn,
            contextlib.redirect_stderr(errors),
        ):
            exit_code = cli.run_from_standard_input(None, "codex", explicit_tier="low")

        self.assertEqual(exit_code, 0, errors.getvalue())
        spawn.assert_called_once()

    def test_a_stale_acknowledgement_is_still_refused(self) -> None:
        """Breaks if requiring the flag replaced verifying its value."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            result = _weightclass(
                "run",
                "--tier",
                "low",
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

    def test_the_token_cannot_replace_the_executable(self) -> None:
        """Breaks if untrusted task text can become argv[0]."""
        for command in (["{{task}}"], ["{{task}}", "--fixed-argument"]):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                path = self._policy(Path(directory), command)
                with self.assertRaises(cli.InvalidInputError):
                    cli.load_routes(path)
                result = _weightclass(
                    "route",
                    "--suggest-tier",
                    "--policy",
                    str(path),
                    "--source-vendor",
                    "codex",
                    task="/bin/echo",
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})

    def test_the_token_inside_a_larger_argument_is_rejected(self) -> None:
        """Breaks if how the task and a flag were joined becomes ambiguous."""
        for argument in ("--prompt={{task}}", "prefix{{task}}", "{{task}}suffix"):
            with self.subTest(argument=argument), tempfile.TemporaryDirectory() as directory:
                path = self._policy(Path(directory), ["/bin/echo", argument])
                with self.assertRaises(cli.InvalidInputError):
                    cli.load_routes(path)

    def test_route_identifiers_reject_terminal_control_characters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": "visible\x1b[8mconcealed",
                                "vendor": "codex",
                                "tier": "low",
                                "command": ["/bin/echo"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(cli.InvalidInputError):
                cli.load_routes(policy_path)

    def test_the_token_is_rejected_in_a_workflow_route(self) -> None:
        """Breaks if a workflow route can declare a slot nothing ever fills.

        select_tier_route 만 티어 라우트를 대상으로 치환을 준비하므로, workflow
        라우트에 이 토큰을 허용하면 render 가 미치환 리터럴 "{{task}}" 를 그대로
        보여준다. 파싱 단계에서 닫아야 검토 산출물이 항상 실제 실행을 반영한다.
        """
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": "r",
                                "vendor": "codex",
                                "workflow": "review",
                                "command": ["/bin/echo", "{{task}}"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(cli.InvalidInputError):
                cli.load_routes(policy_path)

    def test_substitution_fills_exactly_the_reserved_slot(self) -> None:
        """Breaks if substitution touches an argument it was not given."""
        filled = router.substitute_task(("agy", "--print", "{{task}}", "--effort"), "긴 태스크")

        self.assertEqual(filled, ("agy", "--print", "긴 태스크", "--effort"))

    def _recorder(self, directory: Path) -> Path:
        """자식이 받은 argv 와 stdin 을 그대로 파일에 적는 가짜 실행 파일."""
        recorder = directory / "recorder.py"
        recorder.write_text(
            "import json, sys\n"
            "record = {'argv': sys.argv[1:], 'stdin': sys.stdin.read()}\n"
            "open(sys.argv[1], 'w', encoding='utf-8').write(json.dumps(record))\n",
            encoding="utf-8",
        )
        return recorder

    def test_argv_delivery_puts_the_task_in_argv_and_leaves_stdin_empty(self) -> None:
        """Breaks if the task is delivered twice or in the wrong channel."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recorder = self._recorder(root)
            record_path = root / "record.json"
            policy_path = self._policy(
                root,
                [sys.executable, str(recorder), str(record_path), "{{task}}"],
            )

            result = reviewed_run(policy_path, "Fix a typo.")
            recorded = json.loads(record_path.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(recorded["argv"][-1], "Fix a typo.")
        self.assertEqual(recorded["stdin"], "")

    def test_stdin_delivery_is_unchanged(self) -> None:
        """Breaks if adding argv delivery altered the path every existing policy uses."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recorder = self._recorder(root)
            record_path = root / "record.json"
            policy_path = self._policy(root, [sys.executable, str(recorder), str(record_path)])

            result = reviewed_run(policy_path, "Fix a typo.")
            recorded = json.loads(record_path.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(recorded["stdin"], "Fix a typo.")
        self.assertNotIn("Fix a typo.", recorded["argv"])

    def test_a_task_carrying_nul_is_refused_before_spawn(self) -> None:
        """Breaks if an argv-delivery run reaches execve with a byte it cannot carry."""
        with tempfile.TemporaryDirectory() as directory:
            policy_path = self._policy(Path(directory), ["/bin/echo", "{{task}}"])
            result = reviewed_run(policy_path, "Fix a typo.\x00second part")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_task"})

    def test_stdin_delivery_accepts_nul_bytes(self) -> None:
        """Breaks if stdin-delivery path rejects valid input that execve never sees."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recorder = self._recorder(root)
            record_path = root / "record.json"
            policy_path = self._policy(root, [sys.executable, str(recorder), str(record_path)])

            result = reviewed_run(policy_path, "Fix a typo.\x00second part")
            recorded = json.loads(record_path.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(recorded["stdin"], "Fix a typo.\x00second part")

    def test_review_names_argv_delivery_and_never_shows_the_task(
        self,
    ) -> None:
        """Breaks if a reviewer cannot see that this route puts the task on the command line."""
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            policy_path = directory_path / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": "r-low",
                                "vendor": "codex",
                                "tier": "low",
                                "command": ["/bin/echo", "low"],
                            },
                            {
                                "id": "r",
                                "vendor": "codex",
                                "tier": "standard",
                                "command": ["/bin/echo", "{{task}}"],
                            },
                            {
                                "id": "r-high",
                                "vendor": "codex",
                                "tier": "high",
                                "command": ["/bin/echo", "high"],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            review = _weightclass(
                "route", "--suggest-tier", "--policy", str(policy_path), task="비밀 태스크"
            )

        rendered = json.loads(review.stdout)
        self.assertEqual(review.returncode, 0, review.stderr)
        self.assertEqual(rendered["task_delivery"], "argv")
        self.assertEqual(rendered["command"], ["/bin/echo", "{{task}}"])
        self.assertNotIn("비밀 태스크", review.stdout)

    def test_review_omits_the_key_for_stdin_delivery(self) -> None:
        """Breaks if every existing review output grows a field it never had."""
        with tempfile.TemporaryDirectory() as directory:
            policy_path = self._policy(Path(directory), ["/bin/echo", "ok"])
            review = _weightclass(
                "route", "--suggest-tier", "--policy", str(policy_path), task="Fix a typo."
            )

        self.assertNotIn("task_delivery", json.loads(review.stdout))

    def test_two_tasks_at_one_tier_share_one_fingerprint(self) -> None:
        """Breaks if the fingerprint starts covering substituted task text.

        지문이 태스크마다 달라지면 한 번의 검토가 한 번의 실행만 묶게 되고,
        태스크의 해시를 남기지 않는다는 규칙도 사실상 깨진다.
        """
        with tempfile.TemporaryDirectory() as directory:
            policy_path = self._policy(Path(directory), ["/bin/echo", "{{task}}"])
            first = _weightclass(
                "route", "--suggest-tier", "--policy", str(policy_path), task="Fix a typo."
            )
            second = _weightclass(
                "route", "--suggest-tier", "--policy", str(policy_path), task="Rename a var."
            )

        self.assertEqual(
            json.loads(first.stdout)["route_fingerprint"],
            json.loads(second.stdout)["route_fingerprint"],
        )


class AgyBuiltInRouteTests(unittest.TestCase):
    def test_every_tier_has_an_agy_route_that_carries_the_task_in_argv(self) -> None:
        """Breaks if a tier is missing or the built-in stops declaring its task slot."""
        for tier, effort in (("low", "low"), ("standard", "medium"), ("high", "high")):
            with self.subTest(tier=tier):
                route = select_tier_route(DEFAULT_ROUTES, cast(Tier, tier), "agy")

                self.assertEqual(route.vendor, "agy")
                self.assertEqual(route.command[0], "agy")
                self.assertIn(router.TASK_PLACEHOLDER, route.command)
                self.assertEqual(route.command[route.command.index("--effort") + 1], effort)
                self.assertIn("--mode", route.command)
                self.assertEqual(route.command[route.command.index("--mode") + 1], "accept-edits")

    def test_agy_is_a_supported_source_vendor(self) -> None:
        """Breaks if the vendor label is rejected by the surfaces that gate routing."""
        self.assertIn("agy", BUILT_IN_VENDORS)

    def test_the_default_vendor_is_still_codex(self) -> None:
        """Breaks if adding a vendor changed which route an unqualified call selects."""
        route = select_tier_route(DEFAULT_ROUTES, "low")

        self.assertEqual(route.vendor, "codex")


class GrokBuiltInRouteTests(unittest.TestCase):
    def test_every_tier_has_a_grok_route_that_carries_the_task_in_argv(self) -> None:
        """Breaks if a tier is missing or the built-in stops declaring its task slot."""
        for tier, effort in (("low", "low"), ("standard", "medium"), ("high", "high")):
            with self.subTest(tier=tier):
                route = select_tier_route(DEFAULT_ROUTES, cast(Tier, tier), "grok")

                self.assertEqual(route.vendor, "grok")
                self.assertEqual(route.command[0], "grok")
                self.assertIn(router.TASK_PLACEHOLDER, route.command)
                index = route.command.index("--reasoning-effort")
                self.assertEqual(route.command[index + 1], effort)
                mode = route.command.index("--permission-mode")
                self.assertEqual(route.command[mode + 1], "acceptEdits")

    def test_grok_is_a_supported_source_vendor(self) -> None:
        """Breaks if the vendor label is rejected by the surfaces that gate routing."""
        self.assertIn("grok", BUILT_IN_VENDORS)

    def test_the_built_in_does_not_assert_an_unverified_sandbox_profile(self) -> None:
        """Breaks if a profile value nobody measured is baked into a shipped command."""
        route = select_tier_route(DEFAULT_ROUTES, "high", "grok")

        self.assertNotIn("--sandbox", route.command)


class OpenVendorLabelTests(unittest.TestCase):
    """벤더 라벨은 격리 식별자이지 이 패키지가 아는 도구 목록이 아니다.

    select_tier_route 는 문자열을 비교하고 native_route_fingerprint 는 문자열을
    해싱할 뿐이다. 둘 다 벤더별 지식을 갖고 있지 않으므로, 목록을 닫아둘 근거는
    "명령을 함께 배포한다"뿐인데 그건 라벨과 별개다.
    """

    def _policy(self, directory: Path, routes: list[dict[str, object]]) -> Path:
        policy_path = directory / "policy.json"
        policy_path.write_text(json.dumps({"routes": routes}), encoding="utf-8")
        return policy_path

    def test_an_unknown_vendor_label_is_accepted(self) -> None:
        """Breaks if a user cannot bring an agent this package never heard of."""
        with tempfile.TemporaryDirectory() as directory:
            path = self._policy(
                Path(directory),
                [
                    {
                        "id": "r",
                        "vendor": "qwen",
                        "tier": "low",
                        "command": ["/bin/echo", "{{task}}"],
                    }
                ],
            )
            routes = cli.load_routes(path)

        self.assertEqual(routes[0].vendor, "qwen")

    def test_a_malformed_vendor_label_is_rejected(self) -> None:
        """Breaks if the label stops being a reviewable identifier."""
        for label in ("", "two words", "a" * 65, "tab\there", "  padded  "):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                path = self._policy(
                    Path(directory),
                    [{"id": "r", "vendor": label, "tier": "low", "command": ["/bin/echo"]}],
                )
                with self.assertRaises(cli.InvalidInputError):
                    cli.load_routes(path)

    def test_a_malformed_source_vendor_is_rejected_before_any_subcommand_runs(self) -> None:
        """Breaks if main()'s only guard against a garbled --source-vendor stops firing.

        라벨이 열려 있어 argparse choices 가 오타를 잡아주지 못하는 서브커맨드에서,
        main() 의 형식 검사가 라우트 선택까지 내려가기 전에 거부해야 한다.
        """
        result = _weightclass("classify", "--source-vendor", "bad vendor", task="Fix a typo.")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})

    def test_a_well_formed_but_unrouted_source_vendor_fails_closed_at_selection(self) -> None:
        """Breaks if a syntactically valid label that matches no route is silently accepted."""
        result = _weightclass(
            "route", "--suggest-tier", "--source-vendor", "qwen", task="Fix a typo."
        )

        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stderr), {"error": "unsupported_route"})

    def test_containment_still_holds_for_unknown_labels(self) -> None:
        """Breaks if opening the label also opened the boundary it exists to enforce."""
        with tempfile.TemporaryDirectory() as directory:
            path = self._policy(
                Path(directory),
                [
                    {"id": "a", "vendor": "qwen", "tier": "low", "command": ["/bin/echo"]},
                    {"id": "b", "vendor": "kimi", "tier": "high", "command": ["/bin/echo"]},
                ],
            )
            routes = cli.load_routes(path)

            with self.assertRaises(RouteSelectionError):
                select_tier_route(routes, "high", "qwen")

            self.assertEqual(select_tier_route(routes, "low", "qwen").route_id, "a")

    def test_the_fingerprint_still_separates_two_unknown_vendors(self) -> None:
        """Breaks if the vendor stops being bound, letting a review cover another vendor."""
        # mypy --strict: 값 타입이 섞인 딕셔너리를 **로 풀면 각 필드의 정확한
        # 타입이 사라져 Route 생성자와 맞지 않는다고 판정한다. 동작은 그대로
        # 두고 주석 타입만 명시한다.
        shared: dict[str, Any] = {"tier": "low", "command": ("/bin/echo", "ok"), "workflow": ""}
        first = native_route_fingerprint(Route(route_id="r", vendor="qwen", **shared), False)
        second = native_route_fingerprint(Route(route_id="r", vendor="kimi", **shared), False)

        self.assertNotEqual(first, second)

    def test_the_built_in_vendors_are_still_named(self) -> None:
        """Breaks if the shipped commands lose the set that documents them."""
        self.assertLessEqual(frozenset({"claude", "codex"}), BUILT_IN_VENDORS)


class CommandSurfaceTests(unittest.TestCase):
    def test_prints_the_installable_cost_focused_policy_example(self) -> None:
        """Breaks if wheel users can no longer retrieve the reviewed opt-in policy."""
        result = _weightclass("example-policy", "claude-cost-focused", task="")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        policy = json.loads(result.stdout)
        routes = {route["tier"]: route for route in policy["routes"]}
        self.assertEqual(policy["schema_version"], 1)
        self.assertEqual(
            routes["low"]["command"][-4:],
            ["--model", "haiku", "--effort", "low"],
        )
        self.assertNotIn("--model", routes["standard"]["command"])
        self.assertNotIn("--model", routes["high"]["command"])

    def test_prints_installable_cost_experiments_without_lowering_standard_effort(self) -> None:
        """Breaks if a failed standard-low experiment remains in a packaged preset."""
        expected_commands = {
            "codex-cost-focused": {
                "low": [
                    "codex",
                    "exec",
                    "--ephemeral",
                    "--sandbox",
                    "workspace-write",
                    "-c",
                    "model_reasoning_effort=low",
                    "-",
                ],
                "standard": [
                    "codex",
                    "exec",
                    "--ephemeral",
                    "--sandbox",
                    "workspace-write",
                    "-c",
                    "model_reasoning_effort=medium",
                    "-",
                ],
                "high": [
                    "codex",
                    "exec",
                    "--ephemeral",
                    "--sandbox",
                    "workspace-write",
                    "-c",
                    "model_reasoning_effort=high",
                    "-",
                ],
            },
            "agy-cost-focused": {
                "low": [
                    "agy",
                    "--print",
                    "{{task}}",
                    "--mode",
                    "accept-edits",
                    "--effort",
                    "low",
                ],
                "standard": [
                    "agy",
                    "--print",
                    "{{task}}",
                    "--mode",
                    "accept-edits",
                    "--effort",
                    "medium",
                ],
                "high": [
                    "agy",
                    "--print",
                    "{{task}}",
                    "--mode",
                    "accept-edits",
                    "--effort",
                    "high",
                ],
            },
            "grok-cost-focused": {
                "low": [
                    "grok",
                    "-p",
                    "{{task}}",
                    "--permission-mode",
                    "acceptEdits",
                    "--reasoning-effort",
                    "low",
                ],
                "standard": [
                    "grok",
                    "-p",
                    "{{task}}",
                    "--permission-mode",
                    "acceptEdits",
                    "--reasoning-effort",
                    "medium",
                ],
                "high": [
                    "grok",
                    "-p",
                    "{{task}}",
                    "--permission-mode",
                    "acceptEdits",
                    "--reasoning-effort",
                    "high",
                ],
            },
        }

        for name, commands in expected_commands.items():
            with self.subTest(name=name):
                result = _weightclass("example-policy", name, task="")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                policy = json.loads(result.stdout)
                self.assertEqual(policy["schema_version"], 1)
                self.assertFalse(policy["allow_mixed_vendors"])
                self.assertEqual(policy["posture"], "balanced")
                routes = {route["tier"]: route for route in policy["routes"]}
                self.assertEqual(set(routes), {"low", "standard", "high"})
                self.assertEqual(
                    {route["vendor"] for route in routes.values()},
                    {name.removesuffix("-cost-focused")},
                )
                self.assertEqual(
                    {tier: route["command"] for tier, route in routes.items()},
                    commands,
                )
                with tempfile.TemporaryDirectory() as directory:
                    policy_path = Path(directory) / "policy.json"
                    policy_path.write_text(result.stdout, encoding="utf-8")
                    routed = _weightclass(
                        "route",
                        "--policy",
                        str(policy_path),
                        "--source-vendor",
                        name.removesuffix("-cost-focused"),
                        "--tier",
                        "standard",
                        task="Fix a spelling typo.",
                    )
                self.assertEqual(routed.returncode, 0, routed.stderr)
                descriptor = json.loads(routed.stdout)
                self.assertEqual(descriptor["command"], commands["standard"])
                self.assertEqual(
                    descriptor.get("task_delivery"),
                    "argv" if name in {"agy-cost-focused", "grok-cost-focused"} else None,
                )
                self.assertNotIn("Fix a spelling typo.", routed.stdout)

    def test_codex_cost_experiment_accepts_an_explicit_model_with_tier_efforts(self) -> None:
        """Breaks if users cannot bind a reviewed Codex model without editing JSON."""
        result = _weightclass(
            "example-policy",
            "codex-cost-focused",
            "--model",
            "reviewed-codex-model",
            task="",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        routes = {route["tier"]: route for route in json.loads(result.stdout)["routes"]}
        expected_efforts = {"low": "low", "standard": "medium"}
        for tier, effort in expected_efforts.items():
            with self.subTest(tier=tier):
                self.assertEqual(
                    routes[tier]["command"],
                    [
                        "codex",
                        "exec",
                        "--ephemeral",
                        "--sandbox",
                        "workspace-write",
                        "--model",
                        "reviewed-codex-model",
                        "-c",
                        f"model_reasoning_effort={effort}",
                        "-",
                    ],
                )
        self.assertNotIn("--model", routes["high"]["command"])

        without_model = _weightclass("example-policy", "codex-cost-focused", task="")
        self.assertEqual(without_model.returncode, 0, without_model.stderr)
        fingerprints = []
        for policy_text in (without_model.stdout, result.stdout):
            with tempfile.TemporaryDirectory() as directory:
                policy_path = Path(directory) / "policy.json"
                policy_path.write_text(policy_text, encoding="utf-8")
                routed = _weightclass(
                    "route",
                    "--policy",
                    str(policy_path),
                    "--source-vendor",
                    "codex",
                    "--tier",
                    "standard",
                    task="private task text",
                )
            self.assertEqual(routed.returncode, 0, routed.stderr)
            self.assertNotIn("private task text", routed.stdout)
            fingerprints.append(json.loads(routed.stdout)["route_fingerprint"])
        self.assertNotEqual(*fingerprints)

    def test_example_policy_rejects_model_overrides_outside_codex(self) -> None:
        """Breaks if a generic flag silently changes an unevaluated vendor command."""
        for name in ("agy-cost-focused", "claude-cost-focused", "grok-cost-focused"):
            with self.subTest(name=name):
                result = _weightclass(
                    "example-policy",
                    name,
                    "--model",
                    "reviewed-model",
                    task="",
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, '{"error": "invalid_input"}\n')

    def test_codex_cost_experiment_rejects_an_unsafe_model_label(self) -> None:
        """Breaks if task-like or option-like text can enter a generated command."""
        for model in ("", "contains whitespace", "--option", "control\u001fvalue"):
            with self.subTest(model=model):
                result = _weightclass(
                    "example-policy",
                    "codex-cost-focused",
                    "--model",
                    model,
                    task="",
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, '{"error": "invalid_input"}\n')

    def test_cost_focused_option_selects_each_packaged_vendor_policy(self) -> None:
        """Breaks if the opt-in flag falls back to a built-in or another vendor."""
        cases = (
            (
                "claude",
                "low",
                (),
                "claude-cost-experiment-low",
                [
                    "claude",
                    "--print",
                    "--no-session-persistence",
                    "--safe-mode",
                    "--permission-mode",
                    "acceptEdits",
                    "--tools",
                    "Read,Edit,Glob,Grep",
                    "--output-format",
                    "json",
                    "--model",
                    "haiku",
                    "--effort",
                    "low",
                ],
            ),
            (
                "codex",
                "standard",
                ("--model", "reviewed-codex-model"),
                "codex-cost-experiment-standard",
                [
                    "codex",
                    "exec",
                    "--ephemeral",
                    "--sandbox",
                    "workspace-write",
                    "--model",
                    "reviewed-codex-model",
                    "-c",
                    "model_reasoning_effort=medium",
                    "-",
                ],
            ),
            (
                "agy",
                "standard",
                (),
                "agy-cost-experiment-standard",
                [
                    "agy",
                    "--print",
                    "{{task}}",
                    "--mode",
                    "accept-edits",
                    "--effort",
                    "medium",
                ],
            ),
            (
                "grok",
                "standard",
                (),
                "grok-cost-experiment-standard",
                [
                    "grok",
                    "-p",
                    "{{task}}",
                    "--permission-mode",
                    "acceptEdits",
                    "--reasoning-effort",
                    "medium",
                ],
            ),
        )

        for vendor, tier, extra, expected_route, expected_command in cases:
            with self.subTest(vendor=vendor):
                result = _weightclass(
                    "route",
                    "--cost-focused",
                    "--source-vendor",
                    vendor,
                    "--tier",
                    tier,
                    *extra,
                    task="private task text",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                descriptor = json.loads(result.stdout)
                self.assertEqual(descriptor["route"], expected_route)
                self.assertEqual(descriptor["vendor"], vendor)
                expected_command[0] = resolve_builtin_executable(vendor)
                self.assertEqual(descriptor["command"], expected_command)
                self.assertTrue(descriptor["route_fingerprint"].startswith("sha256:"))
                self.assertNotIn("private task text", result.stdout)

    def test_preset_shorthand_selects_its_vendor_without_a_separate_flag(self) -> None:
        """Breaks if preset selection requires duplicated or inferred vendor input."""
        result = _weightclass(
            "route",
            "--preset",
            "codex-cost-focused",
            "--tier",
            "standard",
            "--model",
            "reviewed-codex-model",
            task="private task text",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        descriptor = json.loads(result.stdout)
        self.assertEqual(descriptor["route"], "codex-cost-experiment-standard")
        self.assertEqual(descriptor["vendor"], "codex")
        expected_executable = resolve_builtin_executable("codex")
        self.assertEqual(
            descriptor["command"],
            [
                expected_executable,
                "exec",
                "--ephemeral",
                "--sandbox",
                "workspace-write",
                "--model",
                "reviewed-codex-model",
                "-c",
                "model_reasoning_effort=medium",
                "-",
            ],
        )
        self.assertTrue(descriptor["route_fingerprint"].startswith("sha256:"))
        self.assertNotIn("private task text", result.stdout)

    def test_review_preset_reports_every_bound_route_without_reading_a_task(self) -> None:
        """Breaks if policy review needs task content or omits execution boundaries."""
        output = io.StringIO()
        errors = io.StringIO()
        with (
            mock.patch.object(
                cli,
                "read_task_from_standard_input",
                side_effect=AssertionError("task input was read"),
            ),
            mock.patch.object(
                cli,
                "run_owned_foreground",
                side_effect=AssertionError("vendor process was started"),
            ),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            exit_code = cli.main(["review-preset", "claude-cost-focused"])

        self.assertEqual(exit_code, 0, errors.getvalue())
        descriptor = json.loads(output.getvalue())
        self.assertEqual(
            set(descriptor),
            {
                "allow_mixed_vendors",
                "configuration_status",
                "posture",
                "preset",
                "routes",
                "vendor",
            },
        )
        self.assertEqual(descriptor["preset"], "claude-cost-focused")
        self.assertEqual(descriptor["vendor"], "claude")
        self.assertEqual(descriptor["configuration_status"], "measured_low_route_only")
        self.assertFalse(descriptor["allow_mixed_vendors"])
        self.assertEqual(descriptor["posture"], "balanced")
        self.assertEqual(
            [route["route"] for route in descriptor["routes"]],
            [
                "claude-cost-experiment-low",
                "claude-cost-experiment-standard",
                "claude-cost-experiment-high",
            ],
        )
        self.assertEqual(
            [route["tier"] for route in descriptor["routes"]],
            ["low", "standard", "high"],
        )
        for route in descriptor["routes"]:
            self.assertEqual(route["vendor"], "claude")
            self.assertEqual(route["task_delivery"], "stdin")
            self.assertTrue(route["route_fingerprint"].startswith("sha256:"))
            self.assertIsInstance(route["command"], list)

    def test_review_preset_applies_claude_model_and_effort_by_tier(self) -> None:
        """Breaks if Claude tier overrides drift or retain measured status."""
        baseline = _weightclass("review-preset", "claude-cost-focused", task="")
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        result = _weightclass(
            "review-preset",
            "claude-cost-focused",
            "--low-model",
            "claude-low-model",
            "--standard-model",
            "claude-standard-model",
            "--high-model",
            "claude-high-model",
            "--low-effort",
            "minimal",
            "--standard-effort",
            "low",
            "--high-effort",
            "maximum",
            task="",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        descriptor = json.loads(result.stdout)
        self.assertEqual(descriptor["configuration_status"], "unqualified_custom")
        self.assertNotEqual(
            [route["route_fingerprint"] for route in descriptor["routes"]],
            [route["route_fingerprint"] for route in json.loads(baseline.stdout)["routes"]],
        )
        for route, model, effort in zip(
            descriptor["routes"],
            ("claude-low-model", "claude-standard-model", "claude-high-model"),
            ("minimal", "low", "maximum"),
            strict=True,
        ):
            model_index = route["command"].index("--model")
            effort_index = route["command"].index("--effort")
            self.assertEqual(route["command"][model_index + 1], model)
            self.assertEqual(route["command"][effort_index + 1], effort)

    def test_review_preset_applies_codex_model_and_effort_by_tier(self) -> None:
        """Breaks if Codex tier overrides mutate the wrong configuration tokens."""
        baseline = _weightclass("review-preset", "codex-cost-focused", task="")
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        result = _weightclass(
            "review-preset",
            "codex-cost-focused",
            "--low-model",
            "codex-low-model",
            "--standard-model",
            "codex-standard-model",
            "--high-model",
            "codex-high-model",
            "--low-effort",
            "minimal",
            "--standard-effort",
            "medium",
            "--high-effort",
            "maximum",
            task="",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        descriptor = json.loads(result.stdout)
        self.assertEqual(descriptor["configuration_status"], "unqualified_custom")
        self.assertNotEqual(
            [route["route_fingerprint"] for route in descriptor["routes"]],
            [route["route_fingerprint"] for route in json.loads(baseline.stdout)["routes"]],
        )
        for route, model, effort in zip(
            descriptor["routes"],
            ("codex-low-model", "codex-standard-model", "codex-high-model"),
            ("minimal", "medium", "maximum"),
            strict=True,
        ):
            model_index = route["command"].index("--model")
            config_index = route["command"].index("-c")
            self.assertEqual(route["command"][model_index + 1], model)
            self.assertEqual(
                route["command"][config_index + 1],
                f"model_reasoning_effort={effort}",
            )

    def test_review_preset_applies_grok_model_by_tier(self) -> None:
        """Breaks if Grok tier models share a command or lose fingerprint binding."""
        baseline = _weightclass("review-preset", "grok-cost-focused", task="")
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        result = _weightclass(
            "review-preset",
            "grok-cost-focused",
            "--low-model",
            "grok-low-model",
            "--standard-model",
            "grok-standard-model",
            "--high-model",
            "grok-high-model",
            task="",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        descriptor = json.loads(result.stdout)
        self.assertEqual(descriptor["configuration_status"], "unqualified_custom")
        self.assertNotEqual(
            [route["route_fingerprint"] for route in descriptor["routes"]],
            [route["route_fingerprint"] for route in json.loads(baseline.stdout)["routes"]],
        )
        expected_commands = (
            [
                "grok",
                "-p",
                "{{task}}",
                "--permission-mode",
                "acceptEdits",
                "--model",
                "grok-low-model",
                "--reasoning-effort",
                "low",
            ],
            [
                "grok",
                "-p",
                "{{task}}",
                "--permission-mode",
                "acceptEdits",
                "--model",
                "grok-standard-model",
                "--reasoning-effort",
                "medium",
            ],
            [
                "grok",
                "-p",
                "{{task}}",
                "--permission-mode",
                "acceptEdits",
                "--model",
                "grok-high-model",
                "--reasoning-effort",
                "high",
            ],
        )
        self.assertEqual(
            [route["command"] for route in descriptor["routes"]],
            list(expected_commands),
        )
        self.assertEqual(
            {route["task_delivery"] for route in descriptor["routes"]},
            {"argv"},
        )

    def test_preset_overrides_reject_unsupported_vendors_and_unsafe_labels(self) -> None:
        """Breaks if unreviewed argv shapes or task-like labels reach a route."""
        cases = (
            ("agy-cost-focused", "--low-model", "reviewed-model"),
            ("grok-cost-focused", "--standard-effort", "low"),
            ("grok-cost-focused", "--standard-model", ""),
            ("claude-cost-focused", "--high-model", "contains whitespace"),
            ("codex-cost-focused", "--low-effort", "control\u001fvalue"),
        )
        for preset, option, value in cases:
            with self.subTest(preset=preset, option=option):
                task = "private invalid override task"
                result = _weightclass(
                    "route",
                    "--suggest-tier",
                    "--preset",
                    preset,
                    option,
                    value,
                    task=task,
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, '{"error": "invalid_input"}\n')
                self.assertNotIn(task, result.stderr)

    def test_review_preset_labels_unqualified_vendors_and_argv_delivery(self) -> None:
        """Breaks if task-in-argv presets or their evidence scope become hidden."""
        for preset, task_delivery in (
            ("agy-cost-focused", "argv"),
            ("codex-cost-focused", "stdin"),
            ("grok-cost-focused", "argv"),
        ):
            with self.subTest(preset=preset):
                result = _weightclass("review-preset", preset, task="")
                self.assertEqual(result.returncode, 0, result.stderr)
                descriptor = json.loads(result.stdout)
                self.assertEqual(descriptor["configuration_status"], "unqualified_experiment")
                self.assertEqual(
                    {route["task_delivery"] for route in descriptor["routes"]},
                    {task_delivery},
                )

    def test_cost_focused_selector_accepts_the_same_claude_tier_overrides(self) -> None:
        """Breaks if the compatibility selector diverges from preset behavior."""
        result = _weightclass(
            "route",
            "--cost-focused",
            "--source-vendor",
            "claude",
            "--tier",
            "standard",
            "--standard-model",
            "claude-standard-model",
            "--standard-effort",
            "low",
            task="Fix a typo.",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        descriptor = json.loads(result.stdout)
        self.assertEqual(descriptor["configuration_status"], "unqualified_custom")
        self.assertEqual(
            descriptor["command"][-4:],
            ["--model", "claude-standard-model", "--effort", "low"],
        )

    def test_tier_overrides_never_mutate_a_file_backed_policy(self) -> None:
        """Breaks if a label can silently rewrite an explicitly reviewed policy file.

        내장 라우트에 라벨을 붙이는 것은 허용된다. 사용자가 검토해 파일로 고정한
        정책을 명령줄 라벨이 뒤에서 바꾸는 것은 다른 문제다. 그 경우 검토한 정책
        파일과 실행되는 명령이 달라진다.
        """
        task = "private file-backed override task"
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": "codex-low",
                                "vendor": "codex",
                                "tier": "low",
                                "command": ["codex", "exec"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            result = _weightclass(
                "route",
                "--suggest-tier",
                "--policy",
                str(policy_path),
                "--source-vendor",
                "codex",
                "--low-model",
                "codex-low-model",
                task=task,
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, '{"error": "invalid_input"}\n')
        self.assertNotIn(task, result.stderr)

    def test_legacy_and_tier_model_overrides_cannot_be_combined(self) -> None:
        """Breaks if two model selectors can silently override one another."""
        task = "private ambiguous model task"
        result = _weightclass(
            "route",
            "--suggest-tier",
            "--preset",
            "codex-cost-focused",
            "--model",
            "legacy-model",
            "--standard-model",
            "tier-model",
            task=task,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, '{"error": "invalid_input"}\n')
        self.assertNotIn(task, result.stderr)

    def test_cost_focused_option_rejects_conflicting_policy_inputs(self) -> None:
        """Breaks if automatic and caller-provided commands can become ambiguous."""
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "routes": [
                            {
                                "id": "caller-policy-low",
                                "vendor": "codex",
                                "tier": "low",
                                "command": ["/bin/echo", "caller-policy"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = _weightclass(
                "route",
                "--suggest-tier",
                "--cost-focused",
                "--policy",
                str(policy_path),
                "--source-vendor",
                "codex",
                task="Fix a typo.",
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, '{"error": "invalid_input"}\n')
        self.assertNotIn("Fix a typo.", result.stderr)

    def test_preset_rejects_ambiguous_policy_inputs_before_reading_the_task(self) -> None:
        """Breaks if preset and separately supplied routing inputs can be combined."""
        task = "private preset conflict task"
        result = _weightclass(
            "route",
            "--suggest-tier",
            "--preset",
            "codex-cost-focused",
            "--source-vendor",
            "codex",
            task=task,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, '{"error": "invalid_input"}\n')
        self.assertNotIn(task, result.stderr)

    def test_help_lists_every_reachable_subcommand(self) -> None:
        """Breaks if a mode becomes undiscoverable from the command line."""
        result = subprocess.run(
            [sys.executable, "-m", "weightclass", "--help"],
            capture_output=True,
            check=False,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        for subcommand in (
            "classify",
            "discover",
            "profile",
            "review-preset",
            "route",
            "run",
            "render",
            "v2",
        ):
            with self.subTest(subcommand=subcommand):
                self.assertIn(subcommand, result.stdout)

    def test_bare_command_prints_help_successfully(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "weightclass"],
            capture_output=True,
            check=False,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: wclass", result.stdout)
        self.assertIn("Review on the terminal by default, then run one agent.", result.stdout)

    def test_global_output_flags_work_before_or_after_the_command(self) -> None:
        human = _weightclass("--human", "classify", task="Fix a typo.")
        machine = _weightclass("classify", "--json", task="Fix a typo.")

        self.assertEqual(human.returncode, 0, human.stderr)
        self.assertEqual(human.stdout, "Tier: low\n")
        self.assertEqual(machine.returncode, 0, machine.stderr)
        self.assertEqual(json.loads(machine.stdout), {"tier": "low"})

    def test_conflicting_output_flags_fail_without_reading_task(self) -> None:
        task = "private output-mode conflict"
        result = _weightclass("--human", "classify", "--json", task=task)

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})
        self.assertNotIn(task, result.stderr)

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
            exit_code = cli.main(["future-command"])

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
                ["discover"],
                [
                    "profile",
                    "--agent",
                    "codex",
                    "--tier",
                    "low",
                    "--effort",
                    "low",
                ],
                ["classify"],
                ["route", "--suggest-tier"],
                ["route", "--suggest-tier", "--source-vendor", "codex"],
                ["route", "--suggest-tier", "--policy", "/nonexistent/policy.json"],
                ["run", "--tier", "low", "--policy", str(native_policy_path)],
                ["run", "--tier", "low", "--policy", "/nonexistent/policy.json"],
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
                [*base, "--suggest-tier"],
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
        # tier_source 도 지문과 같은 부류다 — 어느 플래그를 썼는지에서 유도되므로
        # 개별 테스트가 리터럴로 고정할 필요가 없다. 형태만 확인하고 뺀다.
        # 계약 자체는 tests/test_explicit_tier_selection.py 가 고정한다.
        tier_source = rendered.pop("tier_source", None)
        self.assertIn(tier_source, (None, "suggested"))
        suggestion = rendered.pop("tier_suggestion", None)
        self.assertEqual(suggestion is None, tier_source is None)
        route_id = rendered.get("route")
        vendor = rendered.get("vendor")
        command = rendered.get("command")
        if (
            isinstance(route_id, str)
            and isinstance(vendor, str)
            and route_id.startswith(f"{vendor}-")
            and isinstance(command, list)
            and command
            and isinstance(command[0], str)
            and os.path.isabs(command[0])
        ):
            # Built-in/default routes now bind an admitted absolute PATH target;
            # the historical route-shape assertions below compare only argv shape.
            command[0] = vendor
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
                "policy_version": "4",
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

    def test_default_outputs_keep_the_same_route_shape_without_explain(self) -> None:
        classify = subprocess.run(
            [sys.executable, "-m", "weightclass", "classify"],
            capture_output=True,
            check=False,
            input="Fix the typo.",
            text=True,
        )
        route = subprocess.run(
            [
                sys.executable,
                "-m",
                "weightclass",
                "route",
                "--suggest-tier",
                "--source-vendor",
                "codex",
            ],
            capture_output=True,
            check=False,
            input="Fix the typo.",
            text=True,
        )

        self.assertEqual(classify.stdout, '{"tier": "low"}\n')
        self.assertEqual(
            self._rendered_route(route),
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

    def test_route_explain_adds_only_task_free_classification_provenance(self) -> None:
        task = "Fix the typo in sentinel-private-route-9371."
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
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "route",
                    "--suggest-tier",
                    "--policy",
                    str(policy_path),
                    "--explain",
                ],
                capture_output=True,
                check=False,
                input=task,
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = self._rendered_route(result)
        self.assertEqual(rendered["reason_code"], "low.mechanical")
        self.assertEqual(rendered["classification_policy_version"], "4")
        self.assertEqual(rendered["confidence_class"], "rule_match")
        self.assertNotIn("sentinel-private-route-9371", result.stdout)

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
        for arguments in (["classify"], ["route", "--suggest-tier"], ["run", "--tier", "low"]):
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
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "route",
                    "--suggest-tier",
                    "--policy",
                    str(policy_path),
                ],
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
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "route",
                    "--suggest-tier",
                    "--policy",
                    str(policy_path),
                ],
                capture_output=True,
                check=False,
                input="Implement the requested feature.",
                text=True,
            )
            policy["posture"] = "cautious"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            cautious = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "route",
                    "--suggest-tier",
                    "--policy",
                    str(policy_path),
                ],
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
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "route",
                    "--suggest-tier",
                    "--policy",
                    str(policy_path),
                ],
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
                    "--suggest-tier",
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
                    [
                        sys.executable,
                        "-m",
                        "weightclass",
                        "route",
                        "--suggest-tier",
                        "--policy",
                        str(policy_path),
                    ],
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
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "route",
                    "--suggest-tier",
                    "--policy",
                    str(policy_path),
                ],
                capture_output=True,
                check=False,
                input="Review authorization.",
                text=True,
            )
            policy["posture"] = "cautious"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            cautious = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "route",
                    "--suggest-tier",
                    "--policy",
                    str(policy_path),
                ],
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
                    "--tier",
                    json.loads(balanced.stdout)["tier"],
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
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "route",
                    "--suggest-tier",
                    "--policy",
                    str(policy_path),
                ],
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
            arguments = [
                sys.executable,
                "-m",
                "weightclass",
                "route",
                "--suggest-tier",
                "--policy",
                str(policy_path),
            ]
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
                    "--suggest-tier",
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
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "route",
                    "--suggest-tier",
                    "--policy",
                    str(policy_path),
                ],
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
                    "--suggest-tier",
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
            [
                sys.executable,
                "-m",
                "weightclass",
                "route",
                "--suggest-tier",
                "--source-vendor",
                "codex",
            ],
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
            [
                sys.executable,
                "-m",
                "weightclass",
                "route",
                "--suggest-tier",
                "--source-vendor",
                "claude",
            ],
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
            [
                sys.executable,
                "-m",
                "weightclass",
                "route",
                "--suggest-tier",
                "--source-vendor",
                "claude",
            ],
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
            [sys.executable, "-m", "weightclass", "route", "--suggest-tier"],
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
            [sys.executable, "-m", "weightclass", "route", "--suggest-tier"],
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
            [sys.executable, "-m", "weightclass", "route", "--suggest-tier"],
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
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "route",
                    "--suggest-tier",
                    "--policy",
                    str(policy_path),
                ],
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
                    "--tier",
                    json.loads(review.stdout)["tier"],
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
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "route",
                    "--suggest-tier",
                    "--policy",
                    str(policy_path),
                ],
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
                    "--tier",
                    json.loads(review.stdout)["tier"],
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
                    "--tier",
                    json.loads(review.stdout)["tier"],
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
            unbound = _weightclass(
                "run", "--tier", "low", "--policy", str(policy_path), task="Fix a typo."
            )

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
                    [
                        sys.executable,
                        "-m",
                        "weightclass",
                        "route",
                        "--suggest-tier",
                        "--policy",
                        str(policy_path),
                    ],
                    capture_output=True,
                    check=False,
                    input=task,
                    text=True,
                )
                self.assertEqual(review.returncode, 0, review.stderr)
                return str(json.loads(review.stdout)["route_fingerprint"])

            def run_with(
                task: str, fingerprint: str, tier: str = "low"
            ) -> subprocess.CompletedProcess[str]:
                # 티어를 분류에 맡기지 않고 지정한다. 이 테스트가 보는 것은 지문이
                # 어떤 필드를 묶는지이지 분류기의 판정이 아니다.
                return subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "weightclass",
                        "run",
                        "--tier",
                        tier,
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
            other_tier = run_with("Review the authorization boundary.", baseline, tier="high")

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
            [sys.executable, "-m", "weightclass", "route", "--suggest-tier"],
            capture_output=True,
            check=False,
            input=task,
            text=True,
        )
        with_vendor = subprocess.run(
            [
                sys.executable,
                "-m",
                "weightclass",
                "route",
                "--suggest-tier",
                "--source-vendor",
                "codex",
            ],
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


class BuiltInModelRoutingTests(unittest.TestCase):
    """모델 등급은 비용 차이의 가장 큰 변수다.

    지금까지는 cost-focused 프리셋을 구체화해야만 티어별 모델을 지정할 수 있어서,
    모델 라우팅이 실험용 곁길에 머물렀다. 내장 라우트에 직접 붙일 수 있어야
    라우터가 그 변수를 실제로 다룬다.
    """

    def route(self, *arguments: str) -> "subprocess.CompletedProcess[str]":
        return subprocess.run(
            [sys.executable, "-m", "weightclass", "route", "--tier", "low", *arguments],
            capture_output=True,
            check=False,
            input="fix the typo",
            text=True,
        )

    def test_a_tier_model_label_binds_to_the_built_in_route_of_each_vendor(self) -> None:
        """Breaks if tier model routing again requires materializing a preset policy."""
        expected = {
            "codex": ["--model", "cheap-model", "-c", "model_reasoning_effort=low"],
            "claude": ["--model", "cheap-model", "--effort", "low"],
            "grok": ["--model", "cheap-model", "--reasoning-effort", "low"],
        }

        for vendor, tail in expected.items():
            with self.subTest(vendor=vendor):
                result = self.route("--source-vendor", vendor, "--low-model", "cheap-model")

                self.assertEqual(result.returncode, 0, result.stderr)
                rendered = json.loads(result.stdout)
                command = rendered["command"]
                self.assertIsInstance(command, list)
                # 라벨은 그 벤더 명령이 실제로 읽는 자리에 들어가야 한다. 위치가
                # 어긋나면 라벨이 붙었는데도 조용히 무시되거나 다른 옵션의 값이
                # 되어, 검토한 명령과 실행된 명령이 달라진다.
                self.assertIn(
                    tail,
                    [command[start : start + len(tail)] for start in range(len(command))],
                )
                self.assertEqual(rendered["vendor"], vendor)
                self.assertEqual(rendered["configuration_status"], "unqualified_custom")

    def test_only_the_named_tier_changes(self) -> None:
        """Breaks if one tier's label leaks into the routes that were not overridden."""
        overridden = self.route("--source-vendor", "codex", "--low-model", "cheap-model")
        untouched = subprocess.run(
            [
                sys.executable,
                "-m",
                "weightclass",
                "route",
                "--tier",
                "high",
                "--source-vendor",
                "codex",
                "--low-model",
                "cheap-model",
            ],
            capture_output=True,
            check=False,
            input="fix the typo",
            text=True,
        )

        self.assertEqual(untouched.returncode, 0, untouched.stderr)
        self.assertIn("cheap-model", json.loads(overridden.stdout)["command"])
        self.assertNotIn("cheap-model", json.loads(untouched.stdout)["command"])

    def test_a_bound_label_changes_the_reviewed_route_fingerprint(self) -> None:
        """Breaks if a run could be acknowledged with the unlabelled route's fingerprint."""
        plain = self.route("--source-vendor", "codex")
        labelled = self.route("--source-vendor", "codex", "--low-model", "cheap-model")

        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertNotEqual(
            json.loads(plain.stdout)["route_fingerprint"],
            json.loads(labelled.stdout)["route_fingerprint"],
        )

    def test_an_unsupported_vendor_or_dimension_fails_closed(self) -> None:
        """Breaks if a label is bound to a command shape that has no place for it.

        agy 는 모델 플래그가 없고, grok 의 노력 오버라이드는 측정되지 않았다.
        벤더를 밝히지 않으면 어느 내장 라우트에 붙는지가 결정적이지 않다.
        """
        cases = (
            ("--source-vendor", "agy", "--low-model", "cheap-model"),
            ("--source-vendor", "grok", "--low-effort", "minimal"),
            ("--low-model", "cheap-model"),
        )

        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = self.route(*arguments)

                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})


class EscalationSuggestionTests(unittest.TestCase):
    """싼 티어로 먼저 보내는 전략은 실패했을 때 다음 경로가 손에 있어야 성립한다.

    V1 은 자식을 재시도하거나 감독하지 않는다. 그 경계는 그대로 두고, 실패한
    뒤에 다음 티어를 지목만 한다. 지목이 없으면 사용자가 라우트를 직접 찾아
    지문을 다시 검토하고 --usage-rework 를 기억해야 하는데, 그 마찰 때문에
    아무도 싼 티어를 쓰지 않고 라우터는 안전하게 위로 보내며 비용이 샌다.
    """

    def _policy(self, directory: str) -> Path:
        path = Path(directory) / "policy.json"
        path.write_text(
            json.dumps(
                {
                    "routes": [
                        {
                            "id": "f-low",
                            "vendor": "fake",
                            "tier": "low",
                            "command": ["/usr/bin/false"],
                        },
                        {
                            "id": "f-standard",
                            "vendor": "fake",
                            "tier": "standard",
                            "command": ["/usr/bin/false", "--medium"],
                        },
                        {
                            "id": "f-high",
                            "vendor": "fake",
                            "tier": "high",
                            "command": ["/usr/bin/false", "--high"],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        return path

    def _stderr_objects(self, result: "subprocess.CompletedProcess[str]") -> list[dict[str, Any]]:
        """Parse stderr into JSON objects, failing loudly if a line is missing.

        위치 인덱스로 바로 집으면 승급 줄이 아예 없을 때 IndexError 로 죽어서
        무엇을 검사하려던 것인지가 사라진다.
        """
        objects = [json.loads(line) for line in result.stderr.splitlines() if line.strip()]
        self.assertGreaterEqual(
            len(objects), 2, f"expected a diagnostic and an escalation line, got {objects}"
        )
        return objects

    def _run(self, policy: Path, tier: str, *extra: str) -> "subprocess.CompletedProcess[str]":
        route = subprocess.run(
            [
                sys.executable,
                "-m",
                "weightclass",
                "route",
                "--policy",
                str(policy),
                "--source-vendor",
                "fake",
                "--tier",
                tier,
            ],
            capture_output=True,
            check=True,
            input="fix the typo",
            text=True,
        )
        fingerprint = json.loads(route.stdout)["route_fingerprint"]
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "weightclass",
                "run",
                "--policy",
                str(policy),
                "--source-vendor",
                "fake",
                "--tier",
                tier,
                "--ack-route-fingerprint",
                fingerprint,
                *extra,
            ],
            capture_output=True,
            check=False,
            input="fix the typo",
            text=True,
        )

    def test_a_failed_child_names_the_route_one_tier_up(self) -> None:
        """Breaks if escalation stops carrying a reviewable route and fingerprint."""
        with tempfile.TemporaryDirectory() as directory:
            policy = self._policy(directory)
            result = self._run(policy, "low", "--suggest-escalation")
            lines = self._stderr_objects(result)

        self.assertEqual(result.returncode, 7)
        self.assertEqual(lines[0]["error"], "executor_failed")
        escalation = lines[1]["escalation"]
        self.assertEqual(escalation["from_tier"], "low")
        self.assertEqual(escalation["to_tier"], "standard")
        # README 가 문서화한 필드다. 테스트가 없으면 조용히 사라질 수 있다.
        self.assertEqual(escalation["route"], "f-standard")
        self.assertEqual(escalation["vendor"], "fake")
        self.assertTrue(escalation["route_fingerprint"].startswith("sha256:"))
        # 승급 실행은 이미 센 태스크의 재시도다. 이 플래그를 빠뜨리면 기준선이
        # 부풀어 실패한 저비용 라우팅이 절감처럼 보인다.
        self.assertIsNone(escalation["record_as_rework"])
        self.assertIs(escalation["usage_rework_supported"], False)
        # 티어가 실패의 원인이라고 주장하지 않는다. 라우터는 자식 출력을 읽지
        # 않으므로 원인을 알 방법이 없다.
        self.assertIs(escalation["failure_cause_diagnosed"], False)

    def test_the_suggestion_never_carries_the_command_itself(self) -> None:
        """Breaks if a route a caller never reviewed leaks its argv into failure logs.

        승급을 실행하는 데 필요한 것은 티어와 지문이다. argv 를 함께 내면, low 로만
        돌리고 상위 라우트를 검토한 적 없는 사용자가 실패 로그로 그 명령을 처음
        보게 되고, 정책에 인라인 자격증명이 있으면 그것이 로그로 나간다.
        """
        with tempfile.TemporaryDirectory() as directory:
            policy = Path(directory) / "secret.json"
            policy.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": "s-low",
                                "vendor": "fake",
                                "tier": "low",
                                "command": ["/usr/bin/false", "--api-key", "sk-not-a-real-secret"],
                            },
                            {
                                "id": "s-standard",
                                "vendor": "fake",
                                "tier": "standard",
                                "command": [
                                    "/usr/bin/false",
                                    "--api-key",
                                    "sk-not-a-real-secret",
                                    "--m",
                                ],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            result = self._run(policy, "low", "--suggest-escalation")
            lines = self._stderr_objects(result)

        escalation = lines[1]["escalation"]
        self.assertNotIn("command", escalation)
        self.assertNotIn("sk-not-a-real-secret", result.stderr)
        # 티어와 지문만으로 승급을 실행할 수 있어야 기능이 유지된다.
        self.assertEqual(escalation["to_tier"], "standard")
        self.assertTrue(escalation["route_fingerprint"].startswith("sha256:"))

    def test_bound_suggestion_keeps_the_higher_executable_path_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            low = root / "low-failure"
            higher = root / "higher-private-path"
            low.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            higher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            low.chmod(0o700)
            higher.chmod(0o700)
            policy = root / "bound.json"
            policy.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": "bound-low",
                                "vendor": "fake",
                                "tier": "low",
                                "command": [str(low)],
                            },
                            {
                                "id": "bound-standard",
                                "vendor": "fake",
                                "tier": "standard",
                                "command": [str(higher)],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            common = [
                "--policy",
                str(policy),
                "--source-vendor",
                "fake",
                "--bind-executable-identity",
            ]
            reviewed_low = subprocess.run(
                [sys.executable, "-m", "weightclass", "route", *common, "--tier", "low"],
                capture_output=True,
                check=True,
                input="fix the typo",
                text=True,
            )
            low_fingerprint = json.loads(reviewed_low.stdout)["route_fingerprint"]
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "run",
                    *common,
                    "--tier",
                    "low",
                    "--ack-route-fingerprint",
                    low_fingerprint,
                    "--suggest-escalation",
                ],
                capture_output=True,
                check=False,
                input="fix the typo",
                text=True,
            )
            reviewed_higher = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "route",
                    *common,
                    "--tier",
                    "standard",
                ],
                capture_output=True,
                check=True,
                input="fix the typo",
                text=True,
            )
            lines = self._stderr_objects(result)

        escalation = lines[1]["escalation"]
        self.assertEqual(result.returncode, 7)
        self.assertEqual(escalation["executable_binding"], "observed")
        self.assertNotIn("executable_identity", escalation)
        self.assertNotIn(str(higher), result.stderr)
        self.assertEqual(
            escalation["route_fingerprint"],
            json.loads(reviewed_higher.stdout)["route_fingerprint"],
        )

    def test_the_suggested_fingerprint_is_the_one_that_route_renders(self) -> None:
        """Breaks if the suggestion cannot be acknowledged as-is on the next run."""
        with tempfile.TemporaryDirectory() as directory:
            policy = self._policy(directory)
            suggested = self._stderr_objects(self._run(policy, "low", "--suggest-escalation"))[1]
            rendered = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "route",
                    "--policy",
                    str(policy),
                    "--source-vendor",
                    "fake",
                    "--tier",
                    "standard",
                ],
                capture_output=True,
                check=True,
                input="fix the typo",
                text=True,
            )

        self.assertEqual(
            suggested["escalation"]["route_fingerprint"],
            json.loads(rendered.stdout)["route_fingerprint"],
        )

    def test_the_ladder_skips_a_tier_the_policy_does_not_define(self) -> None:
        """Breaks if a gap in the policy silently swallows the suggestion.

        한 칸만 올라가면 low 와 high 만 정의한 정책에서 low 가 실패했을 때 high
        라우트가 멀쩡히 있는데도 아무것도 알려주지 못한다.
        """
        with tempfile.TemporaryDirectory() as directory:
            policy = Path(directory) / "gap.json"
            policy.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": "g-low",
                                "vendor": "fake",
                                "tier": "low",
                                "command": ["/usr/bin/false"],
                            },
                            {
                                "id": "g-high",
                                "vendor": "fake",
                                "tier": "high",
                                "command": ["/usr/bin/false", "--high"],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            lines = self._stderr_objects(self._run(policy, "low", "--suggest-escalation"))

        escalation = lines[1]["escalation"]
        self.assertEqual(escalation["to_tier"], "high")
        self.assertEqual(escalation["route"], "g-high")

    def test_a_policy_with_one_tier_suggests_nothing(self) -> None:
        """Breaks if a single-tier policy starts naming a route that cannot exist."""
        with tempfile.TemporaryDirectory() as directory:
            policy = Path(directory) / "single.json"
            policy.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": "s-low",
                                "vendor": "fake",
                                "tier": "low",
                                "command": ["/usr/bin/false"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            lines = self._stderr_objects(self._run(policy, "low", "--suggest-escalation"))

        self.assertIsNone(lines[1]["escalation"])
        self.assertEqual(lines[1]["reason"], "no_route_for_higher_tier")

    def test_the_highest_tier_has_nowhere_to_escalate(self) -> None:
        """Breaks if the top of the ladder starts suggesting a route that cannot exist."""
        with tempfile.TemporaryDirectory() as directory:
            result = self._run(self._policy(directory), "high", "--suggest-escalation")
            lines = self._stderr_objects(result)

        self.assertIsNone(lines[1]["escalation"])
        self.assertEqual(lines[1]["reason"], "already_highest_tier")

    def test_the_default_run_output_is_unchanged(self) -> None:
        """Breaks if the diagnostic contract changes for callers that did not opt in."""
        with tempfile.TemporaryDirectory() as directory:
            result = self._run(self._policy(directory), "low")

        self.assertEqual(result.returncode, 7)
        self.assertEqual(
            json.loads(result.stderr), {"error": "executor_failed", "executor_exit_code": 1}
        )

    def test_a_router_diagnosed_failure_suggests_nothing(self) -> None:
        """Breaks if escalation is offered when no child ever ran.

        자식이 돌지도 않았는데 다음 티어를 권하면, 티어와 무관한 실패에 돈을
        쓰게 만든다. 실행 이전에 닫히는 경로가 여럿이므로 하나만 검사하면
        나머지로 제안이 새어 나가는 것을 놓친다.
        """
        with tempfile.TemporaryDirectory() as directory:
            mismatch = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "run",
                    "--policy",
                    str(self._policy(directory)),
                    "--source-vendor",
                    "fake",
                    "--tier",
                    "low",
                    "--ack-route-fingerprint",
                    "sha256:" + "0" * 64,
                    "--suggest-escalation",
                ],
                capture_output=True,
                check=False,
                input="fix the typo",
                text=True,
            )

            missing = Path(directory) / "no-such-executable"
            absent = Path(directory) / "absent-policy.json"
            absent.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": "a-low",
                                "vendor": "fake",
                                "tier": "low",
                                "command": [str(missing)],
                            },
                            {
                                "id": "a-standard",
                                "vendor": "fake",
                                "tier": "standard",
                                "command": [str(missing), "--m"],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            route = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "route",
                    "--policy",
                    str(absent),
                    "--source-vendor",
                    "fake",
                    "--tier",
                    "low",
                ],
                capture_output=True,
                check=True,
                input="fix the typo",
                text=True,
            )
            unavailable = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "run",
                    "--policy",
                    str(absent),
                    "--source-vendor",
                    "fake",
                    "--tier",
                    "low",
                    "--ack-route-fingerprint",
                    json.loads(route.stdout)["route_fingerprint"],
                    "--suggest-escalation",
                ],
                capture_output=True,
                check=False,
                input="fix the typo",
                text=True,
            )

        self.assertEqual(mismatch.returncode, 6)
        self.assertEqual(json.loads(mismatch.stderr), {"error": "route_fingerprint_mismatch"})
        self.assertNotIn("escalation", mismatch.stderr)
        self.assertEqual(unavailable.returncode, 4)
        self.assertEqual(json.loads(unavailable.stderr), {"error": "executor_unavailable"})
        self.assertNotIn("escalation", unavailable.stderr)


if __name__ == "__main__":
    unittest.main()
