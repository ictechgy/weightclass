"""Deterministic, local task classification without persistence or logging."""

import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final, Literal

Tier = Literal["low", "standard", "high"]
ReasonCode = Literal[
    "high.risk_floor",
    "high.complexity_signal",
    "high.harmful_outcome",
    "high.uncertain_diagnostic",
    "high.cautious_ambiguity",
    "low.mechanical",
    "low.mechanical_pair",
    "low.substitution",
    "standard.length_floor",
    "standard.not_clearly_mechanical",
]
CLASSIFICATION_POLICY_VERSION: Final = "4"


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
# 결과 패턴에 한 번에 넘기는 최대 길이. 난이도 판정이 아니라 되추적 비용의
# 상한으로만 쓴다. 아래 classify_task_with_reason 의 설명을 참조할 것.
PATTERN_SCAN_CHARACTERS: Final = 1_200
# 창을 겹치는 폭. 결과 패턴을 훑기 전에 연속 공백을 한 칸으로 접으므로, 정규식의
# ``\s+``/``\s*`` 가 의미 없는 공백만으로 한 결과를 창보다 길게 늘릴 수 없다.
# 그 뒤 남는 유계 와일드카드와 중복 작업 문맥이 창 경계에서 함께 보이도록 겹친다.
PATTERN_SCAN_OVERLAP: Final = 400
# 이 길이를 넘으면 low 자격만 잃는다. 길이는 "기계적이지 않다"의 증거는 되지만
# "위험하다"의 증거는 아니다. 예전 정책은 이 값을 high 바닥으로 썼고, 그래서 파일
# 목록을 붙여넣은 단순 작업이 최고 비용 경로로 갔다.
HIGH_TASK_CHARACTERS: Final = 1_200
LOW_TASK_CHARACTERS: Final = 240
# 기계적 동사와 목적어가 같은 요청에 속한다고 보는 최대 거리.
#
# 실측 분포. 참 쌍(low 가 맞는 것)은 3, 5, 5, 5, 58, 66 자였고 거짓 쌍(만장일치
# high 인데 문제 서술에 걸린 것)은 90 자였다. 둘 사이 값을 고르면 거짓 사례
# 한 점에 맞추는 것이 되므로 그렇게 정하지 않았다.
#
# 이 값이 치르는 대가는 측정했다. 공개 코퍼스에서 정답 low 두 건(#7 "commented-out
# block ... Delete it", #9 "missing newline ... add")이 standard 로 넘어간다. 둘
# 다 동사와 목적어가 같은 요청 안에 있지만 58~66 자 떨어져 있다.
#
# 그럼에도 좁게 두는 이유는 오류 비용이 대칭이 아니기 때문이다. 만장일치 high
# 작업을 최저 티어로 보내는 쪽이 low 작업을 standard 로 보내는 쪽보다 비싸다.
# 거짓 사례가 더 모이면 이 값을 데이터로 정할 수 있다. 지금은 아니다.
MECHANICAL_PAIR_DISTANCE: Final = 30
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
# 영어 from/to 는 다른 규칙과 달리 기계적 동사를 요구하지 않아 가장 넓다. 그대로
# 두면 "switch the cache from redis to memcached" 처럼 구성 요소를 통째로 바꾸는
# 요청까지 치환으로 읽힌다. 두 리터럴 중 하나가 숫자를 품을 때(값 교체)만 이
# 패턴 단독으로 low 를 인정하고, 그렇지 않으면 기계적 동사를 함께 요구한다.
_LOW_NUMERIC_SUBSTITUTION: Final = re.compile(rf"\bfrom\s+{_LOW_LITERAL}\s+to\s+{_LOW_LITERAL}")
_LOW_SUBSTITUTION_PATTERNS: Final = (
    re.compile(r"\brename\s+\S{1,40}\s+to\s+\S{1,40}"),
    re.compile(r"\bsort\w{0,3}\b[^\n]{0,60}\balphabetically\b"),
    re.compile(rf"{_LOW_LITERAL}\s*에서\s*{_LOW_LITERAL}\s*(?:으로|로)"),
    re.compile(rf"{_LOW_LITERAL}\s*(?:으로|로)\s*(?:바꾸|바꿔|변경|통일|교체)"),
)

# 여러 지시가 이어 붙은 요청. 기계적 증거가 그중 한 조각에만 걸려 있어도 규칙은
# 전체를 low 로 내린다. 실제 작업은 나머지 절에 있을 수 있으므로, 지시가 하나가
# 아니면 어느 low 규칙도 적용하지 않는다. `typo` 같은 명시적 어휘도 다른 절의
# 구현 요청을 설명하지 못하므로 같은 제약을 받는다.
_MULTI_INSTRUCTION_PATTERN: Final = re.compile(
    r",\s*(?:then|and|also)\b"
    r"|\band\s+(?:make|add|remove|delete|update|fix|rewrite|refactor|move|check)\b"
    r"|;"
    r"|(?:지우|바꾸|고치|만들|넣|옮기|없애|추가하|제거하|정렬하)고\s+"
    r"(?!\s)(?!싶[다은어었으습겠])(?!있[다어었으는습])(?!계[시신셔세실십])(?!나서)"
    r"|그리고|그다음|그 다음"
)
# A full stop alone is not evidence of a second instruction: ordinary task
# descriptions commonly use one sentence for the symptom and one for the
# requested change. Count only English sentences that begin with a reviewed
# imperative verb, so two explicit requests close the inferred mechanical and
# substitution paths without closing them for "problem. fix" prompts.
_ENGLISH_IMPERATIVE_PREFIX_PATTERN: Final = re.compile(
    r"^(?:please\s+)?(?:add|bump|change|check|create|delete|drop|fix|implement|insert|"
    r"make|move|remove|rename|reorder|replace|rewrite|sort|update|write)\b"
)
_ENGLISH_SENTENCE_BOUNDARY_PATTERN: Final = re.compile(r"(?<=[.!?])\s+")
# 여기서 뺀 두 가지를 다시 넣지 말 것.
#
# `\.\s+\S` 는 지시가 둘인 요청이 아니라 문장이 둘인 요청을 잡는다. 실제 태스크
# 프롬프트는 거의 전부 "무엇이 문제다. 이렇게 고쳐라" 형태라, 이 대안 하나가
# 저비용 규칙 전체를 사실상 꺼버렸다. blind 평가 36개 세트에서 이 패턴이 34개에
# 걸렸고 그중 대부분이 평범한 마침표였다.
#
# 한국어 `~고 ` 도 조건 없이 두면 접속이 아니라 보조 용언에 걸린다. "노출되고
# 있어", "확인하고 싶은데" 가 명령 두 개로 읽혔다. 그래서 어간을 명시적인
# 동작 동사로 좁히고 뒤따르는 보조 용언을 배제한다.
#
# `(?!\s)` 를 빼지 말 것. 배제 lookahead 만 두면 `\s+` 가 탐욕적이라 공백이 둘
# 이상일 때 되감기로 무력화된다. "추가하고  싶은데" 는 `\s+` 가 공백 하나만
# 소비하도록 되감으면 다음 문자가 공백이라 배제가 통과하고, 보조 용언인데도
# 명령 두 개로 읽힌다. 앞의 lookahead 가 공백을 끝까지 먹도록 강제한다.
#
# 배제어를 한 음절로 두지 말 것. "계" 하나로 두면 계산·계정·계획 같은 평범한
# 명사까지 배제되어 "이 주석 지우고 계산 로직도 고쳐줘" 가 단일 지시로 읽힌다.
# 그러면 저비용 규칙이 열려 실제로는 두 가지 일인 요청이 low 로 떨어진다. 이는
# 이 파일이 스스로 더 비싸다고 적어 둔 방향이다. 그래서 보조 용언이 실제로
# 취하는 어미까지 붙여 좁힌다. 격식체(싶습니다, 있습니다, 계실까요)의 둘째
# 음절도 함께 담아야 한다. 빠뜨리면 배제가 실패해 평범한 서술이 명령 둘로
# 읽히고, 저비용 규칙이 닫혀 과대 라우팅이 된다.
#
# "~고 있는" 은 좁히지 못한다. "주석 지우고 있는 로직" 은 진행형("주석을 지우고
# 있는")으로도, 지시 둘("주석을 지우고, 있는 로직을")로도 읽힌다. 형태소 분석
# 없이 가를 수 없어 더 흔한 진행형 쪽으로 둔다. 이 파일이 이미 여러 곳에서
# 감수한 것과 같은 한계다.

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

# Symptom-described debugging reaches high only when the request contains both
# explicit root-cause investigation intent and evidence that the symptom is
# intermittent or nondeterministic. Neither vocabulary group is a high signal
# by itself. These patterns are deliberately phrases (or inflected stems in
# Korean), not additions to the broad HIGH_SIGNALS word list.
_ROOT_CAUSE_INTENT_PATTERNS: Final = (
    re.compile(
        r"\b(?:investigate|investigating|determine|identify|find)\s+"
        r"(?:the\s+)?(?:root[\s-]+cause|cause\s+of|why)\b"
    ),
    re.compile(r"(?:근본\s*)?원인(?:을|를|이|가)?\s*(?:찾|조사|분석|규명|파악)"),
)
_NONDETERMINISTIC_SYMPTOM_PATTERNS: Final = (
    re.compile(
        r"\b(?:sometimes|intermittent(?:ly)?|non[\s-]?deterministic(?:ally)?|"
        r"sporadic(?:ally)?|flaky|unpredictab(?:le|ly))\b"
    ),
    re.compile(r"(?:간헐적(?:으로|인)?|비결정적(?:으로|인)?|때때로|가끔|종종)"),
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
    if _has_signal(
        normalized_task,
        _HIGH_RISK_FLOOR_ASCII_PATTERN,
        _HIGH_RISK_FLOOR_NON_ASCII_SIGNALS,
    ):
        return ClassificationDecision("high", "high.risk_floor")
    if _has_signal(normalized_task, _HIGH_ASCII_PATTERN, _HIGH_NON_ASCII_SIGNALS):
        return ClassificationDecision("high", "high.complexity_signal")
    if _has_high_risk_outcome(normalized_task):
        return ClassificationDecision("high", "high.harmful_outcome")
    if _has_uncertain_diagnostic(normalized_task):
        return ClassificationDecision("high", "high.uncertain_diagnostic")
    if len(normalized_task) <= LOW_TASK_CHARACTERS:
        has_multiple_instructions = _has_multiple_instructions(normalized_task)
        if not has_multiple_instructions and _has_signal(
            normalized_task, _LOW_ASCII_PATTERN, _LOW_NON_ASCII_SIGNALS
        ):
            return ClassificationDecision("low", "low.mechanical")
        if not has_multiple_instructions:
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


def _signal_spans(
    task: str,
    ascii_pattern: re.Pattern[str],
    non_ascii_signals: frozenset[str],
) -> list[tuple[int, int]]:
    """Return where each signal of one group occurs."""
    spans = [match.span() for match in ascii_pattern.finditer(task)]
    for signal in non_ascii_signals:
        start = task.find(signal)
        while start != -1:
            spans.append((start, start + len(signal)))
            start = task.find(signal, start + 1)
    return spans


def _has_mechanical_pair(task: str) -> bool:
    """Report whether a mechanical action actually applies to a mechanical object.

    동사와 목적어가 함께 나오기만 하면 되는 것이 아니라 서로 가까워야 한다.
    거리를 보지 않으면 요청이 아니라 문제 서술에 걸린다. 실제로 "purge 는 그걸
    파일 이름의 stem 과 비교해서 ... 전부 지워져. 파생 파일까지 남도록 고쳐줘"
    라는 만장일치 high 태스크가 "이름"(목적어)과 "지워"(동사)로 low 가 되었다.
    둘은 70자 넘게 떨어져 있었고 어느 쪽도 요청의 일부가 아니었다.

    한국어는 어순이 자유로워 동사가 앞뒤 어디에 오는지는 보지 않고 거리만 본다.
    """
    actions = _signal_spans(task, _LOW_ACTION_ASCII_PATTERN, _LOW_ACTION_NON_ASCII_SIGNALS)
    if not actions:
        return False
    objects = _signal_spans(task, _LOW_OBJECT_ASCII_PATTERN, _LOW_OBJECT_NON_ASCII_SIGNALS)
    return any(
        max(action[0], obj[0]) - min(action[1], obj[1]) <= MECHANICAL_PAIR_DISTANCE
        for action in actions
        for obj in objects
    )


def _has_multiple_instructions(task: str) -> bool:
    """Report whether the task carries more than one instruction."""
    if _MULTI_INSTRUCTION_PATTERN.search(task) is not None:
        return True
    imperative_sentences = 0
    for sentence in _ENGLISH_SENTENCE_BOUNDARY_PATTERN.split(task):
        if _ENGLISH_IMPERATIVE_PREFIX_PATTERN.match(sentence.lstrip()):
            imperative_sentences += 1
            if imperative_sentences == 2:
                return True
    return False


def _has_uncertain_diagnostic(task: str) -> bool:
    """Match root-cause intent paired with a nondeterministic symptom."""
    return any(pattern.search(task) for pattern in _ROOT_CAUSE_INTENT_PATTERNS) and any(
        pattern.search(task) for pattern in _NONDETERMINISTIC_SYMPTOM_PATTERNS
    )


def _has_low_substitution(task: str) -> bool:
    """Report whether the task states a concrete target state to substitute in."""
    if any(pattern.search(task) for pattern in _LOW_SUBSTITUTION_PATTERNS):
        return True
    match = _LOW_NUMERIC_SUBSTITUTION.search(task)
    if match is None:
        return False
    # 값 교체(숫자)면 그 자체로 기계적이다. 아니면 기계적 동사가 함께 있어야 한다.
    return any(character.isdigit() for character in match.group(0)) or _has_signal(
        task, _LOW_ACTION_ASCII_PATTERN, _LOW_ACTION_NON_ASCII_SIGNALS
    )


def _scan_windows(task: str) -> Iterator[str]:
    """Yield overlapping bounded windows covering the whole task.

    결과 패턴은 유계 와일드카드를 중첩하므로 한 번에 넘기는 길이를 닫아야 한다.
    그렇다고 앞부분만 보면 안 된다. 길이가 더 이상 티어를 올리지 않게 된 뒤로는,
    앞을 채워 넣는 것만으로 뒤에 있는 유해 결과 서술을 확정적으로 숨길 수 있기
    때문이다. 창을 겹쳐 전체를 훑으면 창당 비용이 유계이므로 총비용은 길이에
    선형이고, 경계에 걸친 서술도 사라지지 않는다.
    """
    step = PATTERN_SCAN_CHARACTERS - PATTERN_SCAN_OVERLAP
    for start in range(0, max(len(task), 1), step):
        yield task[start : start + PATTERN_SCAN_CHARACTERS]
        if start + PATTERN_SCAN_CHARACTERS >= len(task):
            return


def _has_high_risk_outcome(task: str) -> bool:
    """Report whether a narrowly defined costly outcome is described."""
    # 결과 패턴의 공백 구분자는 표기 차이를 받아들이기 위한 것이지 거리를 무한히
    # 늘리기 위한 것이 아니다. 그대로 창을 내면 반복 공백만으로 의미상 한 문장을
    # 두 창에 갈라 위험 결과를 숨길 수 있다. 결과 검사에만 공백을 접어 길이·기계적
    # 쌍 거리 같은 다른 분류 계약은 바꾸지 않는다.
    compact_task = re.sub(r"\s+", " ", task)
    return any(
        any(pattern.search(window) for pattern in _HIGH_RISK_OUTCOME_PATTERNS)
        or _has_duplicate_work_outcome(window)
        for window in _scan_windows(compact_task)
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
