from __future__ import annotations

import contextlib
import io
import sys
import unittest
from collections.abc import Callable, Sequence

from weightclass.advisory import (
    advisory_campaign,
    advisory_portfolio,
    advisory_routes,
    speculative_report,
    speculative_run,
)


class AdvisoryArgvIsolationTests(unittest.TestCase):
    def test_argument_aware_entrypoints_never_replace_process_argv(self) -> None:
        entrypoints: tuple[Callable[[Sequence[str] | None], int], ...] = (
            advisory_campaign.main,
            advisory_routes.main,
            speculative_report.main,
            advisory_portfolio.main,
            speculative_run.main,
        )
        original_object = sys.argv
        original_values = list(sys.argv)
        for entrypoint in entrypoints:
            with self.subTest(entrypoint=entrypoint.__module__):
                with (
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()),
                    self.assertRaises(SystemExit) as raised,
                ):
                    entrypoint(["--help"])
                self.assertEqual(raised.exception.code, 0)
                self.assertIs(sys.argv, original_object)
                self.assertEqual(sys.argv, original_values)


if __name__ == "__main__":
    unittest.main()
