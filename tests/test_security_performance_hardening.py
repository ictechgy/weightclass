from __future__ import annotations

import errno
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from weightclass import json_input, triage
from weightclass.delegation_runtime import (
    DelegationRuntimeUnavailableError,
    run_delegation_runtime,
    validate_delegation_runtime,
)
from weightclass.delegation_types import DirectChildCleanup
from weightclass.executable_observation import ExecutableObservation


class SecurityPerformanceHardeningTests(unittest.TestCase):
    def test_shared_json_loader_rejects_large_integer_before_schema_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text('{"value":' + "9" * 129 + "}", encoding="ascii")
            with self.assertRaisesRegex(json_input.JsonInputError, "^$"):
                json_input.load_json_object(path, max_bytes=1_024)

    def test_protocol_one_rejects_symlink_and_spawn_adjacent_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
            runtime.chmod(0o700)
            link = root / "runtime-link"
            link.symlink_to(runtime)
            with self.assertRaisesRegex(DelegationRuntimeUnavailableError, "^$"):
                validate_delegation_runtime(str(link))

        expected = ExecutableObservation("/runtime", 1, 1, stat.S_IFREG, 0o700, 1, 1, 1, True)
        changed = ExecutableObservation("/runtime", 1, 2, stat.S_IFREG, 0o700, 1, 1, 1, True)
        with (
            mock.patch(
                "weightclass.delegation_runtime.validate_delegation_runtime",
                return_value=changed,
            ),
            mock.patch(
                "weightclass.delegation_runtime.subprocess.Popen",
                side_effect=AssertionError("replacement must not spawn"),
            ) as popen,
            self.assertRaisesRegex(DelegationRuntimeUnavailableError, "^$"),
        ):
            run_delegation_runtime(
                "/runtime",
                b"frame",
                DirectChildCleanup(1, 1),
                expected,
            )
        popen.assert_not_called()

    def test_triage_status_loss_releases_numeric_signal_targets(self) -> None:
        process = mock.Mock()
        process.stdin = None
        process.stdout = None
        with (
            mock.patch.object(triage, "signal_process_group") as signal_group,
            mock.patch.object(triage, "wait_owned_child") as wait_child,
            mock.patch.object(triage, "close_leader_exit_queue"),
        ):
            failed, return_code, pending = triage._cleanup_vendor_process(
                process,
                None,
                None,
                None,
                None,
                True,
                bytearray(),
                time.monotonic() + 1,
                time.monotonic() + 2,
                True,
            )

        self.assertTrue(failed)
        self.assertIsNone(return_code)
        self.assertIsNone(pending)
        signal_group.assert_not_called()
        wait_child.assert_not_called()

    def test_triage_native_echild_is_classified_as_status_loss(self) -> None:
        self.assertTrue(triage._child_status_lost(OSError(errno.ECHILD, "already reaped")))
        self.assertFalse(triage._child_status_lost(OSError(errno.EIO, "other failure")))


if __name__ == "__main__":
    unittest.main()
