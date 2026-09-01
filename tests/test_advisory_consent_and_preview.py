"""세션 스코프 송신 동의와 사람이 읽는 미리보기 출력의 계약.

동의는 디스크에 남기지 않는다. `AGENTS.md` 가 advisory 상태에 저장소 경로와
시각을 금지하므로, 저장소·시각 단위 grant 는 이 프로젝트에서 선택지가 아니다.
셸 세션의 수명에만 두면 아무것도 쓰지 않고 같은 마찰을 없앨 수 있다.
"""

from __future__ import annotations

import io
import unittest
from unittest import mock

from weightclass.advisory import advisory_quick


class SessionEgressGrantTests(unittest.TestCase):
    def test_only_the_exact_opt_in_value_grants_the_session(self) -> None:
        for value, granted in (
            ("session", True),
            ("", False),
            ("0", False),
            ("1", False),
            ("Session", False),
            ("session ", False),
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    advisory_quick.session_egress_granted({"WCLASS_ADVISORY_EGRESS": value}),
                    granted,
                )

    def test_absent_variable_does_not_grant(self) -> None:
        self.assertFalse(advisory_quick.session_egress_granted({}))

    def test_session_grant_confirms_without_touching_the_terminal(self) -> None:
        member = {"vendor": "codex", "delivery": "stdin"}
        with (
            mock.patch.dict("os.environ", {"WCLASS_ADVISORY_EGRESS": "session"}, clear=False),
            mock.patch("weightclass.advisory.advisory_quick.os.ctermid") as ctermid,
        ):
            source = advisory_quick._confirm_task_egress(
                members=(member,),
                workflow="review",
                stage="manual",
                context_mode="task",
                context_file_count=0,
                confirmed=False,
            )
        self.assertEqual(source, "session_environment")
        ctermid.assert_not_called()

    def test_explicit_flag_still_reports_its_own_source(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            source = advisory_quick._confirm_task_egress(
                members=({"vendor": "codex", "delivery": "stdin"},),
                workflow="review",
                stage="manual",
                context_mode="task",
                context_file_count=0,
                confirmed=True,
            )
        self.assertEqual(source, "flag")

    def test_without_a_grant_a_non_terminal_run_still_fails_closed(self) -> None:
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            mock.patch("weightclass.advisory.advisory_quick.os.ctermid", None),
            self.assertRaises(advisory_quick.QuickAdvisoryError) as raised,
        ):
            advisory_quick._confirm_task_egress(
                members=({"vendor": "codex", "delivery": "stdin"},),
                workflow="review",
                stage="manual",
                context_mode="task",
                context_file_count=0,
                confirmed=False,
            )
        self.assertEqual(raised.exception.code, "ask_confirmation_required")


class PreviewHumanRenderingTests(unittest.TestCase):
    @staticmethod
    def _render(receipt: dict[str, object]) -> str:
        stream = io.StringIO()
        with mock.patch("sys.stdout", stream):
            advisory_quick._render_human(receipt)
        return stream.getvalue()

    @staticmethod
    def _preview(**overrides: object) -> dict[str, object]:
        receipt: dict[str, object] = {
            "event": "advisory_preview",
            "workflow": "review",
            "stage": "manual",
            "context_mode": "repo",
            "context_file_count": 0,
            "task_bearing_child_bound": 1,
            "task_free_probe_children_on_run": 2,
            "timeout_seconds_per_child": 3600,
            "total_timeout_seconds": None,
            "requested_repository_behavior": "read_only",
            "host_filesystem_confined": False,
            "quality_verified": False,
            "session_egress_grant_active": False,
            "routes": [
                {
                    "vendor": "agy",
                    "command": ["agy", "--sandbox", "--json-schema", "x" * 4096],
                    "task_delivery": "stdin",
                    "task_process_exposure": False,
                    "model_selection": "vendor_default",
                }
            ],
        }
        receipt.update(overrides)
        return receipt

    def test_long_arguments_are_summarized_instead_of_flooding_the_screen(self) -> None:
        rendered = self._render(self._preview())
        self.assertNotIn("x" * 200, rendered)
        self.assertIn("<4096 bytes, sha256:", rendered)
        # 짧은 플래그는 그대로 남아야 검토가 가능하다.
        self.assertIn("--sandbox", rendered)
        self.assertLess(len(rendered), 2000, "미리보기는 한 화면에 들어와야 한다")

    def test_delivery_and_exposure_are_stated_in_words(self) -> None:
        rendered = self._render(self._preview())
        self.assertIn("agy — stdin delivery, no argv exposure", rendered)

    def test_argv_exposure_is_named_when_a_route_still_has_it(self) -> None:
        preview = self._preview()
        routes = preview["routes"]
        assert isinstance(routes, list)
        routes[0]["task_delivery"] = "argv"
        routes[0]["task_process_exposure"] = True
        rendered = self._render(preview)
        self.assertIn("task visible in local process arguments", rendered)

    def test_consent_state_is_disclosed_both_ways(self) -> None:
        self.assertIn("this run will ask on the terminal", self._render(self._preview()))
        self.assertIn(
            "granted for this shell",
            self._render(self._preview(session_egress_grant_active=True)),
        )

    def test_preview_says_nothing_was_read_or_started(self) -> None:
        rendered = self._render(self._preview())
        self.assertIn("No task was read and no vendor process was started.", rendered)
        self.assertIn("--json", rendered)


if __name__ == "__main__":
    unittest.main()
