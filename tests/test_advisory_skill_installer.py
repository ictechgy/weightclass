from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
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
        self.assertEqual(manifest, {"managed_onboarding": 13, "schema_version": 1})
        self.assertIn("managed_runner_version_changed", skill)
        self.assertIn("managed_setup_busy", skill)

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
