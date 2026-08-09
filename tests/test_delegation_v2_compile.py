import copy
import hashlib
import json
import unittest
from pathlib import Path
from typing import Any, cast

from tests.test_delegation_v2_schema import valid_manifest, valid_policy
from weightclass.delegation_v2_compile import compile_delegation_v2
from weightclass.delegation_v2_schema import (
    parse_delegation_manifest_v2,
    parse_delegation_policy_v2,
)
from weightclass.native_v2_types import CompiledExecutionV2


def compilable_inputs() -> tuple[dict[str, Any], dict[str, Any]]:
    policy = cast(dict[str, Any], valid_policy())
    grants = policy["workflows"][0]["grants"]
    values = {
        "provider": ("openai", "anthropic"),
        "intended_recipient": ("r1", "r2"),
        "billing_boundary": ("b1", "b2"),
        "profile": ("a1", "a2"),
    }
    for dimension, (source, destination) in values.items():
        grants[dimension] = [{"id": f"grant-{dimension}", "from": source, "to": destination}]
    return policy, valid_manifest()


def compile_raw(policy: dict[str, Any], manifest: dict[str, Any]) -> CompiledExecutionV2:
    return compile_delegation_v2(
        parse_delegation_policy_v2(policy),
        parse_delegation_manifest_v2(manifest),
        source_vendor_family="codex",
        source_profile_id="source",
        tier="low",
        runtime_path="/owned/runtime",
    )


class DelegationV2CompileTests(unittest.TestCase):
    def test_compiles_immutable_truth_and_exact_source_root_transition(self) -> None:
        policy, manifest = compilable_inputs()
        compiled = compile_raw(policy, manifest)
        descriptor = json.loads(compiled.canonical_descriptor_bytes)
        self.assertEqual(
            compiled.argv, ("/owned/runtime", "--weightclass-delegation-protocol", "2")
        )
        self.assertEqual(compiled.executable, "/owned/runtime")
        self.assertEqual((compiled.transport, compiled.transport_version), ("wcd2_stdin", 2))
        self.assertEqual(
            list(descriptor["transitions"][0]["from_endpoint"]),
            [
                "billing_boundary",
                "intended_recipient",
                "profile",
                "provider",
                "router_profile_id",
                "transport",
            ],
        )
        transition = descriptor["transitions"][0]
        self.assertEqual(transition["kind"], "source_root")
        self.assertEqual(
            transition["changed_dimensions"],
            ["provider", "intended_recipient", "billing_boundary", "profile"],
        )
        self.assertEqual(transition["from_endpoint"]["router_profile_id"], "source")
        self.assertEqual(transition["from_endpoint"]["profile"], "a1")
        self.assertFalse(hasattr(compiled, "policy"))
        self.assertFalse(hasattr(compiled, "task"))
        self.assertFalse(hasattr(compiled, "descriptor"))

    def test_fingerprint_payload_excludes_only_fingerprint_and_is_order_independent(self) -> None:
        policy, manifest = compilable_inputs()
        first = compile_raw(policy, manifest)
        policy["profiles"].reverse()
        policy["workflows"][0]["grants"]["provider"].reverse()
        second = compile_raw(policy, manifest)
        self.assertEqual(first, second)
        payload = json.loads(first.fingerprint_payload_bytes)
        descriptor = json.loads(first.canonical_descriptor_bytes)
        self.assertNotIn("route_fingerprint", payload)
        self.assertEqual(set(descriptor) - set(payload), {"route_fingerprint"})
        self.assertEqual(
            first.route_fingerprint,
            "sha256:" + hashlib.sha256(first.fingerprint_payload_bytes).hexdigest(),
        )

    def test_descriptor_matches_canonical_golden(self) -> None:
        policy, manifest = compilable_inputs()
        compiled = compile_raw(policy, manifest)
        golden = (
            Path(__file__).parent / "fixtures/delegation_v2_schema/g07_golden_descriptor.json"
        ).read_bytes()
        self.assertEqual(compiled.canonical_descriptor_bytes + b"\n", golden)

    def test_dependency_and_gate_each_get_distinct_transition(self) -> None:
        policy, manifest = compilable_inputs()
        workflow = policy["workflows"][0]
        template = workflow["tasks"][0]
        second = copy.deepcopy(template)
        second["id"] = "task-two"
        second["requested_task_id"] = "requested-two"
        second["requested_dispatch_id"] = "dispatch-two"
        workflow["tasks"].append(second)
        workflow["dependency_edges"] = [
            {"id": "dep", "from_task_id": "task", "to_task_id": "task-two"}
        ]
        workflow["gate_edges"] = [
            {
                "id": "gate",
                "from_task_id": "task",
                "to_task_id": "task-two",
                "output": {
                    "producer_task_id": "task",
                    "producer_output_id": "approval",
                    "required_type": "weightclass.gate/v1",
                },
                "predicate": {"operator": "equals", "value": "approved"},
            }
        ]
        template["outputs"] = [{"id": "approval", "type": "text", "artifacts": []}]
        descriptor = json.loads(compile_raw(policy, manifest).canonical_descriptor_bytes)
        self.assertEqual(
            {(item["kind"], item["id"]) for item in descriptor["transitions"]},
            {("source_root", "source-root-task"), ("dependency", "dep"), ("gate", "gate")},
        )


if __name__ == "__main__":
    unittest.main()
