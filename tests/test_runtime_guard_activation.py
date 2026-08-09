import socket
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

from tests.runtime_guard import RuntimeGuard, RuntimeGuardViolation


class RuntimeGuardActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.guard = RuntimeGuard()

    def assert_rejected(self, argv: list[str], **kwargs: Any) -> None:
        with self.guard.activated(), self.assertRaises(RuntimeGuardViolation):
            subprocess.run(argv, **kwargs)

    def test_rejects_path_lookup_and_bare_commands(self) -> None:
        self.assert_rejected(["true"], check=False)

    def test_rejects_shell_execution(self) -> None:
        self.assert_rejected([str(Path("/bin/true"))], shell=True, check=False)

    def test_rejects_named_provider_commands(self) -> None:
        for command in ("codex", "claude"):
            with self.subTest(command=command):
                self.assert_rejected([command], check=False)

    def test_rejects_an_absolute_nonallowlisted_path(self) -> None:
        self.assert_rejected([str(Path("/bin/true"))], check=False)

    def test_popen_is_independently_guarded(self) -> None:
        with self.guard.activated(), self.assertRaises(RuntimeGuardViolation):
            subprocess.Popen(["true"])

    def test_run_is_independently_guarded(self) -> None:
        self.assert_rejected(["true"], check=False)

    def test_exact_registered_prefix_is_required(self) -> None:
        python = Path(sys.executable).resolve()
        self.guard.register_executable(python, "-c", "pass")
        with self.guard.activated():
            completed = subprocess.run([str(python), "-c", "pass"], check=False)
            self.assertEqual(completed.returncode, 0)
            with self.assertRaises(RuntimeGuardViolation):
                subprocess.run([str(python), "-c", "raise SystemExit(9)"], check=False)

    def test_rejects_an_explicit_executable_override(self) -> None:
        python = Path(sys.executable).resolve()
        self.guard.register_executable(python, "-c", "pass")
        with self.guard.activated(), self.assertRaises(RuntimeGuardViolation):
            subprocess.run(
                [str(python), "-c", "pass"],
                executable=str(Path("/bin/true")),
                check=False,
            )

    def test_owned_fake_executable_succeeds(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "fake_native_runtime.py"
        prefix = self.guard.register_python_harness(fixture)
        with self.guard.activated():
            completed = subprocess.run(prefix, input=b"task", check=False)
        self.assertEqual(completed.returncode, 0)

    def test_inet_families_are_rejected(self) -> None:
        with self.guard.activated():
            for family in (socket.AF_INET, socket.AF_INET6):
                with self.subTest(family=family), self.assertRaises(RuntimeGuardViolation):
                    socket.socket(family, socket.SOCK_STREAM)

    def test_create_connection_is_rejected(self) -> None:
        with self.guard.activated(), self.assertRaises(RuntimeGuardViolation):
            socket.create_connection(("127.0.0.1", 1))

    def test_af_unix_and_socketpair_remain_available(self) -> None:
        with self.guard.activated():
            unix_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            unix_socket.close()
            left, right = socket.socketpair()
            left.close()
            right.close()


if __name__ == "__main__":
    unittest.main()
