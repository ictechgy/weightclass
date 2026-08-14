import copy
import unittest
from typing import cast

from weightclass.native_v2_schema import dispatch_native_policy_schema
from weightclass.native_v3_schema import (
    NativePolicyV3,
    parse_native_policy_v3,
    validate_argv,
    validate_executable_path,
    validate_identifier,
    validate_label,
    validate_opaque_token,
)
from weightclass.v2_validation import V2ValidationError


def valid_policy() -> dict[str, object]:
    return {
        "schema_version": 3,
        "profiles": [
            {"id": "source", "vendor": "codex", "account_profile": "work"},
            {"id": "grok-profile", "vendor": "grok", "account_profile": "personal"},
        ],
        "execution_targets": [
            {
                "id": "grok-target",
                "profile_id": "grok-profile",
                "vendor": "grok",
                "executable": "/opt/grok",
                "builder": {"kind": "grok-print-v1", "version": 1},
                "allowed_model_effort_pairs": [{"model": None, "effort": "low"}],
            },
            {
                "id": "codex-target",
                "profile_id": "source",
                "vendor": "codex",
                "executable": "/opt/codex",
                "builder": {"kind": "codex-exec-v1", "version": 1},
                "allowed_model_effort_pairs": [{"model": None, "effort": "high"}],
            },
        ],
        "routes": [
            {
                "id": "low-grok",
                "source_profile_id": "source",
                "tier": "low",
                "target_id": "grok-target",
                "model": None,
                "effort": "low",
            },
            {
                "id": "high-codex",
                "source_profile_id": "source",
                "tier": "high",
                "target_id": "codex-target",
                "model": None,
                "effort": "high",
            },
        ],
        "profile_grants": [
            {"id": "source-to-grok", "from_profile_id": "source", "to_profile_id": "grok-profile"}
        ],
        "vendor_grants": [{"id": "codex-to-grok", "from_vendor": "codex", "to_vendor": "grok"}],
    }


class NativeV3SchemaTests(unittest.TestCase):
    def test_exact_schema_binds_profiles_targets_builders_and_minimal_grants(self) -> None:
        parsed = parse_native_policy_v3(valid_policy())
        self.assertEqual(parsed.routes[0].model, None)
        self.assertEqual(parsed.execution_targets[0].builder.kind, "grok-print-v1")
        for change in ("extra", "missing_profile_grant", "wrong_builder", "open_effort"):
            value = copy.deepcopy(valid_policy())
            if change == "extra":
                value["command"] = ["grok"]
            elif change == "missing_profile_grant":
                value["profile_grants"] = []
            elif change == "wrong_builder":
                value["execution_targets"][0]["builder"]["kind"] = "codex-exec-v1"  # type: ignore[index]
            else:
                value["routes"][0]["effort"] = "extreme"  # type: ignore[index]
            with self.assertRaisesRegex(V2ValidationError, "^$"):
                parse_native_policy_v3(value)

    def test_all_built_in_builders_are_closed_and_version_one(self) -> None:
        policy = valid_policy()
        targets = policy["execution_targets"]
        assert isinstance(targets, list)
        targets.extend(
            {
                "id": f"{vendor}-target",
                "profile_id": f"{vendor}-profile",
                "vendor": vendor,
                "executable": f"/opt/{vendor}",
                "builder": {"kind": f"{vendor}-print-v1", "version": 1},
                "allowed_model_effort_pairs": [{"model": None, "effort": "medium"}],
            }
            for vendor in ("claude", "agy")
        )
        profiles = policy["profiles"]
        assert isinstance(profiles, list)
        profiles.extend(
            {"id": f"{vendor}-profile", "vendor": vendor, "account_profile": "account"}
            for vendor in ("claude", "agy")
        )
        routes = policy["routes"]
        assert isinstance(routes, list)
        routes.extend(
            {
                "id": f"{vendor}-route",
                "source_profile_id": "source",
                "tier": "standard",
                "target_id": f"{vendor}-target",
                "model": None,
                "effort": "medium",
            }
            for vendor in ("claude",)
        )
        grants = policy["profile_grants"]
        assert isinstance(grants, list)
        grants.extend(
            {
                "id": f"source-to-{vendor}",
                "from_profile_id": "source",
                "to_profile_id": f"{vendor}-profile",
            }
            for vendor in ("claude",)
        )
        vendor_grants = policy["vendor_grants"]
        assert isinstance(vendor_grants, list)
        vendor_grants.extend(
            {"id": f"codex-to-{vendor}", "from_vendor": "codex", "to_vendor": vendor}
            for vendor in ("claude",)
        )
        parsed = parse_native_policy_v3(policy)
        self.assertEqual(
            {target.builder.kind for target in parsed.execution_targets},
            {"grok-print-v1", "codex-exec-v1", "claude-print-v1", "agy-print-v1"},
        )

    def test_schema3_dispatch_is_additive(self) -> None:
        version, dispatched = dispatch_native_policy_schema(valid_policy())
        self.assertEqual(version, 3)
        self.assertEqual(cast(NativePolicyV3, dispatched).schema_version, 3)

        legacy: dict[str, object] = {"routes": []}
        self.assertEqual(dispatch_native_policy_schema(legacy), (1, legacy))

    def test_reusable_string_validators_count_strict_utf8_bytes(self) -> None:
        self.assertEqual(validate_identifier("é" * 32), "é" * 32)
        self.assertEqual(validate_label("가" * 80), "가" * 80)
        self.assertEqual(validate_opaque_token("é" * 120), "é" * 120)
        self.assertEqual(validate_executable_path("/" + "é" * 2_047), "/" + "é" * 2_047)
        for validator, value in (
            (validate_identifier, "é" * 33),
            (validate_label, "가" * 81),
            (validate_opaque_token, "é" * 121),
            (validate_executable_path, "/" + "é" * 2_048),
        ):
            with self.subTest(validator=validator.__name__):
                with self.assertRaisesRegex(V2ValidationError, "^$"):
                    validator(value)

    def test_validators_require_nfc_and_reject_unsafe_labels(self) -> None:
        for field, value in (
            ("id", "e\u0301"),
            ("account_profile", "has\u200bformat"),
            ("account_profile", "has\nline"),
            ("account_profile", "-option-like"),
            ("id", "-leading"),
        ):
            policy = copy.deepcopy(valid_policy())
            profiles = cast(list[dict[str, object]], policy["profiles"])
            if field == "id":
                profiles[0]["id"] = value
            else:
                profiles[0][field] = value
            with self.subTest(field=field, value=value):
                with self.assertRaisesRegex(V2ValidationError, "^$"):
                    parse_native_policy_v3(policy)

        policy = copy.deepcopy(valid_policy())
        targets = cast(list[dict[str, object]], policy["execution_targets"])
        targets[0]["executable"] = "/opt/./grok"
        with self.assertRaisesRegex(V2ValidationError, "^$"):
            parse_native_policy_v3(policy)

    def test_identifiers_are_globally_unique_across_policy_objects(self) -> None:
        policy = copy.deepcopy(valid_policy())
        targets = cast(list[dict[str, object]], policy["execution_targets"])
        routes = cast(list[dict[str, object]], policy["routes"])
        targets[0]["id"] = "source"
        routes[0]["target_id"] = "source"
        with self.assertRaisesRegex(V2ValidationError, "^$"):
            parse_native_policy_v3(policy)

    def test_model_override_is_opaque_and_antigravity_stays_default_only(self) -> None:
        policy = copy.deepcopy(valid_policy())
        routes = cast(list[dict[str, object]], policy["routes"])
        targets = cast(list[dict[str, object]], policy["execution_targets"])
        routes[0]["model"] = "user-model"
        pairs = cast(list[dict[str, object]], targets[0]["allowed_model_effort_pairs"])
        pairs[0]["model"] = "user-model"
        parse_native_policy_v3(policy)

        profiles = cast(list[dict[str, object]], policy["profiles"])
        targets = cast(list[dict[str, object]], policy["execution_targets"])
        routes = cast(list[dict[str, object]], policy["routes"])
        profile_grants = cast(list[dict[str, object]], policy["profile_grants"])
        vendor_grants = cast(list[dict[str, object]], policy["vendor_grants"])
        profiles.append({"id": "agy-profile", "vendor": "agy", "account_profile": "a"})
        targets.append(
            {
                "id": "agy-target",
                "profile_id": "agy-profile",
                "vendor": "agy",
                "executable": "/opt/agy",
                "builder": {"kind": "agy-print-v1", "version": 1},
                "allowed_model_effort_pairs": [{"model": "not-default", "effort": "low"}],
            }
        )
        routes.append(
            {
                "id": "agy-route",
                "source_profile_id": "source",
                "tier": "standard",
                "target_id": "agy-target",
                "model": "not-default",
                "effort": "low",
            }
        )
        profile_grants.append(
            {"id": "source-to-agy", "from_profile_id": "source", "to_profile_id": "agy-profile"}
        )
        vendor_grants.append({"id": "codex-to-agy", "from_vendor": "codex", "to_vendor": "agy"})
        with self.assertRaisesRegex(V2ValidationError, "^$"):
            parse_native_policy_v3(policy)

    def test_argv_validator_enforces_token_and_aggregate_bounds(self) -> None:
        self.assertEqual(
            validate_argv(("/opt/agent", "--effort", "low")), ("/opt/agent", "--effort", "low")
        )
        for argv in (
            "not-an-argv",
            ("x\x00y",),
            ("has\u200bformat",),
            ("has\u2003space",),
            tuple("x" for _ in range(33)),
            ("x" * 4097,),
            tuple("x" * 4096 for _ in range(5)),
        ):
            with self.subTest(argv_length=len(argv)):
                with self.assertRaisesRegex(V2ValidationError, "^$"):
                    validate_argv(argv)


if __name__ == "__main__":
    unittest.main()
