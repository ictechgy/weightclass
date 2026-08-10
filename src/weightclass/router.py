"""Deterministic route selection for user-reviewable vendor commands."""

import hashlib
import json
from dataclasses import dataclass
from typing import Final, Literal

from .classification import Tier

NATIVE_FINGERPRINT_VERSION: Final = 1
Posture = Literal["balanced", "cautious"]


SUPPORTED_VENDORS: Final = frozenset({"claude", "codex"})
# manual 은 사람에게 승인을 묻는 모드다. --print 는 비대화형이라 물어볼 상대가
# 없어 모든 편집이 거부되고, 그런데도 claude 는 0으로 종료한다. 즉 라우터가
# 성공을 보고하면서 아무 일도 일어나지 않는다. acceptEdits 는 파일 편집만
# 자동 승인하므로, Codex 기본 라우트가 이미 할 수 있던 파일 수정을 Claude
# 라우트도 할 수 있게 한다. 명령 실행까지 같아지는 것은 아니다. Codex 의
# workspace-write 는 명령을 실행하지만 acceptEdits 는 편집 외 도구를 여전히
# 프롬프트로 넘기고, --print 에는 응답할 사람이 없다.
#
# 이 선택을 "wclass route 로 검토했으니 안전하다"로 정당화하지 말 것. route 와
# run 은 정책을 각각 따로 읽으므로, --ack-route-fingerprint 로 명시적으로 묶지
# 않으면 그 사이에 정책이 바뀌어도 검토한 것과 다른 명령이 그대로 실행된다.
# 항상 적용되는 경계는 정책 파일에 대한 사용자의 통제와, 코드에 고정되어
# 교체할 수 없는 기본 라우트다.
CLAUDE_COMMAND_PREFIX: Final = (
    "claude",
    "--print",
    "--no-session-persistence",
    "--permission-mode",
    "acceptEdits",
    "--effort",
)
CODEX_COMMAND_PREFIX: Final = (
    "codex",
    "exec",
    "--ephemeral",
    "--sandbox",
    "workspace-write",
    "-c",
)


def codex_command(reasoning_effort: str) -> tuple[str, ...]:
    """Build the built-in Codex command for one reasoning effort label.

    Codex exec에는 Claude의 --effort에 해당하는 전용 플래그가 없으므로,
    설정 오버라이드(-c)로 티어별 추론 강도를 전달한다. 마지막 "-"는 태스크를
    표준 입력에서 읽으라는 뜻이다.
    """
    return CODEX_COMMAND_PREFIX + (f"model_reasoning_effort={reasoning_effort}", "-")


# 프롬프트를 stdin 이 아니라 인자로만 받는 CLI 가 있다. 그런 명령에서 태스크가
# 들어갈 자리를 이 토큰으로 표시한다. 이 토큰이 없으면 지금까지처럼 stdin 으로
# 전달한다.
TASK_PLACEHOLDER: Final = "{{task}}"


def uses_argv_task_delivery(command: tuple[str, ...]) -> bool:
    """Report whether this command carries the task in argv rather than on stdin."""
    return TASK_PLACEHOLDER in command


def substitute_task(command: tuple[str, ...], task: str) -> tuple[str, ...]:
    """Fill the single reserved slot. Call this only immediately before spawn.

    치환된 argv 는 실행에만 쓴다. 검토 출력과 지문은 치환 전 명령으로 계산해야
    태스크가 그 둘에 새어 들어가지 않는다.
    """
    return tuple(task if token == TASK_PLACEHOLDER else token for token in command)


@dataclass(frozen=True)
class Route:
    """A reviewable vendor command. `command` is the only thing ever executed.

    모델을 별도 라벨로 들고 있지 않는 것은 의도적이다. 실행되는 것은 command
    뿐이므로, 검증할 수 없는 라벨을 함께 실으면 리뷰 산출물이 실제 실행과
    어긋날 수 있다. 모델은 command 안에서 드러난다.
    """

    route_id: str
    vendor: str
    workflow: str
    command: tuple[str, ...]
    tier: Tier | None = None


@dataclass(frozen=True)
class RouteRequest:
    vendor: str
    workflow: str


@dataclass(frozen=True)
class RoutingPolicy:
    routes: tuple[Route, ...]
    allow_mixed_vendors: bool = False
    posture: Posture | None = None


DEFAULT_ROUTES: Final = (
    Route(
        route_id="codex-low",
        vendor="codex",
        workflow="",
        tier="low",
        command=codex_command("low"),
    ),
    Route(
        route_id="codex-standard",
        vendor="codex",
        workflow="",
        tier="standard",
        command=codex_command("medium"),
    ),
    Route(
        route_id="codex-high",
        vendor="codex",
        workflow="",
        tier="high",
        command=codex_command("high"),
    ),
    Route(
        route_id="claude-low",
        vendor="claude",
        workflow="",
        tier="low",
        command=CLAUDE_COMMAND_PREFIX + ("low",),
    ),
    Route(
        route_id="claude-standard",
        vendor="claude",
        workflow="",
        tier="standard",
        command=CLAUDE_COMMAND_PREFIX + ("medium",),
    ),
    Route(
        route_id="claude-high",
        vendor="claude",
        workflow="",
        tier="high",
        command=CLAUDE_COMMAND_PREFIX + ("high",),
    ),
)


class RouteSelectionError(LookupError):
    """Raised when no policy route supports a request."""


def native_route_fingerprint(
    route: Route,
    allow_mixed_vendors: bool,
    posture: Posture | None = None,
) -> str:
    """Bind a rendered review to the selection it rendered.

    route 와 run 은 정책을 각각 따로 읽으므로, 사이에 정책이 바뀌면 검토한 것과
    다른 명령이 실행된다. run 에 이 지문을 넘기면 실행 직전에 다시 계산해
    비교하므로, 선택된 라우트·명령·벤더·티어·혼합 허용 여부 중 하나라도
    달라지면 실행되지 않는다.

    분류된 티어는 따로 넣지 않는다. select_tier_route 가 route.tier 와 같은
    라우트만 돌려주므로 이미 route 안에 들어 있고, 중복해서 넣으면 그 필드만
    독립적으로 검증할 수 없는 죽은 항목이 된다.

    묶는 것은 "선택 결과"이지 태스크가 아니다. 태스크를 묶으려면 해시를 남겨야
    하는데, 이 프로젝트는 태스크의 해시조차 금지한다. 따라서 같은 티어의 다른
    태스크는 같은 지문을 쓴다. --source-vendor 도 넣지 않는다. 선택에 영향을
    주지만 결과인 라우트에 이미 반영되어 있고, 넣으면 route 출력만으로는 지문을
    재계산할 수 없어 동일한 선택이 거부되는 오탐이 생긴다.
    """
    policy_semantics: dict[str, object] = {"allow_mixed_vendors": allow_mixed_vendors}
    if posture is not None:
        policy_semantics["posture"] = posture
    semantic_route = {
        "schema_version": NATIVE_FINGERPRINT_VERSION,
        "policy": policy_semantics,
        "route": {
            "id": route.route_id,
            "vendor": route.vendor,
            "workflow": route.workflow,
            "tier": route.tier,
            "command": list(route.command),
        },
    }
    encoded = json.dumps(
        semantic_route,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def select_route(routes: tuple[Route, ...], request: RouteRequest) -> Route:
    """Return the first policy route that exactly matches the request."""
    for route in routes:
        if route.vendor == request.vendor and route.workflow == request.workflow:
            return route
    raise RouteSelectionError("No supported route matches the request.")


def select_tier_route(
    routes: tuple[Route, ...],
    tier: Tier,
    source_vendor: str | None = None,
    allow_mixed_vendors: bool = False,
) -> Route:
    """Return the first tier route allowed for the originating vendor.

    source_vendor가 없어도 벤더는 하나로 고정한다. 고정하지 않으면 정책의 나열
    순서에 따라 난이도별로 벤더가 바뀌어, allow_mixed_vendors 옵트인 없이도
    태스크가 다른 벤더·다른 구독·다른 과금 경계로 넘어간다.
    """
    required_vendor = _required_vendor(routes, source_vendor, allow_mixed_vendors)
    for route in routes:
        if route.tier != tier:
            continue
        if required_vendor is not None and route.vendor != required_vendor:
            continue
        return route
    raise RouteSelectionError("No supported route matches the request.")


def _required_vendor(
    routes: tuple[Route, ...],
    source_vendor: str | None,
    allow_mixed_vendors: bool,
) -> str | None:
    """Return the vendor every candidate route must match, or None when mixing is allowed."""
    if allow_mixed_vendors:
        return None
    if source_vendor is not None:
        return source_vendor
    # 호출자가 벤더를 밝히지 않은 경우, 정책이 처음 선언한 티어 라우트의 벤더를
    # 그 정책의 벤더로 본다. 리뷰 가능한 값이므로 선택 결과가 결정적이다.
    # workflow 라우트는 티어 선택 후보가 아니므로 기준에서 제외한다. 포함하면
    # workflow 라우트를 먼저 선언한 정책의 모든 티어가 선택 불가능해진다.
    return next((route.vendor for route in routes if route.tier is not None), None)
