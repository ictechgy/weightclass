"""Parent-owned, no-follow snapshots for read-only advisory workspaces.

The child workspace is deliberately not inspected with Git after execution.  A
Git repository is executable configuration: ``.git/config``, attributes,
filters, hooks, and the index are all untrusted child output.  This module
therefore walks the clean checkout with directory descriptors and compares
the same descriptors after the child exits.

Snapshots are transient and stay in memory.  The bounds are intentionally
conservative; an unsupported or raced tree must make the caller use the
existing clean handover clone, never silently count as unchanged. Extended
attributes are intentionally outside this Git-visible contract: they are not
read, logged, or used for acceptance, and the trusted handover is rebuilt from
the committed tree before verification.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

MAX_SNAPSHOT_ENTRIES = 200_000
MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024
MAX_SNAPSHOT_FILE_BYTES = 64 * 1024 * 1024
MAX_SNAPSHOT_DEPTH = 128
MAX_SYMLINK_TARGET_BYTES = 16 * 1024


class SnapshotError(RuntimeError):
    """Base class for a snapshot that cannot safely support a decision."""


class SnapshotUnsupported(SnapshotError):
    """The platform or tree exceeded the safe snapshot capability."""


class SnapshotRejected(SnapshotError):
    """The tree contains an object that a read-only result must reject."""


@dataclass(frozen=True)
class SnapshotEntry:
    kind: str
    mode: int
    size: int
    mtime_ns: int
    device: int
    inode: int
    links: int
    uid: int
    gid: int
    digest: bytes = b""
    symlink_target: bytes = b""


@dataclass(frozen=True)
class TreeSnapshot:
    root: SnapshotEntry
    entries: tuple[tuple[str, SnapshotEntry], ...]
    total_bytes: int


@dataclass(frozen=True)
class SnapshotComparison:
    changed: bool
    changed_count: int
    scaffolding: tuple[str, ...]


def _require_descriptor_support() -> tuple[int, int, int]:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    if not all(isinstance(value, int) and value for value in (nofollow, directory, cloexec)):
        raise SnapshotUnsupported()
    if os.open not in getattr(os, "supports_dir_fd", ()):
        raise SnapshotUnsupported()
    if os.lstat not in getattr(os, "supports_dir_fd", ()):
        raise SnapshotUnsupported()
    if os.readlink not in getattr(os, "supports_dir_fd", ()):
        raise SnapshotUnsupported()
    return nofollow, directory, cloexec


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
        stat.S_IMODE(metadata.st_mode),
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _entry_metadata(metadata: os.stat_result, kind: str) -> SnapshotEntry:
    return SnapshotEntry(
        kind=kind,
        mode=stat.S_IMODE(metadata.st_mode),
        size=metadata.st_size,
        mtime_ns=metadata.st_mtime_ns,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        links=metadata.st_nlink,
        uid=metadata.st_uid,
        gid=metadata.st_gid,
    )


def root_identity(root: Path) -> tuple[int, int]:
    """Return one real directory identity without following its pathname."""

    try:
        metadata = root.lstat()
    except OSError:
        raise SnapshotRejected() from None
    if not stat.S_ISDIR(metadata.st_mode):
        raise SnapshotRejected()
    return metadata.st_dev, metadata.st_ino


def same_root(root: Path, expected: tuple[int, int]) -> bool:
    try:
        return root_identity(root) == expected
    except SnapshotRejected:
        return False


def find_child_root(parent: Path, expected: tuple[int, int]) -> Path | None:
    """Find one bounded relocated directory without following child links."""

    try:
        parent_device = parent.lstat().st_dev
    except OSError:
        return None
    pending: list[tuple[Path, int]] = [(parent, 0)]
    visited = 0
    while pending:
        selected, depth = pending.pop()
        if depth > MAX_SNAPSHOT_DEPTH:
            return None
        try:
            children = tuple(os.scandir(selected))
        except (OSError, ValueError):
            return None
        for child in children:
            visited += 1
            if visited > MAX_SNAPSHOT_ENTRIES:
                return None
            try:
                metadata = child.stat(follow_symlinks=False)
            except OSError:
                continue
            if not stat.S_ISDIR(metadata.st_mode) or metadata.st_dev != parent_device:
                continue
            candidate = Path(child.path)
            if (metadata.st_dev, metadata.st_ino) == expected:
                return candidate
            pending.append((candidate, depth + 1))
    return None


def _read_regular(
    parent_fd: int,
    name: str,
    before: os.stat_result,
    nofollow: int,
    cloexec: int,
    total: list[int],
) -> SnapshotEntry:
    descriptor: int | None = None
    try:
        descriptor = os.open(name, os.O_RDONLY | nofollow | cloexec, dir_fd=parent_fd)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _identity(before) != _identity(opened):
            raise SnapshotUnsupported()
        if opened.st_size < 0 or opened.st_size > MAX_SNAPSHOT_FILE_BYTES:
            raise SnapshotUnsupported()
        digest = hashlib.sha256()
        read_bytes = 0
        while True:
            chunk = os.read(descriptor, min(65_536, MAX_SNAPSHOT_FILE_BYTES - read_bytes + 1))
            if not chunk:
                break
            read_bytes += len(chunk)
            if read_bytes > MAX_SNAPSHOT_FILE_BYTES:
                raise SnapshotUnsupported()
            total[0] += len(chunk)
            if total[0] > MAX_SNAPSHOT_BYTES:
                raise SnapshotUnsupported()
            digest.update(chunk)
        after = os.fstat(descriptor)
        if _identity(opened) != _identity(after) or read_bytes != after.st_size:
            raise SnapshotUnsupported()
        value = _entry_metadata(after, "file")
        return SnapshotEntry(
            kind=value.kind,
            mode=value.mode,
            size=value.size,
            mtime_ns=value.mtime_ns,
            device=value.device,
            inode=value.inode,
            links=value.links,
            uid=value.uid,
            gid=value.gid,
            digest=digest.digest(),
        )
    except SnapshotError:
        raise
    except (OSError, ValueError):
        raise SnapshotUnsupported() from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _read_symlink(
    parent_fd: int, name: str, before: os.stat_result, total: list[int]
) -> SnapshotEntry:
    try:
        target = os.readlink(name, dir_fd=parent_fd)
        encoded = os.fsencode(target)
        if len(encoded) > MAX_SYMLINK_TARGET_BYTES:
            raise SnapshotUnsupported()
        after = os.lstat(name, dir_fd=parent_fd)
        if _identity(before) != _identity(after):
            raise SnapshotUnsupported()
        total[0] += len(encoded)
        if total[0] > MAX_SNAPSHOT_BYTES:
            raise SnapshotUnsupported()
        value = _entry_metadata(after, "symlink")
        return SnapshotEntry(
            kind=value.kind,
            mode=value.mode,
            size=value.size,
            mtime_ns=value.mtime_ns,
            device=value.device,
            inode=value.inode,
            links=value.links,
            uid=value.uid,
            gid=value.gid,
            symlink_target=encoded,
        )
    except SnapshotError:
        raise
    except (OSError, ValueError, UnicodeError):
        raise SnapshotUnsupported() from None


def _scan_directory(
    descriptor: int,
    *,
    relative: str,
    depth: int,
    entries: dict[str, SnapshotEntry],
    total: list[int],
    nofollow: int,
    directory: int,
    cloexec: int,
    skip_top_level_git: bool,
    root_device: int,
) -> None:
    if depth > MAX_SNAPSHOT_DEPTH:
        raise SnapshotUnsupported()
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISDIR(before.st_mode):
            raise SnapshotUnsupported()
        names = sorted(os.listdir(descriptor))
    except SnapshotError:
        raise
    except (OSError, ValueError):
        raise SnapshotUnsupported() from None

    for name in names:
        if skip_top_level_git and depth == 0 and name == ".git":
            continue
        if depth > 0 and name == ".git":
            raise SnapshotRejected()
        child_relative = f"{relative}/{name}" if relative else name
        if len(entries) >= MAX_SNAPSHOT_ENTRIES:
            raise SnapshotUnsupported()
        try:
            metadata = os.lstat(name, dir_fd=descriptor)
            if metadata.st_dev != root_device:
                raise SnapshotRejected()
            mode = metadata.st_mode
            if stat.S_ISREG(mode):
                entry = _read_regular(descriptor, name, metadata, nofollow, cloexec, total)
            elif stat.S_ISLNK(mode):
                entry = _read_symlink(descriptor, name, metadata, total)
            elif stat.S_ISDIR(mode):
                child: int | None = None
                try:
                    child = os.open(
                        name,
                        os.O_RDONLY | directory | nofollow | cloexec,
                        dir_fd=descriptor,
                    )
                    opened = os.fstat(child)
                    if _identity(metadata) != _identity(opened):
                        raise SnapshotUnsupported()
                    entry = _entry_metadata(opened, "directory")
                    entries[child_relative] = entry
                    _scan_directory(
                        child,
                        relative=child_relative,
                        depth=depth + 1,
                        entries=entries,
                        total=total,
                        nofollow=nofollow,
                        directory=directory,
                        cloexec=cloexec,
                        skip_top_level_git=skip_top_level_git,
                        root_device=root_device,
                    )
                    after = os.fstat(child)
                    if _identity(opened) != _identity(after):
                        raise SnapshotUnsupported()
                    continue
                finally:
                    if child is not None:
                        try:
                            os.close(child)
                        except OSError:
                            pass
            else:
                raise SnapshotRejected()
            entries[child_relative] = entry
        except SnapshotError:
            raise
        except (OSError, ValueError, UnicodeError):
            raise SnapshotUnsupported() from None

    try:
        after_names = sorted(os.listdir(descriptor))
        after = os.fstat(descriptor)
        if names != after_names or _identity(before) != _identity(after):
            raise SnapshotUnsupported()
    except SnapshotError:
        raise
    except (OSError, ValueError):
        raise SnapshotUnsupported() from None


def snapshot_tree(root: Path) -> TreeSnapshot:
    """Capture a bounded clean-tree snapshot without following pathnames."""
    nofollow, directory, cloexec = _require_descriptor_support()
    descriptor: int | None = None
    try:
        expected_root = root_identity(root)
        descriptor = os.open(root, os.O_RDONLY | directory | nofollow | cloexec)
        opened_root = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(opened_root.st_mode)
            or (
                opened_root.st_dev,
                opened_root.st_ino,
            )
            != expected_root
        ):
            raise SnapshotRejected()
        entries: dict[str, SnapshotEntry] = {}
        total = [0]
        _scan_directory(
            descriptor,
            relative="",
            depth=0,
            entries=entries,
            total=total,
            nofollow=nofollow,
            directory=directory,
            cloexec=cloexec,
            skip_top_level_git=True,
            root_device=os.fstat(descriptor).st_dev,
        )
        final_root = os.fstat(descriptor)
        if (final_root.st_dev, final_root.st_ino) != expected_root:
            raise SnapshotRejected()
        return TreeSnapshot(
            _entry_metadata(final_root, "directory"), tuple(sorted(entries.items())), total[0]
        )
    except SnapshotError:
        raise
    except (OSError, ValueError):
        raise SnapshotUnsupported() from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def compare_tree(
    root: Path, baseline: TreeSnapshot, scaffolding: frozenset[str]
) -> SnapshotComparison:
    """Compare a post-child tree and return only task-free aggregate details."""
    expected_root = (baseline.root.device, baseline.root.inode)
    if not same_root(root, expected_root):
        raise SnapshotRejected()
    current = snapshot_tree(root)
    if (current.root.device, current.root.inode) != expected_root:
        raise SnapshotRejected()
    before = dict(baseline.entries)
    after = dict(current.entries)
    changed_names = {name for name in before.keys() ^ after.keys()}
    changed_names.update(
        name for name in before.keys() & after.keys() if before[name] != after[name]
    )
    scaffolding_names = {
        part for name in changed_names for part in name.split("/") if part in scaffolding
    }
    root_changed = current.root != baseline.root
    return SnapshotComparison(
        changed=bool(changed_names) or root_changed,
        changed_count=len(changed_names) + int(root_changed),
        scaffolding=tuple(sorted(scaffolding_names)),
    )
