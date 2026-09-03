"""`run` 과 `route` 는 티어를 스스로 고르지 않는다.

사전등록된 연구가 티어 라우팅의 이득을 반증했는데도 분류기가 기본 경로로
남아 있었다. 문서는 분류기를 실험적이라고 말하고 CLI 는 그것을 기본값으로
실행하는 상태였고, 이 모듈은 그 불일치가 돌아오지 못하게 한다.

명시적 `--tier` 와 명시적 `--suggest-tier` 중 정확히 하나가 필요하다.
`--suggest-tier` 는 예전 기본 동작과 같은 판정을 내리되, 그 판정 옆에
분류기 자신의 측정 실적을 함께 낸다.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest

from weightclass.classification import CLASSIFIER_MEASURED_AGREEMENT


def _run(
    arguments: list[str], task: str = "Fix a spelling typo."
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "weightclass", *arguments],
        capture_output=True,
        check=False,
        input=task,
        text=True,
        timeout=10,
    )


class ExplicitTierSelectionTests(unittest.TestCase):
    def test_route_without_a_tier_selection_fails_closed(self) -> None:
        """Breaks if the refuted classifier returns as the default front door."""
        result = _run(["route", "--source-vendor", "codex"])

        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})

    def test_run_without_a_tier_selection_fails_closed(self) -> None:
        """Breaks if `run` keeps classifying silently while `route` stops."""
        result = _run(["run", "--source-vendor", "codex"])

        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})

    def test_the_usage_line_is_where_the_requirement_is_discoverable(self) -> None:
        """Breaks if the redacted error is the only signal an operator ever gets.

        `SafeArgumentParser` 는 호출자 값이 새지 않도록 메시지를 통째로 버린다.
        그래서 어떤 플래그가 필요한지는 usage 가 알려주어야 한다.
        """
        for command in ("route", "run"):
            with self.subTest(command=command):
                result = _run([command, "--help"])

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--tier", result.stdout)
                self.assertIn("--suggest-tier", result.stdout)

    def test_tier_and_suggest_tier_together_are_rejected(self) -> None:
        """Breaks if two contradictory tier sources are resolved in one direction."""
        result = _run(["route", "--source-vendor", "codex", "--tier", "low", "--suggest-tier"])

        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(result.stdout, "")

    def test_an_explicit_tier_adds_nothing_to_the_frozen_route_output(self) -> None:
        """Breaks if choosing a tier yourself changes the reviewed route bytes.

        파서가 두 플래그 중 정확히 하나를 요구하므로 이 키들의 부재가 곧
        "조작자가 골랐다" 이다. 동결된 schema-1 출력에 필드를 더할 이유가 없다.
        """
        result = _run(["route", "--source-vendor", "codex", "--tier", "low"])

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["tier"], "low")
        self.assertNotIn("tier_source", payload)
        self.assertNotIn("tier_suggestion", payload)

    def test_suggest_tier_still_classifies_and_reaches_the_same_route(self) -> None:
        """Breaks if the suggestion path stopped being the old default behaviour."""
        suggested = _run(["route", "--source-vendor", "codex", "--suggest-tier"])
        self.assertEqual(suggested.returncode, 0, suggested.stderr)
        payload = json.loads(suggested.stdout)

        pinned = _run(["route", "--source-vendor", "codex", "--tier", payload["tier"]])
        self.assertEqual(pinned.returncode, 0, pinned.stderr)
        self.assertEqual(json.loads(pinned.stdout)["route"], payload["route"])

    def test_suggest_tier_publishes_the_classifier_measured_record(self) -> None:
        """Breaks if a suggestion is offered without its own measured track record."""
        result = _run(["route", "--source-vendor", "codex", "--suggest-tier"])

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["tier_source"], "suggested")
        self.assertEqual(payload["tier_suggestion"]["measured"], CLASSIFIER_MEASURED_AGREEMENT)

    def test_the_measured_record_states_both_routing_directions(self) -> None:
        """Breaks if only over-routing is published while under-routing is the stronger warning."""
        measured = CLASSIFIER_MEASURED_AGREEMENT

        self.assertEqual(measured["agreement"], "10/24 (41.7%)")
        self.assertEqual(measured["high_tier_recall"], "1/9 (11.1%)")
        self.assertEqual(measured["over_routing"], "6/24 (25.0%)")
        self.assertEqual(measured["under_routing"], "8/9 (88.9%)")
        self.assertEqual(measured["reference"], "docs/policy4-fresh-blind-evaluation.md")

    def test_human_route_output_shows_the_suggestion_is_not_established(self) -> None:
        """Breaks if the terminal reader sees a tier with no indication of its accuracy."""
        result = _run(["--human", "route", "--source-vendor", "codex", "--suggest-tier"])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("suggested", result.stdout)
        self.assertIn("41.7%", result.stdout)

    def test_a_suggested_tier_cannot_start_a_vendor_without_a_review(self) -> None:
        """Breaks if the refuted classifier can start a child nobody looked at.

        run 의 stdout 은 자식 것이고 stderr 는 닫힌 JSON 오류 하나만 싣는다.
        제안의 실적을 낼 자리는 콘솔 검토뿐이므로, 검토 없는 제안 실행은
        그 실적을 아무에게도 보이지 않은 채 벤더를 띄우는 일이 된다.
        """
        result = _run(["run", "--preset", "codex-cost-focused", "--suggest-tier"], task="")

        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})

    def test_the_refusal_precedes_every_other_run_check(self) -> None:
        """Breaks if a suggested run reaches policy or task handling before the refusal."""
        result = _run(
            ["run", "--policy", "/nonexistent/policy.json", "--suggest-tier"],
            task="Fix a spelling typo.",
        )

        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})

    def test_an_explicit_tier_run_keeps_stderr_to_the_closed_error(self) -> None:
        """Breaks if stderr stops being one machine-readable object for automation."""
        result = _run(["run", "--preset", "codex-cost-focused", "--tier", "standard"], task="")

        self.assertEqual(result.returncode, 6, result.stderr)
        self.assertEqual(json.loads(result.stderr), {"error": "route_fingerprint_mismatch"})

    def test_human_route_output_omits_the_record_for_an_explicit_tier(self) -> None:
        """Breaks if the classifier's record is shown where the classifier did not decide."""
        result = _run(["--human", "route", "--source-vendor", "codex", "--tier", "low"])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("41.7%", result.stdout)


if __name__ == "__main__":
    unittest.main()
