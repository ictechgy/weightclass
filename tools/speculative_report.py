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
import statistics
from pathlib import Path


def _safe(text: str, limit: int = 200) -> str:
    """Strip control characters before printing anything read from the log.

    The log is an ordinary file that a compromised or crafted run could have
    written. Echoing it verbatim lets ANSI escapes rewrite the terminal, which
    is a poor way to learn that a measurement went wrong.
    """
    cleaned = "".join(character for character in text if character.isprintable())
    return cleaned[:limit] + ("…" if len(cleaned) > limit else "")


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument(
        "--cost-ratio",
        type=float,
        default=0.31,
        help="cheap route cost relative to expensive (default 0.31, the measured value)",
    )
    arguments = parser.parse_args()

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
    print(f"  승급 필요        : {failed}")
    print(f"  둘 다 실패       : {both_failed}")
    print(f"\np = {p:.1%}   95% CI [{lo:.1%}, {hi:.1%}]")

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
            # 중간에 죽인 실행의 사용량은 부분값이다. 그것을 c 표본에 넣으면
            # 싼 경로의 비용이 실제보다 낮게 잡혀 절감이 부풀려진다. 비용을
            # 모르는 것으로 처리해 표본에서 빠지게 한다.
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
        cost = float(value)
        return cost if math.isfinite(cost) and cost >= 0 else None

    paired: list[float] = []
    cheap_total = 0.0
    expensive_total = 0.0
    escalated_total = 0
    for r in usable:
        cheap_cost = cost_of(r.get("cheap"))
        expensive_cost = cost_of(r.get("expensive"))
        # 어느 쪽이든 0 이면 비율이 의미를 잃는다. 싼 쪽 0 은 c=0 으로
        # "공짜" 라는 결론을 만들고, 비싼 쪽 0 은 나눗셈 자체가 안 된다.
        # 요금표가 비었거나 벤더가 0 을 보고한 경우이므로 표본에서 뺀다.
        if r.get("expensive") is not None:
            escalated_total += 1
        if cheap_cost and expensive_cost:
            paired.append(cheap_cost / expensive_cost)
            cheap_total += cheap_cost
            expensive_total += expensive_cost

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
        return "turn" in str(usage.get("source", ""))

    multiturn = sum(1 for r in usable for arm in ("cheap", "expensive") if is_multiturn(r.get(arm)))
    if multiturn:
        print(
            f"\n주의: 여러 턴을 돈 실행 {multiturn}건. turn.completed 가 턴별 증분인지"
            " 누적인지 확인되지 않아 그 실행의 토큰은 과대 집계일 수 있다."
        )

    if len(paired) >= MINIMUM_PAIRED:
        # 기대 비용 식 c + p 는 c 를 **비용 가중** 비율로 본다. 과제별 비율의
        # 중앙값은 다른 값이고, 비용 분포가 치우치면 크게 갈린다. 공식에는
        # 가중 비율을 넣고 중앙값은 이상치 확인용으로 함께 보여 준다.
        measured = cheap_total / expensive_total
        median_ratio = statistics.median(paired)
        print(
            f"\n실측 비용비 c = {measured:.3f}  (승급 {escalated_total}건 중 양쪽 비용을"
            f" 얻은 {len(paired)}건의 비용 합계 비율. 기대 비용 식이 전제하는 가중치다)"
        )
        if len(paired) < escalated_total:
            print(
                f"  승급 {escalated_total - len(paired)}건은 비용이 없거나 0 이라 빠졌다."
                " 표본이 승급 전체를 대표하지 않을 수 있다."
            )
        print(f"  과제별 비율의 중앙값: {median_ratio:.3f}")
        if abs(median_ratio - measured) > 0.1:
            print(
                "  둘이 크게 다르다 — 비용이 큰 과제 몇 건이 합계를 지배한다는 뜻이다."
                " 과제를 더 모으기 전에는 어느 쪽도 안정적이지 않다."
            )
        print(
            "  이 c 는 승급이 일어난 과제, 즉 싼 경로가 실패한 부분집합에서만 나온다."
            " 그 과제들이 더 길거나 어려웠다면 전체를 대표하지 않는다."
        )
        if abs(measured - c) > 0.05:
            print(f"  주의: --cost-ratio 로 준 {c:.2f} 와 다르다. 아래 계산은 실측값을 쓴다.")
        c = measured
    elif paired:
        print(
            f"\n비용비 c = {c:.2f} — **가정값**이다. 양쪽 비용을 얻은 승급 과제가"
            f" {len(paired)}개뿐이라 실측값을 쓰기에 부족하다(최소 {MINIMUM_PAIRED}개)."
        )
    else:
        print(f"\n비용비 c = {c:.2f} — **가정값**이다. 승급 과제에서 양쪽 비용을 모두 얻지 못했다.")
        print(
            "  Claude 는 --output-format json 이면 total_cost_usd 를 준다."
            " Codex 는 USD 를 주지 않으므로 --prices 로 요금표를 넘겨야 한다."
        )

    print("\n기대 비용 = c + p")
    print(f"  기대 비용 {c + p:.2f}  ->  절감 {1 - (c + p):.1%}")
    print(f"  구간 하한 p={lo:.1%} 이면 절감 {1 - (c + lo):.1%}")
    print(f"  구간 상한 p={hi:.1%} 이면 절감 {1 - (c + hi):.1%}")
    print(f"  손익분기 p = {1 - c:.1%}")

    if hi < 1 - c:
        print("\n  -> 구간 전체가 손익분기 아래다. 싼 경로 우선이 이 표본에서 유리하다.")
    elif lo > 1 - c:
        print("\n  -> 구간 전체가 손익분기 위다. 싼 경로 우선은 손해다.")
    else:
        print("\n  -> 구간이 손익분기를 가로지른다. 아직 결론 낼 수 없다.")

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
