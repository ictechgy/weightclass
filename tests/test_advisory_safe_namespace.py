from __future__ import annotations

import os
import stat
import tempfile
import types
import unittest
from pathlib import Path

from weightclass.advisory import safe_namespace


class AdvisorySafeNamespaceTests(unittest.TestCase):
    def test_supported_posix_runtime_exposes_required_descriptor_operations(self) -> None:
        self.assertTrue(getattr(os, "O_NOFOLLOW", 0))
        self.assertTrue(getattr(os, "O_DIRECTORY", 0))
        for operation in (os.open, os.mkdir, os.stat, os.rename, os.unlink, os.rmdir):
            with self.subTest(operation=operation.__name__):
                self.assertIn(operation, os.supports_dir_fd)
        self.assertIn(os.listdir, os.supports_fd)

    def test_ancestor_permission_and_owner_rules_are_closed(self) -> None:
        uid = os.getuid()

        def metadata(mode: int, owner: int = uid) -> os.stat_result:
            return types.SimpleNamespace(st_mode=stat.S_IFDIR | mode, st_uid=owner)  # type: ignore[return-value]

        for mode in (0o755, 0o700, 0o1777):
            self.assertTrue(safe_namespace._admitted_directory(metadata(mode)))
        for mode in (0o770, 0o775, 0o777, 0o1733, 0o1770):
            self.assertFalse(safe_namespace._admitted_directory(metadata(mode)))
        self.assertFalse(safe_namespace._admitted_directory(metadata(0o755, uid + 1)))

    def test_safe_intermediate_link_requires_both_lexical_and_resolved_chains(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / "target"
            target.mkdir(mode=0o700)
            alias = base / "alias"
            alias.symlink_to(target, target_is_directory=True)
            selected = alias / "managed"

            safe_namespace.admit_existing_ancestors(
                selected,
                managed_root=selected,
                allow_missing=True,
            )

            base.chmod(0o777)
            with self.assertRaises(safe_namespace.SafeNamespaceError):
                safe_namespace.admit_existing_ancestors(
                    selected,
                    managed_root=selected,
                    allow_missing=True,
                )

    def test_managed_suffix_links_are_rejected_without_following_or_chmodding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            managed = base / "managed"
            target = base / "target"
            target.mkdir(mode=0o700)
            managed.symlink_to(target, target_is_directory=True)

            with self.assertRaises(safe_namespace.SafeNamespaceError):
                safe_namespace.ensure_private_directory(
                    managed / "child",
                    managed_root=managed,
                    create=True,
                )
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
            self.assertEqual(list(target.iterdir()), [])

    def test_missing_private_root_is_created_componentwise(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            selected = base / "one" / "two" / "managed"
            safe_namespace.ensure_private_directory(
                selected,
                managed_root=selected,
                create=True,
            )

            for path in (base / "one", base / "one" / "two", selected):
                self.assertFalse(path.is_symlink())
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)

    def test_existing_readable_managed_parents_can_precede_a_private_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            managed = base / "managed"
            managed.mkdir(mode=0o755)
            selected = managed / "private"

            safe_namespace.ensure_private_directory(
                selected,
                managed_root=managed,
                create=True,
            )

            self.assertEqual(stat.S_IMODE(managed.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(selected.stat().st_mode), 0o700)


if __name__ == "__main__":
    unittest.main()
