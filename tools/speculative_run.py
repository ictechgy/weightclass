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
    dropped_ignored: list[str]
    accepted: bool
    patch: str
    verify: VerifyResult
    error: str


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


def run_git(arguments: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=cwd, capture_output=True, text=True, timeout=GIT_TIMEOUT
    )
    if result.returncode != 0:
        raise RunFailure(f"git {' '.join(arguments)}: {result.stderr.strip()}")
    return result.stdout


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
    )
    run_git(["checkout", "--quiet", "--detach", commit], destination)
    # origin 이 사용자의 실제 저장소를 가리킨 채 남으면, 자식이 그리로 push
    # 하거나 fetch 로 상태를 흔들 수 있다. 클론이 끝난 뒤 원격을 끊는다.
    run_git(["remote", "remove", "origin"], destination)


def run_child(command: list[str], workspace: Path, task: str) -> ChildResult:
    """One vendor invocation. The task goes in on stdin and never into the log."""
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
                return {
                    "exit_code": None,
                    "timed_out": True,
                    "seconds": CHILD_TIMEOUT,
                    "tokens": None,
                }
            code = child.returncode
    except OSError as error:
        raise RunFailure(f"could not start the route: {error}") from error
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


def run_verify(verify: Path, workspace: Path) -> VerifyResult:
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
        if name in ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "SHELL", "USER")
    }
    with subprocess.Popen(
        [str(verify)],
        cwd=workspace,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    ) as verifier:
        try:
            verifier.communicate(timeout=VERIFY_TIMEOUT)
        except subprocess.TimeoutExpired:
            _kill_group(verifier)
            try:
                verifier.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                verifier.kill()
            return {
                "passed": False,
                "exit_code": None,
                "timed_out": True,
                "seconds": VERIFY_TIMEOUT,
            }
        code = verifier.returncode
    return {
        "passed": code == 0,
        "exit_code": code,
        "timed_out": False,
        "seconds": round(time.monotonic() - started, 1),
    }


def build_handover_tree(repo: Path, commit: str, workspace: Path, handover: Path) -> None:
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
    """
    clone_at(repo, commit, handover)
    # 클론에 있는 최상위 이름이 곧 "이 저장소가 추적하는 것" 이다. 자식이 만든
    # 점-디렉터리(.serena, .omc 같은 에이전트 스캐폴딩)만 걸러내야지, 저장소가
    # 실제로 추적하는 .github 같은 것까지 버리면 그 변경이 조용히 사라진다.
    tracked_top_level = {entry.name for entry in handover.iterdir()} - {".git"}
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
        if entry.name.startswith(".") and entry.is_dir() and entry.name not in tracked_top_level:
            # 자식의 도구가 흘린 것. 패치에도 검증 트리에도 들어가지 않는다.
            continue
        target = handover / entry.name
        # 심링크는 **따라가지 않고 링크 자체로** 복사한다. 기본값은 따라가는
        # 것이라, 자식이 ~/.ssh/id_rsa 를 가리키는 링크를 심어두면 그 내용이
        # 복사되어 패치에 실린다. 사용자가 적용하라고 받는 바로 그 패치다.
        # 링크로 남기면 git 은 mode 120000 으로 기록하고 diff 에 그대로 보인다.
        if entry.is_symlink() or entry.is_file():
            shutil.copy2(entry, target, follow_symlinks=False)
        elif entry.is_dir():
            shutil.copytree(entry, target, symlinks=True)


def make_patch(handover: Path) -> tuple[str, list[str]]:
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
    run_git(["add", "-A"], handover)
    dropped = [line for line in run_git(["clean", "-ndX"], handover).splitlines() if line.strip()]
    if dropped:
        run_git(["clean", "-fdX"], handover)
    patch = run_git(
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
    live.add(str(workspace)) if add else live.discard(str(workspace))
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
        resolved.parent == root
        and resolved.name.startswith(("spec-cheap-", "spec-expensive-"))
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
    keep_on_pass: bool,
) -> Attempt:
    """Clone, run one route, verify. The workspace survives only a pass."""
    workspace = Path(tempfile.mkdtemp(prefix=f"spec-{name}-", dir=out_dir))
    handover = Path(tempfile.mkdtemp(prefix=f"spec-{name}-", dir=out_dir))
    register(registry, workspace, add=True)
    register(registry, handover, add=True)
    record: Attempt = {"route": name, "workspace": str(handover)}
    # 패치 이름에 무작위 접미사를 물려 준다. 고정 이름이면 과제를 20개 재는
    # 동안 out_dir/cheap.patch 를 계속 덮어써 마지막 하나만 남고, 그 20개를
    # 재는 것이 이 스크립트의 목적이다.
    patch = out_dir / f"{handover.name}.patch"
    patch_text = ""
    try:
        clone_at(repo, commit, workspace)
        record["child"] = run_child(command, workspace, task)
        # 자식의 작업을 자식이 손댄 적 없는 클론으로 옮긴 뒤, 패치와 검증을
        # 모두 그 트리에서 한다. 검증한 것과 건네는 것이 같아야 하고, 자식이
        # 오염시킨 .git 위에서는 git 도 검증 스크립트도 돌리지 않는다.
        build_handover_tree(repo, commit, workspace, handover)
        # 패치는 검증 **전에** 뜬다. 검증은 테스트를 돌리므로 __pycache__,
        # 커버리지 파일, 빌드 산출물을 남기고, 나중에 뜨면 그것들이 패치에
        # 섞여 들어가 적용이 깨진다.
        patch_text, dropped = make_patch(handover)
        record["patch_lines"] = patch_text.count("\n")
        record["dropped_ignored"] = dropped
        record["made_changes"] = record["patch_lines"] > 0
        record["verify"] = run_verify(verify, handover)
    except (RunFailure, subprocess.SubprocessError, OSError) as error:
        # RunFailure 만 잡으면 clone_at 의 CalledProcessError 나 복사 중의
        # OSError 가 그대로 올라가, 등록된 채 지워지지 않은 작업공간이 남는다.
        # 이 함수의 계약은 "무슨 일이 있어도 검증 실패로 끝난다" 여야 한다.
        record["error"] = f"{type(error).__name__}: {error}"
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
    record["accepted"] = bool(verdict and verdict["passed"] and record.get("made_changes"))
    if record["accepted"] and keep_on_pass:
        # 검증을 통과한 뒤에야 디스크에 쓴다.
        patch.write_text(patch_text, encoding="utf-8")
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

    print(f"기준 커밋 {commit[:12]}  저장소 {repo}")
    print(f"싼 경로: {arguments.cheap}")

    cheap = attempt(
        "cheap",
        shlex.split(arguments.cheap),
        repo,
        commit,
        task,
        verify,
        arguments.out_dir,
        registry,
        keep_on_pass=True,
    )
    cheap_child = cheap.get("child")
    child_seconds = cheap_child["seconds"] if cheap_child else None
    reason = f" — {cheap['error']}" if not cheap["accepted"] and cheap.get("error") else ""
    print(
        f"  싼 경로 {'통과' if cheap['accepted'] else '실패'}"
        f"  (자식 {child_seconds}s, 검증 {cheap['verify']['seconds']}s){reason}"
    )

    expensive: Attempt | None = None
    record: dict[str, object] = {
        "commit": commit,
        "label": arguments.label,
        "cheap": cheap,
        "escalated": False,
        "expensive": None,
    }

    if not cheap["accepted"]:
        print(f"승급: {arguments.expensive}")
        record["escalated"] = True
        expensive = attempt(
            "expensive",
            shlex.split(arguments.expensive),
            repo,
            commit,
            task,
            verify,
            arguments.out_dir,
            registry,
            keep_on_pass=True,
        )
        record["expensive"] = expensive
        reason = (
            f" — {expensive['error']}"
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
