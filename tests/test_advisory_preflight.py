from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from weightclass.advisory import advisory_preflight, managed_advisory


class AdvisoryPreflightTests(unittest.TestCase):
    def _fake_cli(self, root: Path, vendor: str, help_text: str) -> Path:
        path = root / vendor
        path.write_text(
            "#!/bin/sh\n"
            'case " $* " in\n'
            "  *' --version '*) printf '%s\\n' '" + vendor + " 9.9.9' ;;\n"
            "  *) printf '%s\\n' '" + help_text + "' ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        path.chmod(0o700)
        return path

    def test_builtin_specs_accept_complete_help_and_report_safe_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for vendor, spec in advisory_preflight._SPECS.items():
                with self.subTest(vendor=vendor):
                    self._fake_cli(root, vendor, " ".join(spec.required_help_tokens))
            environment = {"PATH": str(root)}
            with mock.patch.dict(os.environ, environment, clear=True):
                results = [
                    advisory_preflight.check_local_capability(vendor, vendor)
                    for vendor in advisory_preflight._SPECS
                ]

        self.assertTrue(all(result.ready for result in results))
        self.assertTrue(all(result.status == "ready" for result in results))
        self.assertTrue(all(result.version == f"{result.vendor} 9.9.9" for result in results))

    def test_missing_and_incompatible_executables_fail_with_fixed_codes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fake_cli(root, "claude", "--print only")
            with mock.patch.dict(os.environ, {"PATH": str(root)}, clear=True):
                missing = advisory_preflight.check_local_capability("codex", "codex")
                incompatible = advisory_preflight.check_local_capability("claude", "claude")

        self.assertEqual((missing.status, missing.failure_code), ("missing", "executable_missing"))
        self.assertEqual(
            (incompatible.status, incompatible.failure_code),
            ("incompatible", "cli_incompatible"),
        )

    def test_custom_vendor_checks_only_executable_presence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fake_cli(root, "acme", "custom help")
            with mock.patch.dict(os.environ, {"PATH": str(root)}, clear=True):
                result = advisory_preflight.check_local_capability("acme", "acme")

        self.assertTrue(result.ready)
        self.assertEqual(result.status, "custom_unverified")
        self.assertIsNone(result.version)

    def test_probe_output_and_time_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            noisy = root / "noisy"
            noisy.write_text("#!/bin/sh\nprintf '%0200d' 1\n", encoding="utf-8")
            noisy.chmod(0o700)
            with mock.patch.object(advisory_preflight, "MAX_PROBE_BYTES", 32):
                code, payload = advisory_preflight._bounded_command((str(noisy),))
            self.assertEqual(code, 125)
            self.assertEqual(payload, b"")

            slow = root / "slow"
            slow.write_text("#!/bin/sh\n/bin/sleep 5\n", encoding="utf-8")
            slow.chmod(0o700)
            started = time.monotonic()
            with mock.patch.object(advisory_preflight, "PROBE_TIMEOUT_SECONDS", 0.05):
                code, payload = advisory_preflight._bounded_command((str(slow),))
            self.assertEqual(code, 124)
            self.assertEqual(payload, b"")
            self.assertLess(time.monotonic() - started, 1.5)

    def test_version_text_is_bounded_to_safe_printable_ascii(self) -> None:
        self.assertIsNone(advisory_preflight._safe_version(b"x" * 121))
        self.assertIsNone(advisory_preflight._safe_version("비ASCII".encode()))
        self.assertEqual(advisory_preflight._safe_version(b"tool 1.2.3\n"), "tool 1.2.3")

    def test_cli_check_is_explicitly_task_free_and_uses_a_minimal_environment(self) -> None:
        ready = advisory_preflight.CapabilityResult("codex", "ready", "none", "codex 1")
        with mock.patch.object(advisory_preflight, "check_local_capability", return_value=ready):
            with mock.patch("builtins.print") as printed:
                code = managed_advisory.cli_check_main(("--vendor", "codex"))

        self.assertEqual(code, 0)
        payload = printed.call_args.args[0]
        self.assertIn('"task_free":true', payload)
        self.assertIn('"task_bytes_sent":false', payload)
        self.assertIn('"provider_request_sent":false', payload)
        self.assertIn('"environment_policy":"minimal"', payload)

    def test_builtin_probe_scrubs_unrelated_environment_and_rejects_cwd_binary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observed = root / "observed"
            spec = advisory_preflight._SPECS["claude"]
            executable = self._fake_cli(root, "claude", " ".join(spec.required_help_tokens))
            original = executable.read_text(encoding="utf-8")
            executable.write_text(
                original.replace(
                    "#!/bin/sh\n",
                    (
                        "#!/bin/sh\n"
                        f'test -n "$WCLASS_TEST_SECRET" && printf inherited > {observed!s}\n'
                    ),
                ),
                encoding="utf-8",
            )
            executable.chmod(0o700)
            with mock.patch.dict(
                os.environ,
                {"PATH": str(root), "WCLASS_TEST_SECRET": "sensitive-value"},
                clear=True,
            ):
                result = advisory_preflight.check_local_capability("claude", "claude")

            self.assertTrue(result.ready)
            self.assertFalse(observed.exists())
            with (
                mock.patch.object(Path, "cwd", return_value=root),
                mock.patch.dict(os.environ, {"PATH": str(root)}, clear=True),
            ):
                rejected = advisory_preflight.check_local_capability("claude", "claude")
            self.assertEqual(
                (rejected.status, rejected.failure_code),
                ("unsafe", "unsafe_executable"),
            )


if __name__ == "__main__":
    unittest.main()
