"""Lightweight constants shared by managed advisory parsers and services."""

SCHEMA_VERSION = 1
WORKFLOWS = ("implementation", "review", "research", "diagnosis", "design")
EVIDENCE_WORKFLOWS = WORKFLOWS[1:]
BUILTIN_VENDORS = ("codex", "claude", "agy", "grok")
ROLES = ("cheap", "advisor", "expensive")
CONSULT_DEFAULT_TIMEOUT_SECONDS = 5_400.0
