#!/usr/bin/env python3
"""Read `p` and the realised saving off a speculative-run log.

`p` is the share of tasks where the cheap route failed verification. It is the
only number that decides whether the built-in version of this mode is worth
building, because expected cost is `c + p` and break-even is `p = 1 - c`.

The saving is reported two ways on purpose. The **modelled** saving uses the
externally measured cost ratio and the observed `p`, which is what the decision
turns on. The **token** columns are descriptive only: they are per-vendor counts
that must not be divided across vendors, and with a cheap-model strategy they
barely move anyway — the lever is price per token, not token count.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from pathlib import Path
from typing import NamedTuple


def _safe(text: str, limit: int = 200) -> str:
    """Strip control characters before printing anything read from the log.

    The log is an ordinary file that a compromised or crafted run could have
    written. Echoing it verbatim lets ANSI escapes rewrite the terminal, which
    is a poor way to learn that a measurement went wrong.
    """
    cleaned = "".join(character for character in text if character.isprintable())
    return cleaned[:limit] + ("…" if len(cleaned) > limit else "")


# 지문에 넣을 **설정** 키. 블랙리스트(제외 목록)로 두면 레코드에 과제별
# 값이 하나만 추가돼도 모든 지문이 달라져, 단일 설정 로그가 "혼합" 으로
# 잡힌다. 화이트리스트가 그 사고를 막는다.
ADVISOR_CONFIG_KEYS = frozenset({"route", "advise_first", "advise_on_failure", "context"})
# s 를 판정하려면 이만큼의 조언받은 실패가 있어야 한다. 세 건짜리 표본으로도
# Wilson 하한이 손익분기를 넘을 수 있고, 그것을 "유리하다" 로 찍으면 안 된다.
MINIMUM_ADVISED_FAILURES = 12


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def escalated_of(record: dict[str, object]) -> bool:
    """이 과제가 승급했는가.

    p 를 세는 쪽, 비용을 모으는 쪽, 요약을 찍는 쪽이 모두 이 하나를 쓴다.
    각자 인라인으로 판정하면 러너가 빈 dict 나 오류 스텁을 남겼을 때 숫자가
    서로 어긋나고, 어느 쪽이 맞는지 읽는 사람이 알 수 없다.
    """
    return isinstance(record.get("expensive"), dict)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument(
        "--cost-ratio",
        type=float,
        default=None,
        help=(
            "assumed cheap/expensive cost ratio, used only when this log cannot measure "
            "one (default 0.31, from the 90-pair model-grade study)"
        ),
    )
    arguments = parser.parse_args()

    # 기본값을 쓴 사람에게 "네가 준 값과 다르다" 고 경고하면 무슨 말인지 알 수
    # 없다. 명시했는지 여부를 구분한다.
    given_cost_ratio = arguments.cost_ratio is not None
    if arguments.cost_ratio is None:
        arguments.cost_ratio = 0.31

    # c 는 비용비이므로 (0, 1) 안에 있어야 한다. 범위를 벗어나면 손익분기
    # 1-c 가 뒤집히고 절감이 음수나 100% 초과로 나와 조용히 헛소리를 한다.
    if not 0 < arguments.cost_ratio < 1:
        parser.error(f"--cost-ratio must be between 0 and 1, exclusive: {arguments.cost_ratio}")

    # 한 줄이 손상됐다고 이미 수집한 측정 전체를 버릴 이유는 없다. 러너는
    # append 만 하고 잠금이 없어, 크래시나 동시 실행이 잘린 줄을 남길 수 있다.
    log_path = arguments.log.expanduser()
    if not log_path.is_file():
        parser.error(f"no such log: {log_path}")

    records = []
    damaged = 0
    # 바이트로 읽어 줄 단위로 디코딩한다. 파일 전체를 엄격하게 디코딩하면
    # 잘린 멀티바이트 한 곳 때문에 손상 줄을 견딘다는 약속이 무의미해진다.
    for raw in log_path.read_bytes().splitlines():
        line = raw.decode("utf-8", "replace")
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            # 손상된 줄만 견디고 필드 누락에는 죽는 것은 일관성이 없다. 이
            # 리포트가 읽는 모양을 갖췄는지 여기서 한 번에 본다.
            record["cheap"]["accepted"]
            # 키가 아예 없는 줄은 .get() 으로는 통과하지만 아래에서
            # r["expensive"] 로 역참조된다. 존재 자체를 확인한다.
            if record["expensive"] is not None:
                record["expensive"]["accepted"]
            records.append(record)
        except (ValueError, KeyError, TypeError):
            damaged += 1
    if damaged:
        print(f"경고: 손상된 줄 {damaged}개를 건너뛴다")
    if not records:
        print("기록 없음")
        return 1

    # 인프라 실패(클론 불가, 복사 오류, 자식 기동 실패)는 싼 경로의 품질과
    # 무관하다. p 에 섞으면 도구 고장이 "싼 모델이 나쁘다" 로 둔갑한다.
    # attempt 는 그런 경우 error 를 남기되 "made no change" 만은 진짜 결과다.
    def reached_verify(record: dict[str, object]) -> bool:
        """싼 경로가 검증까지 갔는가. 조언이 닿을 수 있었던 실행인지의 술어다.

        러너의 `reached_verify` 와 같은 판정이어야 한다. 한쪽만 고치면
        조언의 분자와 분모가 다른 집합을 세게 된다.
        """
        cheap = record.get("cheap")
        if not isinstance(cheap, dict):
            return False
        return bool(cheap.get("verify")) and cheap.get("failure_kind") != "infrastructure"

    def is_infrastructure_failure(record: dict[str, object]) -> bool:
        # p 는 **싼 경로** 의 실패율이므로 싼 경로만 본다. 승급이 도구 고장
        # 으로 죽었다고 해서 그 앞의 싼 경로 실패까지 버리면, 관측된 진짜
        # 실패가 사라져 p 가 실제보다 낮게 나온다. 승급 쪽 고장은 아래
        # "둘 다 실패" 집계에서만 제외한다.
        cheap = record["cheap"]
        if not isinstance(cheap, dict):
            # 여기 오면 안 되는 모양이다. 모양 검사를 통과한 기록만 오지만,
            # assert 로 좁히면 python -O 에서 사라지므로 실제로 확인한다.
            # 인프라 실패로 분류해 p 에서 빼는 것이 안전한 쪽이다.
            return True
        # 러너가 failure_kind 로 알려준다. 예전에는 에러 문자열을 부분
        # 일치로 추측했는데, 러너에서 문구를 다듬으면 조용히 분류가 틀어졌다.
        kind = cheap.get("failure_kind")
        if kind is not None:
            return bool(kind == "infrastructure")
        # failure_kind 이전에 수집된 기록과의 호환.
        error = cheap.get("error")
        return bool(error) and not any(
            marker in str(error) for marker in ("made no change", "modified the patched files")
        )

    # 인덱스가 아니라 술어로 가른다. `r not in broken` 은 dict 값 동등성이라,
    # 토큰과 시간이 모두 None 인 인프라 실패 두 건이 함께 걸려 나가고 비용도
    # O(n*m) 이다.
    broken = [r for r in records if is_infrastructure_failure(r)]
    usable = [r for r in records if not is_infrastructure_failure(r)]
    if broken:
        print(f"경고: 인프라 실패 {len(broken)}건은 p 계산에서 제외한다")
        for r in broken[:3]:
            # 로그는 신뢰 대상이 아니다. 제어 문자를 걷어내고 길이를 자른다.
            cheap = r["cheap"]
            detail = cheap.get("error") if isinstance(cheap, dict) else "malformed record"
            print(f"  - {_safe(str(detail))}")

    total = len(usable)
    if not total:
        print("p 를 계산할 수 있는 기록이 없다")
        return 1
    cheap_passed = sum(1 for r in usable if r["cheap"]["accepted"])
    failed = total - cheap_passed

    def both_routes_failed(record: dict[str, object]) -> bool:
        cheap = record["cheap"]
        if not isinstance(cheap, dict) or cheap["accepted"]:
            return False
        expensive = record["expensive"]
        if not isinstance(expensive, dict):
            # 승급이 아예 기록되지 않았다. 둘 다 실패했다고 셀 근거가 없다.
            return False
        # 승급이 도구 고장으로 죽은 것은 비싼 경로에 대한 판정이 아니다.
        # failure_kind 이전 기록과의 호환을 is_infrastructure_failure 와
        # 같은 방식으로 맞춘다.
        kind = expensive.get("failure_kind")
        if kind == "infrastructure":
            return False
        if kind is None and expensive.get("error"):
            error = str(expensive["error"])
            if not any(
                marker in error for marker in ("made no change", "modified the patched files")
            ):
                return False
        return not expensive["accepted"]

    both_failed = sum(1 for r in usable if both_routes_failed(r))

    p = failed / total
    lo, hi = wilson(failed, total)
    c = arguments.cost_ratio

    # 어떤 라우트로 잰 p 인지 보여 준다. 러너가 기록하는데 리포트가 감추면
    # 서로 다른 라우트의 실행이 한 로그에 섞여도 알 길이 없다.
    identities = {
        json.dumps(r.get("routes", {}), sort_keys=True, ensure_ascii=False) for r in usable
    }
    if len(identities) > 1:
        print(f"경고: 서로 다른 라우트 조합 {len(identities)}종이 한 로그에 섞여 있다")
    elif identities:
        routes = json.loads(identities.pop())
        if routes:
            cheap_id = routes.get("cheap", {})
            expensive_id = routes.get("expensive", {})
            print(
                f"라우트: 싼 쪽 {cheap_id.get('executable')}@{cheap_id.get('argv_digest')}"
                f" / 승급 {expensive_id.get('executable')}@{expensive_id.get('argv_digest')}"
            )

    suspect = [r for r in usable if r["cheap"].get("child_failed_without_changes")]
    if suspect:
        print(
            f"주의: 벤더 CLI 가 0 이 아닌 코드로 끝나고 변경도 없던 실행 {len(suspect)}건."
            " 라우트 실패인지 인증/쿼터/네트워크 장애인지 구별되지 않는다."
        )

    print(f"과제 {total}개 (기록 {len(records)}개)")
    print(f"  싼 경로 검증 통과: {cheap_passed}")
    print(f"  싼 경로 실패     : {failed}")
    # 조언 후 재시도가 구제한 건은 실패했지만 승급하지 않았다. 둘을 한 줄로
    # 묶으면 "승급 필요" 가 실제 승급 건수와 어긋난다.
    rescued_total = sum(
        1 for r in usable if isinstance(r.get("retry"), dict) and (r["retry"] or {}).get("accepted")
    )
    if rescued_total:
        print(f"    그중 조언 후 재시도로 구제: {rescued_total}")
    # 승급 여부는 비용 루프와 **같은** 술어를 써야 한다. 여기만 다른 것을
    # 쓰면 두 숫자가 어긋나고, 어느 쪽이 맞는지 읽는 사람이 알 수 없다.
    print(f"  실제 승급        : {sum(1 for r in usable if escalated_of(r))}")
    print(f"  둘 다 실패       : {both_failed}")
    # 일부 기록만 시작 전 조언이면 그 비율은 p 도 p′ 도 아니다. 두 설정을
    # 섞어 놓고 한쪽 이름을 붙이면, 조언 없는 실행이 섞인 수를 단일 설정의
    # p′ 처럼 보이게 한다.
    # **실제로 계획이 붙었는지** 를 본다. 설정 플래그를 보면, 조언이 비어
    # 계획이 안 붙은 실행도 "계획을 받은 뒤의 실패율" 로 라벨링된다. 실제
    # 쪽을 보면 그런 로그는 두 값이 섞여 아래 혼합 가드가 라벨을 거부한다.
    first_flags = {
        bool(
            isinstance(r.get("advisor"), dict) and (r["advisor"] or {}).get("advise_first_applied")
        )
        for r in usable
    }
    # p′ 라벨의 조건은 **전칭** 이다 — 모든 실행에 계획이 붙어야 한다.
    # plan_applied_on 은 존재("하나라도 붙었는가")이므로 둘은 다른 술어다.
    # 한쪽만 고치면 한 보고서 안에서 같은 프라임이 서로 다른 뜻이 된다.
    advice_first_on = first_flags == {True}
    # 조언 **설정** 이 섞이면 라벨도 못 붙인다. 앞선 판은 s 만 막고 p′ 와
    # c_A 는 계속 찍었는데, route 나 context 가 다른 실행을 한 수로 뭉갠
    # 값에 단일 설정의 이름을 붙이는 셈이다.
    advisor_fingerprints = {
        json.dumps(
            {
                key: value
                for key, value in (r.get("advisor") or {}).items()
                if key in ADVISOR_CONFIG_KEYS
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        # **advisor 블록이 없는 레코드도 지문을 낸다.** 빼면 조언을 끄고 돈
        # 실행이 "설정 없음" 으로 사라져, 조언 로그와 비조언 로그가 섞인
        # 것을 단일 설정으로 본다.
        for r in usable
    }
    # 설정 순수성은 계획 적용과 **다른 질문** 이다. 한 변수에 합치면
    # "계획이 붙었는가" 의 답을 잃는다. 따로 둔다.
    # 조언을 켜고 잰 로그인가. 비용비의 이름을 정하는 데도 쓰므로 지문·라벨과
    # **같은 자리** 에서 정한다 — 떨어져 있으면 한쪽만 고쳐진다.
    advisor_on = any(
        isinstance(r.get("advisor"), dict)
        and (
            (r["advisor"] or {}).get("advise_first")
            or (r["advisor"] or {}).get("advise_on_failure")
        )
        for r in usable
    )
    single_advisor_config = len(advisor_fingerprints) <= 1
    # **프라임 라벨의 조건은 하나다.** 네 자리(p′, c_A, s′, a_B′)가 각각
    # 다른 조건을 쓰고 있었고, 그래서 한 보고서 안에서 같은 프라임이 서로
    # 다른 뜻이 됐다. 조건은 셋을 모두 만족해야 한다.
    #
    #   1. 모든 실행에 계획이 실제로 붙었다 (전칭 — 하나라도 아니면 섞인 수다)
    #   2. 조언 설정이 하나다 (route 나 context 가 다르면 다른 arm 이다)
    shape_a_measured = advice_first_on and single_advisor_config
    # 조언 구간 안에서만 대입되던 값이다. 그 구간이 안 돌면 아래 사다리에서
    # NameError 가 난다 — 두 값이 언제나 정의돼 있게 둔다.
    mixed_application = len(first_flags) > 1
    if len(first_flags) > 1:
        print(
            "\n경고: 시작 전 조언을 켠 실행과 끄고 돈 실행이 한 로그에 섞여 있다."
            " 아래 실패율은 어느 설정의 값도 아니다."
        )
    # 시작 전 조언이 켜졌으면 이 실패율은 계획을 받은 뒤의 것이다. p 라고
    # 부르면 조언 없는 설정의 p 와 섞인다.
    # 혼합이면 어느 이름도 맞지 않는다. 이름을 붙이면 그 설정의 값처럼 읽힌다.
    rate_name = (
        "p′"
        if shape_a_measured
        else (
            "실패율(설정·적용 혼합)" if len(first_flags) > 1 or not single_advisor_config else "p"
        )
    )
    print(f"\n{rate_name} = {p:.1%}   95% CI [{lo:.1%}, {hi:.1%}]")
    if advice_first_on:
        print("  (시작 전 조언을 받은 뒤의 실패율이다. 조언 없는 p 와 같은 값이 아니다)")

    # 승급이 일어난 과제에서는 같은 과제를 양쪽 모델로 돌린 셈이다. 거기서
    # c 를 짝지어 실측할 수 있다 — 가정하거나 남의 벤치마크에서 빌려오는 것보다
    # 훨씬 낫다. 같은 과제이므로 과제 난이도 차이가 상쇄된다.
    def cost_of(attempt: object) -> float | None:
        if not isinstance(attempt, dict):
            return None
        child = attempt.get("child")
        if not isinstance(child, dict):
            return None
        if child.get("timed_out"):
            # 중간에 죽인 실행의 사용량은 부분값이라 표본에 넣으면 비용이
            # 낮게 잡힌다. 다만 빼는 것도 공짜가 아니다 — 타임아웃난 싼 실행은
            # 예산을 끝까지 태운, 가장 비싼 싼 실행이다. 넣어도 빼도 c 는
            # 낮아지는 쪽으로 치우친다. 부분값을 실측값으로 부르지 않는 쪽을
            # 택하고, 몇 건이 그렇게 빠졌는지 아래에서 따로 알린다.
            return None
        usage = child.get("usage")
        if not isinstance(usage, dict):
            return None
        value = usage.get("cost_usd")
        # bool 은 int 의 하위형이라 isinstance 를 그냥 통과한다. NaN 과 무한대,
        # 음수도 마찬가지로 걸러야 한다 — 손상되거나 조작된 로그가 그럴듯한
        # 절감률을 만들어 내는 것을 막는다.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        try:
            cost = float(value)
        except OverflowError:
            # 손상된 JSONL 의 임의 정밀도 정수는 float 변환에서 죽는다.
            # 리포트가 로그 한 줄 때문에 멈추면 안 된다.
            return None
        return cost if math.isfinite(cost) and cost >= 0 else None

    def cost_origin(attempt: object) -> str | None:
        """벤더가 알려준 청구액인가, 우리 요금표로 환산한 값인가.

        예전에는 source 문자열에 "claude-json" 이 들어 있는지로 판정했다.
        그러면 "claude-json+price-table"(요금표 환산)이 "claude-json"(벤더
        청구액)과 같은 것으로 분류되고, "codex-json" 과 "stderr-scrape" 는
        둘 다 벤더가 아닌 것으로 뭉뚱그려진다. 러너가 남기는 명시 필드를 쓴다.
        """
        if not isinstance(attempt, dict):
            return None
        child = attempt.get("child")
        if not isinstance(child, dict):
            return None
        usage = child.get("usage")
        if not isinstance(usage, dict):
            return None
        origin = usage.get("cost_origin")
        return origin if isinstance(origin, str) else None

    def has_missing_prices(attempt: object) -> bool:
        if not isinstance(attempt, dict):
            return False
        child = attempt.get("child")
        if not isinstance(child, dict):
            return False
        usage = child.get("usage")
        return isinstance(usage, dict) and bool(usage.get("priced_fields_missing"))

    # c 는 (싼 비용 평균) / (비싼 비용 평균) 이다. **두 평균이 같은 기준으로
    # 걸러진 모집단에서 나와야 한다.** 예전에는 분자가 "비용이 있으면 무엇이든"
    # 이고 분모는 출처 검증까지 통과한 것만이라, 검증에서 뺀 실행의 싼 비용이
    # 분자에는 남아 있었다. 그래서 과제 단위로 한 번에 판정한다.
    #
    # 한 과제는 다음 중 하나다:
    #   - 싼 비용을 신뢰할 수 없다        -> 어느 쪽에도 안 들어간다
    #   - 승급 안 함, 싼 비용 신뢰 가능    -> 분자에만
    #   - 승급함, 양쪽 다 신뢰 가능        -> 분자와 분모 둘 다
    #   - 승급함, 비싼 쪽을 신뢰할 수 없다 -> 분자에만, 분모에서 빠진 것을 센다
    class Task(NamedTuple):
        cheap: float
        # 비율의 분모로 쓸 수 있는 값(0 이 아님).
        expensive: float | None
        escalated: bool
        # 관측된 비싼 비용. 0 도 관측값이므로 평균에는 들어간다. 나눗셈에만
        # 못 쓴다 — 둘을 통째로 묶어 빼면 평균이 커져 a 와 r 이 작아지고,
        # 그것은 조언 쪽에 유리한 방향이다.
        expensive_observed: float | None

    # c 는 두 평균의 비다. **표본 전체가 한 출처여야 한다.** 쌍 단위로만
    # 확인하면 부족하다 — 어떤 과제는 벤더 청구액, 어떤 과제는 요금표
    # 환산값인 채로 분자 평균에 함께 들어가면, 그 평균은 무엇의 평균도
    # 아니다. 앞선 라운드는 쌍만 검사하고 분자는 그대로 뒀다.
    origins: set[str | None] = set()
    # **네 단계를 모두 본다.** 싼/비싼 경로만 검사하면 조언과 재시도가 다른
    # 회계에서 온 로그가 통과하고, 그 수로 s > a + q·r 을 판정하게 된다.
    # 판정에 쓰이는 비용은 전부 같은 출처여야 한다.
    for r in usable:
        for key in ("cheap", "expensive", "advice_first", "advice_failure", "retry"):
            # **`is not None` 이다.** truthiness 로 걸면 비용 0 인 단계가
            # 통째로 빠진다 — 구독 로그에서는 그것이 대부분이고, 같은 파일이
            # 다른 곳에서는 0 을 관측값으로 취급한다. 한 술어를 두 경로가
            # 다르게 판정하는 자리가 된다.
            if cost_of(r.get(key)) is not None:
                origins.add(cost_origin(r.get(key)))
    # 세 경우를 구별한다. 출처가 실제로 섞였는가, 러너가 cost_origin 을 남기지
    # 않은 옛 로그인가, 아니면 비용 자체가 하나도 없는가. 셋 다 c 를 못 재게
    # 하지만 사용자가 할 일이 다르다.
    single_origin = len(origins) == 1 and None not in origins
    if not origins:
        origin_problem = "이 로그에는 비용이 하나도 없다"
        origin_remedy = (
            "  Claude 는 --output-format json 이면 total_cost_usd 를 준다."
            " Codex 는 USD 를 주지 않으므로 --prices 로 요금표를 넘겨야 한다."
        )
    elif origins == {None}:
        origin_problem = "이 로그는 cost_origin 을 남기지 않는 옛 러너가 썼다"
        origin_remedy = (
            "  비용은 있지만 그것이 벤더 청구액인지 요금표 환산값인지 알 수 없어,"
            " 두 arm 을 나눠도 되는지 판단할 수 없다. 지금 러너로 다시 재야 한다."
        )
    else:
        named = sorted(str(o) for o in origins if o is not None)
        unknown = " (일부는 출처 미상)" if None in origins else ""
        origin_problem = f"비용의 출처가 한 가지가 아니다 — 관측된 출처 {named}{unknown}"
        origin_remedy = (
            "  벤더가 알려준 청구액과 요금표 환산값은 세는 항목이 다르다 — 전자는"
            " 캐시 읽기를 포함하고 후자는 표에 적은 필드만 센다. 섞인 값들의 평균은"
            " 무엇의 평균도 아니므로 c 를 재지 않는다. 두 벤더를 비교하는 중이라면"
            " 양쪽 요금표를 주고 --prefer-prices 로 한 기준에 세워라. 한 벤더 안에서"
            " 섞였다면 설정마다 --out-dir 을 나눠야 한다."
        )

    def timed_out(attempt: object) -> bool:
        if not isinstance(attempt, dict):
            return False
        child = attempt.get("child")
        return isinstance(child, dict) and bool(child.get("timed_out"))

    tasks: list[Task] = []
    escalated_total = 0
    unusable_cheap = 0
    # 타임아웃난 실행의 비용은 모르는 것이 아니라 **위쪽이 열려 있는** 것이다.
    # 예산을 끝까지 태웠으므로 그 한 건이 표본에서 가장 비쌀 가능성이 높고,
    # 그것을 빼고 낸 결론은 한 건으로 뒤집힐 수 있다. 따로 센다.
    timed_out_tasks = 0
    unpriced_escalations = 0
    zero_expensive = 0
    # a 와 r 의 분모. tasks 와 달리 싼 비용을 못 얻은 승급도 포함한다.
    all_expensive_costs: list[float] = []
    # 그 분모가 승급 전체를 대표하는지 보는 카운터. c 쪽 카운터
    # (unpriced_escalations, zero_expensive)는 싼 비용이 없는 레코드보다
    # **뒤** 에서 증가하므로 이 질문에 답하지 못한다.
    expensive_missing = 0
    for r in usable:
        # 승급 여부는 p 를 세는 쪽과 같은 술어로 판정한다. 러너가 빈 dict 나
        # 오류 스텁을 남기면 "승급 N건 중 M건" 문구가 실제와 어긋난다.
        escalated = escalated_of(r)
        if escalated:
            escalated_total += 1
        if timed_out(r.get("cheap")) or timed_out(r.get("expensive")):
            timed_out_tasks += 1
        # 비싼 비용 관측은 **싼 비용이 있는지와 무관하다.** 아래 continue 뒤에서
        # 모으면 a 와 r 의 분모가 "싼 비용도 얻은 승급" 이라는 부분집합이 되고,
        # 그 부분집합이 승급 전체를 대표한다는 근거는 어디에도 없다.
        if escalated:
            # **조언이 닿을 수 있었던 승급만 센다.** 검증까지 못 간 인프라
            # 실패는 러너가 조언을 건너뛰고 바로 승급하므로 Shape B 의 대상이
            # 아니다. 그 비용이 a 와 r 의 분모에 섞이면, B 와 무관한 실행의
            # 비용이 B 의 판정을 양방향으로 흔든다.
            # 분모도 **같은 술어** 로 거른다. 분자(a, r)는 부분 가격을
            # 빼는데 분모가 받으면, 분모가 실제보다 작아져 a 와 r 이 커진다.
            # 분모도 **분자와 같은 술어** 로 거른다. 분자(advice_cost,
            # retry_costs)는 타임아웃과 부분 가격을 빼는데 분모가 받으면,
            # 중간에 끊긴 비싼 시도의 부분값이 분모를 낮춰 a 와 r 을 키운다.
            standalone_expensive = (
                None
                if has_missing_prices(r.get("expensive")) or timed_out(r.get("expensive"))
                else cost_of(r.get("expensive"))
            )
            if not reached_verify(r):
                pass
            elif standalone_expensive is None:
                # **0 은 빼지 않는다.** c 의 분모가 이번에 0 을 포함하도록
                # 고쳐졌다. 같은 술어("승급 과제의 비싼 비용 평균")를 두 코드
                # 경로가 다른 모집단으로 판정하면 안 된다 — 0 을 빼면 평균이
                # 커져 a 와 r 이 작아지고 조언 쪽에 유리하게 틀린다.
                expensive_missing += 1
            else:
                all_expensive_costs.append(standalone_expensive)
        cheap_cost = cost_of(r.get("cheap")) if single_origin else None
        if not cheap_cost:
            # 0 도 여기서 뺀다. 싼 쪽 0 은 c 를 끌어내려 "거의 공짜" 라는
            # 결론을 만드는데, 실제로는 요금표가 비었거나 사용량을 못 읽은
            # 경우가 대부분이다. cost_of 는 타임아웃에도 None 을 준다.
            unusable_cheap += 1
            continue
        expensive_cost = cost_of(r.get("expensive")) if escalated else None
        observed_expensive = expensive_cost
        # 비싼 비용 0 은 관측값이지만 **분모로는 못 쓴다.** 이유를 나눠 센다 —
        # 진위값 하나로 뭉뚱그리면 아래 나눗셈 가드가 공허해지고, 나중에 그
        # 검사를 "0 도 관측값이다" 로 고치는 순간 ZeroDivisionError 가 된다.
        if escalated and expensive_cost == 0:
            zero_expensive += 1
            expensive_cost = None
        elif escalated and not expensive_cost:
            unpriced_escalations += 1
            expensive_cost = None
        tasks.append(Task(cheap_cost, expensive_cost, escalated, observed_expensive))

    cheap_all = [x.cheap for x in tasks]
    cheap_escalated = [x.cheap for x in tasks if x.escalated]
    cheap_passing = [x.cheap for x in tasks if not x.escalated]
    priced_pairs = [x for x in tasks if x.expensive is not None]
    # 평균의 모집단은 관측된 것 전부다. 0 을 빼면 평균이 커져 a 와 r 이
    # 작아지고 조언 쪽에 유리해진다.
    observed_expensive_costs = [
        x.expensive_observed for x in tasks if x.expensive_observed is not None
    ]
    paired = [x.cheap / x.expensive for x in priced_pairs if x.expensive]
    expensive_total = sum(x.expensive for x in priced_pairs if x.expensive)

    # 한 건으로 c 를 바꾸면 그 한 과제의 특성이 전체 결론을 정한다. 채택하는
    # 값은 비용 가중 비율이고 그것은 큰 과제 하나에 더 취약하므로, 중앙값보다
    # 오히려 최소 표본이 더 필요하다.
    MINIMUM_PAIRED = 3

    def is_multiturn(attempt: object) -> bool:
        # cost_of 와 같은 방식으로 단계별로 좁힌다. 재첨자로 다시 꺼내면
        # isinstance 로 좁힌 타입이 유지되지 않아 읽는 사람도 검사기도 헷갈린다.
        if not isinstance(attempt, dict):
            return False
        child = attempt.get("child")
        if not isinstance(child, dict):
            return False
        usage = child.get("usage")
        if not isinstance(usage, dict):
            return False
        # 부분 문자열 "turn" 은 "returned" 같은 값에도 걸린다. 러너가 붙이는
        # 표식만 본다 — 이 경고가 c 를 믿을지 말지의 유일한 근거다.
        return bool(re.search(r"\(\d+turn\)", str(usage.get("source", ""))))

    multiturn = sum(1 for r in usable for arm in ("cheap", "expensive") if is_multiturn(r.get(arm)))
    if multiturn:
        print(
            f"\n주의: 여러 턴을 돈 실행 {multiturn}건. turn.completed 가 턴별 증분인지"
            " 누적인지 확인되지 않아 그 실행의 토큰은 과대 집계일 수 있다."
        )

    # n 이 3~5 일 때 정규분위수 1.96 은 구간을 좁게 만든다. 자유도 n-1 의 t
    # 분위수를 쓴다. 표는 흔한 작은 n 만 담고 그 밖은 정규값으로 떨어진다 —
    # 표본이 커지면 둘이 수렴하므로 그때는 차이가 없다.
    T_QUANTILE_95 = {
        1: 12.71,
        2: 4.303,
        3: 3.182,
        4: 2.776,
        5: 2.571,
        6: 2.447,
        7: 2.365,
        8: 2.306,
        9: 2.262,
        10: 2.228,
        11: 2.201,
        12: 2.179,
        13: 2.160,
        14: 2.145,
        15: 2.131,
        16: 2.120,
        17: 2.110,
        18: 2.101,
        19: 2.093,
        20: 2.086,
        25: 2.060,
        30: 2.042,
        40: 2.021,
        60: 2.000,
        120: 1.980,
    }

    def t_quantile(df: int) -> float:
        """자유도 df 의 95% t 분위수. 표에 없으면 **바로 아래** 항목을 쓴다.

        t 는 df 가 커질수록 작아진다. 표에 없는 df 를 위쪽 항목으로 채우면 더
        작은 배수를 골라 구간이 실제보다 좁아진다 — df=21 이 25 의 2.060 을
        받는데 참값은 2.080 이다. 좁은 구간은 손익분기 판정을 뒤집을 수
        있으므로 언제나 보수적인 쪽, 즉 아래 항목을 쓴다.
        """
        best = 1.96
        for key in sorted(T_QUANTILE_95):
            if key <= df:
                best = T_QUANTILE_95[key]
            else:
                break
        return max(best, 1.96) if df >= 1 else T_QUANTILE_95[1]

    def estimate_c(sample: list[Task]) -> float | None:
        """c = (싼 비용 평균) / (비싼 비용 평균). 두 평균은 같은 표본에서 나온다.

        분자는 표본의 모든 과제, 분모는 그중 비싼 비용을 신뢰할 수 있는
        과제다. 분모의 대입 — 승급하지 않은 과제에서도 비싼 비용이 같았으리라 —
        이 남은 유일한 가정이다.
        """
        if not sample:
            return None
        # 분모 모집단은 **관측된 비싼 비용 전부** 다. 0 을 빼면 평균이 커져 c
        # 가 작아진다. 점추정과 이 함수가 서로 다른 모집단을 쓰면 구간이
        # 점추정과 다른 값을 중심으로 잡히므로, 두 곳은 같은 규칙이어야 한다.
        priced = [x.expensive_observed for x in sample if x.expensive_observed is not None]
        if not priced:
            return None
        mean_expensive = statistics.fmean(priced)
        if mean_expensive <= 0:
            return None
        return statistics.fmean([x.cheap for x in sample]) / mean_expensive

    def jackknife(sample: list[Task]) -> tuple[float, float] | None:
        """c 의 95% 구간. **과제** 를 하나씩 빼고 다시 계산한다.

        분모만 흔들면 분자의 변동을 통째로 버린다. 승급된 과제는 양쪽에
        기여하므로, 그 과제를 빼면 두 평균이 함께 움직인다 — 그 상관까지
        담으려면 삭제 단위가 과제여야 한다.

        빼고 계산한 값들의 **최소/최대**를 구간이라고 부르면 안 된다. 한 건을
        뺀 추정치는 원래 추정치에서 대략 1/n 만큼만 움직이므로 그 폭은 1/n 로
        줄어드는데, 참된 불확실성은 1/sqrt(n) 로 줄어든다. 둘 다 좁아지지만
        전자가 훨씬 빨라서, 표본이 커질수록 과소평가가 심해진다. jackknife
        표준오차 sqrt((n-1)/n * Σ(θ_i - θ̄)^2) 의 (n-1) 배가 그 차이를 메운다.
        """
        estimate = estimate_c(sample)
        if estimate is None or len(sample) < 3:
            return None
        leave_one_out = []
        for index in range(len(sample)):
            value = estimate_c(sample[:index] + sample[index + 1 :])
            if value is not None:
                leave_one_out.append(value)
        n = len(leave_one_out)
        if n < 2:
            return None
        mean = statistics.fmean(leave_one_out)
        variance = (n - 1) / n * sum((v - mean) ** 2 for v in leave_one_out)
        if variance <= 0:
            # 한 건씩 뺀 추정치가 전부 같으면 흔들림이 0 으로 나온다. 그것은
            # c 가 정확하다는 뜻이 아니라 이 표본이 그 질문에 답하지 못한다는
            # 뜻이다. 구간을 주지 않는다.
            return None
        # 자유도는 과제 수가 아니라 **실효 표본** 으로 정한다. 과제 20건에
        # 값 매겨진 승급이 3건이면 분모는 3건짜리다. 과제 수로 t 를 고르면
        # df=19 의 2.093 이 나오지만, 실제로 흔들리는 표본은 3 이므로 df=2 의
        # 4.303 이어야 한다.
        effective = min(n, sum(1 for x in sample if x.expensive))
        margin = t_quantile(max(1, effective - 1)) * math.sqrt(variance)
        # 비용비는 음수가 될 수 없다. 위쪽은 자르지 않는다 — 싼 쪽이 더 비쌀
        # 수 있고, 그 사실이 결론이어야 한다.
        return (max(0.0, estimate - margin), estimate + margin)

    c_range: tuple[float, float] | None = None
    measured_c: float | None = None
    if len(paired) >= MINIMUM_PAIRED:
        # 기대 비용 식 c + p 는 c 를 "전체 과제의 싼 비용 / 전체 과제의 비싼
        # 비용" 으로 본다. 승급 과제만 쓴 짝지은 비율은 그 값이 아니다 — 승급
        # 과제는 싼 경로가 실패한 부분집합이라 싼 비용이 체계적으로 다르다.
        #
        # 분자는 전수로 알 수 있다. 싼 경로는 모든 과제에서 돌았다. 분모는
        # 알 수 없다 — 비싼 경로는 승급 과제에서만 돌았으므로 그 평균으로
        # 메꾼다. 그 대입만이 남는 가정이고, 아래에서 그렇게 밝힌다.
        expensive_mean = statistics.fmean(observed_expensive_costs)
        point = estimate_c(tasks)
        if point is None:
            # 여기 올 수 없어야 하지만, 올 수 있게 되면 조용히 죽는 것보다
            # 말하고 멈추는 편이 낫다.
            print("\n비용비 c 를 내지 못했다: 승급 과제의 비싼 비용 평균이 0 이다.")
            return 0
        # 점추정과 구간이 같은 함수를 통과한다. 규칙을 두 곳에 적으면 한쪽만
        # 고쳐지고, 그러면 구간이 점추정과 다른 값을 중심으로 잡힌다.
        measured = point
        median_ratio = statistics.median(paired)
        # 세 경우다. 계획이 전부 붙고 설정이 하나면 c_A, 조언을 아예 안 켰으면
        # c, 그 사이(적용이 섞였거나 설정이 섞였거나)는 **어느 이름도 아니다** —
        # 두 모집단의 평균에 단일 모양의 이름을 붙이면 그 수가 그대로 인용된다.
        # 계획이 붙은 실행이 **하나도 없으면** 이 로그의 싼 실행은 계획 없이
        # 돈 것이므로 그 비용비는 c 다. 실패 후 조언만 켠 로그(B 전용)가
        # 그렇다 — 조언이 검증 뒤에 오므로 싼 실행의 비용을 바꾸지 않는다.
        # 앞선 판은 `advisor_on` 만 보고 그런 로그를 "혼합" 으로 몰았다.
        if shape_a_measured:
            cost_symbol = "c_A"
        elif True not in first_flags:
            cost_symbol = "c"
        else:
            cost_symbol = "비용비(모집단 혼합 — c 도 c_A 도 아니다)"
        print(
            f"\n실측 비용비 {cost_symbol} = {measured:.3f}"
            f"  (싼 경로 과제당 평균 {statistics.fmean(cheap_all):.4f} — {len(cheap_all)}건 —"
            f" 을 승급 {len(paired)}건의 비싼 경로 평균 {expensive_mean:.4f} 로 나눈 값)"
        )
        print(
            "  분모는 대입값이다. 비싼 경로는 승급된 과제에서만 돌았으므로, 승급하지"
            " 않은 과제에서 그것이 얼마였을지는 잰 적이 없다."
        )

        paired_ratio = sum(x.cheap for x in priced_pairs) / expensive_total
        print(
            f"  참고: 승급 과제만 짝지은 비율은 {paired_ratio:.3f} 다. 이 값은 과제"
            " 난이도가 상쇄된다는 장점이 있지만 c + p 식이 요구하는 값은 아니다."
        )
        if zero_expensive:
            print(
                f"  승급 {zero_expensive}건은 비싼 비용이 0 이라 **과제별 비율**"
                " 에서 뺐다. 0 으로는 나눌 수 없기 때문이다. 평균에는 그대로"
                " 들어간다 — 빼면 평균이 커져 a 와 r 이 작아지고, 그것은 조언"
                " 쪽에 유리한 방향이다."
            )
        if unpriced_escalations:
            # 방향을 말할 수 있는 것은 타임아웃뿐이다. 그 실행은 예산을 끝까지
            # 태웠으므로 평균보다 비쌌을 가능성이 높다. 비용이 없거나 0 인
            # 경우는 그렇게 말할 근거가 없으므로 방향을 단정하지 않는다.
            print(
                f"  승급 {unpriced_escalations}건은 비싼 비용을 얻지 못해 분모에서"
                " 빠졌다. 빠진 값이 평균보다 컸는지 작았는지 알 수 없으므로 c 가"
                " 어느 쪽으로 치우쳤는지도 알 수 없다."
            )
        # 요금표가 값을 매기기로 한 필드 중 일부가 그 실행에 없었다면, 없는
        # 캐시 필드일 수도 있지만 CLI 가 필드 이름을 바꾼 것일 수도 있다.
        # 후자면 절반짜리 비용이 그대로 c 에 들어간다.
        # 새 추정량에서는 비승급 실행의 싼 비용도 분자로 들어간다. 승급만
        # 세면 분자의 절반짜리 비용을 놓친다. arm 별이 아니라 과제 단위로
        # 세는 것은 양쪽이 다 부분 계산된 과제가 2 로 잡히지 않게 하기 위해서다.
        partial_priced = sum(
            1
            for r in usable
            if any(has_missing_prices(r.get(arm)) for arm in ("cheap", "expensive"))
        )
        if partial_priced:
            print(
                f"  주의: {partial_priced}건은 요금표가 값을 매기기로 한 필드 일부가"
                " 없는 채로 계산됐다. 진짜 0 일 수도, CLI 가 필드 이름을 바꾼 것일 수도"
                " 있다 — 후자면 비용이 절반만 잡힌다."
            )
        # 중앙값은 승급 과제별 비율의 중앙값이고 paired_ratio 는 같은 과제들의
        # 비용 가중 비율이다. 둘은 같은 추정량의 두 요약이므로 비교가 성립한다.
        # 예전에는 이 중앙값을 새 c 와 비교했는데, 그 둘은 애초에 다른 값을
        # 재는 것이라 차이가 나도 "큰 과제가 지배한다" 는 뜻이 아니었다.
        print(f"  승급 과제별 비율의 중앙값: {median_ratio:.3f}")
        if abs(median_ratio - paired_ratio) > 0.1:
            print(
                "    가중 비율과 크게 다르다 — 비용이 큰 승급 과제 몇 건이 합계를"
                " 지배한다는 뜻이다. 과제를 더 모아야 안정된다."
            )
        c_range = jackknife(tasks)
        if c_range is not None:
            print(
                f"  c 의 95% 구간 [{c_range[0]:.3f}, {c_range[1]:.3f}]"
                f" (jackknife, 과제 {len(tasks)}건 delete-one)"
            )
        # 승급 부분집합 편향. 싼 경로는 모든 과제에서 돌았으므로 전수와 비교할
        # 수 있다. 싼 모델이 일찍 포기해서 실패하는 흔한 양상이면 승급 과제의
        # 싼 비용이 체계적으로 낮고, 그러면 c 가 과소 추정된다.
        # 승급 과제를 **전체** 와 비교하면 전체가 승급 과제를 포함하므로 차이가
        # (1-p) 만큼 희석된다. 승급하지 않은 과제와 직접 비교해야 편향의 크기가
        # 그대로 보인다.
        if len(cheap_escalated) >= 2 and len(cheap_passing) >= 2:
            passing_mean = statistics.fmean(cheap_passing)
            escalated_mean = statistics.fmean(cheap_escalated)
            print(
                f"  싼 경로 과제당 평균 비용: 승급 안 한 과제 {passing_mean:.4f} /"
                f" 승급된 과제 {escalated_mean:.4f}"
            )
            if passing_mean > 0 and abs(escalated_mean - passing_mean) / passing_mean > 0.2:
                low = escalated_mean < passing_mean
                print(
                    f"    승급 과제의 싼 비용이 그렇지 않은 과제보다"
                    f" {'낮다' if low else '높다'}. c 의 분자는 두 무리를 다 담으므로"
                    " 이것이 c 를 직접 틀리게 하지는 않지만, 두 무리의 과제 성격이"
                    " 다르다는 뜻이므로 분모의 대입이 그만큼 위태롭다."
                )
        if unusable_cheap:
            print(
                "  분자가 승급 여부로 걸러지지는 않지만 전수도 아니다. 위에 적은"
                " 누락과, 승급 과제가 더 길거나 어려웠을 가능성이 함께 남는다."
            )
        else:
            print(
                "  분자는 전수라 부분집합 편향이 없다. 남은 위험은 분모뿐이다 — 승급"
                " 과제가 더 길거나 어려웠다면 비싼 평균이 전체를 대표하지 않는다."
            )
        if measured >= 1:
            print(
                "  주의: 싼 경로가 승급 경로보다 비쌌다(c >= 1). 손익분기가 음수가"
                " 되므로 아래 절감 수치는 의미가 없다. 라우트 지정이나 요금표를"
                " 먼저 확인해야 한다."
            )
        if given_cost_ratio and abs(measured - c) > 0.05:
            print(f"  주의: --cost-ratio 로 준 {c:.2f} 와 다르다. 아래 계산은 실측값을 쓴다.")
        measured_c = measured
        c = measured
    elif paired:
        print(
            f"\n비용비 c = {c:.2f} — **가정값**이다. 양쪽 비용을 얻은 승급 과제가"
            f" {len(paired)}개뿐이라 실측값을 쓰기에 부족하다(최소 {MINIMUM_PAIRED}개)."
        )
    elif not single_origin:
        print(f"\n비용비 c = {c:.2f} — **가정값**이다. {origin_problem}.")
        print(origin_remedy)
    else:
        print(f"\n비용비 c = {c:.2f} — **가정값**이다. 승급 과제에서 양쪽 비용을 모두 얻지 못했다.")
        print(
            "  Claude 는 --output-format json 이면 total_cost_usd 를 준다."
            " Codex 는 USD 를 주지 않으므로 --prices 로 요금표를 넘겨야 한다."
        )

    # 출처가 섞여 통째로 뺀 경우는 위에서 이미 그렇게 말했다. 여기서 다시
    # "타임아웃이거나 사용량이 없음" 이라고 하면 원인을 잘못 짚게 된다.
    if unusable_cheap and single_origin:
        # 분자를 "전수" 라고 부르면 안 되는 이유. 빠진 과제는 무작위가 아니다 —
        # 타임아웃은 예산을 끝까지 태운 가장 비싼 싼 실행이고, 그런 실행일수록
        # 승급으로 이어진다. 표본이 적어 c 를 못 잰 로그에서도 알려야 한다.
        print(
            f"  과제 {unusable_cheap}건은 싼 비용을 읽지 못해(타임아웃이거나 사용량이"
            " 없음) 분자에서 빠졌다. 그런 실행은 대개 가장 비싼 싼"
            " 실행이므로, 빠지면 c 가 낮아지고 절감이 부풀려진다."
        )

    # ── 조언 패턴 ────────────────────────────────────────────────────────
    # 설정이 섞이면 s 도 p 도 무엇의 값인지 알 수 없다. 라우트 지문과 같은
    # 방식으로 먼저 확인한다.
    # 지문은 **설정 키만** 으로 만든다. 레코드별 결과값(advise_first_applied)을
    # 같이 넣으면 계획이 붙은 실행과 안 붙은 실행이 "설정 두 종" 으로 보여
    # 엉뚱한 이유를 대며 s 를 거부한다. 두 질문은 따로 물어야 한다.
    advisor_configs = {
        json.dumps(
            {
                key: value
                for key, value in (r.get("advisor") or {}).items()
                if key in ADVISOR_CONFIG_KEYS
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        for r in usable
    }
    advised = [r for r in usable if isinstance(r.get("advisor"), dict)]
    if advised and len(advisor_configs) > 1:
        print(
            f"\n경고: 조언 설정 {len(advisor_configs)}종이 한 로그에 섞여 있다. s 를 내지 않는다."
        )
    elif advised:
        config = json.loads(advisor_configs.pop())
        # **계획이 실제로 붙었는지** 가 설정과 갈리면 프라임 라벨을 못 쓴다.
        # 계획을 받은 실행과 못 받은 실행이 한 수에 섞이기 때문이다.
        if mixed_application:
            print(
                "\n경고: 같은 설정인데 계획이 붙은 실행과 안 붙은 실행이 섞여 있다."
                " 계획을 받은 실패와 못 받은 실패가 한 수에 들어가므로 s 도 q 도"
                " 어느 설정의 값인지 말할 수 없다. **판정을 내지 않는다.**"
            )
        primed = shape_a_measured
        shapes = []
        if config.get("advise_first"):
            shapes.append("시작 전(A)")
        if config.get("advise_on_failure"):
            shapes.append("실패 후(B)")
        if shapes:
            print(f"\n조언: {' + '.join(shapes)}, 조언자가 보는 것 = {config.get('context')}")

            def advice_cost(record: dict[str, object], key: str) -> float | None:
                advice = record.get(key)
                if not isinstance(advice, dict):
                    return None
                # 중간에 죽은 호출의 사용량은 부분값이다. run_child 가 그것을
                # 건져 두므로 값이 있는 것처럼 보이지만, 그것으로 "전부 가격이
                # 있다" 를 만족시키면 판정이 부분값 위에 선다.
                if timed_out(advice):
                    return None
                # **부분 가격도 뺀다.** 싼 경로와 비싼 경로는 이미
                # has_missing_prices 로 거르는데 여기만 안 걸러, 벤더가
                # 일부 필드를 못 매긴 값이 완전한 비용처럼 a 에 들어갔다.
                # 그러면 a 가 작아져 조언에 유리하게 틀린다.
                if has_missing_prices(advice):
                    return None
                return cost_of(advice)

            # 시작 전 조언은 **모든** 과제에서, 실패 후 조언은 실패한 과제에서만
            # 일어난다. 두 모집단의 비용을 한 평균으로 섞으면 그 값은 어느
            # 쪽의 비용도 아니다. 단계별로 따로 낸다.
            def advice_stats(key: str) -> tuple[float, float, int] | None:
                # cost_of 는 0 을 관측값으로 받아들인다. 여기서 > 0 을
                # 요구하면 구독 실행처럼 0 을 보고하는 경우가 "가격 없음" 이
                # 되어, 전수 검사가 영원히 통과하지 못한다.
                values = [value for r in usable if (value := advice_cost(r, key)) is not None]
                # 리스트가 비지 않아도 **평균이 0** 일 수 있다(관측이 전부 0).
                # estimate_c 는 그 가드를 갖는데 같은 분모를 쓰는 여기와 r 쪽
                # 에는 없어서, 구독 로그처럼 비용이 0 인 실행에서 죽었다.
                if not values or not all_expensive_costs:
                    return None
                expensive_mean_all = statistics.fmean(all_expensive_costs)
                # 리스트가 비지 않아도 **평균이 0** 일 수 있다(관측이 전부 0).
                # estimate_c 는 그 가드를 갖는데 같은 분모를 쓰는 여기와 r 쪽
                # 에는 없어서, 비용이 0 인 로그에서 죽었다.
                if expensive_mean_all <= 0:
                    return None
                mean = statistics.fmean(values)
                # a 도 표본에서 온 값이다. 점추정만 쓰면 s > a + q·r 판정이
                # 실제보다 확정적으로 보인다.
                if len(values) > 1:
                    # 소표본에는 정규분위수가 아니라 t 를 쓴다. c 구간은
                    # 이미 그렇게 하는데 여기만 고정 1.96 이면, 관측 세 건짜리
                    # 분모에서 구간이 좁게 나와 조언에 유리하게 틀린다.
                    spread = (
                        t_quantile(len(values) - 1)
                        * statistics.stdev(values)
                        / math.sqrt(len(values))
                    )
                else:
                    # 관측이 하나면 흔들림을 0 으로 두면 안 된다. 그것은
                    # 한 번 봤다는 사실을 "정확히 안다" 로 바꾸는 것이다.
                    # 그 값 자체를 폭으로 삼아 판정이 서지 않게 한다.
                    spread = mean
                # 분모도 표본이다. c 의 구간이 그 불확실성을 이미 담고 있으므로
                # 여기서는 분자만 전파하되, 분모가 얇으면 그 사실을 알린다.
                return (mean / expensive_mean_all, spread / expensive_mean_all, len(values))

            def advice_attempts(key: str) -> int:
                return sum(1 for r in usable if isinstance(r.get(key), dict))

            for key, label in (("advice_first", "시작 전"), ("advice_failure", "실패 후")):
                attempts = advice_attempts(key)
                priced = sum(1 for r in usable if advice_cost(r, key) is not None)
                if attempts and priced < attempts:
                    print(
                        f"  주의: {label} 조언 {attempts}건 중 {priced}건만 비용을 얻었다."
                        " a 는 그 일부에서만 나온다."
                    )

            if mixed_application:
                # 아래 수들(a, q, r, s)은 계획을 받은 실패와 못 받은 실패를 한 수로
                # 뭉갠 값이다. 판정만 막고 수를 찍으면 그 수가 그대로 쓰인다 —
                # 경고는 읽히지 않고 숫자는 인용된다.
                #
                # 다만 **함수를 끝내지는 않는다.** 앞선 판은 여기서 return 했고,
                # 그러면 뒤따르는 타임아웃 판정 보류와 얇은 분모 경고가 통째로
                # 사라졌다. 조언 구간만 건너뛴다.
                print(
                    "  계획 적용이 섞여 있어 a, q, r, s 를 내지 않는다."
                    " 계획이 붙은 실행만 남기거나, 안 붙은 실행을 빼고 다시 모아야 한다."
                    " 위에 찍힌 비용비도 두 모집단이 섞인 값이므로 c 로도 c_A 로도"
                    " 쓸 수 없다."
                )
            else:
                a_first = advice_stats("advice_first")
                a_failure = advice_stats("advice_failure")
                # 두 조언 호출은 입력이 달라 비용도 다르다. 둘 다 `a` 로 찍으면
                # 읽는 쪽이 섞는다. 설계 문서의 기호를 그대로 쓴다 — 시작 전은
                # a_A, 실패 후는 a_B(계획을 받은 뒤면 a_B′).
                for symbol, label, stats in (
                    ("a_A", "시작 전", a_first),
                    (f"a_B{'′' if primed else ''}", "실패 후", a_failure),
                ):
                    if stats is None:
                        continue
                    ratio, spread, count = stats
                    band = f" ± {spread:.3f}" if spread else ""
                    print(f"  {label} 조언 1회 비용비 {symbol} = {ratio:.3f}{band}  ({count}회)")
                a_ratio = a_failure[0] if a_failure else None
                a_spread = a_failure[1] if a_failure else 0.0
                if a_ratio is None:
                    print("  실패 후 조언 비용을 얻지 못해 a 를 내지 못한다.")

                # s 는 실패한 실행에서만 나온다. 재시도가 기록된 건만 센다.
                # 분모는 "재시도가 기록된 건" 이 아니라 "조언을 받은 실패" 다.
                # 조언이 비어 재시도조차 못 한 건은 비용은 쓰고 승급했는데 분모에서
                # 빠져, s 가 위로 치우친다.
                advised_failures = [r for r in usable if isinstance(r.get("advice_failure"), dict)]
                if primed and config.get("advise_on_failure"):
                    print(
                        "  주의: 시작 전 조언과 실패 후 조언이 함께 켜져 있다. 여기 s′ 는"
                        " 계획을 받은 뒤의 재시도 성공률이므로, 실패 후 조언만 켠 설정의"
                        " s 와 같은 값이 아니다. 두 설정의 수를 섞어 쓰면 안 된다."
                    )
                if config.get("advise_on_failure"):
                    rescued = sum(
                        1
                        for r in advised_failures
                        if isinstance(r.get("retry"), dict) and (r["retry"] or {}).get("accepted")
                    )
                    no_retry = sum(
                        1 for r in advised_failures if not isinstance(r.get("retry"), dict)
                    )
                    if advised_failures:
                        s_lo, s_hi = wilson(rescued, len(advised_failures))
                        s_hat = rescued / len(advised_failures)
                        if no_retry:
                            print(
                                f"  조언 {no_retry}건은 비어 있거나 조언자가 실패해 재시도조차"
                                " 못 했다. 비용은 썼고 승급했으므로 분모에 남긴다."
                            )
                            print(
                                "    그 건들은 재시도 비용을 쓰지 않았으므로 손익분기에는"
                                " 재시도 시도율 q 를 곱한 q·r 을 쓴다."
                            )
                        print(
                            f"  조언 후 재시도 성공률 {'s′' if primed else 's'}"
                            f" = {s_hat:.1%}"
                            f"  95% CI [{s_lo:.1%}, {s_hi:.1%}]"
                            f"   ({rescued}/{len(advised_failures)})"
                        )
                        # 재시도는 최초 싼 실행과 같은 비용이 아니다. 과제에 조언이
                        # 붙어 프롬프트가 길고, 모델이 더 오래 돌 수도 있다. 이득
                        # 조건은 s > a + c 가 아니라 s > a + q·r 이다.
                        retry_costs = [
                            value
                            for r in usable
                            if isinstance(r.get("retry"), dict)
                            and not timed_out(r.get("retry"))
                            and not has_missing_prices(r.get("retry"))
                            and (value := cost_of(r.get("retry"))) is not None
                        ]
                        priced_retries = len(retry_costs)
                        all_retries = sum(1 for r in usable if isinstance(r.get("retry"), dict))
                        # 값이 빠진 비율이 크면 경고로 끝낼 일이 아니다. a 와 r 은
                        # 값이 있는 것만 보고 s 와 q 는 전부를 세므로, 두 수가 같은
                        # 모집단이 아니게 된다. 빠진 비용이 얼마였을지 모르므로
                        # 방향도 모른다 — 넓히는 것으로 메울 수 없다.
                        # 절반이면 충분하다는 기준은 너무 헐겁다. 값이 없는 재시도
                        # 하나가 임의로 비쌌을 수 있고, 그것이 결론을 뒤집는다.
                        # 돈에 대한 단정적 판정에는 전수를 요구한다.
                        priced_enough = priced_retries == all_retries
                        # a 와 r 의 분모는 승급 과제의 비싼 평균이다. 거기에
                        # 타임아웃의 부분 사용량이 섞이면 분모가 작아져 두 비율이
                        # 모두 커진다. 위쪽 c 판정은 그것 때문에 판정을 멈추는데,
                        # 조언 분기가 그 금지를 우회하고 있었다.
                        if timed_out_tasks:
                            priced_enough = False
                        # 타임아웃만 막으면 부족하다. 보통의 미가격 승급도 분모
                        # 에서 빠지므로, 남은 비싼 비용 평균이 승급 전체를
                        # 대표하지 않는다. a 와 r 이 모두 그 평균으로 나눈 값이다.
                        # a 와 r 의 분모는 all_expensive_costs 다. 그것이 승급
                        # 전체를 대표하는지는 **그 분모의 카운터** 로 본다. c 쪽
                        # 카운터를 빌려 쓰면, 싼 비용이 없어 c 의 루프에서 먼저
                        # 빠진 승급이 어느 카운터에도 안 잡혀 게이트가 눈이 먼다.
                        if expensive_missing:
                            priced_enough = False
                        # 조언 비용도 마찬가지다. a 가 작은 부분집합에서 나오면
                        # s 와 같은 모집단이 아니다.
                        advice_attempted = sum(
                            1 for r in usable if isinstance(r.get("advice_failure"), dict)
                        )
                        advice_priced = sum(
                            1 for r in usable if advice_cost(r, "advice_failure") is not None
                        )
                        if advice_priced != advice_attempted:
                            priced_enough = False
                        if all_retries and priced_retries < all_retries:
                            print(
                                f"  주의: 재시도 {all_retries}건 중 {priced_retries}건만 비용을"
                                " 얻었다. s 와 q 는 전부를 세는데 r 은 그 일부에서만 나오므로,"
                                " 두 수가 같은 모집단이 아니다."
                            )
                        r_ratio = None
                        r_spread = 0.0
                        retry_denominator = (
                            statistics.fmean(all_expensive_costs) if all_expensive_costs else 0.0
                        )
                        if retry_costs and retry_denominator > 0:
                            expensive_mean_all = statistics.fmean(all_expensive_costs)
                            r_mean = statistics.fmean(retry_costs)
                            r_ratio = r_mean / expensive_mean_all
                            r_spread = (
                                t_quantile(len(retry_costs) - 1)
                                * statistics.stdev(retry_costs)
                                / math.sqrt(len(retry_costs))
                                if len(retry_costs) > 1
                                else r_mean
                            ) / expensive_mean_all
                            print(
                                f"  재시도 1회 비용비 r{'′' if primed else ''}"
                                f" = {r_ratio:.3f}  ({len(retry_costs)}회)"
                                "  — 조언이 붙어 최초 싼 실행보다 비쌀 수 있다"
                            )
                        # **c_range 로 막지 않는다.** 이득 조건 s > a + q·r 에는
                        # c 가 나오지 않는다 — 분모의 흔들림은 아래에서 직접
                        # 낸다. c 를 요구하면 구독 로그처럼 비용이 0 이라 c 를
                        # 못 내는 경우에 Shape B 를 영영 판정할 수 없고, 그것이
                        # 이 도구가 겨냥한 바로 그 사례다.
                        if a_ratio is not None:
                            # 이득 조건은 s > a + q·r. 모든 항의 흔들림을 태운다.
                            # 조언이 비어 재시도조차 못 한 실패에는 r 이 들지 않는다.
                            # 모든 실패에 r 을 물리면 조언 경로가 실제보다 비싸
                            # 보인다. 재시도 시도율 q 를 곱한다.
                            attempted = len(advised_failures) - no_retry
                            q = attempted / len(advised_failures) if advised_failures else 1.0
                            # r 을 쟀으면 그것을 쓴다. 못 쟀으면 c 로 대신하되
                            # 그것이 대입이라고 말한다.
                            if r_ratio is not None and not priced_enough:
                                if timed_out_tasks or expensive_missing:
                                    print(
                                        f"  승급 {expensive_missing}건의 비싼"
                                        f" 비용이 없거나 0 이고 과제 {timed_out_tasks}건이"
                                        " 타임아웃이다. 비싼 경로 평균이 승급 전체를"
                                        " 대표하지 않으므로 판정하지 않는다."
                                    )
                                else:
                                    print(
                                        f"  조언 {advice_attempted}건 중 {advice_priced}건,"
                                        f" 재시도 {all_retries}건 중 {priced_retries}건만"
                                        " 비용이 있다. 값 없는 하나가 임의로 비쌌을 수"
                                        " 있으므로 판정하지 않는다."
                                    )
                            elif r_ratio is None:
                                # r 은 이 판정의 절반이다. 못 쟀는데 c 로 대신하고
                                # 판정까지 내면, 재지 않은 것을 잰 것처럼 보이게
                                # 된다. 조언이 붙은 재시도는 c 보다 비싸므로 그
                                # 대입은 언제나 조언 쪽에 유리하다.
                                print(
                                    "  재시도 비용을 얻지 못해 손익분기를 내지 않는다."
                                    " c 로 대신하면 조언 쪽에 유리한 방향으로만 틀린다."
                                )
                            else:
                                # q 도 표본이다. 시도율의 Wilson 구간을 r 에 태운다.
                                q_lo, q_hi = wilson(attempted, len(advised_failures))
                                bound_low = max(0.0, a_ratio - a_spread) + max(
                                    0.0, (r_ratio - r_spread) * q_lo
                                )
                                bound_high = a_ratio + a_spread + (r_ratio + r_spread) * q_hi
                                # a 와 r 은 같은 분모(승급 과제의 비싼 평균)로 나눈
                                # 값이고 그 분모도 표본이다. c 의 구간을 빌려 쓰면
                                # 안 된다 — 싼 비용과 비싼 비용이 같은 비율로
                                # 움직이면 c 의 구간은 좁은데 분모는 여전히
                                # 흔들린다. 분모의 표준오차를 직접 낸다.
                                expensive_values = all_expensive_costs
                                if len(expensive_values) <= 1:
                                    # 분모가 한 관측뿐이면 그 평균을 정확히 아는
                                    # 값처럼 쓰게 된다. lower_e <= 0 에서 판정을
                                    # 막는 것과 같은 이유로 여기서도 막는다.
                                    print(
                                        "  승급 과제의 비싼 비용 관측이 하나뿐이라 분모의"
                                        " 흔들림을 낼 수 없다. 판정하지 않는다."
                                    )
                                    bound_low, bound_high = 0.0, float("inf")
                                else:
                                    mean_e = statistics.fmean(expensive_values)
                                    se_e = statistics.stdev(expensive_values) / math.sqrt(
                                        len(expensive_values)
                                    )
                                    # 분모가 흔들리면 비율은 반대로 흔들린다.
                                    # 분모의 관측이 서넛일 때가 흔하다.
                                    critical = t_quantile(len(expensive_values) - 1)
                                    lower_e = mean_e - critical * se_e
                                    upper_e = mean_e + critical * se_e
                                    if lower_e <= 0:
                                        # 분모의 구간이 0 을 지나면 비율의 상한이
                                        # 없다. 상한을 지어내지 않고 판정을 막는다.
                                        print(
                                            "  승급 과제의 비싼 비용 평균이 0 을 배제하지"
                                            " 못한다. a 와 r 의 상한이 없으므로 판정하지"
                                            " 않는다."
                                        )
                                        bound_low, bound_high = 0.0, float("inf")
                                    else:
                                        # 비율 구간은 **끝점** 으로 낸다. 반폭을
                                        # 더하면 분자와 분모가 동시에 불리한 조합을
                                        # 덜 덮는다. a 와 r 은 mean_e 로 나눈 값이
                                        # 므로, 다른 분모를 가정하려면 그 비율을
                                        # 되돌려 곱한다.
                                        bound_low = bound_low * mean_e / upper_e
                                        bound_high = bound_high * mean_e / lower_e
                                # A+B 로그에서는 **네 기호가 모두** 프라임이다.
                                # 실패 단계 조언 비용, 시도율, 재시도 비용, 구제율이
                                # 전부 계획을 받은 뒤에 측정된 값이다. s′ 만 찍고
                                # 나머지를 안 찍으면 읽는 쪽이 B 단독의 수와 섞는다.
                                mark = "′" if primed else ""
                                basis = f"{q:.2f}·r{mark}" if q < 1 else f"r{mark}"
                                print(
                                    f"  손익분기 s{mark} = a_B{mark} + {basis}"
                                    f" = {a_ratio + r_ratio * q:.3f}"
                                    f"  (구간 [{bound_low:.3f}, {bound_high:.3f}])"
                                )
                                # 계획 적용이 섞인 경우는 이 블록에 오지
                                # 않는다 — 위쪽 `if mixed_application:` 이
                                # 조언 구간 전체를 건너뛴다. 여기서 그것을
                                # 다시 검사하면 언제나 거짓인 분기가 된다.
                                if len(advised_failures) < MINIMUM_ADVISED_FAILURES:
                                    print(
                                        f"  -> 판정 없음. 조언받은 실패가"
                                        f" {len(advised_failures)}건뿐이다"
                                        f" (최소 {MINIMUM_ADVISED_FAILURES}건)."
                                        " 이 표본으로는 구간이 넓어도 좁아도"
                                        " 근거가 되지 못한다."
                                    )
                                elif s_lo > bound_high:
                                    print(
                                        "  -> 구간 전체가 손익분기 위다."
                                        " 실패 후 조언이 승급보다 싸다."
                                    )
                                elif s_hi < bound_low:
                                    print(
                                        "  -> 구간 전체가 손익분기 아래다. 그냥 승급하는 편이 낫다."
                                    )
                                else:
                                    print(
                                        "  -> 구간이 손익분기를 가로지른다. 아직 결론 낼 수 없다."
                                    )
                        else:
                            print("  a 를 재지 못해 판정하지 않는다.")
                    else:
                        print("  아직 조언을 받은 실패가 없어 s 를 낼 수 없다.")

    # 조언이 켜져 있으면 c + p 는 이 실행의 비용 모형이 아니다. 그것을
    # "기대 비용" 이라 찍고 절감까지 내면, 조언 없는 설정의 숫자를 조언 있는
    # 로그의 결론으로 보여 주는 것이 된다.
    if advisor_on:
        print("\n기대 비용: 아래 c + p 는 **조언 없는** 경로의 모형이다.")
        print(
            "  이 로그는 조언을 켜고 잰 것이므로 실제 비용 모형은 다르다"
            " — 시작 전 조언은 a + c_A + p′, 실패 후 조언은 c + p·(a + q·r + 1 − s)."
            " c_A 와 r 은 각각 계획과 조언이 프롬프트에 붙은 실행의 비용비이고,"
            " 조언 없는 c 와 다르다. 여기 찍힌 c 는 이 로그에서 잰 싼 실행의"
            " 비용비이므로, 시작 전 조언을 켰다면 그것이 이미 c_A 다."
            " 두 설정을 나란히 재기 전에는 절감을 말하지 않는다."
        )
    if advisor_on:
        # 모형이 아니라고 말해 놓고 그 식의 절감률을 찍으면, 사용자는 그
        # 숫자를 가져간다. 조언이 켜진 로그에서는 아예 내지 않는다.
        if len(first_flags) > 1:
            label = "c + 실패율(설정 혼합)"
        else:
            # 프라임 표기의 기준을 **한 곳** 으로 맞춘다. 위쪽 a_B/s/r 은
            # 실제 적용 여부를 쓰는데 여기만 다른 술어를 쓰면 한 보고서 안에서
            # 같은 프라임이 서로 다른 뜻이 된다.
            label = "c + p′" if shape_a_measured else "c + p"
        print(
            f"\n(참고) 이 로그의 {label} = {c + p:.2f}. 실제 모형과의 차이는 조언"
            " 비용만이 아니다 — 재시도가 구제해 승급하지 않은 과제도 이 식에는"
            " 승급으로 들어 있어, 이 값은 실제보다 클 수도 작을 수도 있다."
        )
    else:
        print("\n기대 비용 = c + p")
        print(f"  기대 비용 {c + p:.2f}  ->  절감 {1 - (c + p):.1%}")
    # 이 식은 두 가지를 전제한다. 하나는 싼 비용비가 승급 과제와 그렇지 않은
    # 과제에서 같다는 것, 다른 하나는 비싼 경로의 과제당 비용이 양쪽에서 같다는
    # 것이다. 뒤의 것은 확인할 방법이 없다 — 비싼 경로는 승급된 과제에서만
    # 돌았다. 앞의 것은 싼 비용을 전수로 알기 때문에 확인할 수 있다.
    if c_range is not None and len(cheap_all) > len(cheap_escalated):
        # expensive_total 은 채택된 쌍의 비싼 비용만 담는다. 타임아웃이나
        # 출처 혼합으로 빠진 승급도 돈은 실제로 썼으므로, "실제로 쓴 돈" 이라고
        # 부르려면 몇 건이 빠졌는지 함께 말해야 한다.
        unpriced = escalated_total - len(paired)
        # **조언과 재시도도 실제로 쓴 돈이다.** 빼고 세면 절감의 부호까지
        # 뒤집힌다 — 조언을 켜고 잰 로그에서 그 비용이 어느 항에도 안 들어가
        # 조언 쪽에 유리하게 나온다.
        #
        # **지출은 통계와 다른 질문이다.** 통계는 "이 수를 판정에 쓸 수
        # 있는가" 를 묻고 부분 가격을 뺀다. 지출은 "얼마를 썼는가" 를 묻고,
        # 부분값이라도 안 쓴 것보다는 낫다 — 빼면 실제보다 적게 쓴 것처럼
        # 보이고 절감의 부호까지 뒤집힌다.
        advisory_spend = sum(
            value
            for r in usable
            for key in ("advice_first", "advice_failure", "retry")
            if (value := cost_of(r.get(key))) is not None
        )
        # 값을 아예 못 얻은 건만 센다. **경고와 제외가 같은 술어여야 한다** —
        # 앞선 판은 타임아웃으로 세고 부분 가격으로 뺐다.
        advisory_unpriced = sum(
            1
            for r in usable
            for key in ("advice_first", "advice_failure", "retry")
            if isinstance(r.get(key), dict) and cost_of(r.get(key)) is None
        )
        # 부분 가격은 지출에 **넣되 밝힌다.** 넣는 것은 안 쓴 것처럼 보이지
        # 않기 위해서이고, 밝히는 것은 그 수가 판정에는 안 쓰였기 때문이다.
        advisory_partial = sum(
            1
            for r in usable
            for key in ("advice_first", "advice_failure", "retry")
            if has_missing_prices(r.get(key)) and cost_of(r.get(key)) is not None
        )
        realized = sum(cheap_all) + expensive_total + advisory_spend
        if advisory_unpriced:
            print(
                f"  주의: 조언·재시도 {advisory_unpriced}건의 비용을 얻지 못했다."
                " 아래 '실제로 쓴 돈' 은 그만큼 실제보다 작다."
            )
        if advisory_partial:
            print(
                f"  주의: 조언·재시도 {advisory_partial}건의 비용이 부분값이다."
                " 지출에는 넣었지만 a 와 r 에는 안 넣었다 — 두 수의 모집단이 다르다."
            )
        expensive_mean = expensive_total / len(paired)
        # 승급하지 않은 과제의 비싼 비용은 잰 적이 없다. 승급 과제의 평균으로
        # 메꾸되, 그것이 대입값이라는 사실을 숨기지 않는다.
        counterfactual = expensive_mean * len(cheap_all)
        if counterfactual > 0:
            print(
                f"\n  대조: 실제로 쓴 돈 {realized:.4f} 대 전부 비싼 경로로 돌렸을 때"
                f" {counterfactual:.4f}  ->  절감 {1 - realized / counterfactual:.1%}"
            )
            print(
                f"    승급하지 않은 {len(cheap_all) - len(cheap_escalated)}건의 비싼"
                " 비용은 잰 적이 없어 승급 과제 평균으로 메꿨다. 그 과제들이 더"
                " 쉬웠다면 이 절감은 과대 평가다."
            )
            if unpriced > 0:
                print(
                    f"    또 승급 {unpriced}건의 비싼 비용이 표본에서 빠져 있다."
                    " 그 돈은 실제로 썼으므로 왼쪽 숫자는 과소 집계다."
                )
    # c 를 확정값으로 두고 p 만 흔들면 구간이 실제보다 좁다. c 도 표본에서
    # 추정한 값이므로 둘의 흔들림을 함께 태운다. 결론을 내릴 때 쓰는 구간은
    # 언제나 이 넓은 쪽이다.
    c_lo, c_hi = c_range if c_range is not None else (c, c)
    best, worst = c_lo + lo, c_hi + hi
    if c_range is not None:
        print(f"  c 도 흔들리므로 구간은 c∈[{c_lo:.3f}, {c_hi:.3f}] 와 p 를 함께 태운다")
    # 조언을 켜고 잰 로그에서는 이 수들을 **표식 없이 찍지 않는다.** 이것은
    # 조언 없는 c + p 모형의 절감률과 손익분기다. 바로 위에서 "이 로그의 모형이
    # 아니다" 라고 적어 놓고 무표식으로 찍으면, 읽는 쪽은 그 수를 이 실행의
    # 결과로 가져간다 — 경고는 읽히지 않고 숫자는 인용된다.
    # 이 수들의 c 는 **이 로그에서 잰 것** 이다. 계획이 붙었으면 그것은 c_A
    # 이고, 아래 판정문도 그렇게 부른다. "조언 없는 모형" 이라고 표시하면
    # 한 숫자를 두 곳이 반대로 부르게 된다 — 틀린 것은 c 가 아니라 **모형** 이다.
    if not advisor_on:
        aside = "  "
    elif shape_a_measured:
        aside = "  (c_A 로 세운 조언 없는 모형이다 — 이 로그의 모형이 아니다) "
    else:
        aside = "  (조언 없는 모형의 식이다 — 이 로그의 모형이 아니다) "
    print(f"{aside}가장 유리한 끝 (c={c_lo:.3f}, p={lo:.1%}): 절감 {1 - best:.1%}")
    print(f"{aside}가장 불리한 끝 (c={c_hi:.3f}, p={hi:.1%}): 절감 {1 - worst:.1%}")
    print(f"{aside}손익분기 p = {1 - c:.1%}  (c 가 {c_hi:.3f} 이면 {1 - c_hi:.1%})")

    # 분모가 승급의 절반도 못 담으면, 그것은 요약이라고 부를 수 없다. 빠진
    # 값의 방향은 알 수 없으므로 넓히는 것으로도 메울 수 없다 — jackknife 는
    # 값 매겨진 것만 보기 때문이다. 판정을 내지 않는다. 이 규칙은 판정을
    # 없앨 뿐 새로 만들지 않으므로, 틀려도 과도한 신중함으로만 틀린다.
    thin_denominator = (
        c_range is not None and escalated_total > 0 and len(paired) * 2 < escalated_total
    )
    # 요청은 됐는데 실제로는 안 붙은 실행. 러너가 빈 조언을 안 붙이므로
    # 생긴다. 이런 건이 섞이면 p′ 도 c_A 도 두 모집단의 평균이 된다.
    plan_requested_not_applied = sum(
        1
        for rec in usable
        if isinstance(rec.get("advisor"), dict)
        and (rec["advisor"] or {}).get("advise_first")
        and not (rec["advisor"] or {}).get("advise_first_applied")
    )
    if plan_requested_not_applied:
        print(
            f"\n  경고: 과제 {plan_requested_not_applied}건은 시작 전 조언을 요청했지만"
            " 조언이 비거나 실패해 계획이 붙지 않았다. 그 실행은 Shape A 표본이"
            " 아니다 — p′ 와 c_A 를 이 로그에서 뽑으면 계획을 받은 실행과 못 받은"
            " 실행이 섞인다. 그 건을 빼고 다시 모아야 한다."
        )
    failure_advice_on = any(
        isinstance(rec.get("advisor"), dict) and (rec["advisor"] or {}).get("advise_on_failure")
        for rec in usable
    )
    if thin_denominator:
        # 사다리 **밖** 에서 찍는다. 이것은 판정이 아니라 위에 이미 출력된 c 와
        # 그 구간에 붙는 단서이고, 조언이 켜졌다고 사라져야 할 이유가 없다.
        # 사다리 안에 두면 elif advisor_on 이 먼저 흡수해, 조언 로그를 보는
        # 사람만 유보 문구 없이 얇은 구간을 근거로 판단하게 된다.
        print(
            f"\n  경고: 승급 {escalated_total}건 중 {len(paired)}건만 양쪽 비용을"
            " 얻어, 분모가 승급의 절반도 대표하지 못한다. 빠진 값이 어느 쪽이었는지"
            " 알 수 없으므로 구간을 넓히는 것으로도 메울 수 없다."
        )
    if advisor_on and c_range is None:
        # c 를 재지 못한 로그다. 사다리 뒤쪽의 c_range is None 분기는 아래
        # advisor_on 이 먼저 흡수해 도달하지 못하므로 여기서 막는다. 이것을
        # 빠뜨리면 재지 못한 c 를 c_A 라고 소개하게 된다.
        print(
            "\n  -> 판정 없음. 이 로그는 조언을 켜고 쟀지만 c 를 내지 못했다."
            " c_A 도 없으므로 Shape A 손익식을 세울 수 없다."
        )
    elif advisor_on and timed_out_tasks:
        # 조언 분기가 타임아웃 경고를 가리면 안 된다. 빠진 비용이 위쪽으로
        # 열려 있다는 사실은 조언 판정에도 그대로 해당한다. **어느 모양이든**
        # 그렇다 — 시작 전 조언만 켠 로그에서 이 경고를 건너뛰면 불완전한 c 를
        # c_A 라고 소개하며 Shape A 손익식을 제시하게 된다.
        print(
            f"\n  -> 판정 없음. 과제 {timed_out_tasks}건이 타임아웃이라 그 비용을 모른다."
            + (
                " 위의 조언 판정도 같은 이유로 보류됐다."
                if failure_advice_on
                else " 이 로그의 c 를 c_A 로 쓰면 안 된다 — 빠진 비용만큼 작다."
            )
        )
    elif advisor_on:
        # 조언이 켜진 로그에서는 c + p 기반 판정을 내지 않는다. 위에 그 식이
        # 이 실행의 모형이 아니라고 적어 놓고 그 식으로 판정하면 앞뒤가 맞지
        # 않는다. 조언 자체의 판정은 위쪽 s > a + q·r 이 한다.
        print(
            "\n  -> 판정 없음(c + p 기준). 이 로그는 조언을 켜고 쟀다."
            + (
                (
                    " 실패 후 조언을 **더 붙일지** 의 한계 판정은 위의"
                    " s′ > a_B′ + q′·r′ 을 보라(q′ 는 재시도 시도율). 시작 전"
                    " 조언까지 포함한 전체 이득은 그 식이 답하지 않는다."
                    # **계산부와 같은 술어를 쓴다.** 요청 여부로 프라임을
                    # 고르면, 계획이 실제로 안 붙은 로그에 A+B 식을 가리키게
                    # 된다 — 위쪽 계산은 프라임 없이 찍고 여기만 프라임이다.
                    if shape_a_measured
                    # 계획 적용이 섞이면 위쪽에서 a, q, r, s 를 아예 안
                    # 냈다. 그런데 여기서 "위의 판정을 보라" 고 하면 없는
                    # 것을 가리킨다 — 읽는 쪽은 다른 수를 찾아 쓰게 된다.
                    else (
                        " 계획 적용이 섞여 조언 판정을 내지 않았다. 그 건을 빼고"
                        " 다시 모아야 s 를 낼 수 있다."
                        if mixed_application
                        else " 조언의 이득 판정은 위의 s > a + q·r 을 보라(q 는 재시도 시도율)."
                    )
                )
                if failure_advice_on
                else (
                    (
                        " 시작 전 조언만 켠 로그에는 그 판정이 없다. 이득 조건은"
                        " a_A + (c_A − c) < p − p′ 이고, 이 중 **a_A, c_A, p′ 세"
                        " 항은 이 로그가 이미 준다** — 위에 찍힌 c 가 c_A 이고,"
                        " 싼 경로의 **실패율** 이 p′ 다(통과율이 아니다 — 식은"
                        " 실패율 차 p − p′ 를 쓰므로 여기서 뒤집으면 결론이"
                        " 반대가 된다). 조언을 끄고 같은 과제를 한 번 더 재야"
                        " 나오는 것은 기준선 두 항, c 와 p 뿐이다. a_A 를"
                        " p − p′ 와 바로 비교하면 계획이 늘린 프롬프트"
                        " 비용(c_A − c)이 빠져 조언 쪽에 유리하게 틀린다."
                    )
                    if shape_a_measured
                    # 계획이 한 번도 안 붙었으면 이 로그의 c 는 c_A 가 아니라
                    # 그냥 c 이고 실패율도 p′ 가 아니다. 조언 호출 비용만 쓰고
                    # 계획은 못 받은 실행을 잰 것이다.
                    else (
                        " 시작 전 조언을 요청했지만 계획이 실제로 붙은 실행이"
                        " 없다. 이 로그의 c 와 실패율은 조언 없는 값이고, 조언"
                        " 호출 비용만 더 썼다. c_A 와 p′ 를 얻으려면 조언이"
                        " 실제로 붙은 실행을 다시 모아야 한다."
                    )
                )
            )
        )
    elif timed_out_tasks and c_range is not None:
        # 판정을 내지 않는다. 빠진 비용이 위쪽으로 열려 있으면 구간도 위쪽으로
        # 열려 있고, 닫힌 구간을 근거로 "유리하다/손해다" 를 말할 수 없다.
        print(
            f"\n  -> 판정 없음. 과제 {timed_out_tasks}건이 타임아웃이라 그 비용을 모른다."
            " 타임아웃난 실행은 예산을 끝까지 태운 가장 비싼 실행이므로, 한 건만으로도"
            " 위 결론이 뒤집힐 수 있다. 그 과제를 빼고 다시 모으거나, 러너의"
            " CHILD_TIMEOUT 을 늘려 끝까지 돌게 한 뒤 다시 재야 한다."
        )
    elif thin_denominator:
        print("\n  -> 판정 없음. 위의 얇은 분모 경고를 보라.")
    elif c_range is None:
        # c 를 재지 못했으면 판정하지 않는다. 남의 벤치마크에서 온 0.31 로
        # "유리하다" 를 찍으면, 재지 않은 것을 잰 것처럼 보이게 된다.
        if not single_origin:
            print(f"\n  -> 판정 없음. {origin_problem}. 위에 적은 대로 다시 재야 한다.")
        elif measured_c is None:
            print(
                "\n  -> 판정 없음. c 를 이 표본에서 재지 못해 가정값을 썼다."
                " 양쪽 비용이 있는 승급 과제를 더 모아야 한다."
            )
        else:
            # 이 분기는 c 를 쟀을 때만 온다. 그러려면 값 매겨진 승급이 3건
            # 이상이고 과제는 그 상위집합이므로, "과제가 3건 미만" 은 여기서
            # 나올 수 없다. 남는 원인은 한 건씩 뺀 추정치가 전부 같은 경우다.
            print(
                "\n  -> 판정 없음. c 는 쟀지만 흔들림 폭을 구하지 못했다"
                "(한 건씩 빼도 값이 달라지지 않는다)."
                " 구간 없이는 손익분기를 넘는지 말할 수 없다."
            )
    elif worst < 1:
        print("\n  -> 구간 전체가 손익분기 아래다. 싼 경로 우선이 이 표본에서 유리하다.")
    elif best > 1:
        print("\n  -> 구간 전체가 손익분기 위다. 싼 경로 우선은 손해다.")
    else:
        print("\n  -> 구간이 손익분기를 가로지른다. 아직 결론 낼 수 없다.")
    if c_range is None and paired:
        print(
            "  주의: c 의 흔들림 폭을 계산할 표본이 없어 구간은 p 만 전파한다."
            " 실제 불확실성은 이보다 넓다."
        )

    # 토큰은 벤더 안에서만 의미가 있다. 합쳐서 비율을 내지 않는다.
    def child_tokens(attempt: object) -> int | None:
        if not isinstance(attempt, dict):
            return None
        child = attempt.get("child")
        if not isinstance(child, dict):
            return None
        tokens = child.get("tokens")
        return tokens if isinstance(tokens, int) else None

    cheap_tokens = [t for r in usable if (t := child_tokens(r["cheap"])) is not None]
    expensive_tokens = [t for r in usable if (t := child_tokens(r["expensive"])) is not None]
    if cheap_tokens:
        print(
            f"\n토큰(참고, 벤더 간 비교 금지): 싼 경로 {sum(cheap_tokens):,} "
            f"({len(cheap_tokens)}회)"
        )
    if expensive_tokens:
        print(
            f"                              승급 경로 {sum(expensive_tokens):,} "
            f"({len(expensive_tokens)}회)"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
