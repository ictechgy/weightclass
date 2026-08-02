"""Command-line interface for rendering, never executing, route commands."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .router import Route, RouteRequest, RouteSelectionError, SUPPORTED_VENDORS, select_route


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
    _require_exact_keys(value, {"id", "vendor", "workflow", "command"})

    route_id = _require_nonempty_string(value["id"])
    vendor = _require_nonempty_string(value["vendor"])
    workflow = _require_nonempty_string(value["workflow"])
    command = value["command"]
    if vendor not in SUPPORTED_VENDORS or not isinstance(command, list) or not command:
        raise InvalidInputError()

    return Route(
        route_id=route_id,
        vendor=vendor,
        workflow=workflow,
        command=tuple(_require_nonempty_string(argument) for argument in command),
    )


def load_routes(policy_path: Path) -> tuple[Route, ...]:
    """Load a strictly shaped, trusted-local route policy."""
    policy = _read_json_object(policy_path)
    _require_exact_keys(policy, {"routes"})
    routes = policy["routes"]
    if not isinstance(routes, list) or not routes:
        raise InvalidInputError()

    parsed_routes = tuple(_parse_route(route) for route in routes)
    if len({route.route_id for route in parsed_routes}) != len(parsed_routes):
        raise InvalidInputError()
    return parsed_routes


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


def main(argv: Sequence[str] | None = None) -> int:
    """Render the selected command as JSON; never invoke a vendor executable."""
    try:
        arguments = build_parser().parse_args(argv)
        route = select_route(load_routes(arguments.policy), load_request(arguments.descriptor))
    except InvalidInputError:
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    except RouteSelectionError:
        print(json.dumps({"error": "unsupported_route"}), file=sys.stderr)
        return 3
    print(json.dumps({"command": list(route.command), "route": route.route_id}))
    return 0
