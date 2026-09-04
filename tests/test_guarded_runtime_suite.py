import importlib
import unittest
from pathlib import Path

from tests.runtime_guard import (
    CLAIMED_LAUNCH_TESTS,
    EXCLUDED_LAUNCH_SCOPES,
    RuntimeGuard,
    register_claimed_launches,
)


class GuardedRuntimeSuite(unittest.TestCase):
    """Named suite: guarded-runtime-top-level-launch-v1."""

    def run_claimed_test(self, category: str) -> None:
        symbol = CLAIMED_LAUNCH_TESTS[category]
        module_name, class_name, method_name = symbol.rsplit(".", 2)
        module = importlib.import_module(module_name)
        test_class = getattr(module, class_name)
        case = test_class(method_name)
        guard = RuntimeGuard()
        register_claimed_launches(guard, category)
        result = unittest.TestResult()
        with guard.activated():
            case.run(result)
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(result.skipped, [])
        self.assertTrue(result.wasSuccessful(), f"guarded claimed launch failed: {category}")
        self.assertGreater(guard.validated_launch_count, 0)

    def test_guarded_native_v1_launch(self) -> None:
        self.run_claimed_test("native_v1")

    def test_guarded_native_v2_launch(self) -> None:
        self.run_claimed_test("native_v2")

    def test_guarded_triage_launch(self) -> None:
        self.run_claimed_test("triage")


class GuardedRuntimeEnrollmentTests(unittest.TestCase):
    def test_every_claimed_launch_path_is_enrolled(self) -> None:
        self.assertEqual(
            set(CLAIMED_LAUNCH_TESTS),
            {"native_v1", "native_v2", "triage"},
        )
        for category, symbol in CLAIMED_LAUNCH_TESTS.items():
            with self.subTest(category=category):
                module_name, class_name, method_name = symbol.rsplit(".", 2)
                module = importlib.import_module(module_name)
                test_class = getattr(module, class_name)
                method = getattr(test_class, method_name)
                self.assertEqual(getattr(method, "__guarded_launch_category__", None), category)

    def test_distribution_process_scopes_are_explicitly_outside_the_claim(self) -> None:
        self.assertEqual(EXCLUDED_LAUNCH_SCOPES, ("build", "packaging", "extracted_sdist"))

    def test_guard_claim_is_scoped_to_the_current_test_process(self) -> None:
        specification = Path("docs/archive/protocol-v2-specification.md").read_text(
            encoding="utf-8"
        )
        security = Path("docs/archive/protocol-v2-security.md").read_text(encoding="utf-8")
        for document in (specification, security):
            with self.subTest(document=document[:32]):
                self.assertIn("current test process", document)
                self.assertIn("does not instrument child processes", document)


if __name__ == "__main__":
    unittest.main()
