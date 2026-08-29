from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from weightclass.executable_observation import ExecutableObservation

ROOT = Path(__file__).resolve().parent.parent
ACCEPTANCE_ENABLED = os.environ.get("WCLASS_HARDENING_BATCH_ACCEPTANCE") == "1"


@unittest.skipUnless(ACCEPTANCE_ENABLED, "prospective advisory hardening batch acceptance")
class AdvisoryHardeningBatchAcceptanceTests(unittest.TestCase):
    def _run(
        self,
        *arguments: str,
        task: str = "Add one focused unit test.",
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        selected_environment = os.environ.copy() if environment is None else dict(environment)
        selected_environment["PYTHONPATH"] = str(ROOT / "src")
        return subprocess.run(
            [sys.executable, "-m", "weightclass", *arguments],
            cwd=ROOT,
            capture_output=True,
            check=False,
            env=selected_environment,
            input=task,
            text=True,
            timeout=15,
        )

    def _policy(self, directory: Path, executable: str) -> Path:
        policy = directory / "policy.json"
        policy.write_text(
            json.dumps(
                {
                    "routes": [
                        {
                            "id": "custom-standard",
                            "vendor": "kiro",
                            "tier": "standard",
                            "command": [executable],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return policy

    def _recording_executable(self, path: Path, record: Path) -> None:
        path.write_text(
            "#!"
            + sys.executable
            + "\nimport json,sys\n"
            + f"record={str(record)!r}\n"
            + "json.dump({'argv':sys.argv,'stdin':sys.stdin.read()},open(record,'w'))\n",
            encoding="utf-8",
        )
        path.chmod(0o700)

    def test_legacy_custom_route_is_byte_compatible_and_disclosed_only_on_explain(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            policy = self._policy(directory, "/bin/echo")
            ordinary = self._run(
                "route",
                "--policy",
                str(policy),
                "--source-vendor",
                "kiro",
                "--tier",
                "standard",
            )
            explained = self._run(
                "route",
                "--policy",
                str(policy),
                "--source-vendor",
                "kiro",
                "--tier",
                "standard",
                "--explain",
            )

        self.assertEqual(ordinary.returncode, 0, ordinary.stderr)
        self.assertEqual(explained.returncode, 0, explained.stderr)
        ordinary_value = json.loads(ordinary.stdout)
        explained_value = json.loads(explained.stdout)
        self.assertEqual(
            set(ordinary_value),
            {"command", "route", "tier", "vendor", "route_fingerprint"},
        )
        self.assertEqual(explained_value["executable_binding"], "legacy_lexical")
        self.assertEqual(ordinary_value["route_fingerprint"], explained_value["route_fingerprint"])

    def test_observed_binding_resolves_runs_and_rejects_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            target = directory / "kiro-real"
            link = directory / "kiro-link"
            record = directory / "record.json"
            self._recording_executable(target, record)
            link.symlink_to(target.name)
            policy = self._policy(directory, str(link))
            route_arguments = (
                "route",
                "--policy",
                str(policy),
                "--source-vendor",
                "kiro",
                "--tier",
                "standard",
                "--bind-executable-identity",
            )
            reviewed = self._run(*route_arguments)
            self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
            receipt = json.loads(reviewed.stdout)
            self.assertEqual(receipt["command"][0], str(target))
            self.assertEqual(receipt["executable_binding"], "observed")
            self.assertEqual(
                set(receipt["executable_identity"]),
                set(ExecutableObservation.__dataclass_fields__),
            )
            run_arguments = (
                "run",
                "--policy",
                str(policy),
                "--source-vendor",
                "kiro",
                "--tier",
                "standard",
                "--bind-executable-identity",
                "--ack-route-fingerprint",
                receipt["route_fingerprint"],
            )
            executed = self._run(*run_arguments, task="Implement the bounded change.")
            self.assertEqual(executed.returncode, 0, executed.stderr)
            recorded = json.loads(record.read_text(encoding="utf-8"))
            self.assertEqual(recorded["argv"][0], str(target))
            self.assertEqual(recorded["stdin"], "Implement the bounded change.")

            target.unlink()
            self._recording_executable(target, record)
            record.unlink(missing_ok=True)
            rejected = self._run(*run_arguments, task="Do not disclose this task.")

        self.assertEqual(rejected.returncode, 6)
        self.assertEqual(json.loads(rejected.stderr), {"error": "route_fingerprint_mismatch"})
        self.assertFalse(record.exists())

    def test_observed_binding_fails_closed_for_relative_or_nonpolicy_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            policy = self._policy(Path(directory_name), "kiro-cli")
            relative = self._run(
                "route",
                "--policy",
                str(policy),
                "--source-vendor",
                "kiro",
                "--tier",
                "standard",
                "--bind-executable-identity",
            )
        nonpolicy = self._run(
            "route",
            "--source-vendor",
            "codex",
            "--tier",
            "standard",
            "--bind-executable-identity",
            task="",
        )

        self.assertEqual(relative.returncode, 3)
        self.assertEqual(json.loads(relative.stderr), {"error": "unsupported_route"})
        self.assertEqual(nonpolicy.returncode, 2)
        self.assertEqual(json.loads(nonpolicy.stderr), {"error": "invalid_input"})

    def test_bedrock_is_an_explicit_v2_destination_but_not_a_kiro_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            record = directory / "runtime.json"
            runtime = directory / "runtime"
            self._recording_executable(runtime, record)
            policy = directory / "bedrock.json"
            policy_value = {
                "schema_version": 2,
                "allow_cross_provider": True,
                "allow_api": True,
                "routes": [
                    {
                        "id": "bedrock-standard",
                        "tier": "standard",
                        "eligible_source_vendors": ["claude"],
                        "provider": "bedrock",
                        "transport": "api",
                        "model": "opaque-bedrock-model",
                        "effort": "medium",
                        "intended_recipient": "AWS Bedrock",
                        "intended_billing_boundary": "operator AWS account",
                    }
                ],
            }
            policy.write_text(json.dumps(policy_value), encoding="utf-8")
            reviewed = self._run(
                "v2",
                "route",
                "--policy",
                str(policy),
                "--source-vendor",
                "claude",
                "--api-runtime",
                str(runtime),
            )
            self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
            receipt = json.loads(reviewed.stdout)
            self.assertEqual(receipt["destination"]["provider"], "bedrock")
            self.assertEqual(receipt["destination"]["intended_recipient"], "AWS Bedrock")
            self.assertNotIn("credential", reviewed.stdout.casefold())
            executed = self._run(
                "v2",
                "run",
                "--policy",
                str(policy),
                "--source-vendor",
                "claude",
                "--api-runtime",
                str(runtime),
                "--confirm-api-egress",
                "--ack-route-fingerprint",
                receipt["route_fingerprint"],
                task="Call the reviewed runtime.",
            )
            self.assertEqual(executed.returncode, 0, executed.stderr)
            recorded = json.loads(record.read_text(encoding="utf-8"))
            self.assertEqual(
                recorded["argv"][1:],
                [
                    "--provider",
                    "bedrock",
                    "--model",
                    "opaque-bedrock-model",
                    "--effort",
                    "medium",
                ],
            )
            self.assertEqual(recorded["stdin"], "Call the reviewed runtime.")

            policy_value["allow_cross_provider"] = False
            policy.write_text(json.dumps(policy_value), encoding="utf-8")
            blocked = self._run(
                "v2",
                "route",
                "--policy",
                str(policy),
                "--source-vendor",
                "claude",
                "--api-runtime",
                str(runtime),
            )
            kiro_source = self._run(
                "v2",
                "route",
                "--policy",
                str(policy),
                "--source-vendor",
                "kiro",
                "--api-runtime",
                str(runtime),
            )

        self.assertEqual(blocked.returncode, 3)
        self.assertEqual(json.loads(blocked.stderr), {"error": "unsupported_route"})
        self.assertEqual(kiro_source.returncode, 2)
        self.assertEqual(json.loads(kiro_source.stderr), {"error": "invalid_input"})

    def test_cli_import_and_parser_defer_nonessential_families(self) -> None:
        program = """
import json,sys
import weightclass.cli as cli
cli.build_parser()
print(json.dumps(sorted(name for name in sys.modules if name in {
    'weightclass.cost_recommendation',
    'weightclass.foreground_process',
    'weightclass.usage_aggregation',
})))
"""
        completed = subprocess.run(
            [sys.executable, "-c", program],
            cwd=ROOT,
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
            text=True,
            timeout=10,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), [])

    def test_validation_only_advisory_paths_use_count_streaming(self) -> None:
        from weightclass.advisory import managed_advisory

        configuration_source = inspect.getsource(managed_advisory._configuration)
        ordinal_source = inspect.getsource(managed_advisory._next_ordinal)
        migration_source = inspect.getsource(managed_advisory.migrate_vendor_campaigns)
        self.assertIn("count_bound_lane_records", configuration_source)
        self.assertNotIn("load_merged_lane_records", configuration_source)
        self.assertIn("count_bound_records", ordinal_source)
        self.assertNotIn("load_bound_records", ordinal_source)
        self.assertIn("count_bound_lane_records", migration_source)
        self.assertNotIn("load_merged_lane_records", migration_source)

    def test_handoff_and_kiro_documentation_state_the_exact_residuals(self) -> None:
        handoff = (ROOT / "HANDOFF.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("lock/replace operations are not directory-fd anchored", handoff)
        self.assertIn("parent descriptor", handoff)
        self.assertIn("before the parent is opened", handoff)
        self.assertIn("Verified-object execution remains an open architecture item", handoff)

        marker = "<!-- kiro-custom-policy -->"
        self.assertIn(marker, readme)
        section = readme.split(marker, 1)[1].split("<!-- /kiro-custom-policy -->", 1)[0]
        for phrase in (
            "--bind-executable-identity",
            "not a built-in",
            "task_delivery",
            "argv",
            "session",
            "log",
            "path-based",
            "Kiro source family",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase.casefold(), section.casefold())


if __name__ == "__main__":
    unittest.main()
