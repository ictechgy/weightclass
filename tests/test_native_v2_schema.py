import copy
import unittest

from weightclass.native_v2_schema import (
    NativePolicyV2,
    dispatch_native_policy_schema,
    parse_native_policy_v2,
)
from weightclass.v2_validation import V2ValidationError


def valid_policy() -> dict[str, object]:
    return {
        "schema_version": 2,
        "profiles": [
            {"id": "source", "vendor": "codex", "account_profile": "account-a"},
            {"id": "dest", "vendor": "claude", "account_profile": "account-b"},
        ],
        "execution_targets": [
            {
                "id": "target",
                "profile_id": "dest",
                "vendor": "claude",
                "executable": "/opt/owned/claude",
                "builder": {"kind": "claude-print-v1", "version": 1},
                "allowed_model_effort_pairs": [{"model": "opus", "effort": "high"}],
            }
        ],
        "routes": [
            {
                "id": "route",
                "eligibility": [
                    {
                        "source_vendor": "codex",
                        "source_profile_id": "source",
                        "tier": "high",
                    }
                ],
                "target_id": "target",
                "model": "opus",
                "effort": "high",
            }
        ],
        "profile_grants": [
            {"id": "profile-change", "from_profile_id": "source", "to_profile_id": "dest"}
        ],
        "vendor_grants": [{"id": "vendor-change", "from_vendor": "codex", "to_vendor": "claude"}],
    }


class NativeV2SchemaTests(unittest.TestCase):
    def assert_invalid(self, policy: object) -> None:
        with self.assertRaisesRegex(V2ValidationError, "^$"):
            parse_native_policy_v2(policy)

    def test_version_dispatch_is_additive_and_bool_safe(self) -> None:
        legacy: dict[str, object] = {"routes": []}
        version, dispatched = dispatch_native_policy_schema(legacy)
        self.assertEqual(version, 1)
        self.assertIs(dispatched, legacy)
        explicit = {"schema_version": 1, "routes": []}
        version, dispatched = dispatch_native_policy_schema(explicit)
        self.assertEqual(version, 1)
        self.assertEqual(dispatched, {"routes": []})
        self.assertEqual(explicit, {"schema_version": 1, "routes": []})
        version, dispatched = dispatch_native_policy_schema(valid_policy())
        self.assertEqual(version, 2)
        self.assertIsInstance(dispatched, NativePolicyV2)
        for invalid in (True, False, "2", 0, 3, None):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(V2ValidationError, "^$"):
                    dispatch_native_policy_schema({"schema_version": invalid})

    def test_exact_shapes_counts_and_scalar_boundaries(self) -> None:
        parse_native_policy_v2(valid_policy())
        mutations: list[dict[str, object]] = []
        for key in ("profiles", "execution_targets", "routes"):
            policy = valid_policy()
            policy[key] = []
            mutations.append(policy)
        policy = valid_policy()
        policy["extra"] = 1
        mutations.append(policy)
        policy = valid_policy()
        profiles = copy.deepcopy(policy["profiles"])
        assert isinstance(profiles, list) and isinstance(profiles[0], dict)
        profiles[0]["id"] = "x" * 65
        policy["profiles"] = profiles
        mutations.append(policy)
        policy = valid_policy()
        targets = copy.deepcopy(policy["execution_targets"])
        assert isinstance(targets, list) and isinstance(targets[0], dict)
        targets[0]["executable"] = "relative/tool"
        policy["execution_targets"] = targets
        mutations.append(policy)
        policy = valid_policy()
        targets = copy.deepcopy(policy["execution_targets"])
        assert isinstance(targets, list) and isinstance(targets[0], dict)
        targets[0]["builder"] = {"kind": "claude-print-v1", "version": True}
        policy["execution_targets"] = targets
        mutations.append(policy)
        for mutation in mutations:
            self.assert_invalid(mutation)

    def test_duplicates_references_coherence_and_allowlist_fail_closed(self) -> None:
        for mutation in ("duplicate-profile", "bad-source-vendor", "bad-target-vendor", "bad-pair"):
            policy = valid_policy()
            if mutation == "duplicate-profile":
                profiles = copy.deepcopy(policy["profiles"])
                assert isinstance(profiles, list)
                profiles.append(copy.deepcopy(profiles[0]))
                policy["profiles"] = profiles
            elif mutation == "bad-source-vendor":
                routes = copy.deepcopy(policy["routes"])
                assert isinstance(routes, list) and isinstance(routes[0], dict)
                eligibility = routes[0]["eligibility"]
                assert isinstance(eligibility, list) and isinstance(eligibility[0], dict)
                eligibility[0]["source_vendor"] = "claude"
                policy["routes"] = routes
            elif mutation == "bad-target-vendor":
                targets = copy.deepcopy(policy["execution_targets"])
                assert isinstance(targets, list) and isinstance(targets[0], dict)
                targets[0]["vendor"] = "codex"
                policy["execution_targets"] = targets
            else:
                routes = copy.deepcopy(policy["routes"])
                assert isinstance(routes, list) and isinstance(routes[0], dict)
                routes[0]["model"] = "unlisted"
                policy["routes"] = routes
            self.assert_invalid(policy)

    def test_collection_upper_bounds(self) -> None:
        policy = valid_policy()
        policy["profiles"] = [
            {"id": f"p{i}", "vendor": "codex", "account_profile": "a"} for i in range(65)
        ]
        self.assert_invalid(policy)
        policy = valid_policy()
        routes = copy.deepcopy(policy["routes"])
        assert isinstance(routes, list) and isinstance(routes[0], dict)
        routes[0]["eligibility"] = [
            {"source_vendor": "codex", "source_profile_id": "source", "tier": "high"}
            for _ in range(33)
        ]
        policy["routes"] = routes
        self.assert_invalid(policy)

    def test_allowed_pair_and_collection_maxima_are_accepted(self) -> None:
        policy = valid_policy()
        targets = copy.deepcopy(policy["execution_targets"])
        assert isinstance(targets, list) and isinstance(targets[0], dict)
        targets[0]["allowed_model_effort_pairs"] = [
            {"model": f"model-{index}", "effort": "effort"} for index in range(64)
        ]
        routes = copy.deepcopy(policy["routes"])
        assert isinstance(routes, list) and isinstance(routes[0], dict)
        routes[0]["model"] = "model-0"
        routes[0]["effort"] = "effort"
        policy["execution_targets"] = targets
        policy["routes"] = routes
        parse_native_policy_v2(policy)

        targets[0]["allowed_model_effort_pairs"] = [
            {"model": f"model-{index}", "effort": "effort"} for index in range(65)
        ]
        self.assert_invalid(policy)

    def test_string_and_executable_boundaries(self) -> None:
        policy = valid_policy()
        profiles = copy.deepcopy(policy["profiles"])
        assert isinstance(profiles, list) and isinstance(profiles[0], dict)
        profiles[0]["account_profile"] = "a" * 240
        policy["profiles"] = profiles
        parse_native_policy_v2(policy)
        profiles[0]["account_profile"] = "a" * 241
        self.assert_invalid(policy)

        policy = valid_policy()
        targets = copy.deepcopy(policy["execution_targets"])
        assert isinstance(targets, list) and isinstance(targets[0], dict)
        targets[0]["executable"] = "/" + "x" * 4095
        policy["execution_targets"] = targets
        parse_native_policy_v2(policy)
        targets[0]["executable"] = "/" + "x" * 4096
        self.assert_invalid(policy)


if __name__ == "__main__":
    unittest.main()
