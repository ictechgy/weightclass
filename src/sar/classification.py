"""Deterministic, local task classification without persistence or logging."""

import re
import sys
from typing import Final, Literal


Tier = Literal["low", "standard", "high"]

HIGH_SIGNALS: Final = frozenset(
    {
        "architecture",
        "authentication",
        "authorization",
        "concurrency",
        "credential",
        "database",
        "data loss",
        "deployment",
        "migration",
        "payment",
        "performance",
        "privacy",
        "production",
        "race condition",
        "refactor",
        "rollback",
        "security",
        "개인정보",
        "권한",
        "데이터베이스",
        "동시성",
        "배포",
        "리팩터링",
        "롤백",
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
        "punctuation",
        "rename",
        "spelling",
        "typo",
        "whitespace",
        "오타",
        "이름 변경",
        "문장부호",
        "포맷",
    }
)
MAX_TASK_CHARACTERS: Final = 20_000
HIGH_TASK_CHARACTERS: Final = 1_200
LOW_TASK_CHARACTERS: Final = 240
# UTF-8 한 문자는 최대 4바이트이므로, 이 상한은 문자 상한을 통과할 수 있는 모든
# 입력을 포함한다. 바이트 상한이 문자 상한보다 먼저 걸려 거부하는 일은 없다.
MAX_TASK_BYTES: Final = MAX_TASK_CHARACTERS * 4


class InvalidTaskError(ValueError):
    """Raised for task input that cannot be classified safely."""


def read_task_from_standard_input() -> str:
    """Read a bounded task as UTF-8 bytes, independent of the process locale.

    표준 입력을 텍스트 모드로 읽으면 로케일 인코딩이 적용되어 LC_ALL=C 환경에서
    비ASCII 태스크가 surrogate로 깨지고, 그 실패가 진단 메시지로 새어 나간다.
    바이트로 읽어 UTF-8로 엄격히 디코딩하고, 상한 초과 입력은 전체를 메모리에
    올리기 전에 거부한다.
    """
    try:
        task_bytes = sys.stdin.buffer.read(MAX_TASK_BYTES + 1)
        if len(task_bytes) > MAX_TASK_BYTES:
            raise InvalidTaskError()
        return task_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise InvalidTaskError() from error


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
