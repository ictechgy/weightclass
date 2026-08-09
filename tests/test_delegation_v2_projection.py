import unittest
from typing import Any

from tests.test_delegation_v2_graph import parsed_workflow, workflow_with_tasks
from weightclass.delegation_v2_graph import validate_delegation_v2_graph
from weightclass.delegation_v2_schema import DelegationV2InvalidInputError


class DelegationV2ProjectionTests(unittest.TestCase):
    def _valid(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        raw, workflow = workflow_with_tasks("producer", "consumer")
        workflow["tasks"][0]["outputs"] = [{"id": "result", "type": "json", "artifacts": []}]
        consumer = workflow["tasks"][1]
        consumer["inputs"] = [{"id": "source", "type": "json"}]
        consumer["projections"] = [
            {
                "id": "projection",
                "input_id": "source",
                "producer_task_id": "producer",
                "producer_output_id": "result",
            }
        ]
        workflow["dependency_edges"] = [
            {"id": "d", "from_task_id": "producer", "to_task_id": "consumer"}
        ]
        return raw, workflow, consumer

    def test_binds_exact_owned_output_of_strict_ancestor(self) -> None:
        raw, _, _ = self._valid()
        validate_delegation_v2_graph(parsed_workflow(raw))

    def test_rejects_count_missing_duplicate_binding_type_and_ownership(self) -> None:
        for mutation in ("count", "missing", "duplicate", "type", "owner"):
            raw, workflow, consumer = self._valid()
            if mutation == "count":
                consumer["projections"] = []
            elif mutation == "missing":
                consumer["projections"][0]["input_id"] = "unknown"
            elif mutation == "duplicate":
                consumer["inputs"].append({"id": "other", "type": "json"})
                duplicate = dict(consumer["projections"][0])
                duplicate["id"] = "projection-two"
                consumer["projections"].append(duplicate)
            elif mutation == "type":
                consumer["inputs"][0]["type"] = "text"
            else:
                workflow["tasks"][1]["outputs"] = [{"id": "other", "type": "json", "artifacts": []}]
                consumer["projections"][0]["producer_output_id"] = "other"
            with self.subTest(mutation=mutation), self.assertRaises(DelegationV2InvalidInputError):
                validate_delegation_v2_graph(parsed_workflow(raw))

    def test_rejects_nonancestor_even_when_producer_exists(self) -> None:
        raw, workflow, consumer = self._valid()
        workflow["dependency_edges"] = []
        with self.assertRaises(DelegationV2InvalidInputError):
            validate_delegation_v2_graph(parsed_workflow(raw))

    def test_global_output_artifact_and_projection_namespaces(self) -> None:
        raw, workflow = workflow_with_tasks("a", "b")
        for task in workflow["tasks"]:
            task["outputs"] = [
                {
                    "id": "same-output",
                    "type": "artifact_ref",
                    "artifacts": [{"id": "same-artifact", "media_type": "text/plain"}],
                }
            ]
        parsed = parsed_workflow(raw)
        with self.assertRaises(DelegationV2InvalidInputError):
            validate_delegation_v2_graph(parsed)

        raw, workflow = workflow_with_tasks("producer", "left", "right")
        workflow["tasks"][0]["outputs"] = [{"id": "result", "type": "json", "artifacts": []}]
        workflow["dependency_edges"] = [
            {"id": "left-edge", "from_task_id": "producer", "to_task_id": "left"},
            {"id": "right-edge", "from_task_id": "producer", "to_task_id": "right"},
        ]
        for task in workflow["tasks"][1:]:
            task["inputs"] = [{"id": f"{task['id']}-input", "type": "json"}]
            task["projections"] = [
                {
                    "id": "same-projection",
                    "input_id": f"{task['id']}-input",
                    "producer_task_id": "producer",
                    "producer_output_id": "result",
                }
            ]
        parsed = parsed_workflow(raw)
        with self.assertRaises(DelegationV2InvalidInputError):
            validate_delegation_v2_graph(parsed)

    def test_input_and_projection_count_bounds(self) -> None:
        raw, workflow, consumer = self._valid()
        producer = workflow["tasks"][0]
        producer["outputs"] = []
        consumer["inputs"] = []
        consumer["projections"] = []
        for index in range(32):
            producer["outputs"].append({"id": f"out-{index}", "type": "json", "artifacts": []})
            consumer["inputs"].append({"id": f"in-{index}", "type": "json"})
            consumer["projections"].append(
                {
                    "id": f"projection-{index}",
                    "input_id": f"in-{index}",
                    "producer_task_id": "producer",
                    "producer_output_id": f"out-{index}",
                }
            )
        validate_delegation_v2_graph(parsed_workflow(raw))
        consumer["inputs"].append({"id": "too-many", "type": "json"})
        with self.assertRaises(DelegationV2InvalidInputError):
            parsed_workflow(raw)


if __name__ == "__main__":
    unittest.main()
