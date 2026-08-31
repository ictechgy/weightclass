from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from weightclass.advisory import advisory_quick, advisory_routes, speculative_run


def _review_result() -> dict[str, object]:
    return {
        "schema_version": 1,
        "mode": "review",
        "summary": "No reportable findings.",
        "findings": [],
        "limitations": [],
    }


class _UnreadableInput(io.StringIO):
    def read(self, *_arguments: object, **_keywords: object) -> str:
        raise AssertionError("task input was read before task-free preflight completed")


class AdvisoryQuickRouteTests(unittest.TestCase):
    def test_default_routes_omit_model_and_effort_labels(self) -> None:
        for vendor in advisory_quick.VENDORS:
            with self.subTest(vendor=vendor):
                route = advisory_routes.build_default_evidence_route(vendor, "review")
                self.assertNotIn("--model", route)
                self.assertNotIn("--effort", route)
                self.assertNotIn("--reasoning-effort", route)
                self.assertIn(
                    advisory_routes.command_task_delivery(route),
                    {"stdin", "argv", "file"},
                )

    def test_capability_failure_happens_before_task_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with (
                mock.patch.object(
                    advisory_quick,
                    "_resolve_executable",
                    side_effect=advisory_quick.QuickAdvisoryError("ask_cli_unavailable"),
                ),
                mock.patch(
                    "weightclass.advisory.advisory_quick.sys.stdin",
                    _UnreadableInput(),
                ),
                self.assertRaisesRegex(
                    advisory_quick.QuickAdvisoryError,
                    "^$",
                ) as raised,
            ):
                advisory_quick.ask(
                    vendor="codex",
                    workflow="review",
                    role="cheap",
                    repo=Path(directory),
                    timeout_seconds=30,
                    confirm_task_egress=True,
                )

        self.assertEqual(raised.exception.code, "ask_cli_unavailable")

    def test_unsafe_process_context_has_a_specific_task_free_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with (
                mock.patch.object(
                    advisory_quick,
                    "has_safe_child_status_context",
                    return_value=False,
                ),
                mock.patch(
                    "weightclass.advisory.advisory_quick.sys.stdin",
                    _UnreadableInput(),
                ),
                self.assertRaises(advisory_quick.QuickAdvisoryError) as raised,
            ):
                advisory_quick.ask(
                    vendor="codex",
                    workflow="review",
                    role="cheap",
                    repo=Path(directory),
                    timeout_seconds=30,
                    confirm_task_egress=True,
                )

        self.assertEqual(raised.exception.code, "ask_process_context_unsafe")

    def test_missing_repository_has_a_specific_task_free_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            with (
                mock.patch(
                    "weightclass.advisory.advisory_quick.sys.stdin",
                    _UnreadableInput(),
                ),
                self.assertRaises(advisory_quick.QuickAdvisoryError) as raised,
            ):
                advisory_quick.ask(
                    vendor="codex",
                    workflow="review",
                    role="cheap",
                    repo=missing,
                    timeout_seconds=30,
                    confirm_task_egress=True,
                )

        self.assertEqual(raised.exception.code, "ask_repository_unavailable")

    def test_capability_check_is_bound_to_the_exact_resolved_executable(self) -> None:
        observation = object()
        capability = mock.Mock(ready=True)
        resolved = str(Path("/usr/bin/true").resolve())
        with (
            mock.patch(
                "weightclass.advisory.advisory_quick.shutil.which",
                return_value="/usr/bin/true",
            ),
            mock.patch.object(Path, "is_relative_to", return_value=False),
            mock.patch.object(advisory_quick, "observe_executable", return_value=observation),
            mock.patch(
                "weightclass.advisory.advisory_quick.advisory_preflight.check_local_capability",
                return_value=capability,
            ) as check,
        ):
            executable, bound = advisory_quick._resolve_executable(
                "codex",
                Path("/repository"),
            )

        self.assertEqual(executable, resolved)
        self.assertIs(bound, observation)
        check.assert_called_once_with("codex", resolved)

    def test_success_is_stateless_and_explicitly_unverified(self) -> None:
        child: speculative_run.ChildResult = {
            "exit_code": 0,
            "timed_out": False,
            "seconds": 1.0,
            "tokens": None,
            "failure_code": "none",
            "stdout_present": True,
            "stderr_present": False,
            "usage": None,
        }
        with tempfile.TemporaryDirectory() as directory:
            observation = object()
            task_input = io.TextIOWrapper(io.BytesIO(b"private task"), encoding="utf-8")
            with (
                mock.patch("weightclass.advisory.advisory_quick.sys.stdin", task_input),
                mock.patch.object(
                    advisory_quick,
                    "_resolve_executable",
                    return_value=("/usr/bin/true", observation),
                ),
                mock.patch.object(
                    advisory_quick,
                    "observe_executable",
                    return_value=observation,
                ),
                mock.patch(
                    "weightclass.advisory.advisory_quick.speculative_run.run_child",
                    return_value=(child, "provider envelope"),
                ) as run_child,
                mock.patch(
                    "weightclass.advisory.advisory_quick.speculative_run.extract_evidence_result",
                    return_value=(json.dumps(_review_result()), _review_result()),
                ),
            ):
                receipt = advisory_quick.ask(
                    vendor="codex",
                    workflow="review",
                    role="cheap",
                    repo=Path(directory),
                    timeout_seconds=30,
                    confirm_task_egress=True,
                )

        self.assertFalse(receipt["quality_verified"])
        self.assertFalse(receipt["sample_recorded"])
        self.assertFalse(receipt["campaign_state_read"])
        self.assertFalse(receipt["project_verifier_used"])
        self.assertEqual(receipt["content_trust"], "untrusted_model_authored")
        self.assertTrue(receipt["worktree_unchanged"])
        self.assertFalse(receipt["git_metadata_checked"])
        delivered_prompt = run_child.call_args.args[2]
        self.assertIn("private task", delivered_prompt)
        self.assertNotIn("private task", json.dumps(receipt))

    def test_repository_mutation_rejects_the_result(self) -> None:
        child: speculative_run.ChildResult = {
            "exit_code": 0,
            "timed_out": False,
            "seconds": 1.0,
            "tokens": None,
            "failure_code": "none",
            "stdout_present": True,
            "stderr_present": False,
            "usage": None,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observation = object()

            def mutate(
                _command: list[str],
                workspace: Path,
                _task: str,
                **_keywords: object,
            ) -> tuple[speculative_run.ChildResult, str]:
                (workspace / "unexpected.txt").write_text("changed", encoding="utf-8")
                return child, "provider envelope"

            with (
                mock.patch(
                    "weightclass.advisory.advisory_quick.sys.stdin",
                    io.TextIOWrapper(io.BytesIO(b"private task"), encoding="utf-8"),
                ),
                mock.patch.object(
                    advisory_quick,
                    "_resolve_executable",
                    return_value=("/usr/bin/true", observation),
                ),
                mock.patch.object(
                    advisory_quick,
                    "observe_executable",
                    return_value=observation,
                ),
                mock.patch(
                    "weightclass.advisory.advisory_quick.speculative_run.run_child",
                    side_effect=mutate,
                ),
                self.assertRaises(advisory_quick.QuickAdvisoryError) as raised,
            ):
                advisory_quick.ask(
                    vendor="codex",
                    workflow="review",
                    role="cheap",
                    repo=root,
                    timeout_seconds=30,
                    confirm_task_egress=True,
                )

        self.assertEqual(raised.exception.code, "ask_repository_changed")


if __name__ == "__main__":
    unittest.main()
