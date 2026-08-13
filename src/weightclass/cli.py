"""Command-line interface for rendering, never executing, route commands."""

import argparse
import json
import subprocess
import sys
import unicodedata
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path
from typing import Any, Final, NoReturn, cast

from . import __version__
from .classification import (
    InvalidTaskError,
    Tier,
    apply_cautious_posture,
    classify_task,
    classify_task_with_reason,
    read_task_from_standard_input,
    validate_task,
)
from .delegation_compile import (
    canonical_json_bytes,
    compile_delegation_descriptor,
    render_review_descriptor,
)
from .delegation_protocol import DelegationFrameError, encode_delegation_frame
from .delegation_qualification import (
    QualificationRecord,
    QualifiedRuntimeUnavailableError,
    attach_qualification_requirement,
    build_qualification_candidate,
    load_conformance_evidence,
    load_packaged_qualification_registry,
    select_qualification_for_descriptor,
    verify_qualified_runtime,
)
from .delegation_runtime import (
    DelegationRuntimeFailedError,
    DelegationRuntimeUnavailableError,
    run_delegation_runtime,
    validate_delegation_runtime,
    validate_runtime_process_context,
)
from .delegation_schema import (
    DelegationInvalidInputError,
    DelegationUnsupportedError,
    current_platform_contract,
    load_delegation_manifest,
    load_delegation_policy,
    validate_runtime_path_lexically,
)
from .delegation_types import DelegationTier, DirectChildCleanup, VendorFamily
from .delegation_v2_compile import compile_delegation_v2
from .delegation_v2_protocol import DelegationFrameV2Error, encode_delegation_frame_v2
from .delegation_v2_runtime import run_delegation_v2_runtime
from .delegation_v2_schema import (
    DelegationV2InvalidInputError,
    parse_delegation_manifest_v2,
    parse_delegation_policy_v2,
)
from .delegation_v2_versions import DelegationVersionError, dispatch_delegation_versions
from .executable_observation import observe_executable
from .foreground_process import run_owned_foreground
from .json_input import JsonInputError, load_json_object
from .native_v2_compile import compile_native_v2
from .native_v2_runtime import run_native_v2
from .native_v2_schema import (
    NativePolicyV2,
    dispatch_native_policy_schema,
    validate_native_selector,
)
from .native_v2_types import CompiledExecutionV2
from .process_context import ChildStatusLostError
from .router import (
    DEFAULT_ROUTES,
    TASK_PLACEHOLDER,
    InvalidVendorLabelError,
    Posture,
    Route,
    RouteRequest,
    RouteSelectionError,
    RoutingPolicy,
    native_route_fingerprint,
    select_route,
    select_tier_route,
    substitute_task,
    uses_argv_task_delivery,
    validate_vendor_label,
)
from .task_v2 import ValidatedTaskV2, read_validated_task_v2
from .triage import TriageUnavailableError, ask_vendor_for_tier, triage_descriptor
from .v2 import (
    API_SOURCE_VENDORS,
    V2InvalidInputError,
    load_api_policy,
    observe_api_runtime,
    render_api_route,
    route_fingerprint,
    select_api_route,
)
from .v2_validation import V2ValidationError

EXECUTOR_FAILED_EXIT_CODE: Final = 7
MAX_NATIVE_POLICY_BYTES: Final = 262_144
MAX_NATIVE_DESCRIPTOR_BYTES: Final = 262_144
MAX_EXAMPLE_MODEL_LABEL_BYTES: Final = 240
EXAMPLE_POLICY_RESOURCES: Final = {
    "agy-cost-focused": "agy_cost_focused_policy.json",
    "claude-cost-focused": "claude_cost_focused_policy.json",
    "codex-cost-focused": "codex_cost_focused_policy.json",
    "grok-cost-focused": "grok_cost_focused_policy.json",
}


class InvalidInputError(ValueError):
    """Raised for invalid policy or descriptor data without exposing it."""


def _validate_example_model_label(value: object) -> str:
    """Accept one opaque, reviewable argv token without interpreting the model."""
    if not isinstance(value, str):
        raise InvalidInputError()
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise InvalidInputError() from error
    if (
        not 1 <= len(encoded) <= MAX_EXAMPLE_MODEL_LABEL_BYTES
        or value.startswith("-")
        or any(character.isspace() or not character.isprintable() for character in value)
    ):
        raise InvalidInputError()
    return value


def _render_example_policy(name: str, model: str | None) -> str:
    try:
        policy_text = (
            files("weightclass")
            .joinpath("examples", EXAMPLE_POLICY_RESOURCES[name])
            .read_text(encoding="utf-8")
        )
    except (KeyError, OSError, UnicodeError) as error:
        raise InvalidInputError() from error
    if model is None:
        return policy_text
    if name != "codex-cost-focused":
        raise InvalidInputError()

    reviewed_model = _validate_example_model_label(model)
    try:
        policy = json.loads(policy_text)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise InvalidInputError() from error
    if not isinstance(policy, dict) or not isinstance(policy.get("routes"), list):
        raise InvalidInputError()
    changed_tiers: set[str] = set()
    for route in policy["routes"]:
        if not isinstance(route, dict):
            raise InvalidInputError()
        tier = route.get("tier")
        if tier not in {"low", "standard"}:
            continue
        command = route.get("command")
        if not isinstance(command, list) or "-c" not in command or "--model" in command:
            raise InvalidInputError()
        config_index = command.index("-c")
        command[config_index:config_index] = ["--model", reviewed_model]
        changed_tiers.add(tier)
    if changed_tiers != {"low", "standard"}:
        raise InvalidInputError()
    return json.dumps(policy, ensure_ascii=False, indent=2) + "\n"


def _automatic_cost_policy(
    enabled: bool,
    policy_path: Path | None,
    source_vendor: str | None,
    source_profile: str | None,
    model: str | None,
) -> RoutingPolicy | None:
    """Resolve one packaged opt-in without writing router state or policy files."""
    if not enabled:
        if model is not None:
            raise InvalidInputError()
        return None
    if policy_path is not None or source_vendor is None or source_profile is not None:
        raise InvalidInputError()
    try:
        raw_policy = json.loads(_render_example_policy(f"{source_vendor}-cost-focused", model))
    except (InvalidInputError, json.JSONDecodeError, UnicodeError) as error:
        raise InvalidInputError() from error
    if not isinstance(raw_policy, dict):
        raise InvalidInputError()
    try:
        version, dispatched = dispatch_native_policy_schema(raw_policy)
    except V2ValidationError as error:
        raise InvalidInputError() from error
    if version != 1 or not isinstance(dispatched, dict):
        raise InvalidInputError()
    policy = _parse_routing_policy(dispatched)
    if policy.allow_mixed_vendors or any(route.vendor != source_vendor for route in policy.routes):
        raise InvalidInputError()
    return policy


def _report_executor_result(completed_process: subprocess.CompletedProcess[bytes]) -> int:
    """Map a finished child's status to a router exit code without hiding it.

    자식의 종료 코드를 그대로 반환하면 라우터 자신의 진단 코드(2~6)와 구분할 수
    없고, 시그널로 죽은 경우(음수)는 Python 이 241 같은 값으로 뭉갠다. 실패는
    전용 코드로 보고하고 실제 값은 진단에 담는다.

    자식이 종료 코드에 태스크에서 유도한 값을 실을 수 있다는 지적이 리뷰에서
    나왔으나, 이는 새로운 유출 경로가 아니다. 자식은 이미 태스크 전문을 받고
    stdout/stderr 를 상속받으므로(라우터는 캡처하지 않는다) 훨씬 넓은 대역으로
    무엇이든 내보낼 수 있다. 라우터가 비유출을 보장하는 대상은 라우터가
    생성하는 값이며, 여기 실리는 정수는 자식이 스스로 고른 값이다. 이 값을 빼면
    진단 능력만 잃고 실제 경계는 달라지지 않는다.
    """
    if completed_process.returncode == 0:
        return 0
    diagnostic: dict[str, object] = {"error": "executor_failed"}
    if completed_process.returncode < 0:
        diagnostic["executor_signal"] = -completed_process.returncode
    else:
        diagnostic["executor_exit_code"] = completed_process.returncode
    # 자식은 stderr 를 상속받으므로 이 진단은 자식이 이미 쓴 내용 뒤에 붙는다.
    # 진행 표시처럼 개행 없이 끝나는 출력 뒤에 그대로 이으면 JSON 이 그 줄에
    # 섞여 어떤 파싱으로도 복구되지 않는다. 항상 새 줄에서 시작하게 해서
    # "stderr 의 마지막 줄"이 언제나 진단이 되도록 보장한다.
    print("\n" + json.dumps(diagnostic), file=sys.stderr)
    return EXECUTOR_FAILED_EXIT_CODE


class SafeArgumentParser(argparse.ArgumentParser):
    """Avoid including caller-provided values in diagnostics."""

    def error(self, message: str) -> NoReturn:
        del message
        raise InvalidInputError()


def _read_json_object(path: Path, *, max_bytes: int) -> dict[str, Any]:
    """Read one caller-supplied document that selects what this process runs."""
    try:
        return load_json_object(path, max_bytes=max_bytes, require_exclusive_write_owner=True)
    except JsonInputError:
        raise InvalidInputError() from None


def _require_exact_keys(value: dict[str, Any], expected_keys: set[str]) -> None:
    if set(value) != expected_keys:
        raise InvalidInputError()


def _require_nonempty_string(value: object) -> str:
    """Require an identifier-like policy value: no whitespace at all."""
    if not isinstance(value, str) or not value or any(character.isspace() for character in value):
        raise InvalidInputError()
    return value


def _require_command_argument(value: object) -> str:
    """Require one reviewable argv token.

    명령 인자는 식별자와 규칙이 다르다. 셸을 거치지 않고 argv 로 그대로
    전달되므로 내부 공백은 위험하지 않고, "/Users/me/My Tools/claude" 같은
    설치 경로나 여러 단어로 된 플래그 값에는 반드시 필요하다.

    거부 기준은 "검토자가 본 대로 실행되는가"이다. ord 범위를 손으로 나열하면
    매번 빠진 문자가 나온다(NUL, 그다음 lone surrogate). 대신 두 규칙으로
    정한다.

    - 유니코드 대분류 C 는 전부 거부한다. 제어문자(Cc, C0/C1), 서식
      문자(Cf, zero-width space·RTL override·BOM), 서로게이트(Cs),
      사용자 영역(Co), 미할당(Cn)이 여기 든다. 앞의 둘은 route 출력에
      드러나지 않아 검토를 무력화하고, 서로게이트는 exec 단계에서
      UnicodeEncodeError 로 터져 진단 없이 트레이스백을 남긴다.
    - 공백은 ASCII 스페이스만 허용한다. NBSP 같은 문자는 스페이스처럼 보이지만
      다른 인자를 만든다. 앞뒤 공백도 경로를 조용히 다른 값으로 만든다.
    """
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(
            unicodedata.category(character).startswith("C")
            or (character.isspace() and character != " ")
            for character in value
        )
    ):
        raise InvalidInputError()
    return value


def _require_at_most_one_task_slot(command: tuple[str, ...]) -> tuple[str, ...]:
    """Require the reserved task token to appear at most once, as a whole argument.

    부분 문자열로 쓰면 태스크와 플래그가 어떻게 이어붙었는지가 모호해지고, 두 번
    쓰면 태스크를 두 번 전달한다는 뜻이 되는데 그런 의미는 정의된 적이 없다.
    """
    if sum(token == TASK_PLACEHOLDER for token in command) > 1:
        raise InvalidInputError()
    if any(TASK_PLACEHOLDER in token and token != TASK_PLACEHOLDER for token in command):
        raise InvalidInputError()
    return command


def _parse_route(value: object) -> Route:
    if not isinstance(value, dict):
        raise InvalidInputError()

    keys = set(value)
    has_workflow = keys == {"id", "vendor", "workflow", "command"}
    has_tier = keys == {"id", "vendor", "tier", "command"}
    if not has_workflow and not has_tier:
        raise InvalidInputError()

    route_id = _require_nonempty_string(value["id"])
    try:
        vendor = validate_vendor_label(value["vendor"])
    except InvalidVendorLabelError:
        raise InvalidInputError() from None
    command = value["command"]
    if not isinstance(command, list) or not command:
        raise InvalidInputError()

    workflow = _require_nonempty_string(value["workflow"]) if has_workflow else ""
    tier: Tier | None = None
    if has_tier:
        parsed_tier = _require_nonempty_string(value["tier"])
        if parsed_tier not in {"low", "standard", "high"}:
            raise InvalidInputError()
        tier = cast(Tier, parsed_tier)
    parsed_command = _require_at_most_one_task_slot(
        tuple(_require_command_argument(argument) for argument in command)
    )
    # workflow 라우트는 select_tier_route 의 후보가 아니라 render 만 다루므로
    # 아무도 이 자리를 채우지 않는다. 여기서 닫지 않으면 render 가 미치환
    # 리터럴 "{{task}}" 를 그대로 내보내 검토 산출물이 실제 실행과 어긋난다.
    if has_workflow and TASK_PLACEHOLDER in parsed_command:
        raise InvalidInputError()
    return Route(
        route_id=route_id,
        vendor=vendor,
        workflow=workflow,
        command=parsed_command,
        tier=tier,
    )


def load_routes(policy_path: Path) -> tuple[Route, ...]:
    """Load a strictly shaped, trusted-local route policy."""
    return load_routing_policy(policy_path).routes


def load_routing_policy(policy_path: Path) -> RoutingPolicy:
    """Load routing options and strictly shaped trusted-local routes."""
    policy = _read_json_object(policy_path, max_bytes=MAX_NATIVE_POLICY_BYTES)
    try:
        version, dispatched = dispatch_native_policy_schema(policy)
    except V2ValidationError:
        raise InvalidInputError() from None
    if version != 1:
        raise InvalidInputError()
    assert isinstance(dispatched, dict)
    return _parse_routing_policy(dispatched)


def _parse_routing_policy(policy: dict[str, Any]) -> RoutingPolicy:
    """Parse an already bounded and version-dispatched schema-1 policy."""
    if not {"routes"} <= set(policy) <= {"routes", "allow_mixed_vendors", "posture"}:
        raise InvalidInputError()
    allow_mixed_vendors = policy.get("allow_mixed_vendors", False)
    if not isinstance(allow_mixed_vendors, bool):
        raise InvalidInputError()
    posture = policy.get("posture")
    if "posture" in policy and (
        not isinstance(posture, str) or posture not in {"balanced", "cautious"}
    ):
        raise InvalidInputError()
    routes = policy["routes"]
    if not isinstance(routes, list) or not routes:
        raise InvalidInputError()

    parsed_routes = tuple(_parse_route(route) for route in routes)
    if len({route.route_id for route in parsed_routes}) != len(parsed_routes):
        raise InvalidInputError()
    return RoutingPolicy(parsed_routes, allow_mixed_vendors, cast(Posture | None, posture))


def load_request(descriptor_path: Path) -> RouteRequest:
    """Load a redacted descriptor without task content or credentials."""
    descriptor = _read_json_object(descriptor_path, max_bytes=MAX_NATIVE_DESCRIPTOR_BYTES)
    _require_exact_keys(descriptor, {"vendor", "workflow"})
    try:
        vendor = validate_vendor_label(descriptor["vendor"])
    except InvalidVendorLabelError:
        raise InvalidInputError() from None
    return RouteRequest(vendor=vendor, workflow=_require_nonempty_string(descriptor["workflow"]))


def _add_api_route_arguments(parser: argparse.ArgumentParser) -> None:
    """Declare the arguments shared by both V2 API subcommands."""
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--source-vendor", required=True, choices=sorted(API_SOURCE_VENDORS))
    parser.add_argument("--api-runtime", required=True, type=Path)


def _add_delegation_route_arguments(parser: argparse.ArgumentParser) -> None:
    """Declare offline inputs shared by future delegation commands."""
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--runtime-manifest", required=True, type=Path)
    parser.add_argument("--delegation-runtime", required=True)
    parser.add_argument("--source-vendor", required=True, choices=("claude", "codex"))
    parser.add_argument("--source-profile")
    parser.add_argument("--tier", required=True, choices=("low", "standard", "high"))
    parser.add_argument(
        "--require-qualified-runtime",
        action="store_true",
        help="Require a matching package-owned exact-artifact record.",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the whole command surface so `--help` lists every reachable mode.

    allow_abbrev를 끈 것은 --confirm-api-egress 같은 명시적 승인 플래그가
    --c 같은 축약으로 만족되면 안 되기 때문이다.
    """
    parser = SafeArgumentParser(
        prog="wclass",
        description="Classify a task and select a reviewable vendor command.",
        allow_abbrev=False,
    )
    # argparse 내장 version 액션은 argv의 나머지를 검증하기 전에 종료해 버려서
    # `wclass --version --bogus` 가 0으로 성공한다. 파싱을 끝낸 뒤 직접 처리한다.
    parser.add_argument("--version", action="store_true")
    subcommands = parser.add_subparsers(dest="command")

    classify = subcommands.add_parser(
        "classify",
        allow_abbrev=False,
        description="Print the tier of a task read from standard input.",
    )
    classify.add_argument("--source-vendor")
    # 로컬 판정이 기본이다. 이 플래그를 줄 때만 벤더 CLI 를 한 번 실행한다.
    classify.add_argument("--ask-vendor", action="store_true")
    # 판정 명령도 내장 벤더 명령이므로 실행 전에 볼 수 있어야 한다.
    classify.add_argument("--show-triage-command", action="store_true")
    classify.add_argument(
        "--explain",
        action="store_true",
        help="Include static reason-code and local policy metadata.",
    )
    example_policy = subcommands.add_parser(
        "example-policy",
        allow_abbrev=False,
        description="Print an installable reviewed policy example.",
    )
    example_policy.add_argument("name", choices=tuple(EXAMPLE_POLICY_RESOURCES))
    example_policy.add_argument("--model")
    for name, description in (
        ("route", "Select and print a command for a task read from standard input."),
        ("run", "Select and start a command for a task read from standard input."),
    ):
        native = subcommands.add_parser(name, allow_abbrev=False, description=description)
        native.add_argument("--policy", type=Path)
        native.add_argument("--source-vendor")
        native.add_argument("--source-profile")
        native.add_argument(
            "--cost-focused",
            action="store_true",
            help="Select the packaged opt-in policy for --source-vendor.",
        )
        native.add_argument(
            "--model",
            help="Bind an opaque model label to cost-focused Codex low/standard routes.",
        )
        # wclass classify 가 낸 티어를 그대로 받는다. route 와 run 은 이 경로에서도
        # 네트워크를 쓰지 않는다. 판정은 별도 명령에서 이미 끝났다.
        native.add_argument("--tier", choices=("low", "standard", "high"))
        if name == "run":
            native.add_argument("--ack-route-fingerprint")

    render = subcommands.add_parser(
        "render",
        allow_abbrev=False,
        description="Render the command of a policy route named by a workflow descriptor.",
    )
    render.add_argument("--policy", required=True, type=Path)
    render.add_argument("--descriptor", required=True, type=Path)

    delegate = subcommands.add_parser(
        "delegate",
        allow_abbrev=False,
        description="Compile a reviewable role-delegation policy.",
    )
    delegate_subcommands = delegate.add_subparsers(dest="delegate_command", required=True)
    _add_delegation_route_arguments(
        delegate_subcommands.add_parser(
            "route",
            allow_abbrev=False,
            description="Compile an offline delegation review descriptor.",
        )
    )
    delegate_run = delegate_subcommands.add_parser(
        "run",
        allow_abbrev=False,
        description="Run one reviewed trusted delegation runtime.",
    )
    _add_delegation_route_arguments(delegate_run)
    delegate_run.add_argument("--confirm-trusted-delegation-runtime", action="store_true")
    delegate_run.add_argument("--ack-route-fingerprint")
    qualification_candidate = delegate_subcommands.add_parser(
        "qualification-candidate",
        allow_abbrev=False,
        description="Build an untrusted qualification candidate from task-free evidence.",
    )
    qualification_candidate.add_argument("--evidence", required=True, type=Path)
    qualification_candidate.add_argument("--delegation-runtime", required=True, type=Path)

    api = subcommands.add_parser(
        "v2",
        allow_abbrev=False,
        description="Select a declarative API route served by an external runtime.",
    )
    api_subcommands = api.add_subparsers(dest="api_command", required=True)
    _add_api_route_arguments(
        api_subcommands.add_parser(
            "route",
            allow_abbrev=False,
            description="Review a declarative API route.",
        )
    )
    api_run = api_subcommands.add_parser(
        "run",
        allow_abbrev=False,
        description="Run a reviewed declarative API route.",
    )
    _add_api_route_arguments(api_run)
    api_run.add_argument("--confirm-api-egress", action="store_true")
    api_run.add_argument("--ack-route-fingerprint")
    return parser


def _compile_delegation_inputs(
    policy_path: Path,
    manifest_path: Path,
    runtime_path: str,
    source_vendor: VendorFamily,
    tier: DelegationTier,
    require_qualified_runtime: bool = False,
) -> tuple[dict[str, Any], str, QualificationRecord | None]:
    policy = load_delegation_policy(policy_path)
    manifest = load_delegation_manifest(manifest_path)
    normalized_runtime_path = validate_runtime_path_lexically(runtime_path)
    target_platform = current_platform_contract()
    descriptor = compile_delegation_descriptor(
        policy,
        manifest,
        runtime_path=normalized_runtime_path,
        source_vendor=source_vendor,
        tier=tier,
        target_platform=target_platform,
    )
    qualification = None
    if require_qualified_runtime:
        registry = load_packaged_qualification_registry()
        qualification = select_qualification_for_descriptor(descriptor, registry)
        descriptor = attach_qualification_requirement(descriptor, qualification)
    return descriptor, render_review_descriptor(descriptor), qualification


def _dispatch_delegation_cli_version(
    policy_path: Path,
    manifest_path: Path,
    runtime_path: str,
    source_vendor: str,
    source_profile: str | None,
    tier: str,
    require_qualified_runtime: bool,
) -> int | CompiledExecutionV2 | None:
    """Dispatch v2 parsing while leaving the protocol-1 compiler untouched."""
    try:
        raw_policy = _read_json_object(policy_path, max_bytes=262_144)
        raw_manifest = _read_json_object(manifest_path, max_bytes=262_144)
        policy_version = raw_policy.get("schema_version")
        if policy_version == 1 and raw_manifest.get("manifest_schema_version") == 1:
            version = dispatch_delegation_versions((1, 1, 1, 1, "WCD1"))
        else:
            version = dispatch_delegation_versions(
                (
                    policy_version,
                    raw_manifest.get("schema_version"),
                    raw_policy.get("compiler_contract_version"),
                    raw_policy.get("runtime_protocol_version"),
                    raw_policy.get("frame_version"),
                )
            )
        if version == 1:
            if source_profile is not None:
                raise DelegationV2InvalidInputError()
            return None
        if source_profile is None:
            raise DelegationV2InvalidInputError()
        # Protocol 2 has no qualification semantics. This precedence is before
        # confirmation, acknowledgement, executable inspection, or task input.
        if require_qualified_runtime:
            print(json.dumps({"error": "unsupported_route"}), file=sys.stderr)
            return 3
        policy = parse_delegation_policy_v2(raw_policy)
        manifest = parse_delegation_manifest_v2(raw_manifest)
        return compile_delegation_v2(
            policy,
            manifest,
            source_vendor_family=source_vendor,
            source_profile_id=source_profile,
            tier=tier,
            runtime_path=runtime_path,
        )
    except (
        InvalidInputError,
        DelegationVersionError,
        DelegationV2InvalidInputError,
        RecursionError,
    ):
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    raise AssertionError("unreachable")


def delegation_route(
    policy_path: Path,
    manifest_path: Path,
    runtime_path: str,
    source_vendor: VendorFamily,
    tier: DelegationTier,
    require_qualified_runtime: bool = False,
    source_profile: str | None = None,
) -> int:
    """Compile a descriptor without reading task stdin or inspecting a runtime."""
    version_result = (
        _dispatch_delegation_cli_version(
            policy_path,
            manifest_path,
            runtime_path,
            source_vendor,
            source_profile,
            tier,
            require_qualified_runtime,
        )
        if source_profile is not None
        else None
    )
    if version_result is not None:
        if isinstance(version_result, CompiledExecutionV2):
            print(version_result.canonical_descriptor_bytes.decode("ascii"))
            return 0
        return version_result
    try:
        _, rendered, _ = _compile_delegation_inputs(
            policy_path,
            manifest_path,
            runtime_path,
            source_vendor,
            tier,
            require_qualified_runtime,
        )
    except DelegationInvalidInputError:
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    except DelegationUnsupportedError:
        print(json.dumps({"error": "unsupported_route"}), file=sys.stderr)
        return 3
    print(rendered)
    return 0


def delegation_run_from_standard_input(
    policy_path: Path,
    manifest_path: Path,
    runtime_path: str,
    source_vendor: VendorFamily,
    tier: DelegationTier,
    confirm_trusted_runtime: bool,
    acknowledged_fingerprint: str | None,
    require_qualified_runtime: bool = False,
    source_profile: str | None = None,
) -> int:
    """Run one acknowledged external orchestrator without handling credentials."""
    version_result = (
        _dispatch_delegation_cli_version(
            policy_path,
            manifest_path,
            runtime_path,
            source_vendor,
            source_profile,
            tier,
            require_qualified_runtime,
        )
        if source_profile is not None
        else None
    )
    if version_result is not None:
        if isinstance(version_result, CompiledExecutionV2):
            return _delegation_v2_run(
                version_result,
                confirm_trusted_runtime,
                acknowledged_fingerprint,
            )
        return version_result
    try:
        descriptor, rendered, qualification = _compile_delegation_inputs(
            policy_path,
            manifest_path,
            runtime_path,
            source_vendor,
            tier,
            require_qualified_runtime,
        )
    except DelegationInvalidInputError:
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    except DelegationUnsupportedError:
        print(json.dumps({"error": "unsupported_route"}), file=sys.stderr)
        return 3

    if not confirm_trusted_runtime:
        print(json.dumps({"error": "delegation_confirmation_required"}), file=sys.stderr)
        return 5
    if acknowledged_fingerprint != descriptor["route_fingerprint"]:
        print(json.dumps({"error": "route_fingerprint_mismatch"}), file=sys.stderr)
        return 6
    try:
        if qualification is None:
            validate_delegation_runtime(runtime_path)
        else:
            verify_qualified_runtime(Path(runtime_path), qualification)
        validate_runtime_process_context()
    except (DelegationRuntimeUnavailableError, QualifiedRuntimeUnavailableError):
        print(json.dumps({"error": "executor_unavailable"}), file=sys.stderr)
        return 4
    try:
        task = read_task_from_standard_input()
        validate_task(task)
    except InvalidTaskError:
        print(json.dumps({"error": "invalid_task"}), file=sys.stderr)
        return 2
    try:
        frame = encode_delegation_frame(rendered.encode("ascii"), task)
    except DelegationFrameError:
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2

    workflow_cleanup = descriptor["runtime_contract"]["direct_child_cleanup"]
    cleanup = DirectChildCleanup(
        grace_seconds=workflow_cleanup["grace_seconds"],
        terminate_grace_seconds=workflow_cleanup["terminate_grace_seconds"],
    )
    try:
        completed_process = run_delegation_runtime(runtime_path, frame, cleanup)
    except DelegationRuntimeUnavailableError:
        print(json.dumps({"error": "executor_unavailable"}), file=sys.stderr)
        return 4
    except DelegationRuntimeFailedError:
        print(json.dumps({"error": "executor_failed"}), file=sys.stderr)
        return EXECUTOR_FAILED_EXIT_CODE
    return _report_executor_result(completed_process)


def _delegation_v2_run(
    compiled: CompiledExecutionV2,
    confirm_trusted_runtime: bool,
    acknowledged_fingerprint: str | None,
) -> int:
    """Run one already-compiled protocol-2 route without v1 lifecycle calls."""
    if not confirm_trusted_runtime:
        print(json.dumps({"error": "delegation_confirmation_required"}), file=sys.stderr)
        return 5
    if acknowledged_fingerprint is None:
        print(json.dumps({"error": "route_fingerprint_mismatch"}), file=sys.stderr)
        return 6
    # 환경을 먼저 확인하고 페이로드를 만진다. native schema-2 run 과 delegation
    # protocol-1 run 이 이미 이렇게 하고 있었고, 이 경로만 빠져 있었다. 자식의
    # 종료 상태를 신뢰할 수 없는 컨텍스트라면 태스크를 읽기 전에 닫는다.
    try:
        validate_runtime_process_context()
    except DelegationRuntimeUnavailableError:
        print(json.dumps({"error": "executor_unavailable"}), file=sys.stderr)
        return 4
    try:
        task = read_validated_task_v2(getattr(sys.stdin, "buffer", sys.stdin))
    except V2ValidationError:
        print(json.dumps({"error": "invalid_task"}), file=sys.stderr)
        return 2
    if acknowledged_fingerprint != compiled.route_fingerprint:
        print(json.dumps({"error": "route_fingerprint_mismatch"}), file=sys.stderr)
        return 6
    try:
        first_observation = observe_executable(compiled.executable)
    except (V2ValidationError, OSError, ValueError):
        print(json.dumps({"error": "executor_unavailable"}), file=sys.stderr)
        return 4
    try:
        frame = encode_delegation_frame_v2(
            compiled.canonical_descriptor_bytes, task.delivery_bytes()
        )
    except (DelegationFrameV2Error, V2ValidationError):
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    try:
        completed = run_delegation_v2_runtime(compiled, frame, first_observation)
    except ChildStatusLostError:
        print(json.dumps({"error": "executor_failed"}), file=sys.stderr)
        return EXECUTOR_FAILED_EXIT_CODE
    except (V2ValidationError, OSError, ValueError):
        print(json.dumps({"error": "executor_unavailable"}), file=sys.stderr)
        return 4
    return _report_executor_result(completed)


def delegation_qualification_candidate(evidence_path: Path, runtime_path: Path) -> int:
    """Print a review candidate without changing the package trust registry."""
    try:
        normalized_runtime_path = validate_runtime_path_lexically(str(runtime_path))
        evidence = load_conformance_evidence(evidence_path)
        candidate = build_qualification_candidate(evidence, Path(normalized_runtime_path))
    except DelegationInvalidInputError:
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    print(canonical_json_bytes(candidate).decode("ascii"))
    return 0


def v2_route_from_standard_input(
    policy_path: Path,
    source_vendor: str,
    runtime_path: Path,
) -> int:
    """Render an API review descriptor without sending data to a provider."""
    try:
        runtime_observation = observe_api_runtime(runtime_path)
        runtime_path = Path(runtime_observation.lexical_path)
        policy = load_api_policy(policy_path)
        task = read_task_from_standard_input()
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
    print(
        json.dumps(
            render_api_route(
                route,
                policy,
                tier,
                source_vendor,
                runtime_path,
                runtime_observation,
            )
        )
    )
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
        policy = load_api_policy(policy_path)
    except (V2InvalidInputError, InvalidInputError):
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2

    if not confirm_api_egress:
        print(json.dumps({"error": "api_confirmation_required"}), file=sys.stderr)
        return 5
    if acknowledged_fingerprint is None:
        print(json.dumps({"error": "route_fingerprint_mismatch"}), file=sys.stderr)
        return 6
    try:
        validate_runtime_process_context()
    except DelegationRuntimeUnavailableError:
        print(json.dumps({"error": "executor_unavailable"}), file=sys.stderr)
        return 4
    try:
        runtime_observation = observe_api_runtime(runtime_path)
        runtime_path = Path(runtime_observation.lexical_path)
    except V2InvalidInputError:
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    try:
        task = read_task_from_standard_input()
        tier, route = select_api_route(task, policy, source_vendor)
    except InvalidTaskError:
        print(json.dumps({"error": "invalid_task"}), file=sys.stderr)
        return 2
    except RouteSelectionError:
        print(json.dumps({"error": "unsupported_route"}), file=sys.stderr)
        return 3
    if acknowledged_fingerprint != route_fingerprint(
        route,
        policy,
        tier,
        source_vendor,
        runtime_path,
        runtime_observation,
    ):
        print(json.dumps({"error": "route_fingerprint_mismatch"}), file=sys.stderr)
        return 6
    try:
        final_observation = observe_api_runtime(runtime_path)
    except V2InvalidInputError:
        print(json.dumps({"error": "executor_unavailable"}), file=sys.stderr)
        return 4
    if final_observation != runtime_observation:
        print(json.dumps({"error": "route_fingerprint_mismatch"}), file=sys.stderr)
        return 6
    try:
        # 로케일 인코딩을 쓰는 text 모드는 비ASCII 태스크에서 UnicodeEncodeError를 내고,
        # 그 예외 메시지가 태스크 문자와 위치를 진단에 노출한다. 항상 UTF-8 바이트로 넘긴다.
        completed_process = run_owned_foreground(
            (
                str(runtime_path),
                "--provider",
                route.provider,
                "--model",
                route.model,
                "--effort",
                route.effort,
            ),
            task.encode("utf-8"),
            cleanup_grace_seconds=0,
            terminate_grace_seconds=0,
        )
    except ChildStatusLostError:
        print(json.dumps({"error": "executor_failed"}), file=sys.stderr)
        return EXECUTOR_FAILED_EXIT_CODE
    except (OSError, ValueError):
        # ValueError 는 argv 를 실제로 인코딩하는 단계에서 나온다(NUL, 서로게이트).
        # 검증기가 이미 막고 있지만, 규칙에 빈틈이 생겨도 트레이스백 대신
        # 진단으로 닫히도록 두 번째 방어선을 둔다.
        print(json.dumps({"error": "executor_unavailable"}), file=sys.stderr)
        return 4
    return _report_executor_result(completed_process)


def classify_from_standard_input(
    source_vendor: str | None = None,
    ask_vendor: bool = False,
    show_triage_command: bool = False,
    explain: bool = False,
) -> int:
    """Classify a task read from stdin without echoing or persisting it.

    기본값은 로컬 판정이다. --ask-vendor 는 이미 설치된 벤더 CLI 를 한 번
    실행해 난이도를 묻는다. 로컬 키워드 판정은 어휘만 보므로 전문용어 없이
    설명된 어려운 작업을 놓친다.
    """
    if (ask_vendor or show_triage_command) and source_vendor is None:
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    if explain and (ask_vendor or show_triage_command):
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    if show_triage_command:
        assert source_vendor is not None
        # 태스크를 읽지 않고 벤더도 부르지 않는다. 명령만 보여준다.
        #
        # 벤더 라벨이 열려 있으므로 이 패키지가 판정 명령을 갖지 않는 벤더도
        # 여기까지 내려온다. triage_descriptor 는 그런 벤더에 대해 이미
        # TriageUnavailableError 로 닫히지만(내부에서 실패), 여기서 잡지
        # 않으면 트레이스백으로 새어 나간다. --ask-vendor 경로와 같은 진단으로
        # 닫는다.
        try:
            print(json.dumps(triage_descriptor(source_vendor)))
        except TriageUnavailableError:
            print(json.dumps({"error": "triage_unavailable"}), file=sys.stderr)
            return 8
        return 0
    try:
        task = read_task_from_standard_input()
        if not ask_vendor:
            if explain:
                decision = classify_task_with_reason(task)
                tier = decision.tier
            else:
                tier = classify_task(task)
            tier_source = "local"
        else:
            # 티어를 밖에서 받더라도 검증은 건너뛰지 않는다.
            validate_task(task)
            assert source_vendor is not None
            tier = ask_vendor_for_tier(task, source_vendor)
            tier_source = "vendor"
    except InvalidTaskError:
        print(json.dumps({"error": "invalid_task"}), file=sys.stderr)
        return 2
    except TriageUnavailableError:
        # 조용히 로컬로 되돌아가지 않는다. 판정을 못 했다는 사실이 보여야 한다.
        print(json.dumps({"error": "triage_unavailable"}), file=sys.stderr)
        return 8
    # 기본 경로의 출력은 글자 그대로 유지한다. packaging/homebrew/weightclass.rb
    # 와 .github/workflows/ci.yml 이 {"tier": "low"} 를 정확히 단언하고 있고,
    # formula 는 이미 배포되어 있다. 새 키는 --ask-vendor 를 쓴 경우에만 붙인다.
    response: dict[str, str] = {"tier": tier}
    if explain:
        response["reason_code"] = decision.reason_code
        response["policy_version"] = decision.policy_version
    elif ask_vendor:
        response["tier_source"] = tier_source
    print(json.dumps(response))
    return 0


def select_task_route(
    task: str,
    policy: RoutingPolicy,
    source_vendor: str | None = None,
    explicit_tier: Tier | None = None,
) -> tuple[Tier, str, Route, RoutingPolicy]:
    """Classify a task and select its route without retaining task content.

    explicit_tier 는 wclass classify 가 이미 낸 판정을 받는 경로다. 분류를
    건너뛰더라도 검증은 건너뛰지 않는다. 그러지 않으면 빈 입력이나 상한 초과
    입력이 그대로 벤더 프로세스로 넘어간다.
    """
    if explicit_tier is not None:
        validate_task(task)
        tier = explicit_tier
        reason_code = "explicit.requested_tier"
    else:
        decision = classify_task_with_reason(task)
        reason_code = decision.reason_code
    if explicit_tier is None and policy.posture == "cautious":
        decision = apply_cautious_posture(decision)
        tier = decision.tier
        reason_code = decision.reason_code
    elif explicit_tier is None:
        tier = decision.tier
    route = select_tier_route(
        policy.routes,
        tier,
        source_vendor,
        policy.allow_mixed_vendors,
    )
    return tier, reason_code, route, policy


def route_from_standard_input(
    policy_path: Path | None,
    source_vendor: str | None,
    explicit_tier: Tier | None = None,
    source_profile: str | None = None,
    cost_focused: bool = False,
    model: str | None = None,
) -> int:
    """Select and render a command without echoing or persisting the task."""
    try:
        automatic_policy = _automatic_cost_policy(
            cost_focused, policy_path, source_vendor, source_profile, model
        )
    except InvalidInputError:
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    if policy_path is not None and automatic_policy is None:
        try:
            raw_policy = _read_json_object(policy_path, max_bytes=MAX_NATIVE_POLICY_BYTES)
            version, dispatched = dispatch_native_policy_schema(raw_policy)
        except (InvalidInputError, V2ValidationError):
            print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
            return 2
        if version == 2:
            return _native_v2_route(dispatched, source_vendor, source_profile, explicit_tier)
    else:
        dispatched = None
    if source_profile is not None:
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    try:
        policy = (
            automatic_policy
            if automatic_policy is not None
            else (
                _parse_routing_policy(cast(dict[str, Any], dispatched))
                if policy_path is not None
                else RoutingPolicy(DEFAULT_ROUTES)
            )
        )
        tier, reason_code, route, policy = select_task_route(
            read_task_from_standard_input(), policy, source_vendor, explicit_tier
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
        # 이 지문을 wclass run --ack-route-fingerprint 로 넘기면 검토한 선택이
        # 실행 직전에 다시 확인된다. 넘기지 않으면 구속력은 없다.
        "route_fingerprint": native_route_fingerprint(
            route, policy.allow_mixed_vendors, policy.posture
        ),
    }
    if policy.posture is not None:
        response["posture"] = policy.posture
        response["reason_code"] = reason_code
    # argv 전달은 태스크를 명령줄에 싣는다. 같은 머신의 다른 사용자가 ps 로 볼 수
    # 있으므로, 검토하는 사람이 이 사실을 모르고 지나치지 않게 명시한다.
    if uses_argv_task_delivery(route.command):
        response["task_delivery"] = "argv"
    print(json.dumps(response))
    return 0


def run_from_standard_input(
    policy_path: Path | None,
    source_vendor: str | None,
    acknowledged_fingerprint: str | None = None,
    explicit_tier: Tier | None = None,
    source_profile: str | None = None,
    cost_focused: bool = False,
    model: str | None = None,
) -> int:
    """Run a selected native command without a shell or output capture."""
    try:
        automatic_policy = _automatic_cost_policy(
            cost_focused, policy_path, source_vendor, source_profile, model
        )
    except InvalidInputError:
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    if policy_path is not None and automatic_policy is None:
        try:
            raw_policy = _read_json_object(policy_path, max_bytes=MAX_NATIVE_POLICY_BYTES)
            version, dispatched = dispatch_native_policy_schema(raw_policy)
        except (InvalidInputError, V2ValidationError):
            print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
            return 2
        if version == 2:
            return _native_v2_run(
                dispatched,
                source_vendor,
                source_profile,
                acknowledged_fingerprint,
                explicit_tier,
            )
    else:
        dispatched = None
    if source_profile is not None:
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    # 정책 파일로 실행할 때는 검토한 지문을 반드시 받는다. route 와 run 은 정책을
    # 각각 다른 프로세스에서 읽으므로, 그 사이에 파일이 바뀌면 검토한 것과 다른
    # 명령이 실행된다. 파일 권한 검사로는 이 창을 닫을 수 없다. 부모 디렉터리에
    # 쓸 수 있는 쪽은 모드와 무관하게 rename 으로 파일을 통째로 갈아치울 수 있고,
    # 애초에 두 번째 읽기는 첫 번째와 다른 파일일 수 있기 때문이다. 지문은 선택된
    # 명령까지 묶으므로 그 교체를 실행 직전에 잡아낸다.
    #
    # 코드에 고정되어 교체할 수 없는 기본 라우트에는 이 요구가 없다. 검토 대상이
    # 되는 사용자 소유 파일이 관여할 때만 적용한다.
    if (policy_path is not None or automatic_policy is not None) and (
        acknowledged_fingerprint is None
    ):
        print(json.dumps({"error": "route_fingerprint_mismatch"}), file=sys.stderr)
        return 6
    try:
        validate_runtime_process_context()
    except DelegationRuntimeUnavailableError:
        print(json.dumps({"error": "executor_unavailable"}), file=sys.stderr)
        return 4
    try:
        policy = (
            automatic_policy
            if automatic_policy is not None
            else (
                _parse_routing_policy(cast(dict[str, Any], dispatched))
                if policy_path is not None
                else RoutingPolicy(DEFAULT_ROUTES)
            )
        )
        task = read_task_from_standard_input()
        _, _, route, policy = select_task_route(task, policy, source_vendor, explicit_tier)
        if acknowledged_fingerprint is not None and acknowledged_fingerprint != (
            native_route_fingerprint(route, policy.allow_mixed_vendors, policy.posture)
        ):
            print(json.dumps({"error": "route_fingerprint_mismatch"}), file=sys.stderr)
            return 6
        # 치환은 spawn 직전에 한 번만 한다. 검토 출력과 지문은 이미 치환 전
        # 명령으로 계산되었으므로 태스크가 그 둘에 들어가지 않는다.
        argv = route.command
        child_input = task.encode("utf-8")
        if uses_argv_task_delivery(route.command):
            # execve 는 NUL 을 실을 수 없다. stdin 전달은 실을 수 있으므로 이
            # 거부는 argv 전달에만 적용한다.
            if "\x00" in task:
                raise InvalidTaskError()
            argv = substitute_task(route.command, task)
            child_input = b""
        # text 모드는 로케일 인코딩을 사용하므로 LC_ALL=C 환경에서 비ASCII 태스크가
        # UnicodeEncodeError로 새어 나간다. 자식 출력을 읽지 않으므로 바이트로 전달한다.
        completed_process = run_owned_foreground(
            argv,
            child_input,
            cleanup_grace_seconds=0,
            terminate_grace_seconds=0,
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
    except ChildStatusLostError:
        print(json.dumps({"error": "executor_failed"}), file=sys.stderr)
        return EXECUTOR_FAILED_EXIT_CODE
    except (OSError, ValueError):
        # ValueError 는 argv 를 실제로 인코딩하는 단계에서 나온다(NUL, 서로게이트).
        # 검증기가 이미 막고 있지만, 규칙에 빈틈이 생겨도 트레이스백 대신
        # 진단으로 닫히도록 두 번째 방어선을 둔다.
        print(json.dumps({"error": "executor_unavailable"}), file=sys.stderr)
        return 4
    return _report_executor_result(completed_process)


def _v2_task_and_tier(explicit_tier: Tier | None) -> tuple[ValidatedTaskV2, Tier]:
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    task = read_validated_task_v2(stream)
    tier = explicit_tier if explicit_tier is not None else classify_task(task.classification_text())
    return task, tier


def _native_v2_route(
    policy: object,
    source_vendor: str | None,
    source_profile: str | None,
    explicit_tier: Tier | None,
) -> int:
    if source_vendor is None or source_profile is None or not isinstance(policy, NativePolicyV2):
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    try:
        validated_vendor, validated_profile = validate_native_selector(
            source_vendor, source_profile
        )
    except V2ValidationError:
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    try:
        _, tier = _v2_task_and_tier(explicit_tier)
    except V2ValidationError:
        print(json.dumps({"error": "invalid_task"}), file=sys.stderr)
        return 2
    try:
        compiled = compile_native_v2(
            policy,
            source_vendor=validated_vendor,
            source_profile_id=validated_profile,
            tier=tier,
        )
    except V2ValidationError:
        print(json.dumps({"error": "unsupported_route"}), file=sys.stderr)
        return 3
    print(compiled.canonical_descriptor_bytes.decode("ascii"))
    return 0


def _native_v2_run(
    policy: object,
    source_vendor: str | None,
    source_profile: str | None,
    acknowledged_fingerprint: str | None,
    explicit_tier: Tier | None,
) -> int:
    if source_vendor is None or source_profile is None or not isinstance(policy, NativePolicyV2):
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    try:
        validated_vendor, validated_profile = validate_native_selector(
            source_vendor, source_profile
        )
    except V2ValidationError:
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    if acknowledged_fingerprint is None:
        print(json.dumps({"error": "route_fingerprint_mismatch"}), file=sys.stderr)
        return 6
    try:
        validate_runtime_process_context()
    except DelegationRuntimeUnavailableError:
        print(json.dumps({"error": "executor_unavailable"}), file=sys.stderr)
        return 4
    try:
        task, tier = _v2_task_and_tier(explicit_tier)
    except V2ValidationError:
        print(json.dumps({"error": "invalid_task"}), file=sys.stderr)
        return 2
    try:
        compiled = compile_native_v2(
            policy,
            source_vendor=validated_vendor,
            source_profile_id=validated_profile,
            tier=tier,
        )
    except V2ValidationError:
        print(json.dumps({"error": "unsupported_route"}), file=sys.stderr)
        return 3
    if acknowledged_fingerprint != compiled.route_fingerprint:
        print(json.dumps({"error": "route_fingerprint_mismatch"}), file=sys.stderr)
        return 6
    try:
        first_observation = observe_executable(compiled.executable)
        completed = run_native_v2(compiled, task.delivery_bytes(), first_observation)
    except ChildStatusLostError:
        print(json.dumps({"error": "executor_failed"}), file=sys.stderr)
        return EXECUTOR_FAILED_EXIT_CODE
    except (V2ValidationError, OSError, ValueError):
        print(json.dumps({"error": "executor_unavailable"}), file=sys.stderr)
        return 4
    return _report_executor_result(completed)


def render_workflow_route(policy_path: Path, descriptor_path: Path) -> int:
    """Render the command of the policy route named by a workflow descriptor."""
    try:
        route = select_route(load_routes(policy_path), load_request(descriptor_path))
    except InvalidInputError:
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    except RouteSelectionError:
        print(json.dumps({"error": "unsupported_route"}), file=sys.stderr)
        return 3
    print(json.dumps({"command": list(route.command), "route": route.route_id}))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Classify, route, render, or run a native command from explicit input."""
    try:
        arguments = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    except InvalidInputError:
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2

    if arguments.version:
        # 버전 조회는 단독 호출일 때만 유효하다. 서브커맨드와 함께 오면 어느 쪽을
        # 요청한 것인지 알 수 없으므로 닫는 방향으로 거부한다.
        if arguments.command is not None:
            print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
            return 2
        print(f"weightclass {__version__}")
        return 0
    if arguments.command is None:
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2

    # 라벨이 열려 있으므로 argparse 가 오타를 잡아주지 못한다. 형식만이라도
    # 여기서 닫아, 잘못된 라벨이 라우트 선택까지 내려가지 않게 한다.
    source_vendor = getattr(arguments, "source_vendor", None)
    if source_vendor is not None:
        try:
            validate_vendor_label(source_vendor)
        except InvalidVendorLabelError:
            print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
            return 2

    if arguments.command == "classify":
        return classify_from_standard_input(
            arguments.source_vendor,
            arguments.ask_vendor,
            arguments.show_triage_command,
            arguments.explain,
        )
    if arguments.command == "example-policy":
        try:
            policy_text = _render_example_policy(arguments.name, arguments.model)
        except InvalidInputError:
            print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
            return 2
        print(policy_text, end="" if policy_text.endswith("\n") else "\n")
        return 0
    if arguments.command == "route":
        return route_from_standard_input(
            arguments.policy,
            arguments.source_vendor,
            arguments.tier,
            arguments.source_profile,
            arguments.cost_focused,
            arguments.model,
        )
    if arguments.command == "run":
        return run_from_standard_input(
            arguments.policy,
            arguments.source_vendor,
            arguments.ack_route_fingerprint,
            arguments.tier,
            arguments.source_profile,
            arguments.cost_focused,
            arguments.model,
        )
    if arguments.command == "render":
        return render_workflow_route(arguments.policy, arguments.descriptor)
    if arguments.command == "delegate" and arguments.delegate_command == "route":
        return delegation_route(
            arguments.policy,
            arguments.runtime_manifest,
            arguments.delegation_runtime,
            arguments.source_vendor,
            arguments.tier,
            arguments.require_qualified_runtime,
            arguments.source_profile,
        )
    if arguments.command == "delegate" and arguments.delegate_command == "run":
        return delegation_run_from_standard_input(
            arguments.policy,
            arguments.runtime_manifest,
            arguments.delegation_runtime,
            arguments.source_vendor,
            arguments.tier,
            arguments.confirm_trusted_delegation_runtime,
            arguments.ack_route_fingerprint,
            arguments.require_qualified_runtime,
            arguments.source_profile,
        )
    if arguments.command == "delegate" and arguments.delegate_command == "qualification-candidate":
        return delegation_qualification_candidate(
            arguments.evidence,
            arguments.delegation_runtime,
        )
    if arguments.command == "v2" and arguments.api_command == "route":
        return v2_route_from_standard_input(
            arguments.policy,
            arguments.source_vendor,
            arguments.api_runtime,
        )
    if arguments.command == "v2" and arguments.api_command == "run":
        return v2_run_from_standard_input(
            arguments.policy,
            arguments.source_vendor,
            arguments.api_runtime,
            arguments.confirm_api_egress,
            arguments.ack_route_fingerprint,
        )
    print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
    return 2
