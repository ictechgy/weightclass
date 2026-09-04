"""Lightweight local-classification command family."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from typing import NoReturn

from .classification import (
    InvalidTaskError,
    classify_task,
    classify_task_with_reason,
    read_task_from_standard_input,
)

TaskReader = Callable[[], str]


class ClassifyInvalidInputError(ValueError):
    """Raised for invalid classify argv without exposing caller values."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        raise ClassifyInvalidInputError()


def classify_task_input(
    source_vendor: str | None = None,
    explain: bool = False,
    *,
    read_task: TaskReader = read_task_from_standard_input,
) -> int:
    """Classify one stdin task locally, offline, without starting any vendor.

    `source_vendor` 는 받아서 라벨 형식만 검증하고 판정에는 쓰지 않는다. 분류는
    로컬 결정이므로 벤더를 알 필요가 없고, 라우팅 명령과 같은 인수를 그대로
    넘기는 호출부가 깨지지 않도록 인수 자체는 남긴다.
    """
    del source_vendor
    try:
        task = read_task()
        if explain:
            decision = classify_task_with_reason(task)
            tier = decision.tier
        else:
            tier = classify_task(task)
    except InvalidTaskError:
        print(json.dumps({"error": "invalid_task"}), file=sys.stderr)
        return 2
    response: dict[str, str] = {"tier": tier}
    if explain:
        response["reason_code"] = decision.reason_code
        response["policy_version"] = decision.policy_version
    print(json.dumps(response))
    return 0


def classify_from_standard_input(
    source_vendor: str | None = None,
    explain: bool = False,
) -> int:
    """Retain the historical direct-call seam for the local classification path."""
    return classify_task_input(source_vendor, explain)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _ArgumentParser(
        prog="wclass classify",
        description="Print the tier of a task read from standard input.",
        allow_abbrev=False,
    )
    parser.add_argument("--source-vendor")
    parser.add_argument(
        "--explain",
        action="store_true",
        help="Include static reason-code and local policy metadata.",
    )
    try:
        arguments = parser.parse_args(argv)
        if arguments.source_vendor is not None:
            from .router import InvalidVendorLabelError, validate_vendor_label

            try:
                validate_vendor_label(arguments.source_vendor)
            except InvalidVendorLabelError:
                raise ClassifyInvalidInputError() from None
    except ClassifyInvalidInputError:
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    return classify_from_standard_input(arguments.source_vendor, arguments.explain)
