"""`run` 과 `route` 는 티어를 스스로 고르지 않는다.

사전등록된 연구가 티어 라우팅의 이득을 반증했는데도 분류기가 기본 경로로
남아 있었다. 문서는 분류기를 실험적이라고 말하고 CLI 는 그것을 기본값으로
실행하는 상태였고, 이 모듈은 그 불일치가 돌아오지 못하게 한다.

명시적 `--tier` 와 명시적 `--suggest-tier` 중 정확히 하나가 필요하다.
`--suggest-tier` 는 예전 기본 동작과 같은 판정을 내리되, 그 판정 옆에
분류기 자신의 측정 실적을 함께 낸다.

모든 검사는 인프로세스로 돈다. 벤더 CLI 설치 여부에 기대지 않도록 라우트는
정책 파일이 공급하고, 인터프리터를 새로 띄우지 않아 같은 러너에서 도는 시간
민감한 검사에 부하를 주지 않는다.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from weightclass import cli
from weightclass.classification import CLASSIFIER_MEASURED_AGREEMENT

# 정책이 명령을 공급하므로 이 검사는 설치된 벤더를 필요로 하지 않는다.
POLICY = {
    "routes": [
        {"id": f"fake-{tier}", "vendor": "codex", "tier": tier, "command": ["owned-fake", tier]}
        for tier in ("low", "standard", "high")
    ]
}


class ExplicitTierSelectionTests(unittest.TestCase):
    def _invoke(
        self, arguments: list[str], task: str = "Fix a spelling typo."
    ) -> tuple[int, str, str]:
        """Run one CLI invocation in-process against a task-free fake policy."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(POLICY), encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            # 라우터는 sys.stdin.buffer 에서 바이트를 읽는다. 텍스트 래퍼를
            # 씌워야 실제 경로와 같은 디코딩을 거친다.
            stdin = io.TextIOWrapper(io.BytesIO(task.encode("utf-8")), encoding="utf-8")
            with (
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(err),
                mock.patch("sys.stdin", stdin),
            ):
                exit_code = cli.main([*arguments, "--policy", str(path)])
        return exit_code, out.getvalue(), err.getvalue()

    def test_route_without_a_tier_selection_fails_closed(self) -> None:
        """Breaks if the refuted classifier returns as the default front door."""
        exit_code, out, err = self._invoke(["route"])

        self.assertEqual(exit_code, 2, out)
        self.assertEqual(out, "")
        self.assertEqual(json.loads(err), {"error": "invalid_input"})

    def test_run_without_a_tier_selection_fails_closed(self) -> None:
        """Breaks if `run` keeps classifying silently while `route` stops."""
        exit_code, out, err = self._invoke(["run"])

        self.assertEqual(exit_code, 2, out)
        self.assertEqual(out, "")
        self.assertEqual(json.loads(err), {"error": "invalid_input"})

    def test_tier_and_suggest_tier_together_are_rejected(self) -> None:
        """Breaks if two contradictory tier sources are resolved in one direction."""
        exit_code, out, _ = self._invoke(["route", "--tier", "low", "--suggest-tier"])

        self.assertEqual(exit_code, 2, out)
        self.assertEqual(out, "")

    def test_the_usage_line_is_where_the_requirement_is_discoverable(self) -> None:
        """Breaks if the redacted error is the only signal an operator ever gets.

        `SafeArgumentParser` 는 호출자 값이 새지 않도록 메시지를 통째로 버린다.
        그래서 어떤 플래그가 필요한지는 usage 가 알려주어야 한다.
        """
        for command in ("route", "run"):
            with self.subTest(command=command):
                out = io.StringIO()
                with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as raised:
                    cli.main([command, "--help"])

                self.assertEqual(raised.exception.code, 0)
                self.assertIn("--tier", out.getvalue())
                self.assertIn("--suggest-tier", out.getvalue())

    def test_an_explicit_tier_adds_nothing_to_the_frozen_route_output(self) -> None:
        """Breaks if choosing a tier yourself changes the reviewed route bytes.

        파서가 두 플래그 중 정확히 하나를 요구하므로 이 키들의 부재가 곧
        "조작자가 골랐다" 이다. 동결된 schema-1 출력에 필드를 더할 이유가 없다.
        """
        exit_code, out, err = self._invoke(["route", "--tier", "low"])

        self.assertEqual(exit_code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["tier"], "low")
        self.assertNotIn("tier_source", payload)
        self.assertNotIn("tier_suggestion", payload)

    def test_suggest_tier_still_classifies_and_reaches_the_same_route(self) -> None:
        """Breaks if the suggestion path stopped being the old default behaviour."""
        exit_code, out, err = self._invoke(["route", "--suggest-tier"])
        self.assertEqual(exit_code, 0, err)
        suggested = json.loads(out)

        pinned_code, pinned_out, pinned_err = self._invoke(
            ["route", "--tier", str(suggested["tier"])]
        )
        self.assertEqual(pinned_code, 0, pinned_err)
        pinned = json.loads(pinned_out)
        self.assertEqual(pinned["route"], suggested["route"])
        # 지문까지 같아야 봉인된 모집단의 route 마이그레이션이 필요 없다.
        self.assertEqual(pinned["route_fingerprint"], suggested["route_fingerprint"])

    def test_suggest_tier_publishes_the_classifier_measured_record(self) -> None:
        """Breaks if a suggestion is offered without its own measured track record."""
        exit_code, out, err = self._invoke(["route", "--suggest-tier"])

        self.assertEqual(exit_code, 0, err)
        payload = json.loads(out)
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

    def test_a_suggested_tier_cannot_start_a_vendor_without_a_review(self) -> None:
        """Breaks if the refuted classifier can start a child nobody looked at.

        run 의 stdout 은 자식 것이고 stderr 는 닫힌 JSON 오류 하나만 싣는다.
        제안의 실적을 낼 자리는 콘솔 검토뿐이므로, 검토 없는 제안 실행은
        그 실적을 아무에게도 보이지 않은 채 벤더를 띄우는 일이 된다.
        """
        exit_code, out, err = self._invoke(["run", "--suggest-tier"])

        self.assertEqual(exit_code, 2, out)
        self.assertEqual(out, "")
        self.assertEqual(json.loads(err), {"error": "invalid_input"})

    def test_the_refusal_precedes_every_other_run_check(self) -> None:
        """Breaks if a suggested run reaches policy or task handling before the refusal.

        정책을 읽을 수조차 없는 실행이 그래도 이 거부로 끝나야, 거부가 뒤쪽
        어떤 검사에도 가려지지 않는다는 것이 확인된다.
        """
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            exit_code = cli.main(["run", "--suggest-tier", "--policy", "/nonexistent/policy.json"])

        self.assertEqual(exit_code, 2, out.getvalue())
        self.assertEqual(json.loads(err.getvalue()), {"error": "invalid_input"})

    def test_human_route_output_shows_the_suggestion_is_not_established(self) -> None:
        """Breaks if the terminal reader sees a tier with no indication of its accuracy."""
        exit_code, out, err = self._invoke(["--human", "route", "--suggest-tier"])

        self.assertEqual(exit_code, 0, err)
        self.assertIn("suggested", out)
        self.assertIn("41.7%", out)

    def test_human_route_output_omits_the_record_for_an_explicit_tier(self) -> None:
        """Breaks if the classifier's record is shown where the classifier did not decide."""
        exit_code, out, err = self._invoke(["--human", "route", "--tier", "low"])

        self.assertEqual(exit_code, 0, err)
        self.assertNotIn("41.7%", out)


if __name__ == "__main__":
    unittest.main()
