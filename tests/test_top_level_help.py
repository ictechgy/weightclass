"""`wclass --help` 는 매일 쓰는 명령만 보이고, 고급 명령은 한 줄로 가리킨다.

상위 help 에 열네 개 동사가 나열되어 있어서 새 사용자는 제품이 아니라 연구
디렉터리 목록을 마주했다. 동작은 하나도 지우지 않는다. 고급 명령은 그대로
파싱되고 실행되지만, 목록에서는 빠지고 epilog 한 줄이 이름과 위치를
가리킨다. 이 모듈은 그 경계를 양쪽에서 고정한다: 보이는 집합은 정확히
이것이고, 가려진 명령은 여전히 살아 있다.
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest

from weightclass import cli

VISIBLE = ("discover", "classify", "route", "run", "usage", "example-policy", "review-preset")
ADVANCED = ("profile", "select", "review-cost-profile", "recommend", "render")


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
        # `profile` 은 `review-cost-profile` 안에도 들어 있으므로 단어 경계가 아니라
        # 쉼표로 나눈 토큰을 센다.
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


if __name__ == "__main__":
    unittest.main()
