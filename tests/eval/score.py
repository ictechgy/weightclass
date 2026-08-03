"""Score a classifier against the committed consensus corpus.

기본값은 로컬 분류기만 채점한다. 네트워크를 쓰지 않고 벤더 CLI 도 부르지
않으므로 CI 에서 그대로 돌릴 수 있다. --vendor 를 주면 벤더 판정도 함께
재는데, 태스크당 한 번씩 실제 구독을 쓰므로 기본값에서 제외한다.
"""

import argparse
import json
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from weightclass.classification import Tier, classify_task  # noqa: E402
from weightclass.triage import TriageUnavailableError, ask_vendor_for_tier  # noqa: E402

CORPUS = pathlib.Path(__file__).with_name("corpus.json")
RANK = {"low": 0, "standard": 1, "high": 2}


def report(label: str, expected: list[str], got: list[str]) -> None:
    """Print agreement and, more importantly, which direction the errors run."""
    agreed = sum(1 for e, g in zip(expected, got, strict=True) if e == g)
    under = sum(1 for e, g in zip(expected, got, strict=True) if g in RANK and RANK[g] < RANK[e])
    over = sum(1 for e, g in zip(expected, got, strict=True) if g in RANK and RANK[g] > RANK[e])
    high_total = sum(1 for e in expected if e == "high")
    high_missed = sum(1 for e, g in zip(expected, got, strict=True) if e == "high" and g != "high")

    print(f"\n{label}")
    print(f"  agreement      {agreed}/{len(expected)} ({agreed / len(expected) * 100:.0f}%)")
    print(f"  under-rated    {under}  (the expensive direction)")
    print(f"  over-rated     {over}")
    print(f"  high missed    {high_missed}/{high_total}")
    print(f"  distribution   {dict(Counter(got))}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vendor",
        choices=("claude", "codex"),
        help="also score the vendor path; spends one subscription call per task",
    )
    arguments = parser.parse_args()

    entries = json.loads(CORPUS.read_text(encoding="utf-8"))
    expected = [entry["consensus"] for entry in entries]
    tasks = [entry["task"] for entry in entries]

    print(f"corpus: {len(entries)} tasks, consensus {dict(Counter(expected))}")
    report("local classifier", expected, [classify_task(task) for task in tasks])
    report(
        "vendor tiers recorded when the figures were first measured",
        expected,
        [entry["recorded_vendor_tier"] for entry in entries],
    )

    if arguments.vendor:
        print(f"\nasking {arguments.vendor} for {len(tasks)} tiers — this spends your subscription")
        measured: list[str] = []
        for task in tasks:
            try:
                tier: Tier | str = ask_vendor_for_tier(task, arguments.vendor)
            except TriageUnavailableError:
                tier = "UNAVAILABLE"
            measured.append(tier)
        report(f"vendor path, measured now ({arguments.vendor})", expected, measured)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
