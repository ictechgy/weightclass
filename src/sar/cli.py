"""Command-line interface for rendering, never executing, route commands."""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence, cast

from .classification import (
    InvalidTaskError,
    Tier,
    classify_task,
    read_task_from_standard_input,
)
from .router import (
    Route,
    RouteRequest,
    RouteSelectionError,
    RoutingPolicy,
    SUPPORTED_VENDORS,
    DEFAULT_ROUTES,
    select_route,
    select_tier_route,
)
from .v2 import (
    V2InvalidInputError,
    load_api_policy,
    render_api_route,
    route_fingerprint,
    select_api_route,
    validate_api_runtime,
)


class InvalidInputError(ValueError):
    """Raised for invalid policy or descriptor data without exposing it."""


class SafeArgumentParser(argparse.ArgumentParser):
    """Avoid including caller-provided values in diagnostics."""

    def error(self, message: str) -> None:
        del message
        raise InvalidInputError()


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidInputError() from error
    if not isinstance(value, dict):
        raise InvalidInputError()
    return value


def _require_exact_keys(value: dict[str, Any], expected_keys: set[str]) -> None:
    if set(value) != expected_keys:
        raise InvalidInputError()


def _require_nonempty_string(value: object) -> str:
    if not isinstance(value, str) or not value or any(character.isspace() for character in value):
        raise InvalidInputError()
    return value


def _parse_route(value: object) -> Route:
    if not isinstance(value, dict):
        raise InvalidInputError()

    keys = set(value)
    has_workflow = keys in (
        {"id", "vendor", "workflow", "command"},
        {"id", "vendor", "workflow", "command", "model"},
    )
    has_tier = keys in (
        {"id", "vendor", "tier", "command"},
        {"id", "vendor", "tier", "command", "model"},
    )
    if not has_workflow and not has_tier:
        raise InvalidInputError()

    route_id = _require_nonempty_string(value["id"])
    vendor = _require_nonempty_string(value["vendor"])
    command = value["command"]
    if vendor not in SUPPORTED_VENDORS or not isinstance(command, list) or not command:
        raise InvalidInputError()

    workflow = _require_nonempty_string(value["workflow"]) if has_workflow else ""
    tier: Tier | None = None
    if has_tier:
        parsed_tier = _require_nonempty_string(value["tier"])
        if parsed_tier not in {"low", "standard", "high"}:
            raise InvalidInputError()
        tier = cast(Tier, parsed_tier)
    parsed_command = tuple(_require_nonempty_string(argument) for argument in command)

    model: str | None = None
    if "model" in value:
        model = _require_nonempty_string(value["model"])
        # `wclass route` 출력의 model 필드는 리뷰어가 승인 근거로 읽는 값이다.
        # 실행되는 것은 command뿐이므로, 둘이 어긋나면 리뷰한 것과 다른 모델이
        # 실행된다. 정합성을 로드 시점에 강제해 리뷰 산출물을 신뢰할 수 있게 한다.
        if model not in parsed_command:
            raise InvalidInputError()

    return Route(
        route_id=route_id,
        vendor=vendor,
        workflow=workflow,
        command=parsed_command,
        tier=tier,
        model=model,
    )


def load_routes(policy_path: Path) -> tuple[Route, ...]:
    """Load a strictly shaped, trusted-local route policy."""
    return load_routing_policy(policy_path).routes


def load_routing_policy(policy_path: Path) -> RoutingPolicy:
    """Load routing options and strictly shaped trusted-local routes."""
    policy = _read_json_object(policy_path)
    if set(policy) not in ({"routes"}, {"routes", "allow_mixed_vendors"}):
        raise InvalidInputError()
    allow_mixed_vendors = policy.get("allow_mixed_vendors", False)
    if not isinstance(allow_mixed_vendors, bool):
        raise InvalidInputError()
    routes = policy["routes"]
    if not isinstance(routes, list) or not routes:
        raise InvalidInputError()

    parsed_routes = tuple(_parse_route(route) for route in routes)
    if len({route.route_id for route in parsed_routes}) != len(parsed_routes):
        raise InvalidInputError()
    return RoutingPolicy(parsed_routes, allow_mixed_vendors)


def load_request(descriptor_path: Path) -> RouteRequest:
    """Load a redacted descriptor without task content or credentials."""
    descriptor = _read_json_object(descriptor_path)
    _require_exact_keys(descriptor, {"vendor", "workflow"})
    vendor = _require_nonempty_string(descriptor["vendor"])
    if vendor not in SUPPORTED_VENDORS:
        raise InvalidInputError()
    return RouteRequest(vendor=vendor, workflow=_require_nonempty_string(descriptor["workflow"]))


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(description="Render a supported native workflow command.")
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--descriptor", required=True, type=Path)
    return parser


def build_route_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(description="Select a command for a task read from stdin.")
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--source-vendor", choices=sorted(SUPPORTED_VENDORS))
    return parser


def build_v2_route_parser() -> argparse.ArgumentParser:
    """Parse an explicit V2 API route review request."""
    parser = SafeArgumentParser(description="Review a declarative API route.")
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--source-vendor", required=True, choices=sorted(SUPPORTED_VENDORS))
    parser.add_argument("--api-runtime", required=True, type=Path)
    return parser


def build_v2_run_parser() -> argparse.ArgumentParser:
    """Parse an explicit V2 API execution request."""
    parser = build_v2_route_parser()
    parser.description = "Run a reviewed declarative API route."
    parser.add_argument("--confirm-api-egress", action="store_true")
    parser.add_argument("--ack-route-fingerprint")
    return parser


def v2_route_from_standard_input(
    policy_path: Path,
    source_vendor: str,
    runtime_path: Path,
) -> int:
    """Render an API review descriptor without sending data to a provider."""
    try:
        runtime_path = validate_api_runtime(runtime_path)
        task = read_task_from_standard_input()
        policy = load_api_policy(policy_path)
        tier, route = select_api_route(task, policy, source_vendor)
    except InvalidTaskError:
        print(json.dumps({"error": "invalid_task"}), file=sys.stderr)
        return 2
    except (V2InvalidInputError, InvalidInputError):
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    except RouteSelectionError:
        print(json.dumps({"error": "unsupported_route"}), file=sys.stderr)
        return 3
    print(json.dumps(render_api_route(route, policy, tier, source_vendor, runtime_path)))
    return 0


def v2_run_from_standard_input(
    policy_path: Path,
    source_vendor: str,
    runtime_path: Path,
    confirm_api_egress: bool,
    acknowledged_fingerprint: str | None,
) -> int:
    """Start one acknowledged external API runtime without handling credentials."""
    try:
        runtime_path = validate_api_runtime(runtime_path)
        task = read_task_from_standard_input()
        policy = load_api_policy(policy_path)
        tier, route = select_api_route(task, policy, source_vendor)
    except InvalidTaskError:
        print(json.dumps({"error": "invalid_task"}), file=sys.stderr)
        return 2
    except (V2InvalidInputError, InvalidInputError):
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    except RouteSelectionError:
        print(json.dumps({"error": "unsupported_route"}), file=sys.stderr)
        return 3

    if not confirm_api_egress:
        print(json.dumps({"error": "api_confirmation_required"}), file=sys.stderr)
        return 5
    if acknowledged_fingerprint != route_fingerprint(
        route,
        policy,
        tier,
        source_vendor,
        runtime_path,
    ):
        print(json.dumps({"error": "route_fingerprint_mismatch"}), file=sys.stderr)
        return 6
    try:
        # 로케일 인코딩을 쓰는 text 모드는 비ASCII 태스크에서 UnicodeEncodeError를 내고,
        # 그 예외 메시지가 태스크 문자와 위치를 진단에 노출한다. 항상 UTF-8 바이트로 넘긴다.
        completed_process = subprocess.run(
            (
                str(runtime_path),
                "--provider",
                route.provider,
                "--model",
                route.model,
                "--effort",
                route.effort,
            ),
            check=False,
            input=task.encode("utf-8"),
        )
    except OSError:
        print(json.dumps({"error": "executor_unavailable"}), file=sys.stderr)
        return 4
    return completed_process.returncode


def classify_from_standard_input() -> int:
    """Classify a task read from stdin without echoing or persisting it."""
    try:
        tier = classify_task(read_task_from_standard_input())
    except InvalidTaskError:
        print(json.dumps({"error": "invalid_task"}), file=sys.stderr)
        return 2
    print(json.dumps({"tier": tier}))
    return 0


def select_task_route(
    task: str,
    policy_path: Path | None,
    source_vendor: str | None = None,
) -> tuple[Tier, Route]:
    """Classify a task and select its route without retaining task content."""
    tier = classify_task(task)
    policy = (
        load_routing_policy(policy_path)
        if policy_path is not None
        else RoutingPolicy(DEFAULT_ROUTES)
    )
    return tier, select_tier_route(
        policy.routes,
        tier,
        source_vendor,
        policy.allow_mixed_vendors,
    )


def route_from_standard_input(policy_path: Path | None, source_vendor: str | None) -> int:
    """Select and render a command without echoing or persisting the task."""
    try:
        tier, route = select_task_route(
            read_task_from_standard_input(), policy_path, source_vendor
        )
    except InvalidTaskError:
        print(json.dumps({"error": "invalid_task"}), file=sys.stderr)
        return 2
    except InvalidInputError:
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    except RouteSelectionError:
        print(json.dumps({"error": "unsupported_route"}), file=sys.stderr)
        return 3
    # vendor는 항상 싣는다. 생략하면 정책이 벤더를 바꿔도 리뷰 출력만 봐서는
    # 어느 벤더로 나가는지 알 수 없다.
    response = {
        "command": list(route.command),
        "route": route.route_id,
        "tier": tier,
        "vendor": route.vendor,
    }
    if route.model is not None:
        response["model"] = route.model
    print(json.dumps(response))
    return 0


def run_from_standard_input(policy_path: Path | None, source_vendor: str | None) -> int:
    """Run a selected native command without a shell or output capture."""
    try:
        task = read_task_from_standard_input()
        _, route = select_task_route(task, policy_path, source_vendor)
        # text 모드는 로케일 인코딩을 사용하므로 LC_ALL=C 환경에서 비ASCII 태스크가
        # UnicodeEncodeError로 새어 나간다. 자식 출력을 읽지 않으므로 바이트로 전달한다.
        completed_process = subprocess.run(
            route.command,
            check=False,
            input=task.encode("utf-8"),
        )
    except InvalidTaskError:
        print(json.dumps({"error": "invalid_task"}), file=sys.stderr)
        return 2
    except InvalidInputError:
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    except RouteSelectionError:
        print(json.dumps({"error": "unsupported_route"}), file=sys.stderr)
        return 3
    except OSError:
        print(json.dumps({"error": "executor_unavailable"}), file=sys.stderr)
        return 4
    return completed_process.returncode


def main(argv: Sequence[str] | None = None) -> int:
    """Classify, route, render, or run a native command from explicit input."""
    provided_arguments = list(sys.argv[1:] if argv is None else argv)
    if provided_arguments == ["classify"]:
        return classify_from_standard_input()
    if provided_arguments and provided_arguments[0] == "route":
        try:
            arguments = build_route_parser().parse_args(provided_arguments[1:])
        except InvalidInputError:
            print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
            return 2
        return route_from_standard_input(arguments.policy, arguments.source_vendor)
    if provided_arguments and provided_arguments[0] == "run":
        try:
            arguments = build_route_parser().parse_args(provided_arguments[1:])
        except InvalidInputError:
            print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
            return 2
        return run_from_standard_input(arguments.policy, arguments.source_vendor)
    if len(provided_arguments) >= 2 and provided_arguments[:2] == ["v2", "route"]:
        try:
            arguments = build_v2_route_parser().parse_args(provided_arguments[2:])
        except InvalidInputError:
            print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
            return 2
        return v2_route_from_standard_input(
            arguments.policy,
            arguments.source_vendor,
            arguments.api_runtime,
        )
    if len(provided_arguments) >= 2 and provided_arguments[:2] == ["v2", "run"]:
        try:
            arguments = build_v2_run_parser().parse_args(provided_arguments[2:])
        except InvalidInputError:
            print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
            return 2
        return v2_run_from_standard_input(
            arguments.policy,
            arguments.source_vendor,
            arguments.api_runtime,
            arguments.confirm_api_egress,
            arguments.ack_route_fingerprint,
        )
    try:
        arguments = build_parser().parse_args(provided_arguments)
        route = select_route(load_routes(arguments.policy), load_request(arguments.descriptor))
    except InvalidInputError:
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    except RouteSelectionError:
        print(json.dumps({"error": "unsupported_route"}), file=sys.stderr)
        return 3
    print(json.dumps({"command": list(route.command), "route": route.route_id}))
    return 0
