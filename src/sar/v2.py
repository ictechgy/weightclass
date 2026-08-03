"""Declarative V2 API routing without credential or network access."""

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Final, cast

from .classification import Tier, classify_task
from .router import RouteSelectionError, SUPPORTED_VENDORS


POLICY_SCHEMA_VERSION: Final = 2
MAX_POLICY_BYTES: Final = 262_144
MAX_LABEL_LENGTH: Final = 240
SUPPORTED_PROVIDERS: Final = frozenset({"openai", "anthropic"})
SOURCE_PROVIDER: Final = {"codex": "openai", "claude": "anthropic"}


class V2InvalidInputError(ValueError):
    """Raised for unsafe V2 input without including the original value."""


@dataclass(frozen=True)
class ApiRoute:
    route_id: str
    tier: Tier
    eligible_source_vendors: tuple[str, ...]
    provider: str
    transport: str
    model: str
    effort: str
    intended_recipient: str
    intended_billing_boundary: str


@dataclass(frozen=True)
class ApiRoutingPolicy:
    routes: tuple[ApiRoute, ...]
    allow_cross_provider: bool
    allow_api: bool


def _require_label(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_LABEL_LENGTH
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise V2InvalidInputError()
    return value


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_POLICY_BYTES:
            raise V2InvalidInputError()
        with path.open("r", encoding="utf-8") as policy_file:
            contents = policy_file.read(MAX_POLICY_BYTES + 1)
        if len(contents.encode("utf-8")) > MAX_POLICY_BYTES:
            raise V2InvalidInputError()
        value = json.loads(contents)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise V2InvalidInputError() from error
    if not isinstance(value, dict):
        raise V2InvalidInputError()
    return value


def _parse_route(value: object) -> ApiRoute:
    expected_keys = {
        "id",
        "tier",
        "eligible_source_vendors",
        "provider",
        "transport",
        "model",
        "effort",
        "intended_recipient",
        "intended_billing_boundary",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise V2InvalidInputError()
    tier = _require_label(value["tier"])
    if tier not in {"low", "standard", "high"}:
        raise V2InvalidInputError()
    eligible_sources = value["eligible_source_vendors"]
    if not isinstance(eligible_sources, list) or not eligible_sources:
        raise V2InvalidInputError()
    parsed_sources = tuple(_require_label(source) for source in eligible_sources)
    if (
        any(source not in SUPPORTED_VENDORS for source in parsed_sources)
        or len(set(parsed_sources)) != len(parsed_sources)
    ):
        raise V2InvalidInputError()
    provider = _require_label(value["provider"])
    if provider not in SUPPORTED_PROVIDERS:
        raise V2InvalidInputError()
    transport = _require_label(value["transport"])
    if transport != "api":
        raise V2InvalidInputError()
    return ApiRoute(
        route_id=_require_label(value["id"]),
        tier=cast(Tier, tier),
        eligible_source_vendors=parsed_sources,
        provider=provider,
        transport=transport,
        model=_require_label(value["model"]),
        effort=_require_label(value["effort"]),
        intended_recipient=_require_label(value["intended_recipient"]),
        intended_billing_boundary=_require_label(value["intended_billing_boundary"]),
    )


def load_api_policy(path: Path) -> ApiRoutingPolicy:
    """Load a bounded, declarative API policy without executable commands."""
    policy = _read_json_object(path)
    expected_keys = {"schema_version", "allow_cross_provider", "allow_api", "routes"}
    if set(policy) != expected_keys or policy["schema_version"] != POLICY_SCHEMA_VERSION:
        raise V2InvalidInputError()
    if not isinstance(policy["schema_version"], int) or isinstance(policy["schema_version"], bool):
        raise V2InvalidInputError()
    if not isinstance(policy["allow_cross_provider"], bool) or not isinstance(policy["allow_api"], bool):
        raise V2InvalidInputError()
    routes = policy["routes"]
    if not isinstance(routes, list) or not routes:
        raise V2InvalidInputError()
    parsed_routes = tuple(_parse_route(route) for route in routes)
    if len({route.route_id for route in parsed_routes}) != len(parsed_routes):
        raise V2InvalidInputError()
    return ApiRoutingPolicy(
        routes=parsed_routes,
        allow_cross_provider=policy["allow_cross_provider"],
        allow_api=policy["allow_api"],
    )


def validate_api_runtime(path: Path) -> Path:
    """Require a user-supplied absolute executable runtime path."""
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise V2InvalidInputError()
    return path


def select_api_route(
    task: str,
    policy: ApiRoutingPolicy,
    source_vendor: str,
) -> tuple[Tier, ApiRoute]:
    """Select the first route compatible with tier, source, and provider policy."""
    tier = classify_task(task)
    if source_vendor not in SUPPORTED_VENDORS or not policy.allow_api:
        raise RouteSelectionError()
    for route in policy.routes:
        if route.tier != tier or source_vendor not in route.eligible_source_vendors:
            continue
        if not policy.allow_cross_provider and SOURCE_PROVIDER[source_vendor] != route.provider:
            continue
        return tier, route
    raise RouteSelectionError()


def route_fingerprint(
    route: ApiRoute,
    policy: ApiRoutingPolicy,
    tier: Tier,
    source_vendor: str,
    runtime_path: Path,
) -> str:
    """Bind review acknowledgement to all non-secret route semantics."""
    semantic_route = {
        "schema_version": POLICY_SCHEMA_VERSION,
        "tier": tier,
        "source_vendor": source_vendor,
        "runtime_path": str(runtime_path),
        "policy": {
            "allow_api": policy.allow_api,
            "allow_cross_provider": policy.allow_cross_provider,
        },
        "route": asdict(route),
    }
    encoded = json.dumps(
        semantic_route,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def render_api_route(
    route: ApiRoute,
    policy: ApiRoutingPolicy,
    tier: Tier,
    source_vendor: str,
    runtime_path: Path,
) -> dict[str, object]:
    """Render the review descriptor, deliberately excluding task text and credentials."""
    return {
        "route": route.route_id,
        "tier": tier,
        "source_vendor": source_vendor,
        "destination": {
            "provider": route.provider,
            "transport": route.transport,
            "model": route.model,
            "effort": route.effort,
            "intended_recipient": route.intended_recipient,
            "intended_billing_boundary": route.intended_billing_boundary,
        },
        "runtime_path": str(runtime_path),
        "route_fingerprint": route_fingerprint(
            route,
            policy,
            tier,
            source_vendor,
            runtime_path,
        ),
    }
