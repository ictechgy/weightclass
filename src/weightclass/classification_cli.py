"""Lightweight local-classification command family."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from typing import NoReturn

from .classification import (
    InvalidTaskError,
    Tier,
    classify_task,
    classify_task_with_reason,
    read_task_from_standard_input,
    validate_task,
)

TriageAnswer = Callable[[str, str], Tier]
TriageDescriptor = Callable[[str], dict[str, object]]
TaskReader = Callable[[], str]


class ClassifyInvalidInputError(ValueError):
    """Raised for invalid classify argv without exposing caller values."""


class _NoVendorTriageError(Exception):
    """Sentinel that prevents the local path from catching unrelated failures."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        raise ClassifyInvalidInputError()


def classify_task_input(
    source_vendor: str | None = None,
    ask_vendor: bool = False,
    show_triage_command: bool = False,
    explain: bool = False,
    *,
    ask_vendor_for_tier: TriageAnswer | None = None,
    triage_descriptor: TriageDescriptor | None = None,
    triage_unavailable_error: type[Exception] = _NoVendorTriageError,
    read_task: TaskReader = read_task_from_standard_input,
) -> int:
    """Classify one stdin task while keeping optional vendor imports lazy."""
    if (ask_vendor or show_triage_command) and source_vendor is None:
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    if explain and (ask_vendor or show_triage_command):
        print(json.dumps({"error": "invalid_input"}), file=sys.stderr)
        return 2
    if show_triage_command:
        assert source_vendor is not None
        assert triage_descriptor is not None
        try:
            print(json.dumps(triage_descriptor(source_vendor)))
        except triage_unavailable_error:
            print(json.dumps({"error": "triage_unavailable"}), file=sys.stderr)
            return 8
        return 0
    try:
        task = read_task()
        if not ask_vendor:
            if explain:
                decision = classify_task_with_reason(task)
                tier = decision.tier
            else:
                tier = classify_task(task)
            tier_source = "local"
        else:
            validate_task(task)
            assert source_vendor is not None
            assert ask_vendor_for_tier is not None
            tier = ask_vendor_for_tier(task, source_vendor)
            tier_source = "vendor"
    except InvalidTaskError:
        print(json.dumps({"error": "invalid_task"}), file=sys.stderr)
        return 2
    except triage_unavailable_error:
        print(json.dumps({"error": "triage_unavailable"}), file=sys.stderr)
        return 8
    response: dict[str, str] = {"tier": tier}
    if explain:
        response["reason_code"] = decision.reason_code
        response["policy_version"] = decision.policy_version
    elif ask_vendor:
        response["tier_source"] = tier_source
    print(json.dumps(response))
    return 0


def classify_from_standard_input(
    source_vendor: str | None = None,
    ask_vendor: bool = False,
    show_triage_command: bool = False,
    explain: bool = False,
) -> int:
    """Load the vendor triage family only when the caller explicitly requests it."""
    if ask_vendor or show_triage_command:
        from .triage import TriageUnavailableError, ask_vendor_for_tier, triage_descriptor

        return classify_task_input(
            source_vendor,
            ask_vendor,
            show_triage_command,
            explain,
            ask_vendor_for_tier=ask_vendor_for_tier,
            triage_descriptor=triage_descriptor,
            triage_unavailable_error=TriageUnavailableError,
        )
    return classify_task_input(source_vendor, ask_vendor, show_triage_command, explain)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _ArgumentParser(
        prog="wclass classify",
        description="Print the tier of a task read from standard input.",
        allow_abbrev=False,
    )
    parser.add_argument("--source-vendor")
    parser.add_argument("--ask-vendor", action="store_true")
    parser.add_argument("--show-triage-command", action="store_true")
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
    return classify_from_standard_input(
        arguments.source_vendor,
        arguments.ask_vendor,
        arguments.show_triage_command,
        arguments.explain,
    )
