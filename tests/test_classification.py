import unittest

from sar.classification import (
    HIGH_SIGNALS,
    HIGH_TASK_CHARACTERS,
    LOW_TASK_CHARACTERS,
    MAX_TASK_CHARACTERS,
    InvalidTaskError,
    classify_task,
)


class SignalInflectionTests(unittest.TestCase):
    """굴절형이 시그널에서 빠져나가면 위험한 작업이 싼 티어로 떨어진다."""

    def test_every_ascii_high_signal_survives_its_plural(self) -> None:
        for signal in sorted(HIGH_SIGNALS):
            if not signal.isascii():
                continue
            with self.subTest(signal=signal):
                self.assertEqual(classify_task(f"Please review the {signal}s here."), "high")

    def test_an_inflected_high_signal_outranks_an_inflected_low_signal(self) -> None:
        cases = (
            "Reformatting the credentials file used by the deploy job.",
            "Renaming the payments table columns.",
            "Renaming the columns in the pending migrations.",
            "Fix the formatting of the authorizations audit log.",
        )

        for task in cases:
            with self.subTest(task=task):
                self.assertEqual(classify_task(task), "high")

    def test_a_hyphenated_multi_word_high_signal_still_outranks_a_low_signal(self) -> None:
        """Breaks if a multi-word signal only matches its space-separated spelling."""
        cases = (
            "Reformat the data-loss recovery procedure.",
            "Rename the race-condition guard.",
            "Fix the data loss recovery procedure formatting.",
        )

        for task in cases:
            with self.subTest(task=task):
                self.assertEqual(classify_task(task), "high")

    def test_common_inflections_of_high_signals_stay_high(self) -> None:
        cases = (
            "Rotate the credentials after a suspected leak.",
            "Review the payments flow.",
            "Refactoring this module.",
            "Fix race conditions in the worker pool.",
            "Review the pending migrations.",
            "Compare the two architectures.",
        )

        for task in cases:
            with self.subTest(task=task):
                self.assertEqual(classify_task(task), "high")


class ThresholdTests(unittest.TestCase):
    """길이 임계값은 문서화된 계약이므로 경계 양쪽을 모두 고정한다."""

    def test_a_low_signal_stops_reaching_the_low_tier_past_its_length_limit(self) -> None:
        prefix = "fix typo "
        at_limit = prefix + "x" * (LOW_TASK_CHARACTERS - len(prefix))
        past_limit = prefix + "x" * (LOW_TASK_CHARACTERS - len(prefix) + 1)

        self.assertEqual(len(at_limit), LOW_TASK_CHARACTERS)
        self.assertEqual(classify_task(at_limit), "low")
        self.assertEqual(classify_task(past_limit), "standard")

    def test_length_alone_escalates_a_signal_free_task_to_high(self) -> None:
        below_limit = "x" * (HIGH_TASK_CHARACTERS - 1)
        at_limit = "x" * HIGH_TASK_CHARACTERS

        self.assertEqual(classify_task(below_limit), "standard")
        self.assertEqual(classify_task(at_limit), "high")

    def test_rejects_a_task_past_the_maximum_character_limit(self) -> None:
        self.assertEqual(classify_task("x" * MAX_TASK_CHARACTERS), "high")
        with self.assertRaises(InvalidTaskError):
            classify_task("x" * (MAX_TASK_CHARACTERS + 1))


class ClassificationRegressionTests(unittest.TestCase):
    def test_classifies_operational_privacy_and_credential_tasks_as_high(self) -> None:
        cases = {
            "Plan a rollback after the failed deployment.": "high",
            "Review the privacy implications for customer records.": "high",
            "Rotate the service credential after a suspected leak.": "high",
            "배포 롤백 절차를 검토해줘.": "high",
            "개인정보 처리 범위를 검토해줘.": "high",
        }

        for task, expected_tier in cases.items():
            with self.subTest(task=task):
                self.assertEqual(classify_task(task), expected_tier)

    def test_classifies_short_nonfunctional_edits_as_low(self) -> None:
        cases = {
            "Normalize whitespace in this README.": "low",
            "README 제목의 문장부호만 고쳐줘.": "low",
        }

        for task, expected_tier in cases.items():
            with self.subTest(task=task):
                self.assertEqual(classify_task(task), expected_tier)

    def test_does_not_escalate_on_a_signal_embedded_in_a_longer_word(self) -> None:
        """Breaks if HIGH signals go back to matching without word boundaries."""
        cases = {
            "Write reproduction steps for this crash.": "standard",
            "Update the reproduction script comment.": "standard",
            "Add a focused unit test for this formatter.": "standard",
        }

        for task, expected_tier in cases.items():
            with self.subTest(task=task):
                self.assertEqual(classify_task(task), expected_tier)

    def test_classifies_inflected_nonfunctional_edits_as_low(self) -> None:
        """Breaks if only the bare stem of a LOW signal reaches the cheap tier."""
        cases = {
            "Fix the typos in the changelog.": "low",
            "Fix the formatting of this markdown table.": "low",
            "Reformat this file.": "low",
            "Renaming the variable userId to accountId.": "low",
            "Strip trailing whitespaces from the file.": "low",
        }

        for task, expected_tier in cases.items():
            with self.subTest(task=task):
                self.assertEqual(classify_task(task), expected_tier)

    def test_classifies_common_korean_spellings_consistently(self) -> None:
        """Breaks if a Korean transliteration variant misses its tier."""
        cases = {
            "이 함수를 리팩토링 해줘.": "high",
            "이 함수를 리팩터링 해줘.": "high",
            "변수 이름변경 해줘.": "low",
            "변수명 리네임 해줘.": "low",
            "코드 포매팅만 정리해줘.": "low",
            "본문 띄어쓰기만 고쳐줘.": "low",
        }

        for task, expected_tier in cases.items():
            with self.subTest(task=task):
                self.assertEqual(classify_task(task), expected_tier)

    def test_lets_a_high_signal_win_over_a_low_signal(self) -> None:
        """Breaks if the conservative high-wins precedence is reversed."""
        cases = {
            "Fix a typo in the authorization error message.": "high",
            "결제 화면 문구 오타 수정.": "high",
        }

        for task, expected_tier in cases.items():
            with self.subTest(task=task):
                self.assertEqual(classify_task(task), expected_tier)


if __name__ == "__main__":
    unittest.main()
