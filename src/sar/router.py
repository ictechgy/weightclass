"""Deterministic route selection for user-reviewable vendor commands."""

from dataclasses import dataclass
from typing import Final

from .classification import Tier


SUPPORTED_VENDORS: Final = frozenset({"claude", "codex"})


@dataclass(frozen=True)
class Route:
    route_id: str
    vendor: str
    workflow: str
    command: tuple[str, ...]
    tier: Tier | None = None


@dataclass(frozen=True)
class RouteRequest:
    vendor: str
    workflow: str


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
        route_id="claude-high",
        vendor="claude",
        workflow="",
        tier="high",
        command=(
            "claude",
            "--print",
            "--no-session-persistence",
            "--permission-mode",
            "manual",
            "--effort",
            "high",
        ),
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


def select_tier_route(routes: tuple[Route, ...], tier: Tier) -> Route:
    """Return the first route configured for the classified effort tier."""
    for route in routes:
        if route.tier == tier:
            return route
    raise RouteSelectionError("No supported route matches the request.")
