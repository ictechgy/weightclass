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

import sys
import unittest


class LeafRecorder(unittest.TestResult):
    """메서드와 subTest 를 같은 층위의 "잎" 으로 기록한다."""

    def __init__(self) -> None:
        """잎 목록과, subTest 를 가진 메서드 이름을 따로 모은다."""
        super().__init__()
        self.leaves: list[tuple[str, bool]] = []
        self.parents_with_subtests: set[str] = set()

    def addSubTest(self, test, subtest, outcome) -> None:  # noqa: N802
        """subTest 한 건을 잎으로 기록한다. outcome 이 None 이면 통과다."""
        super().addSubTest(test, subtest, outcome)
        self.parents_with_subtests.add(test.id())
        self.leaves.append((subtest.id(), outcome is None))

    def addSuccess(self, test) -> None:  # noqa: N802
        """subTest 가 없는 메서드만 잎이 된다."""
        super().addSuccess(test)
        self.leaves.append((test.id(), True))

    def addFailure(self, test, err) -> None:  # noqa: N802
        """실패한 메서드를 잎으로 기록한다."""
        super().addFailure(test, err)
        self.leaves.append((test.id(), False))

    def addError(self, test, err) -> None:  # noqa: N802
        """오류를 통과로 세면 안 된다. 실패한 잎으로 기록한다."""
        super().addError(test, err)
        self.leaves.append((test.id(), False))


def main() -> int:
    """잎마다 `OK <id>` 또는 `NG <id>` 를 한 줄씩 낸다."""
    suite = unittest.TestLoader().discover("tests", pattern="{pattern}")
    result = LeafRecorder()
    suite.run(result)
    for identifier, ok in result.leaves:
        if identifier in result.parents_with_subtests:
            continue
        print(f"{'OK' if ok else 'NG'} {identifier}")
    if not result.leaves:
        print("수집한 테스트가 없다.", file=sys.stderr)
        return 1
    return 0


raise SystemExit(main())
'''


def main() -> int:
    """항등 리댁션에서도 통과하는 테스트 이름을 출력한다."""
    work = pathlib.Path(tempfile.mkdtemp(prefix="vacuity-"))
    try:
        shutil.copytree(REPO / "tools", work / "tools")
        shutil.copytree(REPO / "tests", work / "tests")
        runner = work / "tools" / "speculative_run.py"
        source = runner.read_text(encoding="utf-8")
        for signature, body in NEUTRALISED:
            if signature not in source:
                print(f"경고: {signature!r} 를 찾지 못했다. 점검이 불완전하다.", file=sys.stderr)
                continue
            source = source.replace(signature, f"{signature}\n{body}  # 공허성 점검", 1)
        runner.write_text(source, encoding="utf-8")
        (work / "run_leaves.py").write_text(
            RUNNER_SOURCE.replace("{pattern}", PATTERN), encoding="utf-8"
        )
        return report(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def leaf_name(identifier: str) -> str:
    """`모듈.클래스.메서드 [파라미터]` 에서 사람이 읽을 부분만 남긴다."""
    head, _, params = identifier.partition(" ")
    method = head.rsplit(".", 1)[-1]
    return f"{method} {params}".strip()


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

    def method_of(identifier: str) -> str:
        """파라미터를 뗀 메서드 이름."""
        return identifier.partition(" ")[0]

    partial = sorted({method_of(name) for name in survivors} & {method_of(n) for n in failed})
    if partial:
        print("\n**부분적으로 공허한 테스트** — 같은 이름의 다른 파라미터는 실패했다:")
        for name in partial:
            print(f"    {name.rsplit('.', 1)[-1]}")
            for node in (s for s in survivors if method_of(s) == name):
                print(f"      통과: {leaf_name(node)}")
    print("\n항등 리댁션에서도 통과한 것 — 각각 어느 방향을 보는지 확인하라:")
    print("  (보존 테스트와 봉투 파싱 단위 테스트는 여기 나오는 것이 정상이다)")
    for node in survivors:
        print(f"    {leaf_name(node)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
