"""상태 파일 읽기와 원자적 쓰기."""

from __future__ import annotations

import json
import os
from pathlib import Path

from ledger.schema import validate_schema_version


class StateCorruptError(ValueError):
    """상태 파일이 JSON 이 아니거나 기대한 모양이 아니다."""


def load_state(path: Path) -> dict[str, object]:
    """상태 파일을 읽는다. 손상되었으면 예외를 내고 파일은 건드리지 않는다.

    손상을 조용히 기본값으로 바꿔 쓰면 사용자는 데이터가 사라진 것을 모른다.
    복구는 호출자의 결정이어야 하므로 여기서는 실패만 알린다.
    """
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise StateCorruptError(f"unreadable state file: {path.name}") from error
    if not isinstance(loaded, dict) or "schema_version" not in loaded:
        raise StateCorruptError(f"state file has no schema_version: {path.name}")
    validate_schema_version(loaded["schema_version"])
    return dict(loaded)


def save_state(path: Path, state: dict[str, object]) -> None:
    """상태를 임시 파일에 쓴 뒤 이름을 바꿔 원자적으로 교체한다.

    직렬화나 쓰기가 중간에 실패해도 기존 파일은 그대로 남아야 한다. 실패는
    예외로 알리고 절대 성공으로 보고하지 않는다.
    """
    validate_schema_version(state.get("schema_version"))
    serialized = json.dumps(state, sort_keys=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(serialized, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
