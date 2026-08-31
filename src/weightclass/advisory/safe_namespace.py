"""Shared path admission and private managed-directory creation.

The helpers close cross-user pathname redirection through existing ancestors.
They do not try to defend a user from another process running as the same UID.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path


class SafeNamespaceError(ValueError):
    """A path is outside the admitted owner-controlled namespace."""


def _parts(path: Path) -> tuple[str, ...]:
    if not path.is_absolute() or ".." in path.parts:
        raise SafeNamespaceError
    normalized = Path(os.path.normpath(os.fspath(path)))
    if not normalized.is_absolute():
        raise SafeNamespaceError
    return normalized.parts


def _admitted_directory(metadata: os.stat_result) -> bool:
    mode = metadata.st_mode
    if not stat.S_ISDIR(mode) or metadata.st_uid not in {0, os.getuid()}:
        return False
    writable_by_others = bool(mode & stat.S_IWOTH)
    writable_by_group = bool(mode & stat.S_IWGRP)
    if writable_by_others:
        return stat.S_IMODE(mode) == 0o1777
    return not writable_by_group


def _admit_resolved_chain(path: Path) -> None:
    current = Path(path.anchor)
    try:
        root_metadata = current.lstat()
    except OSError as error:
        raise SafeNamespaceError from error
    if not _admitted_directory(root_metadata):
        raise SafeNamespaceError
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except OSError as error:
            raise SafeNamespaceError from error
        if stat.S_ISLNK(metadata.st_mode) or not _admitted_directory(metadata):
            raise SafeNamespaceError


def admit_existing_ancestors(
    path: Path,
    *,
    managed_root: Path,
    allow_missing: bool,
) -> None:
    """Admit every existing component and reject links in the managed suffix."""

    path_parts = _parts(path)
    managed_parts = _parts(managed_root)
    if path_parts[: len(managed_parts)] != managed_parts:
        raise SafeNamespaceError

    current = Path(path.anchor)
    try:
        root_metadata = current.lstat()
    except OSError as error:
        raise SafeNamespaceError from error
    if not _admitted_directory(root_metadata):
        raise SafeNamespaceError
    missing = False
    for index, part in enumerate(path_parts[1:], start=1):
        current /= part
        if missing:
            continue
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            missing = True
            continue
        except OSError as error:
            raise SafeNamespaceError from error

        in_managed_suffix = index >= len(managed_parts) - 1
        if stat.S_ISLNK(metadata.st_mode):
            if in_managed_suffix or metadata.st_uid not in {0, os.getuid()}:
                raise SafeNamespaceError
            try:
                resolved = current.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise SafeNamespaceError from error
            _admit_resolved_chain(resolved)
            try:
                target_metadata = current.stat()
            except OSError as error:
                raise SafeNamespaceError from error
            if not _admitted_directory(target_metadata):
                raise SafeNamespaceError
        elif not _admitted_directory(metadata):
            raise SafeNamespaceError

    if missing and not allow_missing:
        raise SafeNamespaceError


def ensure_private_directory(
    path: Path,
    *,
    managed_root: Path,
    create: bool,
    private_leaf: bool = True,
) -> None:
    """Create missing components one at a time and require a private leaf.

    Existing components above ``managed_root`` use the ancestor-admission
    rules. Every component at or below it must be a nonsymlink directory owned
    by the current user and not group/other writable. Newly created components
    and, by default, the requested leaf are owner-private.
    """

    path_parts = _parts(path)
    managed_parts = _parts(managed_root)
    if path_parts[: len(managed_parts)] != managed_parts:
        raise SafeNamespaceError

    current = Path(path.anchor)
    try:
        root_metadata = current.lstat()
    except OSError as error:
        raise SafeNamespaceError from error
    if not _admitted_directory(root_metadata):
        raise SafeNamespaceError
    missing = False
    for index, part in enumerate(path_parts[1:], start=1):
        current /= part
        in_managed_suffix = index >= len(managed_parts) - 1
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            if not create:
                raise SafeNamespaceError from None
            try:
                current.mkdir(mode=0o700)
                metadata = current.lstat()
            except OSError as error:
                raise SafeNamespaceError from error
            missing = True
        except OSError as error:
            raise SafeNamespaceError from error

        if in_managed_suffix or missing:
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) & 0o022
                or (
                    private_leaf
                    and index == len(path_parts) - 1
                    and stat.S_IMODE(metadata.st_mode) & 0o077
                )
            ):
                raise SafeNamespaceError
            continue

        if stat.S_ISLNK(metadata.st_mode):
            if metadata.st_uid not in {0, os.getuid()}:
                raise SafeNamespaceError
            try:
                resolved = current.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise SafeNamespaceError from error
            _admit_resolved_chain(resolved)
            try:
                metadata = current.stat()
            except OSError as error:
                raise SafeNamespaceError from error
        if not _admitted_directory(metadata):
            raise SafeNamespaceError
