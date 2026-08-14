from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class AgentDiscoveryCliTests(unittest.TestCase):
    def test_discovers_known_executables_without_starting_them(self) -> None:
        """Breaks if local discovery executes a vendor or overstates availability."""
        with tempfile.TemporaryDirectory() as directory:
            bin_directory = Path(directory) / "bin"
            bin_directory.mkdir()
            marker = Path(directory) / "vendor-started"
            for executable_name in ("codex", "grok"):
                executable = bin_directory / executable_name
                executable.write_text(
                    '#!/bin/sh\n: > "$DISCOVERY_MARKER"\n',
                    encoding="utf-8",
                )
                executable.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = str(bin_directory)
            environment["DISCOVERY_MARKER"] = str(marker)

            result = subprocess.run(
                [sys.executable, "-m", "weightclass", "discover"],
                capture_output=True,
                check=False,
                env=environment,
                input="private task that discovery must not read",
                text=True,
            )
            vendor_was_started = marker.exists()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertFalse(vendor_was_started)
        payload = json.loads(result.stdout)
        self.assertEqual(
            {
                "schema_version": payload["schema_version"],
                "discovery_mode": payload["discovery_mode"],
                "network_used": payload["network_used"],
                "vendor_processes_started": payload["vendor_processes_started"],
            },
            {
                "schema_version": 1,
                "discovery_mode": "local_path_only",
                "network_used": False,
                "vendor_processes_started": False,
            },
        )
        agents = {agent["agent"]: agent for agent in payload["agents"]}
        self.assertEqual(
            [agent["agent"] for agent in payload["agents"]],
            ["agy", "claude", "codex", "grok"],
        )
        self.assertEqual(set(agents), {"agy", "claude", "codex", "grok"})
        self.assertEqual(agents["codex"]["executable"], str(bin_directory / "codex"))
        self.assertTrue(agents["codex"]["executable_detected"])
        self.assertEqual(agents["grok"]["task_delivery"], "argv")
        self.assertEqual(agents["claude"]["executable"], None)
        self.assertFalse(agents["claude"]["executable_detected"])
        for agent in agents.values():
            self.assertEqual(
                agent["effort_catalog"],
                {
                    "availability_verified": False,
                    "source": "package_catalog",
                    "values": ["low", "medium", "high"],
                },
            )
            self.assertEqual(agent["model_catalog"]["values"], ["default"])
            self.assertFalse(agent["model_catalog"]["availability_verified"])
            self.assertEqual(agent["subscription"], "unknown")
            self.assertEqual(agent["pricing"], "unknown")
            self.assertEqual(agent["quota"], "unknown")
        self.assertNotIn("private task", result.stdout)

    def test_selected_agent_model_and_effort_generate_a_routable_policy(self) -> None:
        """Breaks if profile selection stops compiling the exact reviewed command."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_directory = root / "bin"
            bin_directory.mkdir()
            marker = root / "vendor-started"
            executable = bin_directory / "codex"
            executable.write_text(
                '#!/bin/sh\n: > "$DISCOVERY_MARKER"\n',
                encoding="utf-8",
            )
            executable.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = str(bin_directory)
            environment["DISCOVERY_MARKER"] = str(marker)

            generated = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "profile",
                    "--agent",
                    "codex",
                    "--tier",
                    "low",
                    "--model",
                    "user-selected-model",
                    "--effort",
                    "low",
                    "--allow-cross-vendor",
                ],
                capture_output=True,
                check=False,
                env=environment,
                input="private task that profile generation must not read",
                text=True,
            )
            vendor_was_started = marker.exists()

            self.assertEqual(generated.returncode, 0, generated.stderr)
            self.assertEqual(generated.stderr, "")
            self.assertFalse(vendor_was_started)
            policy = json.loads(generated.stdout)
            expected_command = [
                str(executable),
                "exec",
                "--ephemeral",
                "--sandbox",
                "workspace-write",
                "--model",
                "user-selected-model",
                "-c",
                "model_reasoning_effort=low",
                "-",
            ]
            self.assertEqual(
                policy,
                {
                    "schema_version": 1,
                    "allow_mixed_vendors": True,
                    "posture": "balanced",
                    "routes": [
                        {
                            "id": "selected-codex-low",
                            "vendor": "codex",
                            "tier": "low",
                            "command": expected_command,
                        }
                    ],
                },
            )
            policy_path = root / "selected-policy.json"
            policy_path.write_text(generated.stdout, encoding="utf-8")
            reviewed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "route",
                    "--policy",
                    str(policy_path),
                    "--source-vendor",
                    "claude",
                    "--tier",
                    "low",
                ],
                capture_output=True,
                check=False,
                env=environment,
                input="Fix a spelling typo.",
                text=True,
            )

        self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
        self.assertEqual(json.loads(reviewed.stdout)["command"], expected_command)
        self.assertNotIn("Fix a spelling typo.", reviewed.stdout)

    def test_every_supported_adapter_builds_its_reviewed_command_shape(self) -> None:
        """Breaks if selection generates a command that differs from a built-in adapter."""
        with tempfile.TemporaryDirectory() as directory:
            bin_directory = Path(directory) / "bin"
            bin_directory.mkdir()
            for executable_name in ("agy", "claude", "grok"):
                executable = bin_directory / executable_name
                executable.write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")
                executable.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = str(bin_directory)
            cases = (
                (
                    "claude",
                    "selected-claude-model",
                    "medium",
                    [
                        str(bin_directory / "claude"),
                        "--print",
                        "--no-session-persistence",
                        "--permission-mode",
                        "acceptEdits",
                        "--model",
                        "selected-claude-model",
                        "--effort",
                        "medium",
                    ],
                ),
                (
                    "agy",
                    "default",
                    "low",
                    [
                        str(bin_directory / "agy"),
                        "--print",
                        "{{task}}",
                        "--mode",
                        "accept-edits",
                        "--effort",
                        "low",
                    ],
                ),
                (
                    "grok",
                    "selected-grok-model",
                    "high",
                    [
                        str(bin_directory / "grok"),
                        "-p",
                        "{{task}}",
                        "--permission-mode",
                        "acceptEdits",
                        "--model",
                        "selected-grok-model",
                        "--reasoning-effort",
                        "high",
                    ],
                ),
            )
            for agent, model, effort, expected_command in cases:
                with self.subTest(agent=agent):
                    result = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "weightclass",
                            "profile",
                            "--agent",
                            agent,
                            "--tier",
                            "low",
                            "--model",
                            model,
                            "--effort",
                            effort,
                        ],
                        capture_output=True,
                        check=False,
                        env=environment,
                        input="",
                        text=True,
                    )

                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        json.loads(result.stdout)["routes"][0]["command"],
                        expected_command,
                    )

    def test_discovery_can_select_one_agent_catalog(self) -> None:
        """Breaks if callers must infer one selection from an unrelated full inventory."""
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "grok"
            executable.write_text("#!/bin/sh\nexit 92\n", encoding="utf-8")
            executable.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = directory

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "discover",
                    "--agent",
                    "grok",
                ],
                capture_output=True,
                check=False,
                env=environment,
                input="",
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        agents = json.loads(result.stdout)["agents"]
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0]["agent"], "grok")
        self.assertEqual(agents[0]["executable"], str(executable))

    def test_profile_fails_closed_when_the_selected_executable_is_absent(self) -> None:
        """Breaks if an undetected agent produces a policy that can run another binary."""
        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment["PATH"] = directory
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "profile",
                    "--agent",
                    "claude",
                    "--tier",
                    "low",
                    "--effort",
                    "low",
                ],
                capture_output=True,
                check=False,
                env=environment,
                input="private absent-agent task",
                text=True,
            )

        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(result.stderr), {"error": "unsupported_route"})
        self.assertNotIn("private absent-agent task", result.stderr)

    def test_profile_rejects_unreviewable_or_unsupported_model_overrides(self) -> None:
        """Breaks if a model label can become an option or an unsupported agy argument."""
        with tempfile.TemporaryDirectory() as directory:
            for executable_name in ("agy", "codex"):
                executable = Path(directory) / executable_name
                executable.write_text("#!/bin/sh\nexit 93\n", encoding="utf-8")
                executable.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = directory
            for agent, model in (("codex", "--unsafe-option"), ("agy", "opaque-model")):
                with self.subTest(agent=agent, model=model):
                    result = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "weightclass",
                            "profile",
                            "--agent",
                            agent,
                            "--tier",
                            "low",
                            "--model",
                            model,
                            "--effort",
                            "low",
                        ],
                        capture_output=True,
                        check=False,
                        env=environment,
                        input="",
                        text=True,
                    )

                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})

    def test_profile_ignores_relative_path_entries(self) -> None:
        """Breaks if the current directory can silently supply the selected executable."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative_bin = root / "relative-bin"
            relative_bin.mkdir()
            executable = relative_bin / "codex"
            executable.write_text("#!/bin/sh\nexit 94\n", encoding="utf-8")
            executable.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = "relative-bin"
            environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "weightclass",
                    "profile",
                    "--agent",
                    "codex",
                    "--tier",
                    "low",
                    "--effort",
                    "low",
                ],
                capture_output=True,
                check=False,
                cwd=root,
                env=environment,
                input="",
                text=True,
            )

        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stderr), {"error": "unsupported_route"})


if __name__ == "__main__":
    unittest.main()
