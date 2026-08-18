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
TESTS = "tests/test_speculative_redaction.py"
# 항등으로 바꿀 함수들과 그 자리에 넣을 본문.
NEUTRALISED = (
    ("def verify_excerpt(output: str) -> str:", "    return output"),
    ("def redact_text(text: str) -> str:", "    return text"),
    ("def redact_private_keys(text: str) -> str:", "    return text"),
    ("def join_streams(out: str, err: str) -> str:", "    return (out + err).strip()"),
)


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
        return report(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def report(work: pathlib.Path) -> int:
    """항등 사본에서 테스트를 돌리고 살아남은 **노드 ID** 를 낸다.

    파라미터 ID(`[4]`, `[8]`)를 지우면 안 된다. 한 파라미터가 실패하면 그
    이름 전체가 "검사된 것" 으로 보이지만, 나머지 파라미터는 프로브가 입력에
    없어 공허하게 통과하고 있을 수 있다 — 라운드 24 의 결함이 정확히 그
    형태였고, 이 도구의 첫 판이 같은 실수를 했다.
    """
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", TESTS],
        capture_output=True,
        text=True,
        cwd=work,
    )
    failed = {
        line.split(" ", 1)[1].strip()
        for line in run.stdout.splitlines()
        if line.startswith("FAILED ")
    }
    collected = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
            "--collect-only",
            TESTS,
        ],
        capture_output=True,
        text=True,
        cwd=work,
    )
    every = {line.strip() for line in collected.stdout.splitlines() if "::" in line}
    if not every:
        print("테스트를 수집하지 못했다.", file=sys.stderr)
        return 1
    survivors = sorted(every - failed)
    print(
        f"리댁션을 항등으로 바꿨을 때:"
        f" {len(every) - len(survivors)}개 실패, {len(survivors)}개 통과"
    )
    partial = sorted(
        {name.split("[")[0] for name in survivors} & {name.split("[")[0] for name in failed}
    )
    if partial:
        print("\n**부분적으로 공허한 테스트** — 같은 이름의 다른 파라미터는 실패했다:")
        for name in partial:
            cases = sorted(node for node in survivors if node.split("[")[0] == name)
            print(f"    {name}")
            for node in cases:
                print(f"      통과: {node.split('::')[-1]}")
    print("\n항등 리댁션에서도 통과한 것 — 각각 어느 방향을 보는지 확인하라:")
    print("  (보존 테스트와 봉투 파싱 단위 테스트는 여기 나오는 것이 정상이다)")
    for node in survivors:
        print(f"    {node.split('::')[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
