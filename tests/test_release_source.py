from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ReleaseSourceTests(unittest.TestCase):
    def _git(self, repository: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            capture_output=True,
            check=True,
            text=True,
        )
        return result.stdout.strip()

    def _commit(self, repository: Path, name: str) -> str:
        (repository / "tracked.txt").write_text(name, encoding="utf-8")
        self._git(repository, "add", "tracked.txt")
        self._git(repository, "commit", "-m", name)
        return self._git(repository, "rev-parse", "HEAD")

    def _verify(self, repository: Path, commit: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("verify_release_source.py")),
                "--repository",
                str(repository),
                "--tag-commit",
                commit,
                "--main-ref",
                "refs/heads/main",
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )

    def test_accepts_only_a_tag_commit_reachable_from_reviewed_main(self) -> None:
        """Breaks if an unmerged detached commit can authorize a release."""
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            self._git(repository, "init", "--initial-branch=main")
            self._git(repository, "config", "user.name", "Release Test")
            self._git(repository, "config", "user.email", "release-test@example.invalid")
            reviewed_commit = self._commit(repository, "reviewed")
            self._git(repository, "switch", "-c", "unmerged")
            unmerged_commit = self._commit(repository, "unmerged")
            self._git(repository, "switch", "main")
            self._commit(repository, "later-main")

            accepted = self._verify(repository, reviewed_commit)
            rejected = self._verify(repository, unmerged_commit)

        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(accepted.stdout, "")
        self.assertEqual(accepted.stderr, "")
        self.assertEqual(rejected.returncode, 1)
        self.assertEqual(rejected.stdout, "")
        self.assertEqual(rejected.stderr, "release source verification failed\n")
        self.assertNotIn(unmerged_commit, rejected.stderr)

    def test_rejects_an_unknown_or_noncanonical_commit_without_echoing_it(self) -> None:
        """Breaks if git diagnostics or caller values escape the release boundary."""
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            self._git(repository, "init", "--initial-branch=main")
            self._git(repository, "config", "user.name", "Release Test")
            self._git(repository, "config", "user.email", "release-test@example.invalid")
            self._commit(repository, "reviewed")

            for candidate in ("f" * 40, "HEAD"):
                with self.subTest(candidate=candidate):
                    result = self._verify(repository, candidate)

                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(result.stderr, "release source verification failed\n")
                    self.assertNotIn(candidate, result.stderr)


if __name__ == "__main__":
    unittest.main()
