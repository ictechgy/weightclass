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
    assert lines[0] not in cleaned


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
    assert lines[0] not in cleaned


def test_credential_straddling_the_scan_window_is_redacted() -> None:
    """창 경계에 걸린 자격증명이 앵커를 잃고 남으면 안 된다."""
    module = load_runner()
    lines = pem_body(1400)
    padding = "y" * (module.SCAN_WINDOW_CHARS + 5000)
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
        "마커 반복": (BEGIN + KEY) * 5_000,
        "언급 반복": (f"{BEGIN}{KEY} nope\n") * 5_000,
        "이스케이프 줄바꿈 대량": ("\\n" + "A" * 64) * 3_000,
    }
    for name, text in hostile.items():
        started = time.monotonic()
        module.verify_excerpt(text)
        elapsed = time.monotonic() - started
        assert elapsed < 3.0, f"{name}: {elapsed:.2f}s"


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
    assert lines[0] not in module.verify_excerpt(text)


def test_schemeless_proxy_with_short_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """스킴 없는 형태도 curl, wget, pip 가 받아들인다. 짧은 비밀번호도 비밀이다."""
    module = load_runner()
    monkeypatch.setenv("HTTP_PROXY", "user:aB3x5@proxy.corp:3128")
    cleaned = module.verify_excerpt("proxy error at user:aB3x5@proxy.corp:3128")
    assert "aB3x5" not in cleaned
    assert "proxy.corp:3128" in cleaned
