"""벤더 판정 경로 테스트.

실제 claude/codex 는 호출하지 않는다. subprocess 를 가로채 응답만 흉내낸다.
"""

import contextlib
import gc
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import warnings
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

from weightclass import cli
from weightclass.router import SUPPORTED_VENDORS
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
    def test_claude_triage_disables_customizations_tools_mcp_and_persistence(self) -> None:
        """Breaks if untrusted task text can reach an ambient Claude capability."""
        command = triage_command("claude")

        self.assertEqual(command[0], "claude")
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

    def test_codex_triage_fails_closed_without_a_no_tools_contract(self) -> None:
        """Breaks if read-only filesystem access is mistaken for no tool access."""
        with self.assertRaises(TriageUnavailableError):
            triage_command("codex")

        descriptor = triage_descriptor("codex")
        self.assertEqual(descriptor["source_vendor"], "codex")
        self.assertFalse(descriptor["available"])
        self.assertEqual(descriptor["unavailable_reason"], "no_no_tools_boundary")
        self.assertNotIn("command", descriptor)

    def test_every_supported_vendor_has_a_reviewable_triage_descriptor(self) -> None:
        """Breaks if a new source vendor has an implicit triage policy."""
        for vendor in SUPPORTED_VENDORS:
            with self.subTest(vendor=vendor):
                descriptor = triage_descriptor(vendor)
                self.assertEqual(descriptor["source_vendor"], vendor)
                self.assertIn("available", descriptor)

    def test_enabled_triage_commands_keep_their_read_only_pin(self) -> None:
        """Breaks if an enabled adapter loses its filesystem restriction."""
        self.assertEqual(set(TRIAGE_COMMANDS), {"claude"})
        self.assertEqual(set(TRIAGE_READ_ONLY_MARKERS), {"claude"})
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


class AskVendorTests(unittest.TestCase):
    """실제 claude/codex 는 부르지 않는다. PATH 앞에 가짜를 놓아 가로챈다.

    subprocess 를 mock 하지 않는 것이 중요하다. 출력 상한과 stderr 폐기는
    실제 파이프에서만 검증되고, mock 은 그 둘을 통과시킨다.
    """

    def _ask(self, body: str, task: str = "task") -> str | None:
        with _fake_vendor_on_path(body) as env, mock.patch.dict(os.environ, env):
            try:
                return ask_vendor_for_tier(task, "claude")
            except TriageUnavailableError:
                return None

    def test_accepts_a_bare_tier_word(self) -> None:
        self.assertEqual(self._ask("printf high"), "high")

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
                ask_vendor_for_tier("task", "claude")

    def test_refuses_output_past_the_size_cap(self) -> None:
        """Breaks if the cap moves back to after the whole stream is buffered."""
        self.assertIsNone(self._ask("printf low; head -c 10000 /dev/zero | tr '\\000' ' '"))

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
            ask_vendor_for_tier("task", "claude")

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
            started = time.monotonic()
            with _fake_python_vendor_on_path(source) as env, mock.patch.dict(os.environ, env):
                tier = ask_vendor_for_tier("task", "claude")

            self.assertEqual(tier, "high")
            self.assertLess(time.monotonic() - started, 1.0)
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
                    ask_vendor_for_tier("task", "claude")

            self.assertLess(time.monotonic() - started, 1.0)
            time.sleep(0.8)
            self.assertFalse(sentinel.exists())

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
                ],
                capture_output=True,
                check=False,
                input="Fix a typo.",
                text=True,
            )

        # 로컬 판정이었다면 low 라 이 라우트는 선택되지 않는다.
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "ran\n")


if __name__ == "__main__":
    unittest.main()
