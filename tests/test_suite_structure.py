"""테스트 모듈이 릴리스 게이트에서 실제로 실행되는지 검사한다.

릴리스 워크플로는 `python -m unittest discover -s tests` 로 게이트를 건다.
그 환경에는 `requirements/release.txt` 만 설치되고 pytest 는 없다. CI 는
pytest 로 돌기 때문에, pytest 에서만 도는 모듈은 CI 를 통과하면서 릴리스
게이트에서는 **조용히 0 개를 돌린다.** 실제로 그렇게 된 적이 있다:
`tests/test_speculative_redaction.py` 가 모듈 레벨 함수와 `parametrize` 로
쓰여 있어, 릴리스 환경에서는 import 부터 실패했다.

여기서 검사하는 두 가지는 그 사고의 두 원인이다.

- 모듈 레벨 `test_*` 함수는 unittest 가 **수집하지 않는다.** 실패하지 않고
  사라지므로, 눈으로는 초록색과 구분되지 않는다.
- pytest import 는 릴리스 환경에서 모듈 전체를 import 실패로 만든다.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

# unittest discover 의 기본 패턴. 이 패턴에 걸리는 파일만 게이트가 본다.
DISCOVER_PATTERN = "test*.py"

BANNED_TOP_LEVEL_IMPORTS = frozenset({"pytest"})


def discovered_modules() -> list[Path]:
    """릴리스 게이트가 수집하는 테스트 모듈 목록."""
    return sorted(TESTS_DIR.glob(DISCOVER_PATTERN))


def top_level_test_functions(tree: ast.Module) -> list[str]:
    """unittest 가 수집하지 못하는 모듈 레벨 테스트 함수 이름들."""
    return [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test")
    ]


def imported_roots(tree: ast.Module) -> set[str]:
    """모듈이 최상위에서 import 하는 최상위 패키지 이름들."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


class SuiteStructureTests(unittest.TestCase):
    """릴리스 게이트가 모든 테스트를 실제로 실행하는지 확인한다."""

    def test_discovery_finds_the_suite(self) -> None:
        """검사 자체가 공허해지지 않도록, 수집된 모듈이 있는지부터 본다."""
        self.assertGreater(len(discovered_modules()), 1)

    def test_no_module_level_test_functions(self) -> None:
        """모듈 레벨 test 함수는 unittest 가 조용히 건너뛴다.

        pytest 로만 돌리면 통과처럼 보이지만 릴리스 게이트에서는 그 파일의
        검사가 통째로 사라진다. TestCase 메서드로 써야 한다.
        """
        for path in discovered_modules():
            with self.subTest(module=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                self.assertEqual(top_level_test_functions(tree), [])

    def test_no_test_framework_dependency(self) -> None:
        """릴리스 환경에는 pytest 가 없다. import 하면 모듈 전체가 죽는다."""
        for path in discovered_modules():
            with self.subTest(module=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                banned = imported_roots(tree) & BANNED_TOP_LEVEL_IMPORTS
                self.assertEqual(banned, set())

    def test_every_module_defines_a_test_case(self) -> None:
        """수집 대상 모듈은 TestCase 를 하나 이상 정의해야 한다.

        정의가 없으면 그 파일은 게이트에 아무것도 싣지 않는다.
        """
        for path in discovered_modules():
            with self.subTest(module=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
                self.assertNotEqual(classes, [])


if __name__ == "__main__":
    unittest.main()
