from __future__ import annotations

import contextlib
import io
import json
import multiprocessing
import os
import sys
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

from weightclass.advisory import (
    advisory_campaign,
    advisory_orchestration,
    speculative_run,
    wclass_advisory,
)


def _register_workspace_batch(registry: str, prefix: str, count: int) -> None:
    from weightclass.advisory import speculative_run as runner

    for index in range(count):
        runner.register(Path(registry), Path(f"/private/{prefix}-{index}"), add=True)


class AdvisoryTaskEgressTests(unittest.TestCase):
    def _arguments(self, root: Path, *, confirmed: bool) -> list[str]:
        arguments = [
            "wclass-advisory run",
            "--out-dir",
            str(root / "out"),
            "--repo",
            str(root / "repo"),
            "--task-file",
            str(root / "PRIVATE-TASK-PATH"),
            "--cheap",
            "/usr/bin/true",
            "--expensive",
            "/usr/bin/true",
            "--verify",
            str(root / "verify"),
        ]
        if confirmed:
            arguments.append("--confirm-task-egress")
        return arguments

    def test_exact_commands_require_confirmation_before_task_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            errors = io.StringIO()
            with (
                mock.patch.object(sys, "argv", self._arguments(root, confirmed=False)),
                mock.patch.object(speculative_run, "read_task_file") as read_task,
                contextlib.redirect_stderr(errors),
                self.assertRaises(SystemExit) as raised,
            ):
                speculative_run.main()

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("advisory execution requires --confirm-task-egress", errors.getvalue())
        self.assertNotIn("PRIVATE-TASK-PATH", errors.getvalue())
        self.assertFalse((root / "out").exists())
        read_task.assert_not_called()

    def test_exact_command_confirmation_requires_a_private_task_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            (repo / ".git").mkdir(parents=True)
            verify = root / "verify"
            verify.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            verify.chmod(0o700)
            task = root / "PRIVATE-TASK-PATH"
            task.write_text("bounded test task", encoding="utf-8")
            task.chmod(0o644)
            errors = io.StringIO()
            with (
                mock.patch.object(sys, "argv", self._arguments(root, confirmed=True)),
                mock.patch.object(speculative_run, "run_git", return_value=""),
                mock.patch.object(speculative_run, "run_child") as run_child,
                contextlib.redirect_stderr(errors),
                self.assertRaises(SystemExit) as raised,
            ):
                speculative_run.main()

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--task-file is invalid", errors.getvalue())
        self.assertNotIn("PRIVATE-TASK-PATH", errors.getvalue())
        run_child.assert_not_called()


class AdvisoryLaneDiagnosticTests(unittest.TestCase):
    @staticmethod
    @contextlib.contextmanager
    def _raising_context(error: BaseException) -> Iterator[None]:
        raise error
        yield None  # pragma: no cover

    def test_low_level_lane_errors_are_value_free_json(self) -> None:
        cases: tuple[tuple[advisory_orchestration.CampaignLaneError, str], ...] = (
            (
                advisory_orchestration.LaneUnavailableError(),
                "managed_lane_unavailable",
            ),
            (
                advisory_orchestration.CampaignCapacityError(),
                "managed_campaign_capacity_reached",
            ),
            (
                advisory_orchestration.CampaignRecordsInvalidError(
                    advisory_campaign.CampaignError("campaign_record_binding_mismatch")
                ),
                "campaign_record_binding_mismatch",
            ),
            (
                advisory_orchestration.AllocatorUnavailableError(),
                "managed_allocator_busy",
            ),
            (
                advisory_orchestration.CampaignLaneStateError(),
                "invalid_campaign_lane_state",
            ),
        )
        for error, expected in cases:
            with self.subTest(expected=expected):
                stderr = io.StringIO()
                with (
                    mock.patch.object(
                        advisory_orchestration,
                        "acquire_campaign_lanes",
                        return_value=self._raising_context(error),
                    ),
                    contextlib.redirect_stderr(stderr),
                ):
                    result = wclass_advisory._run(
                        [
                            "--campaign-root",
                            "/private/opaque-root",
                            "--confirm-task-egress",
                        ],
                        prune=False,
                    )

                self.assertEqual(result, 2)
                self.assertEqual(json.loads(stderr.getvalue()), {"error": expected})
                self.assertNotIn("Traceback", stderr.getvalue())
                self.assertNotIn("opaque-root", stderr.getvalue())

    def test_low_level_wrapper_requires_confirmation_before_lane_allocation(self) -> None:
        stderr = io.StringIO()
        with (
            mock.patch.object(advisory_orchestration, "acquire_campaign_lanes") as acquire,
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            wclass_advisory._run(
                ["--campaign-root", "/private/PRIVATE-ROOT"],
                prune=False,
            )

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("advisory execution requires --confirm-task-egress", stderr.getvalue())
        self.assertNotIn("PRIVATE-ROOT", stderr.getvalue())
        acquire.assert_not_called()


@unittest.skipUnless(os.name == "posix", "workspace registry locking is POSIX-only")
class WorkspaceRegistryHardeningTests(unittest.TestCase):
    def test_concurrent_registration_does_not_lose_entries(self) -> None:
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("fork multiprocessing unavailable")
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "workspaces.txt"
            context = multiprocessing.get_context("fork")
            first = context.Process(
                target=_register_workspace_batch,
                args=(str(registry), "first", 12),
            )
            second = context.Process(
                target=_register_workspace_batch,
                args=(str(registry), "second", 12),
            )
            first.start()
            second.start()
            first.join(timeout=15)
            second.join(timeout=15)
            self.assertEqual(first.exitcode, 0)
            self.assertEqual(second.exitcode, 0)
            entries = set(registry.read_text(encoding="utf-8").splitlines())

        self.assertEqual(len(entries), 24)
        self.assertEqual(
            entries,
            {
                *(f"/private/first-{index}" for index in range(12)),
                *(f"/private/second-{index}" for index in range(12)),
            },
        )

    def test_registry_rejects_a_symlink_without_touching_its_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "outside.txt"
            target.write_text("outside sentinel", encoding="utf-8")
            registry = root / "workspaces.txt"
            registry.symlink_to(target)

            with self.assertRaises(OSError):
                speculative_run.write_registry(registry, ["/private/workspace"])

            self.assertEqual(target.read_text(encoding="utf-8"), "outside sentinel")
            self.assertTrue(registry.is_symlink())

    def test_registry_rejects_an_oversized_entry_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "workspaces.txt"
            with (
                mock.patch.object(speculative_run, "MAX_WORKSPACE_REGISTRY_LINE_BYTES", 8),
                self.assertRaises(OSError),
            ):
                speculative_run.write_registry(registry, ["/private/workspace"])

            self.assertFalse(registry.exists())


if __name__ == "__main__":
    unittest.main()
