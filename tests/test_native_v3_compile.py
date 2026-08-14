import contextlib
import copy
import dataclasses
import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from tests.test_native_v3_schema import valid_policy
from weightclass import cli, native_v3_compile
from weightclass.executable_observation import ExecutableObservation
from weightclass.native_v3_compile import PurposeV3, compile_native_policy_v3
from weightclass.native_v3_schema import TierV3, parse_native_policy_v3


def observation(path: str) -> ExecutableObservation:
    return ExecutableObservation(path, 1, 2, 0o100000, 0o100755, 100, 3, 4, True)


class NativeV3CompileTests(unittest.TestCase):
    def compile(
        self,
        policy: dict[str, object] | None = None,
        tier: TierV3 = "low",
        purpose: PurposeV3 = "native_route",
    ) -> dict[str, object]:
        return compile_native_policy_v3(
            parse_native_policy_v3(policy or valid_policy()),
            source_vendor="codex",
            source_profile_id="source",
            tier=tier,
            purpose=purpose,
            observations={
                "/opt/grok": observation("/opt/grok"),
                "/opt/codex": observation("/opt/codex"),
            },
        )

    def test_compiles_exactly_one_selected_task_free_route(self) -> None:
        review = self.compile()
        self.assertEqual(review["purpose"], "native_route")
        self.assertEqual(review["task_delivery"], "argv")
        self.assertIn("{{task}}", cast(list[object], review["argv_template"]))
        self.assertEqual(
            cast(dict[str, object], review["transition"])["authorizations"],
            [
                {"dimension": "profile", "grant_id": "source-to-grok"},
                {"dimension": "vendor", "grant_id": "codex-to-grok"},
            ],
        )
        self.assertNotIn("private task", json.dumps(review))
        with self.assertRaises(ValueError):
            self.compile(purpose=cast(PurposeV3, "ordinary_route"))

    def test_static_compile_selects_before_any_executable_observation(self) -> None:
        """Breaks if route/grant selection again requires a process observation."""
        compile_static = getattr(native_v3_compile, "compile_static_native_policy_v3", None)
        self.assertIsNotNone(compile_static)
        assert compile_static is not None
        selected = compile_static(
            parse_native_policy_v3(valid_policy()),
            source_vendor="codex",
            source_profile_id="source",
            tier="low",
            purpose="native_route",
        )
        self.assertEqual(selected.executable, "/opt/grok")
        self.assertEqual(selected.required_confirmations, ("endpoint_transition",))
        self.assertEqual(selected.argv_template.count("{{task}}"), 1)

    def test_artifact_binds_confirmation_and_execution_template_contracts(self) -> None:
        review = self.compile()
        argv = [
            "/opt/grok",
            "-p",
            "{{task}}",
            "--permission-mode",
            "acceptEdits",
            "--reasoning-effort",
            "low",
        ]
        self.assertEqual(review["required_confirmations"], ["endpoint_transition"])
        self.assertEqual(
            review["execution_template"],
            {
                "argv_template": argv,
                "delivery": "argv",
                "task_slot_count": 1,
                "transport_version": 1,
                "cleanup": {"grace_seconds": 0, "terminate_grace_seconds": 0},
            },
        )
        serialized = json.dumps(review)
        self.assertNotIn("task_hash", serialized)
        self.assertNotIn("task_content", serialized)

        delegated = self.compile(purpose="native_delegation")
        self.assertEqual(
            delegated["required_confirmations"],
            ["native_delegation", "endpoint_transition"],
        )
        self.assertNotEqual(review["route_fingerprint"], delegated["route_fingerprint"])

        same_endpoint = self.compile(tier="high")
        self.assertEqual(same_endpoint["required_confirmations"], [])
        self.assertEqual(
            cast(dict[str, object], same_endpoint["execution_template"])["task_slot_count"], 0
        )

    def test_fingerprint_binds_the_exact_used_grants(self) -> None:
        baseline = self.compile()["route_fingerprint"]
        for collection in ("profile_grants", "vendor_grants"):
            changed = copy.deepcopy(valid_policy())
            grants = cast(list[dict[str, object]], changed[collection])
            grants[0]["id"] = f"changed-{collection}"
            self.assertNotEqual(baseline, self.compile(changed)["route_fingerprint"])

    def test_fingerprint_binds_both_account_profiles_builder_and_observation(self) -> None:
        baseline = self.compile()["route_fingerprint"]
        for mutation in ("source", "destination", "builder"):
            changed = copy.deepcopy(valid_policy())
            if mutation == "source":
                changed["profiles"][0]["account_profile"] = "other"  # type: ignore[index]
            elif mutation == "destination":
                changed["profiles"][1]["account_profile"] = "other"  # type: ignore[index]
            else:
                changed["execution_targets"][0]["builder"]["version"] = 2  # type: ignore[index]
            if mutation == "builder":
                with self.assertRaises(ValueError):
                    self.compile(changed)
            else:
                self.assertNotEqual(baseline, self.compile(changed)["route_fingerprint"])

    def test_compile_canonicalizes_unordered_allowed_pairs(self) -> None:
        first = valid_policy()
        targets = cast(list[dict[str, object]], first["execution_targets"])
        pairs = cast(list[dict[str, object]], targets[0]["allowed_model_effort_pairs"])
        pairs.append({"model": "alternate", "effort": "high"})
        second = copy.deepcopy(first)
        second_targets = cast(list[dict[str, object]], second["execution_targets"])
        second_pairs = cast(
            list[dict[str, object]], second_targets[0]["allowed_model_effort_pairs"]
        )
        second_pairs.reverse()
        self.assertEqual(
            self.compile(first)["route_fingerprint"], self.compile(second)["route_fingerprint"]
        )

    def test_observation_must_describe_a_regular_executable(self) -> None:
        policy = parse_native_policy_v3(valid_policy())
        for changed in (
            dataclasses.replace(observation("/opt/grok"), file_type=0o040000),
            dataclasses.replace(observation("/opt/grok"), executable_bit=False),
            dataclasses.replace(observation("/opt/grok"), mode=0o040755),
            dataclasses.replace(observation("/opt/grok"), mode=0o100644),
            dataclasses.replace(observation("/opt/grok"), mode=cast(Any, "invalid")),
        ):
            with self.subTest(observation=changed):
                with self.assertRaisesRegex(ValueError, "^$"):
                    compile_native_policy_v3(
                        policy,
                        source_vendor="codex",
                        source_profile_id="source",
                        tier="low",
                        purpose="native_route",
                        observations={"/opt/grok": changed},
                    )

    def test_model_label_cannot_be_mistaken_for_a_task_slot(self) -> None:
        raw = valid_policy()
        targets = cast(list[dict[str, object]], raw["execution_targets"])
        routes = cast(list[dict[str, object]], raw["routes"])
        pairs = cast(list[dict[str, object]], targets[1]["allowed_model_effort_pairs"])
        pairs[0]["model"] = "{{task}}"
        routes[1]["model"] = "{{task}}"
        with self.assertRaisesRegex(ValueError, "^$"):
            self.compile(raw, tier="high")

    def test_ordinary_route_dispatch_is_task_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(valid_policy()), encoding="utf-8")
            stdout = io.StringIO()
            with (
                patch.object(__import__("sys"), "stdin", io.StringIO("PRIVATE TASK")),
                patch("weightclass.cli.read_task_from_standard_input") as task_reader,
                patch("weightclass.cli.observe_executable", return_value=observation("/opt/grok")),
                patch("weightclass.cli.validate_runtime_process_context") as context,
                contextlib.redirect_stdout(stdout),
            ):
                self.assertEqual(
                    cli.main(
                        [
                            "route",
                            "--policy",
                            str(path),
                            "--source-vendor",
                            "codex",
                            "--source-profile",
                            "source",
                            "--tier",
                            "low",
                        ]
                    ),
                    0,
                )
        self.assertEqual(len(stdout.getvalue().splitlines()), 1)
        self.assertNotIn("PRIVATE TASK", stdout.getvalue())
        task_reader.assert_not_called()
        context.assert_not_called()

    def test_schema3_route_rejects_invalid_source_selector_before_observation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(valid_policy()), encoding="utf-8")
            with (
                patch("weightclass.cli.observe_executable") as observer,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                result = cli.main(
                    [
                        "route",
                        "--policy",
                        str(path),
                        "--source-vendor",
                        "not-a-built-in",
                        "--source-profile",
                        "source",
                        "--tier",
                        "low",
                    ]
                )
        self.assertEqual(result, 2)
        observer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
