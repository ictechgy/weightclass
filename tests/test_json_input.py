import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest import mock

from weightclass import cli, json_input

MAX_RUNTIME_JSON_BYTES = 262_144


def _native_policy() -> dict[str, object]:
    return {
        "routes": [
            {
                "id": "codex-low",
                "vendor": "codex",
                "tier": "low",
                "command": ["codex", "exec"],
            }
        ]
    }


def _api_policy() -> dict[str, object]:
    return {
        "schema_version": 2,
        "allow_cross_provider": False,
        "allow_api": True,
        "routes": [
            {
                "id": "openai-low-api",
                "tier": "low",
                "eligible_source_vendors": ["codex"],
                "provider": "openai",
                "transport": "api",
                "model": "opaque-model",
                "effort": "low",
                "intended_recipient": "OpenAI API",
                "intended_billing_boundary": "user OpenAI API account",
            }
        ],
    }


class RuntimeJsonInputTests(unittest.TestCase):
    def _run(
        self, arguments: list[str], task: str = "Fix a typo."
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [sys.executable, "-m", "weightclass", *arguments],
                capture_output=True,
                check=False,
                input=task,
                text=True,
                timeout=2,
            )
        except subprocess.TimeoutExpired:
            self.fail("runtime JSON input blocked instead of failing closed")

    def _assert_invalid_input(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_input"})

    def test_native_policy_rejects_duplicate_keys(self) -> None:
        """Breaks if a security gate silently uses the last duplicate value."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            raw = json.dumps(_native_policy())[:-1]
            path.write_text(
                raw + ', "allow_mixed_vendors": false, "allow_mixed_vendors": true}',
                encoding="utf-8",
            )

            result = self._run(["route", "--policy", str(path), "--source-vendor", "codex"])

        self._assert_invalid_input(result)

    def test_native_descriptor_rejects_duplicate_keys(self) -> None:
        """Breaks if a reviewed request can be replaced by its final duplicate key."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = root / "policy.json"
            descriptor_path = root / "descriptor.json"
            policy_path.write_text(json.dumps(_native_policy()), encoding="utf-8")
            descriptor_path.write_text(
                '{"vendor":"claude","vendor":"codex","workflow":"review"}',
                encoding="utf-8",
            )

            result = self._run(
                [
                    "render",
                    "--policy",
                    str(policy_path),
                    "--descriptor",
                    str(descriptor_path),
                ],
                task="",
            )

        self._assert_invalid_input(result)

    def test_v2_policy_rejects_duplicate_top_level_keys(self) -> None:
        """Breaks if a duplicated API kill switch can be changed by ordering."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            raw = json.dumps(_api_policy()).replace(
                '"allow_api": true', '"allow_api": false, "allow_api": true'
            )
            path.write_text(raw, encoding="utf-8")

            result = self._run(
                [
                    "v2",
                    "route",
                    "--policy",
                    str(path),
                    "--source-vendor",
                    "codex",
                    "--api-runtime",
                    sys.executable,
                ]
            )

        self._assert_invalid_input(result)

    def test_v2_policy_rejects_duplicate_nested_keys(self) -> None:
        """Breaks if duplicate detection covers only the document root."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            raw = json.dumps(_api_policy()).replace(
                '"provider": "openai"', '"provider": "anthropic", "provider": "openai"'
            )
            path.write_text(raw, encoding="utf-8")

            result = self._run(
                [
                    "v2",
                    "route",
                    "--policy",
                    str(path),
                    "--source-vendor",
                    "codex",
                    "--api-runtime",
                    sys.executable,
                ]
            )

        self._assert_invalid_input(result)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO is not available")
    def test_every_runtime_json_consumer_rejects_a_fifo_promptly(self) -> None:
        """Breaks if validation stats a path and then blocks while opening it."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            native_policy_path = root / "native.json"
            native_policy_path.write_text(json.dumps(_native_policy()), encoding="utf-8")

            cases = (
                (
                    "native policy",
                    ["route", "--policy", str(root / "policy-fifo"), "--source-vendor", "codex"],
                    "task",
                ),
                (
                    "native descriptor",
                    [
                        "render",
                        "--policy",
                        str(native_policy_path),
                        "--descriptor",
                        str(root / "descriptor-fifo"),
                    ],
                    "",
                ),
                (
                    "v2 policy",
                    [
                        "v2",
                        "route",
                        "--policy",
                        str(root / "v2-fifo"),
                        "--source-vendor",
                        "codex",
                        "--api-runtime",
                        sys.executable,
                    ],
                    "task",
                ),
            )
            for label, arguments, task in cases:
                with self.subTest(consumer=label):
                    fifo_path = Path(arguments[arguments.index("--policy") + 1])
                    if label == "native descriptor":
                        fifo_path = Path(arguments[arguments.index("--descriptor") + 1])
                    os.mkfifo(fifo_path)
                    result = self._run(arguments, task=task)
                    self._assert_invalid_input(result)

    def test_native_policy_rejects_more_than_the_common_byte_limit(self) -> None:
        """Breaks if native inputs remain unbounded while V2 is capped."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            encoded = json.dumps(_native_policy()).encode("utf-8")
            path.write_bytes(encoded + b" " * (MAX_RUNTIME_JSON_BYTES + 1 - len(encoded)))

            result = self._run(["route", "--policy", str(path), "--source-vendor", "codex"])

        self._assert_invalid_input(result)

    def test_native_policy_accepts_exactly_the_common_byte_limit(self) -> None:
        """Breaks if the raw-byte boundary is accidentally implemented as exclusive."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            encoded = json.dumps(_native_policy()).encode("utf-8")
            path.write_bytes(encoded + b" " * (MAX_RUNTIME_JSON_BYTES - len(encoded)))

            result = self._run(["route", "--policy", str(path), "--source-vendor", "codex"])

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_open_descriptor_is_validated_independently_of_path_replacement(self) -> None:
        """Breaks if parsing reopens a pathname after validating a different file."""
        load_open_descriptor = getattr(json_input, "_load_json_object_from_open_fd", None)
        self.assertIsNotNone(load_open_descriptor)
        if load_open_descriptor is None:
            return

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text('{"source":"opened"}', encoding="utf-8")
            file_descriptor = os.open(path, os.O_RDONLY)
            path.unlink()
            path.write_text('{"source":"replacement"}', encoding="utf-8")

            try:
                result = load_open_descriptor(
                    file_descriptor,
                    max_bytes=MAX_RUNTIME_JSON_BYTES,
                )
                with self.assertRaises(OSError):
                    os.fstat(file_descriptor)
            finally:
                try:
                    os.close(file_descriptor)
                except OSError:
                    pass

        self.assertEqual(result, {"source": "opened"})

    def test_open_descriptor_is_closed_when_the_byte_limit_is_invalid(self) -> None:
        """Breaks if argument validation leaks an already-owned descriptor."""
        load_open_descriptor = getattr(json_input, "_load_json_object_from_open_fd", None)
        self.assertIsNotNone(load_open_descriptor)
        if load_open_descriptor is None:
            return

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text("{}", encoding="utf-8")
            file_descriptor = os.open(path, os.O_RDONLY)

            try:
                with self.assertRaises(json_input.JsonInputError):
                    load_open_descriptor(file_descriptor, max_bytes=0)
                with self.assertRaises(OSError):
                    os.fstat(file_descriptor)
            finally:
                try:
                    os.close(file_descriptor)
                except OSError:
                    pass

    def test_invalid_static_policy_is_rejected_before_task_input_is_read(self) -> None:
        """Breaks if invalid configuration causes unnecessary task handling."""
        with tempfile.TemporaryDirectory() as directory:
            invalid_policy = Path(directory) / "invalid.json"
            invalid_policy.write_text('{"duplicate":1,"duplicate":2}', encoding="utf-8")
            calls: tuple[Callable[[], int], ...] = (
                lambda: cli.route_from_standard_input(invalid_policy, "codex"),
                lambda: cli.run_from_standard_input(invalid_policy, "codex"),
                lambda: cli.v2_route_from_standard_input(
                    invalid_policy, "codex", Path(sys.executable)
                ),
                lambda: cli.v2_run_from_standard_input(
                    invalid_policy,
                    "codex",
                    Path(sys.executable),
                    False,
                    None,
                ),
            )
            for invoke in calls:
                with self.subTest(call=invoke):
                    task_was_read = False

                    def read_task() -> str:
                        nonlocal task_was_read
                        task_was_read = True
                        return "Fix a typo."

                    errors = io.StringIO()
                    with (
                        mock.patch(
                            "weightclass.cli.read_task_from_standard_input",
                            side_effect=read_task,
                        ),
                        contextlib.redirect_stderr(errors),
                    ):
                        exit_code = invoke()

                    self.assertEqual(exit_code, 2)
                    self.assertEqual(json.loads(errors.getvalue()), {"error": "invalid_input"})
                    self.assertFalse(task_was_read)


if __name__ == "__main__":
    unittest.main()
