"""벤더 판정 경로 테스트.

실제 claude/codex 는 호출하지 않는다. subprocess 를 가로채 응답만 흉내낸다.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

from weightclass import cli
from weightclass.router import SUPPORTED_VENDORS
from weightclass.triage import (
    TRIAGE_COMMANDS,
    TriageUnavailableError,
    ask_vendor_for_tier,
    triage_command,
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


class TriageCommandTests(unittest.TestCase):
    def test_every_supported_vendor_has_a_cheap_triage_command(self) -> None:
        """Breaks if a vendor gains a triage command that is not the cheap one.

        판정은 한 단어를 받는 호출이다. 실제 작업과 같은 비용을 쓰면 안 된다.
        """
        # 지원 벤더가 늘었는데 판정 명령이 없으면 그 벤더는 --ask-vendor 를 못 쓴다.
        self.assertEqual(set(TRIAGE_COMMANDS), set(SUPPORTED_VENDORS))
        for vendor, command in TRIAGE_COMMANDS.items():
            with self.subTest(vendor=vendor):
                self.assertEqual(command[0], vendor)
                # codex 는 -c model_reasoning_effort=low 한 토큰으로 전달한다.
                self.assertTrue(
                    any("low" in token for token in command),
                    f"{vendor} triage command does not ask for the cheapest effort",
                )

    def test_codex_triage_never_writes_to_the_workspace(self) -> None:
        """Breaks if asking for a tier gains permission to change files."""
        self.assertIn("read-only", triage_command("codex"))

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

    def test_accepts_a_tier_at_the_end_of_a_sentence(self) -> None:
        """Breaks if a model answering in prose makes triage fail."""
        self.assertEqual(self._ask("printf 'This one is subtle, so: high'"), "high")

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
