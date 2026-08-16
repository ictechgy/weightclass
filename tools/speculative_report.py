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

    records = [
        json.loads(line)
        for line in arguments.log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not records:
        print("기록 없음")
        return 1

    total = len(records)
    cheap_passed = sum(1 for r in records if r["cheap"]["verify"]["passed"])
    failed = total - cheap_passed
    both_failed = sum(
        1
        for r in records
        if not r["cheap"]["verify"]["passed"]
        and (r["expensive"] is None or not r["expensive"]["verify"]["passed"])
    )

    p = failed / total
    lo, hi = wilson(failed, total)
    c = arguments.cost_ratio

    print(f"과제 {total}개")
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
        r["cheap"]["child"]["tokens"] for r in records if r["cheap"].get("child", {}).get("tokens")
    ]
    expensive_tokens = [
        r["expensive"]["child"]["tokens"]
        for r in records
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
