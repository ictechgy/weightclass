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
    """Return the first tier route allowed for the originating vendor."""
    for route in routes:
        if route.tier != tier:
            continue
        if source_vendor is not None and not allow_mixed_vendors and route.vendor != source_vendor:
            continue
        return route
    raise RouteSelectionError("No supported route matches the request.")
