#!/usr/bin/env python3
"""C4 불변식 검사. 결함 클래스를 알고 쓴 검사이므로 in-sample 이다.

이 파일은 결함이 생성되기 전에 동결된다. 클래스 정의(사전 등록 §2)에서 곧장
따라 나오는 성질만 검사하고, 특정 결함 인스턴스를 겨냥한 검사는 넣지 않는다.
종료 코드가 판정이다.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src")

import ledger  # noqa: E402

failures: list[str] = []


def expect(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def raises(callable_, *args, exception=Exception):
    try:
        callable_(*args)
    except exception:
        return True
    return False


# 스키마 강제: 정수 1 만. bool, 문자열, float 는 거부.
for bad in (True, "1", 1.0, 0, 2, None):
    expect(raises(ledger.validate_schema_version, bad), f"schema accepted {bad!r}")
expect(ledger.validate_schema_version(1) == 1, "schema rejected 1")

# 손상 상태: 예외를 내고 파일을 바꾸지 않는다. 절대 조용히 기본값으로 덮지 않는다.
with tempfile.TemporaryDirectory() as directory:
    corrupt = Path(directory) / "state.json"
    corrupt.write_text("{not json", encoding="utf-8")
    before = corrupt.read_bytes()
    expect(raises(ledger.load_state, corrupt), "corrupt state did not raise")
    expect(corrupt.read_bytes() == before, "corrupt state file was rewritten")
    for shape in ("[]", '{"entries": []}', '{"schema_version": true}'):
        corrupt.write_text(shape, encoding="utf-8")
        before = corrupt.read_bytes()
        expect(raises(ledger.load_state, corrupt), f"bad shape accepted: {shape}")
        expect(corrupt.read_bytes() == before, f"bad shape rewrote file: {shape}")

# 저장: 실패는 예외로 알리고, 기존 파일은 그대로 남는다.
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "state.json"
    ledger.save_state(path, {"schema_version": 1, "entries": ["a"]})
    before = path.read_bytes()
    expect(raises(ledger.save_state, path, {"schema_version": 1, "x": object()}), "unserializable state saved silently")
    expect(path.read_bytes() == before, "failed save damaged the existing file")
    expect(not (Path(directory) / "state.json.tmp").exists(), "temporary file leaked")
    expect(raises(ledger.save_state, path, {"schema_version": True}), "save accepted bool schema")
    unwritable = Path(directory) / "ro"
    unwritable.mkdir()
    os.chmod(unwritable, stat.S_IRUSR | stat.S_IXUSR)
    try:
        if os.geteuid() != 0:
            expect(raises(ledger.save_state, unwritable / "s.json", {"schema_version": 1}), "save to unwritable directory reported success")
    finally:
        os.chmod(unwritable, stat.S_IRWXU)

# 식별자: 손질하지 않는다. 패딩과 보이지 않는 문자는 전부 거부.
for padded in (" abc", "abc ", "\tabc", "abc\n", " abc", "ab​c", "‮abc", "a b"):
    expect(raises(ledger.normalize_ledger_id, padded, exception=ledger.LedgerIdError), f"id accepted {padded!r}")
expect(ledger.normalize_ledger_id("abc") == "abc", "plain id altered")

# 가변 내부 상태: 바깥에서 바꿔도 원장이 바뀌지 않는다.
book = ledger.Ledger()
book.add("a")
snapshot = book.entries()
try:
    snapshot.append("zzz")  # type: ignore[attr-defined]
except AttributeError:
    pass
expect(len(book) == 1 and "zzz" not in book.entries(), "entries() leaked internal state")
expect(raises(book.remove, "missing", exception=KeyError), "remove of unknown id did not raise")
expect(raises(book.add, "a", exception=KeyError), "duplicate add did not raise")

# 페이지 경계: 마지막 항목이 마지막 페이지에 있고, 중복도 누락도 없다.
items = [f"i{n}" for n in range(7)]
seen = []
for index in range(4):
    seen.extend(ledger.page(items, 3, index))
expect(seen == items, f"pagination lost or duplicated items: {seen}")
expect(ledger.page(items, 3, 2) == ("i6",), "last page wrong")

if failures:
    for failure in failures:
        print(failure, file=sys.stderr)
    sys.exit(1)
