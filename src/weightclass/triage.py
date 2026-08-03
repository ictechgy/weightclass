"""Ask an already-installed vendor CLI to rate a task's difficulty.

로컬 키워드 판정은 어휘를 볼 뿐 의미를 읽지 못한다. 사람들은 어려운 문제를
전문용어 없이 설명하므로("잔액이 가끔 음수로 내려가요"), 어휘를 아무리 늘려도
도달할 수 없다. 40개 태스크 측정에서 키워드 15/40, 벤더 CLI 33/40 이었다.

여기서 새 런타임을 만들지 않는 것이 핵심이다. wclass run 은 어차피 그 벤더
CLI 를 띄워 태스크를 넘긴다. 같은 CLI 에게 "이거 얼마나 어려워?"를 먼저 묻는
것은 새 자격증명도, 새 과금 경계도, run 기준으로는 새 유출 경로도 만들지
않는다. 이미 설치되어 있고 인증까지 끝난 것을 쓴다.

weightclass 자신은 HTTP 를 하지 않는다. 벤더 CLI 를 전면에서 한 번 실행할
뿐이며, 자격증명과 네트워크는 전적으로 그 CLI 가 소유한다. V2 가 외부 런타임을
다루는 방식과 같은 경계다.
"""

import json
import subprocess
from typing import Final

from .classification import Tier

# 판정 기준은 이 저장소가 소유한다. 벤더 쪽 프롬프트에 의존하면 두 저장소
# 사이에서 기준이 조용히 갈라진다. 버전을 붙여 변경을 추적한다.
TRIAGE_RUBRIC_VERSION: Final = 1
TRIAGE_PROMPT: Final = """\
Rate how much careful reasoning this software task needs.
Answer with exactly one word: low, standard, or high.

low       mechanical, hard to get wrong, minimal reasoning
standard  ordinary engineering judgement
high      subtle, high-stakes, or easy to get subtly wrong

Task:
{task}
"""

# 판정 호출은 짧고 싸야 한다. 실제 작업이 아니라 한 단어를 받는 호출이다.
TRIAGE_COMMANDS: Final = {
    "claude": ("claude", "--print", "--no-session-persistence", "--effort", "low"),
    "codex": (
        "codex",
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "-c",
        "model_reasoning_effort=low",
        "-",
    ),
}

TRIAGE_TIMEOUT_SECONDS: Final = 120
MAX_TRIAGE_OUTPUT_BYTES: Final = 4096
_VALID_TIERS: Final = frozenset({"low", "standard", "high"})


class TriageUnavailableError(RuntimeError):
    """Raised when a vendor could not produce a usable tier."""


def triage_command(source_vendor: str) -> tuple[str, ...]:
    """Return the reviewable command used to ask one vendor for a tier."""
    try:
        return TRIAGE_COMMANDS[source_vendor]
    except KeyError as error:
        raise TriageUnavailableError() from error


def ask_vendor_for_tier(task: str, source_vendor: str) -> Tier:
    """Run one vendor CLI in the foreground and read a tier from its output.

    응답은 한 단어를 기대하지만 모델이 문장으로 답할 수 있으므로 마지막 토큰까지
    본다. 그래도 티어가 아니면 조용히 로컬로 되돌아가지 않고 예외를 던진다.
    판정을 못 했는데 아무 일 없었던 것처럼 진행하면, 라우팅이 틀렸다는 사실이
    호출자에게 보이지 않는다.
    """
    command = triage_command(source_vendor)
    try:
        completed_process = subprocess.run(
            command,
            input=TRIAGE_PROMPT.format(task=task).encode("utf-8"),
            stdout=subprocess.PIPE,
            # 벤더의 진행 표시가 weightclass 진단에 섞이면 안 된다. 이 출력은
            # 사용자가 요청한 결과가 아니라 판정을 위한 중간 산물이다.
            stderr=subprocess.DEVNULL,
            timeout=TRIAGE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise TriageUnavailableError() from error

    if completed_process.returncode != 0:
        raise TriageUnavailableError()

    answer = completed_process.stdout[:MAX_TRIAGE_OUTPUT_BYTES]
    try:
        decoded = answer.decode("utf-8").strip().casefold()
    except UnicodeDecodeError as error:
        raise TriageUnavailableError() from error

    for candidate in (decoded, decoded.split()[-1] if decoded.split() else ""):
        if candidate in _VALID_TIERS:
            return candidate  # type: ignore[return-value]
    raise TriageUnavailableError()


def render_triage_command(source_vendor: str) -> str:
    """Return the triage command as a reviewable single line."""
    return " ".join(triage_command(source_vendor))


def triage_descriptor(source_vendor: str) -> str:
    """Render what a triage call would do, without making it."""
    return json.dumps(
        {
            "source_vendor": source_vendor,
            "command": list(triage_command(source_vendor)),
            "rubric_version": TRIAGE_RUBRIC_VERSION,
        }
    )
