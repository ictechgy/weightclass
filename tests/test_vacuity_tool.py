from __future__ import annotations

import importlib.util
import io
import sys
import types
import unittest
from contextlib import redirect_stderr
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "check_test_vacuity.py"


def load_tool() -> types.ModuleType:
    if not TOOL.is_file():
        raise unittest.SkipTest("repository-only vacuity tool unavailable")
    spec = importlib.util.spec_from_file_location("check_test_vacuity", TOOL)
    if spec is None or spec.loader is None:
        raise unittest.SkipTest("vacuity tool unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_test_vacuity"] = module
    spec.loader.exec_module(module)
    return module


class VacuityLeafRecorderTests(unittest.TestCase):
    def test_every_outcome_remains_visible_as_a_leaf(self) -> None:
        """Breaks if subtest parents or skipped methods disappear from the audit."""
        tool = load_tool()

        class RecorderFixture(unittest.TestCase):
            def test_mixed_subtests(self) -> None:
                for value in (1, 2):
                    with self.subTest(value=value):
                        self.assertEqual(value, 1)

            def test_subtest_then_parent_error(self) -> None:
                with self.subTest(value=1):
                    self.assertTrue(True)
                raise RuntimeError("parent failure")

            @unittest.skip("a skipped test checked nothing")
            def test_skip(self) -> None:
                self.fail("unreachable")

        suite = unittest.defaultTestLoader.loadTestsFromTestCase(RecorderFixture)
        result = tool.LeafRecorder()

        suite.run(result)
        leaves = tool.reported_leaves(result)

        self.assertEqual(len(leaves), 5)
        self.assertEqual(sum(ok for _, ok in leaves), 2)
        self.assertEqual(sum(not ok for _, ok in leaves), 3)
        self.assertEqual(result.missing_test_ids(), set())
        self.assertTrue(
            any(
                not ok and identifier.endswith("test_subtest_then_parent_error")
                for identifier, ok in leaves
            )
        )
        self.assertTrue(
            any(not ok and identifier.endswith("test_skip") for identifier, ok in leaves)
        )

    def test_subtest_ids_are_opaque_and_include_skips(self) -> None:
        tool = load_tool()

        class RecorderFixture(unittest.TestCase):
            def test_subtests(self) -> None:
                with self.subTest(secret_parameter="must-not-leak"):
                    self.skipTest("skipped subtest")
                with self.subTest(secret_parameter="also-must-not-leak"):
                    self.assertTrue(True)

        result = tool.LeafRecorder()
        unittest.defaultTestLoader.loadTestsFromTestCase(RecorderFixture).run(result)

        leaves = tool.reported_leaves(result)
        identifiers = [identifier for identifier, _ in leaves]
        self.assertEqual(
            [identifier.rsplit("#", 1)[-1] for identifier in identifiers],
            ["subtest-1", "subtest-2"],
        )
        parent_id = identifiers[0].rsplit("#", 1)[0]
        self.assertTrue(all(identifier.startswith(parent_id) for identifier in identifiers))
        self.assertEqual(
            [tool.leaf_name(identifier) for identifier in identifiers],
            ["test_subtests [subtest 1]", "test_subtests [subtest 2]"],
        )
        self.assertTrue(
            all(tool.parent_test_id(identifier) == parent_id for identifier in identifiers)
        )
        self.assertEqual([ok for _, ok in leaves], [False, True])
        self.assertTrue(all("must-not-leak" not in identifier for identifier in identifiers))

    def test_expected_failure_is_ng_and_unexpected_success_is_ok(self) -> None:
        tool = load_tool()

        class RecorderFixture(unittest.TestCase):
            @unittest.expectedFailure
            def test_expected_failure(self) -> None:
                self.fail("expected")

            @unittest.expectedFailure
            def test_unexpected_success(self) -> None:
                pass

        result = tool.LeafRecorder()
        unittest.defaultTestLoader.loadTestsFromTestCase(RecorderFixture).run(result)

        outcomes = {
            identifier.rsplit(".", 1)[-1]: ok for identifier, ok in tool.reported_leaves(result)
        }
        self.assertEqual(
            outcomes,
            {"test_expected_failure": False, "test_unexpected_success": True},
        )

    def test_neutralization_requires_exactly_one_target(self) -> None:
        tool = load_tool()
        source = "\n".join(signature for signature, _ in tool.NEUTRALISED)

        missing_stderr = io.StringIO()
        with redirect_stderr(missing_stderr):
            self.assertIsNone(tool.neutralize_source(source.replace(tool.NEUTRALISED[0][0], "")))
        self.assertIn("정확히 1개 필요", missing_stderr.getvalue())

        duplicate_stderr = io.StringIO()
        with redirect_stderr(duplicate_stderr):
            self.assertIsNone(tool.neutralize_source(source + "\n" + tool.NEUTRALISED[0][0]))
        self.assertIn("정확히 1개 필요", duplicate_stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
