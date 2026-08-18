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
import hashlib
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
    assert module.advice_text_extracted(claude, ["claude", "--output-format", "json"])[0] == (
        "Return a copy of the list."
    )
    assert module.advice_text_extracted(codex, ["codex", "exec", "--json"])[0] == "Use a channel."
    # 구조화 출력을 요청하지 않았으면 stdout 은 산문이므로 그대로 쓴다.
    assert module.advice_text_extracted("just advice", ["claude", "--print"])[0] == "just advice"


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
    assert (
        module.advice_text_extracted(envelope, ["claude", "--output-format", "json"])[0] == expected
    )


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
    assert (
        module.advice_text_extracted(envelope, ["claude", "--output-format", "json"])[0]
        == "first second"
    )


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


# --- 라운드 20: 창의 방향, 판정 술어, 코드와 자격증명의 경계 -------------------


def test_the_reverse_seam_window_drops_the_right_edges():
    """역순 이음매를 이루는 조각은 err 의 꼬리와 out 의 머리다.

    앞선 판은 반대쪽(out 의 꼬리, err 의 머리)을 버려, 검출을 유발한 두
    조각이 고스란히 살아남았다.
    """
    module = load_runner()
    out = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY" + "p" * 5000
    err = "q" * 5000 + "AWS_SECRET_ACCESS_KEY="
    assert "wJalrXUtnFEMI" not in module.join_streams(out, err)


def test_short_streams_are_dropped_when_the_seam_forms_a_credential():
    """양쪽이 창보다 짧으면 값이 한 스트림 안에 통째로 있다. 남길 방법이 없다."""
    module = load_runner()
    joined = module.join_streams("KEY=SECRETVALUE1234567890AB", "AWS_SECRET_ACCESS_")
    assert "SECRETVALUE1234" not in joined


def test_the_seam_verdict_does_not_rest_on_length():
    """길이가 같아도 잡은 것이 다를 수 있다. 동등성으로 판정해야 한다.

    두 순서가 **서로 다른** 자격증명을 같은 길이만큼 지우면 길이 비교는
    "차이 없음" 을 내지만 잡은 것은 다르다. 그때 정방향 결과만 내보내면
    역순에서만 형성되는 값이 남는다.
    """
    module = load_runner()
    out = "KEN=abcdef1234567890\nAWS_SECRET_ACCESS_"
    err = "KEY=wJalrXUtnFEMI/K7MDENGbPxRf\nAPI_TO"
    joined = module.join_streams(out, err)
    assert "abcdef1234567890" not in joined
    assert "wJalrXUtnFEMI/K7" not in joined


@pytest.mark.parametrize(
    "line,token",
    [
        ("PASSWORD=correct.horse.battery", "correct.horse"),
        ("DB_PASSWORD=Some.Long.Pass.Phrase", "Some.Long"),
        (
            "SENDGRID_API_KEY=SG.abcdefghijklmnopqrstuv.abcdefghijklmnopqrstuvwxyz0123",
            "SG.abcdefg",
        ),
    ],
)
def test_dotted_credentials_are_redacted(line, token):
    """점이 든 값도 자격증명이다. 점을 통째로 빼면 SendGrid 키가 그대로 나간다."""
    module = load_runner()
    assert token not in module.verify_excerpt(line)


@pytest.mark.parametrize(
    "source",
    [
        "self.api_key = config.api_key",
        "password = get_password(user)",
        'TOKEN_RE = re.compile(r"x")',
    ],
)
def test_spaced_assignments_read_as_code(source):
    """사람이 쓴 코드는 구분자를 띄우고, 환경 덤프는 붙인다. 그 차이로 가른다."""
    module = load_runner()
    assert module.verify_excerpt(source) == source


# --- 라운드 21: 창의 제거, 한쪽 공백, 표현의 깊이 -----------------------------


@pytest.mark.parametrize(
    "out,err,secret",
    [
        # 짧은 스트림
        ("KEY=SECRETVALUE1234567890AB", "AWS_SECRET_ACCESS_", "SECRETVALUE1234"),
        # 조각이 한 스트림의 머리부터 수천 자에 걸치는 경우 — 고정 창으로는 못 잡는다
        ("KEN=" + "A1" * 3000, "y" * 100 + "\nAPI_TO", "A1" * 40),
    ],
)
def test_the_reverse_seam_needs_no_window(out, err, secret):
    """창은 언제나 틀린 크기다. 순서를 바꾸면 창이 필요 없다."""
    module = load_runner()
    assert secret not in module.join_streams(out, err)


def test_the_reverse_seam_keeps_the_failure_signal():
    """이음매 대응이 검증 출력을 통째로 버리면 조언자가 진단할 것이 없다."""
    module = load_runner()
    joined = module.join_streams("KEN=A1B2C3D4E5F6G7\nFAILED test_auth", "API_TO")
    assert "FAILED test_auth" in joined


def test_a_long_key_split_across_streams_is_redacted():
    """긴 PEM 본문이 한 스트림의 머리에 통째로 있어도 잡아야 한다."""
    module = load_runner()
    blob = base64.b64encode(b"\x01" * 6000).decode()
    out = "VATE KEY-----\n" + blob + "\n" + END + KEY
    joined = module.join_streams(out, "x" * 100 + BEGIN + "PRI")
    assert blob[3000:3040] not in joined


@pytest.mark.parametrize(
    "line,token",
    [
        # YAML — 콜론 **뒤에만** 공백이 있다. 앞선 판은 양쪽 공백만 다뤘다.
        ("database_password: Tr0ub4dorHorseBattery", "Tr0ub4dor"),
        ("api_token: abcdef1234567890", "abcdef1234"),
        ("password: correcthorsebattery", "correcthorse"),
        # dotenv 와 환경 덤프
        ("PASSWORD=correct.horse.battery", "correct.horse"),
        ("API_TOKEN = abc123def456ghi789", "abc123def"),
    ],
)
def test_one_sided_spacing_is_covered(line, token):
    """설정 파일은 구분자 한쪽에만 공백을 둔다. 그 형식이 가장 흔하다."""
    module = load_runner()
    assert token not in module.verify_excerpt(line)


@pytest.mark.parametrize("depth", [1, 2, 3])
def test_known_secrets_survive_any_serialisation_depth(monkeypatch, depth):
    """표현을 열거하면 깊이가 임의적이다. 텍스트를 풀어 가며 본다."""
    secret = 'wJalr"XUtnFEMI\\K7MDENGbPxRfiCYEX'
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", secret)
    module = load_runner()
    wrapped = secret
    for _ in range(depth):
        wrapped = json.dumps(wrapped)[1:-1]
    assert wrapped not in module.verify_excerpt(f"payload {wrapped} end")


def test_percent_encoded_known_secret_is_redacted(monkeypatch):
    """퍼센트 인코딩도 한 겹이다."""
    import urllib.parse

    secret = "wJalrXUtnFEMIK7MDENGbPxRfiCYEX"
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", secret)
    module = load_runner()
    encoded = urllib.parse.quote(secret, safe="")
    assert encoded not in module.verify_excerpt(f"payload {encoded} end")


# --- 라운드 22: 양방향 형성, 인코딩 합성, 이름 쪽의 점 ------------------------


def test_credentials_formed_in_both_orders_are_both_redacted():
    """두 순서가 각각 다른 자격증명을 만들면 어느 한쪽 결과도 안전하지 않다.

    역순 결과를 그대로 내보내면, 정방향에서만 잡히던 값이 이름 없이 되살아난다.
    """
    module = load_runner()
    joined = module.join_streams(
        "KEN=abcdef1234567890\nAWS_SECRET_ACCESS_",
        "KEY=wJalrXUtnFEMI/K7MDENGbPxRf\nAPI_TO",
    )
    assert "abcdef1234567890" not in joined
    assert "wJalrXUtnFEMI/K7" not in joined


def test_removed_spans_recovers_the_redacted_regions():
    """지운 구간을 되짚지 못하면 None 을 줘야 한다 — 틀린 위치보다 낫다."""
    module = load_runner()
    assert module.removed_spans("abcXYZdef", "abc[REDACTED]def") == [(3, 6)]
    assert module.removed_spans("already [REDACTED] here", "already [REDACTED] here") is None


@pytest.mark.parametrize(
    "wrap",
    [
        "json",
        "json2",
        "json3",
        "percent",
        "percent_lower",
        "percent_then_json",
        "json_then_percent",
        "percent2",
    ],
)
def test_encoded_forms_are_closed_under_composition(monkeypatch, wrap):
    """손으로 고른 목록은 조합을 빠뜨리고, 텍스트 파싱은 여러 줄에서 깨진다.

    값 쪽에서 두 변환의 합성에 닫힌 집합을 만들면 둘 다 피한다.
    """
    import re as regex
    import urllib.parse

    secret = 'wJalr"XUtn/FEMIK7MDENGbPxRfiCYEX'
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", secret)
    module = load_runner()

    def js(value):
        return json.dumps(value)[1:-1]

    def pc(value):
        return urllib.parse.quote(value, safe="")

    forms = {
        "json": js(secret),
        "json2": js(js(secret)),
        "json3": js(js(js(secret))),
        "percent": pc(secret),
        "percent_lower": regex.sub(
            r"%[0-9A-Fa-f]{2}", lambda match: match.group(0).lower(), pc(secret)
        ),
        "percent_then_json": js(pc(secret)),
        "json_then_percent": pc(js(secret)),
        "percent2": pc(pc(secret)),
    }
    encoded = forms[wrap]
    # 여러 줄 텍스트에서도 동작해야 한다. 검증 출력은 언제나 여러 줄이다.
    assert encoded not in module.verify_excerpt(f"line one\nline two {encoded} end")


@pytest.mark.parametrize(
    "source",
    [
        "self.api_token=config.api_token",
        "obj.password=other.password",
        "self.token = self.token2",
        "password=get_password(user)",
    ],
)
def test_attribute_assignments_are_not_credentials(source):
    """이름 앞의 점은 속성 접근이다. 값 뒤의 괄호는 호출이다. 둘 다 코드다."""
    module = load_runner()
    assert module.verify_excerpt(source) == source


def test_a_name_glued_to_preceding_text_is_still_matched():
    """자식은 구분자 없이 붙여 쓸 수 있다. 값이 확실한 모양이면 위치를 안 따진다."""
    module = load_runner()
    assert "A1B2C3D4" not in module.verify_excerpt("yyyyAPI_TOKEN=A1B2C3D4E5F6G7H8")


# --- 라운드 23: 되짚기의 세 함정, 값 모양의 단일 정의 -------------------------


def test_removed_spans_handles_adjacent_and_repeated_regions():
    """맞닿은 표식을 '끝까지 지움' 으로 읽으면 엉뚱한 자리를 짚는다."""
    module = load_runner()
    assert module.removed_spans("abcXYZdef", "abc[REDACTED]def") == [(3, 6)]
    assert module.removed_spans("abcXXXdefYYYghi", "abc[REDACTED]def[REDACTED]ghi") == [
        (3, 6),
        (9, 12),
    ]
    assert module.removed_spans("abcXYZ", "abc[REDACTED]") == [(3, 6)]


def test_removed_spans_does_not_align_inside_the_removed_region():
    """살아남은 조각이 지워진 구간 안에도 있으면 뒤쪽 자리를 골라야 한다."""
    module = load_runner()
    assert module.removed_spans("AAsecretAAtail", "AA[REDACTED]AAtail") == [(2, 8)]


def test_removed_spans_refuses_when_the_token_is_already_present():
    """되짚을 수 없으면 틀린 위치를 주느니 없다고 말한다."""
    module = load_runner()
    assert module.removed_spans("a [REDACTED] b", "a [REDACTED] b") is None


def test_injecting_the_token_fails_closed():
    """되짚기 실패는 자식이 고를 수 있는 사건이다. 그 실패 방향이 안전해야 한다."""
    module = load_runner()
    joined = module.join_streams(
        "KEY=SECRETVALUE1234567890AB\n[REDACTED] noise", "AWS_SECRET_ACCESS_"
    )
    assert "SECRETVALUE1234" not in joined


@pytest.mark.parametrize(
    "source",
    [
        "password=get_password(user)",
        'AWS_SESSION_TOKEN = os.environ.get("AWS_SESSION_TOKEN")',
        "aws_secret_access_key = credentials.get_secret()",
    ],
)
def test_the_value_shape_rules_are_shared_by_every_branch(source):
    """갈래마다 값 규칙을 따로 적으면 한 갈래만 고쳐진다. 정의는 한 곳이다."""
    module = load_runner()
    assert module.verify_excerpt(source) == source


def test_a_partial_match_never_splits_a_value():
    """값을 절반만 지우는 것이 가장 나쁜 결과다.

    지울 것이면 전부 지우고, 남길 것이면 전부 남긴다. 토큰 끝을 고정하지
    않으면 `authentication_failure` 에서 `authentication` 만 지워져 뒤쪽이
    남는다 — 자격증명이었다면 앵커만 잃은 채 값이 남고, 코드였다면 조언자가
    읽을 수 없는 조각이 된다.
    """
    module = load_runner()
    cleaned = module.verify_excerpt("API_TOKEN=authentication_failure")
    # 전부 지워졌거나(이름까지 함께) 전부 남았거나 — 그 사이는 없어야 한다.
    assert cleaned in ("[REDACTED]", "API_TOKEN=authentication_failure")


# --- 라운드 24: 뒤집은 판정과 그 이유 ------------------------------------------


@pytest.mark.parametrize(
    "line,token",
    [
        # 숫자도 점도 없는 패스프레이즈. 앞선 판은 밑줄을 식별자의 표식으로
        # 보고 남겼는데, 그러면 이것이 통째로 조언자에게 간다.
        ("PASSWORD=correct_horse_battery", "correct_horse"),
        ("PASSWORD=correct.horse.battery", "correct.horse"),
        ("password: correcthorsebattery", "correcthorse"),
    ],
)
def test_passphrase_values_are_redacted_even_without_digits(line, token):
    """`correct_horse_battery` 와 `authentication_failure` 는 모양이 같다.

    정규식으로 갈릴 수 있는 것이 아니다. 네 라운드 동안 세 기준(점, 밑줄,
    구분자 공백)을 시도했고 셋 다 한쪽 방향으로 틀렸다. 그래서 **한 방향을
    고른다** — 지운다. 코드는 다른 신호로만 살린다(이름 앞의 점, 값 뒤의
    괄호, 따옴표 안의 공백). 그 셋에 안 걸리는 소스 줄은 지워지지만,
    조언자에게는 diff 가 함께 가므로 되찾을 수 있다. 반대 방향의 실수는
    되돌릴 수 없다.
    """
    module = load_runner()
    assert token not in module.verify_excerpt(line)


@pytest.mark.parametrize(
    "source",
    [
        # 이름 앞의 점 — 속성 접근
        "self.api_token=config.api_token",
        "obj.password=other.password",
        # 값 뒤의 괄호 — 호출
        "password=get_password(user)",
        "aws_secret_access_key = credentials.get_secret()",
        # 따옴표 안의 공백 — 문장
        'TOKEN_ERROR = "authentication failed"',
        'raise TokenError("invalid token here")',
    ],
)
def test_code_survives_through_the_three_remaining_signals(source):
    """코드를 살리는 신호는 이제 셋뿐이다. 그 셋이 실제로 동작해야 한다."""
    module = load_runner()
    assert module.verify_excerpt(source) == source


@pytest.mark.parametrize("width", [4, 8, 12, 16, 40, 64])
@pytest.mark.parametrize("prefix", ["", "[INFO] "])
def test_folded_key_bodies_are_actually_removed(width, prefix):
    """**프로브는 입력에 실제로 있는 줄이어야 한다.**

    앞선 판의 이 테스트는 접힘 너비보다 긴 40자 연속을 찾았다. 너비 4/8/16
    에서는 그 문자열이 입력 어디에도 없으므로, 리댁션이 항등 함수여도
    통과했다 — 그래서 네 자로 접힌 키가 통째로 새는 것을 다섯 라운드 동안
    가렸다. 게이트는 열리는데 범위 주사가 아무것도 안 먹고 있었다.
    """
    module = load_runner()
    blob = base64.b64encode(b"\x01" * 1200).decode()
    lines = [blob[index : index + width] for index in range(0, len(blob), width)]
    pem = BEGIN + KEY + "\n" + "\n".join(prefix + line for line in lines) + "\n" + END + KEY
    cleaned = module.verify_excerpt(pem)
    assert not [line for line in lines if line in cleaned]


def test_short_decoded_forms_stay_out_of_the_list(monkeypatch):
    """프로브가 실제 복호 형태를 담아야 한다.

    앞선 판은 `%2F`×6 을 등록하고 슬래시 4개짜리 문자열로 쟀다. 정확 일치
    치환이므로 6개짜리는 4개 안에 없고, 그래서 무엇을 해도 통과했다.
    """
    monkeypatch.setenv("SOME_TOKEN", "%2F%2F%2F%2F%2F%2F")
    module = load_runner()
    assert "//////" in module.verify_excerpt("path a//////b and c//////d")


@pytest.mark.parametrize(
    "text",
    [
        "-----BEGIN PRIVATE KEY-----\nFAILED authenticationfailure here\nTRACE line 42\n",
        "-----BEGIN PRIVATE KEY----- was not in the bundle\nFAILED configuration mismatch",
        "ERROR expected -----BEGIN PRIVATE KEY----- but authentication failed\nFAILED test_auth",
    ],
)
def test_a_marker_mention_never_swallows_the_failure(text):
    """줄 **안** 의 base64 연속만으로 게이트를 열면 산문이 키로 오인된다.

    `_prefix_variants` 가 낱말 경계에서도 자르므로, 산문 한 줄의 마지막
    낱말(`bundle`)이 짧은 본문으로 둔갑하기도 했다.
    """
    module = load_runner()
    assert "FAILED" in module.verify_excerpt(text)


# --- 라운드 25: 게이트/주사의 방향, 인코딩 조합, 정렬의 안전한 방향 -----------


@pytest.mark.parametrize(
    "text,kept",
    [
        (
            "-----BEGIN PRIVATE KEY-----\nFAILED\nauthenticationfailure\n"
            "Traceback\nAssertionError\nsource line 42\n",
            "FAILED",
        ),
        (
            "expected -----BEGIN PRIVATE KEY-----\n" + "FAILED\n" * 8 + "AssertionError: boom\n",
            "AssertionError",
        ),
        (
            "ERROR expected -----BEGIN PRIVATE KEY-----\n"
            "FAILED authenticationfailure -----END PRIVATE KEY-----\nTRACE line 42",
            "FAILED authenticationfailure",
        ),
    ],
)
def test_one_word_failure_lines_are_not_a_folded_key(text, kept):
    """접기는 길이가 같은 줄을 만들고, 산문은 길이가 제각각인 낱말을 만든다.

    한 낱말짜리 실패 줄을 base64 로 누적하면 실패 증거가 통째로 지워진다.
    `AssertionError: boom` 이 PEM 헤더로 분류되던 것도 함께 막는다 — 헤더
    이름은 RFC 1421 이 정한 것뿐이다.
    """
    module = load_runner()
    assert kept in module.verify_excerpt(text)


@pytest.mark.parametrize("width", [4, 8, 16, 40, 64])
def test_the_gate_and_the_walk_agree_on_folded_bodies(width):
    """게이트가 열리면 주사도 먹어야 한다. 주사가 더 엄격하면 마커만 지워진다.

    프로브는 입력에 실제로 있는 줄이다 — 그렇지 않으면 리댁션이 항등
    함수여도 통과한다.
    """
    module = load_runner()
    blob = base64.b64encode(hashlib.shake_256(b"seed").digest(1200)).decode()
    lines = [blob[index : index + width] for index in range(0, len(blob), width)]
    pem = BEGIN + KEY + "\n" + "\n".join(lines) + "\n" + END + KEY
    cleaned = module.verify_excerpt(pem)
    assert not [line for line in lines if line in cleaned]


@pytest.mark.parametrize(
    "source",
    [
        "self.aws_secret_access_key = credentials.secret_key",
        "credentials.aws_secret_access_key = credentials.secret_key",
        'API_TOKEN = application_config["api_token"]',
        'token = request.headers["Authorization"]',
    ],
)
def test_indexing_and_spaced_calls_read_as_code(source):
    """색인과 괄호 앞 공백도 코드의 표식이다. AWS 갈래도 같은 가드를 쓴다."""
    module = load_runner()
    assert module.verify_excerpt(source) == source


@pytest.mark.parametrize(
    "encoded_password,printed",
    [
        ("a1%2Fa1%2Fa1%2Fa1%2F", "a1/a1/a1/a1/"),
        ("%41%62%31%41%62%31%41%62%31%41%62%31", "Ab1Ab1Ab1Ab1"),
        ("ab%40ab%40ab%40ab%40", "ab@ab@ab@ab@"),
    ],
)
def test_decoded_proxy_passwords_are_redacted(monkeypatch, encoded_password, printed):
    """복호 등록 조건을 좁히면 진짜 자격증명이 빠진다.

    막으려던 것은 `//////` 처럼 한 글자의 반복이므로 두 종이면 충분하다.
    """
    monkeypatch.setenv("HTTPS_PROXY", f"http://user:{encoded_password}@proxy")
    module = load_runner()
    assert printed not in module.verify_excerpt(f"proxy rejected {printed}")


def test_percent_escape_case_is_chosen_per_escape(monkeypatch):
    """각 이스케이프가 대소문자를 따로 고른다. 형태 열거로는 조합이 2^n 이다."""
    monkeypatch.setenv("SOME_API_KEY", "abcdefghijklmnop/qrstuvwxyz?1234")
    module = load_runner()
    for mixed in (
        "abcdefghijklmnop%2fqrstuvwxyz%3F1234",
        "abcdefghijklmnop%2Fqrstuvwxyz%3f1234",
        "abcdefghijklmnop%2Fqrstuvwxyz%3F1234",
    ):
        assert mixed not in module.verify_excerpt(f"value {mixed} end")


def test_a_short_seam_fragment_is_not_replaced_globally():
    """여섯 자 조각을 전역으로 지우면 `FAILED` 같은 진단이 출력에서 사라진다."""
    module = load_runner()
    joined = module.join_streams("API_TOKEN=A1B2C3D4E5F6G7H8\nFAILED test_auth", "FAILED")
    assert "FAILED test_auth" in joined


def test_alignment_prefers_the_latest_position(monkeypatch):
    """이른 자리를 고르면 지워진 구간 안에서 정렬돼 구간이 짧아진다.

    늦은 자리는 구간을 키운다 — 더 지우는 쪽이므로 틀려도 안전한 방향이다.
    """
    monkeypatch.setenv("SOME_TOKEN", "ABCDEF_START_TAIL_END_SECRET_123")
    module = load_runner()
    joined = module.join_streams("START_TAIL_END_SECRET_123TAIL_END", "prefixABCDEF_")
    assert "START_TAIL_END_SECRET_123" not in joined


# --- 라운드 26: 휴리스틱을 버리고 내용을 본다 ---------------------------------


@pytest.mark.parametrize(
    "text,kept",
    [
        (
            "expected "
            + BEGIN
            + KEY
            + "\n"
            + "FAILED\n" * 8
            + END
            + KEY
            + " in fixture\nTraceback",
            "FAILED",
        ),
        (
            BEGIN + KEY + "\n" + "FAILED\n" * 8 + END + KEY + "\nTRACE\nline 42",
            "FAILED",
        ),
    ],
)
def test_repeated_failure_words_between_markers_are_not_a_key(text, kept):
    """마커 쌍 사이를 **모양** 으로 판정하면 양쪽으로 틀린다.

    조각 길이의 균일함을 보던 앞선 판은 같은 낱말이 여덟 번 이어진 진단
    로그를 키로 오인했고, 줄 길이가 들쭉날쭉한 진짜 키를 놓쳤다. 대신
    내용이 실제로 무엇인지 본다 — base64 로 풀리는가, 푼 결과가 DER
    SEQUENCE 로 시작하는가.
    """
    module = load_runner()
    assert kept in module.verify_excerpt(text)


def test_a_variably_folded_key_is_redacted():
    """줄 길이가 고르지 않아도 DER 로 풀리면 키다.

    **프로브는 입력에 실제로 있는 조각이어야 한다.** 앞선 판은 접힌 자리를
    가로지르는 40자 연속을 찾았고, 그 문자열은 입력 어디에도 없어 리댁션이
    항등 함수여도 통과했다 — tools/check_test_vacuity.py 가 그것을 찾아냈다.
    """
    module = load_runner()
    body = base64.b64encode(b"\x30\x82" + hashlib.shake_256(b"var").digest(400)).decode()
    ragged = []
    index = 0
    for width in (1, 2, 1, 3, 5, 8, 13, 21, 34):
        ragged.append(body[index : index + width])
        index += width
    ragged.append(body[index:])
    pem = BEGIN + KEY + "\n" + "\n".join(ragged) + "\n" + END + KEY
    cleaned = module.verify_excerpt(pem)
    # 마지막 조각은 길고 입력에 그대로 있다. 그것이 사라져야 한다.
    assert ragged[-1] not in cleaned
    assert len(ragged[-1]) > 100


def test_pgp_armoured_private_keys_are_redacted():
    """PGP armor 는 DER 이 아니다. 양으로 본다 — 마커 쌍 사이의 수백 자."""
    module = load_runner()
    body = base64.b64encode(hashlib.shake_256(b"pgp").digest(300)).decode()
    armour = (
        "-----BEGIN PGP PRIVATE KEY BLOCK-----\nVersion: GnuPG v2\n\n"
        + body
        + "\n=abcd\n-----END PGP PRIVATE KEY BLOCK-----\nFAILED: signature mismatch"
    )
    cleaned = module.verify_excerpt(armour)
    assert body[:40] not in cleaned
    # 반대 방향: 뒤따르는 실패 신호는 남아야 한다.
    assert "FAILED: signature mismatch" in cleaned


def test_a_short_diagnostic_word_is_not_replaced_globally():
    """같은 규칙이 반대 방향도 지켜야 한다."""
    module = load_runner()
    joined = module.join_streams("API_TOKEN=A1B2C3D4E5F6G7H8\nFAILED test_auth", "FAILED")
    assert "FAILED test_auth" in joined


def test_composed_encodings_also_get_case_insensitive_matching(monkeypatch):
    """JSON 으로 감싼 뒤 퍼센트 인코딩한 형태도 이스케이프 대소문자를 고를 수 있다."""
    import re as regex
    import urllib.parse

    secret = 'Abcd"efgh/ijklmnop1234'
    monkeypatch.setenv("SOME_API_KEY", secret)
    module = load_runner()
    escaped = json.dumps(secret)[1:-1]
    encoded = urllib.parse.quote(escaped, safe="")
    lowered = regex.sub(r"%[0-9A-Fa-f]{2}", lambda match: match.group(0).lower(), encoded)
    assert lowered not in module.verify_excerpt(f"value {lowered} end")


# --- 라운드 27: 표식으로 형식을 가리고, 위치로 값과 이름을 가른다 -------------


def test_a_punctuated_armour_header_does_not_void_the_body():
    """헤더는 **줄 단위** 로 건너뛴다.

    낱말 단위로 보면 `Version: GnuPG v2.4.7 (GNU/Linux)` 에서 `GnuPG` 가 본문
    으로 섞이고 `v2.4.7` 에서 산문으로 판정돼 키 전체가 빠져나간다.
    """
    module = load_runner()
    armour = (
        "-----BEGIN PGP PRIVATE KEY BLOCK-----\n"
        "Version: GnuPG v2.4.7 (GNU/Linux)\n\n"
        + ("QUJD" * 60)
        + "\n=abcd\n-----END PGP PRIVATE KEY BLOCK-----\nFAILED: signature mismatch"
    )
    cleaned = module.verify_excerpt(armour)
    assert "QUJDQUJD" not in cleaned
    assert "FAILED: signature mismatch" in cleaned


@pytest.mark.parametrize(
    "text,kept",
    [
        # 양만 보면 서른네 번 이어 붙인 낱말이 임계값을 넘는다.
        (BEGIN + KEY + "\n" + "FAILED\n" * 34 + END + KEY + "\nTRACE line 42", "FAILED"),
        # DER 첫 바이트만 보면 세 바이트짜리 문자열이 키가 된다.
        (
            "expected " + BEGIN + KEY + "\nMAAA\nTraceback\nFAILED\n" + END + KEY + "\nline 42",
            "Traceback",
        ),
    ],
)
def test_quantity_alone_does_not_make_a_key(text, kept):
    """표식(armor 체크섬, PEM 헤더)이 없으면 양의 문턱이 훨씬 높아야 한다."""
    module = load_runner()
    assert kept in module.verify_excerpt(text)


def test_the_name_side_of_a_seam_stays_in_the_source():
    """이름은 소스에도 나오는 평범한 식별자일 수 있다. 전역으로 지우면 안 된다."""
    module = load_runner()
    joined = module.join_streams("=ABCDEFGHIJKLMNOP\ndef mySuperSecret(): pass", "mySuperSecret")
    assert "def mySuperSecret(): pass" in joined


# --- 라운드 28: 헤더의 세 갈래, 구분자 위치 ------------------------------------


def test_a_diagnostic_colon_line_is_not_a_pem_header():
    """`Name: value` 를 전부 헤더로 보면 진단 줄이 문턱을 낮춘다.

    `AssertionError: boom` 이 헤더가 되면 표식 없는 400자 문턱이 200자로
    내려가고, 그 줄 자체도 건너뛰어져 실패 증거가 사라진다.
    """
    module = load_runner()
    text = (
        "before\n"
        + BEGIN
        + KEY
        + "\nAssertionError: boom\n"
        + ("QUJD" * 55)
        + "\n"
        + END
        + KEY
        + "\nFAILED keep"
    )
    cleaned = module.verify_excerpt(text)
    assert "AssertionError: boom" in cleaned


def test_header_shaped_body_lines_are_still_body():
    """`Name: <base64>` 로 위장한 본문을 통째로 버리면 키가 빠져나간다."""
    module = load_runner()
    blob = base64.b64encode(b"\x30\x82" + hashlib.shake_256(b"k").digest(400)).decode()
    chunks = [blob[index : index + 64] for index in range(0, len(blob), 64)]
    pem = (
        BEGIN
        + KEY
        + "\n"
        + "\n".join("PrivateBody: " + chunk for chunk in chunks)
        + "\n"
        + END
        + KEY
    )
    cleaned = module.verify_excerpt(pem)
    # **프로브는 입력에 실제로 있는 줄이다.** 64자 경계를 가로지르는 조각을
    # 찾으면 리댁션이 항등 함수여도 통과한다.
    assert not [chunk for chunk in chunks if chunk in cleaned]


@pytest.mark.parametrize(
    "out,err,kept",
    [
        # 접두사 토큰은 구분자가 없다 — 값 하나가 이음매를 가로지를 뿐이다.
        (
            "FAILED test_auth\nsource: FAILED should remain\n",
            "sk-ABCDEFGHIJ",
            "FAILED should remain",
        ),
        ("FAIL\nFAILED test_auth\n", "ghp_ABCDEFGHIJKLMNOP", "FAILED test_auth"),
        # 값이 이음매를 가로지르면 out 쪽 조각만 값인 것이 아니다.
        (
            "Traceback: failure at auth.py:42\nTraceback: repeated",
            "AWS_SECRET_ACCESS_KEY: prefix",
            "Traceback: repeated",
        ),
    ],
)
def test_the_seam_value_is_scoped_by_the_separator(out, err, kept):
    """ "err 쪽이 이름" 은 이름-값 형태에만 맞다.

    구분자가 없거나 값이 이음매를 가로지르면 out 쪽 조각도 값의 일부일 뿐이라,
    그것을 전역으로 지우면 멀쩡한 로그가 사라진다. 값의 범위는 **첫** 구분자가
    정한다 — 마지막 것을 잡으면 값 안에 우연히 든 콜론이 경계가 된다.
    """
    module = load_runner()
    assert kept in module.join_streams(out, err)


# --- 라운드 29: 키 형식의 매직, 위장한 본문, 값 텍스트 ------------------------


def test_openssh_private_keys_are_redacted():
    """DER 만 보면 ssh-keygen 의 기본 출력이 통째로 샌다.

    OpenSSH 형식의 본문은 `openssh-key-v1\\0` 로 시작하므로 DER SEQUENCE
    검사에 걸리지 않고, armor 체크섬도 PEM 헤더도 없다. 좁게 접으면 줄 단위
    판정도 빠져나가 400자 문턱만 남는데, ed25519 키는 그보다 짧다.
    """
    module = load_runner()
    body = base64.b64encode(b"openssh-key-v1\x00" + bytes(range(256))).decode()
    ragged = []
    index = 0
    for width in list(range(1, 16)) * 30:
        if index >= len(body):
            break
        ragged.append(body[index : index + width])
        index += width
    pem = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        + "\n".join(ragged)
        + "\n-----END OPENSSH PRIVATE KEY-----"
    )
    cleaned = module.verify_excerpt(pem)
    assert not [chunk for chunk in ragged if len(chunk) > 8 and chunk in cleaned]


def test_four_character_chunks_behind_a_colon_prefix_are_body():
    """base64 한 묶음(4자)이면 본문이다.

    여덟 자를 요구했더니 네 자씩 쪼개 헤더로 위장한 본문이 빠져나갔다.
    게이트가 그것을 본문으로 세면 **범위 주사도 먹어야 한다** — 안 먹으면
    마커만 지워지고 본문이 남는다.
    """
    module = load_runner()
    blob = base64.b64encode(b"\x30\x82" + hashlib.shake_256(b"chunks").digest(400)).decode()
    chunks = [blob[index : index + 4] for index in range(0, len(blob), 4)]
    pem = (
        BEGIN
        + KEY
        + "\n"
        + "\n".join("PrivateBody: " + chunk for chunk in chunks)
        + "\n"
        + END
        + KEY
    )
    cleaned = module.verify_excerpt(pem)
    assert not [chunk for chunk in chunks if "PrivateBody: " + chunk in cleaned]


def test_a_separator_inside_a_known_secret_is_not_a_name_boundary(monkeypatch):
    """아는 값이 콜론을 담으면 그것은 값의 일부다.

    이름-값 경계로 읽으면 흔한 낱말(`FAILED`)이 전역 삭제의 대상이 된다.
    """
    monkeypatch.setenv("SOME_TOKEN", "prefix:FAILED")
    module = load_runner()
    joined = module.join_streams("FAILED test_auth\nsource FAILED remains", "SOME_TOKEN=prefix:")
    assert "source FAILED remains" in joined


# --- 라운드 30: PPK, DER 길이, 이음매 전역 삭제의 경계 ------------------------


def test_putty_private_key_bodies_are_redacted():
    """PuTTY 형식에는 PEM 마커가 없다. `Private-Lines: N` 이 스스로 선언한다."""
    module = load_runner()
    ppk = (
        "PuTTY-User-Key-File-3: ssh-ed25519\nEncryption: none\nComment: demo\n"
        "Public-Lines: 1\nQUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=\n"
        "Private-Lines: 2\nYWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXo=\n"
        "MTIzNDU2Nzg5MGFiY2RlZmdoaWprbG1ub3A=\nPrivate-MAC: 0011\nFAILED keep this"
    )
    cleaned = module.verify_excerpt(ppk)
    assert "YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXo=" not in cleaned
    assert "MTIzNDU2Nzg5MGFiY2RlZmdoaWprbG1ub3A=" not in cleaned
    # 공개 부분과 뒤따르는 실패 신호는 남아야 한다.
    assert "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=" in cleaned
    assert "FAILED keep this" in cleaned


def test_a_huge_private_lines_count_does_not_erase_the_output():
    """선언된 줄 수는 자식이 정한다. 상한이 없으면 출력 전체를 지우게 만든다."""
    module = load_runner()
    text = "Private-Lines: 9999\nkeep\nthis\ntext"
    assert module.verify_excerpt(text) == text


@pytest.mark.parametrize(
    "text,kept",
    [
        (
            BEGIN + KEY + "\n" + "AssertionError: MAAA\n" * 30 + END + KEY + "\nTRACE",
            "AssertionError",
        ),
        (BEGIN + KEY + "\nAssertionError: M" + "A" * 63 + "\n" + END + KEY, "AssertionError"),
    ],
)
def test_der_needs_a_sane_length_field(text, kept):
    """태그 한 바이트만 보면 `MAAA`(0x30 0x00 0x00)가 키가 된다.

    개인키는 길이가 127 을 넘으므로 DER 장형 길이를 쓴다. 그 필드까지 봐야
    진단 문자열과 갈린다.
    """
    module = load_runner()
    assert kept in module.verify_excerpt(text)


def test_an_unnamed_word_duplicate_is_not_erased_globally():
    """이음매에서 배운 값이 순소문자 낱말이면 이름 없는 자리는 건드리지 않는다.

    `authentication_failure` 는 소스 앵커와 구별할 수 없다. 전역으로 지우면
    조언자가 무엇이 실패했는지 못 본다 — 이 자리는 세 라운드 동안 두 방향을
    왕복했고, 여기서 방향을 고정한다.
    """
    module = load_runner()
    joined = module.join_streams(
        "authentication_failure\nsource authentication_failure remains", "API_TOKEN="
    )
    assert "source authentication_failure remains" in joined


def test_known_secret_forms_are_not_cached_across_environment_changes(monkeypatch):
    """환경을 한 번 읽어 붙잡으면 자식을 띄운 뒤의 변화를 못 따라간다."""
    module = load_runner()
    module.known_secret_forms()
    monkeypatch.setenv("SOME_TOKEN", "prefix:FAILEDvalue")
    assert "prefix:FAILEDvalue" in module.known_secret_forms()


# --- 라운드 31: 빠른 검사의 일치, 선언은 최소치, 주석과 코드 ------------------


def test_the_ppk_fast_check_matches_its_regex():
    """빠른 검사와 정규식이 다른 규칙이면 빠른 검사가 fail-open 이 된다."""
    module = load_runner()
    text = "private-lines: 1\nYWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXo=\nFAILED keep"
    cleaned = module.verify_excerpt(text)
    assert "YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXo=" not in cleaned
    assert "FAILED keep" in cleaned


def test_an_undercounted_private_lines_declaration_still_removes_the_body():
    """선언된 줄 수는 자식이 정한다. 적게 선언하면 남은 본문이 그대로 나간다."""
    module = load_runner()
    text = (
        "PuTTY-User-Key-File-3: ssh-ed25519\nPrivate-Lines: 1\n"
        "YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXo=\n"
        "MTIzNDU2Nzg5MGFiY2RlZmdoaWprbG1ub3A=\nPrivate-MAC: 0011\nFAILED keep"
    )
    cleaned = module.verify_excerpt(text)
    assert "MTIzNDU2Nzg5MGFiY2RlZmdoaWprbG1ub3A=" not in cleaned
    # 본문 모양이 아닌 줄에서 멈춰야 한다.
    assert "Private-MAC: 0011" in cleaned
    assert "FAILED keep" in cleaned


# --- 라운드 32: PPK 의 다섯 결함, 입력 쪽 리댁션 -----------------------------


def test_the_ppk_anchor_sees_escaped_line_breaks():
    """앵커도 본 주사와 같은 줄 구분자를 봐야 한다.

    물리적 줄바꿈만 보면 JSON 에 직렬화된 로그(`\\n` 두 글자)에서 앵커가
    안 잡혀 본문이 통째로 나간다.
    """
    module = load_runner()
    text = "Private-Lines: 1\\nU0VDUkVUS0VZMTIzNDU2Nzg5MA=="
    assert "U0VDUkVUS0VZMTIzNDU2Nzg5MA==" not in module.verify_excerpt(text)


def test_an_over_cap_declaration_does_not_switch_redaction_off():
    """상한을 넘는 선언에 `continue` 를 걸면 그 상한이 리댁션을 끄는 스위치가 된다."""
    module = load_runner()
    body = "AAAAgQCx3pT1Zk9mQ2p0R5v8Wc7fLd6eXyZbNhQ0aB1cD2eF3gH4iJ5kL6mN7oP8qR"
    assert body not in module.verify_excerpt(f"Private-Lines: 900\n{body}")


@pytest.mark.parametrize(
    "text,kept",
    [
        # 이어짐 판정이 낱말을 본문으로 보면 실패 앵커가 지워진다.
        ("Private-Lines: 1\nQUFB\nFAILED\nTraceback\nAssertionError", "FAILED"),
        # 봉투가 없으면 선언을 믿지 않는다 — 자식이 심을 수 있는 한 줄이다.
        (
            "Private-Lines: 5\nFAILED tests/test_auth.py::test_login\nline2\nline3\nline4\nline5",
            "FAILED tests/test_auth.py",
        ),
    ],
)
def test_the_declaration_alone_does_not_authorise_deletion(text, kept):
    """선언을 믿는 것은 PPK 봉투가 함께 있을 때뿐이다."""
    module = load_runner()
    assert kept in module.verify_excerpt(text)


def test_the_envelope_does_not_authorise_deleting_non_body_lines():
    """봉투가 있어도 **모양은 본다.**

    앞선 판은 봉투가 있으면 선언한 만큼 무조건 지웠다. 그러면 자식이 봉투
    한 줄을 붙이고 큰 수를 적어 그 만큼의 진단 로그를 지우게 할 수 있다.
    선언은 "어디까지" 를 말할 뿐 "무엇이든" 을 말하지 않는다.

    대가는 있다 — 자식이 본문 아닌 줄로 채워 진짜 본문을 뒤로 밀면 그
    본문은 남는다. 그러나 그것은 이름 없는 base64 로 남는 것이고, 진단
    로그를 지우는 쪽보다 낫다.
    """
    module = load_runner()
    text = (
        "PuTTY-User-Key-File-3: ssh-ed25519\nPrivate-Lines: 3\n"
        "FAILED tests/test_auth.py::test_login\nTraceback\nAssertionError"
    )
    cleaned = module.verify_excerpt(text)
    assert "FAILED tests/test_auth.py::test_login" in cleaned
    assert "Traceback" in cleaned


def test_the_task_text_is_redacted_before_it_reaches_the_advisor():
    """자식의 출력만 막고 입력을 안 막으면 과제에 붙어 온 자격증명이 그대로 나간다.

    조언자는 이 실행에서 유일하게 외부로 나가는 경로다.
    """
    module = load_runner()
    task = "Fix the parser.\npassword=Abcd1234Efgh5678"
    assert "Abcd1234Efgh5678" not in module.redact_text(task)


# --- 라운드 33: 자식이 정하는 수는 믿지 않는다 --------------------------------


@pytest.mark.parametrize(
    "text,kept",
    [
        # 문서 어딘가의 봉투 언급이 모든 선언을 신뢰하게 만들면 안 된다.
        (
            "Private-Lines: 2\nTraceback: boom\nAssertionError: failed\n"
            "PuTTY-User-Key-File-3: later",
            "Traceback: boom",
        ),
        # 봉투를 붙이고 큰 수를 적어도 모양이 아니면 안 지운다.
        (
            "PuTTY-User-Key-File-3: x\nPrivate-Lines: 3\n"
            "FAILED tests/test_auth.py::test_login\nTraceback\nAssertionError",
            "FAILED tests/test_auth.py::test_login",
        ),
    ],
)
def test_the_declared_count_never_authorises_deletion(text, kept):
    """`Private-Lines:` 는 **어디를 볼지** 만 말한다. 무엇을 지울지는 모양이 정한다.

    세 라운드에 걸쳐 선언과 봉투를 믿는 판을 세 번 냈고 세 번 다 틀렸다 —
    상한 초과 선언이 리댁션을 끄는 스위치가 되고, 적게 선언하면 남은 본문이
    나가고, 봉투 한 줄을 심으면 선언한 만큼의 진단 로그가 지워졌다.
    """
    module = load_runner()
    assert kept in module.verify_excerpt(text)


def test_the_ppk_anchor_tolerates_line_prefixes():
    """PEM 경로는 접두사를 벗기는데 PPK 앵커만 안 벗기면 쌍둥이 한쪽만 막힌다."""
    module = load_runner()
    body = "YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXo="
    assert body not in module.verify_excerpt(f"+++ b/x\n+Private-Lines: 1\n+{body}")


# --- 라운드 34: 한 루프가 모든 줄을 같은 방식으로 본다 ------------------------


def test_the_last_wrapped_ppk_line_is_short():
    """PuTTY 는 예순네 자로 접으므로 마지막 줄은 네 자일 수도 있다.

    모든 줄에 열여섯 자를 요구하면 대략 다섯 키에 하나꼴로 꼬리가 남는다.
    길이 조건은 **첫 줄에만** 건다 — 산문 한 낱말로 본문이 시작되는 것만
    막으면 된다.
    """
    module = load_runner()
    text = "Private-Lines: 2\n" + "A" * 60 + "\nQUJD\nPrivate-MAC: x"
    cleaned = module.verify_excerpt(text)
    assert "QUJD" not in cleaned
    assert "Private-MAC: x" in cleaned


def test_the_ppk_walk_normalises_escaped_line_breaks():
    """앵커와 본문이 같은 루프에서 같은 방식으로 정규화돼야 한다."""
    module = load_runner()
    body = "YWJjZGVmZ2hpamtsbW5vcA=="
    assert body not in module.verify_excerpt(f"Private-Lines: 1\\n{body}\\nPrivate-MAC: x")


def test_a_quoted_declaration_does_not_erase_prose():
    """인용된 선언 뒤의 산문 한 낱말은 본문이 아니다."""
    module = load_runner()
    assert "AssertionError" in module.verify_excerpt(
        "diagnostic:\n> Private-Lines: 1\nAssertionError"
    )


@pytest.mark.parametrize(
    "line,token",
    [
        ("API_TOKEN = abc123def456ghi789 (note)", "abc123def456ghi789"),
        # 숫자 없는 값에도 같은 규칙이다. 완화를 남기면 자식이 괄호 주석
        # 하나를 덧붙여 자격증명을 통과시킬 수 있다.
        ("PASSWORD=orchid_copper_velvet (copied)", "orchid_copper_velvet"),
    ],
)
def test_a_value_before_a_spaced_paren_is_still_redacted(line, token):
    """공백을 사이에 둔 괄호는 호출이 아니라 주석이다.

    호출로 보면 자식이 `(copied)` 한 마디를 덧붙이는 것만으로 값을 통과시킬
    수 있다. 대가는 `password=get_password (user)` 같은 소스 줄이 지워지는
    것인데, 그 서식은 PEP8 이 금지하는 형태이고 diff 가 함께 가므로 되찾을
    수 있다 — 반대 방향은 되돌릴 수 없다.
    """
    module = load_runner()
    assert token not in module.verify_excerpt(line)


# --- 라운드 35: 진입 조건과 동치성 --------------------------------------------


def test_the_seam_arm_is_entered_only_when_the_seam_is_the_cause():
    """진입 조건이 "이음매에서 형성됐다" 와 동치가 아니었다.

    `reverse != 따로 지운 것` 은 역순이 **덜** 지울 때도 참이다. 그때까지
    검증 출력을 통째로 버리면, 자식이 스트림 하나를 마침표 하나로 만들어
    진단을 통째로 날릴 수 있다. 가로지르는 구간이 있을 때만 복구한다.
    """
    module = load_runner()
    joined = module.join_streams("PASSWORD=orchid_copper_velvet\nAssertionError: boom", ".")
    assert "AssertionError: boom" in joined
    assert "orchid_copper_velvet" not in joined


def test_the_name_value_separator_stays_on_one_line():
    """값의 끝은 같은 줄로 못박아 놓고 시작은 안 그랬다.

    `\\s` 는 줄바꿈을 포함하므로, 비밀 이름이 줄 끝에 있으면 다음 줄 첫
    토큰이 값으로 잡혀 두 줄이 한 매치로 지워진다.
    """
    module = load_runner()
    text = "DB_PASSWORD=\nnext_line_value_here"
    assert module.verify_excerpt(text) == text


def test_a_ppk_body_longer_than_the_old_cap_is_fully_redacted():
    """상한에서 멈추면 같은 본문의 나머지가 그대로 나간다."""
    module = load_runner()
    body = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo="
    cleaned = module.verify_excerpt("Private-Lines: 401\n" + (body + "\n") * 401)
    assert body not in cleaned


# --- 라운드 36: 이음매는 리댁션을 없앨 수도 있다 ------------------------------


def test_the_seam_can_suppress_a_redaction_too():
    """이음매는 자격증명을 **만들** 수도, 이미 있던 리댁션을 **없앨** 수도 있다.

    `API_TOKEN=<값>` 뒤에 stderr 의 첫 글자가 `(` 이면 값 끝 판정이 그것을
    호출로 보고 매치를 통째로 죽인다. 원문을 이어 붙인 뒤에만 지우면 그
    억제를 막을 방법이 없다 — 조각을 먼저 지운 뒤 이어 다시 훑는다.
    """
    module = load_runner()
    assert "abc123def456ghi789" not in module.join_streams("API_TOKEN=abc123def456ghi789", "(")


@pytest.mark.parametrize(
    "text,token",
    [
        ("password: |\n  abc123def456ghi789", "abc123def456ghi789"),
        ("api_token: >-\n  abc123def456ghi789", "abc123def456ghi789"),
    ],
)
def test_yaml_block_scalars_are_redacted(text, token):
    """YAML 블록 스칼라는 값이 다음 줄에 온다.

    구분자를 다시 줄바꿈 넘게 열면 앞선 라운드의 결함(비밀 이름이 줄 끝에
    있으면 다음 줄 첫 토큰이 값으로 잡힘)이 돌아오므로 이 한 형태만 잡는다.
    """
    module = load_runner()
    assert token not in module.redact_text(text)


def test_an_ordinary_block_scalar_survives():
    """같은 규칙의 반대 방향. 비밀 이름이 아닌 블록은 건드리지 않는다."""
    module = load_runner()
    text = "description: |\n  a short note"
    assert module.verify_excerpt(text) == text


# --- 라운드 37: 분석과 출력을 가르고, 들여쓰기를 역참조로 --------------------


def test_a_first_pass_marker_does_not_disable_seam_recovery():
    """지운 조각에는 `[REDACTED]` 표식이 들어 있다.

    `removed_spans` 는 원문에 그 표식이 있으면 되짚기를 포기하므로, 조각을
    먼저 지우도록 바꾼 순간 그 포기가 **언제나** 일어나 정상 출력이 통째로
    버려졌다. 출력은 지운 조각에서, 분석은 원문에서 한다.
    """
    module = load_runner()
    joined = module.join_streams(
        "Cobalt7SilverAnchor bare API_TOKEN=Zz9Yy8Xx7Ww6Vv5",
        "\nAWS_SECRET_ACCESS_KEY=Aa1Bb2Cc3Dd4Ee5",
    )
    assert "Cobalt7SilverAnchor" not in joined


def test_a_block_scalar_stops_at_the_key_indentation():
    """본문은 **키보다 더 들여쓴 줄** 까지다.

    들여쓰기를 역참조로 잡지 않으면 뒤따르는 들여쓴 줄을 전부 먹어, 같은
    깊이의 진단 줄이 함께 지워진다.
    """
    module = load_runner()
    text = "  password: |\n    orchidcoppervelvet\n  AssertionError: expected 1, got 2"
    cleaned = module.verify_excerpt(text)
    assert "orchidcoppervelvet" not in cleaned
    assert "AssertionError: expected 1, got 2" in cleaned


@pytest.mark.parametrize(
    "text,token",
    [
        # 명시 들여쓰기 지시자
        ("password: |2\n  cobalt7silveranchor", "cobalt7silveranchor"),
        # chomping
        ("api_token: >-\n  abc123def456ghi789", "abc123def456ghi789"),
        ("api_key: |+\n  Zz9Yy8Xx7Ww6Vv5Uu4", "Zz9Yy8Xx7Ww6Vv5Uu4"),
    ],
)
def test_block_scalar_indicators_are_recognised(text, token):
    """YAML 은 `|2`, `|-`, `>+` 같은 지시자를 붙일 수 있다."""
    module = load_runner()
    assert token not in module.redact_text(text)


# --- 라운드 38: 분석과 출력이 다른 텍스트를 본다 ------------------------------


def test_raw_spans_are_applied_to_raw_text():
    """원문에서 찾은 구간을 이미 지운 텍스트에 대고 맞추면 어긋난다.

    그 텍스트에는 표식이 박혀 있어 정확 일치가 대부분 실패한다. 조각을
    원문에서 걷어낸 뒤 마지막에 한 번 지운다.
    """
    module = load_runner()
    joined = module.join_streams(
        "B2C3D4E5F6G7H8I9\nsk-ABCDEFGHIJKLMNOPQRST", "AWS_SECRET_ACCESS_KEY=A1"
    )
    assert "B2C3D4E5F6G7H8I9" not in joined


@pytest.mark.parametrize(
    "text,token",
    [
        ("password: |-2\n  correct_horse_battery", "correct_horse_battery"),
        ("api_key: |2-\n  Zz9Yy8Xx7Ww6Vv5Uu4", "Zz9Yy8Xx7Ww6Vv5Uu4"),
        ("token: >+\n  abc123def456ghi", "abc123def456ghi"),
    ],
)
def test_block_scalar_indicators_come_in_either_order(text, token):
    """YAML 은 `|2-` 와 `|-2` 를 둘 다 받는다. 한쪽만 받으면 다른 쪽이 샌다."""
    module = load_runner()
    assert token not in module.redact_text(text)


# --- 라운드 39: 수를 세는 판정은 코드로 -------------------------------------


def test_an_explicit_indentation_indicator_is_respected():
    """`|2` 는 본문이 두 칸 더 들여쓴 줄이라고 말한다.

    정규식은 수를 셀 수 없어 지시자를 무시했고, 한 칸만 들여쓴 진단 줄을
    본문으로 먹었다. 이 파일에서 정규식으로 옮긴 판정은 매번 양쪽으로
    틀렸고, 코드로 옮긴 판정(PPK, userinfo)은 그러지 않았다.
    """
    module = load_runner()
    text = "password: |2\n AssertionError: database unavailable\nTraceback"
    assert "AssertionError: database unavailable" in module.verify_excerpt(text)


@pytest.mark.parametrize(
    "text,token",
    [
        ("password: |\n  orchidcoppervelvet", "orchidcoppervelvet"),
        ("password: |-2\n  correct_horse_battery", "correct_horse_battery"),
        ("api_key: |2-\n  Zz9Yy8Xx7Ww6Vv5Uu4", "Zz9Yy8Xx7Ww6Vv5Uu4"),
        ("token: >+\n  abc123def456ghi", "abc123def456ghi"),
    ],
)
def test_block_scalar_bodies_are_still_redacted(text, token):
    """규칙을 코드로 옮겨도 원래 잡던 것은 그대로 잡아야 한다."""
    module = load_runner()
    assert token not in module.redact_text(text)


def test_a_block_scalar_keeps_a_sibling_diagnostic_line():
    """본문은 키보다 더 들여쓴 줄까지다. 같은 깊이의 줄은 형제이지 본문이 아니다."""
    module = load_runner()
    text = "  password: |\n    orchidcoppervelvet\n  AssertionError: expected 1, got 2"
    cleaned = module.verify_excerpt(text)
    assert "orchidcoppervelvet" not in cleaned
    assert "AssertionError: expected 1, got 2" in cleaned


def test_a_digits_only_seam_value_is_not_erased_output_wide():
    """이음매는 **가짜** 자격증명도 만든다.

    `API_TOKEN=` 뒤에 숫자열이 붙으면 문법상 자격증명이지만, 그 숫자열이
    실제로는 진단에 쓰인 수일 수 있다. 숫자 하나만 있어도 전역 삭제하면
    `assert retry_after == 123456789012` 같은 줄이 함께 사라진다.
    자격증명은 글자와 숫자가 섞이거나 대소문자가 섞인다.
    """
    module = load_runner()
    joined = module.join_streams("123456789012\nassert retry_after == 123456789012", "API_TOKEN=")
    assert "assert retry_after == 123456789012" in joined


@pytest.mark.parametrize(
    "text,kept",
    [
        # `|0` 은 유효하지 않은 지시자다. 그 수를 믿으면 필요한 들여쓰기가
        # 0 이 되어 뒤의 모든 줄을 먹는다.
        ("password: |0\nFAILED test\nTraceback", "FAILED test"),
        # 탭은 YAML 이 들여쓰기로 금지한다.
        (
            "password: |\n  correct_horse_battery\n\tTraceback: worker failed\n"
            "AssertionError: boom",
            "Traceback: worker failed",
        ),
    ],
)
def test_child_controlled_numbers_do_not_widen_the_block(text, kept):
    """지시자도 들여쓰기도 자식이 쓴다. 그 수를 그대로 믿으면 진단이 지워진다."""
    module = load_runner()
    assert kept in module.verify_excerpt(text)


def test_a_hyphenated_key_name_is_recognised():
    """이름의 구분자는 밑줄과 하이픈 둘 다다. `api_key` 만 보면 `api-key` 가 샌다."""
    module = load_runner()
    assert "orchidcoppervelvet" not in module.redact_text("api-key: |\n  orchidcoppervelvet")


def test_a_block_scalar_at_end_of_input_adds_no_newline():
    """원문에 없던 줄바꿈을 넣지 않는다."""
    module = load_runner()
    assert not module.verify_excerpt("password: |\n  s3cr3tvalue").endswith("\n")


def test_a_serialised_block_scalar_head_is_recognised():
    """본문은 이스케이프된 줄바꿈을 순회하는데 머리만 물리적 줄바꿈을 보면 샌다."""
    module = load_runner()
    text = "password: |\\n  correct_horse_battery\\nnext: failure"
    assert "correct_horse_battery" not in module.verify_excerpt(text)


def test_a_seam_learned_value_is_redacted_only_where_the_evidence_is():
    """이음매에서 얻은 근거는 **그 자리에 대한 것** 이다.

    여섯 라운드 동안 이 자리는 양쪽으로 왕복했다. 전역으로 지우면
    `AuthenticationFailure` 같은 소스 식별자가 출력 전체에서 사라지고, 안
    지우면 같은 값의 맨몸 중복이 남는다. 좁히면 진짜 자격증명을 놓치고
    (`correcthorsebattery`), 넓히면 진단을 지운다(`123456789012` 가
    `assert retry_after == 123456789012` 에도 있을 때). 그 사이에 안정된
    자리는 없었다.

    다른 자리의 같은 문자열에는 이름이 붙어 있지 않고, 이름 없는 문자열을
    못 잡는 것은 이 리댁터의 원래 한계다. 근거가 있는 자리만 지운다.
    """
    module = load_runner()
    joined = module.join_streams(
        "AuthenticationFailure\nraise AuthenticationFailure()", "API_TOKEN="
    )
    # 이음매 자리는 지워진다.
    assert not joined.startswith("AuthenticationFailure")
    # 이름 없는 다른 자리는 남는다 — 그것이 이 리댁터의 원래 한계다.
    assert "raise AuthenticationFailure()" in joined


def test_the_twin_pattern_also_learned_the_hyphen():
    """하이픈을 한 패턴에만 넣으면 쌍둥이 쪽으로 샌다."""
    module = load_runner()
    assert "orchidcoppervelvet" not in module.redact_text("api-key=orchidcoppervelvet123")


def test_an_invalid_indicator_rejects_the_header():
    """유효하지 않은 지시자를 1 로 대신하면 그 수를 자식이 고르는 것은 똑같다."""
    module = load_runner()
    text = "password: |0\n AssertionError: database unavailable\nTraceback"
    assert "AssertionError: database unavailable" in module.verify_excerpt(text)


def test_a_diff_prefixed_block_scalar_is_recognised():
    """패치 안의 YAML 은 줄마다 diff 표식을 단다."""
    module = load_runner()
    assert "correcthorsebattery" not in module.verify_excerpt(
        "+password: |\n+  correcthorsebattery"
    )


# --- 라운드 42: 표식은 맞추지 말고 벗긴다 ------------------------------------


@pytest.mark.parametrize(
    "text,token",
    [
        # YAML 시퀀스의 하이픈이 diff 표식과 같은 자리에 온다.
        ("- password: |\n    hunter2_correct_battery", "hunter2_correct_battery"),
        # 머리와 본문이 같은 표식.
        ("+password: |\n+  correcthorsebattery", "correcthorsebattery"),
    ],
)
def test_line_markers_are_decided_by_the_head(text, token):
    """줄머리 표식은 **머리가 정한다.**

    세 가지를 시도했고 앞의 둘은 각각 한 방향으로 틀렸다.

    - 머리와 본문이 **맞추도록** 요구하면 통합 diff 에서 머리가 문맥 줄이고
      본문만 바뀐 줄일 때 본문이 남는다.
    - **무조건 벗기면** `password: |` 뒤의 진단 불릿(`- AssertionError…`)이
      들여쓴 본문으로 둔갑해 실패 증거가 지워진다.

    머리에 표식이 있을 때만, 그 표식이 붙은 줄에서만 벗긴다.
    """
    module = load_runner()
    assert token not in module.verify_excerpt(text)


def test_the_hyphen_reached_the_suffix_too():
    """이름의 접두사에만 하이픈을 넣으면 접미사 쪽으로 샌다."""
    module = load_runner()
    assert "correcthorsebattery" not in module.redact_text(
        "db-password-primary: correcthorsebattery"
    )


def test_a_tool_payload_is_not_advice():
    """`content` 는 도구 결과에도 쓰이는 이름이다.

    배열 쪽은 이미 막았는데 dict 쪽만 안 막으면 비대칭이고, 그 틈으로 파일
    내용이 조언 자리에 들어간다.
    """
    module = load_runner()
    payload = {"type": "tool_result", "tool_use_id": "x", "content": "-----BEGIN id_rsa"}
    assert "id_rsa" not in module._first_text(payload)
    # 반대 방향: 평범한 content 는 여전히 본문이다.
    assert module._first_text({"content": "Use a bounded queue."}) == "Use a bounded queue."


def test_a_diagnostic_bullet_after_a_block_scalar_survives():
    """`password: |` 에는 표식이 없으므로 불릿은 불릿으로 남는다.

    무조건 벗기던 판은 `- AssertionError…` 를 들여쓴 본문으로 보고 지웠다.
    """
    module = load_runner()
    text = "password: |\n- AssertionError: TLS certificate failed\nTraceback"
    assert "AssertionError: TLS certificate" in module.verify_excerpt(text)


def test_a_context_line_head_with_changed_body_is_a_known_limit():
    """머리가 diff 문맥 줄이고 본문만 바뀐 줄이면 그 본문은 남는다.

    **문서화된 한계다.** 세 경우(문맥 머리 + 바뀐 본문 / 표식 머리 + 표식
    본문 / 표식 없는 머리 + 불릿)를 한 규칙으로 만들 수 없다 — 그 셋의
    요구가 서로 반대이기 때문이다. 표식 없는 머리 쪽을 택했다: 진단을
    지우는 것이 값 하나를 놓치는 것보다 나쁘고, 그 값은 이름 없이 남는
    문자열과 같은 부류로 남는다.

    이 테스트는 그 한계가 **의도된 것** 임을 기록한다. 여기가 바뀌면
    셋 중 다른 하나가 깨진다.
    """
    module = load_runner()
    text = " db_password: |\n-  Tr0ub4dor3xyz\n+  correct-horse-battery"
    assert "Tr0ub4dor3xyz" in module.verify_excerpt(text)
