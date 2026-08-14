"""Task-free discovery of package-supported native agent executables."""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass
from typing import Literal

from .router import CLAUDE_COMMAND_PREFIX, agy_command, codex_command, grok_command

DiscoveryTaskDelivery = Literal["stdin", "argv"]

MAX_PATH_BYTES = 32_768
MAX_PATH_ENTRIES = 256


class AgentDiscoveryError(ValueError):
    """A value-free local discovery failure."""


class AgentUnavailableError(LookupError):
    """Raised when a selected package-supported executable is not detected."""


@dataclass(frozen=True, slots=True)
class AgentAdapter:
    agent: str
    executable_name: str
    task_delivery: DiscoveryTaskDelivery
    accepts_opaque_model_override: bool


AGENT_ADAPTERS = (
    AgentAdapter("agy", "agy", "argv", False),
    AgentAdapter("claude", "claude", "stdin", True),
    AgentAdapter("codex", "codex", "stdin", True),
    AgentAdapter("grok", "grok", "argv", True),
)
AGENT_IDS = tuple(adapter.agent for adapter in AGENT_ADAPTERS)
TIERS = ("low", "standard", "high")
EFFORTS = ("low", "medium", "high")
MAX_MODEL_LABEL_BYTES = 240


def _path_entries(path_value: str) -> tuple[str, ...]:
    try:
        encoded = path_value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise AgentDiscoveryError() from None
    entries = path_value.split(os.pathsep)
    if len(encoded) > MAX_PATH_BYTES or len(entries) > MAX_PATH_ENTRIES:
        raise AgentDiscoveryError()
    return tuple(entry for entry in entries if entry and os.path.isabs(entry))


def _reviewable_path(value: str) -> bool:
    return value == value.strip(" ") and not any(
        unicodedata.category(character).startswith("C")
        or (character.isspace() and character != " ")
        for character in value
    )


def _find_executable(name: str, entries: tuple[str, ...]) -> str | None:
    for directory in entries:
        candidate = os.path.join(directory, name)
        if not _reviewable_path(candidate):
            continue
        try:
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        except OSError:
            continue
    return None


def render_agent_discovery(
    path_value: str | None = None,
    *,
    agent: str | None = None,
) -> dict[str, object]:
    """Describe locally detected built-in agents without starting a process."""
    if agent is not None and agent not in AGENT_IDS:
        raise AgentDiscoveryError()
    entries = _path_entries(os.environ.get("PATH", "") if path_value is None else path_value)
    agents: list[dict[str, object]] = []
    for adapter in AGENT_ADAPTERS:
        if agent is not None and adapter.agent != agent:
            continue
        executable = _find_executable(adapter.executable_name, entries)
        agents.append(
            {
                "agent": adapter.agent,
                "executable": executable,
                "executable_detected": executable is not None,
                "task_delivery": adapter.task_delivery,
                "model_catalog": {
                    "source": "package_default_only",
                    "values": ["default"],
                    "accepts_opaque_override": adapter.accepts_opaque_model_override,
                    "availability_verified": False,
                },
                "effort_catalog": {
                    "source": "package_catalog",
                    "values": ["low", "medium", "high"],
                    "availability_verified": False,
                },
                "subscription": "unknown",
                "pricing": "unknown",
                "quota": "unknown",
            }
        )
    return {
        "schema_version": 1,
        "discovery_mode": "local_path_only",
        "network_used": False,
        "vendor_processes_started": False,
        "agents": agents,
    }


def _model_label(value: object) -> str:
    if not isinstance(value, str):
        raise AgentDiscoveryError()
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise AgentDiscoveryError() from None
    if (
        not 1 <= len(encoded) <= MAX_MODEL_LABEL_BYTES
        or value.startswith("-")
        or any(character.isspace() or not character.isprintable() for character in value)
    ):
        raise AgentDiscoveryError()
    return value


def _selected_command(
    adapter: AgentAdapter,
    executable: str,
    model: str,
    effort: str,
) -> list[str]:
    if adapter.agent == "codex":
        command = list(codex_command(effort))
        if model != "default":
            command[command.index("-c") : command.index("-c")] = ["--model", model]
    elif adapter.agent == "claude":
        command = list(CLAUDE_COMMAND_PREFIX + (effort,))
        if model != "default":
            insertion = command.index("--effort")
            command[insertion:insertion] = ["--model", model]
    elif adapter.agent == "agy":
        if model != "default":
            raise AgentDiscoveryError()
        command = list(agy_command(effort))
    elif adapter.agent == "grok":
        command = list(grok_command(effort))
        if model != "default":
            insertion = command.index("--reasoning-effort")
            command[insertion:insertion] = ["--model", model]
    else:
        raise AgentDiscoveryError()
    command[0] = executable
    return command


def generate_selected_policy(
    *,
    agent: str,
    tier: str,
    model: str,
    effort: str,
    allow_cross_vendor: bool,
    path_value: str | None = None,
) -> dict[str, object]:
    """Compile one selected built-in agent profile into a schema-1 policy."""
    if tier not in TIERS or effort not in EFFORTS or not isinstance(allow_cross_vendor, bool):
        raise AgentDiscoveryError()
    selected_model = _model_label(model)
    adapter = next((item for item in AGENT_ADAPTERS if item.agent == agent), None)
    if adapter is None:
        raise AgentDiscoveryError()
    entries = _path_entries(os.environ.get("PATH", "") if path_value is None else path_value)
    executable = _find_executable(adapter.executable_name, entries)
    if executable is None:
        raise AgentUnavailableError()
    command = _selected_command(adapter, executable, selected_model, effort)
    return {
        "schema_version": 1,
        "allow_mixed_vendors": allow_cross_vendor,
        "posture": "balanced",
        "routes": [
            {
                "id": f"selected-{adapter.agent}-{tier}",
                "vendor": adapter.agent,
                "tier": tier,
                "command": command,
            }
        ],
    }
