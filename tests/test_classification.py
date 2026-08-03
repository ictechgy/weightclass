import unittest

from sar.classification import classify_task


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
