import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

RUNTIME_PATH = "/weightclass-tests/runtime-that-does-not-exist"


def _normalized_host_platform() -> dict[str, str]:
    operating_system = platform.system().lower()
    machine = platform.machine().lower()
    architecture = {
        "amd64": "x86_64",
        "arm64": "aarch64",
        "x86_64": "x86_64",
        "aarch64": "aarch64",
    }.get(machine, machine)
    return {"os": operating_system, "architecture": architecture}


def _profile(vendor: str, role: str) -> dict[str, object]:
    return {
        "id": f"{vendor}-{role}",
        "role": role,
        "vendor_family": vendor,
        "transport": "native",
        "model": f"opaque-{vendor}-{role}-model",
        "effort": "opaque-effort",
        "allowed_categories": ["implementation", "tests", "documentation"],
        "global_role_process_limit": 3 if role == "worker" else 1,
    }


def _retention() -> dict[str, str]:
    return {
        "worker_context": "release_after_workers_completed",
        "artifacts": "retain_through_integration",
        "on_reviewer_rejection": "runtime_destroy",
        "after_integration": "runtime_destroy",
    }


def _workflow(vendor: str, tier: str = "standard") -> dict[str, object]:
    return {
        "id": f"{vendor}-{tier}-delegation",
        "eligible_source_vendors": [vendor],
        "eligible_tiers": [tier],
        "adapter_id": f"{vendor}-native-v1",
        "profiles": {
            "orchestrator": f"{vendor}-orchestrator",
            "worker": f"{vendor}-worker",
            "reviewer": f"{vendor}-reviewer",
        },
        "assignments": [
            {
                "category": category,
                "execution": "must_delegate",
                "review": "required",
                "retention": _retention(),
                "integration": "mechanical_runtime",
            }
            for category in ("implementation", "tests", "documentation")
        ],
        "integration": {
            "inputs": ["reviewer_approved_worker_artifacts"],
            "allowed_operations": [
                "apply_approved_artifact",
                "run_approved_verification_command",
            ],
            "verification_commands": [["python3", "-m", "unittest", "discover", "-s", "tests"]],
        },
        "runtime_deadline_seconds": 1800,
        "direct_child_cleanup": {
            "grace_seconds": 2,
            "terminate_grace_seconds": 2,
        },
        "boundary_authorizations": {
            "provider_pairs": [],
            "recipient_pairs": [],
            "billing_pairs": [],
            "mixed_transport_pairs": [],
        },
    }


def _policy() -> dict[str, object]:
    return {
        "schema_version": 1,
        "profiles": [
            _profile(vendor, role)
            for vendor in ("claude", "codex")
            for role in ("orchestrator", "worker", "reviewer")
        ],
        "workflows": [_workflow("claude"), _workflow("codex")],
    }


def _adapter(vendor: str) -> dict[str, object]:
    return {
        "id": f"{vendor}-native-v1",
        "vendor_family": vendor,
        "transports": ["native"],
        "global_role_process_limit": 3,
        "capabilities": [
            "artifact_integrity",
            "descendant_cleanup",
            "distinct_enforcement_contexts",
            "mechanical_integration",
            "observable_action_attribution",
            "runtime_deadline",
        ],
        "enforcement_primitives": {
            action: {
                "allow": f"opaque-{vendor}-{action}-allow",
                "deny": f"opaque-{vendor}-{action}-deny",
            }
            for action in ("workspace_read", "workspace_write", "command_execution")
        }
        | {
            "process_isolation": {
                "create": f"opaque-{vendor}-context-create",
                "attribute": f"opaque-{vendor}-context-attribute",
            }
        },
    }


def _manifest() -> dict[str, object]:
    return {
        "manifest_schema_version": 1,
        "runtime_protocol_versions": [1],
        "runtime_build_id": "opaque-runtime-build",
        "supported_platforms": [_normalized_host_platform()],
        "adapters": [_adapter("claude"), _adapter("codex")],
    }


class DelegationRouteTests(unittest.TestCase):
    def _route(
        self,
        *,
        source_vendor: str = "claude",
        tier: str = "standard",
        policy: dict[str, object] | None = None,
        manifest: dict[str, object] | None = None,
        runtime_path: str = RUNTIME_PATH,
        task_input: str = "",
        require_qualified_runtime: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "delegation-policy.json"
            manifest_path = directory / "runtime-manifest.json"
            policy_path.write_text(json.dumps(policy or _policy()), encoding="utf-8")
            manifest_path.write_text(json.dumps(manifest or _manifest()), encoding="utf-8")
            arguments = [
                sys.executable,
                "-m",
                "weightclass",
                "delegate",
                "route",
                "--policy",
                str(policy_path),
                "--runtime-manifest",
                str(manifest_path),
                "--delegation-runtime",
                runtime_path,
                "--source-vendor",
                source_vendor,
                "--tier",
                tier,
            ]
            if require_qualified_runtime:
                arguments.append("--require-qualified-runtime")
            return subprocess.run(
                arguments,
                capture_output=True,
                check=False,
                input=task_input,
                text=True,
            )

    def test_qualified_route_fails_closed_with_empty_package_registry(self) -> None:
        """Breaks if a user declaration alone can claim conformance qualification."""
        distinctive_task = "zephyrine glimmerfast quokka"

        result = self._route(
            require_qualified_runtime=True,
            task_input=distinctive_task,
        )

        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stderr), {"error": "unsupported_route"})
        self.assertEqual(result.stdout, "")
        self.assertNotIn(distinctive_task, result.stderr)

    def test_routes_without_reading_task_or_inspecting_runtime(self) -> None:
        """Breaks if offline review starts depending on stdin or runtime filesystem state."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "delegation-policy.json"
            manifest_path = directory / "runtime-manifest.json"
            policy_path.write_text(json.dumps(_policy()), encoding="utf-8")
            manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "delegate",
                    "route",
                    "--policy",
                    str(policy_path),
                    "--runtime-manifest",
                    str(manifest_path),
                    "--delegation-runtime",
                    RUNTIME_PATH,
                    "--source-vendor",
                    "claude",
                    "--tier",
                    "standard",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                return_code = process.wait(timeout=5)
                stdout, stderr = process.communicate(timeout=1)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()

        self.assertEqual(return_code, 0, stderr)
        descriptor = json.loads(stdout)
        self.assertEqual(descriptor["runtime_path"], RUNTIME_PATH)
        self.assertEqual(descriptor["assurance"], "declared_enforcement")
        self.assertEqual(descriptor["run_requirement"], {"kind": "trusted_runtime_confirmation"})

    def test_compiles_both_vendor_families_through_the_same_role_contract(self) -> None:
        """Breaks if Codex or Claude gets a weaker, incompatible delegation shape."""
        for vendor in ("claude", "codex"):
            with self.subTest(vendor=vendor):
                result = self._route(source_vendor=vendor)
                self.assertEqual(result.returncode, 0, result.stderr)
                descriptor = json.loads(result.stdout)
                self.assertEqual(descriptor["source_vendor"], vendor)
                self.assertEqual(set(descriptor["roles"]), {"orchestrator", "worker", "reviewer"})
                for role in descriptor["roles"].values():
                    self.assertEqual(role["vendor_family"], vendor)
                    self.assertEqual(role["transport"], "native")

    def test_printed_descriptor_reproduces_its_fingerprint(self) -> None:
        """Breaks if review output omits a hidden fingerprint input."""
        result = self._route()
        self.assertEqual(result.returncode, 0, result.stderr)
        descriptor = json.loads(result.stdout)
        fingerprint = descriptor.pop("route_fingerprint")
        payload = json.dumps(
            descriptor,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        self.assertEqual(fingerprint, f"sha256:{hashlib.sha256(payload).hexdigest()}")

    def test_unused_declaration_order_does_not_change_the_fingerprint(self) -> None:
        """Breaks if hidden policy ordering becomes part of the reviewed route."""
        baseline = self._route()
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        reordered = _policy()
        profiles = reordered["profiles"]
        workflows = reordered["workflows"]
        assert isinstance(profiles, list)
        assert isinstance(workflows, list)
        profiles.reverse()
        workflows.reverse()

        changed = self._route(policy=reordered)

        self.assertEqual(changed.returncode, 0, changed.stderr)
        self.assertEqual(
            json.loads(changed.stdout)["route_fingerprint"],
            json.loads(baseline.stdout)["route_fingerprint"],
        )

    def test_multiple_exact_workflow_matches_fail_closed(self) -> None:
        """Breaks if declaration order silently chooses one ambiguous workflow."""
        policy = _policy()
        workflows = policy["workflows"]
        assert isinstance(workflows, list)
        duplicate = deepcopy(workflows[0])
        assert isinstance(duplicate, dict)
        duplicate["id"] = "claude-second-standard-delegation"
        workflows.append(duplicate)

        result = self._route(policy=policy)

        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stderr), {"error": "unsupported_route"})

    def test_cross_vendor_roles_fail_closed_in_protocol_one(self) -> None:
        """Breaks if a same-vendor route can silently launch another vendor family."""
        policy = _policy()
        workflows = policy["workflows"]
        assert isinstance(workflows, list)
        workflow = workflows[0]
        assert isinstance(workflow, dict)
        profiles = workflow["profiles"]
        assert isinstance(profiles, dict)
        profiles["worker"] = "codex-worker"

        result = self._route(policy=policy)

        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stderr), {"error": "unsupported_route"})

    def test_nonempty_boundary_authorization_requires_a_later_protocol(self) -> None:
        """Breaks if one broad declaration enables an unimplemented crossed boundary."""
        policy = _policy()
        workflows = policy["workflows"]
        assert isinstance(workflows, list)
        workflow = workflows[0]
        assert isinstance(workflow, dict)
        boundaries = workflow["boundary_authorizations"]
        assert isinstance(boundaries, dict)
        boundaries["provider_pairs"] = [{"from": "anthropic", "to": "openai"}]

        result = self._route(policy=policy)

        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stderr), {"error": "unsupported_route"})

    def test_flat_enforcement_primitive_declarations_are_rejected(self) -> None:
        """Breaks if an opaque label is treated as proof of allow and deny controls."""
        baseline = self._route()
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        manifest = _manifest()
        adapters = manifest["adapters"]
        assert isinstance(adapters, list)
        adapter = adapters[0]
        assert isinstance(adapter, dict)
        adapter["enforcement_primitives"] = {
            "workspace_read": "opaque-read",
            "workspace_write": "opaque-write",
            "command_execution": "opaque-command",
            "process_isolation": "opaque-process",
        }

        result = self._route(manifest=manifest)

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})

    def test_boolean_schema_version_is_not_an_integer(self) -> None:
        """Breaks if Python's bool-as-int subtype bypasses a version gate."""
        baseline = self._route()
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        policy = _policy()
        policy["schema_version"] = True

        result = self._route(policy=policy)

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})

    def test_duplicate_platform_declarations_are_rejected(self) -> None:
        """Breaks if platform selection depends on declaration order."""
        baseline = self._route()
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        manifest = _manifest()
        platforms = manifest["supported_platforms"]
        assert isinstance(platforms, list)
        platforms.append(deepcopy(platforms[0]))

        result = self._route(manifest=manifest)

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})

    def test_excessive_json_nesting_fails_with_a_redacted_diagnostic(self) -> None:
        """Breaks if parser recursion can escape the delegation error boundary."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path = directory / "deep-policy.json"
            manifest_path = directory / "runtime-manifest.json"
            nested_value = "[" * 1_500 + "0" + "]" * 1_500
            policy_path.write_text(
                '{"schema_version":1,"profiles":' + nested_value + ',"workflows":[]}',
                encoding="utf-8",
            )
            manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "delegate",
                    "route",
                    "--policy",
                    str(policy_path),
                    "--runtime-manifest",
                    str(manifest_path),
                    "--delegation-runtime",
                    RUNTIME_PATH,
                    "--source-vendor",
                    "claude",
                    "--tier",
                    "standard",
                ],
                capture_output=True,
                check=False,
                text=True,
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})
        self.assertNotIn("Traceback", result.stderr)

    def test_relative_runtime_path_is_rejected_without_resolving_it(self) -> None:
        """Breaks if route review inherits a runtime from the caller's working directory."""
        baseline = self._route()
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        result = self._route(runtime_path="runtime")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})

    def test_task_data_is_absent_from_success_and_failure_output(self) -> None:
        """Breaks if delegate route starts consuming or reporting transient task content."""
        distinctive_task = "zephyrine glimmerfast quokka"
        result = self._route(task_input=distinctive_task)
        invalid = self._route(runtime_path="relative-runtime", task_input=distinctive_task)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(invalid.returncode, 2)

        for stream in (result.stdout, result.stderr, invalid.stdout, invalid.stderr):
            for word in distinctive_task.split():
                self.assertNotIn(word, stream)


if __name__ == "__main__":
    unittest.main()
