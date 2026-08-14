"""Deterministic, local task classification without persistence or logging."""

import re
import sys
from dataclasses import dataclass
from typing import Final, Literal

Tier = Literal["low", "standard", "high"]
ReasonCode = Literal[
    "high.risk_floor",
    "high.complexity_signal",
    "high.harmful_outcome",
    "high.cautious_ambiguity",
    "low.mechanical",
    "low.mechanical_pair",
    "low.substitution",
    "standard.length_floor",
    "standard.not_clearly_mechanical",
]
CLASSIFICATION_POLICY_VERSION: Final = "3"


@dataclass(frozen=True)
class ClassificationDecision:
    """Privacy-safe result from the versioned local classification policy."""

    tier: Tier
    reason_code: ReasonCode
    policy_version: str = CLASSIFICATION_POLICY_VERSION


HIGH_SIGNALS: Final = frozenset(
    {
        "architecture",
        "auth",
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
    "auth": (),
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
    "auth": (
        "authentication/authorization 의 축약형이며 그 두 시그널이 각자의 어형을 이미 "
        "가지고 있다. auth 자체에서 유도할 별도 어형이 없다."
    ),
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
        "indentation",
        "lint",
        "linting",
        "punctuation",
        "reformat",
        "reformatting",
        "rename",
        "renames",
        "renaming",
        "spelling",
        "trailing newline",
        "trailing whitespace",
        "typo",
        "typos",
        "whitespace",
        "whitespaces",
        "오타",
        "오탈자",
        "이름 변경",
        "이름변경",
        "리네임",
        "문장부호",
        "포맷",
        "포매팅",
        "들여쓰기",
        "띄어쓰기",
        "줄바꿈",
    }
)

# 단독으로는 난이도를 말해주지 않는 동사와, 그 동사가 걸렸을 때만 기계적 작업으로
# 확정되는 좁은 목적어. 화이트리스트 한 장으로 어휘를 계속 늘리는 대신, 둘의
# 동시 출현을 요구해 오탐을 목적어 쪽에서 막는다.
#
# 이 규칙은 상위 시그널·위험 바닥·유해 결과 검사를 모두 통과한 뒤에만 도달한다.
# 따라서 "결제 금액 계산을 수정해줘" 처럼 상위 시그널을 품은 문장은 여기까지 오지
# 않는다. 동사를 넓게 두어도 안전한 이유가 그것이다.
LOW_MECHANICAL_ACTIONS: Final = frozenset(
    {
        "add",
        "bump",
        "delete",
        "drop",
        "insert",
        "remove",
        "reorder",
        "replace",
        "sort",
        "update",
        "정렬",
        "제거",
        "삭제",
        "추가",
        "바꾸",
        "바꿔",
        "변경",
        "수정",
        "올려",
        "지워",
    }
)
LOW_MECHANICAL_OBJECTS: Final = frozenset(
    {
        "changelog",
        "comment",
        "comments",
        "docstring",
        "docstrings",
        "import",
        "imports",
        "license header",
        "log message",
        "newline",
        "semicolon",
        "semicolons",
        "unused import",
        "version",
        "주석",
        "임포트",
        "세미콜론",
        "로그 메시지",
        "버전",
        "이름",
    }
)

MAX_TASK_CHARACTERS: Final = 20_000
# 길이 상한. 난이도 판정이 아니라 되추적 비용의 상한으로만 쓴다. 아래
# classify_task_with_reason 의 설명을 참조할 것.
PATTERN_SCAN_CHARACTERS: Final = 1_200
# 이 길이를 넘으면 low 자격만 잃는다. 길이는 "기계적이지 않다"의 증거는 되지만
# "위험하다"의 증거는 아니다. 예전 정책은 이 값을 high 바닥으로 썼고, 그래서 파일
# 목록을 붙여넣은 단순 작업이 최고 비용 경로로 갔다.
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
_HIGH_RISK_FLOOR_NON_ASCII_SIGNALS: Final = _select_non_ascii_signals(HIGH_RISK_FLOOR_SIGNALS)
_LOW_ASCII_PATTERN: Final = _compile_ascii_signals(LOW_SIGNALS)
_LOW_NON_ASCII_SIGNALS: Final = _select_non_ascii_signals(LOW_SIGNALS)
# 어휘가 아니라 문장 구조로 잡는 기계적 작업. "A 를 B 로 바꿔라" 는 목표 상태가
# 이미 주어졌다는 뜻이고, 그런 작업은 판단할 것이 남아 있지 않다. 화이트리스트를
# 계속 늘리는 대신 이 구조를 본다.
#
# 세 가지가 이 규칙의 오탐을 막는다: 상위 시그널·위험 바닥·유해 결과가 먼저
# 검사되고, 길이 상한이 걸리며, 치환 대상과 값이 각각 40자 이내로 닫혀 있다.
# 되추적 관점에서도 모든 수량자가 유계이고 중첩되지 않는다.
# 치환될 값이 리터럴 토큰일 것을 요구한다. 이 조건이 없으면 "무한 스크롤로 바꿔줘"
# 처럼 목표가 기능 서술인 요청까지 치환으로 읽혀 구현 작업이 최저 비용 경로로
# 떨어진다. 리터럴은 ASCII 식별자·경로·숫자 형태로 좁힌다.
_LOW_LITERAL: Final = r"[a-z0-9._/*<>=-]{1,40}"
_LOW_SUBSTITUTION_PATTERNS: Final = (
    re.compile(rf"\bfrom\s+{_LOW_LITERAL}\s+to\s+{_LOW_LITERAL}"),
    re.compile(r"\brename\s+\S{1,40}\s+to\s+\S{1,40}"),
    re.compile(r"\bsort\w{0,3}\b[^\n]{0,60}\balphabetically\b"),
    re.compile(rf"{_LOW_LITERAL}\s*에서\s*{_LOW_LITERAL}\s*(?:으로|로)"),
    re.compile(rf"{_LOW_LITERAL}\s*(?:으로|로)\s*(?:바꾸|바꿔|변경|통일|교체)"),
)

_LOW_ACTION_ASCII_PATTERN: Final = _compile_ascii_signals(LOW_MECHANICAL_ACTIONS)
_LOW_ACTION_NON_ASCII_SIGNALS: Final = _select_non_ascii_signals(LOW_MECHANICAL_ACTIONS)
_LOW_OBJECT_ASCII_PATTERN: Final = _compile_ascii_signals(LOW_MECHANICAL_OBJECTS)
_LOW_OBJECT_NON_ASCII_SIGNALS: Final = _select_non_ascii_signals(LOW_MECHANICAL_OBJECTS)

# 기술 명사가 아니라 이미 발생한 고비용 결과를 말하는 좁은 표현들이다. 단어 하나
# (예: "negative", "job")만으로는 올리지 않아, 단순한 UI 표시나 의도적인 테스트
# 반복은 standard 에 남긴다. 각 패턴의 오탐 경계는 테스트로 고정한다.
_HIGH_RISK_OUTCOME_PATTERNS: Final = (
    re.compile(
        r"\b(?:account|customer|user|i|(?:my\s+)?cards?)\b[\s\S]{0,80}"
        r"\b(?:is|are|gets?|got|was|were)?\s*charged\b[\s\S]{0,32}"
        r"\b(?:twice|multiple\s+times|more\s+than\s+once)\b"
    ),
    re.compile(
        r"\bbalances?\b[\s\S]{0,80}"
        r"\b(?:is|are|becomes?|became|turns?|turned|ends?|ended|gets?)\b[\s\S]{0,32}"
        r"\bnegative\b"
    ),
    re.compile(
        r"같은\s*(?:작업|잡|요청|이벤트)[\s\S]{0,80}"
        r"(?:두\s*번|중복)[\s\S]{0,40}(?:실행|처리)"
    ),
    re.compile(r"잔액[\s\S]{0,40}(?:가끔|종종|때때로|자꾸)[\s\S]{0,40}음수"),
    re.compile(r"잔액[\s\S]{0,40}음수[\s\S]{0,32}(?:되|돼|내려|떨어)"),
)

_DUPLICATE_WORK_PATTERNS: Final = (
    re.compile(
        r"\bsame\s+(?:job|task|worker|request|event)s?\b[\s\S]{0,80}"
        r"\b(?:runs?|executes?|processes?)\b[\s\S]{0,32}"
        r"(?P<multiplicity>\b(?:twice|multiple\s+times|more\s+than\s+once)\b)"
    ),
    re.compile(
        r"\b(?:job|task|worker|request|event)s?\b[\s\S]{0,80}"
        r"\b(?:runs?|executes?|processes?)\b[\s\S]{0,32}"
        r"\bsame\s+(?:job|task|worker|request|event)s?\b[\s\S]{0,32}"
        r"(?P<multiplicity>\b(?:twice|multiple\s+times|more\s+than\s+once)\b)"
    ),
)
_DUPLICATE_WORK_QUALIFIER_PATTERN: Final = re.compile(
    r"\b(?:sometimes|unexpectedly|after|again|duplicate(?:d)?|multiple)\b"
)
_INTENTIONAL_DUPLICATE_CONTEXT_PATTERN: Final = re.compile(r"\bintegration\s+test\b")
_DUPLICATE_WORK_CONTEXT_CHARACTERS: Final = 160


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
    """Return a versioned decision containing no task-derived content.

    검사 순서는 취향이 아니라 계약이다. 상위 시그널과 위험 바닥이 반드시 low 판정
    앞에 와야 한다. 그래야 기계적 동사를 넓게 두어도 위험한 작업이 싼 경로로
    떨어지지 않는다.

    되추적 상한은 이제 길이 바닥이 아니라 입력 슬라이스가 맡는다. 아래 결과
    패턴들은 `[\\s\\S]{0,80}` 처럼 경계 있는 와일드카드를 여러 겹 중첩하므로,
    상한(20,000자)까지 통과시키면 적대적 입력 하나로 수 밀리초를 태울 수 있다.
    그래서 그 패턴들에만 PATTERN_SCAN_CHARACTERS 로 자른 입력을 넘긴다.

    시그널 검사는 자르지 않은 전체 입력을 본다. 단일 교대 패턴이라 길이에 선형이고,
    잘라서 넘기면 긴 작업의 후반부에 있는 상위 시그널을 통째로 놓쳐 위험한 작업이
    standard 로 내려간다. 예전 정책은 길이 바닥이 그 구멍을 가려주었지만, 지금은
    길이가 티어를 올리지 않으므로 시그널이 전체를 봐야 한다.

    이 계약을 바꾸려면 먼저 비용을 측정할 것. tests/test_classification.py 의
    되추적 상한 회귀 테스트가 이 계약을 고정한다.
    """
    normalized_task = validate_task(task)
    scanned_task = normalized_task[:PATTERN_SCAN_CHARACTERS]
    if _has_signal(
        normalized_task,
        _HIGH_RISK_FLOOR_ASCII_PATTERN,
        _HIGH_RISK_FLOOR_NON_ASCII_SIGNALS,
    ):
        return ClassificationDecision("high", "high.risk_floor")
    if _has_signal(normalized_task, _HIGH_ASCII_PATTERN, _HIGH_NON_ASCII_SIGNALS):
        return ClassificationDecision("high", "high.complexity_signal")
    if _has_high_risk_outcome(scanned_task):
        return ClassificationDecision("high", "high.harmful_outcome")
    if len(normalized_task) <= LOW_TASK_CHARACTERS:
        if _has_signal(normalized_task, _LOW_ASCII_PATTERN, _LOW_NON_ASCII_SIGNALS):
            return ClassificationDecision("low", "low.mechanical")
        if _has_mechanical_pair(normalized_task):
            return ClassificationDecision("low", "low.mechanical_pair")
        if _has_low_substitution(normalized_task):
            return ClassificationDecision("low", "low.substitution")
    elif len(normalized_task) >= HIGH_TASK_CHARACTERS:
        # 길이는 low 자격만 박탈한다. 티어를 올리지 않는다.
        return ClassificationDecision("standard", "standard.length_floor")
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


def _has_mechanical_pair(task: str) -> bool:
    """Report whether a mechanical action and a narrow mechanical object co-occur.

    동사와 목적어의 위치 관계는 보지 않는다. 한국어는 어순이 자유롭고, 위치까지
    검사하려면 태스크 본문을 잘라 들고 있어야 해서 전송 계약과 충돌한다. 대신
    길이 상한과 상위 시그널 우선순위가 오탐의 범위를 닫는다.
    """
    return _has_signal(
        task, _LOW_ACTION_ASCII_PATTERN, _LOW_ACTION_NON_ASCII_SIGNALS
    ) and _has_signal(task, _LOW_OBJECT_ASCII_PATTERN, _LOW_OBJECT_NON_ASCII_SIGNALS)


def _has_low_substitution(task: str) -> bool:
    """Report whether the task states a concrete target state to substitute in."""
    return any(pattern.search(task) for pattern in _LOW_SUBSTITUTION_PATTERNS)


def _has_high_risk_outcome(task: str) -> bool:
    """Report whether a narrowly defined costly outcome is described."""
    return any(pattern.search(task) for pattern in _HIGH_RISK_OUTCOME_PATTERNS) or (
        _has_duplicate_work_outcome(task)
    )


def _has_duplicate_work_outcome(task: str) -> bool:
    """Match one bounded duplicate-work event with independent instability evidence."""
    for pattern in _DUPLICATE_WORK_PATTERNS:
        for candidate in pattern.finditer(task):
            suppressor_end = min(len(task), candidate.end() + 80)
            if _INTENTIONAL_DUPLICATE_CONTEXT_PATTERN.search(
                task, candidate.start(), suppressor_end
            ):
                continue

            context_start = max(0, candidate.start() - _DUPLICATE_WORK_CONTEXT_CHARACTERS)
            context_end = min(len(task), candidate.end() + _DUPLICATE_WORK_CONTEXT_CHARACTERS)
            multiplicity_start, multiplicity_end = candidate.span("multiplicity")
            for qualifier in _DUPLICATE_WORK_QUALIFIER_PATTERN.finditer(
                task, context_start, context_end
            ):
                if qualifier.start() >= multiplicity_start and qualifier.end() <= multiplicity_end:
                    continue
                return True
    return False
