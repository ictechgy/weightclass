#!/usr/bin/env python3
"""리댁션 테스트가 공허하지 않은지 확인한다.

라운드 24 에서 회귀 테스트 두 개가 공허한 것으로 드러났다. 프로브 문자열이
입력에 아예 존재하지 않아, 리댁션이 아무것도 안 해도 통과했다 — 그래서 네
자로 접힌 개인키가 통째로 새는 것을 다섯 라운드 동안 가렸다.

이 스크립트는 그 부류를 기계적으로 잡는다. `speculative_run.py` 의 리댁션
함수들을 **항등 함수로 바꾼 사본** 을 만들고 테스트를 돌린다. 그때도 통과하는
테스트는 리댁션이 하는 일을 검사하지 않는 것이다.

통과가 곧 결함은 아니다. "지워지면 안 되는 것이 남는가" 를 보는 보존 테스트는
당연히 통과한다. 그래서 이 스크립트는 판정하지 않고 **목록만** 낸다 — 각
이름을 보고 그 테스트가 어느 방향을 보는지 사람이 확인해야 한다.

    python3 tools/check_test_vacuity.py
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import TracebackType

ExcInfo = tuple[type[BaseException], BaseException, TracebackType] | tuple[None, None, None]

REPO = pathlib.Path(__file__).resolve().parent.parent
PATTERN = "test_speculative_redaction.py"
# 항등으로 바꿀 함수들과 그 자리에 넣을 본문.
NEUTRALISED = (
    ("def verify_excerpt(output: str) -> str:", "    return output"),
    ("def redact_text(text: str) -> str:", "    return text"),
    ("def redact_private_keys(text: str) -> str:", "    return text"),
    ("def join_streams(out: str, err: str) -> str:", "    return (out + err).strip()"),
)

# 사본 안에서 돌릴 러너. 결과를 **잎 단위** 로 낸다.
#
# subTest 한 건마다 한 줄이다. 메서드 단위로 뭉치면, 한 파라미터가 실패했을
# 때 그 이름 전체가 "검사된 것" 으로 보이지만 나머지 파라미터는 프로브가
# 입력에 없어 공허하게 통과하고 있을 수 있다 — 라운드 24 의 결함이 정확히 그
# 형태였고, 이 도구의 첫 판이 같은 실수를 했다.
RUNNER_SOURCE = '''\
"""항등 리댁션 사본에서 테스트를 돌리고 잎 단위 결과를 낸다."""

from __future__ import annotations

import pathlib
import sys
import unittest

sys_path = str(pathlib.Path(__file__).parent / "tools")
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)
from check_test_vacuity import LeafRecorder, reported_leaves

def main() -> int:
    """잎마다 `OK <id>` 또는 `NG <id>` 를 한 줄씩 낸다."""
    suite = unittest.TestLoader().discover("tests", pattern="{pattern}")
    result = LeafRecorder()
    suite.run(result)
    leaves = reported_leaves(result)
    for identifier, ok in leaves:
        print(f"{'OK' if ok else 'NG'} {identifier}")
    if not leaves:
        print("수집한 테스트가 없다.", file=sys.stderr)
        return 1
    return 0


raise SystemExit(main())
'''


class LeafRecorder(unittest.TestResult):
    """Record every leaf, while dropping only redundant parent successes."""

    def __init__(self) -> None:
        super().__init__()
        self.leaves: list[tuple[str, bool]] = []
        self.collected_test_ids: set[str] = set()
        self.parents_with_subtests: set[str] = set()
        self._subtest_ordinals: dict[str, int] = {}

    def startTest(self, test: unittest.TestCase) -> None:  # noqa: N802
        super().startTest(test)
        self.collected_test_ids.add(test.id())

    def addSubTest(  # noqa: N802
        self,
        test: unittest.TestCase,
        subtest: unittest.TestCase,
        outcome: ExcInfo | None,
    ) -> None:
        super().addSubTest(test, subtest, outcome)
        parent_id = test.id()
        self.parents_with_subtests.add(parent_id)
        self.leaves.append((self._next_subtest_id(parent_id), outcome is None))

    def addSuccess(self, test: unittest.TestCase) -> None:  # noqa: N802
        super().addSuccess(test)
        self.leaves.append((test.id(), True))

    def addFailure(self, test: unittest.TestCase, err: ExcInfo) -> None:  # noqa: N802
        super().addFailure(test, err)
        self.leaves.append((test.id(), False))

    def addError(self, test: unittest.TestCase, err: ExcInfo) -> None:  # noqa: N802
        super().addError(test, err)
        self.leaves.append((test.id(), False))

    def addExpectedFailure(  # noqa: N802
        self, test: unittest.TestCase, err: ExcInfo
    ) -> None:
        super().addExpectedFailure(test, err)
        self.leaves.append((test.id(), False))

    def addUnexpectedSuccess(self, test: unittest.TestCase) -> None:  # noqa: N802
        super().addUnexpectedSuccess(test)
        self.leaves.append((test.id(), True))

    def addSkip(self, test: unittest.TestCase, reason: str) -> None:  # noqa: N802
        super().addSkip(test, reason)
        parent = getattr(test, "test_case", None)
        if parent is not None and hasattr(test, "params"):
            parent_id = parent.id()
            self.parents_with_subtests.add(parent_id)
            self.leaves.append((self._next_subtest_id(parent_id), False))
            return
        self.leaves.append((test.id(), False))

    def _next_subtest_id(self, parent_id: str) -> str:
        ordinal = self._subtest_ordinals.get(parent_id, 0) + 1
        self._subtest_ordinals[parent_id] = ordinal
        return f"{parent_id}#subtest-{ordinal}"

    def missing_test_ids(self) -> set[str]:
        """Return collected tests for which no reported leaf was produced."""
        leaf_ids = {identifier for identifier, _ in self.leaves}
        covered = leaf_ids | self.parents_with_subtests
        return self.collected_test_ids - covered


def reported_leaves(result: LeafRecorder) -> list[tuple[str, bool]]:
    """Return visible leaves and turn every unrepresented method into NG."""
    leaves = [
        (identifier, ok)
        for identifier, ok in result.leaves
        if not (identifier in result.parents_with_subtests and ok)
    ]
    return [*leaves, *((identifier, False) for identifier in sorted(result.missing_test_ids()))]


def main() -> int:
    """항등 리댁션에서도 통과하는 테스트 이름을 출력한다."""
    work = pathlib.Path(tempfile.mkdtemp(prefix="vacuity-"))
    try:
        shutil.copytree(REPO / "tools", work / "tools")
        shutil.copytree(
            REPO / "src" / "weightclass" / "advisory",
            work / "src" / "weightclass" / "advisory",
        )
        shutil.copytree(REPO / "tests", work / "tests")
        runner = work / "src" / "weightclass" / "advisory" / "speculative_run.py"
        source = runner.read_text(encoding="utf-8")
        neutralized = neutralize_source(source)
        if neutralized is None:
            return 1
        runner.write_text(neutralized, encoding="utf-8")
        (work / "run_leaves.py").write_text(
            RUNNER_SOURCE.replace("{pattern}", PATTERN), encoding="utf-8"
        )
        return report(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def neutralize_source(source: str) -> str | None:
    """Replace each reviewed redaction target exactly once, or fail closed."""
    for signature, body in NEUTRALISED:
        occurrences = source.count(signature)
        if occurrences != 1:
            print(
                f"중화 대상 검증 실패: {occurrences}개 일치 (정확히 1개 필요)",
                file=sys.stderr,
            )
            return None
        source = source.replace(signature, f"{signature}\n{body}  # 공허성 점검", 1)
    return source


def leaf_name(identifier: str) -> str:
    """Render a safe method name and optional opaque subtest ordinal."""
    parent, marker, ordinal = identifier.partition("#subtest-")
    method = parent.rsplit(".", 1)[-1]
    return f"{method} [subtest {ordinal}]" if marker else method


def parent_test_id(identifier: str) -> str:
    """Return the method ID shared by every opaque subtest leaf."""
    return identifier.partition("#subtest-")[0]


def report(work: pathlib.Path) -> int:
    """항등 사본에서 테스트를 돌리고 살아남은 **잎** 을 낸다.

    릴리스 게이트와 같은 러너(`unittest`)를 쓴다. 도구가 게이트와 다른
    러너를 쓰면, 게이트에서는 돌지 않는 테스트를 도구가 "검사됐다" 로
    세는 일이 생긴다.
    """
    run = subprocess.run(
        [sys.executable, "run_leaves.py"],
        capture_output=True,
        text=True,
        cwd=work,
    )
    leaves = [line.split(" ", 1) for line in run.stdout.splitlines() if line[:3] in ("OK ", "NG ")]
    if not leaves:
        print("테스트를 수집하지 못했다.", file=sys.stderr)
        print(run.stderr.strip()[-2000:], file=sys.stderr)
        return 1
    if run.returncode != 0:
        print(f"경고: 러너가 코드 {run.returncode} 로 끝났다.", file=sys.stderr)

    survivors = sorted(identifier for status, identifier in leaves if status == "OK")
    failed = {identifier for status, identifier in leaves if status == "NG"}
    print(
        f"리댁션을 항등으로 바꿨을 때: {len(failed)}개 실패, {len(survivors)}개 통과"
        f" (잎 {len(leaves)}개)"
    )

    partial = sorted(
        {parent_test_id(name) for name in survivors} & {parent_test_id(name) for name in failed}
    )
    if partial:
        print("\n**부분적으로 공허한 테스트** — 같은 이름의 다른 파라미터는 실패했다:")
        for name in partial:
            print(f"    {name.rsplit('.', 1)[-1]}")
            for node in (survivor for survivor in survivors if parent_test_id(survivor) == name):
                print(f"      통과: {leaf_name(node)}")
    print("\n항등 리댁션에서도 통과한 것 — 각각 어느 방향을 보는지 확인하라:")
    print("  (보존 테스트와 봉투 파싱 단위 테스트는 여기 나오는 것이 정상이다)")
    for node in survivors:
        print(f"    {leaf_name(node)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
