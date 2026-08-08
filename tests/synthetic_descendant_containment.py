"""Test-only conservative descendant-containment feasibility record."""

from collections.abc import Mapping
from typing import Final

from tests.synthetic_probe_runner import is_runner_direct_probe_result

_PLATFORM_CONCLUSIONS: Final = {
    "darwin": [
        "process-groups-and-sessions-do-not-prevent-escape",
        "kqueue-observes-only-known-processes",
        "runner-owned-authoritative-boundary-not-established",
    ],
    "linux": [
        "process-groups-and-sessions-do-not-prevent-escape",
        "pidfd-observes-only-known-processes",
        "runner-owned-cgroup-v2-boundary-not-verified",
    ],
}
_REJECTED_NON_AUTHORITIES: Final = [
    "child-cooperation",
    "child-self-report",
    "process-group-membership",
]


def assess_descendant_containment(
    platform_target: str, probe_observation: Mapping[str, object]
) -> dict[str, object]:
    """Record a deterministic NO-GO without inferring capability from a label."""
    if platform_target not in _PLATFORM_CONCLUSIONS:
        raise ValueError("unsupported containment target")

    direct_exit_and_invalid_traffic = (
        is_runner_direct_probe_result(probe_observation)
        and probe_observation.get("child_started") is True
        and isinstance(probe_observation.get("exit_status"), int)
        and not isinstance(probe_observation.get("exit_status"), bool)
        and probe_observation.get("diagnostic") == "probe_protocol_invalid"
    )
    bounded_observation = (
        "direct-child-exit-and-invalid-runner-fd-traffic-observed"
        if direct_exit_and_invalid_traffic
        else "authoritative-descendant-observation-unavailable"
    )
    observation_provenance = (
        "runner-direct" if direct_exit_and_invalid_traffic else "unavailable-or-untrusted"
    )
    return {
        "authority": "not-established",
        "bounded_observation": bounded_observation,
        "decision": "NO-GO",
        "delegation_support": False,
        "observation_provenance": observation_provenance,
        "path_execution_identity": "TOCTOU-UNRESOLVED",
        "platform_conclusions": list(_PLATFORM_CONCLUSIONS[platform_target]),
        "platform_target": platform_target,
        "primitive_availability": "unavailable-or-unverified",
        "qualification_eligible": False,
        "rejected_non_authorities": list(_REJECTED_NON_AUTHORITIES),
    }
