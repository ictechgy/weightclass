#!/usr/bin/env python3
"""Install the optional advisory Agent Skill for Codex, Claude Code, or both."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import stat
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, TypedDict

if TYPE_CHECKING or __package__:
    from . import bounded_io, safe_namespace
else:
    try:
        from weightclass.advisory import bounded_io, safe_namespace
    except ImportError:
        import bounded_io  # type: ignore[no-redef,import-not-found]
        import safe_namespace  # type: ignore[no-redef,import-not-found]

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
# Exact package-owned four-file bundle published in weightclass 0.17.0.
PREVIOUS_BUNDLE_FILE_SHA256 = {
    "SKILL.md": "39e0af77e6b7056733e452e25b5a3adaec51241cda8b5835841ee0cef82d9865",
    "manifest.json": "a0970fe561d6237243f20a3a93217e62071a3f217a7ec171c0ee1edf37f57d31",
    "agents/openai.yaml": "b946bd779de9ec40e785fecfe7950956c41d51d2d230e4bec4897d1381f443e1",
    "references/modes.md": "cd0a791f464eb110439ace1ef132ddd4a744eb4b42329a7aad05a1a2a4b4171f",
}
# Exact package-owned four-file bundle published in weightclass 0.17.1.
ADDITIONAL_PREVIOUS_BUNDLE_FILE_SHA256 = {
    "SKILL.md": "59ae8d515cc5a56635319d32fe5f2d682f817b9d99a8bc7aa0edd02b9f5655a9",
    "manifest.json": "be3decb721a60c8dea1f03cd9e70923e044bd00ed8bda8fd0255f47a13f47606",
    "agents/openai.yaml": "b946bd779de9ec40e785fecfe7950956c41d51d2d230e4bec4897d1381f443e1",
    "references/modes.md": "cd0a791f464eb110439ace1ef132ddd4a744eb4b42329a7aad05a1a2a4b4171f",
}
# Exact package-owned four-file bundle published in weightclass 0.17.2.
LATEST_PREVIOUS_BUNDLE_FILE_SHA256 = {
    "SKILL.md": "d4fd69e9e41c0a9fd05873fef7646af62cd6fbc814b7500481fdfb3e2a48a722",
    "manifest.json": "fc3d5e6de7427f21fd58b167894ec80ec7b611e57328d450311bb970e8fb2324",
    "agents/openai.yaml": "b946bd779de9ec40e785fecfe7950956c41d51d2d230e4bec4897d1381f443e1",
    "references/modes.md": "cd0a791f464eb110439ace1ef132ddd4a744eb4b42329a7aad05a1a2a4b4171f",
}
# Exact package-owned four-file bundle published in weightclass 0.17.3.
CURRENT_PREVIOUS_BUNDLE_FILE_SHA256 = {
    "SKILL.md": "5a3bd1fc4f0e268597c32fbc518828b5c5e1045263705cecce7e6376720c7d16",
    "manifest.json": "4ef820d61bb764cc4517e100e022a5497b66dad6622253dfb64703172cfe6bde",
    "agents/openai.yaml": "b946bd779de9ec40e785fecfe7950956c41d51d2d230e4bec4897d1381f443e1",
    "references/modes.md": "cd0a791f464eb110439ace1ef132ddd4a744eb4b42329a7aad05a1a2a4b4171f",
}
# Exact package-owned four-file bundle published in weightclass 0.17.4.
NEXT_PREVIOUS_BUNDLE_FILE_SHA256 = {
    "SKILL.md": "2d8aa8753e62cb041a4482e1c03ed360f2d240f1b96587207e40f8a72f5cb4a7",
    "manifest.json": "179218e4ea34e5a876a85a896fd1d572f9bfb20d6e74d8742490b0fe63d1d956",
    "agents/openai.yaml": "b946bd779de9ec40e785fecfe7950956c41d51d2d230e4bec4897d1381f443e1",
    "references/modes.md": "cd0a791f464eb110439ace1ef132ddd4a744eb4b42329a7aad05a1a2a4b4171f",
}
# Exact package-owned four-file bundle published in weightclass 0.17.5.
FINAL_PREVIOUS_BUNDLE_FILE_SHA256 = {
    "SKILL.md": "4bafedff92b083f8eb8cf81e85460c62a241954e16429426f7c91d486620cb86",
    "manifest.json": "3879bb357be0670cd90563b3f4fdb2cb92725081bcdf86835d7cb41b05787697",
    "agents/openai.yaml": "b946bd779de9ec40e785fecfe7950956c41d51d2d230e4bec4897d1381f443e1",
    "references/modes.md": "edeab0bd355a4a10a4f8f98ce3a47b4ac18a2433443ed514048cd6916a759c6f",
}
# Exact package-owned four-file bundle published in weightclass 0.17.6.
RELEASE_0176_BUNDLE_FILE_SHA256 = {
    "SKILL.md": "4bafedff92b083f8eb8cf81e85460c62a241954e16429426f7c91d486620cb86",
    "manifest.json": "17a750964825d151c232b76e82897bfc4078ff5a31dd38243fcf43d231526fc1",
    "agents/openai.yaml": "b946bd779de9ec40e785fecfe7950956c41d51d2d230e4bec4897d1381f443e1",
    "references/modes.md": "edeab0bd355a4a10a4f8f98ce3a47b4ac18a2433443ed514048cd6916a759c6f",
}
# Exact package-owned four-file bundle published in weightclass 0.17.7.
RELEASE_0177_BUNDLE_FILE_SHA256 = {
    "SKILL.md": "94b880373bc7e044a13273cdb0007ccd90d036790ac355d6367139b37e841c1b",
    "manifest.json": "e5662c0b70df9f85345e68b5cbfdcb9d2ee69a74f72e6c7145c8c36dc3b64008",
    "agents/openai.yaml": "b946bd779de9ec40e785fecfe7950956c41d51d2d230e4bec4897d1381f443e1",
    "references/modes.md": "edeab0bd355a4a10a4f8f98ce3a47b4ac18a2433443ed514048cd6916a759c6f",
}
# Exact package-owned four-file bundle published in weightclass 0.17.8.
RELEASE_0178_BUNDLE_FILE_SHA256 = {
    "SKILL.md": "09af6211a27666c6bad3312369a7b8a1e14cd7bcb9113faad75be206b19af58c",
    "manifest.json": "36d50d6a12d07027dd43f3ddab52dcdc8e3965db453004ab68b639d4ef91913c",
    "agents/openai.yaml": "b946bd779de9ec40e785fecfe7950956c41d51d2d230e4bec4897d1381f443e1",
    "references/modes.md": "edeab0bd355a4a10a4f8f98ce3a47b4ac18a2433443ed514048cd6916a759c6f",
}
# Exact package-owned four-file bundle published in weightclass 0.17.9.
RELEASE_0179_BUNDLE_FILE_SHA256 = {
    "SKILL.md": "db0f2184142c560e6cfd9b935b0b5280acfbc00268137a2e9254931fcd42963b",
    "manifest.json": "2b51581a7e283f72bff2a23ed0c66a92b38792090f268c3abc9da50de3119099",
    "agents/openai.yaml": "b946bd779de9ec40e785fecfe7950956c41d51d2d230e4bec4897d1381f443e1",
    "references/modes.md": "edeab0bd355a4a10a4f8f98ce3a47b4ac18a2433443ed514048cd6916a759c6f",
}
# Exact package-owned four-file bundle published in weightclass 0.18.0.
RELEASE_0180_BUNDLE_FILE_SHA256 = {
    "SKILL.md": "c0623cd3526fc8d8de08380081d587b5e40607ccd7beccfca45c25f87fe34447",
    "manifest.json": "289d57a04d684e8ffb5503db3bbd18295018cb7daaf88a76d801106434a62ce2",
    "agents/openai.yaml": "b946bd779de9ec40e785fecfe7950956c41d51d2d230e4bec4897d1381f443e1",
    "references/modes.md": "edeab0bd355a4a10a4f8f98ce3a47b4ac18a2433443ed514048cd6916a759c6f",
}
# Exact package-owned four-file bundle published in weightclass 0.19.0.
RELEASE_0190_BUNDLE_FILE_SHA256 = {
    "SKILL.md": "246893888c13674d61e1d6e4bc746bf8542b7154f3ba59294ca667290b416ffe",
    "manifest.json": "82b79efc67f7ecac575c7fb4a58305feca11e9dbd8322f686fa4c5a929d4cc9c",
    "agents/openai.yaml": "b946bd779de9ec40e785fecfe7950956c41d51d2d230e4bec4897d1381f443e1",
    "references/modes.md": "edeab0bd355a4a10a4f8f98ce3a47b4ac18a2433443ed514048cd6916a759c6f",
}
# Exact package-owned four-file bundle published through weightclass 0.26.0.
RELEASE_0260_BUNDLE_FILE_SHA256 = {
    "SKILL.md": "b2afc03c6310f72dd286a25a1c3726908960cfd15b42341ce3596a7a0d69858f",
    "manifest.json": "01122cf85e2d4aec74de69803f3bd01e7c67757390521473432ad50a6e1a668c",
    "agents/openai.yaml": "b946bd779de9ec40e785fecfe7950956c41d51d2d230e4bec4897d1381f443e1",
    "references/modes.md": "edeab0bd355a4a10a4f8f98ce3a47b4ac18a2433443ed514048cd6916a759c6f",
}
# Exact package-owned four-file bundle published through weightclass 0.27.1.
RELEASE_0271_BUNDLE_FILE_SHA256 = {
    "SKILL.md": "d13ea9e08f45a94de86b1e605f3ffd92c7efe1609a54c56a5143df5d1dcfce77",
    "manifest.json": "ddce5171e479240e01eeef036fc9ee4a6c73db31f47dbe3147f7d51550c566c5",
    "agents/openai.yaml": "5aee8388c2735994411240ea01273df1f0dfa8fcf71bf9876c854b1722564e44",
    "references/modes.md": "b1a22cda0a5588f5d154831de1e768808f1c2c898d4d7f7c767c8a147672465a",
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
    try:
        return bounded_io.read_regular_bytes(
            path,
            MAX_BUNDLE_FILE_BYTES,
            require_nonempty=True,
        )
    except bounded_io.BoundedFileError as error:
        raise SkillInstallError("invalid_bundle") from error


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


def _directory_flags() -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if not isinstance(nofollow, int) or not isinstance(directory, int):
        _fail("unsafe_skill_root")
    return os.O_RDONLY | nofollow | directory | getattr(os, "O_CLOEXEC", 0)


def _private_directory_metadata(descriptor: int, *, private: bool) -> bool:
    metadata = os.fstat(descriptor)
    return (
        stat.S_ISDIR(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and not stat.S_IMODE(metadata.st_mode) & (0o077 if private else 0o022)
    )


def _open_directory(
    name: str | Path,
    *,
    dir_fd: int | None = None,
    private: bool = True,
) -> int:
    descriptor = os.open(name, _directory_flags(), dir_fd=dir_fd)
    if not _private_directory_metadata(descriptor, private=private):
        os.close(descriptor)
        raise OSError
    return descriptor


def _write_private_at(directory_fd: int, name: str, payload: bytes) -> None:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, 0o600, dir_fd=directory_fd)
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


def _open_relative_parent(root_fd: int, relative: str) -> tuple[int, str]:
    parts = Path(relative).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise OSError
    descriptor = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            child = _open_directory(part, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor, parts[-1]
    except BaseException:
        os.close(descriptor)
        raise


def _regular_bytes_at(root_fd: int, relative: str) -> bytes:
    parent_fd, name = _open_relative_parent(root_fd, relative)
    descriptor = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(name, flags, dir_fd=parent_fd)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise OSError
        chunks: list[bytes] = []
        remaining = MAX_BUNDLE_FILE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if not payload or len(payload) > MAX_BUNDLE_FILE_BYTES:
            raise OSError
        return payload
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)


def _exact_bundle_fd(
    root_fd: int,
    expected_files: tuple[str, ...],
    expected_sha256: dict[str, str],
) -> bool:
    try:
        expected_entries = {Path(relative).parts[0] for relative in expected_files}
        if set(os.listdir(root_fd)) != expected_entries:
            return False
        for directory in EXPECTED_DIRECTORIES:
            child_fd = _open_directory(directory, dir_fd=root_fd)
            try:
                expected_children = {
                    Path(relative).name
                    for relative in expected_files
                    if Path(relative).parts[0] == directory
                }
                if set(os.listdir(child_fd)) != expected_children:
                    return False
            finally:
                os.close(child_fd)
        return all(
            hashlib.sha256(_regular_bytes_at(root_fd, relative)).hexdigest()
            == expected_sha256[relative]
            for relative in expected_files
        )
    except OSError:
        return False


def _exact_bundle_at(
    parent_fd: int,
    destination_name: str,
    expected_files: tuple[str, ...],
    expected_sha256: dict[str, str],
) -> bool:
    root_fd = -1
    try:
        root_fd = _open_directory(destination_name, dir_fd=parent_fd)
        return _exact_bundle_fd(root_fd, expected_files, expected_sha256)
    except OSError:
        return False
    finally:
        if root_fd >= 0:
            os.close(root_fd)


def _recognized_previous_files_at(parent_fd: int, destination_name: str) -> tuple[str, ...] | None:
    root_fd = -1
    try:
        root_fd = _open_directory(destination_name, dir_fd=parent_fd)
        return _recognized_previous_files_fd(root_fd)
    except OSError:
        return None
    finally:
        if root_fd >= 0:
            os.close(root_fd)


def _recognized_previous_files_fd(root_fd: int) -> tuple[str, ...] | None:
    for files, expected_sha256 in (
        (LEGACY_FILES, LEGACY_FILE_SHA256),
        (EXPECTED_FILES, PREVIOUS_BUNDLE_FILE_SHA256),
        (EXPECTED_FILES, ADDITIONAL_PREVIOUS_BUNDLE_FILE_SHA256),
        (EXPECTED_FILES, LATEST_PREVIOUS_BUNDLE_FILE_SHA256),
        (EXPECTED_FILES, CURRENT_PREVIOUS_BUNDLE_FILE_SHA256),
        (EXPECTED_FILES, NEXT_PREVIOUS_BUNDLE_FILE_SHA256),
        (EXPECTED_FILES, FINAL_PREVIOUS_BUNDLE_FILE_SHA256),
        (EXPECTED_FILES, RELEASE_0176_BUNDLE_FILE_SHA256),
        (EXPECTED_FILES, RELEASE_0177_BUNDLE_FILE_SHA256),
        (EXPECTED_FILES, RELEASE_0178_BUNDLE_FILE_SHA256),
        (EXPECTED_FILES, RELEASE_0179_BUNDLE_FILE_SHA256),
        (EXPECTED_FILES, RELEASE_0180_BUNDLE_FILE_SHA256),
        (EXPECTED_FILES, RELEASE_0190_BUNDLE_FILE_SHA256),
        (EXPECTED_FILES, RELEASE_0260_BUNDLE_FILE_SHA256),
        (EXPECTED_FILES, RELEASE_0271_BUNDLE_FILE_SHA256),
    ):
        if _exact_bundle_fd(root_fd, files, expected_sha256):
            return files
    return None


def _remove_bundle_contents_fd(root_fd: int, files: tuple[str, ...]) -> None:
    for relative in files:
        try:
            relative_parent_fd, leaf = _open_relative_parent(root_fd, relative)
        except FileNotFoundError:
            continue
        try:
            os.unlink(leaf, dir_fd=relative_parent_fd)
        except FileNotFoundError:
            pass
        finally:
            os.close(relative_parent_fd)
    for relative in EXPECTED_DIRECTORIES:
        try:
            os.rmdir(relative, dir_fd=root_fd)
        except FileNotFoundError:
            pass


def _remove_bundle_at(parent_fd: int, name: str, files: tuple[str, ...]) -> None:
    root_fd = _open_directory(name, dir_fd=parent_fd)
    try:
        _remove_bundle_contents_fd(root_fd, files)
    finally:
        os.close(root_fd)
    try:
        os.rmdir(name, dir_fd=parent_fd)
    except FileNotFoundError:
        pass


def _remove_verified_bundle_at(
    parent_fd: int,
    name: str,
    files: tuple[str, ...],
    current_hashes: dict[str, str],
) -> None:
    """Rename, revalidate, and delete through the verified directory descriptor."""

    tombstone = f".advisory-skill-remove-{secrets.token_hex(8)}"
    os.rename(name, tombstone, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    root_fd = -1
    valid = False
    try:
        root_fd = _open_directory(tombstone, dir_fd=parent_fd)
        valid = _exact_bundle_fd(
            root_fd,
            EXPECTED_FILES,
            current_hashes,
        ) or (_recognized_previous_files_fd(root_fd) == files)
        if not valid:
            raise SkillInstallError("skill_conflict")
        _remove_bundle_contents_fd(root_fd, files)
    except BaseException:
        if root_fd >= 0:
            os.close(root_fd)
            root_fd = -1
        try:
            os.rename(tombstone, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        except OSError:
            pass
        raise
    finally:
        if root_fd >= 0:
            os.close(root_fd)
    try:
        os.rmdir(tombstone, dir_fd=parent_fd)
    except FileNotFoundError:
        pass


def _make_temporary_directory(parent_fd: int, prefix: str) -> str:
    for _ in range(128):
        name = f"{prefix}{secrets.token_hex(8)}"
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
            return name
        except FileExistsError:
            continue
    raise OSError


def _open_skill_parent(home: Path, parent: Path, *, create: bool = True) -> int:
    if not home.is_absolute():
        _fail("unsafe_skill_root")
    try:
        relative_parts = parent.relative_to(home).parts
        if not relative_parts:
            _fail("unsafe_skill_root")
        safe_namespace.ensure_private_directory(
            parent,
            managed_root=home / relative_parts[0],
            create=create,
            private_leaf=False,
        )
        return _open_directory(parent, private=False)
    except (OSError, ValueError, safe_namespace.SafeNamespaceError) as error:
        raise SkillInstallError("unsafe_skill_root") from error


def _inspect_destination(
    home: Path,
    destination: Path,
    payloads: dict[str, bytes],
    *,
    upgrade: bool,
) -> tuple[str, tuple[str, ...] | None]:
    parent = destination.parent
    try:
        relative_parts = parent.relative_to(home).parts
        if not relative_parts:
            _fail("unsafe_skill_root")
        safe_namespace.admit_existing_ancestors(
            parent,
            managed_root=home / relative_parts[0],
            allow_missing=True,
        )
    except (ValueError, safe_namespace.SafeNamespaceError) as error:
        raise SkillInstallError("unsafe_skill_root") from error
    try:
        parent_fd = _open_skill_parent(home, parent, create=False)
    except SkillInstallError:
        if not parent.exists() and not parent.is_symlink():
            return "planned", None
        raise
    try:
        try:
            os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return "planned", None
        current_hashes = {
            relative: hashlib.sha256(payload).hexdigest() for relative, payload in payloads.items()
        }
        if _exact_bundle_at(parent_fd, destination.name, EXPECTED_FILES, current_hashes):
            return "already_installed", None
        if upgrade:
            previous_files = _recognized_previous_files_at(parent_fd, destination.name)
            if previous_files is not None:
                return "upgrade", previous_files
        _fail("skill_conflict")
    finally:
        os.close(parent_fd)


def _stage_bundle(parent_fd: int, payloads: dict[str, bytes]) -> str:
    staging_name = _make_temporary_directory(parent_fd, ".advisory-skill-")
    staging_fd = -1
    try:
        staging_fd = _open_directory(staging_name, dir_fd=parent_fd)
        directory_fds: dict[str, int] = {}
        try:
            for relative in EXPECTED_DIRECTORIES:
                os.mkdir(relative, mode=0o700, dir_fd=staging_fd)
                directory_fds[relative] = _open_directory(relative, dir_fd=staging_fd)
            for relative, payload in payloads.items():
                parts = Path(relative).parts
                target_fd = staging_fd if len(parts) == 1 else directory_fds[parts[0]]
                _write_private_at(target_fd, parts[-1], payload)
        finally:
            for descriptor in directory_fds.values():
                os.close(descriptor)
        return staging_name
    except BaseException:
        try:
            _remove_bundle_at(parent_fd, staging_name, EXPECTED_FILES)
        except OSError:
            pass
        raise
    finally:
        if staging_fd >= 0:
            os.close(staging_fd)


def _publish(home: Path, destination: Path, payloads: dict[str, bytes]) -> None:
    parent = destination.parent
    parent_fd = _open_skill_parent(home, parent)
    staging_name = ""
    destination_created = False
    try:
        staging_name = _stage_bundle(parent_fd, payloads)
        try:
            os.mkdir(destination.name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError as error:
            raise SkillInstallError("skill_conflict") from error
        destination_created = True
        staging_fd = _open_directory(staging_name, dir_fd=parent_fd)
        destination_fd = _open_directory(destination.name, dir_fd=parent_fd)
        try:
            for relative in EXPECTED_DIRECTORIES:
                os.mkdir(relative, mode=0o700, dir_fd=destination_fd)
            for relative in EXPECTED_FILES:
                os.rename(
                    relative,
                    relative,
                    src_dir_fd=staging_fd,
                    dst_dir_fd=destination_fd,
                )
            os.fsync(parent_fd)
        finally:
            os.close(destination_fd)
            os.close(staging_fd)
        destination_created = False
    finally:
        if destination_created:
            try:
                _remove_bundle_at(parent_fd, destination.name, EXPECTED_FILES)
            except OSError:
                pass
        if staging_name:
            try:
                _remove_bundle_at(parent_fd, staging_name, EXPECTED_FILES)
            except OSError:
                pass
        os.close(parent_fd)


def _upgrade(
    home: Path,
    destination: Path,
    payloads: dict[str, bytes],
    previous_files: tuple[str, ...],
) -> None:
    parent = destination.parent
    parent_fd = _open_skill_parent(home, parent)
    staging_name = ""
    backup_name = f".advisory-skill-backup-{secrets.token_hex(8)}"
    moved_existing = False
    published = False
    try:
        staging_name = _stage_bundle(parent_fd, payloads)
        if _recognized_previous_files_at(parent_fd, destination.name) != previous_files:
            _fail("skill_conflict")
        os.rename(destination.name, backup_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        moved_existing = True
        os.rename(staging_name, destination.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        published = True
        staging_name = ""
        os.fsync(parent_fd)
        try:
            _remove_bundle_at(parent_fd, backup_name, previous_files)
        except OSError:
            pass
        moved_existing = False
    except OSError:
        if published:
            try:
                staging_name = _make_temporary_directory(parent_fd, ".advisory-skill-failed-")
                os.rmdir(staging_name, dir_fd=parent_fd)
                os.rename(
                    destination.name,
                    staging_name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
                published = False
            except OSError:
                pass
        if moved_existing:
            try:
                os.rename(
                    backup_name,
                    destination.name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
                moved_existing = False
            except OSError:
                pass
        raise
    finally:
        if staging_name:
            try:
                _remove_bundle_at(parent_fd, staging_name, EXPECTED_FILES)
            except OSError:
                pass
        os.close(parent_fd)


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
    upgrade_targets: dict[str, tuple[str, ...]] = {}
    for selected in targets:
        destination = _destination(home, selected)
        destinations[selected] = destination
        state, previous_files = _inspect_destination(
            home,
            destination,
            payloads,
            upgrade=upgrade,
        )
        if state == "already_installed":
            already_installed.append(selected)
        elif state == "upgrade":
            assert previous_files is not None
            upgrade_targets[selected] = previous_files
        else:
            assert state == "planned" and previous_files is None
            planned.append(selected)

    if not dry_run:
        for selected in planned:
            _publish(home, destinations[selected], payloads)
            installed.append(selected)
        for selected, previous_files in upgrade_targets.items():
            _upgrade(home, destinations[selected], payloads, previous_files)
            upgraded.append(selected)

    return {
        "schema_version": SCHEMA_VERSION,
        "skill": SKILL_NAME,
        "target": target,
        "installed": installed,
        "upgraded": upgraded,
        "upgrade_planned": list(upgrade_targets),
        "already_installed": already_installed,
        "planned": planned,
        "dry_run": dry_run,
    }


def uninstall_skill(
    bundle: Path,
    *,
    home: Path,
    target: str,
    dry_run: bool,
) -> dict[str, object]:
    """Remove only an exact package-owned advisory Skill bundle."""

    payloads = _bundle_payloads(bundle)
    current_hashes = {
        relative: hashlib.sha256(payload).hexdigest() for relative, payload in payloads.items()
    }
    removed: list[str] = []
    planned: list[str] = []
    missing: list[str] = []
    for selected in _selected_targets(target):
        destination = _destination(home, selected)
        try:
            parent_fd = _open_skill_parent(home, destination.parent, create=False)
        except SkillInstallError:
            if not destination.parent.exists() and not destination.parent.is_symlink():
                missing.append(selected)
                continue
            raise
        try:
            try:
                os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                missing.append(selected)
                continue
            files: tuple[str, ...] | None = None
            if _exact_bundle_at(parent_fd, destination.name, EXPECTED_FILES, current_hashes):
                files = EXPECTED_FILES
            else:
                files = _recognized_previous_files_at(parent_fd, destination.name)
            if files is None:
                _fail("skill_conflict")
            planned.append(selected)
            if not dry_run:
                _remove_verified_bundle_at(
                    parent_fd,
                    destination.name,
                    files,
                    current_hashes,
                )
                removed.append(selected)
                os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    return {
        "schema_version": SCHEMA_VERSION,
        "skill": SKILL_NAME,
        "target": target,
        "removed": removed,
        "removal_planned": planned,
        "missing": missing,
        "dry_run": dry_run,
    }


def uninstall_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wclass-advisory skill uninstall",
        description="Remove only an exact package-owned advisory Skill bundle.",
        allow_abbrev=False,
    )
    parser.add_argument("--target", choices=("codex", "claude", "both"), default="both")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="confirm removal of exact package-owned bundle files",
    )
    arguments = parser.parse_args(argv)
    if not arguments.dry_run and not arguments.confirm:
        print(json.dumps({"error": "uninstall_confirmation_required"}), file=sys.stderr)
        return 2
    bundle = Path(__file__).resolve().parent / "skill"
    try:
        receipt = uninstall_skill(
            bundle,
            home=Path.home(),
            target=arguments.target,
            dry_run=arguments.dry_run,
        )
    except SkillInstallError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2
    except (OSError, UnicodeError):
        print(json.dumps({"error": "uninstall_failed"}), file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


def _install_main(argv: list[str] | None, *, prog: str) -> int:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=__doc__,
        allow_abbrev=False,
    )
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


def main(argv: list[str] | None = None) -> int:
    return _install_main(argv, prog="wclass-advisory install-skill")


def skill_install_main(argv: list[str] | None = None) -> int:
    return _install_main(argv, prog="wclass-advisory skill install")


def skill_status_main(argv: list[str] | None = None) -> int:
    arguments = list(argv or ())
    return _install_main(
        [*arguments, "--upgrade", "--dry-run"],
        prog="wclass-advisory skill status",
    )


if __name__ == "__main__":
    raise SystemExit(main())
