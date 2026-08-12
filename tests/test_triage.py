"""벤더 판정 경로 테스트.

실제 claude/codex 는 호출하지 않는다. subprocess 를 가로채 응답만 흉내낸다.
"""

import contextlib
import errno
import gc
import io
import json
import os
import resource
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast
from unittest import mock

from tests.runtime_guard import guarded_launch
from weightclass import cli, delegation_conformance, process_context, triage
from weightclass.router import BUILT_IN_VENDORS
from weightclass.triage import (
    TRIAGE_COMMANDS,
    TRIAGE_PROMPT,
    TRIAGE_READ_ONLY_MARKERS,
    TRIAGE_TIMEOUT_SECONDS,
    TriageUnavailableError,
    ask_vendor_for_tier,
    triage_command,
    triage_descriptor,
)


def _completed(stdout: bytes, returncode: int = 0) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(args=(), returncode=returncode, stdout=stdout)


@contextlib.contextmanager
def _fake_vendor_on_path(body: str) -> "Iterator[dict[str, str]]":
    """Put a fake `claude` on PATH so the real capture path is exercised.

    실제 claude 는 절대 부르지 않는다. PATH 앞에 가짜를 놓아 가로챈다.
    """
    with tempfile.TemporaryDirectory() as directory:
        executable = Path(directory) / "claude"
        executable.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        executable.chmod(0o755)
        yield {
            "PATH": f"{directory}:{os.environ.get('PATH', '')}",
            "PYTHONPATH": f"{Path(__file__).resolve().parent.parent}/src",
        }


@contextlib.contextmanager
def _fake_python_vendor_on_path(source: str) -> "Iterator[dict[str, str]]":
    """Install a fake Python vendor when a shell cannot express the lifecycle."""
    with tempfile.TemporaryDirectory() as directory:
        executable = Path(directory) / "claude"
        executable.write_text(
            f"#!{sys.executable}\n{textwrap.dedent(source)}",
            encoding="utf-8",
        )
        executable.chmod(0o755)
        yield {
            "PATH": f"{directory}:{os.environ.get('PATH', '')}",
            "PYTHONPATH": f"{Path(__file__).resolve().parent.parent}/src",
        }


class TriageCommandTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin", "macOS containment command")
    def test_claude_triage_disables_customizations_tools_mcp_and_persistence(self) -> None:
        """Breaks if untrusted task text can reach an ambient Claude capability."""
        command = triage_command("claude")

        self.assertEqual(command[:2], ("/usr/bin/sandbox-exec", "-p"))
        self.assertEqual(command[3], "claude")
        self.assertIn("--safe-mode", command)
        self.assertIn("--no-session-persistence", command)
        self.assertIn("--strict-mcp-config", command)
        setting_sources_index = command.index("--setting-sources")
        self.assertEqual(command[setting_sources_index + 1], "")
        tools_index = command.index("--tools")
        self.assertEqual(command[tools_index + 1], "")
        permission_mode_index = command.index("--permission-mode")
        self.assertEqual(command[permission_mode_index + 1], "plan")
        effort_index = command.index("--effort")
        self.assertEqual(command[effort_index + 1], "low")

    @unittest.skipUnless(sys.platform == "darwin", "macOS sandbox profile")
    def test_claude_triage_descriptor_pins_metadata_mutation_denials(self) -> None:
        """Breaks if production can create cleanup-defeating metadata again."""
        descriptor = triage_descriptor("claude")
        command = tuple(cast(list[str], descriptor["command"]))

        self.assertEqual(command[:2], ("/usr/bin/sandbox-exec", "-p"))
        profile = command[2]
        self.assertIn("(allow default)", profile)
        self.assertIn("(deny file-write-mode)", profile)
        self.assertIn("(deny file-write-flags)", profile)
        self.assertIn("(deny file-write-acl)", profile)
        self.assertIn(
            '(deny file-write-unlink (regex #"/weightclass-triage-[^/]+$"))',
            profile,
        )
        self.assertEqual(command[3], "claude")
        self.assertEqual(descriptor["adapter_version"], 2)

    def test_claude_triage_is_unavailable_without_reviewed_containment(self) -> None:
        """Breaks if non-Darwin task content can reach an uncontained child."""
        with mock.patch("weightclass.triage.sys.platform", "linux"):
            with self.assertRaises(TriageUnavailableError):
                triage_command("claude")

            descriptor = triage_descriptor("claude")

        self.assertFalse(descriptor["available"])
        self.assertEqual(descriptor["adapter_version"], 2)
        self.assertEqual(
            descriptor["unavailable_reason"],
            "no_reviewed_filesystem_containment",
        )
        self.assertNotIn("command", descriptor)

    def test_codex_triage_fails_closed_without_a_no_tools_contract(self) -> None:
        """Breaks if read-only filesystem access is mistaken for no tool access."""
        with self.assertRaises(TriageUnavailableError):
            triage_command("codex")

        descriptor = triage_descriptor("codex")
        self.assertEqual(descriptor["source_vendor"], "codex")
        self.assertFalse(descriptor["available"])
        self.assertEqual(descriptor["unavailable_reason"], "no_no_tools_boundary")
        self.assertNotIn("command", descriptor)

    def test_every_built_in_vendor_has_a_reviewable_triage_descriptor(self) -> None:
        """Breaks if a shipped vendor has an implicit triage policy."""
        for vendor in BUILT_IN_VENDORS:
            with self.subTest(vendor=vendor):
                descriptor = triage_descriptor(vendor)
                self.assertEqual(descriptor["source_vendor"], vendor)
                self.assertIn("available", descriptor)

    def test_a_vendor_this_package_ships_no_command_for_has_no_triage(self) -> None:
        """Breaks if an unreviewed adapter is invented for an unknown vendor."""
        with self.assertRaises(TriageUnavailableError):
            triage_descriptor("qwen")

    def test_enabled_triage_commands_keep_their_read_only_pin(self) -> None:
        """Breaks if an enabled adapter loses its filesystem restriction."""
        self.assertEqual(set(TRIAGE_COMMANDS), {"claude"})
        self.assertEqual(set(TRIAGE_READ_ONLY_MARKERS), {"claude"})
        if sys.platform != "darwin":
            return
        for vendor, marker in TRIAGE_READ_ONLY_MARKERS.items():
            with self.subTest(vendor=vendor):
                self.assertIn(marker, triage_command(vendor))

    def test_the_rubric_names_every_tier_and_asks_for_one_word(self) -> None:
        """Breaks if the rubric drifts into something that cannot be parsed.

        이 프롬프트가 판정 품질의 근거다. 저장소가 소유한다고 적어두고 아무도
        검사하지 않으면 조용히 바뀐다.
        """
        for tier in ("low", "standard", "high"):
            with self.subTest(tier=tier):
                self.assertIn(tier, TRIAGE_PROMPT)
        self.assertIn("exactly one word", TRIAGE_PROMPT)
        # 태스크는 울타리 안에 놓이고, 지시가 아니라 데이터로 다뤄져야 한다.
        self.assertIn("{task}", TRIAGE_PROMPT)
        for fence in ("BEGIN TASK", "END TASK"):
            with self.subTest(fence=fence):
                self.assertIn(fence, TRIAGE_PROMPT)
        self.assertIn("never as instructions", TRIAGE_PROMPT)
        self.assertIn("data to be", TRIAGE_PROMPT)

    def test_rejects_an_unsupported_vendor(self) -> None:
        with self.assertRaises(TriageUnavailableError):
            triage_command("gemini")

    def test_agy_has_no_reviewed_triage_adapter(self) -> None:
        """Breaks if an unreviewed adapter starts sending task text to a new vendor."""
        with self.assertRaises(TriageUnavailableError):
            triage_command("agy")

        descriptor = triage_descriptor("agy")
        self.assertFalse(descriptor["available"])
        self.assertEqual(descriptor["unavailable_reason"], "no_reviewed_triage_adapter")
        self.assertNotIn("command", descriptor)

    def test_grok_has_no_reviewed_triage_adapter(self) -> None:
        """Breaks if an unreviewed adapter starts sending task text to a new vendor."""
        with self.assertRaises(TriageUnavailableError):
            triage_command("grok")

        descriptor = triage_descriptor("grok")
        self.assertFalse(descriptor["available"])
        self.assertEqual(descriptor["unavailable_reason"], "no_reviewed_triage_adapter")
        self.assertNotIn("command", descriptor)


class AskVendorTests(unittest.TestCase):
    """실제 claude/codex 는 부르지 않는다. PATH 앞에 가짜를 놓아 가로챈다.

    subprocess 를 mock 하지 않는 것이 중요하다. 출력 상한과 stderr 폐기는
    실제 파이프에서만 검증되고, mock 은 그 둘을 통과시킨다.
    """

    def _ask_test_command(self, task: str = "task") -> str:
        """Exercise after-gate lifecycle and parsing with an explicit test command."""
        with mock.patch(
            "weightclass.triage.triage_command",
            return_value=("claude",),
        ):
            return ask_vendor_for_tier(task, "claude")

    def _ask(self, body: str, task: str = "task") -> str | None:
        with _fake_vendor_on_path(body) as env, mock.patch.dict(os.environ, env):
            try:
                return self._ask_test_command(task)
            except TriageUnavailableError:
                return None

    def test_non_darwin_refuses_before_starting_a_vendor(self) -> None:
        """Breaks if platform rejection occurs only after task egress."""
        with (
            mock.patch("weightclass.triage.sys.platform", "linux"),
            mock.patch("weightclass.triage.subprocess.Popen") as popen,
            mock.patch("weightclass.triage._read_bounded_vendor_answer") as read_answer,
        ):
            with self.assertRaises(TriageUnavailableError) as captured:
                ask_vendor_for_tier("private task", "claude")

        popen.assert_not_called()
        read_answer.assert_not_called()
        self.assertEqual(captured.exception.args, ())

    def test_accepts_a_bare_tier_word(self) -> None:
        self.assertEqual(self._ask("printf high"), "high")

    @guarded_launch("triage")
    def test_guarded_owned_absolute_vendor(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "fake_triage_vendor.py"
        with mock.patch(
            "weightclass.triage.triage_command",
            return_value=(str(fixture.resolve(strict=True)),),
        ):
            self.assertEqual(ask_vendor_for_tier("task", "claude"), "high")

    def test_refuses_a_tier_at_the_end_of_a_sentence(self) -> None:
        """Breaks if prose can masquerade as the authoritative one-token output."""
        self.assertIsNone(self._ask("printf 'This one is subtle, so: high'"))

    def test_refuses_multiple_tier_tokens(self) -> None:
        """Breaks if the parser accepts only the final whitespace token."""
        self.assertIsNone(self._ask("printf 'high low'"))

    def test_refuses_uppercase_tiers(self) -> None:
        """Breaks if the output protocol silently expands beyond the prompt contract."""
        self.assertIsNone(self._ask("printf HIGH"))

    def test_accepts_unicode_whitespace_around_one_tier(self) -> None:
        """Breaks if strict token parsing rejects harmless surrounding whitespace."""
        self.assertEqual(self._ask(r"printf '\302\240high\302\240'"), "high")

    def test_refuses_an_embedded_nul(self) -> None:
        """Breaks if a tier prefix is accepted without validating the complete output."""
        self.assertIsNone(self._ask(r"printf 'high\000'"))

    def test_refuses_an_answer_that_is_not_a_tier(self) -> None:
        """Breaks if unparseable output silently becomes a tier."""
        for label, body in {
            "empty": "true",
            "prose without a tier": "printf 'maybe medium?'",
            "invalid utf-8": r"printf '\377\376'",
        }.items():
            with self.subTest(case=label):
                self.assertIsNone(self._ask(body))

    def test_refuses_when_the_vendor_exits_non_zero(self) -> None:
        self.assertIsNone(self._ask("printf high; exit 1"))

    def test_refuses_when_the_vendor_is_not_installed(self) -> None:
        with mock.patch.dict(os.environ, {"PATH": "/nonexistent"}):
            with self.assertRaises(TriageUnavailableError):
                self._ask_test_command()

    def test_rejects_an_unsafe_sigchld_context_before_starting_the_vendor(self) -> None:
        """Breaks if discarded child status is noticed only after task egress."""
        with (
            mock.patch(
                "weightclass.triage.has_safe_sigchld_disposition",
                return_value=False,
            ),
            mock.patch(
                "weightclass.triage.subprocess.Popen",
                side_effect=AssertionError("vendor process was started"),
            ) as popen,
        ):
            with self.assertRaises(TriageUnavailableError):
                self._ask_test_command()

        popen.assert_not_called()

    def test_rejects_a_worker_thread_before_starting_the_vendor(self) -> None:
        """Breaks if triage starts a child where SIGCHLD ownership is unsafe."""
        errors: list[BaseException] = []

        def ask_from_worker() -> None:
            try:
                self._ask_test_command()
            except BaseException as error:
                errors.append(error)

        with mock.patch(
            "weightclass.triage.subprocess.Popen",
            side_effect=AssertionError("vendor process was started"),
        ) as popen:
            worker = threading.Thread(target=ask_from_worker)
            worker.start()
            worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], TriageUnavailableError)
        popen.assert_not_called()

    def test_refuses_output_past_the_size_cap(self) -> None:
        """Breaks if the cap moves back to after the whole stream is buffered."""
        self.assertIsNone(self._ask("printf low; head -c 10000 /dev/zero | tr '\\000' ' '"))

    @unittest.skipUnless(sys.platform == "darwin", "public Claude triage containment")
    def test_discards_vendor_stderr(self) -> None:
        """Breaks if a vendor's own output can reach weightclass's streams.

        mock 이 아니라 실제 파이프로 확인한다. mock 은 stderr 처리를 통과시킨다.
        """
        with _fake_vendor_on_path("echo 'VENDORNOISE' >&2; printf low") as env:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "classify",
                    "--source-vendor",
                    "claude",
                    "--ask-vendor",
                ],
                capture_output=True,
                check=False,
                input="task",
                text=True,
                env=env,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("VENDORNOISE", result.stdout + result.stderr)

    def test_a_hung_vendor_does_not_hang_weightclass(self) -> None:
        """Breaks if the timeout stops being armed.

        예외가 났다는 것만으로는 부족하다. 타임아웃을 꺼도 가짜 벤더가 알아서
        끝나면 결국 같은 예외가 나므로, 느리게 통과할 뿐이다. 경과 시간까지
        단언해야 타임아웃이 실제로 끊었다는 것이 증명된다.
        """
        started = time.monotonic()
        with (
            mock.patch("weightclass.triage.TRIAGE_TIMEOUT_SECONDS", 1),
            # exec 로 셸을 대체한다. 그러지 않으면 kill 이 셸만 죽이고 손자
            # 프로세스가 stdout 파이프를 계속 잡아 read 가 막힌다. 실제 벤더도
            # 자식을 남기면 같은 일이 생길 수 있다는 뜻이므로 주석으로 남긴다.
            _fake_vendor_on_path("exec sleep 60") as env,
            mock.patch.dict(os.environ, env),
            self.assertRaises(TriageUnavailableError),
        ):
            self._ask_test_command()

        # 가짜 벤더는 60초를 잔다. 그보다 한참 전에 끊겼어야 한다.
        self.assertLess(time.monotonic() - started, 20)

    def test_vendor_runs_from_an_empty_private_working_directory(self) -> None:
        """Breaks if project instructions or files remain discoverable from cwd."""
        source = """
            import os
            import sys

            sys.stdout.write("high" if not os.listdir(".") else "not-a-tier")
        """
        with _fake_python_vendor_on_path(source) as env, mock.patch.dict(os.environ, env):
            self.assertEqual(self._ask_test_command(), "high")

    @unittest.skipUnless(sys.platform == "darwin", "macOS sandbox profile")
    def test_vendor_cannot_make_the_private_tree_cleanup_hostile(self) -> None:
        """Breaks if production can chmod its cwd/root or create an artifact."""
        source = """
            import os
            import pathlib
            import stat
            import sys

            cwd = pathlib.Path.cwd()
            root = cwd.parent
            blocked = stat.S_IMODE(cwd.stat().st_mode) == 0o500
            blocked = blocked and stat.S_IMODE(root.stat().st_mode) == 0o500
            for target in (cwd, root):
                try:
                    os.chmod(target, 0o700)
                except PermissionError:
                    pass
                else:
                    blocked = False
            try:
                (cwd / "artifact").write_text("ephemeral", encoding="utf-8")
            except PermissionError:
                pass
            else:
                blocked = False
            sys.stdout.write("high" if blocked else "not-a-tier")
        """
        with _fake_python_vendor_on_path(source) as env, mock.patch.dict(os.environ, env):
            self.assertEqual(ask_vendor_for_tier("task", "claude"), "high")

    @unittest.skipUnless(sys.platform == "darwin", "macOS sandbox profile")
    def test_vendor_cannot_rename_the_private_root(self) -> None:
        """Breaks if the child can move the root beyond exact-name removal."""
        source = """
            import os
            import pathlib
            import sys

            root = pathlib.Path.cwd().parent
            moved = root.with_name(root.name + "-moved")
            try:
                os.rename(root, moved)
            except PermissionError:
                sys.stdout.write("high")
            else:
                sys.stdout.write("not-a-tier")
        """
        with _fake_python_vendor_on_path(source) as env, mock.patch.dict(os.environ, env):
            self.assertEqual(ask_vendor_for_tier("task", "claude"), "high")

    @unittest.skipUnless(sys.platform == "darwin", "macOS sandbox profile")
    def test_vendor_can_unlink_an_unrelated_cache_file(self) -> None:
        """Breaks if the root-rename denial blocks ordinary unrelated cleanup."""
        source = """
            import os
            import pathlib
            import tempfile
            import sys

            file_descriptor, path = tempfile.mkstemp(prefix="weightclass-cache-")
            os.close(file_descriptor)
            cache_file = pathlib.Path(path)
            cache_file.unlink()
            sys.stdout.write("high" if not cache_file.exists() else "not-a-tier")
        """
        with _fake_python_vendor_on_path(source) as env, mock.patch.dict(os.environ, env):
            self.assertEqual(ask_vendor_for_tier("task", "claude"), "high")

    @unittest.skipUnless(sys.platform == "darwin", "macOS sandbox wrapper")
    def test_missing_sandbox_wrapper_fails_redacted_without_a_child(self) -> None:
        """Breaks if a missing required containment wrapper falls back to Claude."""
        command = triage_command("claude")

        with mock.patch(
            "weightclass.triage.subprocess.Popen",
            side_effect=FileNotFoundError("raw"),
        ) as popen:
            with self.assertRaises(TriageUnavailableError) as captured:
                ask_vendor_for_tier("task", "claude")

        self.assertEqual(popen.call_args.args[0], command)
        self.assertEqual(command[0], "/usr/bin/sandbox-exec")
        self.assertEqual(captured.exception.args, ())

    def test_root_open_failure_removes_the_unspawned_private_directory(self) -> None:
        """Breaks if partial root acquisition forgets a new empty directory."""
        allocated: list[Path] = []
        real_mkdtemp = tempfile.mkdtemp
        real_open = os.open

        def record_allocation(*args: Any, **kwargs: Any) -> str:
            path = cast(str, real_mkdtemp(*args, **kwargs))
            allocated.append(Path(path))
            return path

        def fail_root_open(
            path: str | os.PathLike[str],
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            if Path(path).name.startswith("weightclass-triage-"):
                raise OSError("raw")
            return real_open(path, flags, mode, dir_fd=dir_fd)

        try:
            with (
                mock.patch(
                    "weightclass.triage.tempfile.mkdtemp",
                    side_effect=record_allocation,
                ),
                mock.patch("weightclass.triage.os.open", side_effect=fail_root_open),
                mock.patch("weightclass.triage.subprocess.Popen") as popen,
            ):
                with self.assertRaises(TriageUnavailableError) as captured:
                    self._ask_test_command()

            popen.assert_not_called()
            self.assertEqual(captured.exception.args, ())
            self.assertEqual(len(allocated), 1)
            self.assertFalse(allocated[0].exists())
        finally:
            for path in allocated:
                if path.exists():
                    path.rmdir()

    def test_parent_open_failure_removes_the_unspawned_private_directory(self) -> None:
        """Breaks if parent descriptor acquisition leaks its pinned empty root."""
        allocated: list[Path] = []
        real_mkdtemp = tempfile.mkdtemp
        real_open = os.open

        def record_allocation(*args: Any, **kwargs: Any) -> str:
            path = cast(str, real_mkdtemp(*args, **kwargs))
            allocated.append(Path(path))
            return path

        def fail_parent_open(
            path: str | os.PathLike[str],
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            if path == ".." and dir_fd is not None:
                raise OSError("raw")
            return real_open(path, flags, mode, dir_fd=dir_fd)

        try:
            with (
                mock.patch(
                    "weightclass.triage.tempfile.mkdtemp",
                    side_effect=record_allocation,
                ),
                mock.patch("weightclass.triage.os.open", side_effect=fail_parent_open),
                mock.patch("weightclass.triage.subprocess.Popen") as popen,
            ):
                with self.assertRaises(TriageUnavailableError) as captured:
                    self._ask_test_command()

            popen.assert_not_called()
            self.assertEqual(captured.exception.args, ())
            self.assertEqual(len(allocated), 1)
            self.assertFalse(allocated[0].exists())
        finally:
            for path in allocated:
                if path.exists():
                    path.rmdir()

    def test_unproved_private_directory_removal_fails_redacted(self) -> None:
        """Breaks if task-derived artifacts can be silently left on disk."""
        real_remove = triage._remove_private_directory

        def remove_without_proof(*args: Any, **kwargs: Any) -> bool:
            self.assertTrue(real_remove(*args, **kwargs))
            return False

        with (
            _fake_vendor_on_path("printf high") as env,
            mock.patch.dict(os.environ, env),
            mock.patch(
                "weightclass.triage._remove_private_directory",
                side_effect=remove_without_proof,
            ),
        ):
            with self.assertRaises(TriageUnavailableError) as captured:
                self._ask_test_command()

        self.assertEqual(captured.exception.args, ())

    def test_private_cleanup_handles_a_tree_deeper_than_python_recursion(self) -> None:
        """Breaks if adversarial directory depth can escape as RecursionError."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            root.mkdir()
            child = root
            for _ in range(120):
                child /= "d"
                child.mkdir()
            root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            previous_limit = sys.getrecursionlimit()
            try:
                sys.setrecursionlimit(80)
                removed_contents = triage._erase_private_tree(root_fd, time.monotonic() + 10)
            finally:
                sys.setrecursionlimit(previous_limit)
                os.close(root_fd)

            self.assertTrue(removed_contents)
            self.assertEqual(list(root.iterdir()), [])

    def test_private_cleanup_normalizes_fanout_allocation_failure(self) -> None:
        """Breaks if adversarial fanout can expose MemoryError and a path."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "artifact").touch()
            root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            real_scandir = os.scandir
            calls = 0

            def fail_once(file_descriptor: int) -> Any:
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise MemoryError
                return real_scandir(file_descriptor)

            try:
                with mock.patch("weightclass.triage.os.scandir", side_effect=fail_once):
                    self.assertFalse(triage._erase_private_tree(root_fd, time.monotonic() + 1))
            finally:
                os.close(root_fd)

            self.assertEqual(list(root.iterdir()), [])

    def test_private_cleanup_returns_on_a_permanent_scan_failure(self) -> None:
        """Breaks if unbounded ownership escalation becomes a CPU spin."""
        source = textwrap.dedent(
            """
            import os
            import pathlib
            import tempfile
            import time
            from unittest import mock

            from weightclass import triage

            with tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                (root / "artifact").touch()
                root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    with mock.patch(
                        "weightclass.triage.os.scandir", side_effect=MemoryError
                    ):
                        removed = triage._erase_private_tree(
                            root_fd, time.monotonic() + 0.01
                        )
                finally:
                    os.close(root_fd)
                if removed:
                    raise SystemExit(1)
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", source],
            capture_output=True,
            check=False,
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src"),
            },
            text=True,
            timeout=3,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_private_cleanup_returns_on_a_permanent_leaf_failure(self) -> None:
        """Breaks if a failed leaf is requeued forever in the final pass."""
        source = textwrap.dedent(
            """
            import os
            import pathlib
            import tempfile
            import time
            from unittest import mock

            from weightclass import triage

            with tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                (root / "artifact").touch()
                root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    with mock.patch(
                        "weightclass.triage.os.unlink", side_effect=PermissionError
                    ):
                        removed = triage._erase_private_tree(
                            root_fd, time.monotonic() + 0.01
                        )
                finally:
                    os.close(root_fd)
                if removed:
                    raise SystemExit(1)
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", source],
            capture_output=True,
            check=False,
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src"),
            },
            text=True,
            timeout=3,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_directory_swap_before_open_cannot_normalize_the_replacement(
        self,
    ) -> None:
        """Breaks if identity validation happens after replacement normalization."""
        with tempfile.TemporaryDirectory() as directory:
            outer = Path(directory)
            root = outer / "root"
            root.mkdir()
            child = root / "child"
            child.mkdir()
            (child / "artifact").touch()
            moved_child = outer / "moved-child"
            external = outer / "external"
            external.mkdir()
            sentinel = external / "sentinel"
            sentinel.write_text("preserve", encoding="utf-8")
            external.chmod(0o750)
            external_identity = external.stat()
            child_identity = child.stat()
            root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            real_open = os.open
            swapped = False

            def swap_before_open(
                path: str | os.PathLike[str],
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                nonlocal swapped
                if not swapped and path == "child" and dir_fd is not None:
                    swapped = True
                    os.rename(child, moved_child)
                    os.rename(external, child)
                return real_open(path, flags, mode, dir_fd=dir_fd)

            try:
                with (
                    mock.patch(
                        "weightclass.triage.os.open",
                        side_effect=swap_before_open,
                    ),
                    mock.patch("weightclass.triage._normalize_open_fd") as normalize,
                    self.assertRaises(OSError),
                ):
                    triage._open_child_directory(
                        root_fd,
                        "child",
                        child_identity,
                        time.monotonic() + 1,
                    )

                self.assertTrue(swapped)
                normalize.assert_not_called()
                self.assertTrue(triage._same_identity(external_identity, child.stat()))
                self.assertEqual(
                    stat.S_IMODE(child.stat().st_mode),
                    0o750,
                )
                self.assertEqual(
                    (child / "sentinel").read_text(encoding="utf-8"),
                    "preserve",
                )
            finally:
                os.close(root_fd)
                for candidate in (child, moved_child, external):
                    if candidate.is_dir():
                        candidate.chmod(0o700)

    @unittest.skipUnless(sys.platform == "darwin", "macOS descriptor semantics")
    def test_directory_swap_after_metadata_pin_is_rejected_on_reopen(self) -> None:
        """Breaks if Darwin fallback trusts a mutable name after fd normalization."""
        with tempfile.TemporaryDirectory() as directory:
            outer = Path(directory)
            root = outer / "root"
            root.mkdir()
            child = root / "child"
            child.mkdir()
            moved_child = outer / "moved-child"
            external = outer / "external"
            external.mkdir()
            sentinel = external / "sentinel"
            sentinel.write_text("preserve", encoding="utf-8")
            external.chmod(0o750)
            external_identity = external.stat()
            child_identity = child.stat()
            root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            real_open = os.open
            real_normalize = triage._normalize_open_fd
            child_open_calls = 0
            swapped = False

            def force_metadata_fallback(
                path: str | os.PathLike[str],
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                nonlocal child_open_calls
                if path == "child" and dir_fd == root_fd:
                    child_open_calls += 1
                    if child_open_calls == 1:
                        raise PermissionError("forced")
                return real_open(path, flags, mode, dir_fd=dir_fd)

            def normalize_then_swap(
                file_descriptor: int,
                mode: int,
                deadline: float | None,
            ) -> bool:
                nonlocal swapped
                result = real_normalize(file_descriptor, mode, deadline)
                if not swapped:
                    swapped = True
                    os.rename(child, moved_child)
                    os.rename(external, child)
                return result

            try:
                with (
                    mock.patch(
                        "weightclass.triage.os.open",
                        side_effect=force_metadata_fallback,
                    ),
                    mock.patch(
                        "weightclass.triage._normalize_open_fd",
                        side_effect=normalize_then_swap,
                    ),
                    self.assertRaises(OSError),
                ):
                    triage._open_child_directory(
                        root_fd,
                        "child",
                        child_identity,
                        time.monotonic() + 1,
                    )

                self.assertTrue(swapped)
                self.assertTrue(triage._same_identity(external_identity, child.stat()))
                self.assertEqual(stat.S_IMODE(child.stat().st_mode), 0o750)
                self.assertEqual(
                    (child / "sentinel").read_text(encoding="utf-8"),
                    "preserve",
                )
            finally:
                os.close(root_fd)
                for candidate in (child, moved_child, external):
                    if candidate.is_dir():
                        candidate.chmod(0o700)

    @unittest.skipUnless(sys.platform == "darwin", "macOS descriptor semantics")
    def test_private_cleanup_fails_closed_for_an_unpinnable_chmod_zero_directory(
        self,
    ) -> None:
        """Document the state production sandboxing prevents on Darwin."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            root.mkdir()
            child = root / "child"
            child.mkdir()
            (child / "artifact").write_text("ephemeral", encoding="utf-8")
            child.chmod(0)
            root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                removed = triage._erase_private_tree(
                    root_fd,
                    time.monotonic() + 2,
                )
            finally:
                os.close(root_fd)

            try:
                self.assertFalse(removed)
            finally:
                child.chmod(0o700)
            self.assertTrue((child / "artifact").is_file())

    def test_private_cleanup_handles_wide_fanout_without_a_snapshot_list(self) -> None:
        """Breaks if cleanup allocates the full child set before making progress."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(2_000):
                (root / f"entry-{index}").touch()
            root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                self.assertTrue(triage._erase_private_tree(root_fd, time.monotonic() + 5))
            finally:
                os.close(root_fd)

            self.assertEqual(list(root.iterdir()), [])

    def test_private_cleanup_finishes_after_its_admission_deadline(self) -> None:
        """Breaks if an elapsed budget permits transient artifacts to remain."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            root.mkdir()
            (root / "artifact").touch()
            root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            parent_fd = os.open(root.parent, os.O_RDONLY | os.O_DIRECTORY)
            root_identity = os.fstat(root_fd)
            deadline = time.monotonic() - 1
            try:
                removed_within_deadline = triage._remove_private_directory(
                    root_fd,
                    parent_fd,
                    root.name,
                    root_identity,
                    deadline,
                )
            finally:
                os.close(root_fd)
                os.close(parent_fd)

            self.assertFalse(removed_within_deadline)
            self.assertFalse(root.exists())

    @unittest.skipUnless(hasattr(resource, "RLIMIT_NOFILE"), "requires POSIX fd limits")
    def test_private_cleanup_uses_bounded_fds_for_a_deep_tree(self) -> None:
        """Breaks if descriptor use grows with adversarial directory depth."""
        source = textwrap.dedent(
            """
            import os
            import pathlib
            import resource
            import tempfile
            import time

            from weightclass import triage

            with tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory) / "root"
                root.mkdir()
                child = root
                for _ in range(100):
                    child /= "d"
                    child.mkdir()
                root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
                resource.setrlimit(resource.RLIMIT_NOFILE, (64, hard))
                try:
                    removed = triage._erase_private_tree(root_fd, time.monotonic() + 10)
                finally:
                    os.close(root_fd)
                if not removed or list(root.iterdir()):
                    raise SystemExit(1)
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", source],
            capture_output=True,
            check=False,
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src"),
            },
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_private_cleanup_does_not_reopen_each_deep_path_quadratically(self) -> None:
        """Breaks if every ascent walks from the root through all ancestors."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            root.mkdir()
            child = root
            for _ in range(100):
                child /= "d"
                child.mkdir()
            root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            real_open = os.open
            open_calls = 0

            def count_open(*args: Any, **kwargs: Any) -> int:
                nonlocal open_calls
                open_calls += 1
                return real_open(*args, **kwargs)

            try:
                with mock.patch("weightclass.triage.os.open", side_effect=count_open):
                    removed = triage._erase_private_tree(
                        root_fd,
                        time.monotonic() + 10,
                    )
            finally:
                os.close(root_fd)

            self.assertTrue(removed)
            self.assertLess(open_calls, 500)
            self.assertEqual(list(root.iterdir()), [])

    def test_failed_leaf_unlink_cannot_chmod_a_swapped_external_hardlink(self) -> None:
        """Breaks if normalization acts on a mutable name after unlink failure."""
        with tempfile.TemporaryDirectory() as directory:
            outer = Path(directory)
            external = outer / "external"
            external.write_text("preserve", encoding="utf-8")
            external.chmod(0o640)
            root = outer / "root"
            root.mkdir()
            leaf = root / "leaf"
            leaf.write_text("ephemeral", encoding="utf-8")
            root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            real_unlink = os.unlink
            swapped = False

            def fail_after_swap(path: str, *, dir_fd: int | None = None) -> None:
                nonlocal swapped
                if not swapped and path == "leaf":
                    swapped = True
                    real_unlink(path, dir_fd=dir_fd)
                    os.link(external, path, dst_dir_fd=dir_fd)
                    raise PermissionError("forced")
                real_unlink(path, dir_fd=dir_fd)

            try:
                with mock.patch("weightclass.triage.os.unlink", side_effect=fail_after_swap):
                    removed = triage._erase_private_tree(root_fd, time.monotonic() + 1)
            finally:
                os.close(root_fd)

            self.assertFalse(removed)
            self.assertEqual(external.read_text(encoding="utf-8"), "preserve")
            self.assertEqual(stat.S_IMODE(external.stat().st_mode), 0o640)

    def test_private_cleanup_closes_a_child_fd_when_scandir_allocation_fails(self) -> None:
        """Breaks if pre-stack child traversal failure leaks an owned descriptor."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "child"
            child.mkdir()
            (child / "artifact").touch()
            root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            real_scandir = os.scandir
            baseline_descriptors = set(os.listdir("/dev/fd"))
            calls = 0

            def fail_child_scandir(file_descriptor: int) -> Any:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise MemoryError
                return real_scandir(file_descriptor)

            try:
                with mock.patch("weightclass.triage.os.scandir", side_effect=fail_child_scandir):
                    self.assertFalse(triage._erase_private_tree(root_fd, time.monotonic() + 1))
                self.assertEqual(set(os.listdir("/dev/fd")), baseline_descriptors)
            finally:
                os.close(root_fd)

    def test_cleanup_empties_a_root_renamed_outside_production_containment(self) -> None:
        """Defense in depth for a root moved by a directly invoked helper."""
        moved_root: Path | None = None
        try:
            with tempfile.TemporaryDirectory() as directory:
                root_record = Path(directory) / "moved-root"
                source = f"""
                    import os
                    import pathlib
                    import sys

                    original = pathlib.Path.cwd().parent
                    moved = original.with_name(original.name + "-moved")
                    os.rename(original, moved)
                    pathlib.Path({str(root_record)!r}).write_text(
                        str(moved), encoding="utf-8"
                    )
                    pathlib.Path("artifact").write_text("ephemeral", encoding="utf-8")
                    sys.stdout.write("high")
                """
                with (
                    _fake_python_vendor_on_path(source) as env,
                    mock.patch.dict(os.environ, env),
                    mock.patch(
                        "weightclass.triage.TRIAGE_COMMANDS",
                        {"claude": ("claude",)},
                    ),
                ):
                    with self.assertRaises(TriageUnavailableError) as captured:
                        self._ask_test_command()

                moved_root = Path(root_record.read_text(encoding="utf-8"))
                self.assertTrue(moved_root.is_dir())
                self.assertEqual(list(moved_root.iterdir()), [])
                self.assertEqual(captured.exception.args, ())
        finally:
            if moved_root is not None and moved_root.exists():
                shutil.rmtree(moved_root)

    def test_helper_root_replacement_is_never_followed_or_removed(self) -> None:
        """Defense in depth for replacement outside production containment."""
        moved_root: Path | None = None
        replacement: Path | None = None
        with tempfile.TemporaryDirectory() as directory:
            external = Path(directory) / "external"
            external.mkdir()
            sentinel = external / "sentinel"
            sentinel.write_text("preserve", encoding="utf-8")
            root_record = Path(directory) / "moved-root"
            replacement_record = Path(directory) / "replacement"
            source = f"""
                import os
                import pathlib
                import sys

                original = pathlib.Path.cwd().parent
                moved = original.with_name(original.name + "-moved")
                os.rename(original, moved)
                original.symlink_to(pathlib.Path({str(external)!r}), target_is_directory=True)
                pathlib.Path({str(root_record)!r}).write_text(
                    str(moved), encoding="utf-8"
                )
                pathlib.Path({str(replacement_record)!r}).write_text(
                    str(original), encoding="utf-8"
                )
                pathlib.Path("artifact").write_text("ephemeral", encoding="utf-8")
                sys.stdout.write("high")
            """
            try:
                with (
                    _fake_python_vendor_on_path(source) as env,
                    mock.patch.dict(os.environ, env),
                    mock.patch(
                        "weightclass.triage.TRIAGE_COMMANDS",
                        {"claude": ("claude",)},
                    ),
                ):
                    with self.assertRaises(TriageUnavailableError):
                        self._ask_test_command()

                moved_root = Path(root_record.read_text(encoding="utf-8"))
                replacement = Path(replacement_record.read_text(encoding="utf-8"))
                self.assertTrue(replacement.is_symlink())
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")
                self.assertEqual(list(moved_root.iterdir()), [])
            finally:
                if replacement is not None and replacement.is_symlink():
                    replacement.unlink()
                if moved_root is not None and moved_root.exists():
                    shutil.rmtree(moved_root)

    def test_private_cleanup_unlinks_a_hardlink_without_changing_external_metadata(self) -> None:
        """Breaks if cleanup mutates a multiply-linked inode outside its root."""
        with tempfile.TemporaryDirectory() as directory:
            outer = Path(directory)
            external = outer / "external"
            external.write_text("preserve", encoding="utf-8")
            external.chmod(0o640)
            root = outer / "root"
            root.mkdir()
            os.link(external, root / "linked")
            root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                self.assertTrue(triage._erase_private_tree(root_fd, time.monotonic() + 1))
            finally:
                os.close(root_fd)

            self.assertEqual(external.read_text(encoding="utf-8"), "preserve")
            self.assertEqual(stat.S_IMODE(external.stat().st_mode), 0o640)
            self.assertEqual(list(root.iterdir()), [])

    @unittest.skipUnless(sys.platform == "darwin", "macOS ACL semantics")
    def test_vendor_cannot_create_a_deny_delete_acl(self) -> None:
        """Breaks if the production sandbox permits hostile ACL metadata."""
        source = """
            import subprocess
            import sys

            result = subprocess.run(
                ["/bin/chmod", "+a", "everyone deny delete", "."],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            sys.stdout.write("high" if result.returncode != 0 else "not-a-tier")
        """
        with _fake_python_vendor_on_path(source) as env, mock.patch.dict(os.environ, env):
            self.assertEqual(ask_vendor_for_tier("task", "claude"), "high")

    def test_descendant_retaining_stdout_is_stopped_before_it_can_write(self) -> None:
        """Breaks if a successful leader can leave a descendant running."""
        with tempfile.TemporaryDirectory() as directory:
            sentinel = Path(directory) / "descendant-survived"
            source = f"""
                import os
                import pathlib
                import sys
                import time

                if os.fork() == 0:
                    time.sleep(0.4)
                    pathlib.Path({str(sentinel)!r}).write_text("survived", encoding="utf-8")
                    os._exit(0)
                sys.stdout.write("high")
                sys.stdout.flush()
                os._exit(0)
            """
            with (
                mock.patch("weightclass.triage.TRIAGE_TIMEOUT_SECONDS", 5),
                _fake_python_vendor_on_path(source) as env,
                mock.patch.dict(os.environ, env),
            ):
                tier = self._ask_test_command()

            self.assertEqual(tier, "high")
            time.sleep(0.6)
            self.assertFalse(sentinel.exists())

    def test_timeout_stops_the_whole_vendor_process_group(self) -> None:
        """Breaks if timeout kills only the leader and waits for its descendant."""
        with tempfile.TemporaryDirectory() as directory:
            sentinel = Path(directory) / "descendant-survived"
            source = f"""
                import os
                import pathlib
                import time

                if os.fork() == 0:
                    time.sleep(0.6)
                    pathlib.Path({str(sentinel)!r}).write_text("survived", encoding="utf-8")
                    os._exit(0)
                time.sleep(60)
            """
            started = time.monotonic()
            with (
                mock.patch("weightclass.triage.TRIAGE_TIMEOUT_SECONDS", 0.2),
                _fake_python_vendor_on_path(source) as env,
                mock.patch.dict(os.environ, env),
            ):
                with self.assertRaises(TriageUnavailableError):
                    self._ask_test_command()

            self.assertLess(time.monotonic() - started, 1.0)
            time.sleep(0.8)
            self.assertFalse(sentinel.exists())

    def test_sigterm_failure_does_not_skip_sigkill_or_direct_child_reap(self) -> None:
        """Breaks if one cleanup error abandons later ownership duties."""
        signals: list[int] = []
        reaped: list[int] = []
        real_wait = process_context.wait_owned_child

        def fail_term(process_group_id: int, signal_number: int) -> None:
            signals.append(signal_number)
            if signal_number == signal.SIGTERM:
                raise OSError("sensitive signal detail")
            process_context.signal_process_group(process_group_id, signal_number)

        def record_reap(process: subprocess.Popen[bytes], timeout: float | None = None) -> int:
            return_code = real_wait(process, timeout)
            reaped.append(return_code)
            return return_code

        with (
            _fake_vendor_on_path("printf high") as env,
            mock.patch.dict(os.environ, env),
            mock.patch("weightclass.triage.signal_process_group", side_effect=fail_term),
            mock.patch(
                "weightclass.triage.wait_owned_child",
                side_effect=record_reap,
            ),
        ):
            with self.assertRaises(TriageUnavailableError) as captured:
                self._ask_test_command()

        self.assertEqual(signals, [signal.SIGTERM, signal.SIGKILL])
        self.assertEqual(len(reaped), 1)
        self.assertEqual(captured.exception.args, ())

    def test_selector_construction_failure_still_kills_and_reaps_the_child(self) -> None:
        """Breaks if setup after Popen sits outside the ownership cleanup region."""
        signals: list[int] = []
        reaped: list[int] = []
        real_signal = process_context.signal_process_group
        real_wait = process_context.wait_owned_child

        def record_signal(process_group_id: int, signal_number: int) -> None:
            signals.append(signal_number)
            real_signal(process_group_id, signal_number)

        def record_reap(process: subprocess.Popen[bytes], timeout: float | None = None) -> int:
            return_code = real_wait(process, timeout)
            reaped.append(return_code)
            return return_code

        with (
            _fake_vendor_on_path("exec sleep 60") as env,
            mock.patch.dict(os.environ, env),
            mock.patch(
                "weightclass.triage.selectors.DefaultSelector",
                side_effect=OSError("raw"),
            ),
            mock.patch("weightclass.triage.signal_process_group", side_effect=record_signal),
            mock.patch("weightclass.triage.wait_owned_child", side_effect=record_reap),
        ):
            with self.assertRaises(TriageUnavailableError) as captured:
                self._ask_test_command()

        self.assertEqual(signals, [signal.SIGTERM, signal.SIGKILL])
        self.assertEqual(len(reaped), 1)
        self.assertEqual(captured.exception.args, ())

    def test_exchange_keyboard_interrupt_cleans_up_before_it_is_re_raised(self) -> None:
        """Breaks if BaseException can abandon a spawned triage child."""
        signals: list[int] = []
        reaped: list[int] = []
        real_signal = process_context.signal_process_group
        real_wait = process_context.wait_owned_child

        def record_signal(process_group_id: int, signal_number: int) -> None:
            signals.append(signal_number)
            real_signal(process_group_id, signal_number)

        def record_reap(process: subprocess.Popen[bytes], timeout: float | None = None) -> int:
            return_code = real_wait(process, timeout)
            reaped.append(return_code)
            return return_code

        with (
            _fake_vendor_on_path("exec sleep 60") as env,
            mock.patch.dict(os.environ, env),
            mock.patch("weightclass.triage.observe_leader_exit", side_effect=KeyboardInterrupt),
            mock.patch("weightclass.triage.signal_process_group", side_effect=record_signal),
            mock.patch("weightclass.triage.wait_owned_child", side_effect=record_reap),
        ):
            with self.assertRaises(KeyboardInterrupt):
                self._ask_test_command()

        self.assertEqual(signals, [signal.SIGTERM, signal.SIGKILL])
        self.assertEqual(len(reaped), 1)

    def test_sigint_during_popen_is_deferred_until_the_child_is_reaped(self) -> None:
        """Breaks if SIGINT can land after fork but before ownership is recorded."""
        started: list[subprocess.Popen[bytes]] = []
        reaped: list[int] = []
        real_popen = subprocess.Popen
        real_signal = process_context.signal_process_group
        real_wait = process_context.wait_owned_child

        def interrupt_before_return(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
            process = cast(subprocess.Popen[bytes], real_popen(*args, **kwargs))
            started.append(process)
            signal.raise_signal(signal.SIGINT)
            return process

        def record_reap(process: subprocess.Popen[bytes], timeout: float | None = None) -> int:
            return_code = real_wait(process, timeout)
            reaped.append(return_code)
            return return_code

        try:
            with (
                _fake_vendor_on_path("exec sleep 60") as env,
                mock.patch.dict(os.environ, env),
                mock.patch(
                    "weightclass.triage.subprocess.Popen",
                    side_effect=interrupt_before_return,
                ),
                mock.patch("weightclass.triage.wait_owned_child", side_effect=record_reap),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    self._ask_test_command()
        finally:
            for process in started:
                if process.returncode is None:
                    real_signal(process.pid, signal.SIGKILL)
                    real_wait(process)
                if process.stdin is not None and not process.stdin.closed:
                    process.stdin.close()
                if process.stdout is not None and not process.stdout.closed:
                    process.stdout.close()

        self.assertEqual(len(reaped), 1)

    def test_sigint_during_cleanup_is_dispatched_only_after_kill_and_reap(self) -> None:
        """Breaks if a repeated SIGINT can interrupt the ownership finally."""
        signals: list[int] = []
        reaped: list[int] = []
        real_signal = process_context.signal_process_group
        real_wait = process_context.wait_owned_child

        def interrupt_on_term(process_group_id: int, signal_number: int) -> None:
            signals.append(signal_number)
            if signal_number == signal.SIGTERM:
                signal.raise_signal(signal.SIGINT)
            real_signal(process_group_id, signal_number)

        def record_reap(process: subprocess.Popen[bytes], timeout: float | None = None) -> int:
            return_code = real_wait(process, timeout)
            reaped.append(return_code)
            return return_code

        with (
            _fake_vendor_on_path("exec sleep 60") as env,
            mock.patch.dict(os.environ, env),
            mock.patch("weightclass.triage.TRIAGE_TIMEOUT_SECONDS", 0.05),
            mock.patch("weightclass.triage.signal_process_group", side_effect=interrupt_on_term),
            mock.patch("weightclass.triage.wait_owned_child", side_effect=record_reap),
        ):
            with self.assertRaises(KeyboardInterrupt):
                self._ask_test_command()

        self.assertEqual(signals, [signal.SIGTERM, signal.SIGKILL])
        self.assertEqual(len(reaped), 1)

    def test_sigint_after_private_allocation_prevents_spawn_and_removes_the_root(self) -> None:
        """Breaks if the pre-spawn allocation window can leak a private root."""
        allocated: list[Path] = []
        real_mkdtemp = tempfile.mkdtemp

        def allocate_then_interrupt(*args: Any, **kwargs: Any) -> str:
            temporary_root = cast(str, real_mkdtemp(*args, **kwargs))
            allocated.append(Path(temporary_root))
            signal.raise_signal(signal.SIGINT)
            return temporary_root

        with (
            mock.patch("weightclass.triage.tempfile.mkdtemp", side_effect=allocate_then_interrupt),
            mock.patch(
                "weightclass.triage.subprocess.Popen",
                side_effect=AssertionError("vendor process was started"),
            ) as popen,
        ):
            with self.assertRaises(KeyboardInterrupt):
                self._ask_test_command()

        popen.assert_not_called()
        self.assertEqual(len(allocated), 1)
        self.assertFalse(allocated[0].exists())

    def test_output_drain_failure_does_not_skip_sigkill_or_direct_child_reap(self) -> None:
        """Breaks if failed pipe cleanup strands the owned direct child."""
        signals: list[int] = []
        reaped: list[int] = []
        real_signal = process_context.signal_process_group
        real_wait = process_context.wait_owned_child

        def record_signal(process_group_id: int, signal_number: int) -> None:
            signals.append(signal_number)
            real_signal(process_group_id, signal_number)

        def record_reap(process: subprocess.Popen[bytes], timeout: float | None = None) -> int:
            return_code = real_wait(process, timeout)
            reaped.append(return_code)
            return return_code

        with (
            _fake_vendor_on_path("printf high") as env,
            mock.patch.dict(os.environ, env),
            mock.patch("weightclass.triage._read_available_output", side_effect=OSError("raw")),
            mock.patch("weightclass.triage.signal_process_group", side_effect=record_signal),
            mock.patch(
                "weightclass.triage.wait_owned_child",
                side_effect=record_reap,
            ),
        ):
            with self.assertRaises(TriageUnavailableError) as captured:
                self._ask_test_command()

        self.assertEqual(signals, [signal.SIGTERM, signal.SIGKILL])
        self.assertEqual(len(reaped), 1)
        self.assertEqual(captured.exception.args, ())

    def test_selector_unregister_failure_does_not_skip_sigkill_or_reap(self) -> None:
        """Breaks if cleanup registration failure abandons the owned child."""
        real_selector = selectors.DefaultSelector
        signals: list[int] = []
        reaped: list[int] = []
        started_processes: list[subprocess.Popen[bytes]] = []
        real_popen = subprocess.Popen
        real_signal = process_context.signal_process_group
        real_wait = process_context.wait_owned_child

        class SelectorFailingCleanupUnregister:
            def __init__(self) -> None:
                self.wrapped = real_selector()
                self.failed = False

            def register(self, *args: Any) -> Any:
                return self.wrapped.register(*args)

            def unregister(self, file_object: Any) -> Any:
                result = self.wrapped.unregister(file_object)
                if not self.failed:
                    self.failed = True
                    raise OSError("raw")
                return result

            def select(self, timeout: float | None = None) -> list[object]:
                if timeout is not None:
                    time.sleep(timeout)
                return []

            def close(self) -> None:
                self.wrapped.close()

        def record_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
            process = cast(subprocess.Popen[bytes], real_popen(*args, **kwargs))
            started_processes.append(process)
            return process

        def record_signal(process_group_id: int, signal_number: int) -> None:
            signals.append(signal_number)
            real_signal(process_group_id, signal_number)

        def record_reap(process: subprocess.Popen[bytes], timeout: float | None = None) -> int:
            return_code = real_wait(process, timeout)
            reaped.append(return_code)
            return return_code

        try:
            with (
                mock.patch("weightclass.triage.TRIAGE_TIMEOUT_SECONDS", 0.05),
                _fake_vendor_on_path("exec sleep 60") as env,
                mock.patch.dict(os.environ, env),
                mock.patch(
                    "weightclass.triage.selectors.DefaultSelector",
                    new=SelectorFailingCleanupUnregister,
                ),
                mock.patch("weightclass.triage.subprocess.Popen", side_effect=record_popen),
                mock.patch("weightclass.triage.signal_process_group", side_effect=record_signal),
                mock.patch("weightclass.triage.wait_owned_child", side_effect=record_reap),
            ):
                with self.assertRaises(TriageUnavailableError):
                    self._ask_test_command()
        finally:
            for process in started_processes:
                if process.returncode is None:
                    real_signal(process.pid, signal.SIGKILL)
                    real_wait(process)
                if process.stdin is not None and not process.stdin.closed:
                    process.stdin.close()
                if process.stdout is not None and not process.stdout.closed:
                    process.stdout.close()

        self.assertEqual(signals, [signal.SIGTERM, signal.SIGKILL])
        self.assertEqual(len(reaped), 1)

    def test_handle_close_failure_does_not_skip_direct_child_reap(self) -> None:
        """Breaks if releasing one cleanup handle prevents direct-child reap."""
        reaped: list[int] = []
        real_wait = process_context.wait_owned_child

        def record_reap(process: subprocess.Popen[bytes], timeout: float | None = None) -> int:
            return_code = real_wait(process, timeout)
            reaped.append(return_code)
            return return_code

        with (
            _fake_vendor_on_path("printf high") as env,
            mock.patch.dict(os.environ, env),
            mock.patch(
                "weightclass.triage.close_leader_exit_queue",
                side_effect=OSError("raw"),
            ),
            mock.patch(
                "weightclass.triage.wait_owned_child",
                side_effect=record_reap,
            ),
        ):
            with self.assertRaises(TriageUnavailableError) as captured:
                self._ask_test_command()

        self.assertEqual(len(reaped), 1)
        self.assertEqual(captured.exception.args, ())

    def test_stream_close_failure_does_not_skip_sigkill_or_direct_child_reap(self) -> None:
        """Breaks if a pipe close failure short-circuits remaining cleanup."""
        signals: list[int] = []
        reaped: list[int] = []
        close_failed = False
        real_close = triage._close_process_stream
        real_signal = process_context.signal_process_group
        real_wait = process_context.wait_owned_child

        def record_signal(process_group_id: int, signal_number: int) -> None:
            signals.append(signal_number)
            real_signal(process_group_id, signal_number)

        def record_reap(process: subprocess.Popen[bytes], timeout: float | None = None) -> int:
            return_code = real_wait(process, timeout)
            reaped.append(return_code)
            return return_code

        def close_then_fail_once(stream: object) -> None:
            nonlocal close_failed
            real_close(stream)
            if not close_failed:
                close_failed = True
                raise OSError("raw")

        with (
            _fake_vendor_on_path("printf high") as env,
            mock.patch.dict(os.environ, env),
            mock.patch(
                "weightclass.triage._close_process_stream",
                side_effect=close_then_fail_once,
            ),
            mock.patch("weightclass.triage.signal_process_group", side_effect=record_signal),
            mock.patch(
                "weightclass.triage.wait_owned_child",
                side_effect=record_reap,
            ),
        ):
            with self.assertRaises(TriageUnavailableError) as captured:
                self._ask_test_command()

        self.assertEqual(signals, [signal.SIGTERM, signal.SIGKILL])
        self.assertEqual(len(reaped), 1)
        self.assertEqual(captured.exception.args, ())

    def test_a_result_reaped_after_the_deadline_is_not_reported_as_success(self) -> None:
        """Breaks if late cleanup is described as a bounded successful triage."""
        real_wait = process_context.wait_owned_child
        reaped: list[int] = []

        def reap_late(process: subprocess.Popen[bytes], timeout: float | None = None) -> int:
            return_code = real_wait(process, timeout)
            reaped.append(return_code)
            time.sleep(0.08)
            return return_code

        with (
            mock.patch("weightclass.triage.TRIAGE_TIMEOUT_SECONDS", 0.05),
            _fake_vendor_on_path("printf high") as env,
            mock.patch.dict(os.environ, env),
            mock.patch(
                "weightclass.triage.wait_owned_child",
                side_effect=reap_late,
            ),
        ):
            with self.assertRaises(TriageUnavailableError):
                self._ask_test_command()

        self.assertEqual(len(reaped), 1)

    def test_success_closes_all_parent_pipe_objects(self) -> None:
        """Breaks if completed triage leaves stdin or stdout for garbage collection."""
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always", ResourceWarning)
            self.assertEqual(self._ask("printf high"), "high")
            gc.collect()

        resource_warnings = [item for item in captured if item.category is ResourceWarning]
        self.assertEqual(resource_warnings, [])

    def test_the_timeout_default_stays_short(self) -> None:
        """Breaks if a call the module calls cheap gains an open-ended budget."""
        self.assertLessEqual(TRIAGE_TIMEOUT_SECONDS, 120)

    def test_accepts_a_task_larger_than_the_pipe_buffer(self) -> None:
        """Breaks if writing the prompt and reading the answer can deadlock.

        태스크가 파이프 버퍼보다 크면, 프롬프트 쓰기와 응답 읽기를 한 스레드에서
        하다가 양쪽이 서로를 기다릴 수 있다.
        """
        self.assertEqual(self._ask("printf high", task="x" * 200_000), "high")


class ClassifyWithVendorTests(unittest.TestCase):
    def test_default_output_stays_byte_identical(self) -> None:
        """Breaks if the local path gains a key.

        packaging/homebrew/weightclass.rb 와 .github/workflows/ci.yml 이
        {"tier": "low"} 를 정확히 단언하고, formula 는 이미 배포되어 있다.
        """
        result = subprocess.run(
            [sys.executable, "-m", "weightclass", "classify"],
            capture_output=True,
            check=False,
            input="Fix a spelling typo in the README heading.",
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '{"tier": "low"}')

    def test_rejects_bad_input_before_spending_a_vendor_call(self) -> None:
        """Breaks if empty or oversized input can start a billed vendor process.

        검증이 classify_task 안에만 있으면 --ask-vendor 는 분류를 하지 않으므로
        그 검사를 건너뛴다. 예외를 확인하는 것으로는 부족하고, 벤더가 아예
        실행되지 않았다는 것까지 봐야 한다.
        """
        with tempfile.TemporaryDirectory() as directory:
            sentinel = Path(directory) / "vendor-was-called"
            with _fake_vendor_on_path(f"touch {sentinel}; printf low") as env:
                for label, task in {
                    "empty": "",
                    "whitespace only": "   \n  ",
                    "oversized": "x" * 25_000,
                }.items():
                    with self.subTest(case=label):
                        result = subprocess.run(
                            [
                                sys.executable,
                                "-m",
                                "weightclass",
                                "classify",
                                "--source-vendor",
                                "claude",
                                "--ask-vendor",
                            ],
                            capture_output=True,
                            check=False,
                            input=task,
                            text=True,
                            env=env,
                        )

                        self.assertEqual(result.returncode, 2)
                        self.assertEqual(json.loads(result.stderr), {"error": "invalid_task"})
                        self.assertFalse(
                            sentinel.exists(), "vendor was called for input that must fail closed"
                        )

    def test_requires_a_source_vendor(self) -> None:
        """Breaks if the tool has to guess which vendor to bill."""
        result = subprocess.run(
            [sys.executable, "-m", "weightclass", "classify", "--ask-vendor"],
            capture_output=True,
            check=False,
            input="Fix a typo.",
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})

    def test_reports_the_tier_and_its_source(self) -> None:
        errors = io.StringIO()
        output = io.StringIO()
        with (
            mock.patch("weightclass.cli.ask_vendor_for_tier", return_value="high"),
            mock.patch("weightclass.cli.read_task_from_standard_input", return_value="a task"),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            exit_code = cli.classify_from_standard_input("claude", ask_vendor=True)

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue()), {"tier": "high", "tier_source": "vendor"})

    def test_fails_closed_instead_of_falling_back_to_local(self) -> None:
        """Breaks if a failed vendor call silently reverts to keyword matching.

        조용한 폴백은 라우팅이 틀렸다는 사실을 호출자에게서 숨긴다.
        """
        errors = io.StringIO()
        with (
            mock.patch("weightclass.cli.ask_vendor_for_tier", side_effect=TriageUnavailableError()),
            mock.patch("weightclass.cli.read_task_from_standard_input", return_value="a task"),
            contextlib.redirect_stderr(errors),
        ):
            exit_code = cli.classify_from_standard_input("claude", ask_vendor=True)

        self.assertEqual(exit_code, 8)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "triage_unavailable"})

    def test_fake_vendor_failure_is_terminal(self) -> None:
        """Exercise the CLI boundary without invoking an installed vendor."""
        with _fake_vendor_on_path("printf not-a-tier") as env:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "classify",
                    "--source-vendor",
                    "claude",
                    "--ask-vendor",
                ],
                capture_output=True,
                check=False,
                input="Fix a typo.",
                text=True,
                env=env,
            )

        self.assertEqual(result.returncode, 8)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(result.stderr), {"error": "triage_unavailable"})

    def test_show_triage_command_fails_closed_for_a_vendor_with_no_adapter(self) -> None:
        """Breaks if opening the vendor label lets --show-triage-command crash instead of closing.

        벤더 라벨이 열려 있으므로 판정 어댑터가 없는 벤더도 여기까지 내려온다.
        --ask-vendor 경로는 이미 TriageUnavailableError 를 잡지만, 이 분기는
        태스크를 읽기도 전에 별도로 triage_descriptor 를 부르므로 같은 예외를
        따로 잡아야 한다. 잡지 않으면 트레이스백이 새어 나간다.
        """
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "weightclass",
                "classify",
                "--source-vendor",
                "qwen",
                "--show-triage-command",
            ],
            capture_output=True,
            check=False,
            input="",
            text=True,
        )

        self.assertEqual(result.returncode, 8)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(result.stderr), {"error": "triage_unavailable"})
        self.assertNotIn("Traceback", result.stderr)

    def test_does_not_echo_the_task(self) -> None:
        """Breaks if the triage path starts placing task content in output."""
        errors = io.StringIO()
        output = io.StringIO()
        with (
            mock.patch("weightclass.cli.ask_vendor_for_tier", side_effect=TriageUnavailableError()),
            mock.patch(
                "weightclass.cli.read_task_from_standard_input", return_value="Zephyrine quokka"
            ),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            cli.classify_from_standard_input("claude", ask_vendor=True)

        self.assertNotIn("Zephyrine", output.getvalue() + errors.getvalue())


class ExplicitTierTests(unittest.TestCase):
    def _route_with_tier(
        self, tier: str, task: str = "Fix a typo."
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "weightclass",
                "route",
                "--source-vendor",
                "claude",
                "--tier",
                tier,
            ],
            capture_output=True,
            check=False,
            input=task,
            text=True,
        )

    def test_an_explicit_tier_selects_that_tier_route(self) -> None:
        """Breaks if the tier from `wclass classify` cannot be handed to route."""
        result = self._route_with_tier("high")

        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["tier"], "high")
        self.assertEqual(rendered["route"], "claude-high")

    def test_an_explicit_tier_does_not_bypass_task_validation(self) -> None:
        """Breaks if --tier lets empty or oversized input reach a vendor process.

        빈 입력과 길이 상한 검사는 classify_task 안에 있었다. 분류를 건너뛰면
        검증도 함께 건너뛰게 되므로 validate_task 를 따로 호출해야 한다.
        """
        result = self._route_with_tier("low", task="")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_task"})

    def test_run_accepts_an_explicit_tier_without_any_network_call(self) -> None:
        """Breaks if route or run starts reaching a vendor to classify."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            worker_path = directory / "worker.py"
            worker_path.write_text(
                "import sys\nsys.stdin.buffer.read()\nprint('ran')\n", encoding="utf-8"
            )
            policy_path = directory / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "routes": [
                            {
                                "id": "claude-high",
                                "vendor": "claude",
                                "tier": "high",
                                "command": [sys.executable, str(worker_path)],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            review = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "route",
                    "--policy",
                    str(policy_path),
                    "--tier",
                    "high",
                ],
                capture_output=True,
                check=False,
                input="Fix a typo.",
                text=True,
            )
            self.assertEqual(review.returncode, 0, review.stderr)
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "run",
                    "--policy",
                    str(policy_path),
                    "--tier",
                    "high",
                    "--ack-route-fingerprint",
                    json.loads(review.stdout)["route_fingerprint"],
                ],
                capture_output=True,
                check=False,
                input="Fix a typo.",
                text=True,
            )

        # 로컬 판정이었다면 low 라 이 라우트는 선택되지 않는다.
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "ran\n")


class LeaderObserverSharingTests(unittest.TestCase):
    """자식 수명주기 관찰은 한 곳에만 있어야 한다.

    conformance 러너는 Darwin 이 이미 종료한 자식의 EVFILT_PROC 등록을 ESRCH 로
    거부한다는 사실을 처리하고 있었지만, 같은 로직의 사본을 들고 있던 triage 는
    그 처리를 받지 못해 빠르게 죽는 벤더의 정상 답변까지 버렸다. 사본이 다시
    생기면 같은 일이 반복된다.
    """

    def test_triage_uses_the_shared_observer_rather_than_a_private_copy(self) -> None:
        """Breaks if a fixed child-process race is re-forked into a module-local copy."""
        shared = (
            ("open_leader_exit_queue", process_context.open_leader_exit_queue),
            ("observe_leader_exit", process_context.observe_leader_exit),
            ("has_leader_exit_observer", process_context.has_leader_exit_observer),
            ("signal_process_group", process_context.signal_process_group),
            ("close_leader_exit_queue", process_context.close_leader_exit_queue),
        )
        for name, implementation in shared:
            with self.subTest(helper=name):
                self.assertIs(getattr(triage, name), implementation)

        conformance_aliases = (
            ("_observe_leader_exit", process_context.observe_leader_exit),
            ("_has_leader_exit_observer", process_context.has_leader_exit_observer),
            ("_signal_process_group", process_context.signal_process_group),
        )
        for name, implementation in conformance_aliases:
            with self.subTest(helper=f"conformance.{name}"):
                self.assertIs(getattr(delegation_conformance, name), implementation)

    def test_an_already_exited_leader_is_observed_rather_than_treated_as_a_failure(
        self,
    ) -> None:
        """Breaks if the exited-leader sentinel is mistaken for an unusable observer."""
        self.assertTrue(
            process_context.observe_leader_exit(-1, process_context.LEADER_ALREADY_EXITED)
        )
        # 센티널은 닫을 핸들이 아니다. 닫기가 여기서 터지면 정리 경로가 무너진다.
        process_context.close_leader_exit_queue(process_context.LEADER_ALREADY_EXITED)
        process_context.close_leader_exit_queue(None)

    def test_registration_refused_only_because_the_leader_exited_is_not_a_failure(self) -> None:
        """Breaks if Darwin's ESRCH-on-an-exited-child becomes 'observer unusable' again.

        실제 경합 창은 마이크로초라 자식을 실제로 그 타이밍에 죽일 수 없다. 대신
        Darwin 이 그때 내는 ESRCH 를 등록 지점에 직접 넣고, 상태 소유권이 남아
        있는 경우와 아닌 경우가 서로 다른 결과로 갈리는지 확인한다.
        """
        with (
            mock.patch.object(os, "waitid", None, create=True),
            mock.patch.multiple(
                "weightclass.process_context.select",
                KQ_FILTER_PROC=0,
                KQ_EV_ADD=0,
                KQ_EV_ONESHOT=0,
                KQ_NOTE_EXIT=0,
                create=True,
            ),
            mock.patch(
                "weightclass.process_context.select.kevent",
                side_effect=ProcessLookupError(errno.ESRCH, "no such process"),
                create=True,
            ),
        ):
            with mock.patch.object(
                process_context, "darwin_child_status_waitable", return_value=True
            ):
                self.assertIs(
                    process_context.open_leader_exit_queue(1234),
                    process_context.LEADER_ALREADY_EXITED,
                )

            # 상태 소유권이 증명되지 않으면 같은 ESRCH 라도 닫는 방향으로 간다.
            with mock.patch.object(
                process_context, "darwin_child_status_waitable", return_value=False
            ):
                with self.assertRaises(ChildProcessError):
                    process_context.open_leader_exit_queue(1234)

    def test_an_unrelated_registration_failure_still_fails_closed(self) -> None:
        """Breaks if the exited-leader allowance widens into a general error swallow."""
        with (
            mock.patch.object(os, "waitid", None, create=True),
            mock.patch.multiple(
                "weightclass.process_context.select",
                KQ_FILTER_PROC=0,
                KQ_EV_ADD=0,
                KQ_EV_ONESHOT=0,
                KQ_NOTE_EXIT=0,
                create=True,
            ),
            mock.patch(
                "weightclass.process_context.select.kevent",
                side_effect=OSError(errno.EMFILE, "too many open files"),
                create=True,
            ),
        ):
            with self.assertRaises(process_context.LeaderObserverError):
                process_context.open_leader_exit_queue(1234)


if __name__ == "__main__":
    unittest.main()
