"""Command-line interface for rendering, never executing, route commands."""

import argparse
import json
import subprocess
import sys
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final, NoReturn, cast

from . import __version__
from .classification import (
    InvalidTaskError,
    Tier,
    classify_task,
    read_task_from_standard_input,
)
from .router import (
    DEFAULT_ROUTES,
    SUPPORTED_VENDORS,
    Route,
    RouteRequest,
    RouteSelectionError,
    RoutingPolicy,
    native_route_fingerprint,
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

EXECUTOR_FAILED_EXIT_CODE: Final = 7


class InvalidInputError(ValueError):
    """Raised for invalid policy or descriptor data without exposing it."""


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


def _parse_route(value: object) -> Route:
    if not isinstance(value, dict):
        raise InvalidInputError()

    keys = set(value)
    has_workflow = keys == {"id", "vendor", "workflow", "command"}
    has_tier = keys == {"id", "vendor", "tier", "command"}
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
    return Route(
        route_id=route_id,
        vendor=vendor,
        workflow=workflow,
        command=tuple(_require_command_argument(argument) for argument in command),
        tier=tier,
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


def _add_api_route_arguments(parser: argparse.ArgumentParser) -> None:
    """Declare the arguments shared by both V2 API subcommands."""
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--source-vendor", required=True, choices=sorted(SUPPORTED_VENDORS))
    parser.add_argument("--api-runtime", required=True, type=Path)


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

    subcommands.add_parser(
        "classify",
        allow_abbrev=False,
        description="Print the tier of a task read from standard input.",
    )
    for name, description in (
        ("route", "Select and print a command for a task read from standard input."),
        ("run", "Select and start a command for a task read from standard input."),
    ):
        native = subcommands.add_parser(name, allow_abbrev=False, description=description)
        native.add_argument("--policy", type=Path)
        native.add_argument("--source-vendor", choices=sorted(SUPPORTED_VENDORS))
        if name == "run":
            native.add_argument("--ack-route-fingerprint")

    render = subcommands.add_parser(
        "render",
        allow_abbrev=False,
        description="Render the command of a policy route named by a workflow descriptor.",
    )
    render.add_argument("--policy", required=True, type=Path)
    render.add_argument("--descriptor", required=True, type=Path)

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
    except (OSError, ValueError):
        # ValueError 는 argv 를 실제로 인코딩하는 단계에서 나온다(NUL, 서로게이트).
        # 검증기가 이미 막고 있지만, 규칙에 빈틈이 생겨도 트레이스백 대신
        # 진단으로 닫히도록 두 번째 방어선을 둔다.
        print(json.dumps({"error": "executor_unavailable"}), file=sys.stderr)
        return 4
    return _report_executor_result(completed_process)


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
) -> tuple[Tier, Route, RoutingPolicy]:
    """Classify a task and select its route without retaining task content."""
    tier = classify_task(task)
    policy = (
        load_routing_policy(policy_path)
        if policy_path is not None
        else RoutingPolicy(DEFAULT_ROUTES)
    )
    route = select_tier_route(
        policy.routes,
        tier,
        source_vendor,
        policy.allow_mixed_vendors,
    )
    return tier, route, policy


def route_from_standard_input(policy_path: Path | None, source_vendor: str | None) -> int:
    """Select and render a command without echoing or persisting the task."""
    try:
        tier, route, policy = select_task_route(
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
        # 이 지문을 wclass run --ack-route-fingerprint 로 넘기면 검토한 선택이
        # 실행 직전에 다시 확인된다. 넘기지 않으면 구속력은 없다.
        "route_fingerprint": native_route_fingerprint(route, tier, policy.allow_mixed_vendors),
    }
    print(json.dumps(response))
    return 0


def run_from_standard_input(
    policy_path: Path | None,
    source_vendor: str | None,
    acknowledged_fingerprint: str | None = None,
) -> int:
    """Run a selected native command without a shell or output capture."""
    try:
        task = read_task_from_standard_input()
        tier, route, policy = select_task_route(task, policy_path, source_vendor)
        if acknowledged_fingerprint is not None and acknowledged_fingerprint != (
            native_route_fingerprint(route, tier, policy.allow_mixed_vendors)
        ):
            print(json.dumps({"error": "route_fingerprint_mismatch"}), file=sys.stderr)
            return 6
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
    except (OSError, ValueError):
        # ValueError 는 argv 를 실제로 인코딩하는 단계에서 나온다(NUL, 서로게이트).
        # 검증기가 이미 막고 있지만, 규칙에 빈틈이 생겨도 트레이스백 대신
        # 진단으로 닫히도록 두 번째 방어선을 둔다.
        print(json.dumps({"error": "executor_unavailable"}), file=sys.stderr)
        return 4
    return _report_executor_result(completed_process)


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

    if arguments.command == "classify":
        return classify_from_standard_input()
    if arguments.command == "route":
        return route_from_standard_input(arguments.policy, arguments.source_vendor)
    if arguments.command == "run":
        return run_from_standard_input(
            arguments.policy,
            arguments.source_vendor,
            arguments.ack_route_fingerprint,
        )
    if arguments.command == "render":
        return render_workflow_route(arguments.policy, arguments.descriptor)
    if arguments.api_command == "route":
        return v2_route_from_standard_input(
            arguments.policy,
            arguments.source_vendor,
            arguments.api_runtime,
        )
    return v2_run_from_standard_input(
        arguments.policy,
        arguments.source_vendor,
        arguments.api_runtime,
        arguments.confirm_api_egress,
        arguments.ack_route_fingerprint,
    )
