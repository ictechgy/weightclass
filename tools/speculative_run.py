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


class VerifyResult(TypedDict):
    passed: bool
    exit_code: int | None
    timed_out: bool
    seconds: float


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
    patch: str
    verify: VerifyResult
    error: str


# 에이전트 런타임이 작업 트리에 흘리는 디렉터리들. 이름으로 아는 수밖에 없다.
# 자식이 만든 점-디렉터리를 전부 버리면 .github 나 .vscode 를 새로 추가하는
# 정당한 변경이 조용히 사라지고, 아무것도 안 버리면 스캐폴딩 수백 줄이 패치와
# 검증 트리에 섞인다. 목록은 틀릴 수 있으므로 --exclude-dir 로 늘릴 수 있고,
# 무엇을 뺐는지는 매번 기록에 남긴다.
# 작업공간 이름의 접두사. mkdtemp 호출부와 삭제 허용 목록이 같은 상수를
# 보게 해서, 한쪽만 바뀌면 --prune 이 조용히 아무것도 못 지우는 일을 막는다.
WORKSPACE_PREFIXES = ("spec-cheap-", "spec-expensive-", "spec-home-")

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
    **os.environ,
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


def run_child(command: list[str], workspace: Path, task: str) -> ChildResult:
    """One vendor invocation. The task goes in on stdin and never into the log.

    Unlike `run_verify`, this inherits the full environment on purpose. The
    child here **is** the agent CLI the user chose, and it needs its own
    credentials — scrubbing `HOME` would leave it unable to authenticate and
    the script unable to do anything at all. Running it exposes exactly what
    running `codex exec` by hand already exposes; this script adds nothing.

    The verifier is a different trust level and is treated differently. It runs
    code the *agent wrote*, which nobody chose and nobody reviewed, so it gets a
    scrubbed environment and an empty `HOME`.
    """
    started = time.monotonic()
    # 자체 프로세스 그룹에서 돌린다. subprocess 의 타임아웃은 직계 자식만
    # 죽이므로, 벤더 CLI 가 띄운 손자들은 "타임아웃" 을 보고한 뒤에도 계속
    # 돌며 작업공간에 쓴다 — 곧 지울 디렉터리에.
    try:
        with subprocess.Popen(
            command,
            cwd=workspace,
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
                    child.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    child.kill()
                # with 블록 안에서 반환하지 않는다. Popen.__exit__ 이 파이프를
                # 닫고 wait() 를 부르는데, 손자가 파이프를 붙들고 있으면 거기서
                # 무한정 막힌다. 죽었는지 확인만 하고 밖에서 반환한다.
                timed_out = True
            else:
                timed_out = False
            code = child.returncode
    except OSError as error:
        raise RunFailure(f"could not start the route: {error}") from error
    if timed_out:
        return {
            "exit_code": None,
            "timed_out": True,
            "seconds": CHILD_TIMEOUT,
            "tokens": None,
        }
    return {
        "exit_code": code,
        "timed_out": False,
        "seconds": round(time.monotonic() - started, 1),
        "tokens": extract_tokens(stdout, stderr),
    }


def extract_tokens(stdout: str, stderr: str) -> int | None:
    """Best-effort token count from whichever vendor produced the output.

    Codex prints a cumulative `tokens used` on stderr. Claude, with
    `--output-format json`, reports a usage object. These two numbers are **not
    comparable to each other** — Claude's includes cache reads and Codex's is
    opaque — so never divide one by the other. Within one vendor they are fine.
    """
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
            try:
                verifier.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                verifier.kill()
        finally:
            # 타임아웃이든 아니든 그룹을 정리한다. 검증기가 백그라운드
            # 프로세스를 띄우고 파이프를 닫은 채 정상 종료하면, 그 손자는
            # 곧 지워질 트리를 붙들고 계속 돈다. with 블록 안에서 반환하지
            # 않는 것도 같은 이유다 — __exit__ 의 wait() 가 거기서 막힌다.
            _kill_group(verifier)
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

    Returns the scaffolding directories it left behind, so the caller can record
    what was dropped rather than let it vanish silently.
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
        if nested.parent != handover and not nested.is_symlink():
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
    temporary.write_text(body, encoding="utf-8")
    temporary.replace(registry)


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
        record["child"] = run_child(command, workspace, task)
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
        record["error"] = f"{type(error).__name__}: {error}"
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
            record["error"] = f"could not write the patch: {error}"
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
    if not os.access(verify, os.X_OK):
        parser.error(f"--verify must be executable: {verify}")

    # 커밋되지 않은 변경은 클론에 들어가지 않는다. 조용히 다른 것을 재는 대신
    # 멈춘다.
    if run_git(["status", "--porcelain"], repo).strip():
        parser.error(f"repository has uncommitted changes; commit or stash first: {repo}")

    task = arguments.task_file.expanduser().read_text(encoding="utf-8")
    commit = head_commit(repo)

    # 라우트 명령 전문은 찍지 않는다. argv 로 넘긴 자격증명이 CI 로그나
    # 화면 캡처에 그대로 남는다. 어떤 실행 파일인지만 알리면 충분하다.
    cheap_argv = shlex.split(arguments.cheap)
    expensive_argv = shlex.split(arguments.expensive)
    print(f"기준 커밋 {commit[:12]}  저장소 {repo}")
    print(f"싼 경로: {cheap_argv[0]} (인자 {len(cheap_argv) - 1}개)")

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
