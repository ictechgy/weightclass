"""조언 경로가 벤더로 내보내는 텍스트의 리댁션 테스트.

이 파일이 존재하는 이유가 있다. 리뷰 6라운드 동안 리댁션에서 여섯 번
연속으로 유출이 나왔고, 그중 한 번은 내가 직전 라운드에 "확인했다" 고
보고한 항목이었다 — 테스트에 쓴 JSON 키가 짧아서 END 마커가 우연히 탐색
범위 안에 들어왔던 것이다. 실제 자격증명은 짧지 않다.

그래서 픽스처는 **실제 크기** 로 만든다. 그리고 검사는 양방향이다:

- 비밀은 사라져야 한다.
- 실패 신호와 평범한 소스 줄은 남아야 한다.

리댁션은 두 방향으로 다 틀릴 수 있고, 한쪽만 검사하면 반대쪽으로 넘어간
것을 통과시킨다. 실제로 그렇게 넘어갔다.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import sys
import time
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER = REPO_ROOT / "tools" / "speculative_run.py"


def load_runner() -> types.ModuleType:
    """tools/speculative_run.py 를 모듈로 읽어 온다.

    배포본에 들어가지 않는 파일이라 패키지 경로로 import 할 수 없다.
    """
    spec = importlib.util.spec_from_file_location("speculative_run", RUNNER)
    if spec is None or spec.loader is None:  # pragma: no cover - 경로가 맞으면 안 온다
        pytest.skip(f"cannot load {RUNNER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["speculative_run"] = module
    spec.loader.exec_module(module)
    return module


pytestmark = pytest.mark.skipif(not RUNNER.exists(), reason="tools/ is not present")

BEGIN = "-----BEGIN "
END = "-----END "
KEY = "PRIVATE KEY-----"


def pem_body(size: int = 1200) -> list[str]:
    """실제 키 본문처럼 64자로 접힌 base64 줄들."""
    blob = base64.b64encode(b"\x01" * size).decode()
    return [blob[index : index + 64] for index in range(0, len(blob), 64)]


def plain_pem() -> tuple[str, list[str]]:
    lines = pem_body()
    return f"{BEGIN}RSA {KEY}\n" + "\n".join(lines) + f"\n{END}RSA {KEY}", lines


def encrypted_pem() -> tuple[str, list[str]]:
    lines = pem_body()
    header = "Proc-Type: 4,ENCRYPTED\nDEK-Info: AES-128-CBC,0123456789ABCDEF0123456789ABCDEF\n\n"
    return f"{BEGIN}RSA {KEY}\n{header}" + "\n".join(lines) + f"\n{END}RSA {KEY}", lines


def gcp_service_account() -> tuple[str, list[str]]:
    """JSON 에 직렬화된 키. 줄바꿈이 `\\n` 두 글자다.

    라운드 6 에서 통째로 유출된 형태이고, 짧은 픽스처로는 재현되지 않는다.
    """
    lines = pem_body()
    escaped = "\\n".join([f"{BEGIN}{KEY}", *lines, f"{END}{KEY}"])
    return (
        '{"type": "service_account", "project_id": "p", '
        f'"private_key": "{escaped}\\n", '
        '"client_email": "svc@p.iam.gserviceaccount.com"}'
    ), lines


SECRET_CASES = [
    ("평문 PEM", plain_pem),
    ("암호화 PEM", encrypted_pem),
    ("JSON 직렬화 GCP 키", gcp_service_account),
]


@pytest.mark.parametrize(("name", "factory"), SECRET_CASES, ids=[c[0] for c in SECRET_CASES])
def test_private_keys_are_redacted(name: str, factory: object) -> None:
    module = load_runner()
    text, lines = factory()  # type: ignore[operator]
    cleaned = module.verify_excerpt(text)
    leaked = [line for line in lines if line in cleaned]
    assert not leaked, f"{name}: 본문 {len(leaked)}/{len(lines)} 줄이 남았다"


def test_encrypted_key_without_end_marker_is_redacted() -> None:
    """END 가 없어도 본문은 지워야 한다.

    문자 종류로 훑던 구현은 `Proc-Type` 의 하이픈에서 멈춰, 마커만 지우고
    키를 남겼다 — 가장 나쁜 결과다.
    """
    module = load_runner()
    lines = pem_body(600)
    text = (
        f"{BEGIN}RSA {KEY}\nProc-Type: 4,ENCRYPTED\n\n"
        + "\n".join(lines)
        + "\nFAILED tests/test_x.py::test_y"
    )
    cleaned = module.verify_excerpt(text)
    assert not [line for line in lines if line in cleaned]
    assert "FAILED tests/test_x.py::test_y" in cleaned


def test_named_credentials_are_redacted() -> None:
    module = load_runner()
    cases = {
        "AWS 환경 변수": (
            "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "wJalrXUt",
        ),
        "AWS JSON": ('{"SecretAccessKey": "wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY"}', "wJalrXUt"),
        "세션 토큰": ('{"SessionToken": "FQoGZXIvYXdzEBYaDF1234567890abcdefghij"}', "FQoGZXIv"),
        "값에 낯선 문자": ("MY_SECRET_TOKEN='wJalr!XUtnFEMIK7MDENG'", "wJalr!XUtn"),
        "GCP API 키": ("key AIzaSyD-1234567890abcdefghijklmnopqrstu here", "AIzaSyD-1234567890"),
        "JWT": ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.dQw4w9WgXcQabcdefghij", "dQw4w9WgXcQ"),
        "환경 덤프": ("DATABASE_PASSWORD=hunter2hunter2hunter2", "hunter2hunter2"),
    }
    module = load_runner()
    for name, (text, probe) in cases.items():
        assert probe not in module.verify_excerpt(text), f"{name}: 남았다"


def test_ordinary_source_lines_survive() -> None:
    """과잉 삭제도 결함이다.

    검증 출력에는 실패한 **소스 줄** 이 함께 나온다. 그것을 지우면 조언자가
    진단할 근거를 잃고, 이 기능은 존재 이유가 없어진다.
    """
    module = load_runner()
    lines = [
        "API_KEY_HEADER = 'X-Api-Key'",
        "TOKEN_RE = re.compile(r'x')",
        "self.secret = None",
        "assert response.token == expected",
        "PASSWORD_FIELD = form['pw']",
        "SECRET_PATH = Path('conf')",
    ]
    for line in lines:
        assert "[REDACTED]" not in module.verify_excerpt(line), f"과잉 삭제: {line}"


def test_failure_signal_survives() -> None:
    module = load_runner()
    report = (
        "FAILED tests/test_ledger.py::test_pad - AssertionError\n"
        'E       assert normalize(" A-1 ") == "A-1"\n'
        '  File "src/ledger.py", line 42, in normalize\n'
        "    return raw\n"
    )
    cleaned = module.verify_excerpt(report)
    for marker in ("AssertionError", "normalize", "line 42", "test_pad"):
        assert marker in cleaned


def test_certificates_are_not_redacted() -> None:
    """인증서는 비밀이 아니다. 지우면 진단만 어려워진다."""
    module = load_runner()
    lines = pem_body(400)
    text = f"{BEGIN}CERTIFICATE-----\n" + "\n".join(lines) + f"\n{END}CERTIFICATE-----"
    cleaned = module.verify_excerpt(text)
    assert lines[0] in cleaned


def test_marker_mention_does_not_swallow_the_report() -> None:
    """마커를 이름만 언급한 오류 줄이 뒤의 출력을 삼키면 안 된다."""
    module = load_runner()
    lines = pem_body(400)
    text = (
        f"expected {BEGIN}{KEY} but got EOF\n"
        + "FAILED important\n" * 40
        + f"{BEGIN}X {KEY}\n"
        + "\n".join(lines)
        + f"\n{END}X {KEY}"
    )
    cleaned = module.verify_excerpt(text)
    assert cleaned.count("FAILED important") > 30
    assert not [line for line in lines if line in cleaned]


def test_unrelated_end_banner_does_not_extend_the_span() -> None:
    """`-----END OF REPORT-----` 같은 배너를 키의 끝으로 보면 안 된다."""
    module = load_runner()
    lines = pem_body(400)
    text = (
        f"{BEGIN}{KEY}\n"
        + "\n".join(lines)
        + f"\n{END}OF REPORT-----\n"
        + "FAILED important\n" * 30
    )
    cleaned = module.verify_excerpt(text)
    assert cleaned.count("FAILED important") > 25
    assert not [line for line in lines if line in cleaned]


def test_credential_far_into_a_long_output_is_redacted() -> None:
    """앞이 아무리 길어도 자격증명은 지워야 한다.

    예전에는 정규식 비용을 줄이려고 뒤쪽 창만 훑었고, 그 자르는 행위가
    앵커를 부수는 유출을 여러 라운드에 걸쳐 만들었다. 이제 자르지 않는다.
    """
    module = load_runner()
    lines = pem_body(1400)
    padding = "y" * 120_000
    text = padding + f"{BEGIN}RSA {KEY}\n" + "\n".join(lines) + f"\n{END}RSA {KEY}\nFAILED tail"
    cleaned = module.verify_excerpt(text)
    assert not [line for line in lines if line in cleaned]
    assert "FAILED tail" in cleaned


def test_known_host_secret_values_are_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    """이름 없이 값만 찍힌 자격증명은 모양으로 못 잡는다.

    아는 값은 정확히 일치로 지운다. 자식이 우리 환경을 읽어 찍었다면 그
    값은 거기 있다.
    """
    module = load_runner()
    monkeypatch.setenv("SOME_SERVICE_API_KEY", "zzTOPSECRETVALUEzz1234567890")
    cleaned = module.verify_excerpt("test failed: got zzTOPSECRETVALUEzz1234567890 instead")
    assert "zzTOPSECRET" not in cleaned


def test_advice_is_redacted_without_being_truncated() -> None:
    """조언은 지우되 자르지 않는다.

    verify_excerpt 를 재사용하면 **뒤** 4000자만 남아 조언의 앞머리 —
    결론과 계획이 오는 자리 — 가 버려진다.
    """
    module = load_runner()
    advice = "PLAN: " + "do the thing. " * 1000
    assert len(module.redact_text(advice)) == len(advice)


def test_redaction_is_fast_on_hostile_input() -> None:
    """이 텍스트는 자식이 길이와 모양을 정한다. 시간이 폭발하면 안 된다."""
    module = load_runner()
    hostile = {
        "비매치 대량": "x" * 200_000,
        # 줄바꿈 없는 마커 반복. 게이트가 마커마다 끝까지 훑으면 이차 시간이
        # 되고, 그 길이는 자식이 정한다.
        "마커 반복": (BEGIN + KEY) * 20_000,
        "언급 반복": (f"{BEGIN}{KEY} nope\n") * 5_000,
        "이스케이프 줄바꿈 대량": ("\\n" + "A" * 64) * 3_000,
    }
    for name, text in hostile.items():
        started = time.monotonic()
        module.verify_excerpt(text)
        elapsed = time.monotonic() - started
        # 여유 있게 잡는다. 이 검사가 잡으려는 것은 파국적 백트래킹이고
        # 그것은 초가 아니라 분 단위로 나타난다. 빠듯하게 잡으면 부하가 걸린
        # 기계에서 실패해, 진짜 결함이 아닌 것으로 신뢰를 깎는다.
        assert elapsed < 15.0, f"{name}: {elapsed:.2f}s"


def test_excerpt_is_bounded() -> None:
    module = load_runner()
    cleaned = module.verify_excerpt("line of output\n" * 20_000)
    assert len(cleaned) <= module.VERIFY_EXCERPT_CHARS + 64


def test_structured_envelopes_yield_the_advice_body() -> None:
    """봉투를 그대로 붙이면 executor 가 조언 대신 계측 데이터를 읽는다."""
    module = load_runner()
    claude = '{"type":"result","total_cost_usd":0.5,"result":"Return a copy of the list."}'
    codex = '{"type":"item.completed","item":{"type":"agent_message","text":"Use a channel."}}'
    assert module.advice_text(claude, ["claude", "--output-format", "json"]) == (
        "Return a copy of the list."
    )
    assert module.advice_text(codex, ["codex", "exec", "--json"]) == "Use a channel."
    # 구조화 출력을 요청하지 않았으면 stdout 은 산문이므로 그대로 쓴다.
    assert module.advice_text("just advice", ["claude", "--print"]) == "just advice"


def test_structured_output_detection_ignores_flag_values() -> None:
    """다른 플래그의 **값** 안에 있는 글자로 구조화 출력을 켜면 안 된다.

    그러면 자식의 산문에서 비용을 읽게 되고, 싼 경로가 제 값을 스스로 정한다.
    """
    module = load_runner()
    assert not module.wants_structured_output(
        ["claude", "--print", "--append-system-prompt", "never emit --output-format json"]
    )
    assert module.wants_structured_output(["claude", "--output-format", "json"])
    assert module.wants_structured_output(["claude", "--output-format=json"])
    assert module.wants_structured_output(["codex", "exec", "--json"])
    assert not module.wants_structured_output(["claude", "--output-format"])


def test_host_secret_values_are_never_short() -> None:
    """짧은 값을 정확 일치로 지우면 평범한 문자열과 부딪혀 과잉이 된다."""
    module = load_runner()
    saved = dict(os.environ)
    try:
        os.environ["TINY_TOKEN"] = "abc"
        assert "abc" not in module.host_secret_values()
    finally:
        os.environ.clear()
        os.environ.update(saved)


@pytest.mark.parametrize("separator", ["\\n", "\\r\\n", "\\\\n"], ids=["\\n", "\\r\\n", "이중"])
def test_serialised_key_separators(separator: str) -> None:
    """직렬화 구분자를 하나씩 열거하면 다음 형태에서 또 뚫린다.

    라운드 6 은 `\\n` 을, 라운드 7 은 `\\r\\n` 을 놓쳤다. 둘 다 키가 통째로
    나갔다.
    """
    module = load_runner()
    lines = pem_body()
    blob = '{"private_key": "' + separator.join([f"{BEGIN}{KEY}", *lines, f"{END}{KEY}"]) + '"}'
    cleaned = module.verify_excerpt(blob)
    assert not [line for line in lines if line in cleaned]


def test_proxy_userinfo_is_redacted_but_the_address_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """프록시 URL 의 userinfo 는 자격증명이다.

    러너는 그 사실을 알면서 프록시 변수를 자식에게 넘긴다. 자식이 그것을
    찍으면 이름 기반 필터는 "PROXY" 를 비밀로 보지 않아 그대로 나간다.
    """
    module = load_runner()
    monkeypatch.setenv("HTTPS_PROXY", "http://alice:s3cr3tpassw0rd@proxy.corp:8080")
    cleaned = module.verify_excerpt("curl failed via http://alice:s3cr3tpassw0rd@proxy.corp:8080")
    assert "s3cr3tpassw0rd" not in cleaned
    assert "proxy.corp:8080" in cleaned


def test_key_body_and_end_marker_on_one_line() -> None:
    """줄 단위로 범위를 못 정해도 지워야 한다.

    범위를 못 정하면 아무것도 안 지우고 다음 반복으로 넘어가, 키가 통째로
    출력됐다 — fail-open 이다.
    """
    module = load_runner()
    blob = base64.b64encode(b"\x01" * 1200).decode()
    text = f"{BEGIN}{KEY} {blob} {END}{KEY}"
    assert blob[:40] not in module.verify_excerpt(text)


def test_large_key_is_redacted_entirely() -> None:
    """본문 주사에 인위적 상한을 두면 큰 키가 중간에서 잘려 나머지가 나간다."""
    module = load_runner()
    blob = base64.b64encode(b"\x02" * 10_500).decode()
    lines = [blob[index : index + 64] for index in range(0, len(blob), 64)]
    text = f"{BEGIN}RSA {KEY}\n" + "\n".join(lines) + f"\n{END}RSA {KEY}"
    cleaned = module.verify_excerpt(text)
    assert not [line for line in lines if line in cleaned]


def test_pgp_armored_private_key_is_redacted() -> None:
    """마커가 KEY 로 끝나지 않는 형식도 있다."""
    module = load_runner()
    lines = pem_body(600)
    marker = "PGP PRIVATE KEY BLOCK-----"
    text = f"{BEGIN}{marker}\n" + "\n".join(lines) + f"\n{END}{marker}"
    cleaned = module.verify_excerpt(text)
    assert not [line for line in lines if line in cleaned]


def test_schemeless_proxy_with_short_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """스킴 없는 형태도 curl, wget, pip 가 받아들인다. 짧은 비밀번호도 비밀이다."""
    module = load_runner()
    monkeypatch.setenv("HTTP_PROXY", "user:aB3x5@proxy.corp:3128")
    cleaned = module.verify_excerpt("proxy error at user:aB3x5@proxy.corp:3128")
    assert "aB3x5" not in cleaned
    assert "proxy.corp:3128" in cleaned


@pytest.mark.parametrize(
    "prefix",
    ["[INFO] ", "stdout | ", "2026-08-17T10:00:00Z ", "  "],
    ids=["bracket", "pipe", "timestamp", "indent"],
)
def test_log_prefixed_key_lines_are_redacted(prefix: str) -> None:
    """CI 출력은 줄마다 접두사가 붙는다.

    접두사를 그대로 판정하면 공백 때문에 본문이 아니라고 보고 멈춰, 키가
    통째로 남는다.
    """
    module = load_runner()
    lines = pem_body()
    text = (
        f"{prefix}{BEGIN}RSA {KEY}\n"
        + "\n".join(prefix + line for line in lines)
        + f"\n{prefix}{END}RSA {KEY}"
    )
    cleaned = module.verify_excerpt(text)
    assert not [line for line in lines if line in cleaned]


def test_fallback_does_not_cut_the_next_line_token() -> None:
    """폴백이 줄을 넘어가면 다음 줄 토큰을 가운데서 잘라 앵커를 없앤다.

    앵커가 없어지면 그 토큰은 모양 패턴에도 안 걸려 그대로 나간다.
    """
    module = load_runner()
    text = (
        f"E   ValueError: {BEGIN}{KEY} MIIEvQIBADANBgkqhkiG not found\n"
        "E   using token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
    )
    cleaned = module.verify_excerpt(text)
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345" not in cleaned
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345" not in cleaned


def test_fallback_never_jumps_a_fixed_distance() -> None:
    """임의 크기 점프는 자식이 거리를 맞추면 앵커만 잘라 낸다.

    `ghp` 세 글자가 사라지고 `_ABC...` 가 남으면 그 토큰은 모양 패턴에도
    안 걸린다 — 지우려는 동작이 유출을 만드는 형태다.
    """
    module = load_runner()
    token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
    for extra in range(0, 40):
        padding = module.PEM_MAX_SPAN - 1 - 3 - extra
        if padding < 0:
            continue
        text = f"{BEGIN}{KEY}\n" + "x" * padding + token + " tail"
        cleaned = module.verify_excerpt(text)
        assert token not in cleaned
        assert "_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345" not in cleaned


def test_fallback_never_cuts_a_token_on_the_same_line() -> None:
    """문자 종류로 훑으면 그 집합 밖의 글자에서 멈춘다.

    그 자리가 토큰 한가운데면 앵커가 잘려 본체만 남는다. 라운드 10 에서
    줄바꿈을 뺐더니 이번에는 밑줄에서 잘렸다 — 집합을 고르는 방식 자체가
    문제다.
    """
    module = load_runner()
    token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
    cleaned = module.verify_excerpt(f"error: {BEGIN}{KEY} got {token} here")
    assert token not in cleaned
    assert "_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345" not in cleaned


def test_proxy_password_containing_at_sign(monkeypatch: pytest.MonkeyPatch) -> None:
    """비밀번호에 @ 가 들어가는 것은 특수문자 정책에서 흔하다."""
    module = load_runner()
    monkeypatch.setenv("HTTPS_PROXY", "http://user:Str0ng@Pass!word@proxy:8080")
    cleaned = module.verify_excerpt("proxy http://user:Str0ng@Pass!word@proxy:8080 failed")
    assert "Pass!word" not in cleaned
    assert "Str0ng" not in cleaned


def test_private_key_inside_a_diff_is_redacted() -> None:
    """실패한 시도의 diff 도 조언자에게 간다. 거기 키가 있을 수 있다."""
    module = load_runner()
    lines = pem_body()
    text = (
        "diff --git a/k.pem b/k.pem\n+++ b/k.pem\n"
        f"+{BEGIN}RSA {KEY}\n" + "\n".join("+" + line for line in lines) + f"\n+{END}RSA {KEY}"
    )
    cleaned = module.verify_excerpt(text)
    assert not [line for line in lines if line in cleaned]


@pytest.mark.parametrize(
    "prefix",
    ["+", "[INFO] ", "stdout | ", "- ", "2026-08-17T10:00:00Z ", "  ", ""],
    ids=["diff", "bracket", "pipe", "dash", "timestamp", "indent", "none"],
)
@pytest.mark.parametrize("encrypted", [False, True], ids=["plain", "encrypted"])
def test_prefixed_keys_of_both_kinds(prefix: str, encrypted: bool) -> None:
    """줄머리 접두사와 키 종류의 조합.

    접두사만 있는 줄이 "짧은 마지막 줄" 로 잡혀 본문 앞에서 멈추면 키가
    통째로 남는다. "[INFO]" 처럼 문자로 시작하는 접두사는 문자 단위로
    벗겨지지 않아 그 함정에 정확히 걸렸다.
    """
    module = load_runner()
    lines = pem_body()
    header = ""
    if encrypted:
        header = (
            f"{prefix}Proc-Type: 4,ENCRYPTED\n"
            f"{prefix}DEK-Info: AES-128-CBC,0123456789ABCDEF\n"
            f"{prefix}\n"
        )
    text = (
        f"{prefix}{BEGIN}RSA {KEY}\n"
        + header
        + "\n".join(prefix + line for line in lines)
        + f"\n{prefix}{END}RSA {KEY}"
    )
    cleaned = module.verify_excerpt(text)
    assert not [line for line in lines if line in cleaned]


@pytest.mark.parametrize(
    "url",
    [
        "http://alice:s3cr3tvalue@proxy:8080",
        "bob:P@ssw0rd!x@corp:3128",
        "https://carol:sh0rt@gw:443",
    ],
    ids=["schemed", "schemeless-at", "schemed-short"],
)
def test_proxy_password_forms(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """정규식 하나로 URL 을 가르려던 시도가 스킴 있는 형태를 깨뜨렸다.

    `(?:://|^)` 는 위치 0 의 `^` 대안이 먼저 걸려 스킴을 사용자 이름으로
    잡는다. 그러면 진짜 비밀번호가 목록에 없다.
    """
    module = load_runner()
    monkeypatch.setenv("HTTPS_PROXY", url)
    parsed = module.split_userinfo(url)
    assert parsed is not None
    user, password = parsed
    # URL 안에 있으면 길이와 무관하게 지운다.
    assert password not in module.verify_excerpt(f"via {url} failed")
    if len(password) >= 6:
        # 값만 단독으로 찍혀도 지운다. 짧은 값은 흔한 단어와 부딪혀
        # 보고서를 통째로 지울 위험이 더 크므로 문맥이 있을 때만 지운다.
        assert password not in module.verify_excerpt(f"auth failed with {password}")
    assert user in module.verify_excerpt(f"user {user} rejected") or len(user) >= 12


@pytest.mark.parametrize(
    "prefix",
    ["+[INFO] ", "[INFO] +", "2026-08-18T00:00:00Z [INFO] "],
    ids=["diff-tag", "tag-diff", "ts-tag"],
)
def test_stacked_line_prefixes(prefix: str) -> None:
    """접두사는 겹쳐서 붙는다. 한 겹만 벗기면 남은 겹이 헤더 인식을 막는다."""
    module = load_runner()
    lines = pem_body()
    text = (
        f"{prefix}{BEGIN}RSA {KEY}\n"
        f"{prefix}Proc-Type: 4,ENCRYPTED\n{prefix}\n"
        + "\n".join(prefix + line for line in lines)
        + f"\n{prefix}{END}RSA {KEY}"
    )
    cleaned = module.verify_excerpt(text)
    assert not [line for line in lines if line in cleaned]


def test_short_wrapped_key_lines() -> None:
    """16자로 접힌 본문에서 첫 줄만 지우고 멈추면 나머지가 통째로 남는다."""
    module = load_runner()
    blob = base64.b64encode(b"\x01" * 1200).decode()
    short = [blob[index : index + 16] for index in range(0, len(blob), 16)]
    text = f"{BEGIN}RSA {KEY}\n" + "\n".join(short) + f"\n{END}RSA {KEY}"
    cleaned = module.verify_excerpt(text)
    assert not [line for line in short if line in cleaned]


def test_proxy_url_with_path_and_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """authority 를 안 자르면 쿼리의 @ 가 경계로 잡혀 비밀번호가 어긋난다."""
    module = load_runner()
    url = "http://alice:SuperSecretValue123@proxy.corp:8080/status?notify=ops@example.com"
    monkeypatch.setenv("HTTPS_PROXY", url)
    assert "SuperSecretValue123" not in module.verify_excerpt("auth SuperSecretValue123 failed")


def test_proxy_url_with_token_and_no_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """https://TOKEN@host 형태에서 토큰 자체가 비밀이다."""
    module = load_runner()
    monkeypatch.setenv("HTTP_PROXY", "http://BareTokenAAAAAAAAAAAA@proxy:3128")
    assert "BareTokenAAAAAAAAAAAA" not in module.verify_excerpt("using BareTokenAAAAAAAAAAAA")


def test_key_far_beyond_any_scan_window() -> None:
    """창을 잘라 앵커를 잃던 부류. 이제 자르지 않고 전부 지운 뒤 발췌한다."""
    module = load_runner()
    lines = pem_body()
    text = (
        "y" * 100_000 + f"{BEGIN}RSA {KEY}\n" + "\n".join(lines) + f"\n{END}RSA {KEY}\nFAILED tail"
    )
    cleaned = module.verify_excerpt(text)
    assert not [line for line in lines if line in cleaned]
    assert "FAILED tail" in cleaned


@pytest.mark.parametrize("width", [12, 15, 16, 24, 64], ids=lambda w: f"{w}col")
def test_key_body_folded_to_any_width(width: int) -> None:
    """본 주사와 이어짐 판정의 하한이 어긋나면 짧게 접힌 키가 첫 줄에서 끊긴다."""
    module = load_runner()
    blob = base64.b64encode(b"\x01" * 1200).decode()
    lines = [blob[index : index + width] for index in range(0, len(blob), width)]
    text = f"{BEGIN}RSA {KEY}\n" + "\n".join(lines) + f"\n{END}RSA {KEY}"
    cleaned = module.verify_excerpt(text)
    assert not [line for line in lines if line in cleaned]


@pytest.mark.parametrize("layers", [1, 2, 4, 8], ids=lambda n: f"{n}layer")
def test_prefix_stripping_reaches_a_fixed_point(layers: int) -> None:
    """상한을 두면 겹이 많을 때 남는다."""
    module = load_runner()
    prefix = "+[INFO] " * layers
    lines = pem_body()
    text = (
        f"{prefix}{BEGIN}RSA {KEY}\n{prefix}Proc-Type: 4,ENCRYPTED\n{prefix}\n"
        + "\n".join(prefix + line for line in lines)
        + f"\n{prefix}{END}RSA {KEY}"
    )
    cleaned = module.verify_excerpt(text)
    assert not [line for line in lines if line in cleaned]


@pytest.mark.parametrize("width", [8, 12, 15, 64], ids=lambda w: f"{w}col")
@pytest.mark.parametrize(
    "separator", ["\n", "\\n", "\\r\\n"], ids=["real", "escaped", "escaped-crlf"]
)
def test_folded_key_with_every_separator(width: int, separator: str) -> None:
    """접힘 너비와 직렬화 구분자의 조합.

    본 주사는 이스케이프된 구분자를 보는데 이어짐 탐침은 물리적 줄바꿈만
    봤다. 두 경로가 같은 판정을 다르게 하면 그 틈이 유출이다.
    """
    module = load_runner()
    blob = base64.b64encode(b"\x01" * 1200).decode()
    lines = [blob[index : index + width] for index in range(0, len(blob), width)]
    joined = separator.join([f"{BEGIN}RSA {KEY}", *lines, f"{END}RSA {KEY}"])
    text = joined if separator == "\n" else '{"private_key": "' + joined + '"}'
    cleaned = module.verify_excerpt(text)
    assert not [line for line in lines if line in cleaned]


@pytest.mark.parametrize("width", [8, 12, 64], ids=lambda w: f"{w}col")
@pytest.mark.parametrize("prefix", ["+[INFO] ", "[INFO] ", "+"], ids=["stacked", "tag", "diff"])
def test_folded_key_with_prefix(width: int, prefix: str) -> None:
    """게이트가 12자 연속을 요구하면 8자로 접힌 본문을 못 알아본다."""
    module = load_runner()
    blob = base64.b64encode(b"\x01" * 1200).decode()
    lines = [blob[index : index + width] for index in range(0, len(blob), width)]
    text = (
        f"{prefix}{BEGIN}RSA {KEY}\n"
        + "\n".join(prefix + line for line in lines)
        + f"\n{prefix}{END}RSA {KEY}"
    )
    cleaned = module.verify_excerpt(text)
    assert not [line for line in lines if line in cleaned]


@pytest.mark.parametrize(
    ("envelope", "expected"),
    [
        ('{"result":"Return a copy."}', "Return a copy."),
        ('[{"item":{"text":"Use a channel."}}]', "Use a channel."),
        ('{"type":"x"}\n{"item":{"text":"Third form."}}', "Third form."),
    ],
    ids=["object", "array", "jsonl"],
)
def test_advice_body_is_extracted_from_every_envelope(envelope: str, expected: str) -> None:
    """봉투를 그대로 붙이면 executor 가 조언 대신 계측 데이터를 읽는다."""
    module = load_runner()
    assert module.advice_text(envelope, ["claude", "--output-format", "json"]) == expected


@pytest.mark.parametrize("layers", [1, 8, 51, 120], ids=lambda n: f"{n}layer")
def test_long_stacked_prefix_does_not_push_body_out_of_the_gate(layers: int) -> None:
    """고정 문자 창을 쓰면 접두사가 길 때 본문 첫 글자가 창 밖으로 밀린다."""
    module = load_runner()
    prefix = "+[INFO] " * layers
    lines = pem_body()
    text = (
        f"{prefix}{BEGIN}RSA {KEY}\n"
        + "\n".join(prefix + line for line in lines)
        + f"\n{prefix}{END}RSA {KEY}"
    )
    cleaned = module.verify_excerpt(text)
    assert not [line for line in lines if line in cleaned]


@pytest.mark.parametrize(
    "url",
    ["http://:opaquepassword123@proxy:3128", "http://opaquetoken123:@proxy:3128"],
    ids=["empty-user", "empty-password"],
)
def test_proxy_userinfo_with_one_empty_half(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """http://:pass@host 와 http://token:@host 는 둘 다 유효하고 쓰인다."""
    module = load_runner()
    monkeypatch.setenv("HTTP_PROXY", url)
    parsed = module.split_userinfo(url)
    assert parsed is not None
    secret = parsed[0] or parsed[1]
    assert secret not in module.verify_excerpt(f"auth {secret} failed")


def test_host_secret_survives_json_serialisation(monkeypatch: pytest.MonkeyPatch) -> None:
    """값이 JSON 으로 직렬화되면 따옴표와 역슬래시가 바뀌어 원문과 다른 바이트가 된다."""
    module = load_runner()
    monkeypatch.setenv("MY_SVC_TOKEN", 'zzTOP"SECRETvalue123')
    cleaned = module.verify_excerpt('{"seen": "zzTOP\\"SECRETvalue123"}')
    assert 'zzTOP\\"SECRETvalue123' not in cleaned
    assert 'zzTOP"SECRETvalue123' not in cleaned


def test_crlf_key_is_redacted() -> None:
    module = load_runner()
    lines = pem_body()
    text = f"{BEGIN}RSA {KEY}\r\n" + "\r\n".join(lines) + f"\r\n{END}RSA {KEY}"
    cleaned = module.verify_excerpt(text)
    assert not [line for line in lines if line in cleaned]


def test_content_array_envelope_is_joined() -> None:
    """content 배열은 조각을 이어야 본문이 된다.

    구분자를 넣지 않는다. 조각 경계에 걸친 자격증명의 앵커가 갈라지면
    리댁션이 그것을 못 잡는다 — 읽기 좋음보다 그쪽이 무겁다.
    """
    module = load_runner()
    envelope = '{"content":[{"text":"first "},{"text":"second"}]}'
    assert module.advice_text(envelope, ["claude", "--output-format", "json"]) == "first second"


@pytest.mark.parametrize("layers", [51, 200, 400, 1000], ids=lambda n: f"{n}layer")
def test_gate_fails_closed_when_its_budget_runs_out(layers: int) -> None:
    """성능을 위한 상한이 유출을 만들면 안 된다.

    라운드 15 가 넣은 3072자 상한이 400겹 접두사에서 본문을 창 밖으로
    밀어내 키가 통째로 나갔다. 예산 안에서 결론을 못 내면 키로 간주한다.
    """
    module = load_runner()
    prefix = "+[INFO] " * layers
    lines = pem_body()
    text = (
        f"{prefix}{BEGIN}RSA {KEY}\n"
        + "\n".join(prefix + line for line in lines)
        + f"\n{prefix}{END}RSA {KEY}"
    )
    cleaned = module.verify_excerpt(text)
    assert not [line for line in lines if line in cleaned]


def test_advice_starting_with_a_bracket_is_not_mistaken_for_an_envelope() -> None:
    """추출 성공 여부를 첫 글자로 되추정하면 정당한 조언이 버려진다."""
    module = load_runner()
    body, extracted = module.advice_text_extracted(
        '{"result":"[1] Inspect the parser"}', ["claude", "--output-format", "json"]
    )
    assert extracted
    assert body == "[1] Inspect the parser"


def test_unknown_envelope_shape_is_reported_as_not_extracted() -> None:
    module = load_runner()
    _, extracted = module.advice_text_extracted(
        '{"unknown_shape": 42}', ["claude", "--output-format", "json"]
    )
    assert not extracted


def test_proxy_token_with_empty_password_half(monkeypatch: pytest.MonkeyPatch) -> None:
    """http://TOKEN:@host 형태의 문맥 문자열은 TOKEN@ 가 아니라 TOKEN:@ 다."""
    module = load_runner()
    monkeypatch.setenv("HTTP_PROXY", "http://abctoken123:@proxy:3128")
    assert "abctoken123" not in module.verify_excerpt("proxy http://abctoken123:@proxy:3128")


@pytest.mark.parametrize("blanks", [0, 6, 20, 100], ids=lambda n: f"{n}blank")
def test_blank_lines_do_not_exhaust_the_gate_budget(blanks: int) -> None:
    """빈 줄이 예산을 먹고 끝나면 게이트가 False 로 떨어져 키가 남는다.

    라운드 16 이 문자 상한을 fail-closed 로 뒤집었는데, 줄 수 예산에는
    같은 구멍이 남아 있었다.
    """
    module = load_runner()
    lines = pem_body()
    text = f"{BEGIN}RSA {KEY}\n" + "\n" * blanks + "\n".join(lines) + f"\n{END}RSA {KEY}"
    cleaned = module.verify_excerpt(text)
    assert not [line for line in lines if line in cleaned]


def test_quoted_body_lines_are_redacted() -> None:
    """탐침이 본 주사와 다른 정규화를 하면 첫 줄만 지우고 멈춘다."""
    module = load_runner()
    blob = base64.b64encode(b"\x01" * 1200).decode()
    lines = [blob[index : index + 16] for index in range(0, len(blob), 16)]
    text = f"{BEGIN}RSA {KEY}\n" + "\n".join(f'"{line}"' for line in lines) + f"\n{END}RSA {KEY}"
    cleaned = module.verify_excerpt(text)
    assert not [line for line in lines if line in cleaned]


def test_content_fragments_join_without_breaking_an_anchor() -> None:
    """구분자를 넣으면 조각 경계에 걸친 마커가 갈라져 안 잡힌다."""
    module = load_runner()
    envelope = (
        '{"content":[{"text":"' + BEGIN + 'PRI"},'
        '{"text":"VATE KEY-----\\nMIIEsecretmaterial\\n' + END + KEY + '"}]}'
    )
    body, extracted = module.advice_text_extracted(envelope, ["claude", "--output-format", "json"])
    assert extracted
    assert "MIIEsecretmaterial" not in module.redact_text(body)


def test_message_stream_array_takes_the_last_element() -> None:
    module = load_runner()
    stream = '[{"type":"a","item":{"text":"first"}},{"type":"b","item":{"text":"last"}}]'
    body, _ = module.advice_text_extracted(stream, ["claude", "--output-format", "json"])
    assert body == "last"


# --- 라운드 18: 이음매, 경계 해석, 봉투 모양 ---------------------------------


def test_separately_redacted_parts_leak_at_the_seam():
    """따로 지운 두 텍스트를 이어 붙이면 앵커가 갈라진다.

    검증 출력이 마커 앞부분으로 끝나고 diff 가 나머지로 시작하면, 각각은
    무해해 보이지만 이어 붙이면 온전한 키다. untrusted_block 은 **먼저
    이어 붙인 뒤** 지우므로 이 형태를 잡는다.
    """
    module = load_runner()
    lines = pem_body()
    head = "verification output ends here " + BEGIN + "PRI"
    tail = "VATE KEY-----\n" + "\n".join(lines) + "\n" + END + KEY + "\n"
    cleaned = module.untrusted_block(head, tail)
    for line in lines:
        assert line not in cleaned
    # 반대 방향도 확인한다: 실패 신호는 남아야 한다.
    assert "verification output ends here" in cleaned


def test_stdout_and_stderr_are_joined_without_a_separator():
    """두 스트림 사이에 넣는 줄바꿈이 마커를 가르면 안 된다."""
    module = load_runner()
    lines = pem_body()
    joined = ("stdout part " + BEGIN + "PRI") + (
        "VATE KEY-----\n" + "\n".join(lines) + "\n" + END + KEY
    )
    cleaned = module.verify_excerpt(joined)
    for line in lines:
        assert line not in cleaned


@pytest.mark.parametrize(
    "url,secret",
    [
        ("http://user:pa/ssword@proxy:3128", "pa/ssword"),
        ("http://user:pa?ssword@proxy:3128", "pa?ssword"),
        ("http://user:pa#ssword@proxy:3128", "pa#ssword"),
        ("http://user:SuperSecretValue123@proxy/x?notify=ops@example.com", "SuperSecretValue123"),
        ("http://user:SuperSecretValue123@proxy/a@b/c", "SuperSecretValue123"),
    ],
)
def test_both_boundary_readings_are_registered(monkeypatch, url, secret):
    """authority 를 먼저 자르는 해석과 @ 를 먼저 찾는 해석이 서로를 깬다.

    어느 하나만 쓰면 다른 형태의 자격증명이 목록에서 빠진다. 둘 다 후보로
    낸다 — 리댁션은 정확 일치이므로 후보가 늘어도 손해가 없다.
    """
    module = load_runner()
    monkeypatch.setenv("HTTPS_PROXY", url)
    assert secret not in module.verify_excerpt(f"auth failed for {secret}")


def test_percent_encoded_proxy_password_is_redacted_when_decoded(monkeypatch):
    """프록시 URL 은 인코딩된 값을 담지만 클라이언트는 복호된 값을 찍는다."""
    module = load_runner()
    monkeypatch.setenv("HTTPS_PROXY", "http://user:Ultra%40Secret%21Value@proxy")
    cleaned = module.verify_excerpt("proxy rejected Ultra@Secret!Value")
    assert "Ultra@Secret!Value" not in cleaned
    assert "proxy rejected" in cleaned


def test_doubly_escaped_host_secret_is_redacted(monkeypatch):
    """이미 직렬화된 페이로드를 한 번 더 감싸면 이스케이프가 두 겹이 된다."""
    module = load_runner()
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", 'quote"and\\backslash_secret_value')
    once = json.dumps('quote"and\\backslash_secret_value')[1:-1]
    twice = json.dumps(once)[1:-1]
    assert once not in module.verify_excerpt(f"payload {once} end")
    assert twice not in module.verify_excerpt(f"payload {twice} end")


def test_serialisation_noise_after_the_marker_does_not_open_the_gate():
    """`json.dumps(pem.splitlines())` 는 마커 줄 뒤에 `",` 를 남긴다."""
    module = load_runner()
    pem, lines = plain_pem()
    wrapped = json.dumps(pem.splitlines(), indent=2)
    cleaned = module.verify_excerpt(wrapped)
    for line in lines:
        assert line not in cleaned


def test_prose_after_the_marker_still_closes_the_gate():
    """부스러기 허용이 산문까지 통과시키면 보고서가 통째로 지워진다."""
    module = load_runner()
    report = "\n".join(
        [BEGIN + KEY + " was not found in the bundle"]
        + [f"FAILED important test {index}" for index in range(40)]
    )
    assert module.verify_excerpt(report).count("FAILED important") > 30


def test_output_text_blocks_are_concatenated():
    """Responses 계열은 조각 타입이 output_text 다. 스트림으로 보면 마지막만 남는다."""
    module = load_runner()
    payload = [
        {"type": "output_text", "text": "first "},
        {"type": "output_text", "text": "second"},
    ]
    assert module._first_text(payload) == "first second"


def test_mixed_content_array_ignores_non_text_blocks():
    """텍스트 블록과 tool_use 가 섞인 배열에서 파일 본문이 조언 자리에 오면 안 된다."""
    module = load_runner()
    payload = [
        {"type": "text", "text": "use a bounded queue"},
        {"type": "tool_use", "name": "Write", "input": {"content": "FILE_BODY_MARKER"}},
    ]
    result = module._first_text(payload)
    assert "FILE_BODY_MARKER" not in result
    assert "bounded queue" in result


# --- 라운드 19: 과잉 삭제, 이음매의 방향, 짧게 접힌 본문 -----------------------


@pytest.mark.parametrize(
    "source",
    [
        "AWS_SESSION_TOKEN: Optional[str] = None",
        "AWS_SECRET_ACCESS_KEY = credentials.secret_key",
        'AWS_SESSION_TOKEN = os.environ.get("AWS_SESSION_TOKEN")',
        'API_KEY_HEADER = "X-Api-Key"',
        'TOKEN_RE = re.compile(r"x")',
        "self.api_key = config.api_key",
        "aws_secret_access_key is not set in the environment",
    ],
)
def test_source_lines_survive_redaction(source):
    """이름이 비밀을 뜻해도 값이 코드면 지우지 않는다.

    과잉 삭제는 유출만큼 나쁘다. 조언자가 봐야 할 것은 실패한 소스 줄이고,
    그것을 지우면 이 기능의 존재 이유가 사라진다.
    """
    module = load_runner()
    assert module.verify_excerpt(source) == source


@pytest.mark.parametrize(
    "line,token",
    [
        ("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "wJalr"),
        ('"SecretAccessKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"', "wJalr"),
        ("AWS_SESSION_TOKEN=IQoJb3JpZ2luX2VjEHIaCXVzLXdlc3QiRzBFAiEA", "IQoJb"),
        ("api_key=abcdef1234567890abcdef", "abcdef1234"),
        ("PASSWORD=correcthorsebattery", "correcthorse"),
    ],
)
def test_real_credentials_are_still_redacted(line, token):
    """소스 줄을 살리는 완화가 진짜 자격증명까지 통과시키면 안 된다."""
    module = load_runner()
    assert token not in module.verify_excerpt(line)


def test_a_large_patch_does_not_evict_the_failure_signal():
    """앞쪽을 자르므로 반드시 남아야 하는 조각이 마지막이어야 한다.

    이 함수를 도입한 라운드에 순서가 반대였고, 8000자짜리 패치 하나가
    검증 실패 이유를 통째로 밀어냈다.
    """
    module = load_runner()
    block = module.untrusted_block("x" * 9000, "FAILED test_auth: assertion at line 42")
    assert "FAILED test_auth" in block


def test_the_stream_seam_is_safe_in_both_directions():
    """어느 스트림에 앞부분을 둘지 자식이 고른다. 한 방향만 막으면 새어 나간다."""
    module = load_runner()
    joined = module.join_streams("KEY=SECRETVALUE1234567890AB", "AWS_SECRET_ACCESS_")
    assert "SECRETVALUE1234" not in joined


def test_ordinary_streams_are_joined_unchanged():
    """이음매 방어가 평범한 출력을 훼손하면 안 된다."""
    module = load_runner()
    assert module.join_streams("out ok ", "err ok") == "out ok err ok"


@pytest.mark.parametrize("width", [4, 8, 16, 40, 64])
def test_key_bodies_folded_at_any_width_are_redacted(width):
    """짧게 재접힌 본문은 어느 한 줄도 본문으로 안 보인다. 줄을 가로질러 센다."""
    module = load_runner()
    blob = base64.b64encode(b"\x01" * 1200).decode()
    folded = "\n".join(blob[index : index + width] for index in range(0, len(blob), width))
    pem = BEGIN + KEY + "\n" + folded + "\n" + END + KEY
    assert blob[200:240] not in module.verify_excerpt(pem)
