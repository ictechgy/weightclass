from __future__ import annotations

import io
import json
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from unittest import mock

from weightclass.advisory import (
    advisory_context,
    advisory_quick,
    advisory_routes,
    speculative_run,
)


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
        self.assertTrue(receipt["task_egressed"])
        self.assertFalse(receipt["persistent_state_written"])
        self.assertEqual(receipt["content_trust"], "untrusted_model_authored")
        self.assertTrue(receipt["worktree_unchanged"])
        self.assertFalse(receipt["git_metadata_checked"])
        delivered_prompt = run_child.call_args.args[2]
        self.assertIn("private task", delivered_prompt)
        self.assertIn("Advisory stage: manual", delivered_prompt)
        self.assertIn("Context mode: repo", delivered_prompt)
        self.assertNotIn("private task", json.dumps(receipt))
        self.assertIn("triage", receipt)

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

    def test_preview_never_reads_task_or_starts_capability_probes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with (
                mock.patch(
                    "weightclass.advisory.advisory_quick.sys.stdin",
                    _UnreadableInput(),
                ),
                mock.patch.object(
                    advisory_quick,
                    "_observe_route_executable",
                    return_value=("/usr/bin/true", object()),
                ),
                mock.patch.object(advisory_quick, "_resolve_executable") as resolve,
                mock.patch(
                    "weightclass.advisory.advisory_quick.speculative_run.run_child"
                ) as run_child,
            ):
                receipt = advisory_quick.preview(
                    vendors=("codex", "claude"),
                    workflow="review",
                    role="cheap",
                    repo=Path(directory),
                    timeout_seconds=30,
                    stage="plan",
                    context_mode="files",
                    context_files=("source.py",),
                )
        resolve.assert_not_called()
        run_child.assert_not_called()
        self.assertFalse(receipt["task_read"])
        self.assertFalse(receipt["repository_content_read"])
        self.assertEqual(receipt["task_bearing_child_bound"], 2)
        self.assertEqual(receipt["task_free_probe_children_on_run"], 4)

    def test_context_syntax_fails_before_task_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
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
                    repo=Path(directory),
                    timeout_seconds=30,
                    confirm_task_egress=True,
                    context_mode="repo",
                    context_files=("source.py",),
                )
        self.assertEqual(raised.exception.code, "ask_context_invalid")

    def test_diff_git_preflight_fails_before_task_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with (
                mock.patch(
                    "weightclass.advisory.advisory_quick.sys.stdin",
                    _UnreadableInput(),
                ),
                mock.patch(
                    "weightclass.advisory.advisory_quick.advisory_context.preflight_git",
                    side_effect=advisory_context.AdvisoryContextError("ask_context_unsupported"),
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
                    context_mode="diff",
                )
        self.assertEqual(raised.exception.code, "ask_context_unsupported")

    def test_plan_can_skip_a_locally_trivial_task_without_vendor_child(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            observation = object()
            with (
                mock.patch(
                    "weightclass.advisory.advisory_quick.sys.stdin",
                    io.TextIOWrapper(io.BytesIO(b"fix typo"), encoding="utf-8"),
                ),
                mock.patch.object(
                    advisory_quick,
                    "_resolve_executable",
                    return_value=("/usr/bin/true", observation),
                ),
                mock.patch(
                    "weightclass.advisory.advisory_quick.speculative_run.run_child"
                ) as run_child,
            ):
                receipt = advisory_quick.ask(
                    vendor="codex",
                    workflow="review",
                    role="cheap",
                    repo=Path(directory),
                    timeout_seconds=30,
                    confirm_task_egress=True,
                    stage="plan",
                    auto_skip_trivial=True,
                )
        run_child.assert_not_called()
        self.assertEqual(receipt["event"], "advisory_skipped")
        self.assertFalse(receipt["task_egressed"])
        budget = receipt["call_budget"]
        self.assertIsInstance(budget, Mapping)
        assert isinstance(budget, Mapping)
        self.assertEqual(budget["used"], 0)

    def test_council_preflights_every_member_before_task_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = {
                "vendor": "codex",
                "command": ("/usr/bin/true",),
                "delivery": "stdin",
                "executable": "/usr/bin/true",
                "observation": object(),
            }
            with (
                mock.patch(
                    "weightclass.advisory.advisory_quick.sys.stdin",
                    _UnreadableInput(),
                ),
                mock.patch.object(
                    advisory_quick,
                    "_preflight_member",
                    side_effect=(
                        first,
                        advisory_quick.QuickAdvisoryError("ask_cli_unavailable"),
                    ),
                ),
                self.assertRaises(advisory_quick.QuickAdvisoryError) as raised,
            ):
                advisory_quick.ask_council(
                    vendors=("codex", "claude"),
                    workflow="review",
                    role="cheap",
                    repo=Path(directory),
                    timeout_seconds=30,
                    confirm_task_egress=True,
                )
        self.assertEqual(raised.exception.code, "ask_cli_unavailable")

    def test_council_preserves_partial_results_and_never_selects_a_winner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            members = [
                {
                    "vendor": vendor,
                    "command": ("/usr/bin/true",),
                    "delivery": "stdin",
                    "executable": "/usr/bin/true",
                    "observation": object(),
                }
                for vendor in ("codex", "claude")
            ]
            with (
                mock.patch(
                    "weightclass.advisory.advisory_quick.sys.stdin",
                    io.TextIOWrapper(io.BytesIO(b"private task"), encoding="utf-8"),
                ),
                mock.patch.object(
                    advisory_quick,
                    "_preflight_member",
                    side_effect=members,
                ),
                mock.patch.object(
                    advisory_quick,
                    "_run_member",
                    side_effect=(
                        ("ok", _review_result()),
                        advisory_quick.QuickAdvisoryError("ask_cli_unavailable"),
                    ),
                ),
            ):
                receipt = advisory_quick.ask_council(
                    vendors=("codex", "claude"),
                    workflow="review",
                    role="cheap",
                    repo=root,
                    timeout_seconds=30,
                    confirm_task_egress=True,
                )
        self.assertFalse(receipt["complete"])
        self.assertEqual(receipt["successful_members"], 1)
        council_members = receipt["members"]
        self.assertIsInstance(council_members, list)
        assert isinstance(council_members, list)
        self.assertIsInstance(council_members[1], Mapping)
        assert isinstance(council_members[1], Mapping)
        self.assertEqual(council_members[1]["status"], "ask_cli_unavailable")
        budget = receipt["call_budget"]
        self.assertIsInstance(budget, Mapping)
        assert isinstance(budget, Mapping)
        self.assertEqual(budget["used"], 1)
        synthesis = receipt["synthesis"]
        self.assertIsInstance(synthesis, Mapping)
        assert isinstance(synthesis, Mapping)
        self.assertFalse(synthesis["selection_performed"])
        self.assertNotIn("winner", json.dumps(receipt))
        self.assertNotIn("private task", json.dumps(receipt))

    def test_confirmation_discloses_argv_process_exposure(self) -> None:
        console = mock.mock_open(read_data="yes\n")
        member = {"vendor": "agy", "delivery": "argv"}
        with (
            mock.patch("weightclass.advisory.advisory_quick.os.ctermid", return_value="/tty"),
            mock.patch("builtins.open", console),
        ):
            advisory_quick._confirm_task_egress(
                members=(member,),
                workflow="review",
                stage="manual",
                context_mode="files",
                context_file_count=1,
                confirmed=False,
            )
        written = "".join(call.args[0] for call in console().write.call_args_list if call.args)
        self.assertIn("visible in local process arguments", written)

    def test_missing_controlling_terminal_api_fails_closed(self) -> None:
        with (
            mock.patch("weightclass.advisory.advisory_quick.os.ctermid", None),
            self.assertRaises(advisory_quick.QuickAdvisoryError) as raised,
        ):
            advisory_quick._confirm_task_egress(
                members=({"vendor": "codex", "delivery": "stdin"},),
                workflow="review",
                stage="manual",
                context_mode="repo",
                context_file_count=0,
                confirmed=False,
            )
        self.assertEqual(raised.exception.code, "ask_confirmation_required")


if __name__ == "__main__":
    unittest.main()
