import copy
import json
import unittest
from pathlib import Path
from typing import Any, cast

from tests.test_delegation_v2_schema import valid_policy
from weightclass.delegation_v2_graph import validate_delegation_v2_graph
from weightclass.delegation_v2_schema import (
    DelegationV2InvalidInputError,
    parse_delegation_policy_v2,
)
from weightclass.delegation_v2_types import DelegationWorkflowV2


def workflow_with_tasks(*task_ids: str) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = cast(dict[str, Any], valid_policy())
    workflow = raw["workflows"][0]
    template = workflow["tasks"][0]
    workflow["tasks"] = []
    for task_id in task_ids:
        task = copy.deepcopy(template)
        task["id"] = task_id
        task["requested_task_id"] = f"requested-{task_id}"
        task["requested_dispatch_id"] = f"dispatch-{task_id}"
        workflow["tasks"].append(task)
    return raw, workflow


def parsed_workflow(raw: dict[str, Any]) -> DelegationWorkflowV2:
    return parse_delegation_policy_v2(raw).workflows[0]


class DelegationV2GraphTests(unittest.TestCase):
    def test_g06_fixture_catalog_names_every_frozen_graph_stage(self) -> None:
        fixture = json.loads(
            (
                Path(__file__).parent / "fixtures/delegation_v2_schema/g06_graph_cases.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(fixture["schema"], "weightclass.delegation-v2-g06-cases/v1")
        self.assertEqual({case["stage"] for case in fixture["cases"]}, set(range(3, 11)))
        self.assertTrue(all(case["expected_error"] == "invalid_input" for case in fixture["cases"]))

    def test_raw_independent_permits_disconnected_roots_fanout_and_join(self) -> None:
        raw, workflow = workflow_with_tasks("root", "left", "right", "join", "island")
        workflow["dependency_edges"] = [
            {"id": "d1", "from_task_id": "root", "to_task_id": "left"},
            {"id": "d2", "from_task_id": "root", "to_task_id": "right"},
            {"id": "d3", "from_task_id": "left", "to_task_id": "join"},
            {"id": "d4", "from_task_id": "right", "to_task_id": "join"},
        ]
        graph = validate_delegation_v2_graph(parsed_workflow(raw))
        self.assertEqual(graph.topological_task_ids, ("island", "root", "left", "right", "join"))

    def test_rejects_unknown_reference_before_duplicate_edge_pair(self) -> None:
        raw, workflow = workflow_with_tasks("a", "b")
        workflow["dependency_edges"] = [
            {"id": "d1", "from_task_id": "missing", "to_task_id": "b"},
            {"id": "d2", "from_task_id": "a", "to_task_id": "b"},
            {"id": "d3", "from_task_id": "a", "to_task_id": "b"},
        ]
        with self.assertRaises(DelegationV2InvalidInputError):
            validate_delegation_v2_graph(parsed_workflow(raw))

    def test_rejects_duplicate_pairs_self_edges_and_combined_cycles(self) -> None:
        mutations = (
            [("a", "b"), ("a", "b")],
            [("a", "a")],
            [("a", "b"), ("b", "a")],
        )
        for pairs in mutations:
            with self.subTest(pairs=pairs):
                raw, workflow = workflow_with_tasks("a", "b")
                workflow["dependency_edges"] = [
                    {"id": f"d{i}", "from_task_id": source, "to_task_id": target}
                    for i, (source, target) in enumerate(pairs)
                ]
                with self.assertRaises(DelegationV2InvalidInputError):
                    validate_delegation_v2_graph(parsed_workflow(raw))

    def test_synthesized_terminal_is_sole_sink_reached_by_every_task(self) -> None:
        raw, workflow = workflow_with_tasks("a", "b", "final")
        workflow["terminal_mode"] = "synthesized"
        workflow["terminal_task_id"] = "final"
        workflow["tasks"][2]["request"]["mode"] = "synthesizer"
        workflow["dependency_edges"] = [
            {"id": "d1", "from_task_id": "a", "to_task_id": "final"},
            {"id": "d2", "from_task_id": "b", "to_task_id": "final"},
        ]
        validate_delegation_v2_graph(parsed_workflow(raw))
        for mutation in ("missing", "extra-synth", "unreachable", "outgoing"):
            broken = copy.deepcopy(raw)
            wf = broken["workflows"][0]
            if mutation == "missing":
                wf["terminal_task_id"] = "unknown"
            elif mutation == "extra-synth":
                wf["tasks"][0]["request"]["mode"] = "synthesizer"
            elif mutation == "unreachable":
                wf["dependency_edges"] = wf["dependency_edges"][:1]
            else:
                wf["dependency_edges"].append(
                    {"id": "d3", "from_task_id": "final", "to_task_id": "a"}
                )
            with self.subTest(mutation=mutation), self.assertRaises(DelegationV2InvalidInputError):
                validate_delegation_v2_graph(parsed_workflow(broken))

    def test_raw_mode_requires_null_terminal_and_no_synthesizer(self) -> None:
        for terminal, synth in (("a", False), (None, True)):
            raw, workflow = workflow_with_tasks("a")
            workflow["terminal_task_id"] = terminal
            if synth:
                workflow["tasks"][0]["request"]["mode"] = "synthesizer"
            with self.assertRaises(DelegationV2InvalidInputError):
                validate_delegation_v2_graph(parsed_workflow(raw))

    def test_gate_owned_explicit_output_allows_shared_producer(self) -> None:
        raw, workflow = workflow_with_tasks("producer", "one", "two")
        workflow["tasks"][0]["outputs"] = [{"id": "approval", "type": "text", "artifacts": []}]
        workflow["gate_edges"] = [
            {
                "id": f"g{i}",
                "from_task_id": "producer",
                "to_task_id": target,
                "output": {
                    "producer_task_id": "producer",
                    "producer_output_id": "approval",
                    "required_type": "weightclass.gate/v1",
                },
                "predicate": {"operator": "equals", "value": "approved"},
            }
            for i, target in enumerate(("one", "two"))
        ]
        validate_delegation_v2_graph(parsed_workflow(raw))
        workflow["gate_edges"][0]["output"]["producer_task_id"] = "one"
        with self.assertRaises(DelegationV2InvalidInputError):
            validate_delegation_v2_graph(parsed_workflow(raw))


if __name__ == "__main__":
    unittest.main()
