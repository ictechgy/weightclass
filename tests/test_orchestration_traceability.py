from __future__ import annotations

import importlib
import json
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

FIXTURE = Path(__file__).parent / "fixtures" / "orchestration_traceability.json"
REQUIRED_FIELDS = {
    "source",
    "capability",
    "schema_path",
    "validator_symbol",
    "test_symbol",
    "status",
    "non_goal",
}
ADOPTED_STATUSES = {
    "caller_requested",
    "structurally_enforced",
    "direct_child_observed",
    "runtime_unverified",
    "runtime_owned",
}
VALID_STATUSES = ADOPTED_STATUSES | {"not_adopted"}
NOT_ADOPTED_REQUIRED = {
    ("AutoGen", "selection_filter"),
    ("CrewAI", "topology_modes"),
    ("LangGraph", "persistence"),
}
EXPECTED_STATUS_IDENTITIES = {
    "caller_requested": {
        ("Orca", "run_task_dispatch_provenance"),
        ("Orca", "requested_ownership"),
        ("CrewAI", "hitl_gates"),
        ("OpenHands", "provider_neutral_capabilities"),
        ("OpenHands", "provider_neutral_workspace"),
    },
    "structurally_enforced": {
        ("Orca", "dag_readiness"),
        ("Orca", "typed_gates"),
        ("Codex", "bounded_independent_workstreams"),
        ("Codex", "projections"),
        ("Codex", "synthesis"),
        ("Codex", "mutable_ownership"),
        ("Codex", "independent_model_effort"),
        ("Claude", "model_effort"),
        ("Cursor", "allowed_pair_pool"),
        ("Cursor", "mode_binding"),
        ("LangGraph", "typed_transitions"),
        ("LangGraph", "typed_gates"),
        ("LangGraph", "typed_projections"),
        ("AutoGen", "fanout_join"),
        ("AutoGen", "synthesis_termination"),
    },
    "direct_child_observed": {("Orca", "direct_child_exit")},
    "runtime_unverified": {
        ("Orca", "completion_vs_settlement"),
        ("Claude", "tools"),
        ("Claude", "permissions"),
        ("Claude", "turns"),
    },
    "runtime_owned": {
        ("Claude", "hooks"),
        ("Claude", "memory"),
        ("Claude", "background"),
        ("Claude", "peer_messaging"),
    },
    "not_adopted": NOT_ADOPTED_REQUIRED,
}
EXPECTED_STATUS_BY_IDENTITY = {
    identity: status
    for status, identities in EXPECTED_STATUS_IDENTITIES.items()
    for identity in identities
}
SchemaSource = tuple[str, str, str, str | None]
SCHEMA_FIELD_SOURCES: dict[str, SchemaSource] = {
    "/profiles/*/allowed_model_effort_pairs": (
        "delegation",
        "profile",
        "allowed_model_effort_pairs",
        "/profiles/*/allowed_model_effort_pairs",
    ),
    "/routes/*/model": ("native", "route", "model", None),
    "/routes/*/effort": ("native", "route", "effort", None),
    "/workflows/*/requested_run_id": ("delegation", "workflow", "requested_run_id", None),
    "/workflows/*/concurrency": ("delegation", "workflow", "concurrency", None),
    "/workflows/*/tasks/*/requested_task_id": (
        "delegation",
        "task",
        "requested_task_id",
        None,
    ),
    "/workflows/*/tasks/*/requested_dispatch_id": (
        "delegation",
        "task",
        "requested_dispatch_id",
        None,
    ),
    "/workflows/*/tasks/*/owner_role": ("delegation", "task", "owner_role", None),
    "/workflows/*/tasks/*/model": ("delegation", "task", "model", None),
    "/workflows/*/tasks/*/effort": ("delegation", "task", "effort", None),
    "/workflows/*/tasks/*/capabilities": (
        "delegation",
        "task",
        "capabilities",
        "/tasks/*/capabilities",
    ),
    "/workflows/*/tasks/*/turns": ("delegation", "task", "turns", None),
    "/workflows/*/tasks/*/request/tools": (
        "delegation",
        "request",
        "tools",
        "/tasks/*/request/tools",
    ),
    "/workflows/*/tasks/*/request/permissions": (
        "delegation",
        "request",
        "permissions",
        "/tasks/*/request/permissions",
    ),
    "/workflows/*/tasks/*/worktree": ("delegation", "task", "worktree", None),
    "/workflows/*/tasks/*/mutable_scopes": (
        "delegation",
        "task",
        "mutable_scopes",
        "/tasks/*/mutable_scopes",
    ),
    "/workflows/*/tasks/*/inputs": (
        "delegation",
        "task",
        "inputs",
        "/tasks/*/inputs",
    ),
    "/workflows/*/tasks/*/projections": (
        "delegation",
        "task",
        "projections",
        "/tasks/*/projections",
    ),
    "/workflows/*/tasks/*/request/mode": ("delegation", "request", "mode", None),
    "/workflows/*/terminal_mode": ("delegation", "workflow", "terminal_mode", None),
    "/workflows/*/terminal_task_id": (
        "delegation",
        "workflow",
        "terminal_task_id",
        None,
    ),
    "/workflows/*/dependency_edges": (
        "delegation",
        "workflow",
        "dependency_edges",
        "/dependency_edges",
    ),
    "/workflows/*/gate_edges": (
        "delegation",
        "workflow",
        "gate_edges",
        "/gate_edges",
    ),
    "/workflows/*/tasks/*/settlement": ("delegation", "task", "settlement", None),
    "/transitions": ("delegation", "descriptor", "transitions", "/transitions"),
}
SCHEMA_PATHS = set(SCHEMA_FIELD_SOURCES)

PARSE_POLICY = "weightclass.delegation_v2_schema:parse_delegation_policy_v2"
VALIDATE_BINDING = "weightclass.delegation_v2_schema:validate_delegation_v2_binding"
VALIDATE_GRAPH = "weightclass.delegation_v2_graph:validate_delegation_v2_graph"
VALIDATE_SCOPES = "weightclass.delegation_v2_permissions:validate_write_scope_conflicts"
TASK_REQUESTS_WRITE = "weightclass.delegation_v2_permissions:task_requests_write"
COMPILE_DELEGATION = "weightclass.delegation_v2_compile:compile_delegation_v2"
COMPILE_NATIVE = "weightclass.native_v2_compile:compile_native_v2"
COMPLETE_TREE_TEST = (
    "tests.test_delegation_v2_schema.DelegationV2SchemaTests."
    "test_complete_parse_tree_preserves_every_policy_and_manifest_field"
)
GRAPH_CYCLE_TEST = (
    "tests.test_delegation_v2_graph.DelegationV2GraphTests."
    "test_rejects_duplicate_pairs_self_edges_and_combined_cycles"
)
GATE_TEST = (
    "tests.test_delegation_v2_graph.DelegationV2GraphTests."
    "test_gate_owned_explicit_output_allows_shared_producer"
)
RAW_GRAPH_TEST = (
    "tests.test_delegation_v2_graph.DelegationV2GraphTests."
    "test_raw_independent_permits_disconnected_roots_fanout_and_join"
)
SYNTHESIS_TEST = (
    "tests.test_delegation_v2_graph.DelegationV2GraphTests."
    "test_synthesized_terminal_is_sole_sink_reached_by_every_task"
)
PAIR_TEST = (
    "tests.test_delegation_v2_schema.DelegationV2SchemaTests."
    "test_task_model_effort_must_be_allowed_by_destination_profile"
)
CAPABILITY_TEST = (
    "tests.test_delegation_v2_schema.DelegationV2SchemaTests."
    "test_adapter_must_cover_task_capabilities"
)

EXPECTED_BINDINGS: dict[tuple[str, str], tuple[str | None, str | None, str | None] | None] = {
    ("Orca", "run_task_dispatch_provenance"): (
        "/workflows/*/requested_run_id",
        PARSE_POLICY,
        COMPLETE_TREE_TEST,
    ),
    ("Orca", "requested_ownership"): (
        "/workflows/*/tasks/*/owner_role",
        PARSE_POLICY,
        COMPLETE_TREE_TEST,
    ),
    ("Orca", "dag_readiness"): (
        "/workflows/*/dependency_edges",
        VALIDATE_GRAPH,
        GRAPH_CYCLE_TEST,
    ),
    ("Orca", "typed_gates"): (
        "/workflows/*/gate_edges",
        VALIDATE_GRAPH,
        GATE_TEST,
    ),
    ("Orca", "completion_vs_settlement"): (
        "/workflows/*/tasks/*/settlement",
        PARSE_POLICY,
        COMPLETE_TREE_TEST,
    ),
    ("Orca", "direct_child_exit"): (
        None,
        "weightclass.delegation_v2_runtime:run_delegation_v2_runtime",
        "tests.test_delegation_v2_runtime.DelegationV2RuntimeTests."
        "test_returns_the_direct_child_exit_without_task_success_claims",
    ),
    ("Codex", "bounded_independent_workstreams"): (
        "/workflows/*/concurrency",
        PARSE_POLICY,
        "tests.test_delegation_v2_schema.DelegationV2SchemaTests."
        "test_workflow_concurrency_is_bounded_and_bool_safe",
    ),
    ("Codex", "projections"): (
        "/workflows/*/tasks/*/projections",
        VALIDATE_GRAPH,
        "tests.test_delegation_v2_projection.DelegationV2ProjectionTests."
        "test_binds_exact_owned_output_of_strict_ancestor",
    ),
    ("Codex", "synthesis"): (
        "/workflows/*/terminal_task_id",
        VALIDATE_GRAPH,
        SYNTHESIS_TEST,
    ),
    ("Codex", "mutable_ownership"): (
        "/workflows/*/tasks/*/mutable_scopes",
        VALIDATE_SCOPES,
        "tests.test_delegation_v2_permissions.DelegationV2PermissionTests."
        "test_equal_and_both_ancestor_overlap_directions_fail_for_writers",
    ),
    ("Codex", "independent_model_effort"): (
        "/routes/*/model",
        COMPILE_NATIVE,
        "tests.test_native_v2_compile.NativeV2CompileTests.test_codex_builder_is_exact",
    ),
    ("Claude", "model_effort"): (
        "/workflows/*/tasks/*/effort",
        VALIDATE_BINDING,
        PAIR_TEST,
    ),
    ("Claude", "tools"): (
        "/workflows/*/tasks/*/request/tools",
        PARSE_POLICY,
        COMPLETE_TREE_TEST,
    ),
    ("Claude", "permissions"): (
        "/workflows/*/tasks/*/request/permissions",
        TASK_REQUESTS_WRITE,
        "tests.test_delegation_v2_permissions.DelegationV2PermissionTests."
        "test_closed_write_predicate_rows",
    ),
    ("Claude", "turns"): (
        "/workflows/*/tasks/*/turns",
        PARSE_POLICY,
        "tests.test_delegation_v2_schema.DelegationV2SchemaTests."
        "test_task_turns_are_bounded_and_bool_safe",
    ),
    ("Claude", "hooks"): None,
    ("Claude", "memory"): None,
    ("Claude", "background"): None,
    ("Claude", "peer_messaging"): None,
    ("Cursor", "allowed_pair_pool"): (
        "/profiles/*/allowed_model_effort_pairs",
        VALIDATE_BINDING,
        PAIR_TEST,
    ),
    ("Cursor", "mode_binding"): (
        "/workflows/*/tasks/*/request/mode",
        VALIDATE_GRAPH,
        "tests.test_delegation_v2_graph.DelegationV2GraphTests."
        "test_raw_mode_requires_null_terminal_and_no_synthesizer",
    ),
    ("LangGraph", "typed_transitions"): (
        "/transitions",
        COMPILE_DELEGATION,
        "tests.test_delegation_v2_compile.DelegationV2CompileTests."
        "test_dependency_and_gate_each_get_distinct_transition",
    ),
    ("LangGraph", "typed_gates"): (
        "/workflows/*/gate_edges",
        VALIDATE_GRAPH,
        GATE_TEST,
    ),
    ("LangGraph", "typed_projections"): (
        "/workflows/*/tasks/*/projections",
        VALIDATE_GRAPH,
        "tests.test_delegation_v2_projection.DelegationV2ProjectionTests."
        "test_rejects_count_missing_duplicate_binding_type_and_ownership",
    ),
    ("LangGraph", "persistence"): None,
    ("AutoGen", "fanout_join"): (
        "/workflows/*/dependency_edges",
        VALIDATE_GRAPH,
        RAW_GRAPH_TEST,
    ),
    ("AutoGen", "synthesis_termination"): (
        "/workflows/*/terminal_mode",
        VALIDATE_GRAPH,
        SYNTHESIS_TEST,
    ),
    ("AutoGen", "selection_filter"): None,
    ("CrewAI", "topology_modes"): None,
    ("CrewAI", "hitl_gates"): (
        "/workflows/*/gate_edges",
        VALIDATE_GRAPH,
        GATE_TEST,
    ),
    ("OpenHands", "provider_neutral_capabilities"): (
        "/workflows/*/tasks/*/capabilities",
        VALIDATE_BINDING,
        CAPABILITY_TEST,
    ),
    ("OpenHands", "provider_neutral_workspace"): (
        "/workflows/*/tasks/*/worktree",
        VALIDATE_SCOPES,
        "tests.test_delegation_v2_permissions.DelegationV2PermissionTests."
        "test_siblings_and_nonwriters_do_not_conflict",
    ),
}


def _load() -> list[dict[str, Any]]:
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise AssertionError("traceability fixture must be a list")
    return value


def _validate_schema_sources_against_frozen_catalogs() -> None:
    fixture_root = FIXTURE.parent
    catalogs = {
        "native": json.loads(
            (fixture_root / "native_v2_schema" / "contract.json").read_text(encoding="utf-8")
        ),
        "delegation": json.loads(
            (fixture_root / "delegation_v2_schema" / "contract.json").read_text(encoding="utf-8")
        ),
    }
    relationships = {
        ("native", "policy", "routes"): ("route", True),
        ("delegation", "policy", "profiles"): ("profile", True),
        ("delegation", "policy", "workflows"): ("workflow", True),
        ("delegation", "workflow", "tasks"): ("task", True),
        ("delegation", "task", "request"): ("request", False),
    }

    def catalog_contains_path(catalog_name: str, schema_path: str) -> bool:
        catalog = catalogs[catalog_name]
        tokens = schema_path.removeprefix("/").split("/")
        if not tokens or any(not token for token in tokens):
            return False
        first = tokens[0]
        if first in catalog["objects"]["policy"]["keys"]:
            object_name = "policy"
        elif first in catalog["objects"]["descriptor"]["keys"]:
            object_name = "descriptor"
        else:
            return False
        index = 0
        while index < len(tokens):
            field_name = tokens[index]
            if field_name == "*" or field_name not in catalog["objects"][object_name]["keys"]:
                return False
            index += 1
            if index == len(tokens):
                return True
            relationship = relationships.get((catalog_name, object_name, field_name))
            if relationship is None:
                return False
            object_name, is_collection = relationship
            if is_collection:
                if index == len(tokens) or tokens[index] != "*":
                    return False
                index += 1
            elif tokens[index] == "*":
                return False
        return False

    for schema_path, source in SCHEMA_FIELD_SOURCES.items():
        catalog_name, object_name, field_name, list_path = source
        catalog = catalogs[catalog_name]
        if not catalog_contains_path(catalog_name, schema_path):
            raise AssertionError(f"schema path is absent from frozen catalog: {schema_path}")
        keys = catalog["objects"][object_name]["keys"]
        if field_name not in keys:
            raise AssertionError(f"schema field is absent from frozen catalog: {schema_path}")
        if list_path is not None and list_path not in catalog["list_paths"]:
            raise AssertionError(f"list path is absent from frozen catalog: {schema_path}")


def _validate(rows: list[dict[str, Any]]) -> None:
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if set(row) != REQUIRED_FIELDS:
            raise AssertionError("traceability row keys")
        source = row["source"]
        capability = row["capability"]
        status = row["status"]
        non_goal = row["non_goal"]
        scalar_values = (source, capability, status, non_goal)
        if not all(isinstance(item, str) and item for item in scalar_values):
            raise AssertionError("traceability scalar")
        identity = (source, capability)
        if identity in seen:
            raise AssertionError("duplicate traceability row")
        seen.add(identity)
        if identity not in EXPECTED_BINDINGS:
            raise AssertionError("unknown orchestration capability")
        if status not in VALID_STATUSES:
            raise AssertionError("traceability status")
        if EXPECTED_STATUS_BY_IDENTITY.get(identity) != status:
            raise AssertionError("orchestration capability status overclaim")
        actual_binding = (
            row["schema_path"],
            row["validator_symbol"],
            row["test_symbol"],
        )
        expected_binding = EXPECTED_BINDINGS[identity]
        if expected_binding is None:
            if actual_binding != (None, None, None):
                raise AssertionError("unbound capability claimed a router binding")
            continue
        if actual_binding != expected_binding:
            raise AssertionError("orchestration capability binding mismatch")
        if identity in NOT_ADOPTED_REQUIRED and status != "not_adopted":
            raise AssertionError("unsupported orchestration overclaim")
        if row["schema_path"] is not None and row["schema_path"] not in SCHEMA_PATHS:
            raise AssertionError("unknown schema path")
        module_name, separator, symbol_name = row["validator_symbol"].partition(":")
        validator = getattr(importlib.import_module(module_name), symbol_name, None)
        if not separator or not callable(validator):
            raise AssertionError("unknown validator symbol")
        test_parts = row["test_symbol"].split(".")
        if len(test_parts) < 4:
            raise AssertionError("unknown or ambiguous test symbol")
        test_module = importlib.import_module(".".join(test_parts[:-2]))
        test_class = getattr(test_module, test_parts[-2], None)
        test_method = getattr(test_class, test_parts[-1], None)
        valid_test = (
            isinstance(test_class, type)
            and issubclass(test_class, unittest.TestCase)
            and test_parts[-1].startswith("test_")
            and test_parts[-1] in test_class.__dict__
            and callable(test_method)
            and test_parts[-1] in unittest.defaultTestLoader.getTestCaseNames(test_class)
        )
        if not valid_test:
            raise AssertionError("unknown or ambiguous test symbol")


class OrchestrationTraceabilityTests(unittest.TestCase):
    def test_schema_paths_are_derived_from_frozen_contract_catalogs(self) -> None:
        _validate_schema_sources_against_frozen_catalogs()

    def test_fabricated_full_schema_path_fails_even_with_a_real_leaf_binding(self) -> None:
        source: SchemaSource = (
            "delegation",
            "descriptor",
            "transitions",
            "/transitions",
        )
        with (
            mock.patch.dict(
                SCHEMA_FIELD_SOURCES,
                {"/fabricated/not-a-contract-path": source},
            ),
            self.assertRaises(AssertionError),
        ):
            _validate_schema_sources_against_frozen_catalogs()

    def test_fixture_rows_resolve_to_schema_validators_and_collected_tests(self) -> None:
        _validate(_load())

    def test_rejects_nonexistent_path_symbol_and_test(self) -> None:
        row = _load()[0]
        for field, value in (
            ("schema_path", "/does/not/exist"),
            ("validator_symbol", "weightclass.delegation_v2_graph:missing"),
            ("test_symbol", "tests.test_delegation_v2_graph.Missing.test_missing"),
        ):
            changed = dict(row)
            changed[field] = value
            with self.assertRaises(AssertionError):
                _validate([changed])

    def test_rejects_noncallable_uncollected_and_unrelated_bindings(self) -> None:
        row = _load()[0]
        mutations = (
            dict(row, validator_symbol="weightclass.delegation_v2_graph:__doc__"),
            dict(
                row,
                test_symbol=("tests.test_delegation_v2_graph.DelegationV2GraphTests.run"),
            ),
            dict(
                row,
                validator_symbol=("weightclass.delegation_v2_graph:validate_delegation_v2_graph"),
                test_symbol=(
                    "tests.test_delegation_v2_graph.DelegationV2GraphTests."
                    "test_rejects_duplicate_pairs_self_edges_and_combined_cycles"
                ),
            ),
        )
        for changed in mutations:
            with self.subTest(changed=changed), self.assertRaises(AssertionError):
                _validate([changed])

    def test_rejects_unsupported_overclaim_and_missing_non_goal(self) -> None:
        row = next(item for item in _load() if item["status"] == "not_adopted")
        overclaim = dict(row)
        overclaim.update(
            status="structurally_enforced",
            schema_path="/workflows/*/tasks/*/request/mode",
            validator_symbol="weightclass.delegation_v2_graph:validate_delegation_v2_graph",
            test_symbol=(
                "tests.test_delegation_v2_graph.DelegationV2GraphTests."
                "test_raw_independent_permits_disconnected_roots_fanout_and_join"
            ),
        )
        with self.assertRaises(AssertionError):
            _validate([overclaim])
        missing = dict(_load()[0], non_goal="")
        with self.assertRaises(AssertionError):
            _validate([missing])

    def test_rejects_invalid_adopted_status(self) -> None:
        row = dict(_load()[0], status="adopted")
        with self.assertRaises(AssertionError):
            _validate([row])

    def test_rejects_runtime_owned_feature_promoted_to_structural_enforcement(self) -> None:
        row = next(
            item for item in _load() if item["source"] == "Claude" and item["capability"] == "hooks"
        )
        overclaim = dict(row, status="structurally_enforced")
        with self.assertRaises(AssertionError):
            _validate([overclaim])

    def test_runtime_owned_features_claim_no_router_binding(self) -> None:
        runtime_owned = [row for row in _load() if row["status"] == "runtime_owned"]
        self.assertTrue(runtime_owned)
        for row in runtime_owned:
            with self.subTest(capability=row["capability"]):
                self.assertIsNone(row["schema_path"])
                self.assertIsNone(row["validator_symbol"])
                self.assertIsNone(row["test_symbol"])

    def test_completion_and_settlement_remain_runtime_unverified(self) -> None:
        row = next(
            item
            for item in _load()
            if item["source"] == "Orca" and item["capability"] == "completion_vs_settlement"
        )
        self.assertEqual(row["status"], "runtime_unverified")
        self.assertEqual(
            row["validator_symbol"],
            "weightclass.delegation_v2_schema:parse_delegation_policy_v2",
        )

    def test_required_source_capabilities_are_complete(self) -> None:
        actual: dict[str, set[str]] = {}
        for row in _load():
            actual.setdefault(row["source"], set()).add(row["capability"])
        required = {
            "Orca": {
                "run_task_dispatch_provenance",
                "requested_ownership",
                "dag_readiness",
                "typed_gates",
                "completion_vs_settlement",
                "direct_child_exit",
            },
            "Codex": {
                "bounded_independent_workstreams",
                "projections",
                "synthesis",
                "mutable_ownership",
                "independent_model_effort",
            },
            "Claude": {
                "model_effort",
                "tools",
                "permissions",
                "turns",
                "hooks",
                "memory",
                "background",
                "peer_messaging",
            },
            "Cursor": {"allowed_pair_pool", "mode_binding"},
            "LangGraph": {"typed_transitions", "typed_gates", "typed_projections", "persistence"},
            "AutoGen": {"fanout_join", "synthesis_termination", "selection_filter"},
            "CrewAI": {"topology_modes", "hitl_gates"},
            "OpenHands": {"provider_neutral_capabilities", "provider_neutral_workspace"},
        }
        self.assertEqual(required, actual)


if __name__ == "__main__":
    unittest.main()
