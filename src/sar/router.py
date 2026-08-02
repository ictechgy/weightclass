"""Deterministic route selection for user-reviewable vendor commands."""

from dataclasses import dataclass
from typing import Final


SUPPORTED_VENDORS: Final = frozenset({"claude", "codex"})


@dataclass(frozen=True)
class Route:
    route_id: str
    vendor: str
    workflow: str
    command: tuple[str, ...]


@dataclass(frozen=True)
class RouteRequest:
    vendor: str
    workflow: str


class RouteSelectionError(LookupError):
    """Raised when no policy route supports a request."""


def select_route(routes: tuple[Route, ...], request: RouteRequest) -> Route:
    """Return the first policy route that exactly matches the request."""
    for route in routes:
        if route.vendor == request.vendor and route.workflow == request.workflow:
            return route
    raise RouteSelectionError("No supported route matches the request.")
