"""단말에서 `run` 은 검토를 기본으로 켠다.

안전 계약의 핵심은 "실행 전에 명령을 보여 준다" 이지 "지문을 클립보드에
복사한다" 가 아니다. `--review` 는 이미 복사 없는 검토를 제공하는데도, 단말
사용자는 그것을 알고 플래그를 붙여야만 했고 정책 실행은 지문 없이 6 으로
죽었다. 이 모듈은 사람이 보고 있는 단말에서는 검토가 기본이고, 자동화 신호
(비단말 stdout, 명시적 `--json`, 지문 인수, `--no-review`) 가 있으면 예전
계약이 그대로 유지된다는 것을 고정한다.

모든 검사는 인프로세스로 돌고 라우트는 가짜 정책이 공급한다. 콘솔 확인은
가로채서 거절하므로 자식 프로세스는 절대 시작되지 않는다.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest import mock

from weightclass import cli

POLICY = {
    "routes": [
        {"id": f"fake-{tier}", "vendor": "codex", "tier": tier, "command": ["owned-fake", tier]}
        for tier in ("low", "standard", "high")
    ]
}


class _TerminalStdout(io.StringIO):
    """stdout 이 단말이라고 보고하는 캡처 스트림."""

    def isatty(self) -> bool:
        return True


class TerminalReviewDefaultTests(unittest.TestCase):
    def _invoke(self, arguments: list[str], *, terminal: bool) -> tuple[int, str, list[object]]:
        """Run `run` in-process; the console confirmation is intercepted and refused."""
        prompts: list[object] = []

        def refuse(*call_arguments: object) -> bool:
            prompts.append(call_arguments)
            return False

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(POLICY), encoding="utf-8")
            out: io.StringIO = _TerminalStdout() if terminal else io.StringIO()
            err = io.StringIO()
            stdin = io.TextIOWrapper(io.BytesIO(b"Fix a spelling typo."), encoding="utf-8")
            with (
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(err),
                mock.patch("sys.stdin", stdin),
                mock.patch.object(cli, "_confirm_legacy_route_on_console", refuse),
            ):
                exit_code = cli.main([*arguments, "--policy", str(path)])
        return exit_code, err.getvalue(), prompts

    def test_a_terminal_run_reviews_instead_of_demanding_a_fingerprint(self) -> None:
        """Breaks if a terminal user still needs to copy a fingerprint to run a policy."""
        exit_code, err, prompts = self._invoke(["run", "--tier", "low"], terminal=True)

        self.assertEqual(len(prompts), 1)
        self.assertEqual(exit_code, 1)
        self.assertEqual(json.loads(err), {"error": "execution_cancelled"})

    def test_a_non_terminal_run_keeps_the_fingerprint_requirement(self) -> None:
        """Breaks if automation silently gains an interactive prompt or loses the gate."""
        exit_code, err, prompts = self._invoke(["run", "--tier", "low"], terminal=False)

        self.assertEqual(prompts, [])
        self.assertEqual(exit_code, 6)
        self.assertEqual(json.loads(err), {"error": "route_fingerprint_mismatch"})

    def test_explicit_json_output_is_an_automation_signal(self) -> None:
        """Breaks if `--json` on a terminal still opens the console prompt."""
        exit_code, err, prompts = self._invoke(["--json", "run", "--tier", "low"], terminal=True)

        self.assertEqual(prompts, [])
        self.assertEqual(exit_code, 6)
        self.assertEqual(json.loads(err), {"error": "route_fingerprint_mismatch"})

    def test_no_review_restores_the_fingerprint_gate_on_a_terminal(self) -> None:
        """Breaks if `--no-review` cannot opt a terminal back into the strict path."""
        exit_code, err, prompts = self._invoke(
            ["run", "--tier", "low", "--no-review"], terminal=True
        )

        self.assertEqual(prompts, [])
        self.assertEqual(exit_code, 6)
        self.assertEqual(json.loads(err), {"error": "route_fingerprint_mismatch"})

    def test_an_acknowledged_fingerprint_never_adds_a_prompt(self) -> None:
        """Breaks if a scripted acknowledgement on a terminal starts asking questions."""
        exit_code, err, prompts = self._invoke(
            ["run", "--tier", "low", "--ack-route-fingerprint", "sha256:not-it"],
            terminal=True,
        )

        self.assertEqual(prompts, [])
        self.assertEqual(exit_code, 6)
        self.assertEqual(json.loads(err), {"error": "route_fingerprint_mismatch"})

    def test_a_suggested_tier_on_a_terminal_reaches_the_review(self) -> None:
        """Breaks if the classifier suggestion needs a second flag before a human sees it."""
        exit_code, err, prompts = self._invoke(["run", "--suggest-tier"], terminal=True)

        self.assertEqual(len(prompts), 1)
        self.assertEqual(exit_code, 1)
        self.assertEqual(json.loads(err), {"error": "execution_cancelled"})

    def test_a_suggested_tier_without_a_terminal_is_still_refused(self) -> None:
        """Breaks if the refuted classifier can start a child nobody looked at."""
        exit_code, err, prompts = self._invoke(["run", "--suggest-tier"], terminal=False)

        self.assertEqual(prompts, [])
        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(err), {"error": "invalid_input"})

    def test_no_review_does_not_let_a_suggested_tier_start_unseen(self) -> None:
        """Breaks if turning the terminal default off also turns the classifier gate off."""
        exit_code, err, prompts = self._invoke(
            ["run", "--suggest-tier", "--no-review"], terminal=True
        )

        self.assertEqual(prompts, [])
        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(err), {"error": "invalid_input"})

    def test_an_acknowledged_fingerprint_does_not_replace_the_classifier_review(self) -> None:
        """Breaks if a scripted acknowledgement counts as someone having seen the suggestion."""
        exit_code, err, prompts = self._invoke(
            ["run", "--suggest-tier", "--ack-route-fingerprint", "sha256:not-it"],
            terminal=True,
        )

        self.assertEqual(prompts, [])
        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(err), {"error": "invalid_input"})

    def test_an_explicit_review_still_reviews_without_a_terminal(self) -> None:
        """Breaks if the terminal default quietly became the only way to get a review."""
        exit_code, err, prompts = self._invoke(["run", "--tier", "low", "--review"], terminal=False)

        self.assertEqual(len(prompts), 1)
        self.assertEqual(exit_code, 1)
        self.assertEqual(json.loads(err), {"error": "execution_cancelled"})

    def test_human_rendering_in_a_pipe_is_not_a_terminal(self) -> None:
        """Breaks if `--human` alone can open a prompt where no terminal exists."""
        exit_code, err, prompts = self._invoke(["--human", "run", "--tier", "low"], terminal=False)

        self.assertEqual(prompts, [])
        self.assertEqual(exit_code, 6)
        self.assertEqual(json.loads(err), {"error": "route_fingerprint_mismatch"})

    def test_review_and_an_acknowledged_fingerprint_together_are_rejected(self) -> None:
        """Breaks if a scripted acknowledgement can be combined with a console prompt.

        `--review` 는 검토를 사람에게, 지문은 검토를 스크립트에 맡긴다는 뜻이다.
        둘을 함께 주면 어느 쪽이 검토했는지 알 수 없으므로 한 방향으로 해석하지
        않고 거부한다. 외부 리뷰가 이 쌍을 "검토가 이긴다" 고 읽었는데, 코드는
        거부한다. 그 사실을 여기서 고정한다.
        """
        exit_code, err, prompts = self._invoke(
            ["run", "--tier", "low", "--review", "--ack-route-fingerprint", "sha256:x"],
            terminal=True,
        )

        self.assertEqual(prompts, [])
        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(err), {"error": "invalid_input"})

    def test_review_and_no_review_together_are_rejected(self) -> None:
        """Breaks if two contradictory review instructions resolve in one direction."""
        exit_code, err, prompts = self._invoke(
            ["run", "--tier", "low", "--review", "--no-review"], terminal=True
        )

        self.assertEqual(prompts, [])
        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(err), {"error": "invalid_input"})


class RunHelpTests(unittest.TestCase):
    def _subparser(self, name: str) -> argparse.ArgumentParser:
        parser = cli.build_parser()
        subparsers = [
            action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
        ]
        return cast(argparse.ArgumentParser, subparsers[0].choices[name])

    def _run_parser(self) -> argparse.ArgumentParser:
        return self._subparser("run")

    def test_every_run_and_route_option_explains_itself(self) -> None:
        """Breaks if a `run` or `route` flag is listed without saying what it does."""
        for name in ("run", "route"):
            for action in self._subparser(name)._actions:
                with self.subTest(command=name, option=action.option_strings):
                    self.assertTrue(action.help, action.option_strings)

    def test_run_help_shows_the_terminal_default_and_a_first_command(self) -> None:
        """Breaks if `--help` alone cannot get a new user to a first reviewed run."""
        # argparse 는 help 를 줄바꿈으로 감싸므로 공백을 정규화한 뒤 비교한다.
        rendered = " ".join(self._run_parser().format_help().split())

        self.assertIn("--no-review", rendered)
        self.assertIn("default when stdout is a terminal", rendered)
        self.assertIn("wclass --json run", rendered)
        self.assertIn("printf '%s' 'Fix a spelling typo.' | wclass run", rendered)


if __name__ == "__main__":
    unittest.main()
