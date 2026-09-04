"""메모리 원장. 내부 상태는 복사본으로만 내보낸다."""

from __future__ import annotations

from ledger.ids import normalize_ledger_id


class Ledger:
    """식별자 순서를 보존하는 단순 원장."""

    def __init__(self) -> None:
        self._entries: list[str] = []

    def add(self, raw_id: str) -> None:
        """식별자를 검증해 추가한다. 중복은 무시하지 않고 거부한다."""
        ledger_id = normalize_ledger_id(raw_id)
        if ledger_id in self._entries:
            raise KeyError(f"duplicate ledger id: {ledger_id}")
        self._entries.append(ledger_id)

    def remove(self, raw_id: str) -> None:
        """식별자를 제거한다. 없으면 KeyError 다. 조용히 넘어가지 않는다."""
        ledger_id = normalize_ledger_id(raw_id)
        if ledger_id not in self._entries:
            raise KeyError(f"unknown ledger id: {ledger_id}")
        self._entries.remove(ledger_id)

    def entries(self) -> tuple[str, ...]:
        """현재 항목의 불변 스냅샷. 내부 리스트를 참조로 내주지 않는다."""
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)
