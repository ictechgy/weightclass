from __future__ import annotations

import hashlib
import importlib.util
import io
import json
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
        self.assertEqual(manifest, {"managed_onboarding": 14, "schema_version": 1})
        self.assertIn("managed_runner_version_changed", skill)
        self.assertIn("managed_setup_busy", skill)

    def test_install_skill_is_the_documented_vendor_path_exception(self) -> None:
        agent_guide_path = ROOT / "AGENTS.md"
        skill_guide_path = ROOT / "docs" / "advisory-skill.md"
        if not agent_guide_path.is_file() or not skill_guide_path.is_file():
            self.skipTest("repository policy documents are intentionally absent from the sdist")
        agent_guide = agent_guide_path.read_text(encoding="utf-8")
        skill_guide = skill_guide_path.read_text(encoding="utf-8")

        self.assertIn("sole vendor-recognized-path exception", agent_guide)
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
