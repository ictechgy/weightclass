from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "check_test_vacuity.py"


def load_tool() -> types.ModuleType:
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
            any(not ok and identifier.endswith("test_subtest_then_parent_error") for identifier, ok in leaves)
        )
        self.assertTrue(any(not ok and identifier.endswith("test_skip") for identifier, ok in leaves))


if __name__ == "__main__":
    unittest.main()
