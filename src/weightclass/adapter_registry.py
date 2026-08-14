"""Package-known facts for built-in native agent adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

TaskDelivery = Literal["stdin", "argv"]


@dataclass(frozen=True, slots=True)
class BuiltInAdapter:
    """Static adapter facts; none imply runtime or account availability."""

    agent: str
    executable_name: str
    task_delivery: TaskDelivery
    accepts_opaque_model_override: bool
    models: tuple[str, ...] = ("default",)
    efforts: tuple[str, ...] = ("low", "medium", "high")


BUILT_IN_ADAPTERS: Final = (
    BuiltInAdapter("agy", "agy", "argv", False),
    BuiltInAdapter("claude", "claude", "stdin", True),
    BuiltInAdapter("codex", "codex", "stdin", True),
    BuiltInAdapter("grok", "grok", "argv", True),
)
BUILT_IN_AGENT_IDS: Final = tuple(adapter.agent for adapter in BUILT_IN_ADAPTERS)
