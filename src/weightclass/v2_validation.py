"""Side-effect-free validation and canonical list-ordering primitives for schema 2."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import TypeVar, cast


class V2ValidationError(ValueError):
    """Value-free rejection for structurally invalid schema-2 input."""


_T = TypeVar("_T")


NATIVE_LIST_PATHS: dict[str, str] = {
    "/route/eligibility": "source_vendor,source_profile_id,tier",
    "/target/allowed_model_effort_pairs": "model,effort",
    "/argv": "ordered",
    "/transition/changed_dimensions": "profile,vendor",
    "/transition/authorizations": "aligned-with-changed-dimensions",
}

DELEGATION_LIST_PATHS: dict[str, str] = {
    "/workflow/eligibility": "source_vendor_family,source_profile_id,tier",
    "/runtime/adapter/supported_transports": "lexical",
    "/runtime/adapter/capabilities": "lexical",
    "/profiles": "id",
    "/profiles/*/capabilities": "lexical",
    "/profiles/*/allowed_model_effort_pairs": "model,effort",
    "/tasks": "id",
    "/tasks/*/inputs": "id",
    "/tasks/*/projections": "id",
    "/tasks/*/outputs": "id",
    "/tasks/*/outputs/*/artifacts": "id",
    "/tasks/*/mutable_scopes": "lexical",
    "/tasks/*/capabilities": "lexical",
    "/tasks/*/request/permissions": "filesystem,commands",
    "/tasks/*/request/tools": "id",
    "/dependency_edges": "id",
    "/gate_edges": "id",
    "/transitions": "kind,id",
    "/transitions/*/changed_dimensions": (
        "provider,intended_recipient,billing_boundary,transport,profile"
    ),
    "/transitions/*/authorizations": (
        "provider,intended_recipient,billing_boundary,transport,profile"
    ),
    "/grants/provider": "id",
    "/grants/intended_recipient": "id",
    "/grants/billing_boundary": "id",
    "/grants/transport": "id",
    "/grants/profile": "id",
    "/argv": "ordered",
}


def require_exact_keys(value: object, expected: set[str] | frozenset[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != set(expected):
        raise V2ValidationError()
    if not all(isinstance(key, str) for key in value):
        raise V2ValidationError()
    return value


def require_integer(value: object, *, lower: int, upper: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
        raise V2ValidationError()
    return value


def require_string(value: object, *, max_bytes: int, minimum_bytes: int = 1) -> str:
    if not isinstance(value, str):
        raise V2ValidationError()
    try:
        size = len(value.encode("utf-8", errors="strict"))
    except UnicodeEncodeError as error:
        raise V2ValidationError() from error
    if not minimum_bytes <= size <= max_bytes:
        raise V2ValidationError()
    return value


def _registry_path(path: str) -> str:
    return "/".join("*" if component.isdigit() else component for component in path.split("/"))


def _sort_key(item: object, ordering: str) -> tuple[str, ...]:
    if ordering == "lexical":
        if not isinstance(item, str):
            raise V2ValidationError()
        return (item,)
    fields = ordering.split(",")
    if isinstance(item, str):
        try:
            return (f"{fields.index(item):03d}",)
        except ValueError as error:
            raise V2ValidationError() from error
    if not isinstance(item, Mapping):
        raise V2ValidationError()
    if all(field in item for field in fields):
        values = tuple(item[field] for field in fields)
        if not all(isinstance(value, str) for value in values):
            raise V2ValidationError()
        return cast(tuple[str, ...], values)
    dimension = item.get("dimension")
    if isinstance(dimension, str):
        try:
            return (f"{fields.index(dimension):03d}",)
        except ValueError as error:
            raise V2ValidationError() from error
    raise V2ValidationError()


def canonicalize_registered_lists(
    value: _T,
    registry: Mapping[str, str],
    *,
    encountered_paths: Sequence[str] | None = None,
) -> _T:
    """Return a deep copy with every encountered registered list canonically ordered."""
    if any(not path.startswith("/") or not ordering for path, ordering in registry.items()):
        raise V2ValidationError()
    if encountered_paths is not None:
        if any(_registry_path(path) not in registry for path in encountered_paths):
            raise V2ValidationError()

    copied = copy.deepcopy(value)

    def visit(node: object, path: str) -> object:
        if isinstance(node, dict):
            return {key: visit(child, f"{path}/{key}") for key, child in node.items()}
        if isinstance(node, list):
            registered_path = _registry_path(path)
            ordering = registry.get(registered_path)
            if ordering is None:
                raise V2ValidationError()
            visited = [visit(child, f"{path}/{index}") for index, child in enumerate(node)]
            if ordering == "ordered":
                return visited
            if ordering == "aligned-with-changed-dimensions":
                ordering = "profile,vendor"
            try:
                return sorted(visited, key=lambda item: _sort_key(item, ordering))
            except (KeyError, TypeError, ValueError) as error:
                raise V2ValidationError() from error
        return node

    return cast(_T, visit(copied, ""))
