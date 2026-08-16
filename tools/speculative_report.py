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
    for line in log_path.read_text(encoding="utf-8").splitlines():
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
        cheap = record["cheap"]
        if not isinstance(cheap, dict):
            # assert 로 좁히면 python -O 에서 사라진다. 이 술어는 손상 줄
            # 분류를 통과한 기록에도 도므로 실제 검사가 필요하다.
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
            print(f"  - {_safe(str(r['cheap'].get('error')))}")

    total = len(usable)
    if not total:
        print("p 를 계산할 수 있는 기록이 없다")
        return 1
    cheap_passed = sum(1 for r in usable if r["cheap"]["accepted"])
    failed = total - cheap_passed
    both_failed = sum(
        1
        for r in usable
        if not r["cheap"]["accepted"] and (r["expensive"] is None or not r["expensive"]["accepted"])
    )

    p = failed / total
    lo, hi = wilson(failed, total)
    c = arguments.cost_ratio

    print(f"과제 {total}개 (기록 {len(records)}개)")
    print(f"  싼 경로 검증 통과: {cheap_passed}")
    print(f"  승급 필요        : {failed}")
    print(f"  둘 다 실패       : {both_failed}")
    print(f"\np = {p:.1%}   95% CI [{lo:.1%}, {hi:.1%}]")

    print(f"\n모델 비용비 c = {c:.2f} 기준 (기대 비용 = c + p)")
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
    cheap_tokens = [
        r["cheap"]["child"]["tokens"] for r in usable if r["cheap"].get("child", {}).get("tokens")
    ]
    expensive_tokens = [
        r["expensive"]["child"]["tokens"]
        for r in usable
        if r["expensive"] and r["expensive"].get("child", {}).get("tokens")
    ]
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
