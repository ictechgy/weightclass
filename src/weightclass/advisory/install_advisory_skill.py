#!/usr/bin/env python3
"""Install the optional advisory Agent Skill for Codex, Claude Code, or both."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import NoReturn, TypedDict

SKILL_NAME = "advisory"
SCHEMA_VERSION = 1
MAX_BUNDLE_FILE_BYTES = 65_536
EXPECTED_DIRECTORIES = frozenset({"agents", "references"})
EXPECTED_FILES = (
    "SKILL.md",
    "manifest.json",
    "agents/openai.yaml",
    "references/modes.md",
)
LEGACY_FILES = (
    "SKILL.md",
    "agents/openai.yaml",
    "references/modes.md",
)
LEGACY_FILE_SHA256 = {
    "SKILL.md": "f7dc2885a852baf577003b8d8413139bd5f9668cc65351c5867c9a3f5ed8d136",
    "agents/openai.yaml": "b946bd779de9ec40e785fecfe7950956c41d51d2d230e4bec4897d1381f443e1",
    "references/modes.md": "cd0a791f464eb110439ace1ef132ddd4a744eb4b42329a7aad05a1a2a4b4171f",
}
TARGET_ROOTS = {
    "codex": (".agents", "skills"),
    "claude": (".claude", "skills"),
}


class InstallReceipt(TypedDict):
    schema_version: int
    skill: str
    target: str
    installed: list[str]
    upgraded: list[str]
    upgrade_planned: list[str]
    already_installed: list[str]
    planned: list[str]
    dry_run: bool


class SkillInstallError(ValueError):
    """Value-free installation rejection with one public reason code."""


def _fail(code: str) -> NoReturn:
    raise SkillInstallError(code)


def _selected_targets(target: str) -> tuple[str, ...]:
    if target == "both":
        return ("codex", "claude")
    if target not in TARGET_ROOTS:
        _fail("invalid_target")
    return (target,)


def _regular_bytes(path: Path) -> bytes:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(nofollow, int):
        _fail("invalid_bundle")
    flags = os.O_RDONLY | nofollow
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            _fail("invalid_bundle")
        chunks: list[bytes] = []
        remaining = MAX_BUNDLE_FILE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError as error:
        raise SkillInstallError("invalid_bundle") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    payload = b"".join(chunks)
    if not payload or len(payload) > MAX_BUNDLE_FILE_BYTES:
        _fail("invalid_bundle")
    return payload


def _bundle_payloads(bundle: Path) -> dict[str, bytes]:
    try:
        metadata = bundle.lstat()
    except OSError as error:
        raise SkillInstallError("invalid_bundle") from error
    if not stat.S_ISDIR(metadata.st_mode):
        _fail("invalid_bundle")

    found_directories: set[str] = set()
    found_files: set[str] = set()
    try:
        for root, directories, files in os.walk(bundle, followlinks=False):
            root_path = Path(root)
            for name in directories:
                path = root_path / name
                if path.is_symlink() or not stat.S_ISDIR(path.lstat().st_mode):
                    _fail("invalid_bundle")
                found_directories.add(path.relative_to(bundle).as_posix())
            for name in files:
                path = root_path / name
                if path.is_symlink():
                    _fail("invalid_bundle")
                found_files.add(path.relative_to(bundle).as_posix())
    except OSError as error:
        raise SkillInstallError("invalid_bundle") from error

    if found_directories != EXPECTED_DIRECTORIES or found_files != set(EXPECTED_FILES):
        _fail("invalid_bundle")
    return {relative: _regular_bytes(bundle / relative) for relative in EXPECTED_FILES}


def _destination(home: Path, target: str) -> Path:
    parts = TARGET_ROOTS[target]
    return home.joinpath(*parts, SKILL_NAME)


def _installed_matches(destination: Path, payloads: dict[str, bytes]) -> bool:
    try:
        destination_metadata = destination.lstat()
        if (
            destination.is_symlink()
            or not stat.S_ISDIR(destination_metadata.st_mode)
            or destination_metadata.st_mode & 0o077
        ):
            return False
        found_directories: set[str] = set()
        found_files: set[str] = set()
        for root, directories, files in os.walk(destination, followlinks=False):
            root_path = Path(root)
            for name in directories:
                path = root_path / name
                metadata = path.lstat()
                if (
                    path.is_symlink()
                    or not stat.S_ISDIR(metadata.st_mode)
                    or metadata.st_mode & 0o077
                ):
                    return False
                found_directories.add(path.relative_to(destination).as_posix())
            for name in files:
                path = root_path / name
                metadata = path.lstat()
                if (
                    path.is_symlink()
                    or not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_mode & 0o077
                ):
                    return False
                found_files.add(path.relative_to(destination).as_posix())
        if found_directories != EXPECTED_DIRECTORIES or found_files != set(EXPECTED_FILES):
            return False
        return all(
            _regular_bytes(destination / relative) == payload
            for relative, payload in payloads.items()
        )
    except (OSError, SkillInstallError):
        return False


def _legacy_matches(destination: Path) -> bool:
    try:
        metadata = destination.lstat()
        if (
            destination.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_mode & 0o077
        ):
            return False
        found_directories: set[str] = set()
        found_files: set[str] = set()
        for root, directories, files in os.walk(destination, followlinks=False):
            root_path = Path(root)
            for name in directories:
                path = root_path / name
                child = path.lstat()
                if path.is_symlink() or not stat.S_ISDIR(child.st_mode) or child.st_mode & 0o077:
                    return False
                found_directories.add(path.relative_to(destination).as_posix())
            for name in files:
                path = root_path / name
                child = path.lstat()
                if path.is_symlink() or not stat.S_ISREG(child.st_mode) or child.st_mode & 0o077:
                    return False
                found_files.add(path.relative_to(destination).as_posix())
        if found_directories != EXPECTED_DIRECTORIES or found_files != set(LEGACY_FILES):
            return False
        return all(
            hashlib.sha256(_regular_bytes(destination / relative)).hexdigest()
            == LEGACY_FILE_SHA256[relative]
            for relative in LEGACY_FILES
        )
    except (OSError, SkillInstallError):
        return False


def _write_private(path: Path, payload: bytes) -> None:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError()
            view = view[written:]
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_staging(staging: Path) -> None:
    for relative in EXPECTED_FILES:
        try:
            (staging / relative).unlink()
        except FileNotFoundError:
            pass
    for relative in ("agents", "references"):
        try:
            (staging / relative).rmdir()
        except FileNotFoundError:
            pass
    try:
        staging.rmdir()
    except FileNotFoundError:
        pass


def _remove_bundle(directory: Path, files: tuple[str, ...]) -> None:
    for relative in files:
        (directory / relative).unlink()
    for relative in ("agents", "references"):
        (directory / relative).rmdir()
    directory.rmdir()


def _ensure_skill_root(home: Path, parent: Path) -> None:
    if not home.is_absolute():
        _fail("unsafe_skill_root")
    current = home
    try:
        relative_parts = parent.relative_to(home).parts
    except ValueError:
        _fail("unsafe_skill_root")
    for part in relative_parts:
        current /= part
        try:
            current.mkdir(mode=0o700)
            current.chmod(0o700)
        except FileExistsError:
            try:
                metadata = current.lstat()
            except OSError as error:
                raise SkillInstallError("unsafe_skill_root") from error
            if current.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
                _fail("unsafe_skill_root")


def _publish(home: Path, destination: Path, payloads: dict[str, bytes]) -> None:
    parent = destination.parent
    _ensure_skill_root(home, parent)
    if parent.is_symlink() or not stat.S_ISDIR(parent.lstat().st_mode):
        _fail("unsafe_skill_root")

    staging = Path(tempfile.mkdtemp(prefix=".advisory-skill-", dir=parent))
    staging.chmod(0o700)
    try:
        for relative in EXPECTED_DIRECTORIES:
            (staging / relative).mkdir(mode=0o700)
        for relative, payload in payloads.items():
            _write_private(staging / relative, payload)

        try:
            destination.mkdir(mode=0o700)
        except FileExistsError as error:
            raise SkillInstallError("skill_conflict") from error
        try:
            for relative in EXPECTED_DIRECTORIES:
                (destination / relative).mkdir(mode=0o700)
            for relative in EXPECTED_FILES:
                os.rename(staging / relative, destination / relative)
            destination.chmod(0o700)
            directory_descriptor = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError:
            for relative in EXPECTED_FILES:
                try:
                    (destination / relative).unlink()
                except FileNotFoundError:
                    pass
            for relative in EXPECTED_DIRECTORIES:
                try:
                    (destination / relative).rmdir()
                except FileNotFoundError:
                    pass
            try:
                destination.rmdir()
            except FileNotFoundError:
                pass
            raise
    finally:
        _remove_staging(staging)


def _upgrade(home: Path, destination: Path, payloads: dict[str, bytes]) -> None:
    parent = destination.parent
    _ensure_skill_root(home, parent)
    if parent.is_symlink() or not stat.S_ISDIR(parent.lstat().st_mode):
        _fail("unsafe_skill_root")
    staging = Path(tempfile.mkdtemp(prefix=".advisory-skill-", dir=parent))
    staging.chmod(0o700)
    backup = Path(tempfile.mkdtemp(prefix=".advisory-skill-backup-", dir=parent))
    backup.rmdir()
    moved_existing = False
    published = False
    try:
        for relative in EXPECTED_DIRECTORIES:
            (staging / relative).mkdir(mode=0o700)
        for relative, payload in payloads.items():
            _write_private(staging / relative, payload)
        os.rename(destination, backup)
        moved_existing = True
        os.rename(staging, destination)
        published = True
        destination.chmod(0o700)
        directory_descriptor = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        try:
            _remove_bundle(backup, LEGACY_FILES)
        except OSError:
            pass
        moved_existing = False
    except OSError:
        if published:
            try:
                os.rename(destination, staging)
                published = False
            except OSError:
                pass
        if moved_existing:
            try:
                os.rename(backup, destination)
                moved_existing = False
            except OSError:
                pass
        raise
    finally:
        _remove_staging(staging)


def install_skill(
    bundle: Path,
    *,
    home: Path,
    target: str,
    dry_run: bool,
    advisory_command_available: bool,
    upgrade: bool = False,
) -> InstallReceipt:
    if not advisory_command_available:
        _fail("advisory_command_unavailable")
    payloads = _bundle_payloads(bundle)
    targets = _selected_targets(target)
    installed: list[str] = []
    upgraded: list[str] = []
    already_installed: list[str] = []
    planned: list[str] = []

    destinations: dict[str, Path] = {}
    upgrade_targets: list[str] = []
    for selected in targets:
        destination = _destination(home, selected)
        destinations[selected] = destination
        if destination.exists() or destination.is_symlink():
            if _installed_matches(destination, payloads):
                already_installed.append(selected)
            elif upgrade and _legacy_matches(destination):
                upgrade_targets.append(selected)
            else:
                _fail("skill_conflict")
        else:
            planned.append(selected)

    if not dry_run:
        for selected in planned:
            _publish(home, destinations[selected], payloads)
            installed.append(selected)
        for selected in upgrade_targets:
            _upgrade(home, destinations[selected], payloads)
            upgraded.append(selected)

    return {
        "schema_version": SCHEMA_VERSION,
        "skill": SKILL_NAME,
        "target": target,
        "installed": installed,
        "upgraded": upgraded,
        "upgrade_planned": upgrade_targets,
        "already_installed": already_installed,
        "planned": planned,
        "dry_run": dry_run,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--target", choices=("codex", "claude", "both"), default="both")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--upgrade", action="store_true")
    arguments = parser.parse_args(argv)
    bundle = Path(__file__).resolve().parent / "skill"
    try:
        receipt = install_skill(
            bundle,
            home=Path.home(),
            target=arguments.target,
            dry_run=arguments.dry_run,
            advisory_command_available=True,
            upgrade=arguments.upgrade,
        )
    except SkillInstallError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2
    except (OSError, UnicodeError):
        print(json.dumps({"error": "install_failed"}), file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
