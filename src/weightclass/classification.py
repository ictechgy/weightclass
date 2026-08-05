"""Deterministic, local task classification without persistence or logging."""

import re
import sys
from dataclasses import dataclass
from typing import Final, Literal

Tier = Literal["low", "standard", "high"]
ReasonCode = Literal[
    "high.length_floor",
    "high.risk_floor",
    "high.harmful_outcome",
    "high.cautious_ambiguity",
    "low.mechanical",
    "standard.not_clearly_mechanical",
]
CLASSIFICATION_POLICY_VERSION: Final = "1"


@dataclass(frozen=True)
class ClassificationDecision:
    """Privacy-safe result from the versioned local classification policy."""

    tier: Tier
    reason_code: ReasonCode
    policy_version: str = CLASSIFICATION_POLICY_VERSION

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
# Narrow security failure classes that impose a high floor even when a task does
# not use the broader HIGH_SIGNALS vocabulary. Keep these phrases reviewable:
# matching fragments or returning the matched phrase would violate the transient
# task-text contract.
HIGH_RISK_FLOOR_SIGNALS: Final = frozenset(
    {
        "account enumeration",
        "password enumeration",
        "sql injection",
        "unauthenticated",
        "xss",
        "계정 열거",
        "비인증",
        "에스큐엘 인젝션",
        "인증되지 않은",
        "크로스 사이트 스크립팅",
        "sql 인젝션",
    }
)
# 접미사 규칙으로 유도할 수 없는 어형. HIGH_SIGNALS 는 명사형만 담고 있어서
# "deployment" 는 잡고 "deploy" 는 놓쳤다. 규칙으로 만들 수 없는 형태이므로
# 표로 못박는다. 빈 튜플은 "추가할 것이 없다"가 아니라 "추가하지 않기로
# 결정했다"는 뜻이며, 이유는 아래 HIGH_SIGNAL_NO_INFLECTION_RATIONALE 에 적는다.
HIGH_SIGNAL_INFLECTIONS: Final = {
    "architecture": ("architectural",),
    "authentication": ("authenticate", "authenticating"),
    "authorization": ("authorize", "authorizing", "authorise", "authorising", "authorisation"),
    "concurrency": ("concurrent", "concurrently"),
    "credential": (),
    "data loss": (),
    "database": (),
    "deployment": ("deploy",),
    "migration": ("migrate", "migrating"),
    "payment": (),
    "performance": ("performant",),
    "privacy": (),
    "production": (),
    "race condition": (),
    "refactor": (),
    "rollback": ("roll back",),
    "security": ("secure", "securing", "insecure"),
}

# 빈 튜플로 둔 이유. 없으면 나중에 누군가 "빠졌네" 하고 오탐을 만들어 넣는다.
HIGH_SIGNAL_NO_INFLECTION_RATIONALE: Final = {
    "credential": "credentials 는 접미사 규칙으로 유도된다.",
    "data loss": "data-loss 는 구분자 규칙이 처리한다. 'lose data' 는 너무 느슨하다.",
    "database": "databases 는 접미사 규칙으로 유도된다.",
    "payment": "pay 는 'pay attention' 처럼 일상 표현에서 흔해 오탐이 크다.",
    "privacy": "private 는 '이 메서드를 private 으로 바꿔줘' 같은 사소한 작업에서 흔하다.",
    "production": (
        "produce 는 같은 뜻의 어형이 아니다. '주간 보고서를 produce' 는 어려운 일이 아니다."
    ),
    "race condition": "race 단독은 판단이 갈린다. 측정 후 별도로 결정한다.",
    "refactor": "refactors/refactoring/refactored 는 접미사 규칙으로 유도된다.",
}

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


# 선행 경계만으로 "reproduction"이 "production"에 걸리는 오분류를 막을 수 있다.
# 후행까지 경계로 닫으면 "credentials", "payments", "refactoring" 같은 굴절형이
# 전부 시그널에서 빠져나가므로, 흔한 어미를 경계 앞에서 함께 허용한다.
_SIGNAL_SUFFIXES: Final = "(?:s|es|d|ed|ing)?"
# "data loss"는 "data-loss"로도 쓴다. 여러 단어로 된 시그널이 공백 표기에만
# 걸리면, 하이픈 표기 작업에서는 상위 시그널이 통째로 사라진다.
_SIGNAL_WORD_SEPARATOR: Final = r"[\s\-]+"


def _compile_ascii_signals(signals: frozenset[str]) -> re.Pattern[str]:
    """Compile the ASCII signals of one tier into a single word-boundary pattern."""
    ascii_signals = sorted(
        (signal for signal in signals if signal.isascii()), key=len, reverse=True
    )
    alternation = "|".join(
        re.escape(signal).replace(r"\ ", " ").replace(" ", _SIGNAL_WORD_SEPARATOR)
        for signal in ascii_signals
    )
    return re.compile(rf"\b(?:{alternation}){_SIGNAL_SUFFIXES}\b")


def _select_non_ascii_signals(signals: frozenset[str]) -> frozenset[str]:
    """Return the signals that must be matched by containment rather than by word."""
    return frozenset(signal for signal in signals if not signal.isascii())


# 한국어는 조사와 합성어 때문에 단어 경계 개념이 없어 부분 문자열로 검사한다.
# 그 결과 "저작권한도"처럼 시그널을 품은 합성어가 상위 티어로 오분류될 수 있다.
# 상위 티어로의 오분류는 보수적인 방향이므로, 형태소 분석 의존성을 들이는 대신
# 이 한계를 문서화하고 감수한다.
_HIGH_ASCII_PATTERN: Final = _compile_ascii_signals(
    HIGH_SIGNALS.union(*HIGH_SIGNAL_INFLECTIONS.values())
)
_HIGH_NON_ASCII_SIGNALS: Final = _select_non_ascii_signals(HIGH_SIGNALS)
_HIGH_RISK_FLOOR_ASCII_PATTERN: Final = _compile_ascii_signals(HIGH_RISK_FLOOR_SIGNALS)
_HIGH_RISK_FLOOR_NON_ASCII_SIGNALS: Final = _select_non_ascii_signals(
    HIGH_RISK_FLOOR_SIGNALS
)
_LOW_ASCII_PATTERN: Final = _compile_ascii_signals(LOW_SIGNALS)
_LOW_NON_ASCII_SIGNALS: Final = _select_non_ascii_signals(LOW_SIGNALS)

# 기술 명사가 아니라 이미 발생한 고비용 결과를 말하는 좁은 표현들이다. 단어 하나
# (예: "negative", "job")만으로는 올리지 않아, 단순한 UI 표시나 의도적인 테스트
# 반복은 standard 에 남긴다. 각 패턴의 오탐 경계는 테스트로 고정한다.
_HIGH_RISK_OUTCOME_PATTERNS: Final = (
    re.compile(
        r"\b(?:account|customer|user|i|(?:my\s+)?cards?)\b.{0,80}"
        r"\b(?:is|are|gets?|got|was|were)?\s*charged\b.{0,32}"
        r"\b(?:twice|multiple\s+times|more\s+than\s+once)\b"
    ),
    re.compile(
        r"\b(?=[\s\S]{0,160}\b(?:sometimes|unexpectedly|after|again|"
        r"duplicate(?:d)?|multiple)\b)(?:"
        r"same\s+(?:job|task|worker|request|event)s?\b.{0,80}"
        r"\b(?:runs?|executes?|processes?)\b.{0,32}"
        r"\b(?:twice|multiple\s+times|more\s+than\s+once)\b|"
        r"(?:job|task|worker|request|event)s?\b.{0,80}"
        r"\b(?:runs?|executes?|processes?)\b.{0,32}"
        r"\bsame\s+(?:job|task|worker|request|event)s?\b.{0,32}"
        r"\b(?:twice|multiple\s+times|more\s+than\s+once)\b)"
    ),
    re.compile(
        r"\bbalances?\b.{0,80}"
        r"\b(?:is|are|becomes?|became|turns?|turned|ends?|ended|gets?)\b.{0,32}"
        r"\bnegative\b"
    ),
    re.compile(r"같은\s*(?:작업|잡|요청|이벤트).{0,80}(?:두\s*번|중복).{0,40}(?:실행|처리)"),
    re.compile(r"잔액.{0,40}(?:가끔|종종|때때로|자꾸).{0,40}음수"),
    re.compile(r"잔액.{0,40}음수.{0,32}(?:되|돼|내려|떨어)"),
)


def validate_task(task: str) -> str:
    """Return the normalized task, rejecting input that must not be routed.

    이 검증은 분류와 분리되어 있어야 한다. 티어를 밖에서 받아 분류를 건너뛰는
    경로(--tier)가 생기면, 검증이 classify_task 안에만 있을 경우 빈 입력이나
    상한 초과 입력이 그대로 벤더 프로세스로 넘어간다.
    """
    normalized_task = task.strip().casefold()
    if not normalized_task or len(normalized_task) > MAX_TASK_CHARACTERS:
        raise InvalidTaskError()
    return normalized_task


def classify_task(task: str) -> Tier:
    """Classify a transient task conservatively using documented local rules.

    두 티어의 시그널이 함께 잡히면 항상 high가 이긴다. 난이도를 낮게 잡는 쪽이
    비싼 실수이므로 의도적으로 보수적인 우선순위를 둔다.
    """
    return classify_task_with_reason(task).tier


def classify_task_with_reason(task: str) -> ClassificationDecision:
    """Return a versioned decision containing no task-derived content."""
    normalized_task = validate_task(task)
    if len(normalized_task) >= HIGH_TASK_CHARACTERS:
        return ClassificationDecision("high", "high.length_floor")
    if _has_signal(
        normalized_task,
        _HIGH_RISK_FLOOR_ASCII_PATTERN,
        _HIGH_RISK_FLOOR_NON_ASCII_SIGNALS,
    ) or _has_signal(normalized_task, _HIGH_ASCII_PATTERN, _HIGH_NON_ASCII_SIGNALS):
        return ClassificationDecision("high", "high.risk_floor")
    if _has_high_risk_outcome(normalized_task):
        return ClassificationDecision("high", "high.harmful_outcome")
    if len(normalized_task) <= LOW_TASK_CHARACTERS and _has_signal(
        normalized_task, _LOW_ASCII_PATTERN, _LOW_NON_ASCII_SIGNALS
    ):
        return ClassificationDecision("low", "low.mechanical")
    return ClassificationDecision("standard", "standard.not_clearly_mechanical")


def apply_cautious_posture(decision: ClassificationDecision) -> ClassificationDecision:
    """Raise an ambiguous standard decision without changing any other tier."""
    if decision.tier == "standard":
        return ClassificationDecision("high", "high.cautious_ambiguity")
    return decision


def _has_signal(
    task: str,
    ascii_pattern: re.Pattern[str],
    non_ascii_signals: frozenset[str],
) -> bool:
    """Report whether a task carries any signal of one tier."""
    if ascii_pattern.search(task):
        return True
    return any(signal in task for signal in non_ascii_signals)


def _has_high_risk_outcome(task: str) -> bool:
    """Report whether a narrowly defined costly outcome is described."""
    return any(pattern.search(task) for pattern in _HIGH_RISK_OUTCOME_PATTERNS)
