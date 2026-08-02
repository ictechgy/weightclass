"""Deterministic, local task classification without persistence or logging."""

import re
from typing import Final, Literal


Tier = Literal["low", "standard", "high"]

HIGH_SIGNALS: Final = frozenset(
    {
        "architecture",
        "authentication",
        "authorization",
        "concurrency",
        "database",
        "data loss",
        "migration",
        "payment",
        "performance",
        "production",
        "race condition",
        "refactor",
        "security",
        "권한",
        "데이터베이스",
        "동시성",
        "리팩터링",
        "마이그레이션",
        "보안",
        "성능",
        "아키텍처",
        "인증",
        "결제",
    }
)
LOW_SIGNALS: Final = frozenset(
    {
        "format",
        "rename",
        "spelling",
        "typo",
        "오타",
        "이름 변경",
        "포맷",
    }
)
MAX_TASK_CHARACTERS: Final = 20_000
HIGH_TASK_CHARACTERS: Final = 1_200
LOW_TASK_CHARACTERS: Final = 240


class InvalidTaskError(ValueError):
    """Raised for task input that cannot be classified safely."""


def classify_task(task: str) -> Tier:
    """Classify a transient task conservatively using documented local rules."""
    normalized_task = task.strip().casefold()
    if not normalized_task or len(normalized_task) > MAX_TASK_CHARACTERS:
        raise InvalidTaskError()
    if len(normalized_task) >= HIGH_TASK_CHARACTERS or any(
        signal in normalized_task for signal in HIGH_SIGNALS
    ):
        return "high"
    if len(normalized_task) <= LOW_TASK_CHARACTERS and _has_low_signal(normalized_task):
        return "low"
    return "standard"


def _has_low_signal(task: str) -> bool:
    for signal in LOW_SIGNALS:
        if signal.isascii():
            if re.search(rf"\b{re.escape(signal)}\b", task):
                return True
        elif signal in task:
            return True
    return False
