from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from weightclass.advisory import managed_verify, safe_git, speculative_run


@unittest.skipUnless(shutil.which("git"), "git is required")
class AdvisorySafeGitTests(unittest.TestCase):
    def _repository(self, root: Path) -> Path:
        repository = root / "repo"
        repository.mkdir()
        subprocess.run(
            ["git", "init", "-q"],
            cwd=repository,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return repository

    def test_environment_drops_git_routing_and_disables_optional_locks(self) -> None:
        environment = safe_git.hardened_environment(
            {
                "PATH": "/usr/bin",
                "HOME": "/private/home",
                "GIT_DIR": "/private/escape",
                "GIT_CONFIG_COUNT": "1",
            }
        )

        self.assertEqual(environment["PATH"], "/usr/bin")
        self.assertEqual(environment["HOME"], "/private/home")
        self.assertNotIn("GIT_DIR", environment)
        self.assertNotIn("GIT_CONFIG_COUNT", environment)
        self.assertEqual(environment["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(environment["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(environment["GIT_OPTIONAL_LOCKS"], "0")
        self.assertEqual(environment["GIT_TERMINAL_PROMPT"], "0")

    def test_large_binary_diff_is_terminated_at_the_output_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = self._repository(Path(directory))
            (repository / "payload.bin").write_bytes(b"x" * 4096)
            added = safe_git.run(
                ["add", "payload.bin"],
                cwd=repository,
                environment=os.environ,
                timeout_seconds=5,
                max_stdout_bytes=1024,
                max_stderr_bytes=1024,
            )
            self.assertEqual(added.returncode, 0)

            with self.assertRaisesRegex(safe_git.SafeGitError, "^$") as raised:
                safe_git.run(
                    ["diff", "--cached", "--binary"],
                    cwd=repository,
                    environment=os.environ,
                    timeout_seconds=5,
                    max_stdout_bytes=64,
                    max_stderr_bytes=1024,
                )

        self.assertEqual(raised.exception.code, "output_limited")

    def test_patch_builder_fails_closed_when_the_verified_patch_exceeds_its_cap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = self._repository(Path(directory))
            (repository / "payload.txt").write_text("x" * 4096, encoding="utf-8")

            with (
                mock.patch.object(speculative_run, "MAX_PATCH_BYTES", 64),
                self.assertRaisesRegex(speculative_run.RunFailure, "git output exceeded limit"),
            ):
                speculative_run.make_patch(repository)

    def test_managed_verifier_rejects_an_oversized_committed_script_before_loading_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = self._repository(Path(directory))
            verifier = repository / ".weightclass" / "verify"
            verifier.parent.mkdir()
            verifier.write_bytes(b"#!/bin/sh\n#" + b"x" * managed_verify.MAX_VERIFIER_BYTES)
            verifier.chmod(0o700)
            subprocess.run(
                ["git", "add", ".weightclass/verify"],
                cwd=repository,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=weightclass-test",
                    "-c",
                    "user.email=test.invalid@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                cwd=repository,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            previous = Path.cwd()
            output = io.StringIO()
            try:
                os.chdir(repository)
                with contextlib.redirect_stdout(output):
                    returncode = managed_verify.main()
            finally:
                os.chdir(previous)

        self.assertEqual(returncode, 1)
        self.assertEqual(
            output.getvalue().strip(),
            "verification failed: committed verifier is missing or invalid",
        )


if __name__ == "__main__":
    unittest.main()
