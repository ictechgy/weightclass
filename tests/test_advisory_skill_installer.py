from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import cast
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "src" / "weightclass" / "advisory"
INSTALLER = TOOLS / "install_advisory_skill.py"
BUNDLE = TOOLS / "skill"


def load_installer() -> types.ModuleType:
    if not INSTALLER.is_file():
        raise AssertionError("repository advisory skill installer is missing")
    spec = importlib.util.spec_from_file_location("advisory_skill_installer", INSTALLER)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load advisory skill installer")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(TOOLS.is_dir(), "repository-only tools unavailable")
class AdvisorySkillInstallerTests(unittest.TestCase):
    def test_bundle_is_portable_and_uses_the_short_advisory_name(self) -> None:
        self.assertTrue(BUNDLE.is_dir())
        skill = (BUNDLE / "SKILL.md").read_text(encoding="utf-8")
        modes = (BUNDLE / "references" / "modes.md").read_text(encoding="utf-8")
        metadata = (BUNDLE / "agents" / "openai.yaml").read_text(encoding="utf-8")
        manifest = json.loads((BUNDLE / "manifest.json").read_text(encoding="utf-8"))

        self.assertIn("name: advisory", skill)
        self.assertIn("$advisory", metadata)
        self.assertNotIn("wclass-advisory\n", skill.split("name:", 1)[1].splitlines()[0])
        self.assertNotIn("/Users/", skill + modes + metadata)
        for workflow in ("implementation", "review", "research", "diagnosis", "design"):
            self.assertIn(f"`{workflow}`", modes)
        self.assertIn("Brainstorming is not a production workflow", modes)
        self.assertEqual(manifest, {"managed_onboarding": 18, "schema_version": 1})
        self.assertIn("managed_runner_version_changed", skill + modes)
        self.assertIn("managed_setup_busy", skill + modes)

    def test_install_skill_is_the_documented_vendor_path_exception(self) -> None:
        # 이 예외는 설치 코드를 편집하는 순간 눈에 들어와야 한다. 그래서 규칙은
        # 그 디렉터리를 지배하는 가이드에 있어야 하고, 루트는 거기로 갈 수 있게
        # 가리켜야 한다. 둘 중 하나만으로는 계약이 성립하지 않는다.
        root_guide_path = ROOT / "AGENTS.md"
        scoped_guide_path = ROOT / "src" / "weightclass" / "advisory" / "AGENTS.md"
        skill_guide_path = ROOT / "docs" / "advisory-skill.md"
        if not all(
            path.is_file() for path in (root_guide_path, scoped_guide_path, skill_guide_path)
        ):
            self.skipTest("repository policy documents are intentionally absent from the sdist")
        root_guide = root_guide_path.read_text(encoding="utf-8")
        scoped_guide = scoped_guide_path.read_text(encoding="utf-8")
        skill_guide = skill_guide_path.read_text(encoding="utf-8")

        self.assertIn("sole vendor-recognized-path exception", scoped_guide)
        self.assertIn("src/weightclass/advisory/AGENTS.md", root_guide)
        self.assertIn("sole vendor-recognized-path exception", skill_guide)
        self.assertIn("historical bundle", skill_guide)
        self.assertIn("compatibility ledger", skill_guide)

    def test_published_0178_bundle_is_a_safe_upgrade_source(self) -> None:
        installer = load_installer()
        self.assertEqual(
            set(installer.RELEASE_0178_BUNDLE_FILE_SHA256),
            set(installer.EXPECTED_FILES),
        )
        self.assertEqual(
            set(installer.RELEASE_0179_BUNDLE_FILE_SHA256),
            {
                "SKILL.md",
                "manifest.json",
                "agents/openai.yaml",
                "references/modes.md",
            },
        )
        self.assertEqual(
            set(installer.RELEASE_0180_BUNDLE_FILE_SHA256),
            set(installer.EXPECTED_FILES),
        )
        self.assertEqual(
            set(installer.RELEASE_0190_BUNDLE_FILE_SHA256),
            set(installer.EXPECTED_FILES),
        )
        self.assertEqual(
            installer.RELEASE_0260_BUNDLE_FILE_SHA256,
            {
                "SKILL.md": "b2afc03c6310f72dd286a25a1c3726908960cfd15b42341ce3596a7a0d69858f",
                "manifest.json": "01122cf85e2d4aec74de69803f3bd01e7c67757390521473432ad50a6e1a668c",
                "agents/openai.yaml": (
                    "b946bd779de9ec40e785fecfe7950956c41d51d2d230e4bec4897d1381f443e1"
                ),
                "references/modes.md": (
                    "edeab0bd355a4a10a4f8f98ce3a47b4ac18a2433443ed514048cd6916a759c6f"
                ),
            },
        )
        self.assertEqual(
            installer.RELEASE_0271_BUNDLE_FILE_SHA256,
            {
                "SKILL.md": "d13ea9e08f45a94de86b1e605f3ffd92c7efe1609a54c56a5143df5d1dcfce77",
                "manifest.json": "ddce5171e479240e01eeef036fc9ee4a6c73db31f47dbe3147f7d51550c566c5",
                "agents/openai.yaml": (
                    "5aee8388c2735994411240ea01273df1f0dfa8fcf71bf9876c854b1722564e44"
                ),
                "references/modes.md": (
                    "b1a22cda0a5588f5d154831de1e768808f1c2c898d4d7f7c767c8a147672465a"
                ),
            },
        )
        self.assertEqual(
            installer.RELEASE_0280_BUNDLE_FILE_SHA256,
            {
                "SKILL.md": "5addd08391cfa6aeab1d23e1026ccf1e27a2cb53628bd703ee564df6c019f277",
                "manifest.json": "da1da5dcc1a0dd3419bcb719833904e12b142a78b7cce655e168ace3ef9895d5",
                "agents/openai.yaml": (
                    "810aaf67c1f4dfa324f17691c618d5f4945f50c688d238c14eaa5fddea3f7644"
                ),
                "references/modes.md": (
                    "2a9ed2d32048b60cbfb8ccdc25e32c0bf0c3b3358c836d2c871a4370eabfd291"
                ),
            },
        )
        ledger_names = (
            "LEGACY_FILE_SHA256",
            "PREVIOUS_BUNDLE_FILE_SHA256",
            "ADDITIONAL_PREVIOUS_BUNDLE_FILE_SHA256",
            "LATEST_PREVIOUS_BUNDLE_FILE_SHA256",
            "CURRENT_PREVIOUS_BUNDLE_FILE_SHA256",
            "NEXT_PREVIOUS_BUNDLE_FILE_SHA256",
            "FINAL_PREVIOUS_BUNDLE_FILE_SHA256",
            "RELEASE_0176_BUNDLE_FILE_SHA256",
            "RELEASE_0177_BUNDLE_FILE_SHA256",
            "RELEASE_0178_BUNDLE_FILE_SHA256",
            "RELEASE_0179_BUNDLE_FILE_SHA256",
            "RELEASE_0180_BUNDLE_FILE_SHA256",
            "RELEASE_0190_BUNDLE_FILE_SHA256",
            "RELEASE_0260_BUNDLE_FILE_SHA256",
            "RELEASE_0271_BUNDLE_FILE_SHA256",
            "RELEASE_0280_BUNDLE_FILE_SHA256",
        )
        for name in ledger_names:
            with self.subTest(name=name):
                for digest in getattr(installer, name).values():
                    self.assertRegex(digest, re.compile(r"[0-9a-f]{64}\Z"))

    def test_install_both_is_private_exact_and_idempotent(self) -> None:
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            first = installer.install_skill(
                BUNDLE,
                home=home,
                target="both",
                dry_run=False,
                advisory_command_available=True,
            )
            second = installer.install_skill(
                BUNDLE,
                home=home,
                target="both",
                dry_run=False,
                advisory_command_available=True,
            )

            for root in (home / ".agents" / "skills", home / ".claude" / "skills"):
                installed = root / "advisory"
                self.assertEqual(
                    (installed / "SKILL.md").read_bytes(),
                    (BUNDLE / "SKILL.md").read_bytes(),
                )
                self.assertEqual(installed.stat().st_mode & 0o777, 0o700)
                for path in installed.rglob("*"):
                    self.assertFalse(path.is_symlink())
                    expected_mode = 0o700 if path.is_dir() else 0o600
                    self.assertEqual(path.stat().st_mode & 0o777, expected_mode)

        self.assertEqual(first["installed"], ["codex", "claude"])
        self.assertEqual(second["already_installed"], ["codex", "claude"])

    def test_uninstall_removes_only_an_exact_package_bundle(self) -> None:
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            installer.install_skill(
                BUNDLE,
                home=home,
                target="codex",
                dry_run=False,
                advisory_command_available=True,
            )
            destination = home / ".agents" / "skills" / "advisory"

            preview = installer.uninstall_skill(
                BUNDLE,
                home=home,
                target="codex",
                dry_run=True,
            )
            self.assertEqual(preview["removal_planned"], ["codex"])
            self.assertTrue(destination.is_dir())

            receipt = installer.uninstall_skill(
                BUNDLE,
                home=home,
                target="codex",
                dry_run=False,
            )

        self.assertEqual(receipt["removed"], ["codex"])
        self.assertFalse(destination.exists())

    def test_uninstall_revalidates_a_name_swap_before_deleting(self) -> None:
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            installer.install_skill(
                BUNDLE,
                home=home,
                target="codex",
                dry_run=False,
                advisory_command_available=True,
            )
            parent = home / ".agents" / "skills"
            destination = parent / "advisory"
            replacement = parent / "replacement"
            backup = parent / "package-backup"
            (replacement / "agents").mkdir(parents=True, mode=0o700)
            (replacement / "references").mkdir(mode=0o700)
            for relative in installer.EXPECTED_FILES:
                target = replacement / relative
                payload = (
                    b"custom\n" if relative == "SKILL.md" else (BUNDLE / relative).read_bytes()
                )
                target.write_bytes(payload)
                target.chmod(0o600)
            replacement.chmod(0o700)
            real_rename = os.rename
            swapped = False

            def swap_before_tombstone(
                source: str,
                target: str,
                *,
                src_dir_fd: int | None = None,
                dst_dir_fd: int | None = None,
            ) -> None:
                nonlocal swapped
                if source == "advisory" and target.startswith(".advisory-skill-remove-"):
                    self.assertFalse(swapped)
                    swapped = True
                    real_rename(destination, backup)
                    real_rename(replacement, destination)
                real_rename(
                    source,
                    target,
                    src_dir_fd=src_dir_fd,
                    dst_dir_fd=dst_dir_fd,
                )

            with (
                mock.patch.object(installer.os, "rename", side_effect=swap_before_tombstone),
                self.assertRaisesRegex(installer.SkillInstallError, "^skill_conflict$"),
            ):
                installer.uninstall_skill(
                    BUNDLE,
                    home=home,
                    target="codex",
                    dry_run=False,
                )

            self.assertTrue(swapped)
            self.assertEqual((destination / "SKILL.md").read_bytes(), b"custom\n")
            self.assertTrue((backup / "SKILL.md").is_file())

    def test_conflict_fails_before_any_target_is_written(self) -> None:
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            conflict = home / ".claude" / "skills" / "advisory"
            conflict.mkdir(parents=True)
            (conflict / "SKILL.md").write_text("different\n", encoding="utf-8")

            with self.assertRaisesRegex(installer.SkillInstallError, "^skill_conflict$"):
                installer.install_skill(
                    BUNDLE,
                    home=home,
                    target="both",
                    dry_run=False,
                    advisory_command_available=True,
                )

            self.assertFalse((home / ".agents" / "skills" / "advisory").exists())

    def test_status_reports_customized_target_without_failing(self) -> None:
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            conflict = home / ".claude" / "skills" / "advisory"
            conflict.mkdir(parents=True)
            (conflict / "SKILL.md").write_text("customized\n", encoding="utf-8")

            receipt = installer.skill_status(BUNDLE, home=home, target="both")

        self.assertEqual(receipt["conflicts"], ["claude"])
        self.assertEqual(receipt["planned"], ["codex"])
        self.assertTrue(receipt["dry_run"])

    def test_explicit_upgrade_replaces_only_a_recognized_legacy_bundle(self) -> None:
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            installed = home / ".agents" / "skills" / "advisory"
            (installed / "agents").mkdir(parents=True, mode=0o700)
            (installed / "references").mkdir(mode=0o700)
            legacy_hashes: dict[str, str] = {}
            for relative in installer.LEGACY_FILES:
                payload = (BUNDLE / relative).read_bytes()
                target = installed / relative
                target.write_bytes(payload)
                target.chmod(0o600)
                legacy_hashes[relative] = hashlib.sha256(payload).hexdigest()
            installed.chmod(0o700)
            with mock.patch.object(installer, "LEGACY_FILE_SHA256", legacy_hashes):
                preview = installer.install_skill(
                    BUNDLE,
                    home=home,
                    target="codex",
                    dry_run=True,
                    advisory_command_available=True,
                    upgrade=True,
                )
                receipt = installer.install_skill(
                    BUNDLE,
                    home=home,
                    target="codex",
                    dry_run=False,
                    advisory_command_available=True,
                    upgrade=True,
                )

            self.assertEqual(preview["upgrade_planned"], ["codex"])
            self.assertEqual(receipt["upgraded"], ["codex"])
            self.assertEqual(
                (installed / "manifest.json").read_bytes(),
                (BUNDLE / "manifest.json").read_bytes(),
            )
            self.assertEqual(installed.stat().st_mode & 0o777, 0o700)

    def test_explicit_upgrade_accepts_an_exact_previous_four_file_bundle(self) -> None:
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            installed = home / ".agents" / "skills" / "advisory"
            (installed / "agents").mkdir(parents=True, mode=0o700)
            (installed / "references").mkdir(mode=0o700)
            previous_hashes: dict[str, str] = {}
            for relative in installer.EXPECTED_FILES:
                payload = (BUNDLE / relative).read_bytes()
                if relative == "SKILL.md":
                    payload += b"\nprevious-version\n"
                target = installed / relative
                target.write_bytes(payload)
                target.chmod(0o600)
                previous_hashes[relative] = hashlib.sha256(payload).hexdigest()
            installed.chmod(0o700)

            with mock.patch.object(
                installer,
                "PREVIOUS_BUNDLE_FILE_SHA256",
                previous_hashes,
            ):
                receipt = installer.install_skill(
                    BUNDLE,
                    home=home,
                    target="codex",
                    dry_run=False,
                    advisory_command_available=True,
                    upgrade=True,
                )

            self.assertEqual(receipt["upgraded"], ["codex"])
            self.assertEqual(
                (installed / "SKILL.md").read_bytes(),
                (BUNDLE / "SKILL.md").read_bytes(),
            )

    def test_destination_and_skill_root_symlinks_fail_closed(self) -> None:
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            home.mkdir()
            external = root / "external"
            external.mkdir()
            claude_root = home / ".claude" / "skills"
            claude_root.mkdir(parents=True)
            (claude_root / "advisory").symlink_to(external, target_is_directory=True)

            with self.assertRaisesRegex(installer.SkillInstallError, "^skill_conflict$"):
                installer.install_skill(
                    BUNDLE,
                    home=home,
                    target="both",
                    dry_run=False,
                    advisory_command_available=True,
                )
            self.assertFalse((home / ".agents").exists())

            (claude_root / "advisory").unlink()
            (home / ".agents").symlink_to(external, target_is_directory=True)
            with self.assertRaisesRegex(installer.SkillInstallError, "^unsafe_skill_root$"):
                installer.install_skill(
                    BUNDLE,
                    home=home,
                    target="codex",
                    dry_run=False,
                    advisory_command_available=True,
                )
            self.assertEqual(list(external.iterdir()), [])

    def test_exact_bundle_behind_a_managed_parent_symlink_is_never_accepted(self) -> None:
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            home.mkdir(mode=0o700)
            external = root / "external" / "skills" / "advisory"
            (external / "agents").mkdir(parents=True, mode=0o700)
            (external / "references").mkdir(mode=0o700)
            for relative in installer.EXPECTED_FILES:
                destination = external / relative
                destination.write_bytes((BUNDLE / relative).read_bytes())
                destination.chmod(0o600)
            external.chmod(0o700)
            agents = home / ".agents"
            agents.symlink_to(external.parent.parent, target_is_directory=True)

            for dry_run in (False, True):
                with (
                    self.subTest(dry_run=dry_run),
                    self.assertRaisesRegex(installer.SkillInstallError, "unsafe_skill_root"),
                ):
                    installer.install_skill(
                        BUNDLE,
                        home=home,
                        target="codex",
                        dry_run=dry_run,
                        advisory_command_available=True,
                    )

            self.assertEqual(
                (external / "SKILL.md").read_bytes(),
                (BUNDLE / "SKILL.md").read_bytes(),
            )

    def test_dry_run_and_missing_command_never_create_skill_directories(self) -> None:
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            receipt = installer.install_skill(
                BUNDLE,
                home=home,
                target="both",
                dry_run=True,
                advisory_command_available=True,
            )
            self.assertFalse((home / ".agents").exists())
            self.assertFalse((home / ".claude").exists())
            self.assertTrue(receipt["dry_run"])

            with self.assertRaisesRegex(
                installer.SkillInstallError, "^advisory_command_unavailable$"
            ):
                installer.install_skill(
                    BUNDLE,
                    home=home,
                    target="codex",
                    dry_run=False,
                    advisory_command_available=False,
                )
            self.assertFalse((home / ".agents").exists())

    def test_symlinked_or_extra_bundle_content_is_rejected(self) -> None:
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "advisory"
            (bundle / "agents").mkdir(parents=True)
            (bundle / "references").mkdir()
            (bundle / "SKILL.md").symlink_to(BUNDLE / "SKILL.md")
            (bundle / "agents" / "openai.yaml").write_bytes(
                (BUNDLE / "agents" / "openai.yaml").read_bytes()
            )
            (bundle / "references" / "modes.md").write_bytes(
                (BUNDLE / "references" / "modes.md").read_bytes()
            )

            with self.assertRaisesRegex(installer.SkillInstallError, "^invalid_bundle$"):
                installer.install_skill(
                    bundle,
                    home=root / "home",
                    target="codex",
                    dry_run=False,
                    advisory_command_available=True,
                )

            (bundle / "SKILL.md").unlink()
            (bundle / "SKILL.md").write_bytes((BUNDLE / "SKILL.md").read_bytes())
            (bundle / "extra.txt").write_text("unexpected\n", encoding="utf-8")
            with self.assertRaisesRegex(installer.SkillInstallError, "^invalid_bundle$"):
                installer.install_skill(
                    bundle,
                    home=root / "home",
                    target="codex",
                    dry_run=False,
                    advisory_command_available=True,
                )

    def test_fresh_publish_remains_bound_to_the_open_skills_parent(self) -> None:
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            home.mkdir(mode=0o755)
            moved_parent = root / "opened-skills"
            real_stage = installer._stage_bundle

            def stage_then_swap(parent_fd: int, payloads: dict[str, bytes]) -> str:
                staging_name = cast(str, real_stage(parent_fd, payloads))
                skills = home / ".agents" / "skills"
                skills.rename(moved_parent)
                skills.mkdir(mode=0o700)
                return staging_name

            with mock.patch.object(installer, "_stage_bundle", side_effect=stage_then_swap):
                receipt = installer.install_skill(
                    BUNDLE,
                    home=home,
                    target="codex",
                    dry_run=False,
                    advisory_command_available=True,
                )

            self.assertEqual(receipt["installed"], ["codex"])
            self.assertFalse((home / ".agents" / "skills" / "advisory").exists())
            self.assertEqual(
                (moved_parent / "advisory" / "SKILL.md").read_bytes(),
                (BUNDLE / "SKILL.md").read_bytes(),
            )

    def test_upgrade_revalidates_through_parent_fd_and_rolls_back_failed_publish(self) -> None:
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            installed = home / ".agents" / "skills" / "advisory"
            (installed / "agents").mkdir(parents=True, mode=0o700)
            (installed / "references").mkdir(mode=0o700)
            previous_hashes: dict[str, str] = {}
            previous_payloads: dict[str, bytes] = {}
            for relative in installer.EXPECTED_FILES:
                payload = (BUNDLE / relative).read_bytes() + b"\nprevious\n"
                target = installed / relative
                target.write_bytes(payload)
                target.chmod(0o600)
                previous_hashes[relative] = hashlib.sha256(payload).hexdigest()
                previous_payloads[relative] = payload
            installed.chmod(0o700)

            real_rename = installer.os.rename

            def reject_staging_publish(
                source: str,
                destination: str,
                *,
                src_dir_fd: int | None = None,
                dst_dir_fd: int | None = None,
            ) -> None:
                if (
                    source.startswith(".advisory-skill-")
                    and not source.startswith(".advisory-skill-backup-")
                    and destination == "advisory"
                ):
                    raise OSError("injected publish failure")
                real_rename(
                    source,
                    destination,
                    src_dir_fd=src_dir_fd,
                    dst_dir_fd=dst_dir_fd,
                )

            with (
                mock.patch.object(
                    installer,
                    "PREVIOUS_BUNDLE_FILE_SHA256",
                    previous_hashes,
                ),
                mock.patch.object(installer.os, "rename", side_effect=reject_staging_publish),
                self.assertRaises(OSError),
            ):
                installer.install_skill(
                    BUNDLE,
                    home=home,
                    target="codex",
                    dry_run=False,
                    advisory_command_available=True,
                    upgrade=True,
                )

            for relative, payload in previous_payloads.items():
                self.assertEqual((installed / relative).read_bytes(), payload)
            self.assertFalse(
                any(path.name.startswith(".advisory-skill-") for path in installed.parent.iterdir())
            )

    def test_upgrade_verification_and_publish_ignore_a_skills_parent_swap(self) -> None:
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            installed = home / ".agents" / "skills" / "advisory"
            (installed / "agents").mkdir(parents=True, mode=0o700)
            (installed / "references").mkdir(mode=0o700)
            previous_hashes: dict[str, str] = {}
            for relative in installer.EXPECTED_FILES:
                payload = (BUNDLE / relative).read_bytes() + b"\nprevious\n"
                target = installed / relative
                target.write_bytes(payload)
                target.chmod(0o600)
                previous_hashes[relative] = hashlib.sha256(payload).hexdigest()
            installed.chmod(0o700)
            moved_parent = root / "opened-skills"
            real_stage = installer._stage_bundle

            def stage_then_swap(parent_fd: int, payloads: dict[str, bytes]) -> str:
                staging_name = cast(str, real_stage(parent_fd, payloads))
                skills = installed.parent
                skills.rename(moved_parent)
                skills.mkdir(mode=0o700)
                replacement = skills / "advisory"
                replacement.mkdir(mode=0o700)
                (replacement / "marker").write_text("replacement\n", encoding="utf-8")
                return staging_name

            with (
                mock.patch.object(
                    installer,
                    "PREVIOUS_BUNDLE_FILE_SHA256",
                    previous_hashes,
                ),
                mock.patch.object(installer, "_stage_bundle", side_effect=stage_then_swap),
            ):
                receipt = installer.install_skill(
                    BUNDLE,
                    home=home,
                    target="codex",
                    dry_run=False,
                    advisory_command_available=True,
                    upgrade=True,
                )

            self.assertEqual(receipt["upgraded"], ["codex"])
            self.assertEqual(
                (moved_parent / "advisory" / "SKILL.md").read_bytes(),
                (BUNDLE / "SKILL.md").read_bytes(),
            )
            self.assertEqual(
                (home / ".agents" / "skills" / "advisory" / "marker").read_text(encoding="utf-8"),
                "replacement\n",
            )

    def test_cli_failure_is_redacted_and_success_receipt_has_no_paths(self) -> None:
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            output = io.StringIO()
            with (
                mock.patch.object(installer.Path, "home", return_value=home),
                mock.patch("sys.stdout", output),
            ):
                self.assertEqual(installer.main(["--target", "both", "--dry-run"]), 0)
            self.assertEqual(json.loads(output.getvalue())["planned"], ["codex", "claude"])

            output = io.StringIO()
            with (
                mock.patch.object(installer.Path, "home", return_value=home),
                mock.patch("sys.stdout", output),
            ):
                self.assertEqual(installer.main(["--target", "codex", "--dry-run"]), 0)
            rendered = output.getvalue()
            self.assertNotIn(str(home), rendered)
            self.assertEqual(json.loads(rendered)["planned"], ["codex"])


if __name__ == "__main__":
    unittest.main()
