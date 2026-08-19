"""Controlling-console schema-3 policy selector.

The selector only reads the injected controlling console.  It never reads task
stdin and never constructs a vendor command from user input; command templates
come from the closed schema-3 compiler.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TextIO

from .adapter_registry import BUILT_IN_ADAPTERS, BuiltInAdapter
from .agent_discovery import _find_executable, _path_entries
from .canonical_v2 import canonical_json_bytes_v2
from .executable_observation import ExecutableObservation, observe_executable
from .native_v3_compile import compile_native_policy_v3
from .native_v3_schema import (
    NativePolicyV3,
    parse_native_policy_v3,
    validate_label,
    validate_native_selector_v3,
    validate_opaque_token,
)
from .v2_validation import V2ValidationError


class InteractiveSelectorError(ValueError):
    """Value-free rejection of invalid interactive choices."""


class _SelectorCancelled(Exception):
    """Internal EOF signal; cancellation never emits a policy."""


MAX_CONSOLE_LINE_CHARACTERS = 4_096
MAX_NUMERIC_CHOICE_CHARACTERS = 32
_EFFORT_CHOICES = ("low", "medium", "high")
_BUILDER_FOR = {
    "agy": "agy-print-v1",
    "claude": "claude-print-v1",
    "codex": "codex-exec-v1",
    "grok": "grok-print-v1",
}


def _read(source: TextIO, sink: TextIO, prompt: str) -> str | None:
    sink.write(prompt)
    sink.flush()
    value = source.readline(MAX_CONSOLE_LINE_CHARACTERS + 2)
    if value == "":
        return None
    stripped = value.rstrip("\r\n")
    if len(stripped) > MAX_CONSOLE_LINE_CHARACTERS:
        raise InteractiveSelectorError()
    return stripped


def _numeric_choice(
    source: TextIO,
    sink: TextIO,
    prompt: str,
    options: tuple[object, ...],
    *,
    optional: bool = False,
) -> object | None:
    raw = _read(source, sink, prompt)
    if raw is None:
        return None
    if optional and raw == "":
        return ""
    if (
        not raw
        or len(raw) > MAX_NUMERIC_CHOICE_CHARACTERS
        or any(character not in "0123456789" for character in raw)
    ):
        raise InteractiveSelectorError()
    index = int(raw) - 1
    if not 0 <= index < len(options):
        raise InteractiveSelectorError()
    return options[index]


def _model_choice(
    source: TextIO,
    sink: TextIO,
    prompt: str,
    *,
    supports_override: bool,
) -> str | None:
    options = ("default", "custom") if supports_override else ("default",)
    sink.write("model modes:\n")
    for number, value in enumerate(options, 1):
        sink.write(f"{number}. {value}\n")
    selected = _numeric_choice(source, sink, prompt, options)
    if selected is None:
        raise _SelectorCancelled()
    if selected == "default":
        return None
    raw = _read(source, sink, "custom model label: ")
    if raw is None:
        raise _SelectorCancelled()
    try:
        return validate_opaque_token(raw)
    except V2ValidationError as error:
        raise InteractiveSelectorError() from error


def _profile_id(
    profiles: list[dict[str, object]],
    *,
    vendor: str,
    account_profile: str,
    source_account_profile: str,
) -> str:
    if vendor == profiles[0]["vendor"] and account_profile == source_account_profile:
        return "source"
    for profile in profiles[1:]:
        if profile["vendor"] == vendor and profile["account_profile"] == account_profile:
            profile_id = profile["id"]
            if isinstance(profile_id, str):
                return profile_id
    profile_id = f"profile-{len(profiles)}"
    profiles.append({"id": profile_id, "vendor": vendor, "account_profile": account_profile})
    return profile_id


def _build_policy(
    source: BuiltInAdapter,
    source_account: str,
    selections: list[tuple[str, BuiltInAdapter, str, str | None, str]],
    executable_by_adapter: dict[str, str],
) -> dict[str, object]:
    """Build declarations only; the compiler remains the command authority."""
    profiles: list[dict[str, object]] = [
        {"id": "source", "vendor": source.agent, "account_profile": source_account}
    ]
    targets: list[dict[str, object]] = []
    routes: list[dict[str, object]] = []
    for tier, adapter, account, model, effort in selections:
        destination_id = _profile_id(
            profiles,
            vendor=adapter.agent,
            account_profile=account,
            source_account_profile=source_account,
        )
        target_id = f"{tier}-{adapter.agent}-target"
        targets.append(
            {
                "id": target_id,
                "profile_id": destination_id,
                "vendor": adapter.agent,
                "executable": executable_by_adapter[adapter.agent],
                "builder": {"kind": _BUILDER_FOR[adapter.agent], "version": 1},
                "allowed_model_effort_pairs": [{"model": model, "effort": effort}],
            }
        )
        routes.append(
            {
                "id": f"{tier}-{adapter.agent}",
                "source_profile_id": "source",
                "tier": tier,
                "target_id": target_id,
                "model": model,
                "effort": effort,
            }
        )
    profile_grants = [
        {
            "id": f"{source.agent}-to-{profile['id']}",
            "from_profile_id": "source",
            "to_profile_id": profile["id"],
        }
        for profile in profiles[1:]
    ]
    vendor_pairs = sorted(
        {(source.agent, target["vendor"]) for target in targets if target["vendor"] != source.agent}
    )
    vendor_grants = [
        {
            "id": f"{from_vendor}-to-{to_vendor}",
            "from_vendor": from_vendor,
            "to_vendor": to_vendor,
        }
        for from_vendor, to_vendor in vendor_pairs
    ]
    return {
        "schema_version": 3,
        "profiles": profiles,
        "execution_targets": targets,
        "routes": routes,
        "profile_grants": profile_grants,
        "vendor_grants": vendor_grants,
    }


def run_interactive_selector(
    console_input: TextIO,
    console_output: TextIO,
    policy_output: TextIO,
    *,
    path_value: str,
    observer: Callable[[str], ExecutableObservation] = observe_executable,
) -> int:
    """Select and confirm one task-free canonical schema-3 policy."""
    try:
        entries = _path_entries(path_value)
    except (ValueError, UnicodeError) as error:
        raise InteractiveSelectorError() from error
    installed: list[tuple[BuiltInAdapter, str]] = []
    for adapter in BUILT_IN_ADAPTERS:
        executable = _find_executable(adapter.executable_name, entries)
        if executable is not None:
            installed.append((adapter, executable))
    if not installed:
        raise InteractiveSelectorError()

    console_output.write("Installed agents:\n")
    for number, (adapter, _executable) in enumerate(installed, 1):
        console_output.write(f"{number}. {adapter.agent}\n")
    selected_source = _numeric_choice(
        console_input,
        console_output,
        "source agent number: ",
        tuple(item[0] for item in installed),
    )
    if selected_source is None:
        return 1
    assert isinstance(selected_source, BuiltInAdapter)
    source = selected_source
    source_account_raw = _read(console_input, console_output, "source account profile: ")
    if source_account_raw is None:
        return 1
    try:
        source_account = validate_label(source_account_raw)
    except V2ValidationError as error:
        raise InteractiveSelectorError() from error

    selections: list[tuple[str, BuiltInAdapter, str, str | None, str]] = []
    executable_by_adapter = {adapter.agent: executable for adapter, executable in installed}
    for tier in ("low", "standard", "high"):
        selected = _numeric_choice(
            console_input,
            console_output,
            f"{tier} destination agent number (blank skips): ",
            tuple(item[0] for item in installed),
            optional=True,
        )
        if selected is None:
            return 1
        if selected == "":
            continue
        assert isinstance(selected, BuiltInAdapter)
        account_raw = _read(
            console_input,
            console_output,
            f"{tier} destination account profile: ",
        )
        if account_raw is None:
            return 1
        try:
            account = validate_label(account_raw)
            effort_values = tuple(_EFFORT_CHOICES)
            console_output.write(f"{tier} effort choices:\n")
            for number, effort_value in enumerate(effort_values, 1):
                console_output.write(f"{number}. {effort_value}\n")
            effort = _numeric_choice(
                console_input,
                console_output,
                f"{tier} effort number: ",
                effort_values,
            )
            if effort is None:
                return 1
            assert isinstance(effort, str)
            model = _model_choice(
                console_input,
                console_output,
                f"{tier} model mode number: ",
                supports_override=selected.accepts_opaque_model_override,
            )
        except _SelectorCancelled:
            return 1
        except (InteractiveSelectorError, V2ValidationError) as error:
            raise InteractiveSelectorError() from error
        executable = executable_by_adapter.get(selected.agent)
        if executable is None:
            raise InteractiveSelectorError()
        selections.append((tier, selected, account, model, effort))

    if not selections:
        raise InteractiveSelectorError()
    raw_policy = _build_policy(source, source_account, selections, executable_by_adapter)
    for grant_key, dimension, from_key, to_key in (
        ("profile_grants", "profile", "from_profile_id", "to_profile_id"),
        ("vendor_grants", "vendor", "from_vendor", "to_vendor"),
    ):
        grants = raw_policy[grant_key]
        assert isinstance(grants, list)
        for grant in grants:
            assert isinstance(grant, dict)
            consent = _read(
                console_input,
                console_output,
                (f'{dimension} transition {grant[from_key]} -> {grant[to_key]}; type "yes": '),
            )
            if consent != "yes":
                return 1
    targets = raw_policy["execution_targets"]
    assert isinstance(targets, list)
    try:
        policy: NativePolicyV3 = parse_native_policy_v3(raw_policy)
        source_vendor, _, _ = validate_native_selector_v3(source.agent, "source", "low")
        observations = {
            target["executable"]: observer(target["executable"])
            for target in targets
            if isinstance(target.get("executable"), str)
        }
        reviews = [
            compile_native_policy_v3(
                policy,
                source_vendor=source_vendor,
                source_profile_id="source",
                tier=route.tier,
                purpose="native_route",
                observations=observations,
            )
            for route in policy.routes
        ]
    except (OSError, RuntimeError, TypeError, ValueError, V2ValidationError) as error:
        raise InteractiveSelectorError() from error

    # Preview stays on the injected controlling console.  It explicitly
    # exposes argv delivery and the placeholder without ever seeing task text.
    console_output.write(canonical_json_bytes_v2({"routes": reviews}).decode("ascii") + "\n")
    confirmation = _read(
        console_input,
        console_output,
        'emit this policy? type "yes": ',
    )
    if confirmation != "yes":
        return 1
    policy_output.write(canonical_json_bytes_v2(raw_policy).decode("ascii") + "\n")
    policy_output.flush()
    return 0
