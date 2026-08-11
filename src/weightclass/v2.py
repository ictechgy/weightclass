"""Declarative V2 API routing without credential or network access."""

import hashlib
import json
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final, cast

from .classification import Tier, classify_task
from .executable_observation import ExecutableObservation, observe_executable
from .json_input import JsonInputError, load_json_object
from .router import RouteSelectionError
from .v2_validation import V2ValidationError

POLICY_SCHEMA_VERSION: Final = 2
MAX_POLICY_BYTES: Final = 262_144
MAX_LABEL_LENGTH: Final = 240
SUPPORTED_PROVIDERS: Final = frozenset({"openai", "anthropic"})
SOURCE_PROVIDER: Final = {"codex": "openai", "claude": "anthropic"}

# API 경로는 벤더에서 provider 를 유도해 교차-provider 를 차단한다. 그 매핑이 없는
# 벤더는 어디로 과금되는지 판단할 근거가 없으므로 이 경로에서 제외한다. 네이티브
# 경로의 BUILT_IN_VENDORS 와 일부러 분리한다.
API_SOURCE_VENDORS: Final = frozenset(SOURCE_PROVIDER)


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
    """Require one reviewable policy label.

    model 과 effort 는 런타임에 argv 로 전달되므로 명령 인자와 같은 기준을
    적용한다. 제어문자와 서식 문자는 검토 출력에 드러나지 않고, 서로게이트는
    exec 단계에서 UnicodeEncodeError 로 터져 진단 없이 트레이스백을 남긴다.
    """
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_LABEL_LENGTH
        or value != value.strip()
        or any(
            unicodedata.category(character).startswith("C")
            or (character.isspace() and character != " ")
            for character in value
        )
    ):
        raise V2InvalidInputError()
    return value


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        return load_json_object(
            path,
            max_bytes=MAX_POLICY_BYTES,
            require_exclusive_write_owner=True,
        )
    except JsonInputError:
        raise V2InvalidInputError() from None


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
    if any(source not in API_SOURCE_VENDORS for source in parsed_sources) or len(
        set(parsed_sources)
    ) != len(parsed_sources):
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
    if not isinstance(policy["allow_cross_provider"], bool) or not isinstance(
        policy["allow_api"], bool
    ):
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
    """Return the resolved executable path used for API review and execution."""
    return Path(observe_api_runtime(path).lexical_path)


def observe_api_runtime(path: Path) -> ExecutableObservation:
    """Observe a resolved API runtime without allowing a final symlink at spawn.

    The CLI executes the returned resolved path, rather than the caller's
    symlink spelling. The observation becomes part of the review fingerprint so
    an ordinary replacement between ``route`` and ``run`` invalidates the
    acknowledgement before the task reaches the runtime.
    """
    if not path.is_absolute():
        raise V2InvalidInputError()
    try:
        resolved_path = path.resolve(strict=True)
        return observe_executable(str(resolved_path))
    except (OSError, RuntimeError, V2ValidationError, ValueError):
        raise V2InvalidInputError() from None


def select_api_route(
    task: str,
    policy: ApiRoutingPolicy,
    source_vendor: str,
) -> tuple[Tier, ApiRoute]:
    """Select the first route compatible with tier, source, and provider policy."""
    tier = classify_task(task)
    if source_vendor not in API_SOURCE_VENDORS or not policy.allow_api:
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
    runtime_observation: ExecutableObservation,
) -> str:
    """Bind review acknowledgement to all non-secret route semantics."""
    semantic_route = {
        "schema_version": POLICY_SCHEMA_VERSION,
        "tier": tier,
        "source_vendor": source_vendor,
        "runtime_path": str(runtime_path),
        "runtime_identity": asdict(runtime_observation),
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
    runtime_observation: ExecutableObservation,
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
        "runtime_identity": asdict(runtime_observation),
        "route_fingerprint": route_fingerprint(
            route,
            policy,
            tier,
            source_vendor,
            runtime_path,
            runtime_observation,
        ),
    }
