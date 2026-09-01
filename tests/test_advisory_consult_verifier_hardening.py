from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from weightclass import __version__
from weightclass.advisory import advisory_consult, managed_verify, speculative_run


class AdvisoryConsultConfirmationTests(unittest.TestCase):
    def test_missing_confirmation_is_value_free_and_precedes_task_access(self) -> None:
        private_marker = "PRIVATE-TASK-MUST-NOT-BE-OPENED"
        arguments = [
            "--expected-package-version",
            __version__,
            "--workflow",
            "research",
            "--vendor",
            "custom",
            "--role",
            "cheap",
            "--repo",
            "/missing/repo",
            "--task-file",
            f"/missing/{private_marker}",
            "--route-profile",
            "/missing/profile",
            "--expected-route-sha256",
            "sha256:" + "0" * 64,
            "--verify",
            "/missing/verifier",
        ]
        stderr = io.StringIO()
        with (
            mock.patch.object(
                speculative_run,
                "read_task_file",
                side_effect=AssertionError("task accessed"),
            ) as read_task,
            contextlib.redirect_stderr(stderr),
        ):
            returncode = advisory_consult.main(arguments)

        self.assertEqual(returncode, 2)
        read_task.assert_not_called()
        self.assertEqual(
            json.loads(stderr.getvalue()),
            {"error": "advisory_consult_task_egress_confirmation_required"},
        )
        self.assertNotIn(private_marker, stderr.getvalue())


@unittest.skipUnless(os.name == "posix", "bounded Git capture requires POSIX descriptors")
class ManagedVerifierHardeningTests(unittest.TestCase):
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

    def _commit(self, repository: Path, message: str) -> str:
        subprocess.run(
            ["git", "add", "."],
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
                message,
            ],
            cwd=repository,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_git_blob_keeps_resolved_object_when_head_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = self._repository(Path(directory))
            verifier = repository / ".weightclass" / "verify"
            verifier.parent.mkdir()
            original_payload = b"#!/bin/sh\nexit 41\n"
            verifier.write_bytes(original_payload)
            first_commit = self._commit(repository, "first")
            verifier.write_bytes(b"#!/bin/sh\nexit 42\n")
            second_commit = self._commit(repository, "second")
            subprocess.run(
                ["git", "update-ref", "HEAD", first_commit],
                cwd=repository,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            original_capture = managed_verify._git_bounded_stdout
            swapped = False

            def capture(max_stdout_bytes: int, *arguments: str) -> bytes | None:
                nonlocal swapped
                result = original_capture(max_stdout_bytes, *arguments)
                if arguments[:2] == ("rev-parse", "--verify"):
                    subprocess.run(
                        ["git", "update-ref", "HEAD", second_commit],
                        cwd=repository,
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    swapped = True
                return result

            previous = Path.cwd()
            try:
                os.chdir(repository)
                with mock.patch.object(managed_verify, "_git_bounded_stdout", side_effect=capture):
                    payload = managed_verify._git_blob("HEAD:.weightclass/verify")
            finally:
                os.chdir(previous)

        self.assertTrue(swapped)
        self.assertEqual(payload, original_payload)

    def test_git_blob_rejects_output_larger_than_the_verified_size_and_cap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_git = root / "git"
            fake_git.write_text(
                "#!/usr/bin/env python3\n"
                "import os,sys\n"
                "arguments=sys.argv[1:]\n"
                "if 'rev-parse' in arguments:\n"
                "    print('a' * 40)\n"
                "elif 'cat-file' in arguments:\n"
                "    index=arguments.index('cat-file')\n"
                "    mode=arguments[index + 1]\n"
                "    if mode == '-t': print('blob')\n"
                "    elif mode == '-s': print('1')\n"
                "    elif mode == 'blob': os.write(1, b'x' * 33)\n"
                "    else: raise SystemExit(2)\n"
                "else:\n"
                "    raise SystemExit(2)\n",
                encoding="utf-8",
            )
            fake_git.chmod(0o700)
            environment = {"PATH": f"{root}{os.pathsep}{os.environ.get('PATH', '')}"}
            with (
                mock.patch.dict(os.environ, environment),
                mock.patch.object(managed_verify, "MAX_VERIFIER_BYTES", 32),
            ):
                payload = managed_verify._git_blob("HEAD:.weightclass/verify")

        self.assertIsNone(payload)

    def test_git_capture_selector_failure_stops_child_and_closes_stdout(self) -> None:
        process = mock.Mock(spec=subprocess.Popen)
        process.stdout = mock.Mock()
        process.stdout.closed = False
        with (
            mock.patch(
                "weightclass.advisory.managed_verify.subprocess.Popen",
                return_value=process,
            ),
            mock.patch(
                "weightclass.advisory.managed_verify.selectors.DefaultSelector",
                side_effect=OSError(),
            ),
            mock.patch.object(managed_verify, "_stop_process") as stop_process,
        ):
            payload = managed_verify._git_bounded_stdout(32, "rev-parse", "HEAD")

        self.assertIsNone(payload)
        stop_process.assert_called_once_with(process)
        process.stdout.close.assert_called_once_with()

    def test_git_capture_timeout_stops_unreaped_group_without_polling(self) -> None:
        process = mock.Mock(spec=subprocess.Popen)
        process.stdout = mock.Mock()
        process.stdout.closed = False
        process.stdout.fileno.return_value = 123
        selector = mock.Mock()
        selector.get_map.return_value = {123: object()}
        selector.select.return_value = []
        with (
            mock.patch(
                "weightclass.advisory.managed_verify.subprocess.Popen",
                return_value=process,
            ),
            mock.patch(
                "weightclass.advisory.managed_verify.selectors.DefaultSelector",
                return_value=selector,
            ),
            mock.patch("weightclass.advisory.managed_verify.os.set_blocking"),
            mock.patch.object(managed_verify, "_stop_process") as stop_process,
        ):
            payload = managed_verify._git_bounded_stdout(32, "rev-parse", "HEAD")

        self.assertIsNone(payload)
        stop_process.assert_called_once_with(process)
        process.poll.assert_not_called()

    def test_copied_standalone_verifier_runs_a_legitimate_small_blob(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = self._repository(Path(directory))
            verifier = repository / ".weightclass" / "verify-review"
            verifier.parent.mkdir()
            verifier.write_text(
                '#!/bin/sh\nread value\n[ -n "$value" ] && exit 42\nexit 1\n',
                encoding="utf-8",
            )
            verifier.chmod(0o700)
            self._commit(repository, "verifier")
            standalone = repository / "managed-verify.py"
            standalone.write_bytes(Path(managed_verify.__file__).read_bytes())
            environment = dict(os.environ)
            environment["WCLASS_ADVISORY_WORKFLOW"] = "review"
            completed = subprocess.run(
                [sys.executable, str(standalone)],
                cwd=repository,
                input=b"baseline probe\n",
                capture_output=True,
                check=False,
                env=environment,
            )

        self.assertEqual(completed.returncode, 42, completed.stdout + completed.stderr)

    def test_candidate_cannot_change_a_verifier_zone_helper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = self._repository(Path(directory))
            protected = repository / ".weightclass"
            protected.mkdir()
            verifier = protected / "verify"
            verifier.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            verifier.chmod(0o700)
            helper = protected / "criteria.txt"
            helper.write_text("strict\n", encoding="utf-8")
            self._commit(repository, "protected verifier zone")
            helper.write_text("weakened\n", encoding="utf-8")
            previous = Path.cwd()
            output = io.StringIO()
            try:
                os.chdir(repository)
                with mock.patch.object(sys, "stdout", output):
                    returncode = managed_verify.main()
            finally:
                os.chdir(previous)

        self.assertEqual(returncode, 1)
        self.assertIn("protected verifier zone", output.getvalue())

    def test_project_verifier_does_not_inherit_unrelated_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = self._repository(Path(directory))
            verifier = repository / ".weightclass" / "verify"
            verifier.parent.mkdir()
            verifier.write_text(
                "#!/bin/sh\n"
                'test -z "${WCLASS_SYNTHETIC_CREDENTIAL+x}" || exit 9\n'
                'test "$WCLASS_ADVISORY_WORKFLOW" = implementation\n',
                encoding="utf-8",
            )
            verifier.chmod(0o700)
            self._commit(repository, "environment verifier")
            previous = Path.cwd()
            try:
                os.chdir(repository)
                with mock.patch.dict(
                    os.environ,
                    {"WCLASS_SYNTHETIC_CREDENTIAL": "must-not-reach-verifier"},
                ):
                    returncode = managed_verify.main()
            finally:
                os.chdir(previous)

        self.assertEqual(returncode, 0)

    def test_project_verifier_timeout_kills_same_group_descendant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = self._repository(Path(directory))
            sentinel = Path(directory) / "verifier-descendant-survived"
            verifier = repository / ".weightclass" / "verify"
            verifier.parent.mkdir()
            verifier.write_text(
                "#!/usr/bin/env python3\n"
                "import os\n"
                "from pathlib import Path\n"
                "import time\n"
                "if os.fork() == 0:\n"
                "    time.sleep(1.2)\n"
                f"    Path({str(sentinel)!r}).write_text('survived', encoding='utf-8')\n"
                "    os._exit(0)\n"
                "time.sleep(60)\n",
                encoding="utf-8",
            )
            verifier.chmod(0o700)
            self._commit(repository, "timeout verifier")
            previous = Path.cwd()
            started = time.monotonic()
            output = io.StringIO()
            try:
                os.chdir(repository)
                with (
                    mock.patch.object(managed_verify, "TIMEOUT_SECONDS", 0.8),
                    contextlib.redirect_stdout(output),
                ):
                    returncode = managed_verify.main()
            finally:
                os.chdir(previous)

            self.assertLess(time.monotonic() - started, 2.0)
            time.sleep(1.4)
            self.assertFalse(sentinel.exists())

        self.assertEqual(returncode, 1)
        self.assertEqual(
            output.getvalue().strip(),
            "verification failed: committed verifier could not run",
        )


if __name__ == "__main__":
    unittest.main()
