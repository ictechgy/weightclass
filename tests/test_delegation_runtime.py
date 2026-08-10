import ctypes
import errno
import fcntl
import io
import json
import os
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections.abc import Callable, Iterator
from contextlib import contextmanager, redirect_stderr
from pathlib import Path
from types import FrameType
from typing import Any, cast
from unittest import mock

from tests.runtime_guard import guarded_launch
from tests.test_delegation import _manifest, _policy
from tests.test_delegation_qualification import (
    _evidence,
    _registry_value,
    _write_executable,
)
from weightclass.cli import delegation_run_from_standard_input
from weightclass.delegation_compile import compile_delegation_descriptor
from weightclass.delegation_conformance import (
    _has_leader_exit_observer,
    _open_leader_exit_queue,
    _process_group_exists,
    _wait_for_leader_exit,
)
from weightclass.delegation_protocol import DelegationFrameError, encode_delegation_frame
from weightclass.delegation_qualification import (
    attach_qualification_requirement,
    build_qualification_candidate,
    load_qualification_registry,
)
from weightclass.delegation_runtime import (
    DelegationRuntimeFailedError,
    DelegationRuntimeUnavailableError,
    _cleanup_direct_child,
    _SigintDeferredUntilChildOwned,
    _wait,
    _write_all,
    run_delegation_runtime,
    validate_runtime_process_context,
)
from weightclass.delegation_schema import (
    current_platform_contract,
    load_delegation_manifest,
    load_delegation_policy,
)
from weightclass.delegation_types import DirectChildCleanup
from weightclass.process_context import (
    has_safe_sigchld_disposition as _has_safe_sigchld_disposition,
)

EXPECTED_TASK = "Apply the reviewed change. 테스트"
FAKE_RUNTIME = Path(__file__).parent / "fixtures" / "fake_delegation_runtime.py"


class _DarwinSigaction(ctypes.Structure):
    _fields_ = [
        ("handler", ctypes.c_void_p),
        ("mask", ctypes.c_uint32),
        ("flags", ctypes.c_int),
    ]


@contextmanager
def _darwin_hidden_no_child_wait() -> Iterator[Callable[[], None]]:
    """Provide one restorable installer for Python-hidden SA_NOCLDWAIT."""
    previous_sigchld = signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    libc = ctypes.CDLL(None, use_errno=True)
    old = _DarwinSigaction()
    if libc.sigaction(signal.SIGCHLD, None, ctypes.byref(old)) != 0:
        raise OSError(ctypes.get_errno(), "sigaction inspection failed")
    installed = _DarwinSigaction()
    ctypes.memmove(ctypes.byref(installed), ctypes.byref(old), ctypes.sizeof(old))
    installed.handler = None
    installed.flags |= 0x20

    def install() -> None:
        if libc.sigaction(signal.SIGCHLD, ctypes.byref(installed), None) != 0:
            raise OSError(ctypes.get_errno(), "sigaction installation failed")
        if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
            raise AssertionError("native SA_NOCLDWAIT must remain Python-hidden")

    try:
        yield install
    finally:
        try:
            if libc.sigaction(signal.SIGCHLD, ctypes.byref(old), None) != 0:
                raise OSError(ctypes.get_errno(), "sigaction restoration failed")
        finally:
            signal.signal(signal.SIGCHLD, previous_sigchld)


class DelegationProtocolUnitTests(unittest.TestCase):
    def _write_sigint_ignoring_runtime(self, directory: Path) -> tuple[Path, Path]:
        pid_path = directory / "runtime.pid"
        runtime_path = directory / "sigint-ignoring-runtime"
        runtime_path.write_text(
            "#!/usr/bin/env python3\n"
            "import os\n"
            "from pathlib import Path\n"
            "import signal\n"
            "import time\n"
            "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
            f"pid_path = Path({str(pid_path)!r})\n"
            "pid_tmp = pid_path.with_name(pid_path.name + '.tmp')\n"
            "pid_tmp.write_text(str(os.getpid()), encoding='ascii')\n"
            "os.replace(pid_tmp, pid_path)\n"
            "time.sleep(60)\n",
            encoding="utf-8",
        )
        runtime_path.chmod(runtime_path.stat().st_mode | stat.S_IXUSR)
        return runtime_path, pid_path

    def _write_stdin_draining_runtime(self, directory: Path) -> Path:
        runtime_path = directory / "stdin-draining-runtime"
        runtime_path.write_text(
            "#!/usr/bin/env python3\nimport sys\nsys.stdin.buffer.read()\n",
            encoding="utf-8",
        )
        runtime_path.chmod(runtime_path.stat().st_mode | stat.S_IXUSR)
        return runtime_path

    def _write_nonzero_runtime(self, directory: Path, start_marker: Path) -> Path:
        runtime_path = directory / "nonzero-runtime"
        runtime_path.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "from pathlib import Path\n"
            f"Path({str(start_marker)!r}).touch()\n"
            "sys.stdin.buffer.read()\n"
            "raise SystemExit(17)\n",
            encoding="utf-8",
        )
        runtime_path.chmod(runtime_path.stat().st_mode | stat.S_IXUSR)
        return runtime_path

    def _wait_for_runtime_pid(
        self,
        process: subprocess.Popen[bytes],
        pid_path: Path,
    ) -> int:
        deadline = time.monotonic() + 5
        while not pid_path.is_file():
            if time.monotonic() >= deadline:
                self.fail("runtime PID was not published")
            time.sleep(0.005)
        return int(pid_path.read_text(encoding="ascii"))

    def _cleanup_test_process(self, process: subprocess.Popen[bytes] | None) -> None:
        if process is None:
            return
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        if process.returncode is None:
            try:
                os.kill(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()

    def test_frame_encoding_has_exact_lengths_and_utf8_bytes(self) -> None:
        """Breaks if runtime framing becomes locale-dependent or structurally ambiguous."""
        frame = encode_delegation_frame(b"{}", "é")

        self.assertEqual(frame, b"WCD1\x00\x00\x00\x02{}\x00\x00\x00\x02\xc3\xa9")

    def test_frame_encoding_rejects_oversized_task_before_spawn(self) -> None:
        """Breaks if a 32-bit frame field bypasses the smaller protocol limit."""
        with self.assertRaises(DelegationFrameError):
            encode_delegation_frame(b"{}", "x" * 80_001)

    def test_write_all_retries_interruptions_and_partial_writes(self) -> None:
        """Breaks if a short write truncates the reviewed descriptor or task."""
        written = bytearray()
        attempts = 0
        read_descriptor, write_descriptor = os.pipe()

        def partial_write(file_descriptor: int, contents: bytes) -> int:
            nonlocal attempts
            self.assertEqual(file_descriptor, write_descriptor)
            attempts += 1
            if attempts == 1:
                raise InterruptedError()
            length = min(2, len(contents))
            written.extend(contents[:length])
            return length

        try:
            with mock.patch("weightclass.delegation_runtime.os.write", side_effect=partial_write):
                _write_all(write_descriptor, b"abcdef", 1)
        finally:
            os.close(read_descriptor)
            os.close(write_descriptor)

        self.assertEqual(bytes(written), b"abcdef")
        self.assertEqual(attempts, 4)

    def test_wait_preserves_one_finite_deadline_across_interruptions(self) -> None:
        """Breaks if each InterruptedError restarts the complete timeout."""
        process = mock.Mock(spec=subprocess.Popen)
        process.args = ("runtime",)
        process.pid = 123
        process.returncode = None

        with (
            mock.patch(
                "weightclass.delegation_runtime.os.waitpid",
                side_effect=[InterruptedError(), InterruptedError(), (123, 17 << 8)],
            ) as waitpid,
            mock.patch(
                "weightclass.delegation_runtime.time.monotonic", side_effect=[10.0, 11.0, 12.0]
            ),
        ):
            return_code = _wait(process, 5.0)

        self.assertEqual(return_code, 17)
        self.assertEqual(process.returncode, 17)
        self.assertEqual(waitpid.call_args_list, [mock.call(123, os.WNOHANG)] * 3)
        process.wait.assert_not_called()

    def test_wait_fails_closed_when_child_status_is_unavailable(self) -> None:
        """Breaks if ECHILD is converted to a synthetic successful exit."""
        process = mock.Mock(spec=subprocess.Popen)
        process.args = ("runtime",)
        process.pid = 123
        process.returncode = None

        with (
            mock.patch(
                "weightclass.delegation_runtime.os.waitpid",
                side_effect=ChildProcessError(),
            ) as waitpid,
            self.assertRaises(DelegationRuntimeFailedError),
        ):
            _wait(process, 1)

        self.assertIsNotNone(process.returncode)
        process.wait.assert_not_called()
        waitpid.reset_mock()
        with self.assertRaises(DelegationRuntimeFailedError):
            _wait(process, 1)
        waitpid.assert_not_called()

    def test_wait_decodes_signal_status_from_owned_waitpid(self) -> None:
        process = mock.Mock(spec=subprocess.Popen)
        process.args = ("runtime",)
        process.pid = 123
        process.returncode = None

        with mock.patch(
            "weightclass.delegation_runtime.os.waitpid",
            return_value=(123, signal.SIGTERM),
        ):
            return_code = _wait(process)

        self.assertEqual(return_code, -signal.SIGTERM)
        self.assertEqual(process.returncode, -signal.SIGTERM)
        process.wait.assert_not_called()

    def test_cleanup_does_not_signal_after_child_status_is_lost(self) -> None:
        """Breaks if ECHILD releases the PID before cleanup signals it."""
        process = mock.Mock(spec=subprocess.Popen)
        process.args = ("runtime",)
        process.pid = 123
        process.returncode = None
        process.stdin = mock.Mock()
        process.stdin.closed = False

        with (
            mock.patch(
                "weightclass.delegation_runtime.os.waitpid",
                side_effect=ChildProcessError(),
            ),
            mock.patch("weightclass.delegation_runtime.os.kill") as kill,
            self.assertRaises(DelegationRuntimeFailedError),
        ):
            _cleanup_direct_child(
                process,
                DirectChildCleanup(grace_seconds=1, terminate_grace_seconds=1),
            )

        process.stdin.close.assert_called_once_with()
        kill.assert_not_called()
        process.poll.assert_not_called()
        process.wait.assert_not_called()
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_keyboard_interrupt_during_write_cleans_child_then_reraises(self) -> None:
        """Breaks if an interrupted owner abandons the direct child during framing."""
        events: list[tuple[str, float | None] | str] = []
        process = mock.Mock(spec=subprocess.Popen)
        process.args = ("runtime",)
        process.pid = 123
        process.returncode = None
        process.stdin = mock.Mock()
        process.stdin.closed = False
        process.stdin.fileno.return_value = 99

        def close_stdin() -> None:
            events.append("close")
            process.stdin.closed = True

        process.stdin.close.side_effect = close_stdin
        wait_outcomes: list[BaseException | int]

        def wait(
            owned_process: subprocess.Popen[bytes],
            timeout: float | None = None,
        ) -> int:
            self.assertIs(owned_process, process)
            events.append(("wait", timeout))
            outcome = wait_outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            process.returncode = outcome
            return outcome

        wait_outcomes = [
            subprocess.TimeoutExpired(process.args, 2),
            subprocess.TimeoutExpired(process.args, 0),
            subprocess.TimeoutExpired(process.args, 3),
            subprocess.TimeoutExpired(process.args, 0),
            0,
        ]

        def signal_child(process_id: int, signal_number: int) -> None:
            self.assertEqual(process_id, process.pid)
            events.append("terminate" if signal_number == signal.SIGTERM else "kill")

        interruption = KeyboardInterrupt()

        with (
            mock.patch(
                "weightclass.delegation_runtime.subprocess.Popen",
                return_value=process,
            ),
            mock.patch(
                "weightclass.delegation_runtime._write_all",
                side_effect=interruption,
            ),
            mock.patch("weightclass.delegation_runtime._wait", side_effect=wait),
            mock.patch("weightclass.delegation_runtime.os.kill", side_effect=signal_child),
            self.assertRaises(KeyboardInterrupt) as raised,
        ):
            run_delegation_runtime(
                "runtime",
                b"frame",
                DirectChildCleanup(grace_seconds=2, terminate_grace_seconds=3),
            )

        self.assertIs(raised.exception, interruption)
        self.assertEqual(
            events,
            [
                "close",
                ("wait", 2),
                ("wait", 0),
                "terminate",
                ("wait", 3),
                ("wait", 0),
                "kill",
                ("wait", None),
            ],
        )

    def test_sigint_after_exec_before_popen_return_cleans_exact_child(self) -> None:
        """Breaks if spawn interruption loses an already-exec'd direct child handle."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            runtime_path, pid_path = self._write_sigint_ignoring_runtime(Path(temporary_directory))
            real_popen = cast(Any, subprocess.Popen)
            spawned: list[subprocess.Popen[bytes]] = []
            interruption = KeyboardInterrupt()

            def interrupt_spawn(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
                process = cast(subprocess.Popen[bytes], real_popen(*args, **kwargs))
                spawned.append(process)
                self._wait_for_runtime_pid(process, pid_path)
                os.kill(os.getpid(), signal.SIGINT)
                return process

            def previous_handler(signal_number: int, frame: FrameType | None) -> None:
                del signal_number, frame
                raise interruption

            previous_sigint = signal.signal(signal.SIGINT, previous_handler)
            try:
                with (
                    mock.patch(
                        "weightclass.delegation_runtime.subprocess.Popen",
                        side_effect=interrupt_spawn,
                    ),
                    self.assertRaises(KeyboardInterrupt) as raised,
                ):
                    run_delegation_runtime(
                        str(runtime_path),
                        b"frame",
                        DirectChildCleanup(grace_seconds=1, terminate_grace_seconds=1),
                    )

                self.assertIs(raised.exception, interruption)
                self.assertEqual(len(spawned), 1)
                process = spawned[0]
                runtime_pid = int(pid_path.read_text(encoding="ascii"))
                self.assertEqual(process.pid, runtime_pid)
                self.assertIsNotNone(process.stdin)
                assert process.stdin is not None
                self.assertTrue(process.stdin.closed)
                self.assertIsNotNone(process.returncode)
                with self.assertRaises(ProcessLookupError):
                    os.kill(runtime_pid, 0)
                self.assertIs(signal.getsignal(signal.SIGINT), previous_handler)
            finally:
                signal.signal(signal.SIGINT, previous_sigint)
                self._cleanup_test_process(spawned[0] if spawned else None)

    def test_sigint_after_popen_store_cleans_exact_child(self) -> None:
        """Breaks if SIGINT lands after Popen returns but before ownership is published."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            runtime_path, pid_path = self._write_sigint_ignoring_runtime(Path(temporary_directory))
            real_popen = cast(Any, subprocess.Popen)
            spawned: list[subprocess.Popen[bytes]] = []
            interrupted = False

            def capture_spawn(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
                process = cast(subprocess.Popen[bytes], real_popen(*args, **kwargs))
                spawned.append(process)
                self._wait_for_runtime_pid(process, pid_path)
                return process

            def interrupt_after_store(
                frame: FrameType,
                event: str,
                argument: object,
            ) -> Any:
                del argument
                nonlocal interrupted
                if (
                    not interrupted
                    and event == "line"
                    and frame.f_code is run_delegation_runtime.__code__
                    and spawned
                    and frame.f_locals.get("process") is spawned[0]
                ):
                    interrupted = True
                    sys.settrace(None)
                    os.kill(os.getpid(), signal.SIGINT)
                return interrupt_after_store

            previous_sigint = signal.signal(signal.SIGINT, signal.default_int_handler)
            try:
                with (
                    mock.patch(
                        "weightclass.delegation_runtime.subprocess.Popen",
                        side_effect=capture_spawn,
                    ),
                    self.assertRaises(KeyboardInterrupt),
                ):
                    sys.settrace(interrupt_after_store)
                    try:
                        run_delegation_runtime(
                            str(runtime_path),
                            b"frame",
                            DirectChildCleanup(
                                grace_seconds=1,
                                terminate_grace_seconds=1,
                            ),
                        )
                    finally:
                        sys.settrace(None)

                self.assertTrue(interrupted)
                self.assertEqual(len(spawned), 1)
                process = spawned[0]
                runtime_pid = int(pid_path.read_text(encoding="ascii"))
                self.assertEqual(process.pid, runtime_pid)
                self.assertIsNotNone(process.stdin)
                assert process.stdin is not None
                self.assertTrue(process.stdin.closed)
                self.assertIsNotNone(process.returncode)
                with self.assertRaises(ProcessLookupError):
                    os.kill(runtime_pid, 0)
                self.assertIs(signal.getsignal(signal.SIGINT), signal.default_int_handler)
            finally:
                sys.settrace(None)
                signal.signal(signal.SIGINT, previous_sigint)
                self._cleanup_test_process(spawned[0] if spawned else None)

    def test_repeated_sigint_during_cleanup_calls_returning_handler_once(self) -> None:
        """Breaks if cleanup is interruptible or a returning handler gains a new error."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            runtime_path, pid_path = self._write_sigint_ignoring_runtime(Path(temporary_directory))
            real_popen = cast(Any, subprocess.Popen)
            spawned: list[subprocess.Popen[bytes]] = []
            handler_observations: list[tuple[bool, int | None, bool]] = []

            def interrupt_spawn(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
                process = cast(subprocess.Popen[bytes], real_popen(*args, **kwargs))
                spawned.append(process)
                self._wait_for_runtime_pid(process, pid_path)
                os.kill(os.getpid(), signal.SIGINT)
                return process

            def interrupt_cleanup(
                process: subprocess.Popen[bytes],
                cleanup: DirectChildCleanup,
            ) -> None:
                os.kill(os.getpid(), signal.SIGINT)
                os.kill(os.getpid(), signal.SIGINT)
                _cleanup_direct_child(process, cleanup)

            def returning_handler(signal_number: int, frame: FrameType | None) -> None:
                del signal_number, frame
                process = spawned[0]
                try:
                    os.kill(process.pid, 0)
                except ProcessLookupError:
                    child_is_gone = True
                else:
                    child_is_gone = False
                handler_observations.append(
                    (
                        process.stdin is not None and process.stdin.closed,
                        process.returncode,
                        child_is_gone,
                    )
                )

            previous_sigint = signal.signal(signal.SIGINT, returning_handler)
            try:
                with (
                    mock.patch(
                        "weightclass.delegation_runtime.subprocess.Popen",
                        side_effect=interrupt_spawn,
                    ),
                    mock.patch(
                        "weightclass.delegation_runtime._cleanup_direct_child",
                        side_effect=interrupt_cleanup,
                    ),
                ):
                    result = run_delegation_runtime(
                        str(runtime_path),
                        b"frame",
                        DirectChildCleanup(grace_seconds=1, terminate_grace_seconds=1),
                    )

                self.assertEqual(len(spawned), 1)
                self.assertIsNotNone(result.returncode)
                self.assertEqual(result.returncode, spawned[0].returncode)
                self.assertEqual(
                    handler_observations,
                    [(True, result.returncode, True)],
                )
                self.assertIs(signal.getsignal(signal.SIGINT), returning_handler)
            finally:
                signal.signal(signal.SIGINT, previous_sigint)
                self._cleanup_test_process(spawned[0] if spawned else None)

    def test_sigint_ignore_disposition_is_preserved(self) -> None:
        """Breaks if the ownership guard turns an ignored SIGINT into interruption."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            runtime_path = self._write_stdin_draining_runtime(Path(temporary_directory))
            real_popen = cast(Any, subprocess.Popen)

            def signal_during_spawn(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
                process = cast(subprocess.Popen[bytes], real_popen(*args, **kwargs))
                os.kill(os.getpid(), signal.SIGINT)
                return process

            previous_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
            try:
                with mock.patch(
                    "weightclass.delegation_runtime.subprocess.Popen",
                    side_effect=signal_during_spawn,
                ):
                    result = run_delegation_runtime(
                        str(runtime_path),
                        b"frame",
                        DirectChildCleanup(grace_seconds=1, terminate_grace_seconds=1),
                    )

                self.assertEqual(result.returncode, 0)
                self.assertEqual(signal.getsignal(signal.SIGINT), signal.SIG_IGN)
            finally:
                signal.signal(signal.SIGINT, previous_sigint)

    @unittest.skipUnless(hasattr(signal, "SIGCHLD"), "requires SIGCHLD")
    def test_ignored_sigchld_fails_closed_before_runtime_spawn(self) -> None:
        """Breaks if auto-reaping turns a nonzero runtime exit into success."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            start_marker = directory / "runtime.started"
            runtime_path = self._write_nonzero_runtime(directory, start_marker)
            previous_sigchld = signal.signal(signal.SIGCHLD, signal.SIG_IGN)
            try:
                with self.assertRaises(DelegationRuntimeUnavailableError):
                    run_delegation_runtime(
                        str(runtime_path),
                        b"frame",
                        DirectChildCleanup(grace_seconds=1, terminate_grace_seconds=1),
                    )
                self.assertFalse(start_marker.exists())
            finally:
                signal.signal(signal.SIGCHLD, previous_sigchld)

    @unittest.skipUnless(hasattr(signal, "SIGCHLD"), "requires SIGCHLD")
    def test_callable_sigchld_fails_closed_before_runtime_spawn(self) -> None:
        """Breaks if an unknown handler can steal the direct child's exit status."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            start_marker = directory / "runtime.started"
            runtime_path = self._write_nonzero_runtime(directory, start_marker)

            def sigchld_handler(signal_number: int, frame: FrameType | None) -> None:
                del signal_number, frame

            previous_sigchld = signal.signal(signal.SIGCHLD, sigchld_handler)
            try:
                with self.assertRaises(DelegationRuntimeUnavailableError):
                    run_delegation_runtime(
                        str(runtime_path),
                        b"frame",
                        DirectChildCleanup(grace_seconds=1, terminate_grace_seconds=1),
                    )
                self.assertFalse(start_marker.exists())
            finally:
                signal.signal(signal.SIGCHLD, previous_sigchld)

    @unittest.skipUnless(hasattr(signal, "SIGCHLD"), "requires SIGCHLD")
    def test_default_sigchld_preserves_nonzero_runtime_exit(self) -> None:
        """Breaks if the signal preflight rejects the safe default disposition."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            start_marker = directory / "runtime.started"
            runtime_path = self._write_nonzero_runtime(directory, start_marker)
            previous_sigchld = signal.signal(signal.SIGCHLD, signal.SIG_DFL)
            try:
                result = run_delegation_runtime(
                    str(runtime_path),
                    b"frame",
                    DirectChildCleanup(grace_seconds=1, terminate_grace_seconds=1),
                )
                self.assertEqual(result.returncode, 17)
                self.assertTrue(start_marker.is_file())
            finally:
                signal.signal(signal.SIGCHLD, previous_sigchld)

    @unittest.skipUnless(hasattr(signal, "SIGCHLD"), "requires SIGCHLD")
    def test_sigchld_reaper_race_after_preflight_fails_closed(self) -> None:
        """Breaks if lost child status is reported as a successful runtime exit."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            start_marker = directory / "runtime.started"
            runtime_path = self._write_nonzero_runtime(directory, start_marker)
            real_popen = cast(Any, subprocess.Popen)

            def spawn_then_enable_auto_reap(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
                process = cast(subprocess.Popen[bytes], real_popen(*args, **kwargs))
                signal.signal(signal.SIGCHLD, signal.SIG_IGN)
                return process

            previous_sigchld = signal.signal(signal.SIGCHLD, signal.SIG_DFL)
            try:
                with (
                    mock.patch(
                        "weightclass.delegation_runtime.subprocess.Popen",
                        side_effect=spawn_then_enable_auto_reap,
                    ),
                    self.assertRaises(DelegationRuntimeFailedError),
                ):
                    run_delegation_runtime(
                        str(runtime_path),
                        b"frame",
                        DirectChildCleanup(grace_seconds=1, terminate_grace_seconds=1),
                    )
                self.assertTrue(start_marker.is_file())
            finally:
                signal.signal(signal.SIGCHLD, previous_sigchld)

    @unittest.skipUnless(hasattr(signal, "SIGCHLD"), "requires SIGCHLD")
    def test_spawn_adjacent_sigchld_check_follows_sigint_arming(self) -> None:
        """Breaks if SIGINT setup can invalidate SIGCHLD ownership before spawn."""
        original_arm = _SigintDeferredUntilChildOwned.arm

        def arm_then_enable_auto_reap(deferred_sigint: _SigintDeferredUntilChildOwned) -> None:
            original_arm(deferred_sigint)
            signal.signal(signal.SIGCHLD, signal.SIG_IGN)

        previous_sigchld = signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        previous_sigint = signal.getsignal(signal.SIGINT)
        try:
            with (
                mock.patch.object(
                    _SigintDeferredUntilChildOwned,
                    "arm",
                    arm_then_enable_auto_reap,
                ),
                mock.patch(
                    "weightclass.delegation_runtime.subprocess.Popen",
                    side_effect=AssertionError("runtime spawn must remain untouched"),
                ) as popen,
                self.assertRaises(DelegationRuntimeUnavailableError),
            ):
                run_delegation_runtime(
                    "runtime",
                    b"frame",
                    DirectChildCleanup(grace_seconds=1, terminate_grace_seconds=1),
                )
            popen.assert_not_called()
            self.assertIs(signal.getsignal(signal.SIGINT), previous_sigint)
        finally:
            signal.signal(signal.SIGCHLD, previous_sigchld)
            signal.signal(signal.SIGINT, previous_sigint)

    @unittest.skipUnless(sys.platform == "darwin", "requires Darwin sigaction")
    def test_hidden_sa_nocldwait_fails_closed_before_runtime_spawn(self) -> None:
        """Breaks if Python-hidden auto-reaping reaches process creation."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            start_marker = directory / "runtime.started"
            runtime_path = self._write_nonzero_runtime(directory, start_marker)
            with (
                _darwin_hidden_no_child_wait() as install,
                mock.patch(
                    "weightclass.delegation_runtime.subprocess.Popen",
                    side_effect=AssertionError("runtime spawn must remain untouched"),
                ) as popen,
            ):
                install()
                with self.assertRaises(DelegationRuntimeUnavailableError):
                    run_delegation_runtime(
                        str(runtime_path),
                        b"frame",
                        DirectChildCleanup(grace_seconds=1, terminate_grace_seconds=1),
                    )

            popen.assert_not_called()
            self.assertFalse(start_marker.exists())

    @staticmethod
    def _mock_linux_glibc(
        *,
        native_flags: int = 0,
        version: object = b"2.36",
    ) -> mock.Mock:
        libc = mock.Mock()
        libc.gnu_get_libc_version = mock.Mock(return_value=version)

        def inspect_sigaction(
            _signal_number: int,
            _new_action: Any,
            current_action: Any,
        ) -> int:
            action = cast(Any, current_action)._obj
            action.handler = None
            action.flags = native_flags
            return 0

        libc.sigaction.side_effect = inspect_sigaction
        return libc

    @staticmethod
    def _ctypes_sizeof_with_pointer_size(pointer_size: int) -> Any:
        real_sizeof = ctypes.sizeof

        def inspected_size(value: Any) -> int:
            if value is ctypes.c_void_p:
                return pointer_size
            return real_sizeof(value)

        return inspected_size

    def test_linux_glibc_native_sigchld_gate_matches_reviewed_dispositions(self) -> None:
        """Breaks if runtime and reviewed glibc native child-status policy diverge."""
        for native_flags, expected_safe in ((0, True), (0x02, False)):
            libc = self._mock_linux_glibc(native_flags=native_flags)
            with (
                self.subTest(native_flags=native_flags),
                mock.patch("weightclass.process_context.sys.platform", "linux"),
                mock.patch(
                    "weightclass.process_context.signal.getsignal",
                    return_value=signal.SIG_DFL,
                ),
                mock.patch(
                    "weightclass.process_context.os.uname",
                    return_value=mock.Mock(machine="x86_64"),
                ),
                mock.patch.object(
                    ctypes,
                    "sizeof",
                    side_effect=self._ctypes_sizeof_with_pointer_size(8),
                ),
                mock.patch.object(ctypes, "CDLL", return_value=libc),
            ):
                if expected_safe:
                    validate_runtime_process_context()
                else:
                    with self.assertRaises(DelegationRuntimeUnavailableError):
                        validate_runtime_process_context()

            libc.sigaction.assert_called_once()
            libc.gnu_get_libc_version.assert_called_once_with()

    def test_linux_unreviewed_libc_or_abi_fails_closed_in_runtime(self) -> None:
        """Breaks if runtime trusts unsupported native sigaction layouts."""
        cases = (
            ("i686", 8, self._mock_linux_glibc()),
            ("x86_64", 4, self._mock_linux_glibc()),
            ("x86_64", 8, object()),
            ("x86_64", 8, self._mock_linux_glibc(version=b"")),
        )
        for machine, pointer_size, libc in cases:
            with (
                self.subTest(machine=machine, pointer_size=pointer_size),
                mock.patch("weightclass.process_context.sys.platform", "linux"),
                mock.patch(
                    "weightclass.process_context.signal.getsignal",
                    return_value=signal.SIG_DFL,
                ),
                mock.patch(
                    "weightclass.process_context.os.uname",
                    return_value=mock.Mock(machine=machine),
                ),
                mock.patch.object(
                    ctypes,
                    "sizeof",
                    side_effect=self._ctypes_sizeof_with_pointer_size(pointer_size),
                ),
                mock.patch.object(ctypes, "CDLL", return_value=libc),
                self.assertRaises(DelegationRuntimeUnavailableError),
            ):
                validate_runtime_process_context()

    def test_unknown_sigchld_state_fails_closed_before_popen(self) -> None:
        """Breaks if an unreadable process signal boundary reaches spawn."""
        for unsafe_state in (OSError(), ValueError(), object()):
            with self.subTest(unsafe_state=type(unsafe_state).__name__):
                real_getsignal = signal.getsignal

                def getsignal(
                    signal_number: int,
                    *,
                    current_unsafe_state: object = unsafe_state,
                    current_getsignal: Any = real_getsignal,
                ) -> Any:
                    if signal_number != signal.SIGCHLD:
                        return current_getsignal(signal_number)
                    if isinstance(current_unsafe_state, BaseException):
                        raise current_unsafe_state
                    return current_unsafe_state

                with (
                    mock.patch(
                        "weightclass.process_context.signal.getsignal",
                        getsignal,
                    ),
                    mock.patch(
                        "weightclass.delegation_runtime.subprocess.Popen",
                        side_effect=AssertionError("runtime spawn must remain untouched"),
                    ) as popen,
                    self.assertRaises(DelegationRuntimeUnavailableError),
                ):
                    run_delegation_runtime(
                        "runtime",
                        b"frame",
                        DirectChildCleanup(grace_seconds=1, terminate_grace_seconds=1),
                    )
                popen.assert_not_called()

    def test_worker_thread_runtime_launch_fails_closed_before_popen(self) -> None:
        """Breaks if another thread can race the process-global SIGCHLD state."""
        errors: list[BaseException] = []

        def launch() -> None:
            try:
                run_delegation_runtime(
                    "runtime",
                    b"frame",
                    DirectChildCleanup(grace_seconds=1, terminate_grace_seconds=1),
                )
            except BaseException as error:
                errors.append(error)

        with mock.patch(
            "weightclass.delegation_runtime.subprocess.Popen",
            side_effect=AssertionError("runtime spawn must remain untouched"),
        ) as popen:
            thread = threading.Thread(target=launch)
            thread.start()
            thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], DelegationRuntimeUnavailableError)
        popen.assert_not_called()

    def test_sigint_default_disposition_is_redispatched_after_cleanup(self) -> None:
        """Breaks if SIG_DFL exits the owner before its direct child is reaped."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path, pid_path = self._write_sigint_ignoring_runtime(directory)
            helper_code = (
                "import os, signal, sys, time\n"
                "from pathlib import Path\n"
                "import weightclass.delegation_runtime as runtime\n"
                "from weightclass.delegation_types import DirectChildCleanup\n"
                "real_popen = runtime.subprocess.Popen\n"
                "def interrupt_spawn(*args, **kwargs):\n"
                " process = real_popen(*args, **kwargs)\n"
                " deadline = time.monotonic() + 5\n"
                " while not Path(sys.argv[2]).is_file():\n"
                "  if time.monotonic() >= deadline:\n"
                "   raise SystemExit(90)\n"
                "  time.sleep(0.005)\n"
                " os.kill(os.getpid(), signal.SIGINT)\n"
                " return process\n"
                "runtime.subprocess.Popen = interrupt_spawn\n"
                "signal.signal(signal.SIGINT, signal.SIG_DFL)\n"
                "runtime.run_delegation_runtime(\n"
                " sys.argv[1], b'frame', DirectChildCleanup(0.1, 0.1)\n"
                ")\n"
            )
            helper = subprocess.Popen(
                [sys.executable, "-c", helper_code, str(runtime_path), str(pid_path)],
                close_fds=True,
                start_new_session=True,
                stderr=subprocess.PIPE,
            )
            try:
                _, stderr = helper.communicate(timeout=8)
                self.assertEqual(helper.returncode, -signal.SIGINT, stderr)
                runtime_pid = int(pid_path.read_text(encoding="ascii"))
                with self.assertRaises(ProcessLookupError):
                    os.kill(runtime_pid, 0)
            finally:
                if helper.returncode is None:
                    try:
                        os.killpg(helper.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    helper.wait()
                if helper.stderr is not None and not helper.stderr.closed:
                    helper.stderr.close()

    def test_sigint_after_reap_does_not_signal_the_completed_pid(self) -> None:
        """Breaks if a pending SIGINT calls terminate or kill after direct-child reap."""
        process = mock.Mock(spec=subprocess.Popen)
        process.args = ("runtime",)
        process.stdin = mock.Mock()
        process.stdin.closed = False
        process.stdin.fileno.return_value = 99
        process.wait.return_value = 0
        process.returncode = 0
        interruption = KeyboardInterrupt()

        def reap_then_interrupt(
            owned_process: subprocess.Popen[bytes],
            interrupt_check: Any,
        ) -> int:
            del owned_process, interrupt_check
            signal.raise_signal(signal.SIGINT)
            return 0

        def previous_handler(signal_number: int, frame: FrameType | None) -> None:
            del signal_number, frame
            raise interruption

        previous_sigint = signal.signal(signal.SIGINT, previous_handler)
        try:
            with (
                mock.patch(
                    "weightclass.delegation_runtime.subprocess.Popen",
                    return_value=process,
                ),
                mock.patch("weightclass.delegation_runtime._write_all"),
                mock.patch(
                    "weightclass.delegation_runtime._wait_interruptibly",
                    side_effect=reap_then_interrupt,
                ),
                mock.patch("weightclass.delegation_runtime.os.kill") as kill,
                self.assertRaises(KeyboardInterrupt) as raised,
            ):
                run_delegation_runtime(
                    "runtime",
                    b"frame",
                    DirectChildCleanup(grace_seconds=2, terminate_grace_seconds=3),
                )

            self.assertIs(raised.exception, interruption)
            process.terminate.assert_not_called()
            process.kill.assert_not_called()
            process.wait.assert_not_called()
            process.poll.assert_not_called()
            kill.assert_not_called()
        finally:
            signal.signal(signal.SIGINT, previous_sigint)

    def test_spawn_errors_restore_the_previous_sigint_handler(self) -> None:
        """Breaks if unavailable spawn leaves the temporary handler installed."""
        previous_sigint = signal.getsignal(signal.SIGINT)
        for spawn_error in (OSError(), ValueError()):
            with self.subTest(spawn_error=type(spawn_error).__name__):
                with (
                    mock.patch(
                        "weightclass.delegation_runtime.subprocess.Popen",
                        side_effect=spawn_error,
                    ),
                    self.assertRaises(DelegationRuntimeUnavailableError),
                ):
                    run_delegation_runtime(
                        "runtime",
                        b"frame",
                        DirectChildCleanup(grace_seconds=2, terminate_grace_seconds=3),
                    )
                self.assertIs(signal.getsignal(signal.SIGINT), previous_sigint)

    def test_sigint_handler_swap_dispatches_each_signal_once(self) -> None:
        """Breaks if handler replacement loses or duplicates a boundary SIGINT."""
        received_frames: list[FrameType | None] = []

        def previous_handler(signal_number: int, frame: FrameType | None) -> None:
            del signal_number
            received_frames.append(frame)

        real_signal = signal.signal
        original_handler = real_signal(signal.SIGINT, previous_handler)
        deferred_sigint = _SigintDeferredUntilChildOwned()
        deferred_sigint.arm()
        injected = False

        def signal_during_swap(signal_number: int, handler: Any) -> Any:
            nonlocal injected
            if signal_number == signal.SIGINT and handler is previous_handler and not injected:
                injected = True
                os.kill(os.getpid(), signal.SIGINT)
            return real_signal(signal_number, handler)

        try:
            with mock.patch(
                "weightclass.delegation_runtime.signal.signal",
                side_effect=signal_during_swap,
            ):
                deferred_sigint.restore_and_dispatch()

            self.assertTrue(injected)
            self.assertEqual(len(received_frames), 1)
            self.assertIsNotNone(received_frames[0])
            self.assertIs(signal.getsignal(signal.SIGINT), previous_handler)
            os.kill(os.getpid(), signal.SIGINT)
            self.assertEqual(len(received_frames), 2)
            self.assertIsNotNone(received_frames[1])
        finally:
            real_signal(signal.SIGINT, original_handler)

    def test_unexpected_final_wait_error_cleans_child_then_reraises(self) -> None:
        """Breaks if an unexpected wait failure escapes while the direct child is live."""
        events: list[tuple[str, float | None] | str] = []
        process = mock.Mock(spec=subprocess.Popen)
        process.args = ("runtime",)
        process.pid = 123
        process.returncode = None
        process.stdin = mock.Mock()
        process.stdin.closed = False
        process.stdin.fileno.return_value = 99

        def close_stdin() -> None:
            events.append("close")
            process.stdin.closed = True

        process.stdin.close.side_effect = close_stdin
        wait_error = RuntimeError("unexpected wait failure")
        wait_outcomes: list[BaseException | int] = [
            wait_error,
            subprocess.TimeoutExpired(process.args, 2),
            subprocess.TimeoutExpired(process.args, 0),
            subprocess.TimeoutExpired(process.args, 3),
            subprocess.TimeoutExpired(process.args, 0),
            0,
        ]

        def wait(
            owned_process: subprocess.Popen[bytes],
            timeout: float | None = None,
        ) -> int:
            self.assertIs(owned_process, process)
            events.append(("wait", timeout))
            outcome = wait_outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            process.returncode = outcome
            return outcome

        def signal_child(process_id: int, signal_number: int) -> None:
            self.assertEqual(process_id, process.pid)
            events.append("terminate" if signal_number == signal.SIGTERM else "kill")

        with (
            mock.patch(
                "weightclass.delegation_runtime.subprocess.Popen",
                return_value=process,
            ),
            mock.patch("weightclass.delegation_runtime._write_all"),
            mock.patch("weightclass.delegation_runtime._wait", side_effect=wait),
            mock.patch("weightclass.delegation_runtime.os.kill", side_effect=signal_child),
            self.assertRaises(RuntimeError) as raised,
        ):
            run_delegation_runtime(
                "runtime",
                b"frame",
                DirectChildCleanup(grace_seconds=2, terminate_grace_seconds=3),
            )

        self.assertIs(raised.exception, wait_error)
        self.assertEqual(
            events,
            [
                "close",
                ("wait", 0.05),
                ("wait", 2),
                ("wait", 0),
                "terminate",
                ("wait", 3),
                ("wait", 0),
                "kill",
                ("wait", None),
            ],
        )

    def test_maximum_frame_to_nonreading_child_is_bounded_and_reaps_exact_pid(self) -> None:
        """Breaks if a full pipe blocks before direct-child cleanup can run."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            pid_path = directory / "runtime.pid"
            status_path = directory / "helper.status"
            runtime_path = directory / "nonreading-runtime"
            runtime_path.write_text(
                "#!/usr/bin/env python3\n"
                "import os\n"
                "from pathlib import Path\n"
                "import time\n"
                f"Path({str(pid_path)!r}).write_text(str(os.getpid()), encoding='ascii')\n"
                "time.sleep(60)\n",
                encoding="utf-8",
            )
            runtime_path.chmod(runtime_path.stat().st_mode | stat.S_IXUSR)
            helper = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    (
                        "import sys; "
                        "from weightclass.delegation_runtime import "
                        "DelegationRuntimeFailedError, run_delegation_runtime; "
                        "from weightclass.delegation_types import DirectChildCleanup; "
                        "\ntry:\n"
                        " run_delegation_runtime(sys.argv[1], b'x' * 342_156, "
                        "DirectChildCleanup(1, 1))\n"
                        "except DelegationRuntimeFailedError:\n"
                        " status = 7\n"
                        "except BaseException:\n"
                        " status = 99\n"
                        "else:\n"
                        " status = 0\n"
                        "from pathlib import Path\n"
                        "status_path = Path(sys.argv[2])\n"
                        "status_tmp = status_path.with_name(status_path.name + '.tmp')\n"
                        "status_tmp.write_text(str(status), encoding='ascii')\n"
                        "status_tmp.replace(status_path)\n"
                        "import time\n"
                        "time.sleep(60)\n"
                    ),
                    str(runtime_path),
                    str(status_path),
                ],
                close_fds=True,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 6
                while not (pid_path.is_file() and status_path.is_file()):
                    if time.monotonic() >= deadline:
                        self.fail("runtime helper did not publish its bounded result")
                    time.sleep(0.01)
                self.assertTrue(pid_path.is_file())
                runtime_pid = int(pid_path.read_text(encoding="ascii"))
                self.assertEqual(status_path.read_text(encoding="ascii"), "7")
                with self.assertRaises(ProcessLookupError):
                    os.kill(runtime_pid, 0)
            finally:
                try:
                    os.killpg(helper.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                helper.wait()


class DelegationRunTests(unittest.TestCase):
    def _write_inputs(
        self,
        directory: Path,
        *,
        cleanup_seconds: int = 1,
    ) -> tuple[Path, Path]:
        policy = _policy()
        workflows = policy["workflows"]
        assert isinstance(workflows, list)
        for workflow in workflows:
            assert isinstance(workflow, dict)
            workflow["direct_child_cleanup"] = {
                "grace_seconds": cleanup_seconds,
                "terminate_grace_seconds": cleanup_seconds,
            }
        policy_path = directory / "delegation-policy.json"
        manifest_path = directory / "runtime-manifest.json"
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
        return policy_path, manifest_path

    def _copy_runtime(self, directory: Path) -> Path:
        runtime_path = directory / "fake-delegation-runtime"
        shutil.copyfile(FAKE_RUNTIME, runtime_path)
        runtime_path.chmod(runtime_path.stat().st_mode | stat.S_IXUSR)
        return runtime_path

    def _write_escaped_writer_runtime(
        self,
        directory: Path,
    ) -> tuple[Path, Path, Path, Path, Path, Path]:
        runtime_path = directory / "escaped-writer-runtime"
        escaped_lock_path = directory / "escaped-writer.lock"
        escaped_ready_path = directory / "escaped-writer.ready"
        escaped_stop_path = directory / "escaped-writer.stop"
        escaped_stdin_closed_path = directory / "escaped-writer-stdin-closed"
        runtime_stdin_closed_path = directory / "runtime-stdin-closed"
        runtime_path.write_text(
            "#!/usr/bin/env python3\n"
            "import fcntl\n"
            "import os\n"
            "import time\n"
            "from pathlib import Path\n"
            f"lock_path = Path({str(escaped_lock_path)!r})\n"
            f"ready_path = Path({str(escaped_ready_path)!r})\n"
            f"stop_path = Path({str(escaped_stop_path)!r})\n"
            f"stdin_closed_path = Path({str(escaped_stdin_closed_path)!r})\n"
            f"runtime_stdin_closed_path = Path({str(runtime_stdin_closed_path)!r})\n"
            "def write_marker(path):\n"
            "    marker_fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)\n"
            "    os.close(marker_fd)\n"
            "child_pid = os.fork()\n"
            "if child_pid == 0:\n"
            "    os.setsid()\n"
            "    os.close(0)\n"
            "    write_marker(stdin_closed_path)\n"
            "    lock_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)\n"
            "    fcntl.flock(lock_fd, fcntl.LOCK_EX)\n"
            "    write_marker(ready_path)\n"
            "    deadline = time.monotonic() + 12\n"
            "    while not stop_path.is_file() and time.monotonic() < deadline:\n"
            "        time.sleep(0.01)\n"
            "    os._exit(0)\n"
            "deadline = time.monotonic() + 3\n"
            "while not ready_path.is_file():\n"
            "    if time.monotonic() >= deadline:\n"
            "        raise SystemExit(92)\n"
            "    time.sleep(0.01)\n"
            "os.close(0)\n"
            "write_marker(runtime_stdin_closed_path)\n"
            "print('escaped-runtime-started', flush=True)\n"
            "time.sleep(15)\n",
            encoding="utf-8",
        )
        runtime_path.chmod(runtime_path.stat().st_mode | stat.S_IXUSR)
        return (
            runtime_path,
            escaped_lock_path,
            escaped_ready_path,
            escaped_stop_path,
            escaped_stdin_closed_path,
            runtime_stdin_closed_path,
        )

    def _arguments(
        self,
        command: str,
        policy_path: Path,
        manifest_path: Path,
        runtime_path: Path,
    ) -> list[str]:
        return [
            sys.executable,
            "-m",
            "weightclass",
            "delegate",
            command,
            "--policy",
            str(policy_path),
            "--runtime-manifest",
            str(manifest_path),
            "--delegation-runtime",
            str(runtime_path),
            "--source-vendor",
            "claude",
            "--tier",
            "standard",
        ]

    def _review(
        self,
        policy_path: Path,
        manifest_path: Path,
        runtime_path: Path,
    ) -> dict[str, object]:
        result = self._run_router(
            self._arguments("route", policy_path, manifest_path, runtime_path),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        assert isinstance(value, dict)
        return value

    @staticmethod
    def _close_router_streams(process: subprocess.Popen[Any]) -> None:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

    @staticmethod
    def _signal_anchored_group(process: subprocess.Popen[Any]) -> None:
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except PermissionError:
            try:
                os.kill(process.pid, signal.SIGKILL)
            except (PermissionError, ProcessLookupError):
                pass
        except ProcessLookupError:
            pass

    @staticmethod
    def _pipe_reader(
        stream: Any,
        chunks: list[bytes],
        name: str,
        stop_event: threading.Event,
        io_failures: list[str],
    ) -> threading.Thread:
        """Read a pipe without holding a BufferedReader lock across cleanup."""
        file_descriptor = stream.fileno()
        os.set_blocking(file_descriptor, False)

        def read_output() -> None:
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(file_descriptor, selectors.EVENT_READ)
                    while True:
                        stopping = stop_event.is_set()
                        if not selector.select(0 if stopping else 0.05):
                            if stopping:
                                return
                            continue
                        try:
                            chunk = os.read(file_descriptor, 65_536)
                        except BlockingIOError:
                            continue
                        except OSError:
                            if not stop_event.is_set():
                                io_failures.append(name)
                            return
                        if not chunk:
                            return
                        chunks.append(chunk)
            except (OSError, ValueError):
                if not stop_event.is_set():
                    io_failures.append(name)

        return threading.Thread(target=read_output, daemon=True, name=f"router-{name}")

    @staticmethod
    def _is_expected_broken_pipe(error: BaseException) -> bool:
        return isinstance(error, BrokenPipeError) or (
            isinstance(error, OSError) and error.errno == errno.EPIPE
        )

    @staticmethod
    def _publish_private_marker(path: Path) -> None:
        try:
            marker_fd = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
        except FileExistsError:
            return
        os.close(marker_fd)

    @staticmethod
    def _stop_escaped_writer(
        escaped_stop_path: Path,
        escaped_lock_path: Path,
        *,
        escaped_ready_path: Path | None = None,
        timeout_seconds: float = 2,
    ) -> bool:
        """Request exit while the escaped writer still owns its lifetime lock."""
        DelegationRunTests._publish_private_marker(escaped_stop_path)
        ready_observed = escaped_ready_path is not None
        if escaped_ready_path is not None:
            deadline = time.monotonic() + timeout_seconds
            while not escaped_ready_path.is_file():
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.01)
        if not escaped_lock_path.is_file():
            return False
        lock_file = escaped_lock_path.open("r+")
        try:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                deadline = time.monotonic() + timeout_seconds
                while True:
                    try:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise AssertionError("escaped writer cleanup timed out") from None
                        time.sleep(0.01)
                    else:
                        return True
            else:
                return ready_observed
        finally:
            lock_file.close()

    def _finalize_broken_pipe_resources(
        self,
        process: subprocess.Popen[Any],
        started_threads: list[threading.Thread],
        stop_readers: threading.Event,
        *,
        exit_queue: object | None,
        leader_observed: bool,
        escaped_stop_path: Path,
        escaped_lock_path: Path,
        escaped_ready_path: Path | None = None,
    ) -> tuple[int | None, bool, bool]:
        """Close every owned probe resource while preserving the first error."""
        original_error = sys.exc_info()[1]
        cleanup_error: BaseException | None = None

        def attempt(operation: Any) -> None:
            nonlocal cleanup_error
            try:
                operation()
            except BaseException as error:
                if cleanup_error is None:
                    cleanup_error = error

        if process.returncode is None and not leader_observed:
            attempt(lambda: self._signal_anchored_group(process))
            if exit_queue is not None:
                attempt(lambda: _wait_for_leader_exit(process.pid, exit_queue, 2))

        attempt(stop_readers.set)
        for thread in started_threads:
            attempt(lambda thread=thread: thread.join(timeout=2))
        attempt(lambda: self._close_router_streams(process))

        escaped_stopped = False

        def stop_escaped_writer() -> None:
            nonlocal escaped_stopped
            escaped_stopped = self._stop_escaped_writer(
                escaped_stop_path,
                escaped_lock_path,
                escaped_ready_path=escaped_ready_path,
            )

        attempt(stop_escaped_writer)
        for thread in started_threads:
            try:
                thread_alive = thread.is_alive()
            except BaseException as error:
                if cleanup_error is None:
                    cleanup_error = error
                continue
            if thread_alive:
                attempt(lambda thread=thread: thread.join(timeout=2))
        io_complete = True
        for thread in started_threads:
            try:
                if thread.is_alive():
                    io_complete = False
            except BaseException as error:
                io_complete = False
                if cleanup_error is None:
                    cleanup_error = error

        if exit_queue is not None:
            attempt(cast(Any, exit_queue).close)

        return_code: int | None = process.returncode
        if process.returncode is None:

            def reap_router() -> None:
                nonlocal return_code
                return_code = process.wait(timeout=2)

            attempt(reap_router)

        if cleanup_error is not None and original_error is None:
            raise cleanup_error
        return return_code, io_complete, escaped_stopped

    def _run_router(
        self,
        arguments: list[str],
        *,
        input_text: str | None = None,
        environment: dict[str, str] | None = None,
        timeout_seconds: float = 8,
    ) -> subprocess.CompletedProcess[str]:
        process = subprocess.Popen(
            arguments,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            start_new_session=True,
            text=True,
        )
        try:
            try:
                stdout, stderr = process.communicate(input=input_text, timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                self._signal_anchored_group(process)
                try:
                    process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    self._close_router_streams(process)
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        raise AssertionError("router cleanup timed out") from None
                raise AssertionError("router subprocess timed out") from None
            return_code = process.returncode
            assert return_code is not None
            return subprocess.CompletedProcess(arguments, return_code, stdout, stderr)
        except BaseException:
            if process.returncode is None:
                self._signal_anchored_group(process)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    raise AssertionError("router cleanup timed out") from None
            raise
        finally:
            self._close_router_streams(process)

    def _observe_owned_group_before_reap(
        self,
        process: subprocess.Popen[Any],
        *,
        exit_queue: object | None,
        timeout_seconds: float,
    ) -> tuple[bool, bool, bool]:
        leader_observed = _wait_for_leader_exit(process.pid, exit_queue, timeout_seconds)
        live_group_observed = leader_observed and _process_group_exists(process.pid)
        cleanup_complete = True
        if not leader_observed or live_group_observed:
            self._signal_anchored_group(process)
            if not leader_observed:
                leader_observed = _wait_for_leader_exit(process.pid, exit_queue, 2)
            deadline = time.monotonic() + 2
            while _process_group_exists(process.pid):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    cleanup_complete = False
                    break
                time.sleep(min(0.005, remaining))
        return leader_observed, live_group_observed, cleanup_complete

    def _wait_without_closing_stdin(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        # These callers exercise pre-spawn confirmation/availability gates. The
        # router must exit without reading task stdin or starting a runtime, so
        # waiting for this direct child before draining its pipes is bounded.
        process = subprocess.Popen(
            arguments,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            text=True,
        )
        try:
            try:
                return_code = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._signal_anchored_group(process)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    raise AssertionError("router cleanup timed out") from None
                raise AssertionError("router subprocess timed out") from None
            stdout, stderr = process.communicate(timeout=1)
        except BaseException:
            if process.returncode is None:
                self._signal_anchored_group(process)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    raise AssertionError("router cleanup timed out") from None
            raise
        finally:
            self._close_router_streams(process)
        return subprocess.CompletedProcess(arguments, return_code, stdout, stderr)

    def test_bounded_router_timeout_kills_anchored_group_and_reaps(self) -> None:
        """Breaks if a timed-out test router can outlive its bounded helper."""
        captured: list[subprocess.Popen[str]] = []
        real_popen = cast(Any, subprocess.Popen)

        def capture_process(*args: Any, **kwargs: Any) -> subprocess.Popen[str]:
            process = cast(subprocess.Popen[str], real_popen(*args, **kwargs))
            captured.append(process)
            return process

        with (
            mock.patch(
                f"{__name__}.subprocess.Popen",
                side_effect=capture_process,
            ),
            self.assertRaisesRegex(AssertionError, "router subprocess timed out"),
        ):
            self._run_router(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                input_text="",
                timeout_seconds=0.05,
            )

        self.assertEqual(len(captured), 1)
        process = captured[0]
        self.assertEqual(process.returncode, -signal.SIGKILL)
        for stream in (process.stdin, process.stdout, process.stderr):
            self.assertIsNotNone(stream)
            assert stream is not None
            self.assertTrue(stream.closed)

    def test_signal_anchored_group_falls_back_to_direct_leader(self) -> None:
        """Breaks if a group permission failure abandons the live router leader."""
        process = mock.Mock(spec=subprocess.Popen)
        process.pid = 424_242
        process.returncode = None

        with (
            mock.patch.object(os, "killpg", side_effect=PermissionError),
            mock.patch.object(os, "kill") as kill,
        ):
            self._signal_anchored_group(process)

        kill.assert_called_once_with(process.pid, signal.SIGKILL)

    def test_pipe_reader_drains_queued_bytes_after_stop(self) -> None:
        """Breaks if shutdown drops bytes already queued by the router."""
        read_file_descriptor, write_file_descriptor = os.pipe()
        stream = os.fdopen(read_file_descriptor, "rb", buffering=0)
        chunks: list[bytes] = []
        io_failures: list[str] = []
        stop_event = threading.Event()
        try:
            os.write(write_file_descriptor, b"queued-output")
            stop_event.set()
            thread = self._pipe_reader(
                stream,
                chunks,
                "stdout",
                stop_event,
                io_failures,
            )
            thread.start()
            thread.join(timeout=1)

            self.assertFalse(thread.is_alive())
            self.assertEqual(chunks, [b"queued-output"])
            self.assertEqual(io_failures, [])
        finally:
            os.close(write_file_descriptor)
            stream.close()

    def test_expected_broken_pipe_does_not_hide_other_stdin_errors(self) -> None:
        self.assertTrue(self._is_expected_broken_pipe(BrokenPipeError()))
        self.assertTrue(self._is_expected_broken_pipe(OSError(errno.EPIPE, "closed")))
        self.assertFalse(self._is_expected_broken_pipe(OSError(errno.EIO, "failed")))
        self.assertFalse(self._is_expected_broken_pipe(ValueError("closed")))

    def test_escaped_stop_does_not_create_marker_when_lock_free(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            escaped_stop_path = directory / "escaped-writer.stop"
            escaped_lock_path = directory / "escaped-writer.lock"
            escaped_lock_path.touch()

            with mock.patch.object(os, "kill") as kill:
                self.assertFalse(self._stop_escaped_writer(escaped_stop_path, escaped_lock_path))

            kill.assert_not_called()
            self.assertTrue(escaped_stop_path.exists())

    def test_escaped_stop_ready_child_released_lock_is_success(self) -> None:
        """A ready child may finish between stop publication and lock probing."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            escaped_stop_path = directory / "escaped-writer.stop"
            escaped_lock_path = directory / "escaped-writer.lock"
            escaped_ready_path = directory / "escaped-writer.ready"
            escaped_lock_path.touch()
            escaped_ready_path.touch()

            self.assertTrue(
                self._stop_escaped_writer(
                    escaped_stop_path,
                    escaped_lock_path,
                    escaped_ready_path=escaped_ready_path,
                )
            )

    def test_escaped_stop_publishes_before_lock_exists(self) -> None:
        """Setup failure must leave a private stop request for a late child."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            escaped_stop_path = directory / "escaped-writer.stop"
            escaped_lock_path = directory / "escaped-writer.lock"

            self.assertFalse(self._stop_escaped_writer(escaped_stop_path, escaped_lock_path))
            self.assertTrue(escaped_stop_path.is_file())

    def test_escaped_stop_bounds_wait_for_late_ready_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            escaped_stop_path = directory / "escaped-writer.stop"
            escaped_lock_path = directory / "escaped-writer.lock"
            escaped_ready_path = directory / "escaped-writer.ready"
            started_at = time.monotonic()

            self.assertFalse(
                self._stop_escaped_writer(
                    escaped_stop_path,
                    escaped_lock_path,
                    escaped_ready_path=escaped_ready_path,
                    timeout_seconds=0.05,
                )
            )
            stop_published = escaped_stop_path.is_file()

        self.assertLess(time.monotonic() - started_at, 1)
        self.assertTrue(stop_published)

    def test_escaped_stop_does_not_signal_after_lock_releases(self) -> None:
        """A lock release race must not cause any process signal."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            escaped_stop_path = directory / "escaped-writer.stop"
            escaped_lock_path = directory / "escaped-writer.lock"
            escaped_lock_path.touch()

            with (
                mock.patch.object(
                    fcntl,
                    "flock",
                    side_effect=[BlockingIOError(), None],
                ),
                mock.patch.object(os, "kill") as kill,
            ):
                self._stop_escaped_writer(escaped_stop_path, escaped_lock_path)

            kill.assert_not_called()
            self.assertTrue(escaped_stop_path.is_file())

    def test_finalizer_waits_and_closes_queue_after_escaped_stop_failure(self) -> None:
        """An escaped-stop error must not skip queue close or the single final reap."""
        events: list[str] = []
        process = mock.Mock(spec=subprocess.Popen)
        process.pid = 424_242
        process.returncode = None

        def wait(*, timeout: float | None = None) -> int:
            del timeout
            events.append("wait")
            process.returncode = -signal.SIGKILL
            return -signal.SIGKILL

        process.wait.side_effect = wait
        started_thread = mock.Mock(spec=threading.Thread)
        started_thread.is_alive.return_value = False
        started_thread.join.side_effect = lambda **_: events.append("join")
        stop_readers = mock.Mock(spec=threading.Event)
        stop_readers.set.side_effect = lambda: events.append("stop-readers")
        exit_queue = mock.Mock()
        exit_queue.close.side_effect = lambda: events.append("queue-close")

        with (
            mock.patch.object(
                type(self),
                "_close_router_streams",
                side_effect=lambda *_: events.append("streams"),
            ),
            mock.patch.object(
                type(self),
                "_stop_escaped_writer",
                side_effect=lambda *_, **__: (_ for _ in ()).throw(RuntimeError("stop failed")),
            ),
            self.assertRaisesRegex(RuntimeError, "stop failed"),
        ):
            self._finalize_broken_pipe_resources(
                process,
                [started_thread],
                stop_readers,
                exit_queue=exit_queue,
                leader_observed=True,
                escaped_stop_path=Path("/private/stop"),
                escaped_lock_path=Path("/private/lock"),
            )

        self.assertEqual(events, ["stop-readers", "join", "streams", "queue-close", "wait"])

    def test_finalizer_preserves_original_error_after_cleanup_failure(self) -> None:
        process = mock.Mock(spec=subprocess.Popen)
        process.pid = 424_242
        process.returncode = None
        process.wait.return_value = -signal.SIGKILL
        started_thread = mock.Mock(spec=threading.Thread)
        started_thread.is_alive.return_value = False
        exit_queue = mock.Mock()

        with (
            mock.patch.object(type(self), "_close_router_streams"),
            mock.patch.object(
                type(self),
                "_stop_escaped_writer",
                side_effect=RuntimeError("stop failed"),
            ),
        ):
            try:
                raise ValueError("original failure")
            except ValueError:
                self._finalize_broken_pipe_resources(
                    process,
                    [started_thread],
                    threading.Event(),
                    exit_queue=exit_queue,
                    leader_observed=True,
                    escaped_stop_path=Path("/private/stop"),
                    escaped_lock_path=Path("/private/lock"),
                )
                with self.assertRaisesRegex(ValueError, "original failure"):
                    raise

        exit_queue.close.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=2)

    def test_live_group_observation_precedes_cleanup_and_reap(self) -> None:
        """Breaks if a leaked group is hidden by reaping its numeric anchor first."""
        events: list[str] = []
        process = mock.Mock(spec=subprocess.Popen)
        process.pid = 424_242
        process.returncode = None

        def observe_exit(pid: int, exit_queue: object, timeout_seconds: float) -> bool:
            del exit_queue, timeout_seconds
            self.assertEqual(pid, process.pid)
            events.append("observe-exit")
            return True

        group_states = iter((True, False))

        def group_exists(process_group_id: int) -> bool:
            self.assertEqual(process_group_id, process.pid)
            self.assertIsNone(process.returncode)
            exists = next(group_states)
            events.append("group-exists" if exists else "group-absent")
            return exists

        def kill_group(process_group_id: int, signal_number: int) -> None:
            self.assertEqual((process_group_id, signal_number), (process.pid, signal.SIGKILL))
            self.assertIsNone(process.returncode)
            events.append("kill-group")

        def reap(*, timeout: float) -> int:
            self.assertEqual(timeout, 2)
            events.append("reap")
            process.returncode = -signal.SIGKILL
            return -signal.SIGKILL

        process.wait.side_effect = reap
        with (
            mock.patch(
                f"{__name__}._wait_for_leader_exit",
                side_effect=observe_exit,
            ),
            mock.patch(
                f"{__name__}._process_group_exists",
                side_effect=group_exists,
            ),
            mock.patch(f"{__name__}.os.killpg", side_effect=kill_group),
        ):
            leader_observed, live_group_observed, cleanup_complete = (
                self._observe_owned_group_before_reap(
                    process,
                    exit_queue=None,
                    timeout_seconds=1,
                )
            )
            process.wait(timeout=2)

        self.assertTrue(leader_observed)
        self.assertTrue(live_group_observed)
        self.assertTrue(cleanup_complete)
        self.assertEqual(
            events,
            ["observe-exit", "group-exists", "kill-group", "group-absent", "reap"],
        )

    def test_confirmation_is_required_before_runtime_or_task_access(self) -> None:
        """Breaks if an unconfirmed run touches a runtime or blocks on task stdin."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path, manifest_path = self._write_inputs(directory)
            runtime_path = directory / "runtime-does-not-exist"

            result = self._wait_without_closing_stdin(
                self._arguments("run", policy_path, manifest_path, runtime_path)
            )

        self.assertEqual(result.returncode, 5)
        self.assertEqual(json.loads(result.stderr), {"error": "delegation_confirmation_required"})

    def test_empty_package_registry_blocks_qualified_run_before_task_access(self) -> None:
        """Breaks if qualification can fall back to declared enforcement during run."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path, manifest_path = self._write_inputs(directory)
            runtime_path = directory / "runtime-does-not-exist"
            arguments = self._arguments("run", policy_path, manifest_path, runtime_path)
            arguments.append("--require-qualified-runtime")

            result = self._wait_without_closing_stdin(arguments)

        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stderr), {"error": "unsupported_route"})

    def test_changed_qualified_artifact_blocks_task_access(self) -> None:
        """Breaks if task stdin is touched before the exact-artifact gate succeeds."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path, manifest_path = self._write_inputs(directory)
            runtime_path = directory / "runtime"
            _write_executable(runtime_path)
            record = build_qualification_candidate(_evidence(), runtime_path)
            registry_path = directory / "registry.json"
            registry_path.write_text(json.dumps(_registry_value(record)), encoding="utf-8")
            registry = load_qualification_registry(registry_path)
            descriptor = compile_delegation_descriptor(
                load_delegation_policy(policy_path),
                load_delegation_manifest(manifest_path),
                runtime_path=str(runtime_path),
                source_vendor="claude",
                tier="standard",
                target_platform=current_platform_contract(),
            )
            qualified = attach_qualification_requirement(descriptor, registry.records[0])
            _write_executable(runtime_path, b"changed-runtime\n")
            diagnostic = io.StringIO()

            with (
                mock.patch(
                    "weightclass.cli.load_packaged_qualification_registry",
                    return_value=registry,
                ),
                mock.patch(
                    "weightclass.cli.read_task_from_standard_input",
                    side_effect=AssertionError("task input must remain untouched"),
                ) as read_task,
                redirect_stderr(diagnostic),
            ):
                result = delegation_run_from_standard_input(
                    policy_path,
                    manifest_path,
                    str(runtime_path),
                    "claude",
                    "standard",
                    True,
                    str(qualified["route_fingerprint"]),
                    True,
                )

        self.assertEqual(result, 4)
        self.assertEqual(json.loads(diagnostic.getvalue()), {"error": "executor_unavailable"})
        read_task.assert_not_called()

    @unittest.skipUnless(hasattr(signal, "SIGCHLD"), "requires SIGCHLD")
    def test_unsafe_sigchld_blocks_task_access(self) -> None:
        """Breaks if task stdin is read before child-status ownership is safe."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path, manifest_path = self._write_inputs(directory)
            runtime_path = self._copy_runtime(directory)
            descriptor = compile_delegation_descriptor(
                load_delegation_policy(policy_path),
                load_delegation_manifest(manifest_path),
                runtime_path=str(runtime_path),
                source_vendor="claude",
                tier="standard",
                target_platform=current_platform_contract(),
            )
            diagnostic = io.StringIO()
            previous_sigchld = signal.signal(signal.SIGCHLD, signal.SIG_IGN)
            try:
                with (
                    mock.patch(
                        "weightclass.cli.read_task_from_standard_input",
                        side_effect=AssertionError("task input must remain untouched"),
                    ) as read_task,
                    redirect_stderr(diagnostic),
                ):
                    result = delegation_run_from_standard_input(
                        policy_path,
                        manifest_path,
                        str(runtime_path),
                        "claude",
                        "standard",
                        True,
                        str(descriptor["route_fingerprint"]),
                    )
            finally:
                signal.signal(signal.SIGCHLD, previous_sigchld)

        self.assertEqual(result, 4)
        self.assertEqual(json.loads(diagnostic.getvalue()), {"error": "executor_unavailable"})
        read_task.assert_not_called()

    @unittest.skipUnless(sys.platform == "darwin", "requires Darwin sigaction")
    def test_hidden_sa_nocldwait_blocks_cli_before_task_access_or_spawn(self) -> None:
        """Breaks if the CLI pre-task gate sees only Python's cached SIG_DFL."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path, manifest_path = self._write_inputs(directory)
            runtime_path = self._copy_runtime(directory)
            descriptor = compile_delegation_descriptor(
                load_delegation_policy(policy_path),
                load_delegation_manifest(manifest_path),
                runtime_path=str(runtime_path),
                source_vendor="claude",
                tier="standard",
                target_platform=current_platform_contract(),
            )
            diagnostic = io.StringIO()
            with (
                _darwin_hidden_no_child_wait() as install,
                mock.patch(
                    "weightclass.cli.read_task_from_standard_input",
                    side_effect=AssertionError("task input must remain untouched"),
                ) as read_task,
                mock.patch(
                    "weightclass.delegation_runtime.subprocess.Popen",
                    side_effect=AssertionError("runtime spawn must remain untouched"),
                ) as popen,
                redirect_stderr(diagnostic),
            ):
                install()
                result = delegation_run_from_standard_input(
                    policy_path,
                    manifest_path,
                    str(runtime_path),
                    "claude",
                    "standard",
                    True,
                    str(descriptor["route_fingerprint"]),
                )

        self.assertEqual(result, 4)
        self.assertEqual(json.loads(diagnostic.getvalue()), {"error": "executor_unavailable"})
        read_task.assert_not_called()
        popen.assert_not_called()

    @unittest.skipUnless(sys.platform == "darwin", "requires Darwin sigaction")
    def test_spawn_adjacent_recheck_blocks_new_hidden_sa_nocldwait(self) -> None:
        """Breaks if native child-status state can change after CLI preflight."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path, manifest_path = self._write_inputs(directory)
            runtime_path = self._copy_runtime(directory)
            descriptor = compile_delegation_descriptor(
                load_delegation_policy(policy_path),
                load_delegation_manifest(manifest_path),
                runtime_path=str(runtime_path),
                source_vendor="claude",
                tier="standard",
                target_platform=current_platform_contract(),
            )
            diagnostic = io.StringIO()

            def install_hidden_state_after_preflight() -> str:
                install()
                return EXPECTED_TASK

            with (
                _darwin_hidden_no_child_wait() as install,
                mock.patch(
                    "weightclass.cli.read_task_from_standard_input",
                    side_effect=install_hidden_state_after_preflight,
                ) as read_task,
                mock.patch(
                    "weightclass.delegation_runtime.subprocess.Popen",
                    side_effect=AssertionError("runtime spawn must remain untouched"),
                ) as popen,
                redirect_stderr(diagnostic),
            ):
                result = delegation_run_from_standard_input(
                    policy_path,
                    manifest_path,
                    str(runtime_path),
                    "claude",
                    "standard",
                    True,
                    str(descriptor["route_fingerprint"]),
                )

        self.assertEqual(result, 4)
        self.assertEqual(json.loads(diagnostic.getvalue()), {"error": "executor_unavailable"})
        read_task.assert_called_once_with()
        popen.assert_not_called()

    @unittest.skipUnless(hasattr(signal, "SIGCHLD"), "requires SIGCHLD")
    def test_spawn_adjacent_sigchld_recheck_blocks_runtime(self) -> None:
        """Breaks if the pre-task check is trusted after signal state changes."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path, manifest_path = self._write_inputs(directory)
            runtime_path = self._copy_runtime(directory)
            descriptor = compile_delegation_descriptor(
                load_delegation_policy(policy_path),
                load_delegation_manifest(manifest_path),
                runtime_path=str(runtime_path),
                source_vendor="claude",
                tier="standard",
                target_platform=current_platform_contract(),
            )

            def change_sigchld_after_preflight() -> str:
                signal.signal(signal.SIGCHLD, signal.SIG_IGN)
                return EXPECTED_TASK

            diagnostic = io.StringIO()
            previous_sigchld = signal.signal(signal.SIGCHLD, signal.SIG_DFL)
            try:
                with (
                    mock.patch(
                        "weightclass.cli.read_task_from_standard_input",
                        side_effect=change_sigchld_after_preflight,
                    ),
                    mock.patch(
                        "weightclass.delegation_runtime.subprocess.Popen",
                        side_effect=AssertionError("runtime spawn must remain untouched"),
                    ) as popen,
                    redirect_stderr(diagnostic),
                ):
                    result = delegation_run_from_standard_input(
                        policy_path,
                        manifest_path,
                        str(runtime_path),
                        "claude",
                        "standard",
                        True,
                        str(descriptor["route_fingerprint"]),
                    )
            finally:
                signal.signal(signal.SIGCHLD, previous_sigchld)

        self.assertEqual(result, 4)
        self.assertEqual(json.loads(diagnostic.getvalue()), {"error": "executor_unavailable"})
        popen.assert_not_called()

    @unittest.skipUnless(hasattr(signal, "SIGCHLD"), "requires SIGCHLD")
    def test_post_spawn_child_status_loss_maps_to_executor_failed(self) -> None:
        """Breaks if ECHILD escapes the CLI boundary or becomes success."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path, manifest_path = self._write_inputs(directory)
            start_marker = directory / "runtime.started"
            runtime_path = directory / "nonzero-runtime"
            runtime_path.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "from pathlib import Path\n"
                f"Path({str(start_marker)!r}).touch()\n"
                "sys.stdin.buffer.read()\n"
                "raise SystemExit(17)\n",
                encoding="utf-8",
            )
            runtime_path.chmod(runtime_path.stat().st_mode | stat.S_IXUSR)
            descriptor = compile_delegation_descriptor(
                load_delegation_policy(policy_path),
                load_delegation_manifest(manifest_path),
                runtime_path=str(runtime_path),
                source_vendor="claude",
                tier="standard",
                target_platform=current_platform_contract(),
            )
            real_popen = cast(Any, subprocess.Popen)

            def spawn_then_enable_auto_reap(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
                process = cast(subprocess.Popen[bytes], real_popen(*args, **kwargs))
                signal.signal(signal.SIGCHLD, signal.SIG_IGN)
                return process

            diagnostic = io.StringIO()
            previous_sigchld = signal.signal(signal.SIGCHLD, signal.SIG_DFL)
            try:
                with (
                    mock.patch(
                        "weightclass.cli.read_task_from_standard_input",
                        return_value=EXPECTED_TASK,
                    ),
                    mock.patch(
                        "weightclass.delegation_runtime.subprocess.Popen",
                        side_effect=spawn_then_enable_auto_reap,
                    ),
                    redirect_stderr(diagnostic),
                ):
                    result = delegation_run_from_standard_input(
                        policy_path,
                        manifest_path,
                        str(runtime_path),
                        "claude",
                        "standard",
                        True,
                        str(descriptor["route_fingerprint"]),
                    )
            finally:
                signal.signal(signal.SIGCHLD, previous_sigchld)
            marker_started = start_marker.is_file()

        self.assertEqual(result, 7)
        self.assertEqual(json.loads(diagnostic.getvalue()), {"error": "executor_failed"})
        self.assertTrue(marker_started)

    def test_fingerprint_mismatch_precedes_runtime_or_task_access(self) -> None:
        """Breaks if an unreviewed descriptor can reach runtime validation or stdin."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path, manifest_path = self._write_inputs(directory)
            runtime_path = directory / "runtime-does-not-exist"
            arguments = self._arguments("run", policy_path, manifest_path, runtime_path)
            arguments.extend(
                [
                    "--confirm-trusted-delegation-runtime",
                    "--ack-route-fingerprint",
                    "sha256:" + "0" * 64,
                ]
            )

            result = self._wait_without_closing_stdin(arguments)

        self.assertEqual(result.returncode, 6)
        self.assertEqual(json.loads(result.stderr), {"error": "route_fingerprint_mismatch"})

    def test_unavailable_runtime_precedes_task_access(self) -> None:
        """Breaks if task data is read before the reviewed runtime is available."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path, manifest_path = self._write_inputs(directory)
            runtime_path = directory / "runtime-does-not-exist"
            descriptor = self._review(policy_path, manifest_path, runtime_path)
            arguments = self._arguments("run", policy_path, manifest_path, runtime_path)
            arguments.extend(
                [
                    "--confirm-trusted-delegation-runtime",
                    "--ack-route-fingerprint",
                    str(descriptor["route_fingerprint"]),
                ]
            )

            result = self._wait_without_closing_stdin(arguments)

        self.assertEqual(result.returncode, 4)
        self.assertEqual(json.loads(result.stderr), {"error": "executor_unavailable"})

    @guarded_launch("delegation_v1")
    def test_success_sends_one_reviewed_frame_and_inherits_output(self) -> None:
        """Breaks if run changes the descriptor, task bytes, argv, or spawn count."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path, manifest_path = self._write_inputs(directory)
            runtime_path = self._copy_runtime(directory)
            start_marker_path = directory / "runtime-started"
            descriptor = self._review(policy_path, manifest_path, runtime_path)
            arguments = self._arguments("run", policy_path, manifest_path, runtime_path)
            arguments.extend(
                [
                    "--confirm-trusted-delegation-runtime",
                    "--ack-route-fingerprint",
                    str(descriptor["route_fingerprint"]),
                ]
            )
            environment = os.environ.copy()
            environment["WEIGHTCLASS_FAKE_DELEGATION_START_MARKER"] = str(start_marker_path)

            result = self._run_router(
                arguments,
                environment=environment,
                input_text=EXPECTED_TASK,
            )
            start_marker_contents = start_marker_path.read_text(encoding="ascii")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(start_marker_contents, "started\n")
        self.assertEqual(result.stdout.count("fake-runtime-ok"), 1)
        self.assertIn(f"fake-runtime-fingerprint:{descriptor['route_fingerprint']}", result.stdout)
        self.assertEqual(result.stderr, "fake-runtime-stderr\n")

    def test_invalid_task_does_not_start_runtime(self) -> None:
        """Breaks if a runtime starts before bounded task validation finishes."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path, manifest_path = self._write_inputs(directory)
            runtime_path = self._copy_runtime(directory)
            start_marker_path = directory / "runtime-started"
            descriptor = self._review(policy_path, manifest_path, runtime_path)
            arguments = self._arguments("run", policy_path, manifest_path, runtime_path)
            arguments.extend(
                [
                    "--confirm-trusted-delegation-runtime",
                    "--ack-route-fingerprint",
                    str(descriptor["route_fingerprint"]),
                ]
            )
            environment = os.environ.copy()
            environment["WEIGHTCLASS_FAKE_DELEGATION_START_MARKER"] = str(start_marker_path)

            result = self._run_router(
                arguments,
                environment=environment,
                input_text="",
            )
            start_marker_exists = start_marker_path.exists()

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_task"})
        self.assertEqual(result.stdout, "")
        self.assertFalse(start_marker_exists)

    def test_runtime_nonzero_maps_to_router_failure_without_task_content(self) -> None:
        """Breaks if runtime status collides with router codes or leaks task text."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path, manifest_path = self._write_inputs(directory)
            runtime_path = self._copy_runtime(directory)
            descriptor = self._review(policy_path, manifest_path, runtime_path)
            arguments = self._arguments("run", policy_path, manifest_path, runtime_path)
            arguments.extend(
                [
                    "--confirm-trusted-delegation-runtime",
                    "--ack-route-fingerprint",
                    str(descriptor["route_fingerprint"]),
                ]
            )
            environment = os.environ.copy()
            environment["WEIGHTCLASS_FAKE_DELEGATION_MODE"] = "exit-9"

            result = self._run_router(
                arguments,
                environment=environment,
                input_text="zephyrine glimmerfast quokka",
            )

        self.assertEqual(result.returncode, 7)
        self.assertEqual(
            json.loads([line for line in result.stderr.splitlines() if line][-1]),
            {"error": "executor_failed", "executor_exit_code": 9},
        )
        self.assertNotIn("zephyrine", result.stdout + result.stderr)

    def test_broken_pipe_terminates_and_reaps_owned_process_group(self) -> None:
        """Breaks if a closed-input escaped writer leaks the owned output pipes."""
        if (
            os.name != "posix"
            or not _has_leader_exit_observer()
            or not _has_safe_sigchld_disposition()
        ):
            self.skipTest("safe non-reaping leader observation is unavailable")
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path, manifest_path = self._write_inputs(directory, cleanup_seconds=1)
            (
                runtime_path,
                escaped_lock_path,
                escaped_ready_path,
                escaped_stop_path,
                escaped_stdin_closed_path,
                runtime_stdin_closed_path,
            ) = self._write_escaped_writer_runtime(directory)
            descriptor = self._review(policy_path, manifest_path, runtime_path)
            arguments = self._arguments("run", policy_path, manifest_path, runtime_path)
            arguments.extend(
                [
                    "--confirm-trusted-delegation-runtime",
                    "--ack-route-fingerprint",
                    str(descriptor["route_fingerprint"]),
                ]
            )
            started_at = time.monotonic()

            process = subprocess.Popen(
                arguments,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            exit_queue: object | None = None
            stdout_chunks: list[bytes] = []
            stderr_chunks: list[bytes] = []
            io_failures: list[str] = []
            threads: list[threading.Thread] = []
            started_threads: list[threading.Thread] = []
            stop_readers = threading.Event()
            leader_observed = False
            live_group_observed = False
            cleanup_complete = False
            io_complete = False
            return_code: int | None = None
            escaped_writer_stopped = False

            def write_task() -> None:
                assert process.stdin is not None
                try:
                    process.stdin.write(("🧪" * 20_000).encode())
                    process.stdin.flush()
                except OSError as error:
                    if not self._is_expected_broken_pipe(error):
                        io_failures.append("stdin")
                except ValueError:
                    io_failures.append("stdin")
                finally:
                    try:
                        process.stdin.close()
                    except OSError as error:
                        if not self._is_expected_broken_pipe(error):
                            io_failures.append("stdin-close")
                    except ValueError:
                        io_failures.append("stdin-close")

            try:
                exit_queue = _open_leader_exit_queue(process.pid)
                assert process.stdin is not None
                assert process.stdout is not None
                assert process.stderr is not None
                threads = [
                    threading.Thread(target=write_task, daemon=True, name="router-stdin"),
                    self._pipe_reader(
                        process.stdout,
                        stdout_chunks,
                        "stdout",
                        stop_readers,
                        io_failures,
                    ),
                    self._pipe_reader(
                        process.stderr,
                        stderr_chunks,
                        "stderr",
                        stop_readers,
                        io_failures,
                    ),
                ]
                for thread in threads:
                    thread.start()
                    started_threads.append(thread)
                ready_deadline = time.monotonic() + 5
                while not escaped_ready_path.is_file():
                    if process.poll() is not None:
                        self.fail("escaped writer exited before ready marker")
                    if time.monotonic() >= ready_deadline:
                        self.fail("escaped writer did not publish ready marker")
                    time.sleep(0.01)
                leader_observed, live_group_observed, cleanup_complete = (
                    self._observe_owned_group_before_reap(
                        process,
                        exit_queue=exit_queue,
                        timeout_seconds=8,
                    )
                )
                elapsed = time.monotonic() - started_at
            finally:
                return_code, io_complete, escaped_writer_stopped = (
                    self._finalize_broken_pipe_resources(
                        process,
                        started_threads,
                        stop_readers,
                        exit_queue=exit_queue,
                        leader_observed=leader_observed,
                        escaped_stop_path=escaped_stop_path,
                        escaped_lock_path=escaped_lock_path,
                        escaped_ready_path=escaped_ready_path,
                    )
                )

            stdout = b"".join(stdout_chunks).decode("utf-8")
            stderr = b"".join(stderr_chunks).decode("utf-8")
            escaped_stdin_closed = escaped_stdin_closed_path.is_file()
            runtime_stdin_closed = runtime_stdin_closed_path.is_file()

        self.assertEqual(return_code, 7, stderr)
        self.assertLess(elapsed, 8)
        self.assertTrue(leader_observed)
        self.assertFalse(live_group_observed)
        self.assertTrue(cleanup_complete)
        self.assertTrue(io_complete)
        self.assertTrue(escaped_writer_stopped)
        self.assertTrue(escaped_stdin_closed)
        self.assertTrue(runtime_stdin_closed)
        self.assertEqual(io_failures, [])
        self.assertEqual(stdout, "escaped-runtime-started\n")
        self.assertEqual(stderr, '{"error": "executor_failed"}\n')


if __name__ == "__main__":
    unittest.main()
