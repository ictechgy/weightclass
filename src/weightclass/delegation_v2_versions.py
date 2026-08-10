"""Closed version tuple dispatch for delegation protocols."""

from typing import Final

DelegationVersionTuple = tuple[object, object, object, object, object]
V1_TUPLE: Final = (1, 1, 1, 1, "WCD1")
V2_TUPLE: Final = (2, 2, 2, 2, "WCD2")


class DelegationVersionError(ValueError):
    """A value-free invalid delegation version tuple."""


def dispatch_delegation_versions(value: DelegationVersionTuple) -> int:
    if any(isinstance(item, bool) for item in value[:4]):
        raise DelegationVersionError()
    if value == V1_TUPLE:
        return 1
    if value == V2_TUPLE:
        return 2
    raise DelegationVersionError()
