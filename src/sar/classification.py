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
        "리팩토링",
    }
)
LOW_SIGNALS: Final = frozenset(
    {
        "format",
        "formats",
        "formatting",
        "punctuation",
        "reformat",
        "reformatting",
        "rename",
        "renames",
        "renaming",
        "spelling",
        "typo",
        "typos",
        "whitespace",
        "whitespaces",
        "오타",
        "이름 변경",
        "이름변경",
        "리네임",
        "문장부호",
        "포맷",
        "포매팅",
        "띄어쓰기",
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


def _compile_ascii_signals(signals: frozenset[str]) -> re.Pattern[str]:
    """Compile the ASCII signals of one tier into a single word-boundary pattern."""
    ascii_signals = sorted(
        (signal for signal in signals if signal.isascii()), key=len, reverse=True
    )
    alternation = "|".join(re.escape(signal) for signal in ascii_signals)
    return re.compile(rf"\b(?:{alternation})\b")


def _select_non_ascii_signals(signals: frozenset[str]) -> frozenset[str]:
    """Return the signals that must be matched by containment rather than by word."""
    return frozenset(signal for signal in signals if not signal.isascii())


# 한국어는 조사와 합성어 때문에 단어 경계 개념이 없어 부분 문자열로 검사한다.
# 그 결과 "저작권한도"처럼 시그널을 품은 합성어가 상위 티어로 오분류될 수 있다.
# 상위 티어로의 오분류는 보수적인 방향이므로, 형태소 분석 의존성을 들이는 대신
# 이 한계를 문서화하고 감수한다.
_HIGH_ASCII_PATTERN: Final = _compile_ascii_signals(HIGH_SIGNALS)
_HIGH_NON_ASCII_SIGNALS: Final = _select_non_ascii_signals(HIGH_SIGNALS)
_LOW_ASCII_PATTERN: Final = _compile_ascii_signals(LOW_SIGNALS)
_LOW_NON_ASCII_SIGNALS: Final = _select_non_ascii_signals(LOW_SIGNALS)


def classify_task(task: str) -> Tier:
    """Classify a transient task conservatively using documented local rules.

    두 티어의 시그널이 함께 잡히면 항상 high가 이긴다. 난이도를 낮게 잡는 쪽이
    비싼 실수이므로 의도적으로 보수적인 우선순위를 둔다.
    """
    normalized_task = task.strip().casefold()
    if not normalized_task or len(normalized_task) > MAX_TASK_CHARACTERS:
        raise InvalidTaskError()
    if len(normalized_task) >= HIGH_TASK_CHARACTERS or _has_signal(
        normalized_task, _HIGH_ASCII_PATTERN, _HIGH_NON_ASCII_SIGNALS
    ):
        return "high"
    if len(normalized_task) <= LOW_TASK_CHARACTERS and _has_signal(
        normalized_task, _LOW_ASCII_PATTERN, _LOW_NON_ASCII_SIGNALS
    ):
        return "low"
    return "standard"


def _has_signal(
    task: str,
    ascii_pattern: re.Pattern[str],
    non_ascii_signals: frozenset[str],
) -> bool:
    """Report whether a task carries any signal of one tier."""
    if ascii_pattern.search(task):
        return True
    return any(signal in task for signal in non_ascii_signals)
