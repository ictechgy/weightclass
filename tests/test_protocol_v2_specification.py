import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs/protocol-v2-specification.md"
FIXTURES = ROOT / "tests/fixtures"


class ProtocolV2SpecificationTests(unittest.TestCase):
    VALIDATION_ORDER = [
        "exact_keys_types_scalar_collection_bounds",
        "local_uniqueness_and_enums",
        "global_namespaces",
        "references_and_producer_ownership",
        "edge_pair_uniqueness_and_gate_ownership",
        "combined_dag_cycle_and_topology",
        "terminal_validation",
        "input_projection_binding_and_types",
        "strict_ancestry",
        "write_predicate_and_scope_conflicts",
        "structural_transitions",
        "directional_grants_and_unused_grants",
        "canonical_sorting_and_descriptor_size",
        "fingerprint_binding",
    ]

    def test_normative_contract_artifacts_exist(self) -> None:
        required = (
            SPEC,
            FIXTURES / "native_v2_schema/contract.json",
            FIXTURES / "native_v2_schema/boundaries.json",
            FIXTURES / "delegation_v2_schema/contract.json",
            FIXTURES / "delegation_v2_schema/boundaries.json",
        )
        self.assertEqual([path for path in required if not path.is_file()], [])

    def test_contract_catalogs_are_complete_and_machine_checkable(self) -> None:
        expected = {
            "native_v2_schema": {
                "policy",
                "profile",
                "target",
                "builder",
                "allowed_pair",
                "route",
                "eligibility",
                "profile_grant",
                "vendor_grant",
                "native_authorization",
                "native_transition",
                "descriptor",
            },
            "delegation_v2_schema": {
                "policy",
                "manifest",
                "runtime",
                "provider_family_mapping",
                "eligibility",
                "profile",
                "allowed_pair",
                "adapter",
                "workflow",
                "task",
                "request",
                "permission",
                "worktree",
                "tool",
                "settlement",
                "cleanup",
                "input",
                "projection",
                "output",
                "artifact",
                "dependency",
                "gate_output",
                "gate_predicate",
                "gate",
                "transition",
                "endpoint",
                "authorization",
                "grant",
                "descriptor",
            },
        }
        for directory, object_names in expected.items():
            with self.subTest(directory=directory):
                contract = json.loads((FIXTURES / directory / "contract.json").read_text())
                self.assertEqual(
                    set(contract),
                    {"meta", "objects", "list_paths", "validation_order"},
                )
                self.assertEqual(set(contract["objects"]), object_names)
                order = contract["validation_order"]
                self.assertIn(order, [list(range(1, 15)), self.VALIDATION_ORDER])
                for name, item in contract["objects"].items():
                    self.assertEqual(
                        set(item),
                        {
                            "keys",
                            "required",
                            "nullable",
                            "fields",
                            "namespace",
                            "ownership",
                            "references",
                            "invariants",
                            "projection",
                            "ordering",
                            "status",
                        },
                        name,
                    )
                    self.assertEqual(set(item["keys"]), set(item["fields"]), name)
                    self.assertEqual(
                        set(item["required"]) | set(item["nullable"]), set(item["keys"]), name
                    )
                    self.assertIn(
                        item["status"],
                        {
                            "caller_requested",
                            "structurally_enforced",
                            "direct_child_observed",
                            "runtime_unverified",
                        },
                    )

    def test_native_descriptor_endpoints_and_list_paths_are_unambiguous(self) -> None:
        contract = json.loads((FIXTURES / "native_v2_schema/contract.json").read_text())
        descriptor = contract["objects"]["descriptor"]
        self.assertEqual(
            set(descriptor["keys"]),
            {
                "descriptor_schema_version",
                "compiler_contract_version",
                "source",
                "route",
                "target",
                "selection",
                "argv",
                "transition",
                "route_fingerprint",
            },
        )
        transition = contract["objects"]["native_transition"]
        self.assertEqual(
            set(transition["keys"]),
            {
                "id",
                "source_profile",
                "destination_profile",
                "changed_dimensions",
                "authorizations",
            },
        )
        self.assertEqual(
            contract["list_paths"],
            {
                "/route/eligibility": "source_vendor,source_profile_id,tier",
                "/target/allowed_model_effort_pairs": "model,effort",
                "/argv": "ordered",
                "/transition/changed_dimensions": "profile,vendor",
                "/transition/authorizations": "aligned-with-changed-dimensions",
            },
        )

    def test_delegation_profiles_provenance_and_list_paths_are_complete(self) -> None:
        contract = json.loads((FIXTURES / "delegation_v2_schema/contract.json").read_text())
        profile = contract["objects"]["profile"]
        self.assertEqual(
            set(profile["keys"]),
            {
                "id",
                "provider",
                "intended_recipient",
                "billing_boundary",
                "transport",
                "account_profile",
                "capabilities",
                "allowed_model_effort_pairs",
            },
        )
        workflow = contract["objects"]["workflow"]
        self.assertIn("requested_run_id", workflow["keys"])
        self.assertIn("eligibility", workflow["keys"])
        task = contract["objects"]["task"]
        self.assertTrue(
            {
                "requested_task_id",
                "requested_dispatch_id",
                "owner_role",
                "destination_profile_id",
                "model",
                "effort",
            }
            <= set(task["keys"])
        )
        required_paths = {
            "/workflow/eligibility",
            "/runtime/adapter/supported_transports",
            "/runtime/adapter/capabilities",
            "/profiles",
            "/profiles/*/capabilities",
            "/profiles/*/allowed_model_effort_pairs",
            "/tasks",
            "/tasks/*/inputs",
            "/tasks/*/projections",
            "/tasks/*/outputs",
            "/tasks/*/outputs/*/artifacts",
            "/tasks/*/mutable_scopes",
            "/tasks/*/capabilities",
            "/tasks/*/request/permissions",
            "/tasks/*/request/tools",
            "/dependency_edges",
            "/gate_edges",
            "/transitions",
            "/transitions/*/changed_dimensions",
            "/transitions/*/authorizations",
            "/grants/provider",
            "/grants/intended_recipient",
            "/grants/billing_boundary",
            "/grants/transport",
            "/grants/profile",
            "/argv",
        }
        self.assertEqual(set(contract["list_paths"]), required_paths)

    def test_boundary_catalogs_cover_required_classes(self) -> None:
        required = {"lower", "upper", "upper_plus_one", "aggregate", "duplicate", "wrong_type"}
        for directory in ("native_v2_schema", "delegation_v2_schema"):
            cases = json.loads((FIXTURES / directory / "boundaries.json").read_text())
            self.assertEqual(set(cases), {"limits", "coverage", "cases"})
            self.assertEqual(set(cases["coverage"]), set(cases["limits"]))
            for limit, classes in cases["coverage"].items():
                self.assertEqual(set(classes), required, limit)
            self.assertTrue(required <= {case["class"] for case in cases["cases"]})
            for case in cases["cases"]:
                self.assertEqual(set(case), {"id", "class", "stage", "expected_error", "exit_code"})
                self.assertIn(case["stage"], range(1, 15))
                self.assertEqual(case["expected_error"], "invalid_input")
                self.assertEqual(case["exit_code"], 2)

    def test_catalogs_freeze_bool_exclusion_projection_and_list_order(self) -> None:
        for directory in ("native_v2_schema", "delegation_v2_schema"):
            contract = json.loads((FIXTURES / directory / "contract.json").read_text())
            rendered = json.dumps(contract, sort_keys=True)
            self.assertIn("integer-not-bool", rendered)
            self.assertNotIn("implementation-defined", rendered)
            for name, item in contract["objects"].items():
                self.assertTrue(item["projection"], name)
                self.assertTrue(item["ordering"], name)
                self.assertTrue(item["namespace"], name)
                self.assertTrue(item["ownership"], name)
                for field, rule in item["fields"].items():
                    self.assertIsInstance(rule, str, f"{name}.{field}")
                    self.assertTrue(rule, f"{name}.{field}")

    def test_specification_freezes_privacy_precedence_and_planning_status(self) -> None:
        text = SPEC.read_text(encoding="utf-8")
        required_phrases = (
            "max_rounds/ITERATE",
            "not consensus-approved",
            "ensure_ascii=True",
            "allow_nan=False",
            "WCD2",
            "ValidatedTaskV2",
            "route_fingerprint",
            "Protocol 2 is categorically ineligible",
            "No production v2 dispatch",
            "value-free",
            "14-stage validation order",
        )
        for phrase in required_phrases:
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
