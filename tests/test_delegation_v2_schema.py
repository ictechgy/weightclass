import copy
import json
import unittest
from dataclasses import FrozenInstanceError
from unittest import mock

from weightclass.delegation_v2_schema import (
    DelegationV2InvalidInputError,
    parse_delegation_manifest_v2,
    parse_delegation_policy_v2,
    validate_delegation_v2_binding,
)


def valid_policy() -> dict[str, object]:
    return {
        "schema_version": 2,
        "manifest_schema_version": 2,
        "compiler_contract_version": 2,
        "runtime_protocol_version": 2,
        "frame_version": "WCD2",
        "profiles": [
            {
                "id": "source",
                "provider": "openai",
                "intended_recipient": "r1",
                "billing_boundary": "b1",
                "transport": "native",
                "account_profile": "a1",
                "capabilities": [],
                "allowed_model_effort_pairs": [{"model": "m1", "effort": "e1"}],
            },
            {
                "id": "destination",
                "provider": "anthropic",
                "intended_recipient": "r2",
                "billing_boundary": "b2",
                "transport": "native",
                "account_profile": "a2",
                "capabilities": [],
                "allowed_model_effort_pairs": [{"model": "m2", "effort": "e2"}],
            },
        ],
        "workflows": [
            {
                "id": "wf",
                "requested_run_id": "run",
                "eligibility": {
                    "source_vendor_family": "codex",
                    "source_profile_id": "source",
                    "tier": "low",
                },
                "terminal_mode": "raw_independent",
                "terminal_task_id": None,
                "concurrency": 1,
                "tasks": [
                    {
                        "id": "task",
                        "requested_task_id": "requested-task",
                        "requested_dispatch_id": "dispatch",
                        "owner_role": "worker",
                        "destination_profile_id": "destination",
                        "adapter_id": "adapter",
                        "model": "m2",
                        "effort": "e2",
                        "instruction": "work",
                        "request": {
                            "mode": "independent",
                            "permissions": {"filesystem": "read", "commands": "deny"},
                            "tools": [],
                        },
                        "worktree": {"mode": "read_only", "scope": None},
                        "settlement": {"mode": "requested_external_settlement"},
                        "cleanup": {"mode": "direct_child_only", "grace_seconds": 0},
                        "outputs": [],
                        "inputs": [],
                        "projections": [],
                        "mutable_scopes": [],
                        "capabilities": [],
                        "turns": 1,
                        "deadline_seconds": 1,
                        "attempts": 1,
                    }
                ],
                "dependency_edges": [],
                "gate_edges": [],
                "grants": {
                    "provider": [],
                    "intended_recipient": [],
                    "billing_boundary": [],
                    "transport": [],
                    "profile": [],
                },
            }
        ],
    }


def valid_manifest() -> dict[str, object]:
    return {
        "schema_version": 2,
        "provider_family_mappings": [
            {"provider": "anthropic", "vendor_family": "claude"},
            {"provider": "openai", "vendor_family": "codex"},
        ],
        "runtimes": [
            {
                "id": "rt",
                "path": "/owned/runtime",
                "adapter": {
                    "id": "adapter",
                    "vendor_family": "claude",
                    "provider": "anthropic",
                    "runtime_build_id": "build",
                    "supported_transports": ["native"],
                    "capabilities": [],
                },
            }
        ],
    }


def complete_policy() -> dict[str, object]:
    raw = valid_policy()
    profiles = raw["profiles"]
    workflows = raw["workflows"]
    assert isinstance(profiles, list) and isinstance(profiles[1], dict)
    assert isinstance(workflows, list) and isinstance(workflows[0], dict)
    profiles[1]["capabilities"] = ["workspace-read"]
    workflow = workflows[0]
    workflow["requested_run_id"] = "运行-1"
    task = workflow["tasks"][0]
    assert isinstance(task, dict)
    task.update(
        {
            "requested_task_id": "任务-1",
            "requested_dispatch_id": "调度-1",
            "owner_role": "review worker",
            "instruction": "Review\nthen report",
            "request": {
                "mode": "independent",
                "permissions": {"filesystem": "read", "commands": "deny"},
                "tools": [{"id": "工具-1", "mode": "allow"}],
            },
            "worktree": {"mode": "read_only", "scope": "workspace/review area"},
            "settlement": {"mode": "requested_external_settlement"},
            "cleanup": {"mode": "direct_child_only", "grace_seconds": 7},
            "outputs": [
                {
                    "id": "result",
                    "type": "artifact_ref",
                    "artifacts": [{"id": "report", "media_type": "text/plain"}],
                }
            ],
            "inputs": [{"id": "source-input", "type": "json"}],
            "projections": [
                {
                    "id": "source-projection",
                    "input_id": "source-input",
                    "producer_task_id": "upstream",
                    "producer_output_id": "upstream-output",
                }
            ],
            "mutable_scopes": ["workspace/review area"],
            "capabilities": ["workspace-read"],
            "turns": 9,
            "deadline_seconds": 300,
            "attempts": 1,
        }
    )
    workflow["terminal_mode"] = "synthesized"
    workflow["terminal_task_id"] = "task"
    workflow["concurrency"] = 3
    workflow["dependency_edges"] = [
        {"id": "dependency-1", "from_task_id": "upstream", "to_task_id": "task"}
    ]
    workflow["gate_edges"] = [
        {
            "id": "gate-1",
            "from_task_id": "upstream",
            "to_task_id": "task",
            "output": {
                "producer_task_id": "upstream",
                "producer_output_id": "approval",
                "required_type": "weightclass.gate/v1",
            },
            "predicate": {"operator": "equals", "value": "approved"},
        }
    ]
    workflow["grants"] = {
        dimension: [{"id": "grant-1", "from": f"{dimension}-a", "to": f"{dimension}-b"}]
        for dimension in (
            "provider",
            "intended_recipient",
            "billing_boundary",
            "transport",
            "profile",
        )
    }
    return raw


class DelegationV2SchemaTests(unittest.TestCase):
    def test_closed_mapping_and_cross_vendor_adapter_binding(self) -> None:
        policy = parse_delegation_policy_v2(valid_policy())
        manifest = parse_delegation_manifest_v2(valid_manifest())
        selected = validate_delegation_v2_binding(
            policy,
            manifest,
            source_vendor_family="codex",
            source_profile_id="source",
            tier="low",
            runtime_path="/owned/runtime",
        )
        self.assertEqual(
            (selected.workflow_id, selected.runtime_id, selected.runtime_path),
            ("wf", "rt", "/owned/runtime"),
        )

    def test_complete_parse_tree_preserves_every_policy_and_manifest_field(self) -> None:
        policy = parse_delegation_policy_v2(complete_policy())
        manifest = parse_delegation_manifest_v2(valid_manifest())

        self.assertEqual(
            (
                policy.schema_version,
                policy.manifest_schema_version,
                policy.compiler_contract_version,
                policy.runtime_protocol_version,
                policy.frame_version,
            ),
            (2, 2, 2, 2, "WCD2"),
        )
        destination = policy.profiles[1]
        self.assertEqual(
            (
                destination.profile_id,
                destination.provider,
                destination.intended_recipient,
                destination.billing_boundary,
                destination.transport,
                destination.account_profile,
                destination.capabilities,
                tuple((pair.model, pair.effort) for pair in destination.allowed_model_effort_pairs),
            ),
            (
                "destination",
                "anthropic",
                "r2",
                "b2",
                "native",
                "a2",
                ("workspace-read",),
                (("m2", "e2"),),
            ),
        )
        workflow = policy.workflows[0]
        task = workflow.tasks[0]
        self.assertEqual(
            (
                workflow.workflow_id,
                workflow.requested_run_id,
                workflow.terminal_mode,
                workflow.terminal_task_id,
                workflow.concurrency,
                workflow.eligibility.source_vendor_family,
                workflow.eligibility.source_profile_id,
                workflow.eligibility.tier,
            ),
            ("wf", "运行-1", "synthesized", "task", 3, "codex", "source", "low"),
        )
        self.assertEqual(
            (
                task.task_id,
                task.requested_task_id,
                task.requested_dispatch_id,
                task.owner_role,
                task.destination_profile_id,
                task.adapter_id,
                task.model,
                task.effort,
                task.instruction,
                task.turns,
                task.deadline_seconds,
                task.attempts,
            ),
            (
                "task",
                "任务-1",
                "调度-1",
                "review worker",
                "destination",
                "adapter",
                "m2",
                "e2",
                "Review\nthen report",
                9,
                300,
                1,
            ),
        )
        self.assertEqual(
            (
                task.request.mode,
                task.request.permissions.filesystem,
                task.request.permissions.commands,
                task.request.tools[0].tool_id,
                task.request.tools[0].mode,
                task.worktree.mode,
                task.worktree.scope,
                task.settlement.mode,
                task.cleanup.mode,
                task.cleanup.grace_seconds,
            ),
            (
                "independent",
                "read",
                "deny",
                "工具-1",
                "allow",
                "read_only",
                "workspace/review area",
                "requested_external_settlement",
                "direct_child_only",
                7,
            ),
        )
        self.assertEqual(
            (
                task.outputs[0].output_id,
                task.outputs[0].output_type,
                task.outputs[0].artifacts[0].artifact_id,
                task.outputs[0].artifacts[0].media_type,
                task.inputs[0].input_id,
                task.inputs[0].input_type,
                task.projections[0].projection_id,
                task.projections[0].input_id,
                task.projections[0].producer_task_id,
                task.projections[0].producer_output_id,
                task.mutable_scopes,
                task.capabilities,
            ),
            (
                "result",
                "artifact_ref",
                "report",
                "text/plain",
                "source-input",
                "json",
                "source-projection",
                "source-input",
                "upstream",
                "upstream-output",
                ("workspace/review area",),
                ("workspace-read",),
            ),
        )
        self.assertEqual(
            (
                workflow.dependencies[0].dependency_id,
                workflow.dependencies[0].from_task_id,
                workflow.dependencies[0].to_task_id,
                workflow.gates[0].gate_id,
                workflow.gates[0].output.producer_task_id,
                workflow.gates[0].output.producer_output_id,
                workflow.gates[0].output.required_type,
                workflow.gates[0].predicate.operator,
                workflow.gates[0].predicate.value,
            ),
            (
                "dependency-1",
                "upstream",
                "task",
                "gate-1",
                "upstream",
                "approval",
                "weightclass.gate/v1",
                "equals",
                "approved",
            ),
        )
        self.assertEqual(workflow.grants.provider[0].grant_id, "grant-1")
        self.assertEqual(workflow.grants.profile[0].to_value, "profile-b")
        self.assertEqual(manifest.schema_version, 2)
        self.assertEqual(
            tuple(
                (item.provider, item.vendor_family) for item in manifest.provider_family_mappings
            ),
            (("anthropic", "claude"), ("openai", "codex")),
        )
        runtime = manifest.runtimes[0]
        self.assertEqual(
            (
                runtime.runtime_id,
                runtime.path,
                runtime.adapter.adapter_id,
                runtime.adapter.vendor_family,
                runtime.adapter.provider,
                runtime.adapter.runtime_build_id,
                runtime.adapter.supported_transports,
                runtime.adapter.capabilities,
            ),
            ("rt", "/owned/runtime", "adapter", "claude", "anthropic", "build", ("native",), ()),
        )
        for node in (policy, destination, workflow, task, task.request, manifest, runtime):
            with self.subTest(node=type(node).__name__):
                self.assertFalse(hasattr(node, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            policy.schema_version = 1  # type: ignore[misc]

    def test_cross_references_are_deferred_until_binding(self) -> None:
        raw = valid_policy()
        workflows = raw["workflows"]
        assert isinstance(workflows, list)
        eligibility = workflows[0]["eligibility"]
        assert isinstance(eligibility, dict)
        eligibility["source_vendor_family"] = "claude"
        policy = parse_delegation_policy_v2(raw)
        manifest = parse_delegation_manifest_v2(valid_manifest())
        with self.assertRaises(DelegationV2InvalidInputError) as caught:
            validate_delegation_v2_binding(
                policy,
                manifest,
                source_vendor_family="claude",
                source_profile_id="source",
                tier="low",
                runtime_path="/owned/runtime",
            )
        self.assertEqual(caught.exception.args, ())

    def test_source_profile_coherence_precedes_selector_uniqueness(self) -> None:
        raw = valid_policy()
        workflows = raw["workflows"]
        assert isinstance(workflows, list) and isinstance(workflows[0], dict)
        duplicate = copy.deepcopy(workflows[0])
        duplicate["id"] = "wf-duplicate"
        workflows.append(duplicate)
        eligibility = workflows[0]["eligibility"]
        assert isinstance(eligibility, dict)
        eligibility["source_vendor_family"] = "claude"
        policy = parse_delegation_policy_v2(raw)
        manifest = parse_delegation_manifest_v2(valid_manifest())

        with mock.patch(
            "weightclass.delegation_v2_schema._validate_source_profile_coherence",
            side_effect=DelegationV2InvalidInputError(),
        ) as coherence:
            with self.assertRaises(DelegationV2InvalidInputError):
                validate_delegation_v2_binding(
                    policy,
                    manifest,
                    source_vendor_family="codex",
                    source_profile_id="source",
                    tier="low",
                    runtime_path="/owned/runtime",
                )
        coherence.assert_called_once()

    def test_adapter_provider_family_coherence_is_deferred_until_binding(self) -> None:
        raw_manifest = valid_manifest()
        adapter = raw_manifest["runtimes"][0]["adapter"]  # type: ignore[index]
        adapter["vendor_family"] = "codex"
        manifest = parse_delegation_manifest_v2(raw_manifest)
        policy = parse_delegation_policy_v2(valid_policy())
        with self.assertRaises(DelegationV2InvalidInputError):
            validate_delegation_v2_binding(
                policy,
                manifest,
                source_vendor_family="codex",
                source_profile_id="source",
                tier="low",
                runtime_path="/owned/runtime",
            )

    def test_bool_integer_and_extra_key_are_value_free_invalid_input(self) -> None:
        for mutation in ("bool", "extra"):
            raw = valid_policy()
            workflow = raw["workflows"][0]  # type: ignore[index]
            if mutation == "bool":
                workflow["concurrency"] = True
            else:
                workflow["unexpected"] = "secret"
            with (
                self.subTest(mutation=mutation),
                self.assertRaises(DelegationV2InvalidInputError) as caught,
            ):
                parse_delegation_policy_v2(raw)
            self.assertEqual(caught.exception.args, ())

    def test_workflow_concurrency_is_bounded_and_bool_safe(self) -> None:
        for value in (0, 33, True):
            raw = valid_policy()
            workflow = raw["workflows"][0]  # type: ignore[index]
            workflow["concurrency"] = value
            with self.subTest(value=value), self.assertRaises(DelegationV2InvalidInputError):
                parse_delegation_policy_v2(raw)

    def test_task_turns_are_bounded_and_bool_safe(self) -> None:
        for value in (0, 257, True):
            raw = valid_policy()
            task = raw["workflows"][0]["tasks"][0]  # type: ignore[index]
            task["turns"] = value
            with self.subTest(value=value), self.assertRaises(DelegationV2InvalidInputError):
                parse_delegation_policy_v2(raw)

    def test_task_local_wrong_type_is_rejected_before_graph_validation(self) -> None:
        raw = valid_policy()
        task = raw["workflows"][0]["tasks"][0]  # type: ignore[index]
        task["turns"] = True
        with self.assertRaises(DelegationV2InvalidInputError):
            parse_delegation_policy_v2(raw)

    def test_edge_and_grant_exact_shapes_are_checked_before_graph_work(self) -> None:
        for field, invalid_value in (
            ("dependency_edges", [{"id": "edge", "from_task_id": "task"}]),
            ("gate_edges", [{"id": "gate", "from_task_id": "task"}]),
            ("grants", {"provider": [], "unexpected": []}),
        ):
            raw = valid_policy()
            workflow = raw["workflows"][0]  # type: ignore[index]
            workflow[field] = invalid_value
            with self.subTest(field=field):
                with self.assertRaises(DelegationV2InvalidInputError) as caught:
                    parse_delegation_policy_v2(raw)
                self.assertEqual(caught.exception.args, ())

    def test_adapter_must_cover_task_capabilities(self) -> None:
        raw = valid_policy()
        task = raw["workflows"][0]["tasks"][0]  # type: ignore[index]
        task["capabilities"] = ["workspace-read"]
        policy = parse_delegation_policy_v2(raw)
        manifest = parse_delegation_manifest_v2(valid_manifest())
        with self.assertRaises(DelegationV2InvalidInputError) as caught:
            validate_delegation_v2_binding(
                policy,
                manifest,
                source_vendor_family="codex",
                source_profile_id="source",
                tier="low",
                runtime_path="/owned/runtime",
            )
        self.assertEqual(caught.exception.args, ())

    def test_task_model_effort_must_be_allowed_by_destination_profile(self) -> None:
        raw = valid_policy()
        task = raw["workflows"][0]["tasks"][0]  # type: ignore[index]
        task["model"] = "unlisted-model"
        policy = parse_delegation_policy_v2(raw)
        manifest = parse_delegation_manifest_v2(valid_manifest())
        with self.assertRaises(DelegationV2InvalidInputError):
            validate_delegation_v2_binding(
                policy,
                manifest,
                source_vendor_family="codex",
                source_profile_id="source",
                tier="low",
                runtime_path="/owned/runtime",
            )

    def test_binding_requires_exact_reviewed_runtime_path(self) -> None:
        policy = parse_delegation_policy_v2(valid_policy())
        manifest = parse_delegation_manifest_v2(valid_manifest())
        for runtime_path in ("/owned/other-runtime", "relative/runtime", "/owned/../runtime"):
            with self.subTest(runtime_path=runtime_path):
                with self.assertRaises(DelegationV2InvalidInputError):
                    validate_delegation_v2_binding(
                        policy,
                        manifest,
                        source_vendor_family="codex",
                        source_profile_id="source",
                        tier="low",
                        runtime_path=runtime_path,
                    )

    def test_ids_and_labels_accept_reviewable_unicode_but_reject_invisibles(self) -> None:
        raw = valid_policy()
        profiles = raw["profiles"]
        assert isinstance(profiles, list) and isinstance(profiles[0], dict)
        profiles[0]["id"] = "出发点"
        workflows = raw["workflows"]
        assert isinstance(workflows, list) and isinstance(workflows[0], dict)
        eligibility = workflows[0]["eligibility"]
        assert isinstance(eligibility, dict)
        eligibility["source_profile_id"] = "出发点"
        parse_delegation_policy_v2(raw)

        for field, invalid in (
            ("id", "hidden\u200bidentifier"),
            ("account_profile", "hidden\u2060label"),
            ("billing_boundary", "line\nbreak"),
        ):
            invalid_raw = valid_policy()
            invalid_profiles = invalid_raw["profiles"]
            assert isinstance(invalid_profiles, list) and isinstance(invalid_profiles[0], dict)
            invalid_profiles[0][field] = invalid
            with self.subTest(field=field), self.assertRaises(DelegationV2InvalidInputError):
                parse_delegation_policy_v2(invalid_raw)

    def test_manifest_byte_bound_does_not_apply_ascii_escape_expansion(self) -> None:
        raw = valid_manifest()
        raw["runtimes"] = [
            {
                "id": f"runtime-{index}",
                "path": "/" + "界" * 1_100,
                "adapter": {
                    "id": f"adapter-{index}",
                    "vendor_family": "claude",
                    "provider": "anthropic",
                    "runtime_build_id": "build",
                    "supported_transports": ["native"],
                    "capabilities": [],
                },
            }
            for index in range(64)
        ]
        encoded = json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertLessEqual(len(encoded), 262_144)
        manifest = parse_delegation_manifest_v2(raw)
        self.assertEqual(len(manifest.runtimes), 64)

    def test_closed_output_types_and_nested_reviewable_scalars_are_enforced(self) -> None:
        for mutation in ("output-type", "input-type", "artifact-media", "scope"):
            raw = complete_policy()
            task = raw["workflows"][0]["tasks"][0]  # type: ignore[index]
            if mutation == "output-type":
                task["outputs"][0]["type"] = "unregistered"
            elif mutation == "input-type":
                task["inputs"][0]["type"] = "unregistered"
            elif mutation == "artifact-media":
                task["outputs"][0]["artifacts"][0]["media_type"] = "text/\u200bplain"
            else:
                task["worktree"]["scope"] = "scope\u0000hidden"
            with self.subTest(mutation=mutation), self.assertRaises(DelegationV2InvalidInputError):
                parse_delegation_policy_v2(raw)

    def test_reviewable_labels_fail_value_free_on_invalid_unicode(self) -> None:
        for field in ("account_profile", "id"):
            raw = valid_policy()
            profile = raw["profiles"][0]  # type: ignore[index]
            profile[field] = "\ud800"
            with self.subTest(field=field):
                with self.assertRaises(DelegationV2InvalidInputError) as caught:
                    parse_delegation_policy_v2(raw)
                self.assertEqual(caught.exception.args, ())

    def test_workflow_aggregate_instruction_and_artifact_bounds(self) -> None:
        for mutation in ("instructions", "artifacts"):
            raw = valid_policy()
            workflow = raw["workflows"][0]  # type: ignore[index]
            original = workflow["tasks"][0]
            workflow["tasks"] = []
            for index in range(9):
                task = copy.deepcopy(original)
                task["id"] = f"task-{index}"
                task["requested_task_id"] = f"requested-{index}"
                task["requested_dispatch_id"] = f"dispatch-{index}"
                if mutation == "instructions":
                    task["instruction"] = "x" * 16_384
                else:
                    task["outputs"] = [
                        {
                            "id": f"output-{index}",
                            "type": "text",
                            "artifacts": [
                                {"id": f"artifact-{index}-{item}", "media_type": "text/plain"}
                                for item in range(15)
                            ],
                        }
                    ]
                workflow["tasks"].append(task)
            with self.subTest(mutation=mutation), self.assertRaises(DelegationV2InvalidInputError):
                parse_delegation_policy_v2(raw)


if __name__ == "__main__":
    unittest.main()
