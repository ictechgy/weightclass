"""agy 라우트가 태스크를 argv 가 아니라 stdin 의 NDJSON 봉투로 넘기는지 본다.

이 계약이 깨지면 태스크 본문이 다시 로컬 프로세스 인자에 노출된다. 그것은
`ps` 를 읽을 수 있는 같은 사용자의 모든 프로세스에 보인다는 뜻이므로, 라우트
모양과 전달 인코딩을 함께 고정한다.
"""

from __future__ import annotations

import json
import unittest

from weightclass.advisory import advisory_routes, speculative_run


class StreamJsonInputDeclarationTests(unittest.TestCase):
    def test_declared_stream_json_input_is_recognized_in_both_flag_forms(self) -> None:
        for command in (
            ["agy", "--input-format", "stream-json"],
            ["agy", "--input-format=stream-json"],
        ):
            with self.subTest(command=command):
                self.assertTrue(speculative_run.declares_stream_json_input(command))

    def test_other_input_formats_and_flag_values_do_not_declare_it(self) -> None:
        for command in (
            ["agy"],
            ["agy", "--input-format", "text"],
            ["agy", "--input-format=text"],
            # 다른 플래그의 **값** 안에 있는 같은 글자로 봉투가 켜지면, 봉투가
            # 필요 없는 라우트의 stdin 이 JSON 으로 감싸여 나간다.
            ["agy", "--rules", "--input-format stream-json"],
            ["agy", "--input-format"],
        ):
            with self.subTest(command=command):
                self.assertFalse(speculative_run.declares_stream_json_input(command))


class StreamJsonTaskEncodingTests(unittest.TestCase):
    def test_encoded_task_is_one_ndjson_user_message_line(self) -> None:
        task = '첫 줄\n둘째 줄\ttab "quoted" \\ backslash'
        encoded = speculative_run.encode_stream_json_task(task)
        self.assertTrue(encoded.endswith("\n"))
        self.assertEqual(encoded.count("\n"), 1, "봉투는 정확히 한 줄이어야 한다")
        payload = json.loads(encoded)
        self.assertEqual(payload["event"], "user")
        self.assertEqual(payload["message"]["role"], "user")
        self.assertEqual(payload["message"]["content"], [{"type": "text", "text": task}])

    def test_empty_task_still_produces_a_valid_envelope(self) -> None:
        payload = json.loads(speculative_run.encode_stream_json_task(""))
        self.assertEqual(payload["message"]["content"], [{"type": "text", "text": ""}])


class PreparedTaskDeliveryTests(unittest.TestCase):
    def test_stream_json_route_wraps_the_stdin_payload(self) -> None:
        command = ["agy", "--input-format", "stream-json", "--output-format", "stream-json"]
        prepared, delivered, pipe = speculative_run._prepare_task_command(command, "review this")
        self.assertEqual(prepared, command, "명령은 감싸기 때문에 바뀌지 않는다")
        self.assertIsNone(pipe)
        self.assertEqual(
            json.loads(delivered)["message"]["content"][0]["text"],
            "review this",
        )

    def test_plain_stdin_route_delivers_the_task_unchanged(self) -> None:
        command = ["codex", "exec", "--json", "-"]
        prepared, delivered, pipe = speculative_run._prepare_task_command(command, "review this")
        self.assertEqual(prepared, command)
        self.assertIsNone(pipe)
        self.assertEqual(delivered, "review this")


class ContradictoryDeliveryTests(unittest.TestCase):
    def test_a_task_slot_plus_declared_ndjson_stdin_fails_closed(self) -> None:
        # 두 곳으로 보내겠다는 라우트에서 한쪽을 골라 주면, 검토한 사람이 본 것과
        # 다른 전달이 조용히 일어난다.
        for command in (
            ["agy", "--input-format", "stream-json", "--print", "{{task}}"],
            ["agy", "--input-format", "stream-json", "--prompt-file", "{{task_file}}"],
        ):
            with self.subTest(command=command):
                with self.assertRaises(speculative_run.RunFailure):
                    speculative_run._prepare_task_command(command, "review this")


class AgyRouteShapeTests(unittest.TestCase):
    def test_default_evidence_route_carries_no_task_in_argv(self) -> None:
        for workflow in ("review", "research", "diagnosis", "design"):
            with self.subTest(workflow=workflow):
                route = advisory_routes.build_default_evidence_route("agy", workflow)
                self.assertEqual(advisory_routes.command_task_delivery(route), "stdin")
                self.assertNotIn(advisory_routes.TASK_PLACEHOLDER, route)
                self.assertNotIn("--print", route)
                self.assertTrue(speculative_run.declares_stream_json_input(list(route)))

    def test_every_default_evidence_route_keeps_the_task_out_of_argv(self) -> None:
        # grok 은 상속된 파이프, 나머지는 stdin 이다. argv 전달이 남아 있으면
        # 그 벤더의 태스크가 로컬 프로세스 목록에 보인다.
        for vendor in ("claude", "codex", "agy", "grok"):
            with self.subTest(vendor=vendor):
                route = advisory_routes.build_default_evidence_route(vendor, "review")
                self.assertIn(
                    advisory_routes.command_task_delivery(route),
                    {"stdin", "file"},
                )


if __name__ == "__main__":
    unittest.main()
