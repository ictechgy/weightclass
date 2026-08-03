"""Deterministic route selection for user-reviewable vendor commands."""

from dataclasses import dataclass
from typing import Final

from .classification import Tier


SUPPORTED_VENDORS: Final = frozenset({"claude", "codex"})
CLAUDE_COMMAND_PREFIX: Final = (
    "claude",
    "--print",
    "--no-session-persistence",
    "--permission-mode",
    "manual",
    "--effort",
)


@dataclass(frozen=True)
class Route:
    route_id: str
    vendor: str
    workflow: str
    command: tuple[str, ...]
    tier: Tier | None = None
    model: str | None = None


@dataclass(frozen=True)
class RouteRequest:
    vendor: str
    workflow: str


@dataclass(frozen=True)
class RoutingPolicy:
    routes: tuple[Route, ...]
    allow_mixed_vendors: bool = False


DEFAULT_ROUTES: Final = (
    Route(
        route_id="codex-low",
        vendor="codex",
        workflow="",
        tier="low",
        command=("codex", "exec", "--ephemeral", "--sandbox", "workspace-write", "-"),
    ),
    Route(
        route_id="codex-standard",
        vendor="codex",
        workflow="",
        tier="standard",
        command=("codex", "exec", "--ephemeral", "--sandbox", "workspace-write", "-"),
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
    Route(
        route_id="codex-high",
        vendor="codex",
        workflow="",
        tier="high",
        command=("codex", "exec", "--ephemeral", "--sandbox", "workspace-write", "-"),
    ),
)


class RouteSelectionError(LookupError):
    """Raised when no policy route supports a request."""


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
    # 호출자가 벤더를 밝히지 않은 경우, 정책이 처음 선언한 벤더를 그 정책의
    # 벤더로 본다. 첫 라우트는 리뷰 가능한 값이므로 선택 결과가 결정적이다.
    return routes[0].vendor if routes else None
