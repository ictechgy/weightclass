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
        expensive: float | None
        escalated: bool

    # c 는 두 평균의 비다. **표본 전체가 한 출처여야 한다.** 쌍 단위로만
    # 확인하면 부족하다 — 어떤 과제는 벤더 청구액, 어떤 과제는 요금표
    # 환산값인 채로 분자 평균에 함께 들어가면, 그 평균은 무엇의 평균도
    # 아니다. 앞선 라운드는 쌍만 검사하고 분자는 그대로 뒀다.
    origins: set[str | None] = set()
    for r in usable:
        if cost_of(r.get("cheap")):
            origins.add(cost_origin(r.get("cheap")))
        if cost_of(r.get("expensive")):
            origins.add(cost_origin(r.get("expensive")))
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
    for r in usable:
        # 승급 여부는 p 를 세는 쪽과 같은 술어로 판정한다. 러너가 빈 dict 나
        # 오류 스텁을 남기면 "승급 N건 중 M건" 문구가 실제와 어긋난다.
        escalated = isinstance(r.get("expensive"), dict)
        if escalated:
            escalated_total += 1
        if timed_out(r.get("cheap")) or timed_out(r.get("expensive")):
            timed_out_tasks += 1
        cheap_cost = cost_of(r.get("cheap")) if single_origin else None
        if not cheap_cost:
            # 0 도 여기서 뺀다. 싼 쪽 0 은 c 를 끌어내려 "거의 공짜" 라는
            # 결론을 만드는데, 실제로는 요금표가 비었거나 사용량을 못 읽은
            # 경우가 대부분이다. cost_of 는 타임아웃에도 None 을 준다.
            unusable_cheap += 1
            continue
        expensive_cost = cost_of(r.get("expensive")) if escalated else None
        if escalated and not expensive_cost:
            unpriced_escalations += 1
            expensive_cost = None
        tasks.append(Task(cheap_cost, expensive_cost, escalated))

    cheap_all = [x.cheap for x in tasks]
    cheap_escalated = [x.cheap for x in tasks if x.escalated]
    cheap_passing = [x.cheap for x in tasks if not x.escalated]
    priced_pairs = [x for x in tasks if x.expensive is not None]
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
        priced = [x.expensive for x in sample if x.expensive]
        if not priced:
            return None
        return statistics.fmean([x.cheap for x in sample]) / statistics.fmean(priced)

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
        expensive_mean = statistics.fmean([x.expensive for x in priced_pairs if x.expensive])
        measured = statistics.fmean(cheap_all) / expensive_mean
        median_ratio = statistics.median(paired)
        print(
            f"\n실측 비용비 c = {measured:.3f}"
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
        if unpriced_escalations:
            print(
                f"  승급 {unpriced_escalations}건은 비싼 비용을 얻지 못해(타임아웃이거나"
                " 비용이 없거나 0) 분모에서 빠졌다. 타임아웃난 실행은 예산을 끝까지"
                " 태운 가장 비싼 실행이므로, 그것이 빠지면 분모가 작아져 c 가 커지는"
                " 쪽으로 치우친다."
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
        realized = sum(cheap_all) + expensive_total
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
    print(f"  가장 유리한 끝 (c={c_lo:.3f}, p={lo:.1%}): 절감 {1 - best:.1%}")
    print(f"  가장 불리한 끝 (c={c_hi:.3f}, p={hi:.1%}): 절감 {1 - worst:.1%}")
    print(f"  손익분기 p = {1 - c:.1%}  (c 가 {c_hi:.3f} 이면 {1 - c_hi:.1%})")

    if timed_out_tasks and c_range is not None:
        # 판정을 내지 않는다. 빠진 비용이 위쪽으로 열려 있으면 구간도 위쪽으로
        # 열려 있고, 닫힌 구간을 근거로 "유리하다/손해다" 를 말할 수 없다.
        print(
            f"\n  -> 판정 없음. 과제 {timed_out_tasks}건이 타임아웃이라 그 비용을 모른다."
            " 타임아웃난 실행은 예산을 끝까지 태운 가장 비싼 실행이므로, 한 건만으로도"
            " 위 결론이 뒤집힐 수 있다. 그 과제를 빼고 다시 모으거나, 러너의"
            " CHILD_TIMEOUT 을 늘려 끝까지 돌게 한 뒤 다시 재야 한다."
        )
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
