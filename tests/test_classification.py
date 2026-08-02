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


if __name__ == "__main__":
    unittest.main()
