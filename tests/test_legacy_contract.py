import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from weightclass.delegation_protocol import encode_delegation_frame
from weightclass.router import DEFAULT_ROUTES, native_route_fingerprint


class LegacyContractTests(unittest.TestCase):
    def _cli(self, *arguments: str, task: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "weightclass", *arguments],
            input=task,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_builtin_routes_and_fingerprints_have_frozen_bytes(self) -> None:
        rows = [
            (r.route_id, r.vendor, r.tier, list(r.command), native_route_fingerprint(r, False))
            for r in DEFAULT_ROUTES
        ]
        self.assertEqual(
            json.dumps(rows, ensure_ascii=True, separators=(",", ":")).encode(), EXPECTED_BUILTINS
        )

    def test_wcd1_bytes_remain_unchanged(self) -> None:
        descriptor = b'{"descriptor_schema_version":1}'
        task = "Fix caf\u00e9"
        expected = (
            b"WCD1"
            + struct.pack(">I", len(descriptor))
            + descriptor
            + struct.pack(">I", len(task.encode()))
            + task.encode()
        )
        self.assertEqual(encode_delegation_frame(descriptor, task), expected)

    def test_cli_invalid_input_is_value_free_and_task_private(self) -> None:
        secret = "PRIVATE-TASK-CONTENT"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "weightclass",
                "route",
                "--suggest-tier",
                "--policy",
                "/definitely/missing",
            ],
            input=secret,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})
        self.assertNotIn(secret, result.stdout + result.stderr)

    def test_absent_version_native_schema_one_route_bytes_are_frozen(self) -> None:
        policy = {
            "routes": [
                {
                    "id": "legacy",
                    "vendor": "codex",
                    "tier": "low",
                    "command": ["owned-fake", "--fixed"],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(policy), encoding="utf-8")
            result = self._cli("route", "--policy", str(path), "--tier", "low", task="legacy task")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            '{"command": ["owned-fake", "--fixed"], "route": "legacy", '
            '"tier": "low", "vendor": "codex", "route_fingerprint": '
            '"sha256:62bb321ad863d908cf93919e5b45db08922bbad2be41220ef0368c87fcdba3c3"}\n',
        )

    def test_explicit_schema_one_alias_is_byte_identical(self) -> None:
        policy = {
            "schema_version": 1,
            "routes": [
                {
                    "id": "legacy",
                    "vendor": "codex",
                    "tier": "low",
                    "command": ["owned-fake", "--fixed"],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(policy), encoding="utf-8")
            result = self._cli("route", "--policy", str(path), "--tier", "low", task="private")
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(
            result.stdout,
            '{"command": ["owned-fake", "--fixed"], "route": "legacy", '
            '"tier": "low", "vendor": "codex", "route_fingerprint": '
            '"sha256:62bb321ad863d908cf93919e5b45db08922bbad2be41220ef0368c87fcdba3c3"}\n',
        )

    def test_legacy_render_bytes_are_frozen(self) -> None:
        policy = {
            "routes": [
                {
                    "id": "legacy",
                    "vendor": "claude",
                    "workflow": "review",
                    "command": ["owned-fake", "--print"],
                }
            ]
        }
        descriptor = {"vendor": "claude", "workflow": "review"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path, descriptor_path = root / "policy.json", root / "descriptor.json"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")
            result = self._cli(
                "render", "--policy", str(policy_path), "--descriptor", str(descriptor_path)
            )
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(
            result.stdout, '{"command": ["owned-fake", "--print"], "route": "legacy"}\n'
        )


EXPECTED_BUILTINS = (
    b'[["codex-low","codex","low",["codex","exec","--ephemeral","--sandbox",'
    b'"workspace-write","-c","model_reasoning_effort=low","-"],'
    b'"sha256:fed094c37c73e6f71bdacfd498248d51c14bd178d4a076237cc22e4d588369b9"],'
    b'["codex-standard","codex","standard",["codex","exec","--ephemeral",'
    b'"--sandbox","workspace-write","-c","model_reasoning_effort=medium","-"],'
    b'"sha256:887072c5a643c62407f6136800a4e068a7f5fe2edcf11228255777f234b59126"],'
    b'["codex-high","codex","high",["codex","exec","--ephemeral","--sandbox",'
    b'"workspace-write","-c","model_reasoning_effort=high","-"],'
    b'"sha256:fccd476109d6a0bf8c77cf92ed049e6ad406e761d10b81610271a70d9a9fb94a"],'
    b'["claude-low","claude","low",["claude","--print","--no-session-persistence",'
    b'"--permission-mode","acceptEdits","--effort","low"],'
    b'"sha256:488cce9cf49eff9179b985897de002101cfbe21e4cc54c6bd9ff0453c3a7bab3"],'
    b'["claude-standard","claude","standard",["claude","--print",'
    b'"--no-session-persistence","--permission-mode","acceptEdits","--effort","medium"],'
    b'"sha256:2cff910e26bf1d2de7ffe74cbe2a78331428f42292701a64890da8fcb0e727e8"],'
    b'["claude-high","claude","high",["claude","--print","--no-session-persistence",'
    b'"--permission-mode","acceptEdits","--effort","high"],'
    b'"sha256:b4fdc8fc64becdc93cf5cc438c058550cca6d00e33f42535e0b63d1edbda18b0"],'
    b'["agy-low","agy","low",["agy","--print","{{task}}","--mode","accept-edits",'
    b'"--effort","low"],'
    b'"sha256:7145e8378541f2b64e5cf4f811fcb78d08354dba98066cf24a859e1a29906ca9"],'
    b'["agy-standard","agy","standard",["agy","--print","{{task}}","--mode",'
    b'"accept-edits","--effort","medium"],'
    b'"sha256:5de03b92f50a013e8e318e1e2bb1791d07d52210dc3a6594e3a981ec2d39a41b"],'
    b'["agy-high","agy","high",["agy","--print","{{task}}","--mode","accept-edits",'
    b'"--effort","high"],'
    b'"sha256:6294541a7054de64e5bf89da85c3cbd85941c562c0c831af43eeaed38f058b64"],'
    b'["grok-low","grok","low",["grok","-p","{{task}}","--permission-mode",'
    b'"acceptEdits","--reasoning-effort","low"],'
    b'"sha256:21fa8116c73daec8e477647952c77e6fa4494d053b3179ea8ee601d81a44bc1a"],'
    b'["grok-standard","grok","standard",["grok","-p","{{task}}","--permission-mode",'
    b'"acceptEdits","--reasoning-effort","medium"],'
    b'"sha256:d99ec8ab2d3b8339a575c092325b0f22d56a933ba7138bbdca788103d226894e"],'
    b'["grok-high","grok","high",["grok","-p","{{task}}","--permission-mode",'
    b'"acceptEdits","--reasoning-effort","high"],'
    b'"sha256:75f5893a91505f4ba5f3a65cb21cca6f618cfe80531183bd3e6bdc9716cac916"]]'
)


if __name__ == "__main__":
    unittest.main()
