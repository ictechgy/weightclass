import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import mock_open, patch

from weightclass import cli
from weightclass.native_v3_selector import InteractiveSelectorError, run_interactive_selector


class NativeV3SelectorTests(unittest.TestCase):
    def installed(self, directory: str) -> str:
        for name in ("codex", "grok"):
            path = Path(directory) / name
            path.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
            path.chmod(0o755)
        return directory

    def answers(
        self,
        confirmation: str = "yes",
        *,
        transition_confirmation: str = "yes",
    ) -> io.StringIO:
        # Numeric choices: source Codex; low Grok/low/custom; standard skip;
        # high Codex/high/custom; profile and vendor transition consent; emit.
        return io.StringIO(
            "1\nwork\n"
            "2\npersonal\n1\n2\ngrok-low-model\n"
            "\n"
            "1\nwork\n3\n2\ncodex-high-model\n"
            f"{transition_confirmation}\n{transition_confirmation}\n{confirmation}\n"
        )

    def test_numeric_installed_choices_emit_canonical_policy_after_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            console = io.StringIO()
            output = io.StringIO()
            result = run_interactive_selector(
                self.answers(), console, output, path_value=self.installed(directory)
            )
        self.assertEqual(result, 0)
        policy = json.loads(output.getvalue())
        self.assertEqual([r["tier"] for r in policy["routes"]], ["low", "high"])
        self.assertEqual(
            policy["vendor_grants"],
            [{"id": "codex-to-grok", "from_vendor": "codex", "to_vendor": "grok"}],
        )
        self.assertEqual(
            policy["profile_grants"],
            [
                {
                    "id": "codex-to-profile-1",
                    "from_profile_id": "source",
                    "to_profile_id": "profile-1",
                }
            ],
        )
        self.assertEqual(
            [target["allowed_model_effort_pairs"] for target in policy["execution_targets"]],
            [
                [{"model": "grok-low-model", "effort": "low"}],
                [{"model": "codex-high-model", "effort": "high"}],
            ],
        )
        self.assertIn("profile transition source -> profile-1", console.getvalue())
        self.assertIn("vendor transition codex -> grok", console.getvalue())
        self.assertIn('"task_delivery":"argv"', console.getvalue())
        self.assertIn('"argv_template"', console.getvalue())
        self.assertNotIn("command", policy)

    def test_cancel_and_eof_emit_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.installed(directory)
            for answers in (
                self.answers("no"),
                self.answers(transition_confirmation="no"),
                io.StringIO(""),
            ):
                output = io.StringIO()
                self.assertEqual(
                    run_interactive_selector(answers, io.StringIO(), output, path_value=path), 1
                )
                self.assertEqual(output.getvalue(), "")

    def test_cli_select_uses_os_controlling_terminal_and_keeps_stdout_for_policy(self) -> None:
        with (
            patch("weightclass.cli.os.ctermid", return_value="/controlled/tty") as ctermid,
            patch("weightclass.cli.open", mock_open()) as opener,
            patch("weightclass.cli.run_interactive_selector", return_value=1) as selector,
        ):
            self.assertEqual(cli.main(["select"]), 1)
        ctermid.assert_called_once_with()
        opener.assert_called_once_with("/controlled/tty", "r+", encoding="utf-8", buffering=1)
        self.assertIs(selector.call_args.args[2], __import__("sys").stdout)

    def test_observation_runtime_error_is_redacted_at_the_selector_boundary(self) -> None:
        def unavailable(path: str) -> object:
            del path
            raise RuntimeError("PRIVATE PATH")

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(InteractiveSelectorError) as raised:
                run_interactive_selector(
                    self.answers(),
                    io.StringIO(),
                    io.StringIO(),
                    path_value=self.installed(directory),
                    observer=unavailable,  # type: ignore[arg-type]
                )

        self.assertEqual(str(raised.exception), "")

    def test_oversized_console_fields_fail_closed(self) -> None:
        """Breaks if controlling-console input can grow or parse without a bound."""
        with tempfile.TemporaryDirectory() as directory:
            installed = self.installed(directory)
            for answers in (
                io.StringIO("1" * 33 + "\n"),
                io.StringIO("1\n" + "x" * 4_097 + "\n"),
            ):
                with (
                    self.subTest(size=len(answers.getvalue())),
                    self.assertRaises(InteractiveSelectorError),
                ):
                    run_interactive_selector(
                        answers,
                        io.StringIO(),
                        io.StringIO(),
                        path_value=installed,
                    )


if __name__ == "__main__":
    unittest.main()
