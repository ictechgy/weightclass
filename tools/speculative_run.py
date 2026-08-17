#!/usr/bin/env python3
"""Run a task on the cheap route first, verify it, and escalate only if it fails.

This is the measurement script from `docs/speculative-cheap-route-design.md`.
It is deliberately **not** part of the weightclass package: it creates and
deletes directories and runs up to two vendor children, all of which V1
forbids. Keeping it here makes it reviewable and versioned without moving that
boundary.

Its job is to produce one number. `p` is the share of tasks where the cheap
route fails verification, and `p` decides whether any of this is worth
building:

    expected cost = c + p        (c = cheap route cost, relative to expensive)

At the measured c = 0.31, break-even is p = 0.69. The cheap route can fail two
times in three and still not lose money. Run this on real work, read `p` off
the log, and only then decide.

What it never does:

- **Touch your repository.** Every attempt happens in a clone under a temp
  directory. The verified patch is written out for you to apply; applying it
  stays a human action.
- **Persist task content or agent output.** The log records outcomes, timings,
  and token counts. Task text and the child's stdout never enter it. That is
  the same rule the router follows.
- **Retry more than once.** Cheap, then one escalation. If the expensive route
  also fails verification, both failures are reported and nothing is retried.

Usage:

    tools/speculative_run.py \\
      --repo ~/work/service --task-file task.txt \\
      --cheap  'codex exec --sandbox workspace-write -c model=cheap-model -' \\
      --expensive 'codex exec --sandbox workspace-write -c model=strong-model -' \\
      --verify ./verify.sh \\
      --out-dir ~/spec-runs

`--verify` is a path to an executable, not a shell string. Put your pipeline in
that file. A string would need a shell, and a shell turns an auditable command
into a quoting exercise; the whole point of this project is exact commands.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TypedDict


class ChildResult(TypedDict):
    exit_code: int | None
    timed_out: bool
    seconds: float
    tokens: int | None
    # 벤더가 말해 주는 만큼의 사용량. cost_usd 가 있으면 그것이 유일하게
    # 벤더 간 비교 가능한 수치다.
    usage: dict[str, object] | None


class VerifyResult(TypedDict):
    passed: bool
    exit_code: int | None
    timed_out: bool
    seconds: float


class Usage(TypedDict, total=False):
    """What one invocation consumed, as far as the vendor will say.

    `cost_usd` is the number that matters and the only one comparable across
    vendors. Claude reports it directly. Codex reports none, so it has to be
    computed from token counts and a price table the caller supplies — which is
    why `--prices` exists. Token counts are **not** comparable between vendors:
    each counts caching differently.
    """

    cost_usd: float
    total_tokens: int
    breakdown: dict[str, int]
    source: str


class Attempt(TypedDict, total=False):
    """One route's clone-run-verify cycle. `total=False` because a step that
    raises leaves the later keys unset, and the caller checks `verify` first."""

    route: str
    workspace: str | None
    child: ChildResult
    made_changes: bool
    patch_lines: int
    # 개수만 남긴다. 경로명은 에이전트가 짓는 문자열이므로 로그에 넣지 않는다.
    dropped_ignored: int
    excluded_scaffolding: list[str]
    accepted: bool
    # "route" = 싼 경로 자체에 대한 판정, "infrastructure" = 도구가 고장난 것.
    # 리포트가 p 에서 무엇을 빼야 하는지 문자열 부분 일치로 추측하지 않도록
    # 여기서 정한다.
    failure_kind: str
    # 벤더 CLI 가 0 이 아닌 코드로 끝나고 변경도 없을 때. 라우트 실패인지
    # 벤더 장애인지 구별할 수 없으므로 리포트가 사람에게 보여 준다.
    child_failed_without_changes: bool
    patch: str
    verify: VerifyResult
    error: str


# 작업공간 이름의 접두사. mkdtemp 호출부와 삭제 허용 목록이 같은 상수를
# 보게 해서, 한쪽만 바뀌면 --prune 이 조용히 아무것도 못 지우는 일을 막는다.
WORKSPACE_PREFIXES = ("spec-cheap-", "spec-expensive-", "spec-home-")

# 벤더 자식이 기본으로 보는 환경. 허용 목록이지 차단 목록이 아니다 — 모르는
# 비밀은 차단 목록으로 막을 수 없고, 이 머신에 어떤 제공자의 키가 있는지
# 우리는 모른다.
#
# 에이전트 CLI 가 동작하는 데 필요한 것과, 그 CLI 자신의 자격증명만 남긴다.
# Codex 실행이 ANTHROPIC_API_KEY 를 볼 이유는 없지만 둘을 구별할 방법이
# 없으므로 양쪽 벤더의 접두사를 모두 통과시킨다. AWS, GitHub, 데이터베이스,
# 사내 시스템의 자격증명은 어느 쪽도 필요로 하지 않으므로 떨군다.
CHILD_ENV_NAMES = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TERM",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        # 사내 프록시와 사설 CA 뒤에서 돌아가는 경우가 흔하다. 이것들이 없으면
        # 인증이 아니라 네트워크가 먼저 끊긴다.
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        # curl 과 Node 계열이 참조한다. 이것만 설정된 사내 환경이 흔하고,
        # 빠지면 인증이 아니라 네트워크에서 먼저 끊긴다.
        "ALL_PROXY",
        "all_proxy",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
        # XDG_ 접두사 전체는 너무 넓다(XDG_RUNTIME_DIR 등). 설정 경로 셋만 둔다.
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
    }
)
# 어떤 실행 파일이 어떤 접두사를 필요로 하는가. Codex 실행이 ANTHROPIC_API_KEY
# 를 볼 이유가 없고, 그 반대도 마찬가지다.
VENDOR_ENV_PREFIXES = {
    "codex": ("OPENAI_", "CODEX_"),
    "claude": ("ANTHROPIC_", "CLAUDE_"),
}
# 실행 파일 이름으로 벤더를 못 알아보면 양쪽을 다 준다. 모르는 CLI 의 인증을
# 우리가 끊어 버리는 것보다는 낫고, 그때도 AWS 나 GitHub 키는 여전히 빠진다.
CHILD_ENV_PREFIXES = ("ANTHROPIC_", "OPENAI_", "CLAUDE_", "CODEX_")

# HOME 을 바꿔도 이것들이 남아 있으면 CLI 가 실제 홈을 먼저 본다. 접미사로
# 훑지 않는 이유는 JAVA_HOME 처럼 홈과 무관한 이름이 같은 모양이기 때문이다.
HOME_REDIRECTING_ENV = (
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
    "XDG_STATE_HOME",
    "CODEX_HOME",
    "CLAUDE_HOME",
    "CLAUDE_CONFIG_DIR",
    "ANTHROPIC_CONFIG_DIR",
    "OPENAI_CONFIG_DIR",
)


def default_child_env(executable: str | None = None) -> frozenset[str]:
    """Names the vendor child keeps when `--child-env-all` is not given.

    When the executable names a vendor we recognise, only that vendor's
    prefixes come through — a Codex run has no business reading an Anthropic
    key. An unrecognised CLI gets both rather than none, because guessing wrong
    in that direction breaks authentication instead of leaking anything new.
    """
    prefixes: tuple[str, ...] = CHILD_ENV_PREFIXES
    if executable:
        for vendor, vendor_prefixes in VENDOR_ENV_PREFIXES.items():
            if vendor in Path(executable).name.lower():
                prefixes = vendor_prefixes
                break
    return frozenset(
        name for name in os.environ if name in CHILD_ENV_NAMES or name.startswith(prefixes)
    )


# 에이전트 런타임이 작업 트리에 흘리는 디렉터리들. 이름으로 아는 수밖에 없다.
# 자식이 만든 점-디렉터리를 전부 버리면 .github 나 .vscode 를 새로 추가하는
# 정당한 변경이 조용히 사라지고, 아무것도 안 버리면 스캐폴딩 수백 줄이 패치와
# 검증 트리에 섞인다. 목록은 틀릴 수 있으므로 --exclude-dir 로 늘릴 수 있고,
# 무엇을 뺐는지는 매번 기록에 남긴다.
AGENT_SCAFFOLDING = frozenset(
    {".serena", ".omc", ".claude", ".codex", ".aider", ".cursor", ".windsurf", ".continue"}
)

# 자식 하나가 걸려도 스크립트가 영원히 매달리지 않게 한다. 벤더 CLI 는 스스로
# 끝나지 않는 경우가 있다.
CHILD_TIMEOUT = 3600
VERIFY_TIMEOUT = 1800
GIT_TIMEOUT = 600

# 토큰 회수는 최선 노력이다. 벤더가 형식을 바꾸면 조용히 None 이 되며, 그것이
# 이 스크립트가 답하려는 질문(p)을 막지는 않는다.
_CODEX_TOKENS = re.compile(r"tokens\s+used[^0-9]{0,20}([0-9][0-9,]*)", re.IGNORECASE)
_CLAUDE_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


class RunFailure(RuntimeError):
    """A step failed in a way that makes the rest of the run meaningless."""


def _kill_group(child: subprocess.Popen[str]) -> None:
    """Kill the whole process group, not just the child we can see."""
    try:
        os.killpg(os.getpgid(child.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        child.kill()


# 인계 트리의 .git/config 는 우리 것이지만, git 은 사용자의 전역/시스템
# config 도 읽는다. 거기 filter.<name>.clean 이 있으면 자식이 심어 온
# .gitattributes 가 그것을 불러낼 수 있다. 이 스크립트가 돌리는 git 은
# 저장소 config 만 보게 한다.
_GIT_ENV = {
    **{
        name: value
        for name, value in os.environ.items()
        # GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE, GIT_ALTERNATE_OBJECT_DIRECTORIES
        # 같은 변수는 우리가 cwd 로 지정한 트리가 아니라 다른 곳을 대상으로
        # 삼게 만든다. 호출자의 셸에 그런 것이 설정돼 있으면 인계 트리가 아닌
        # 저장소를 스테이징하게 된다.
        if not name.startswith("GIT_")
    },
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
}


def _safe(text: str, limit: int = 200) -> str:
    """Strip control characters before printing text the agent could influence.

    Exception messages carry pathnames, and the agent chooses those. A crafted
    filename holding terminal escapes would otherwise repaint the terminal of
    whoever is watching the run.
    """
    cleaned = "".join(character for character in text if character.isprintable())
    return cleaned[:limit] + ("…" if len(cleaned) > limit else "")


def _route_identity(argv: list[str]) -> dict[str, str]:
    """Name a route without echoing its arguments.

    The full command can carry credentials, so the log keeps the executable and
    a digest of the rest. Two runs of the same route match; a changed flag is
    visible as a changed digest without revealing what changed.
    """
    # NUL 로 잇는다. 공백으로 이으면 ["-a b"] 와 ["-a", "b"] 가 같은 지문을
    # 내고, 지문의 목적이 두 실행이 같은 라우트였는지 구별하는 것이다.
    rest = "\0".join(argv[1:]).encode("utf-8")
    return {
        "executable": Path(argv[0]).name,
        "argv_digest": hashlib.sha256(rest).hexdigest()[:16],
        "argv_count": str(len(argv) - 1),
    }


def run_git_bytes(arguments: list[str], cwd: Path) -> bytes:
    """Run git and keep its output as bytes.

    `git diff --binary` emits raw bytes, and a tracked file whose contents are
    not valid UTF-8 makes a text-mode read raise. Decoding with replacement
    would be worse than raising: it would silently corrupt the patch the user
    is told to apply.
    """
    result = subprocess.run(
        ["git", *arguments], cwd=cwd, capture_output=True, timeout=GIT_TIMEOUT, env=_GIT_ENV
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise RunFailure(f"git {' '.join(arguments)}: {detail}")
    return result.stdout


def run_git(arguments: list[str], cwd: Path) -> str:
    return run_git_bytes(arguments, cwd).decode("utf-8", "replace")


def head_commit(repo: Path) -> str:
    return run_git(["rev-parse", "HEAD"], repo).strip()


def clone_at(repo: Path, commit: str, destination: Path) -> None:
    """A full clone, not a worktree.

    `git worktree` shares `.git` with the real repository, so a child that goes
    wrong can reach the shared object store and refs. This mode exists because
    we expect the cheap route to misbehave sometimes; sharing state with the
    thing we are protecting would defeat it. `--no-hardlinks` keeps the object
    store separate too, at the cost of a copy.
    """
    subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", "--no-checkout", str(repo), str(destination)],
        check=True,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
        # 여기에도 같은 환경을 쓴다. 전역 config 의 init.templateDir 은 클론이
        # 만들어질 때 훅을 심을 수 있고, 그 훅은 이후 git 명령에서 실행된다.
        env=_GIT_ENV,
    )
    run_git(["checkout", "--quiet", "--detach", commit], destination)
    # origin 이 사용자의 실제 저장소를 가리킨 채 남으면, 자식이 그리로 push
    # 하거나 fetch 로 상태를 흔들 수 있다. 클론이 끝난 뒤 원격을 끊는다.
    run_git(["remote", "remove", "origin"], destination)


def price_from_tokens(usage: Usage, rates: dict[str, float]) -> float | None:
    """Turn a token breakdown into dollars using caller-supplied rates.

    Only needed for vendors that report no cost. Codex is one: its `--json`
    output carries token counts and no USD anywhere, and the model id is not in
    the events either — so the rates have to be named from the invocation side.

    Rates are USD per million tokens, keyed by the token field they price, and
    **the table drives the sum** — a field the vendor reports but the table does
    not name is deliberately skipped, not an error.

    That direction matters because vendor breakdowns overlap. Codex reports
    `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`,
    `output_tokens`, and `reasoning_output_tokens`. A probe observed
    `cached_input_tokens` (93,952) sitting *below* `input_tokens` (100,507) on
    the same run — which is what a breakout looks like, not a separate line
    item — so pricing all five would double-count. That is evidence, not proof:
    naming the disjoint subset stays the caller's job, because only they can
    check the answer against a real invoice.

    A field the table prices but this run does not report counts as zero, not as
    an error: a run that touched no cache genuinely has no cached tokens, and
    failing there would silently drop the whole run from the cost sample. A rate
    that matches *nothing* across the breakdown is a different matter — that is a
    typo, and it produces None rather than a plausible-looking partial number.
    """
    breakdown = usage.get("breakdown") or {}
    if not breakdown or not rates:
        return None
    if not any(field in breakdown for field in rates):
        return None
    priced = 0.0
    for field, rate in rates.items():
        priced += breakdown.get(field, 0) * rate / 1_000_000
    # 벤더가 준 비용과 --prices 요율은 유한성과 부호를 확인하는데 계산 결과만
    # 확인하지 않으면 기준이 어긋난다. 아주 큰 토큰 수는 곱셈에서 무한대가 될
    # 수 있다.
    return priced if math.isfinite(priced) and priced >= 0 else None


def run_child(
    command: list[str],
    workspace: Path,
    task: str,
    rates: dict[str, float] | None = None,
    allowed_env: frozenset[str] | None = None,
    home: Path | None = None,
) -> ChildResult:
    """One vendor invocation. The task goes in on stdin and never into the log.

    The child **is** the agent CLI the user chose, so it keeps its own
    credentials — take those away and it cannot authenticate at all. What it does
    not keep, by default, is everything else: a task is untrusted input, and an
    agent with shell access reads whatever its environment holds. There is no
    reason a coding run should see `AWS_SECRET_ACCESS_KEY` or a database URL.

    `CHILD_ENV_NAMES`/`CHILD_ENV_PREFIXES` is the default allowlist. `--child-env`
    adds names to it; `--child-env-all` restores the old pass-everything
    behaviour for anyone whose setup needs a variable the list does not know
    about. The run prints how many names it dropped, so a CLI that suddenly
    cannot authenticate points at its own cause.

    **It narrows variables, not the filesystem.** `HOME` survives, because the
    CLI finds its own credentials under it — blank it and nothing authenticates.
    So `~/.aws/credentials` and every other dotfile stay reachable no matter how
    short the variable list is. `--child-home` points `HOME` somewhere else for
    anyone willing to stage the vendor's auth directory there; short of that,
    real isolation means a container, and this script does not pretend to
    provide one.

    The verifier is a different trust level again and is always scrubbed. It
    runs code the *agent wrote*, which nobody chose and nobody reviewed.
    """
    started = time.monotonic()
    # 자체 프로세스 그룹에서 돌린다. subprocess 의 타임아웃은 직계 자식만
    # 죽이므로, 벤더 CLI 가 띄운 손자들은 "타임아웃" 을 보고한 뒤에도 계속
    # 돌며 작업공간에 쓴다 — 곧 지울 디렉터리에.
    environment = (
        {name: value for name, value in os.environ.items() if name in allowed_env}
        if allowed_env is not None
        else None
    )
    if home is not None:
        environment = dict(environment if environment is not None else os.environ)
        environment["HOME"] = str(home)
        # HOME 하나만 바꾸면 격리가 반만 된다. XDG_CONFIG_HOME 이나 CODEX_HOME
        # 같은 변수가 여전히 실제 홈을 가리키면 CLI 는 그쪽을 먼저 본다.
        # 홈 위치를 다시 지목하는 변수는 전부 떨군다 — 남겨서 얻을 것이 없다.
        # 접미사로 훑으면 JAVA_HOME, ANDROID_HOME 처럼 사용자 홈과 무관한
        # 툴체인 경로까지 지워 빌드가 깨진다. 홈을 다시 지목하는 것으로 아는
        # 이름만 명시한다.
        for name in HOME_REDIRECTING_ENV:
            environment.pop(name, None)
    try:
        with subprocess.Popen(
            command,
            cwd=workspace,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            start_new_session=True,
        ) as child:
            try:
                stdout, stderr = child.communicate(task, timeout=CHILD_TIMEOUT)
            except subprocess.TimeoutExpired:
                _kill_group(child)
                try:
                    # 손자가 setsid 로 그룹을 빠져나갔거나 kill 이 막히면
                    # 파이프를 계속 붙들 수 있다. 무한정 기다리지 않는다.
                    stdout, stderr = child.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    child.kill()
                    # 여기까지 오면 파이프에서 읽어낼 방법이 없다. 빈 문자열은
                    # "출력이 없었다" 가 아니라 "읽지 못했다" 라는 뜻이고,
                    # 그래서 이 실행의 토큰은 기록되지 않는다.
                    stdout, stderr = "", ""
                # 타임아웃 여부만 들고 나가 반환은 블록 밖에서 한다. 반환
                # 지점을 한 곳에 모으기 위한 것이지 __exit__ 을 피하려는 것이
                # 아니다 — with 안에서 return 해도 __exit__ 는 똑같이 실행된다.
                timed_out = True
            else:
                timed_out = False
            code = child.returncode
    except OSError as error:
        raise RunFailure(f"could not start the route: {error}") from error
    if timed_out:
        # 시간이 다 됐어도 토큰은 이미 쓰였다. 부분 출력에서 건질 수 있으면
        # 건진다 — 비용에서 빼면 싼 경로가 실제보다 좋아 보인다.
        #
        # 다만 문서가 권하는 두 호출 형태에서는 대개 아무것도 못 건진다.
        # codex --json 의 turn.completed 도, claude --output-format json 의
        # 결과 객체도 마지막에만 나오므로 중간에 죽은 실행에는 없다. 그래도
        # 시도하는 것은 다른 형태로 부르는 사용자를 위해서이고, 못 건진
        # 타임아웃은 cost_usd 없이 기록되어 리포트의 c 표본에서 빠진다.
        partial = extract_usage(stdout, stderr)
        if partial is not None and "cost_usd" not in partial and rates:
            # 정상 경로와 같은 요금 계산을 여기서도 한다. 빠뜨리면 비용을
            # 보고하지 않는 벤더의 타임아웃이 언제나 무비용으로 잡혀, 바로 위
            # 주석이 막으려던 편향이 그대로 남는다.
            computed = price_from_tokens(partial, rates)
            if computed is not None:
                partial["cost_usd"] = computed
                partial["source"] = f"{partial.get('source', '?')}+price-table"
        return {
            "exit_code": None,
            "timed_out": True,
            "seconds": CHILD_TIMEOUT,
            "tokens": partial.get("total_tokens") if partial else None,
            "usage": dict(partial) if partial else None,
        }
    usage = extract_usage(stdout, stderr)
    if usage is not None and "cost_usd" not in usage and rates:
        # 벤더가 비용을 안 알려주는 경우에만 요금표로 계산한다. 벤더가 준
        # 숫자가 있으면 그것이 언제나 우선이다 — 우리 요금표는 낡을 수 있다.
        computed = price_from_tokens(usage, rates)
        if computed is not None:
            usage["cost_usd"] = computed
            usage["source"] = f"{usage.get('source', '?')}+price-table"
    return {
        "exit_code": code,
        "timed_out": False,
        "seconds": round(time.monotonic() - started, 1),
        "tokens": usage.get("total_tokens") if usage else None,
        "usage": dict(usage) if usage else None,
    }


def _claude_usage(stdout: str) -> Usage | None:
    """Claude with `--output-format json` writes one JSON object to stdout.

    Verified against the CLI: `total_cost_usd` sits at the root and is real
    dollars under API-key billing. There is a second copy under
    `modelUsage.<model id>.costUSD`, but those keys are dynamic and contain
    brackets (`claude-opus-5[1m]`), so the root field is the one to read.
    """
    # stdout 전체를 한 번에 파싱하면 CLI 경고나 node deprecation 한 줄만 섞여도
    # 실패한다. 그러면 비용이 통째로 사라지고 리포트는 "비용을 못 얻었다" 만
    # 말한다. 줄 단위로도 찾아본다.
    payload: object = None
    for candidate in (stdout, *stdout.splitlines()):
        text = candidate.strip()
        if not text.startswith("{"):
            continue
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict) and (
            "total_cost_usd" in parsed or parsed.get("type") == "result"
        ):
            payload = parsed
            break
    if not isinstance(payload, dict):
        return None
    raw = payload.get("usage")
    usage_fields = raw if isinstance(raw, dict) else {}
    breakdown: dict[str, int] = {}
    for field in _CLAUDE_USAGE_FIELDS:
        value = usage_fields.get(field)
        if isinstance(value, int) and not isinstance(value, bool):
            breakdown[field] = value
    cost = payload.get("total_cost_usd")
    usage: Usage = {"breakdown": breakdown, "source": "claude-json"}
    if breakdown:
        usage["total_tokens"] = sum(breakdown.values())
    # bool 은 int 의 하위형이라 isinstance 를 그냥 통과한다. true 가 1.0 달러로,
    # 토큰 필드의 true 가 1 토큰으로 기록되는 것을 막는다. 음수도 거른다 —
    # --prices 검증이 같은 기준을 쓰므로 두 경로가 어긋나면 안 된다.
    if (
        not isinstance(cost, bool)
        and isinstance(cost, (int, float))
        and math.isfinite(cost)
        and cost >= 0
    ):
        usage["cost_usd"] = float(cost)
    return usage if breakdown or "cost_usd" in usage else None


def _codex_usage(stdout: str) -> Usage | None:
    """Codex with `--json` writes JSONL; the totals ride on `turn.completed`.

    Verified against the CLI: no USD figure appears anywhere in that output, and
    **stderr is empty in `--json` mode**, so the older `tokens used` scrape
    finds nothing once the flag is on. Scan for the event type rather than
    assuming it is the last line.
    """
    fields = (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    )
    # 실측: 여러 단계를 요구하는 과제(파일 생성 -> 읽기 -> 파생 파일 생성)로
    # 돌려도 `codex exec` 는 turn.completed 를 **한 번만** 낸다. 한 호출이 곧
    # 한 턴이고, 내부의 도구 호출 횟수와는 무관하다. 이 스크립트는 항상 태스크
    # 하나를 stdin 으로 넘기므로 실제로는 언제나 단일 이벤트다.
    #
    # 그래도 합산해 둔다. 대화형 세션처럼 여러 턴이 나오는 형태로 벤더가
    # 바뀌면 첫 이벤트만 읽는 쪽이 조용히 비용을 낮게 잡기 때문이다. 이벤트가
    # 둘 이상이면 source 에 남겨 사람이 증분/누적을 확인할 수 있게 한다.
    totals: dict[str, int] = {}
    seen = False
    turns = 0
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("type") != "turn.completed":
            continue
        raw = event.get("usage")
        if not isinstance(raw, dict):
            continue
        parsed_any = False
        for field in fields:
            value = raw.get(field)
            if isinstance(value, int) and not isinstance(value, bool):
                totals[field] = totals.get(field, 0) + value
                seen = True
                parsed_any = True
        # 알려진 필드를 하나도 못 읽은 이벤트는 세지 않는다. 벤더가 필드 이름을
        # 바꾸면 턴 수만 늘어 다중 턴 경고가 잘못 뜬다.
        if parsed_any:
            turns += 1
    if not seen:
        return None
    # 이벤트가 여러 개면 델타인지 누적인지가 결과를 바꾼다. 프로브는 단일 턴만
    # 봐서 확인하지 못했고, 같은 벤더의 stderr 채널은 누적이었다. 개수를 남겨
    # 1 을 넘으면 사람이 확인할 수 있게 한다. c 는 비율이므로 양쪽 arm 이 같은
    # 방식으로 틀리면 대체로 상쇄된다.

    # cached_input_tokens 와 reasoning_output_tokens 가 각각 input/output 의
    # 내역인지 별도 항목인지는 프로브로 확인되지 않았다. 이중 계산을 피해
    # 총계는 input + output 만으로 낸다.
    total = totals.get("input_tokens", 0) + totals.get("output_tokens", 0)
    # 턴 수는 토큰이 아니므로 breakdown 밖에 둔다. 안에 두면 누가 breakdown 을
    # 합산했을 때 턴 수가 토큰에 섞인다.
    return {
        "breakdown": totals,
        "total_tokens": total,
        "source": f"codex-json({turns}turn)" if turns > 1 else "codex-json",
    }


def extract_usage(stdout: str, stderr: str) -> Usage | None:
    """Best-effort usage from whichever vendor produced the output."""
    for reader in (_claude_usage, _codex_usage):
        usage = reader(stdout)
        if usage:
            return usage
    # 구형 경로: --json 없이 돌린 codex 는 stderr 에 누적 토큰만 찍는다.
    total = extract_tokens(stdout, stderr)
    return {"total_tokens": total, "breakdown": {}, "source": "stderr-scrape"} if total else None


def extract_tokens(stdout: str, stderr: str) -> int | None:
    """The old cumulative-token scrape, kept for runs made without `--json`."""
    matches = _CODEX_TOKENS.findall(stderr) or _CODEX_TOKENS.findall(stdout)
    if matches:
        return int(matches[-1].replace(",", ""))
    try:
        payload = json.loads(stdout)
    except (ValueError, TypeError):
        return None
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return None
    try:
        total = sum(int(usage.get(field, 0) or 0) for field in _CLAUDE_USAGE_FIELDS)
    except (TypeError, ValueError):
        # 독스트링이 약속한 것은 최선 노력이다. 벤더가 문자열이나 실수를 담기
        # 시작하면 조용히 None 이 되어야지, 측정 전체를 멈추면 안 된다.
        return None
    return total or None


def run_verify(verify: Path, workspace: Path, home: Path) -> VerifyResult:
    """The gate. Exit code is the whole verdict; nothing is parsed from output.

    This executes the child's output by design — running the tests is the point
    — so a `conftest.py`, `Makefile`, or `.pth` the child wrote runs with these
    privileges. That cannot be avoided while still verifying anything. The
    clone bounds what it can reach in the repository; it does not bound the
    host. Put the verify command in a container or jail if the output is
    genuinely untrusted.

    Do not use `git` inside the verify script: it would run against a `.git`
    the child could write to, and a planted `filter.<name>.clean` executes on
    the host. `write_patch` takes its diff in a separate clone for this reason.
    """
    started = time.monotonic()
    # 자식이 쓴 코드를 실행하므로, 그것이 띄운 손자까지 확실히 정리해야 한다.
    # 여기서도 프로세스 그룹째 죽인다.
    # 자식이 쓴 코드가 실행되므로 러너의 환경을 통째로 물려주지 않는다. 벤더
    # 토큰이나 자격증명이 환경에 있으면 그대로 읽힌다. 테스트를 돌리는 데
    # 필요한 최소한만 남긴다.
    environment = {
        name: value
        for name, value in os.environ.items()
        if name in ("PATH", "LANG", "LC_ALL", "TZ", "SHELL", "USER")
    }
    # HOME 을 그대로 물려주면 자식이 쓴 테스트가 ~/.ssh, ~/.aws/credentials,
    # ~/.config 를 읽고 셸 rc 를 고칠 수 있다. 빈 디렉터리를 준다. 대부분의
    # 테스트 러너는 쓸 수 있는 HOME 만 있으면 되고, 없으면 오히려 깨진다.
    #
    # 절대 경로로 /Users/<me>/.ssh 를 직접 여는 코드는 이것으로 막지 못한다.
    # 담장이 아니라, `~` 를 쓰는 평범한 도구 경로를 닫는 것이다. 진짜 격리는
    # 검증 명령 자체를 컨테이너나 jail 에 넣는 것뿐이다.
    environment["HOME"] = str(home)
    environment["TMPDIR"] = str(home)
    timed_out = False
    code: int | None = None
    with subprocess.Popen(
        [str(verify)],
        cwd=workspace,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        start_new_session=True,
    ) as verifier:
        try:
            verifier.communicate(timeout=VERIFY_TIMEOUT)
            code = verifier.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            # 여기서는 자식이 아직 회수되지 않았으므로 PID 가 유효하고, 그룹을
            # 죽이는 것이 안전하다. 검증기는 자식이 쓴 코드를 실행하므로 손자
            # 정리가 특히 중요하다.
            _kill_group(verifier)
            try:
                verifier.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                verifier.kill()
        # 정상 종료 경로에서는 그룹을 죽이지 **않는다.** communicate() 가
        # 이미 wait() 로 자식을 회수했으므로 그 PID 는 OS 에 반납된 상태이고,
        # os.getpgid(반납된 PID) 는 재사용된 다른 프로세스의 그룹을 가리킬 수
        # 있다. 거기에 SIGKILL 을 보내면 사용자 머신의 무관한 프로세스를
        # 죽인다. 검증기가 백그라운드 프로세스를 띄우고 정상 종료하면 그것은
        # 살아남는다 — 남의 프로세스를 죽일 위험보다 그편이 낫다.
    if timed_out:
        return {
            "passed": False,
            "exit_code": None,
            "timed_out": True,
            "seconds": VERIFY_TIMEOUT,
        }
    return {
        "passed": code == 0,
        "exit_code": code,
        "timed_out": False,
        "seconds": round(time.monotonic() - started, 1),
    }


def build_handover_tree(
    repo: Path, commit: str, workspace: Path, handover: Path, scaffolding: frozenset[str]
) -> list[str]:
    """Reconstruct the child's work in a clone it never had a handle on.

    Two problems solved by the same move.

    **The child's `.git` is hostile input.** It can write `.git/config` and
    `.gitattributes`, and between them a `filter.<name>.clean` runs on the host
    the next time git stages or diffs that tree. So neither the diff nor the
    verification happens in the workspace the child was given.

    **What is verified must be what is handed over.** The patch is produced by
    `git add`, which honours `.gitignore`, so a file the child created under an
    ignored path would never reach the patch. Verifying the workspace would then
    bless a tree the user cannot reproduce from what they were given. Verifying
    *this* tree cannot drift from the patch, because the patch is taken from it.

    Returns the **top-level** scaffolding directories it left behind. Copies of
    subdirectories filter scaffolding out through `copytree`'s `ignore`, and
    those nested drops are not listed — the count would be misleading either
    way, and the top-level names are what a reader needs to recognise the
    tooling in play.
    """
    excluded: list[str] = []
    clone_at(repo, commit, handover)
    keep = handover / ".git"
    # 순회 중 삭제하면 readdir 동작이 POSIX 상 미정의다. 먼저 목록을 뜬다.
    for entry in list(handover.iterdir()):
        if entry == keep:
            continue
        # is_dir() 은 심링크를 따라가는데 rmtree 는 심링크 인자에 OSError 를
        # 낸다. 디렉터리를 가리키는 추적된 심링크가 있으면 여기서 죽는다.
        if entry.is_symlink() or entry.is_file():
            entry.unlink()
        else:
            shutil.rmtree(entry)
    for entry in workspace.iterdir():
        if entry.name == ".git":
            continue
        if entry.name in scaffolding and entry.is_dir():
            # 자식의 도구가 흘린 것. 패치에도 검증 트리에도 들어가지 않는다.
            excluded.append(entry.name)
            continue
        target = handover / entry.name
        # 심링크는 **따라가지 않고 링크 자체로** 복사한다. 기본값은 따라가는
        # 것이라, 자식이 ~/.ssh/id_rsa 를 가리키는 링크를 심어두면 그 내용이
        # 복사되어 패치에 실린다. 사용자가 적용하라고 받는 바로 그 패치다.
        # 링크로 남기면 git 은 mode 120000 으로 기록하고 diff 에 그대로 보인다.
        if entry.is_symlink() or entry.is_file():
            shutil.copy2(entry, target, follow_symlinks=False)
        elif entry.is_dir():
            # 하위 디렉터리 안의 스캐폴딩도 제외한다. 최상위만 보면
            # services/api/.serena 같은 것이 통째로 실려 온다.
            shutil.copytree(
                entry,
                target,
                symlinks=True,
                ignore=lambda _directory, names: [n for n in names if n in scaffolding],
            )
    return excluded


def tracked_files_unchanged(handover: Path) -> bool:
    """Whether every staged path still holds what it held when it was staged."""
    result = subprocess.run(
        ["git", "diff", "--quiet"],
        cwd=handover,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
        # 다른 git 호출과 같은 환경을 쓴다. 이 트리에는 자식이 심어 온
        # .gitattributes 가 있고, 전역 config 의 filter 와 짝을 이루면
        # 여기서도 실행된다.
        env=_GIT_ENV,
    )
    return result.returncode == 0


def make_patch(handover: Path) -> tuple[bytes, list[str]]:
    """Stage the handover tree, drop what the patch cannot carry, and emit the diff.

    `git add` honours `.gitignore`, so a file the child wrote under an ignored
    path is never in the patch. Leaving it on disk would let verification pass
    on a tree the user cannot rebuild from what they were handed — the cheap
    route could satisfy the tests with a file that silently does not ship.

    So anything staging refused is deleted before the tree is verified, and the
    names are returned so the caller can record that it happened. After this the
    tree is exactly "clean checkout plus the patch".

    The patch is **returned, not written**. Verification runs child-authored
    code with write access to the output directory, so a patch file sitting
    there beforehand can simply be overwritten — and the user would then apply
    something the child wrote rather than what was verified. It reaches disk
    only after the tree it came from has passed.
    """
    # 자식이 하위 디렉터리에서 git init 했다면 git 은 그것을 gitlink(모드
    # 160000) 로 기록한다. 그런 패치는 적용해도 내용이 하나도 오지 않는다.
    # 인계 트리를 만들 때 최상위 .git 만 남기므로, 중첩된 .git 은 자식이
    # 만든 것이고 스캐폴딩과 같은 취급으로 걷어낸다.
    for nested in sorted(handover.rglob(".git")):
        # 바깥쪽을 먼저 지우면 안쪽 경로가 이미 사라진다. rglob 이 미리
        # 만들어 둔 목록이라 그 경로가 다시 검사되므로 존재를 확인한다.
        if nested.parent == handover or nested.is_symlink() or not nested.exists():
            continue
        shutil.rmtree(nested) if nested.is_dir() else nested.unlink()
    run_git(["add", "-A"], handover)
    # `git clean -n` 은 "Would remove <path>" 라는 로케일 의존 문장을 낸다.
    # ls-files 는 경로만 NUL 로 구분해 주므로 파싱할 것이 없다.
    dropped = [
        name
        for name in run_git(
            ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"], handover
        ).split("\0")
        if name
    ]
    if dropped:
        run_git(["clean", "-fdX"], handover)
    patch = run_git_bytes(
        # --binary 가 없으면 바이너리 변경이 "Binary files differ" 로만 남고,
        # 우리가 안내하는 git apply 가 그 패치를 거부한다.
        ["-c", "core.pager=cat", "diff", "--cached", "--binary", "--no-color", "--no-ext-diff"],
        handover,
    )
    return patch, dropped


def write_registry(registry: Path, entries: list[str]) -> None:
    """Replace the registry atomically.

    This file exists to survive a crash. Rewriting it in place would let a
    crash mid-write leave a truncated list, which defeats the one job it has.

    It is not safe against two runs sharing an `--out-dir` concurrently: the
    read-modify-write can lose an entry, and that workspace then survives
    `--prune`. Give concurrent measurements separate `--out-dir` values.
    """
    body = "".join(f"{path}\n" for path in entries)
    temporary = registry.with_suffix(f".{os.getpid()}.tmp")
    # 내용과 디렉터리 엔트리를 모두 내려쓴다. replace 는 원자적이지만, 그
    # 원자성은 캐시에 대한 것이라 전원이 끊기면 이름만 바뀌고 내용은 비어
    # 있을 수 있다. 크래시를 견디는 것이 이 파일의 유일한 목적이다.
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(registry)
    directory = os.open(registry.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def register(registry: Path, workspace: Path, add: bool) -> None:
    """Track live workspaces on disk.

    A `finally` block does not survive SIGKILL or a machine losing power, and a
    workspace left behind holds a half-finished agent run. The registry is the
    crash-safe half; `--prune` is the other half.
    """
    live = set()
    if registry.exists():
        live = {line for line in registry.read_text(encoding="utf-8").splitlines() if line.strip()}
    if add:
        live.add(str(workspace))
    else:
        live.discard(str(workspace))
    write_registry(registry, sorted(live))


def discard(registry: Path, workspace: Path, out_dir: Path) -> None:
    """Delete a failed attempt's workspace, and keep it registered if we cannot.

    Silently swallowing the error and unregistering would leave a tree of
    untrusted output on disk with nothing left pointing at it. Staying in the
    registry is what makes `--prune` able to finish the job later.
    """
    target = resolved_own_workspace(workspace, out_dir)
    if target is None:
        # 지우지 않았으므로 등록에서도 빼지 않는다. 디렉터리는 남아 있는데
        # 참조만 사라지는 것이 이 함수가 막으려는 바로 그 상태다.
        print(f"작업공간을 확인할 수 없어 등록에 남긴다: {workspace}", file=sys.stderr)
        return
    try:
        shutil.rmtree(target)
    except OSError as error:
        # 권한을 고쳐서 재시도하지 않는다. 경로 기반 chmod 는 심링크를 따라가고
        # 검사와 사용 사이에 갈아끼울 틈이 있어, 자식이 트리 밖의 권한을 바꾸게
        # 만들 수 있다. 지우지 못한 것은 등록에 남겨 사람이 보게 하는 편이 낫다.
        print(f"작업공간을 지우지 못했다, 등록에 남긴다: {target} ({error})", file=sys.stderr)
        return
    register(registry, workspace, add=False)


def resolved_own_workspace(candidate: Path, out_dir: Path) -> Path | None:
    """Whether a registry line names a directory this script actually created.

    The registry is a plain file. It can be edited, corrupted by a partial
    write, or left over from a different `--out-dir`. Deleting whatever it says
    would make a stray line into an arbitrary `rm -rf`, so a line is honoured
    only if it is a direct child of this run's `--out-dir` and carries the
    prefix `mkdtemp` was given. Symlinks are resolved first so a link cannot
    point the deletion somewhere else.
    """
    if candidate.is_symlink():
        return None
    try:
        resolved = candidate.resolve(strict=True)
        root = out_dir.resolve(strict=True)
    except OSError:
        return None
    accepted = (
        # 작업공간은 out_dir/.work 아래에만 만들어진다.
        resolved.parent == root / ".work"
        and resolved.name.startswith(WORKSPACE_PREFIXES)
        and resolved.is_dir()
    )
    # 검사한 경로를 그대로 돌려준다. 검사와 삭제가 서로 다른 경로를 보면
    # 그 사이에 링크를 갈아끼워 다른 곳을 지우게 만들 수 있다.
    return resolved if accepted else None


def prune(registry: Path, out_dir: Path) -> int:
    if not registry.exists():
        print("등록된 작업공간 없음")
        return 0
    live = [line for line in registry.read_text(encoding="utf-8").splitlines() if line.strip()]
    removed = 0
    kept: list[str] = []
    for line in live:
        candidate = Path(line)
        if not candidate.exists():
            continue
        target = resolved_own_workspace(candidate, out_dir)
        if target is None:
            # 우리가 만든 것이 아니면 지우지도 않고 잊지도 않는다. 잊으면
            # 사람이 확인할 마지막 단서가 사라진다.
            print(f"건너뜀(이 스크립트가 만든 작업공간이 아님): {line}")
            kept.append(line)
            continue
        try:
            shutil.rmtree(target)
        except OSError as error:
            print(f"삭제 실패, 등록에 남긴다: {target} ({error})")
            kept.append(line)
            continue
        removed += 1
        print(f"삭제: {target}")
    # 지운 것만 등록에서 뺀다. 통째로 비우면 아직 디스크에 남아 있는 신뢰할 수
    # 없는 트리를 가리키는 유일한 참조가 사라진다.
    write_registry(registry, kept)
    print(f"{removed}개 정리 완료 (등록 {len(live)}개, 남김 {len(kept)}개)")
    return 0


def attempt(
    name: str,
    command: list[str],
    repo: Path,
    commit: str,
    task: str,
    verify: Path,
    out_dir: Path,
    registry: Path,
    scaffolding: frozenset[str],
    rates: dict[str, float] | None,
    allowed_env: frozenset[str] | None,
    child_home: Path | None,
) -> Attempt:
    """Clone, run one route, verify. The workspace survives only a pass."""
    # 작업공간은 산출물 디렉터리 바로 아래가 아니라 그 안의 .work 에 만든다.
    # 검증 스크립트의 cwd 가 인계 트리이므로 `..` 가 곧 산출물 디렉터리가 되면,
    # 한 과제의 악의적 검증이 앞선 과제들의 이미 승인된 패치를 덮어쓴다. 20개를
    # 한 디렉터리에 모아 재는 것이 이 스크립트의 의도된 사용법이다.
    #
    # 이것은 문턱을 올릴 뿐 담장이 아니다. 검증은 사용자 권한으로 도는 자식의
    # 코드이므로 사용자가 닿는 곳이면 어디든 닿는다. 진짜 격리는 검증 명령
    # 자체를 컨테이너나 jail 에 넣는 것뿐이고, 설계 문서가 그렇게 권한다.
    work_root = out_dir / ".work"
    work_root.mkdir(mode=0o700, exist_ok=True)
    # 만드는 즉시 등록한다. 둘을 만들고 나서 등록하면 그 사이에 실패했을 때
    # 첫 번째가 아무도 가리키지 않는 채 디스크에 남는다.
    workspace = Path(tempfile.mkdtemp(prefix=f"spec-{name}-", dir=work_root))
    register(registry, workspace, add=True)
    handover = Path(tempfile.mkdtemp(prefix=f"spec-{name}-", dir=work_root))
    register(registry, handover, add=True)
    record: Attempt = {"route": name, "workspace": str(handover)}
    # 패치 이름에 무작위 접미사를 물려 준다. 고정 이름이면 과제를 20개 재는
    # 동안 out_dir/cheap.patch 를 계속 덮어써 마지막 하나만 남고, 그 20개를
    # 재는 것이 이 스크립트의 목적이다.
    patch = out_dir / f"{handover.name}.patch"
    patch_bytes = b""
    build_scaffolding: list[str] = []
    try:
        clone_at(repo, commit, workspace)
        record["child"] = run_child(command, workspace, task, rates, allowed_env, child_home)
        # 자식의 작업을 자식이 손댄 적 없는 클론으로 옮긴 뒤, 패치와 검증을
        # 모두 그 트리에서 한다. 검증한 것과 건네는 것이 같아야 하고, 자식이
        # 오염시킨 .git 위에서는 git 도 검증 스크립트도 돌리지 않는다.
        build_scaffolding = build_handover_tree(repo, commit, workspace, handover, scaffolding)
        # 패치는 검증 **전에** 뜬다. 검증은 테스트를 돌리므로 __pycache__,
        # 커버리지 파일, 빌드 산출물을 남기고, 나중에 뜨면 그것들이 패치에
        # 섞여 들어가 적용이 깨진다.
        record["excluded_scaffolding"] = build_scaffolding
        patch_bytes, dropped = make_patch(handover)
        record["patch_lines"] = patch_bytes.count(b"\n")
        # 경로명 자체를 남기지 않는다. 파일 이름은 에이전트가 짓고, 거기에
        # 태스크 내용이나 자격증명을 실을 수 있다. 이 스크립트가 내건 계약은
        # 결과와 수치만 기록한다는 것이므로 개수만 남긴다.
        record["dropped_ignored"] = len(dropped)
        record["made_changes"] = record["patch_lines"] > 0
        verify_home = Path(tempfile.mkdtemp(prefix="spec-home-", dir=work_root))
        register(registry, verify_home, add=True)
        try:
            record["verify"] = run_verify(verify, handover, verify_home)
        finally:
            discard(registry, verify_home, out_dir)
        # 벤더 CLI 가 0 이 아닌 코드로 죽었고 아무것도 바꾸지 않았다면, 그것이
        # 라우트의 실패인지(못 해냈다) 도구의 실패인지(인증 만료, 쿼터 소진,
        # 네트워크) 여기서는 구별할 수 없다. 추측해서 p 에 넣으면 벤더 장애가
        # "싼 모델이 나쁘다" 로 둔갑하므로, 표시만 하고 사람이 보게 한다.
        child = record["child"]
        if child["exit_code"] not in (0, None) and not record.get("made_changes"):
            record["child_failed_without_changes"] = True
        # 검증은 자식이 쓴 코드를 실행하므로 인계 트리를 바꿀 수 있다. 바뀌면
        # 통과한 트리와 우리가 건네는 패치가 더 이상 같은 것이 아니다.
        #
        # 물어야 할 것은 "검증이 **패치에 담긴 것**을 바꿨나" 이지 "검증이
        # 흔적을 남겼나" 가 아니다. 테스트를 돌리면 __pycache__ 는 당연히
        # 생기고 그것은 패치에 없다. 인덱스에는 방금 스테이징한 에이전트의
        # 작업이 들어 있으므로, 작업 트리와 인덱스를 비교하면 정확히 그
        # 질문에 답한다. 새로 생긴 미추적 파일은 여기 잡히지 않는다.
        if not tracked_files_unchanged(handover):
            record["verify"]["passed"] = False
            record["error"] = "verification modified the patched files; patch no longer matches"
            record["failure_kind"] = "route"
    except (RunFailure, subprocess.SubprocessError, OSError) as error:
        # RunFailure 만 잡으면 clone_at 의 CalledProcessError 나 복사 중의
        # OSError 가 그대로 올라가, 등록된 채 지워지지 않은 작업공간이 남는다.
        # 이 함수의 계약은 "무슨 일이 있어도 검증 실패로 끝난다" 여야 한다.
        # 예외 문구에는 파일 경로가 들어가고 그 이름은 에이전트가 짓는다.
        # 종류만 남긴다 — failure_kind 와 함께 무엇이 고장났는지 가리기에는
        # 충분하고, 로그에 태스크 유래 문자열을 넣지 않는다는 계약을 지킨다.
        record["error"] = type(error).__name__
        record["failure_kind"] = "infrastructure"
        record["verify"] = {"passed": False, "exit_code": None, "timed_out": False, "seconds": 0}

    # 자식이 돌던 워크스페이스는 어느 쪽이든 항상 버린다. 넘길 것은 재구성한
    # 트리이고, 자식의 .git 을 살려 둘 이유가 없다.
    discard(registry, workspace, out_dir)

    verdict = record.get("verify")
    # 변경이 없으면 통과로 치지 않는다. 저장소의 기존 테스트는 이미 초록이므로,
    # 아무것도 하지 않은 자식은 검증을 그냥 통과한다. 그것을 성공으로 세면 p 가
    # 거짓으로 낮아지고, p 를 재는 것이 이 스크립트의 유일한 목적이다.
    #
    # `accepted` 가 이 시도의 유일한 판정이다. verify.passed 는 검증 명령이
    # 무엇을 말했는지를 남길 뿐이고, 호출자는 accepted 만 본다. 둘을 따로 보면
    # 서로 다른 답을 내는 곳이 생긴다.
    if verdict and verdict["passed"] and not record.get("made_changes"):
        record["error"] = "route made no change; not counted as a pass"
        record["failure_kind"] = "route"
    record["accepted"] = bool(verdict and verdict["passed"] and record.get("made_changes"))
    if record["accepted"]:
        # 검증을 통과한 뒤에야 디스크에 쓴다. 여기서 실패해도 이 함수의 계약은
        # 지켜야 한다 — 무슨 일이 있어도 판정을 남기고 정상 반환한다.
        try:
            patch.write_bytes(patch_bytes)
            # 승인된 패치는 읽기 전용으로 둔다. 뒤 과제의 검증이 무심코 훑고 쓰는
            # 것은 막지만, 작정한 코드는 chmod 로 되돌릴 수 있다. 담장이 아니라
            # 실수에 대한 방어다.
            patch.chmod(0o400)
        except OSError as error:
            record["accepted"] = False
            record["error"] = f"could not write the patch: {type(error).__name__}"
            record["failure_kind"] = "infrastructure"
            discard(registry, handover, out_dir)
            record["workspace"] = None
            return record
        record["patch"] = str(patch)
    else:
        discard(registry, handover, out_dir)
        record["workspace"] = None
    return record


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--prune", action="store_true", help="delete every registered workspace and exit"
    )
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--task-file", type=Path)
    parser.add_argument("--cheap", help="exact command for the cheap route")
    parser.add_argument("--expensive", help="exact command for the escalation route")
    parser.add_argument("--verify", type=Path, help="executable; exit code is the verdict")
    parser.add_argument("--label", default="", help="free-text tag recorded with the run")
    parser.add_argument(
        "--child-env",
        action="append",
        default=[],
        help=(
            "extra environment variable the vendor child may see (repeatable). "
            "By default it gets PATH/HOME/locale/proxy plus its own vendor's namespace "
            "(OPENAI_*/CODEX_* for codex, ANTHROPIC_*/CLAUDE_* for claude, both when the "
            "executable name matches neither) — everything else is dropped."
        ),
    )
    parser.add_argument(
        "--child-env-all",
        action="store_true",
        help="pass the entire environment to the vendor child, as older versions did",
    )
    parser.add_argument(
        "--child-home",
        type=Path,
        help=(
            "HOME for the vendor child. --child-env narrows variables but not the "
            "filesystem, and the CLI reads its credentials from HOME, so dotfiles stay "
            "reachable unless you point it elsewhere and stage the vendor's auth there."
        ),
    )
    parser.add_argument(
        "--prices",
        type=Path,
        help=(
            "JSON file with USD-per-million-token rates, for vendors that report no cost "
            '(Codex). Shape: {"cheap": {"input_tokens": 0.25, ...}, "expensive": {...}}'
        ),
    )
    parser.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        help="extra agent-scaffolding directory to keep out of the patch (repeatable)",
    )
    arguments = parser.parse_args()

    arguments.out_dir = arguments.out_dir.expanduser().resolve()
    arguments.out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    # mkdir(mode=...) 는 디렉터리가 이미 있으면 아무것도 하지 않고, parents=True
    # 로 만들어진 상위 디렉터리는 기본 umask 를 받는다. 작업공간에는 에이전트가
    # 쓴 코드가 들어가므로 소유자 전용을 실제로 강제한다.
    arguments.out_dir.chmod(0o700)
    registry = arguments.out_dir / "workspaces.txt"
    log = arguments.out_dir / "runs.jsonl"

    if arguments.prune:
        return prune(registry, arguments.out_dir)

    missing = [
        name
        for name, value in (
            ("--repo", arguments.repo),
            ("--task-file", arguments.task_file),
            ("--cheap", arguments.cheap),
            ("--expensive", arguments.expensive),
            ("--verify", arguments.verify),
        )
        if value is None
    ]
    if missing:
        parser.error(f"required unless --prune: {', '.join(missing)}")

    for name, raw in (("--cheap", arguments.cheap), ("--expensive", arguments.expensive)):
        try:
            parsed = shlex.split(raw)
        except ValueError as error:
            parser.error(f"{name} is not a parseable command: {error}")
        if not parsed:
            parser.error(f"{name} is empty; it must name an executable command")

    repo = arguments.repo.expanduser().resolve()
    verify = arguments.verify.expanduser().resolve()
    if not (repo / ".git").exists():
        parser.error(f"not a git repository: {repo}")
    # os.access(X_OK) 는 디렉터리의 탐색 비트에도 참이라, --verify 에
    # 디렉터리를 줘도 통과한 뒤 실행 시점에 PermissionError 로 죽었다.
    if not verify.is_file() or not os.access(verify, os.X_OK):
        parser.error(f"--verify must be an executable file: {verify}")

    # 커밋되지 않은 변경은 클론에 들어가지 않는다. 조용히 다른 것을 재는 대신
    # 멈춘다.
    if run_git(["status", "--porcelain"], repo).strip():
        parser.error(f"repository has uncommitted changes; commit or stash first: {repo}")

    task_file = arguments.task_file.expanduser()
    try:
        task = task_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        parser.error(f"--task-file is not readable as UTF-8 text: {error}")
    commit = head_commit(repo)

    # 라우트 명령 전문은 찍지 않는다. argv 로 넘긴 자격증명이 CI 로그나
    # 화면 캡처에 그대로 남는다. 어떤 실행 파일인지만 알리면 충분하다.
    cheap_argv = shlex.split(arguments.cheap)
    expensive_argv = shlex.split(arguments.expensive)
    print(f"기준 커밋 {commit[:12]}  저장소 {repo}")
    print(f"싼 경로: {cheap_argv[0]} (인자 {len(cheap_argv) - 1}개)")

    rates: dict[str, dict[str, float]] = {}
    if arguments.prices:
        try:
            loaded = json.loads(arguments.prices.expanduser().read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            parser.error(f"--prices is not readable JSON: {error}")
        if not isinstance(loaded, dict):
            parser.error("--prices must be a JSON object keyed by 'cheap' and 'expensive'")
        unknown = set(loaded) - {"cheap", "expensive"}
        if unknown:
            # 오타난 키를 조용히 무시하면 그 arm 의 요금표가 비어 비용이 전혀
            # 계산되지 않고, 사용자는 리포트에서 "비용을 못 얻었다" 만 본다.
            parser.error(f"--prices has unknown keys: {', '.join(sorted(unknown))}")
        for arm in ("cheap", "expensive"):
            table = loaded.get(arm)
            if table is None:
                continue
            if not isinstance(table, dict):
                parser.error(f"--prices['{arm}'] must be an object of token field rates")
            if not table:
                # 빈 표는 all() 이 True 라 통과한 뒤 그 arm 을 조용히 무요금으로
                # 만든다. 키를 적어 두고 값을 비운 것은 실수일 가능성이 높다.
                parser.error(f"--prices['{arm}'] is empty; remove the key or fill it in")
            if not all(
                # bool 은 int 의 하위형이고, json.loads 는 기본으로 NaN/Infinity 를
                # 허용한다. 둘 다 그럴듯한 비용을 만들어 낸다.
                not isinstance(v, bool)
                and isinstance(v, (int, float))
                and math.isfinite(v)
                and v >= 0
                for v in table.values()
            ):
                parser.error(
                    f"--prices['{arm}'] must map token field names to finite, non-negative numbers"
                )
            rates[arm] = {k: float(v) for k, v in table.items()}

    # 기본이 허용 목록이다. 모르는 비밀은 차단 목록으로 막을 수 없다.
    # arm 마다 실행 파일이 다르므로 허용 목록도 arm 마다 만든다.
    def env_for(argv: list[str]) -> frozenset[str] | None:
        if arguments.child_env_all:
            return None
        name = Path(argv[0]).name.lower()
        if not any(vendor in name for vendor in VENDOR_ENV_PREFIXES):
            print(
                f"  주의: '{Path(argv[0]).name}' 에서 벤더를 알아보지 못해 양쪽 벤더의"
                " 키를 모두 전달한다. --child-env 로 좁힐 수 있다."
            )
        return default_child_env(argv[0]) | frozenset(arguments.child_env)

    cheap_env = env_for(cheap_argv)
    expensive_env = env_for(expensive_argv)
    # 진단은 두 arm 을 함께 본다. 하나만 찍으면 다른 쪽이 다른 벤더로 판별돼
    # 다른 목록을 받는데도 사용자는 알 수 없다.
    allowed_env = cheap_env if cheap_env is None else cheap_env | (expensive_env or frozenset())
    child_home = arguments.child_home.expanduser().resolve() if arguments.child_home else None
    if child_home is not None and not child_home.is_dir():
        parser.error(f"--child-home is not a directory: {child_home}")
    # 프록시 URL 은 http://user:pass@host 형태가 흔하고 그 userinfo 는 그대로
    # 자격증명이다. 자식에게 넘기지 않으면 네트워크가 끊기므로 넘기되, 그것이
    # 비밀을 넘기는 일이라는 사실은 알린다.
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        value = os.environ.get(name, "")
        if "@" in value and (allowed_env is None or name in allowed_env):
            print(f"  주의: {name} 에 자격증명이 들어 있고 자식에게 전달된다")
            break

    if allowed_env is None:
        print("자식 환경: 전체 전달 (--child-env-all)")
    else:
        dropped = len(set(os.environ) - allowed_env)
        print(f"자식 환경: {len(allowed_env & set(os.environ))}개 전달, {dropped}개 제외")
        if child_home is None:
            print(
                "  HOME 은 그대로다. 변수만 좁혔을 뿐 ~/.aws/credentials 같은 파일은"
                " 여전히 읽힌다. --child-home 이나 컨테이너가 필요하다."
            )
        else:
            print(
                "  --child-home 은 두 arm 이 함께 쓰고 자식이 쓸 수 있다. 싼 경로가"
                " 거기 남긴 설정을 승급 경로가 물려받는다. 그것이 문제라면 실행마다"
                " 새 디렉터리를 주거나 컨테이너를 써야 한다."
            )

    scaffolding = AGENT_SCAFFOLDING | set(arguments.exclude_dir)
    cheap = attempt(
        "cheap",
        cheap_argv,
        repo,
        commit,
        task,
        verify,
        arguments.out_dir,
        registry,
        scaffolding=frozenset(scaffolding),
        rates=rates.get("cheap"),
        allowed_env=cheap_env,
        child_home=child_home,
    )
    cheap_child = cheap.get("child")
    child_seconds = cheap_child["seconds"] if cheap_child else None
    reason = f" — {_safe(cheap['error'])}" if not cheap["accepted"] and cheap.get("error") else ""
    print(
        f"  싼 경로 {'통과' if cheap['accepted'] else '실패'}"
        f"  (자식 {child_seconds}s, 검증 {cheap['verify']['seconds']}s){reason}"
    )

    expensive: Attempt | None = None
    record: dict[str, object] = {
        "commit": commit,
        "label": arguments.label,
        # 어떤 라우트로 잰 p 인지 남긴다. 명령 전문은 argv 에 자격증명이 있을
        # 수 있으므로 실행 파일 이름과 인자 지문만 남긴다.
        "routes": {
            "cheap": _route_identity(cheap_argv),
            "expensive": _route_identity(expensive_argv),
        },
        "cheap": cheap,
        "escalated": False,
        "expensive": None,
    }

    if not cheap["accepted"]:
        print(f"승급: {expensive_argv[0]} (인자 {len(expensive_argv) - 1}개)")
        record["escalated"] = True
        expensive = attempt(
            "expensive",
            expensive_argv,
            repo,
            commit,
            task,
            verify,
            arguments.out_dir,
            registry,
            scaffolding=frozenset(scaffolding),
            rates=rates.get("expensive"),
            allowed_env=expensive_env,
            child_home=child_home,
        )
        record["expensive"] = expensive
        reason = (
            f" — {_safe(expensive['error'])}"
            if not expensive["accepted"] and expensive.get("error")
            else ""
        )
        print(f"  승급 경로 {'통과' if expensive['accepted'] else '실패'}{reason}")

    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    winner: Attempt | None = None
    if cheap["accepted"]:
        winner = cheap
    elif expensive is not None and expensive["accepted"]:
        winner = expensive

    if winner:
        print(f"\n검증 통과. 패치: {winner['patch']}  ({winner['patch_lines']}줄)")
        print(f"적용: git -C {shlex.quote(str(repo))} apply {shlex.quote(str(winner['patch']))}")
        print(f"작업공간: {winner['workspace']}")
        return 0

    print("\n두 경로 모두 검증 실패. 작업공간은 지웠다. 재시도하지 않는다.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
