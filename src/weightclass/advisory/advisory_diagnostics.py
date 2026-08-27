"""Shared closed vocabularies for task-free advisory diagnostics."""

FAILURE_STAGES = frozenset(
    {
        "setup",
        "execution",
        "result",
        "handover",
        "verification",
        "verification_integrity",
        "acceptance",
        "persistence",
        "unknown",
    }
)

RESULT_SHAPES = frozenset(
    {
        "empty",
        "unstructured",
        "structured_output",
        "json_text",
        "fenced_json",
        "prose",
        "envelope_without_result",
        "malformed_envelope",
        "unknown",
    }
)

CHILD_FAILURE_CODES = frozenset(
    {
        "none",
        "timeout",
        "authentication",
        "rate_limit",
        "context_limit",
        "invalid_invocation",
        "permission_or_approval",
        "network",
        "provider_unavailable",
        "model_unavailable",
        "account_limit",
        "configuration",
        "result_contract",
        "unknown",
    }
)

PROVIDER_CHECK_FAILURE_CODES = CHILD_FAILURE_CODES | frozenset({"local_probe_failed"})
