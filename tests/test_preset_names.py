"""패키지 프리셋의 이름은 `<vendor>-model-override` 이고, 옛 이름은 별칭이다.

`*-cost-focused` 라는 이름은 측정되지 않은 절약을 주장했다. 저장소의 연구는
effort 라우팅에서 이득을 찾지 못했고 남은 레버는 모델 등급이므로, 프리셋이
실제로 하는 일(모델/effort 라벨을 내장 라우트에 덮어쓰기)을 이름이 말하게
한다. 명령 바이트와 지문은 바뀌지 않는다. 옛 이름은 모든 선택 지점에서
같은 프리셋으로 해석되어 기존 스크립트가 깨지지 않는다.
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from unittest import mock

from weightclass import cli

VENDORS = ("agy", "claude", "codex", "grok")


def _capture(arguments: list[str], task: str | None = None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    stdin = io.TextIOWrapper(io.BytesIO((task or "").encode("utf-8")), encoding="utf-8")
    with (
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
        mock.patch("sys.stdin", stdin),
    ):
        exit_code = cli.main(["--json", *arguments])
    return exit_code, out.getvalue(), err.getvalue()


class PresetNameTests(unittest.TestCase):
    def test_the_canonical_names_say_what_the_preset_does(self) -> None:
        """Breaks if a packaged preset is again named after an unmeasured saving."""
        self.assertEqual(
            tuple(cli.EXAMPLE_POLICY_RESOURCES),
            tuple(f"{vendor}-model-override" for vendor in VENDORS),
        )

    def test_the_old_name_resolves_to_the_same_preset(self) -> None:
        """Breaks if a `*-cost-focused` script stops working or lands on a different preset."""
        for vendor in VENDORS:
            with self.subTest(vendor=vendor):
                self.assertEqual(
                    cli.canonical_preset_name(f"{vendor}-cost-focused"),
                    f"{vendor}-model-override",
                )
                self.assertEqual(
                    cli.canonical_preset_name(f"{vendor}-model-override"),
                    f"{vendor}-model-override",
                )

    def test_an_unknown_preset_name_fails_closed(self) -> None:
        """Breaks if the alias table starts guessing at names it was not given."""
        for name in ("codex", "codex-model-override-low", "cost-focused", ""):
            with self.subTest(name=name), self.assertRaises(cli.InvalidInputError):
                cli.canonical_preset_name(name)

    def test_example_policy_bytes_are_identical_under_both_names(self) -> None:
        """Breaks if renaming the preset changed the policy, and so the fingerprints."""
        for vendor in VENDORS:
            with self.subTest(vendor=vendor):
                new_code, new_out, _ = _capture(["example-policy", f"{vendor}-model-override"])
                old_code, old_out, _ = _capture(["example-policy", f"{vendor}-cost-focused"])
                self.assertEqual((new_code, old_code), (0, 0))
                self.assertEqual(new_out, old_out)

    def test_review_preset_is_byte_identical_and_reports_the_canonical_name(self) -> None:
        """Breaks if an alias yields a different command, fingerprint, status, or name."""
        for vendor in VENDORS:
            with self.subTest(vendor=vendor):
                new_code, new_out, _ = _capture(["review-preset", f"{vendor}-model-override"])
                old_code, old_out, _ = _capture(["review-preset", f"{vendor}-cost-focused"])
                self.assertEqual((new_code, old_code), (0, 0))
                receipt = json.loads(old_out)
                self.assertEqual(receipt["preset"], f"{vendor}-model-override")
                self.assertIn("route_fingerprint", json.dumps(receipt))
                self.assertEqual(new_out, old_out)

    def test_an_alias_accepts_the_same_label_overrides(self) -> None:
        """Breaks if `--model` or a tier label stops working under the old name."""
        new_code, new_out, _ = _capture(
            ["review-preset", "codex-model-override", "--model", "your-model"]
        )
        old_code, old_out, _ = _capture(
            ["review-preset", "codex-cost-focused", "--model", "your-model"]
        )
        self.assertEqual((new_code, old_code), (0, 0))
        self.assertEqual(new_out, old_out)
        self.assertEqual(json.loads(old_out)["configuration_status"], "unqualified_custom")

    def test_the_measured_status_survives_the_alias(self) -> None:
        """Breaks if the claude alias loses its measured-low-route status."""
        status = cli._packaged_configuration_status
        overrides = cli.PresetOverrides()
        self.assertEqual(
            status("claude-cost-focused", None, overrides),
            status("claude-model-override", None, overrides),
        )
        self.assertEqual(status("claude-cost-focused", None, overrides), "measured_low_route_only")

    def test_an_unknown_preset_name_exits_two_at_the_command_line(self) -> None:
        """Breaks if the alias table opens the door to names it was never given."""
        for arguments in (
            ["example-policy", "codex"],
            ["review-preset", "codex-cost"],
            ["route", "--preset", "claude-model-override-low", "--tier", "low"],
        ):
            with self.subTest(arguments=arguments):
                exit_code, out, err = _capture(arguments, task="Fix a typo.")
                self.assertEqual(exit_code, 2)
                self.assertEqual(out, "")
                self.assertEqual(json.loads(err), {"error": "invalid_input"})

    def test_help_lists_only_the_canonical_names(self) -> None:
        """Breaks if the usage line grows the alias list or drops the real names."""
        for arguments in (["run", "--help"], ["route", "--help"]):
            out = io.StringIO()
            with self.subTest(arguments=arguments), contextlib.redirect_stdout(out):
                with self.assertRaises(SystemExit):
                    cli.main(arguments)
                rendered = " ".join(out.getvalue().split())
                self.assertNotIn("cost-focused,", rendered)
                self.assertIn("codex-model-override", rendered)


if __name__ == "__main__":
    unittest.main()
