import json
import unittest
from pathlib import Path

from tests.test_native_v2_schema import valid_policy
from weightclass.native_v2_compile import compile_native_v2
from weightclass.native_v2_schema import parse_native_policy_v2
from weightclass.native_v2_types import CompiledExecutionV2
from weightclass.v2_validation import V2ValidationError

FIXTURE = Path(__file__).parent / "fixtures/native_v2_schema/golden_cross_vendor.json"


class NativeV2CompileTests(unittest.TestCase):
    def compile(self, policy: dict[str, object] | None = None) -> CompiledExecutionV2:
        return compile_native_v2(
            parse_native_policy_v2(policy or valid_policy()),
            source_vendor="codex",
            source_profile_id="source",
            tier="high",
        )

    def test_claude_builder_and_golden_fingerprint(self) -> None:
        compiled = self.compile()
        self.assertEqual(
            compiled.argv,
            (
                "/opt/owned/claude",
                "--print",
                "--no-session-persistence",
                "--permission-mode",
                "acceptEdits",
                "--model",
                "opus",
                "--effort",
                "high",
            ),
        )
        golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(compiled.canonical_descriptor_bytes.decode("ascii"), golden["descriptor"])
        self.assertEqual(compiled.route_fingerprint, golden["route_fingerprint"])

    def test_codex_builder_is_exact(self) -> None:
        policy = valid_policy()
        policy["profiles"] = [{"id": "source", "vendor": "codex", "account_profile": "account-a"}]
        policy["execution_targets"] = [
            {
                "id": "target",
                "profile_id": "source",
                "vendor": "codex",
                "executable": "/opt/owned/codex",
                "builder": {"kind": "codex-exec-v1", "version": 1},
                "allowed_model_effort_pairs": [{"model": "gpt", "effort": "medium"}],
            }
        ]
        routes = policy["routes"]
        assert isinstance(routes, list) and isinstance(routes[0], dict)
        routes[0]["model"] = "gpt"
        routes[0]["effort"] = "medium"
        policy["profile_grants"] = []
        policy["vendor_grants"] = []
        compiled = self.compile(policy)
        self.assertEqual(
            compiled.argv,
            (
                "/opt/owned/codex",
                "exec",
                "--ephemeral",
                "--sandbox",
                "workspace-write",
                "--model",
                "gpt",
                "-c",
                "model_reasoning_effort=medium",
                "-",
            ),
        )

    def test_selector_must_be_unique(self) -> None:
        policy = valid_policy()
        routes = policy["routes"]
        assert isinstance(routes, list)
        duplicate = dict(routes[0])
        duplicate["id"] = "another"
        routes.append(duplicate)
        with self.assertRaisesRegex(V2ValidationError, "^$"):
            parse_native_policy_v2(policy)

    def test_directional_exact_and_fully_used_grants(self) -> None:
        for key, value in (
            ("profile_grants", []),
            ("vendor_grants", []),
            (
                "profile_grants",
                [
                    {
                        "id": "reverse",
                        "from_profile_id": "dest",
                        "to_profile_id": "source",
                    }
                ],
            ),
        ):
            policy = valid_policy()
            policy[key] = value
            with self.assertRaisesRegex(V2ValidationError, "^$"):
                parse_native_policy_v2(policy)
        policy = valid_policy()
        grants = policy["profile_grants"]
        assert isinstance(grants, list)
        grants.append({"id": "unused", "from_profile_id": "dest", "to_profile_id": "source"})
        with self.assertRaisesRegex(V2ValidationError, "^$"):
            parse_native_policy_v2(policy)

    def test_selection_is_order_independent_and_missing_fails(self) -> None:
        first = self.compile()
        policy = valid_policy()
        for key in ("profiles", "profile_grants", "vendor_grants"):
            values = policy[key]
            assert isinstance(values, list)
            values.reverse()
        second = self.compile(policy)
        self.assertEqual(first.canonical_descriptor_bytes, second.canonical_descriptor_bytes)
        with self.assertRaisesRegex(V2ValidationError, "^$"):
            compile_native_v2(
                parse_native_policy_v2(valid_policy()),
                source_vendor="codex",
                source_profile_id="source",
                tier="low",
            )


if __name__ == "__main__":
    unittest.main()
