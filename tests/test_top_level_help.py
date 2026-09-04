"""`wclass --help` 는 매일 쓰는 명령만 보이고, 고급 명령은 한 줄로 가리킨다.

상위 help 에 열네 개 동사가 나열되어 있어서 새 사용자는 제품이 아니라 연구
디렉터리 목록을 마주했다. 남은 고급 명령은 그대로 파싱되고 실행되지만,
목록에서는 빠지고 epilog 한 줄이 이름과 위치를 가리킨다. 이 모듈은 그 경계를
양쪽에서 고정한다: 보이는 집합은 정확히 이것이고, 가려진 명령은 여전히 살아
있으며, 0.32.0 에서 제거된 표면은 어느 쪽에서도 다시 나타나지 않는다.
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest

from weightclass import cli

VISIBLE = ("discover", "classify", "route", "run", "usage", "example-policy", "review-preset")
ADVANCED = ("profile", "select")
# 0.32.0 에서 제거된 서브커맨드. 이름을 다시 파서에 넣으면 이 목록이 깨진다.
REMOVED_COMMANDS = ("recommend", "review-cost-profile", "render")
# 0.32.0 에서 제거된 플래그. 살아 있는 명령에 붙어 있으므로 파서가 닫아야 한다.
# run 은 티어 출처를 필수로 요구하므로, 플래그 하나만으로는 무엇이 거부를
# 일으켰는지 알 수 없다. 정상적으로 통과할 인수를 갖춘 뒤 플래그만 더한다.
REMOVED_FLAG_INVOCATIONS = (
    ["classify", "--ask-vendor"],
    ["classify", "--source-vendor", "codex", "--ask-vendor"],
    ["classify", "--show-triage-command", "--source-vendor", "codex"],
    ["run", "--source-vendor", "codex", "--tier", "low", "--suggest-escalation"],
)


class TopLevelHelpTests(unittest.TestCase):
    def _top_level_help(self) -> str:
        return cli.build_parser().format_help()

    def test_the_listing_is_exactly_the_daily_command_set(self) -> None:
        """Breaks if an advanced command returns to the listing or a daily one drops out."""
        rendered = self._top_level_help()
        listing = rendered.split("advanced commands", 1)[0]
        for name in VISIBLE:
            with self.subTest(visible=name):
                self.assertRegex(listing, rf"(?m)^\s+{name}\s{{2,}}\S")
        for name in ADVANCED:
            with self.subTest(hidden=name):
                self.assertNotRegex(listing, rf"(?m)^\s+{name}\s{{2,}}\S")

    def test_advanced_commands_are_named_once_in_the_epilog(self) -> None:
        """Breaks if a hidden command becomes undiscoverable from `--help` alone."""
        rendered = self._top_level_help()
        self.assertIn("advanced commands", rendered)
        epilog = rendered.split("advanced commands", 1)[1]
        tokens = [token.strip() for token in epilog.split(":", 1)[1].split(",")]
        for name in ADVANCED:
            with self.subTest(advanced=name):
                self.assertEqual(tokens.count(name), 1, tokens)

    def test_the_first_command_is_in_the_help(self) -> None:
        """Breaks if `--help` alone cannot get a new user to a reviewed first run."""
        self.assertIn("printf '%s' 'Fix a spelling typo.' | wclass run", self._top_level_help())

    def test_the_description_does_not_make_classification_the_job(self) -> None:
        """Breaks if the one-line description advertises the refuted classifier again."""
        first_line = (cli.build_parser().description or "").casefold()
        self.assertNotIn("classif", first_line)
        self.assertIn("review", first_line)

    def test_an_unknown_command_still_fails_closed_without_listing_anything(self) -> None:
        """Breaks if hiding commands changed the closed JSON diagnostic for a bad name."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            exit_code = cli.main(["bogus"])
        self.assertEqual(exit_code, 2)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(json.loads(err.getvalue()), {"error": "invalid_input"})

    def test_hidden_commands_still_parse_and_run_their_help(self) -> None:
        """Breaks if hiding a command from the listing also removed it."""
        for name in ADVANCED:
            out, err = io.StringIO(), io.StringIO()
            # 서브파서의 --help 는 argparse 가 SystemExit(0) 으로 끝낸다.
            with (
                self.subTest(advanced=name),
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(err),
                self.assertRaises(SystemExit) as raised,
            ):
                cli.main([name, "--help"])
            self.assertEqual(raised.exception.code, 0, err.getvalue())
            self.assertIn(name, out.getvalue())


class RemovedSurfaceTests(unittest.TestCase):
    """0.32.0 에서 지운 표면은 조용히 무시되지 않고 닫힌 진단으로 거부된다.

    표면을 지울 때 위험한 실패 방식은 두 가지다. 플래그만 지우고 파서가 접두사
    매칭으로 계속 받아들이는 것, 그리고 서브커맨드 이름이 다른 경로로 살아남는
    것이다. 둘 다 사용자에게는 "동작하는 것처럼 보이는" 실패다.

    플래그는 파서에서만 확인한다. main() 으로 확인하면, 플래그가 되살아났을 때
    이 테스트가 실패하는 대신 자식이 읽을 표준 입력을 기다리며 멈춘다. 멈추는
    가드는 실패를 보고하지 못한다.
    """

    def _closed_command(self, argv: list[str]) -> None:
        """Assert the user-visible contract: exit 2 and one closed JSON object."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            exit_code = cli.main(argv)
        self.assertEqual(exit_code, 2)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(json.loads(err.getvalue()), {"error": "invalid_input"})

    def test_removed_subcommands_are_unknown_names(self) -> None:
        """Breaks if `recommend`, `review-cost-profile`, or `render` is reachable again."""
        for name in REMOVED_COMMANDS:
            with self.subTest(removed=name):
                self._closed_command([name, "--help"])

    def test_removed_subcommands_are_absent_from_the_help_text(self) -> None:
        """Breaks if a removed verb is still advertised anywhere in `--help`."""
        rendered = cli.build_parser().format_help()
        for name in REMOVED_COMMANDS:
            with self.subTest(removed=name):
                self.assertNotIn(name, rendered)

    def test_removed_flags_are_rejected_by_the_parser(self) -> None:
        """Breaks if vendor triage or escalation suggestion is accepted again."""
        for argv in REMOVED_FLAG_INVOCATIONS:
            with self.subTest(argv=argv), self.assertRaises(cli.InvalidInputError):
                cli.build_parser().parse_args(argv)

    def test_a_removed_flag_still_fails_closed_through_main(self) -> None:
        """Breaks if the parser closes but the dispatcher reports something else."""
        self._closed_command(["classify", "--ask-vendor"])


if __name__ == "__main__":
    unittest.main()
