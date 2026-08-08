import io
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import FrameType
from typing import Any, cast
from unittest import mock

from tests.test_delegation import _manifest, _policy
from tests.test_delegation_qualification import (
    _evidence,
    _registry_value,
    _write_executable,
)
from weightclass.cli import delegation_run_from_standard_input
from weightclass.delegation_compile import compile_delegation_descriptor
from weightclass.delegation_protocol import DelegationFrameError, encode_delegation_frame
from weightclass.delegation_qualification import (
    attach_qualification_requirement,
    build_qualification_candidate,
    load_qualification_registry,
)
from weightclass.delegation_runtime import (
    DelegationRuntimeUnavailableError,
    _cleanup_direct_child,
    _DeferredSigint,
    _wait,
    _write_all,
    run_delegation_runtime,
)
from weightclass.delegation_schema import (
    current_platform_contract,
    load_delegation_manifest,
    load_delegation_policy,
)
from weightclass.delegation_types import DirectChildCleanup

EXPECTED_TASK = "Apply the reviewed change. 테스트"
FAKE_RUNTIME = Path(__file__).parent / "fixtures" / "fake_delegation_runtime.py"


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
            f"Path({str(pid_path)!r}).write_text(str(os.getpid()), encoding='ascii')\n"
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

    def _wait_for_runtime_pid(
        self,
        process: subprocess.Popen[bytes],
        pid_path: Path,
    ) -> int:
        deadline = time.monotonic() + 5
        while not pid_path.is_file():
            if process.poll() is not None or time.monotonic() >= deadline:
                self.fail("runtime PID was not published")
            time.sleep(0.005)
        return int(pid_path.read_text(encoding="ascii"))

    def _cleanup_test_process(self, process: subprocess.Popen[bytes] | None) -> None:
        if process is None:
            return
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        if process.poll() is None:
            process.kill()
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
        process.wait.side_effect = [InterruptedError(), InterruptedError(), 0]

        with mock.patch("time.monotonic", side_effect=[10.0, 11.0, 12.0]):
            return_code = _wait(process, 5.0)

        self.assertEqual(return_code, 0)
        self.assertEqual(
            process.wait.call_args_list,
            [mock.call(timeout=5.0), mock.call(timeout=4.0), mock.call(timeout=3.0)],
        )

    def test_keyboard_interrupt_during_write_cleans_child_then_reraises(self) -> None:
        """Breaks if an interrupted owner abandons the direct child during framing."""
        events: list[tuple[str, float | None] | str] = []
        process = mock.Mock(spec=subprocess.Popen)
        process.args = ("runtime",)
        process.stdin = mock.Mock()
        process.stdin.closed = False
        process.stdin.fileno.return_value = 99

        def close_stdin() -> None:
            events.append("close")
            process.stdin.closed = True

        process.stdin.close.side_effect = close_stdin
        wait_outcomes: list[BaseException | int] = [
            subprocess.TimeoutExpired(process.args, 2),
            subprocess.TimeoutExpired(process.args, 3),
            0,
        ]

        def wait(*, timeout: float | None = None) -> int:
            events.append(("wait", timeout))
            outcome = wait_outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        process.wait.side_effect = wait
        process.terminate.side_effect = lambda: events.append("terminate")
        process.kill.side_effect = lambda: events.append("kill")
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
                "terminate",
                ("wait", 3),
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
                "  if process.poll() is not None or time.monotonic() >= deadline:\n"
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
            os.kill(os.getpid(), signal.SIGINT)
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
            process.wait.assert_called_once_with(timeout=2)
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
        deferred_sigint = _DeferredSigint()
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
            subprocess.TimeoutExpired(process.args, 3),
            0,
        ]

        def wait(*, timeout: float | None = None) -> int:
            events.append(("wait", timeout))
            outcome = wait_outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        process.wait.side_effect = wait
        process.terminate.side_effect = lambda: events.append("terminate")
        process.kill.side_effect = lambda: events.append("kill")

        with (
            mock.patch(
                "weightclass.delegation_runtime.subprocess.Popen",
                return_value=process,
            ),
            mock.patch("weightclass.delegation_runtime._write_all"),
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
                "terminate",
                ("wait", 3),
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
                        "Path(sys.argv[2]).write_text(str(status), encoding='ascii')\n"
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
        result = subprocess.run(
            self._arguments("route", policy_path, manifest_path, runtime_path),
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        assert isinstance(value, dict)
        return value

    def _wait_without_closing_stdin(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        process = subprocess.Popen(
            arguments,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            return_code = process.wait(timeout=5)
            stdout, stderr = process.communicate(timeout=1)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        return subprocess.CompletedProcess(arguments, return_code, stdout, stderr)

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

    def test_success_sends_one_reviewed_frame_and_inherits_output(self) -> None:
        """Breaks if run changes the descriptor, task bytes, argv, or spawn count."""
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

            result = subprocess.run(
                arguments,
                capture_output=True,
                check=False,
                input=EXPECTED_TASK,
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("fake-runtime-ok"), 1)
        self.assertIn(f"fake-runtime-fingerprint:{descriptor['route_fingerprint']}", result.stdout)
        self.assertEqual(result.stderr, "fake-runtime-stderr\n")

    def test_invalid_task_does_not_start_runtime(self) -> None:
        """Breaks if a runtime starts before bounded task validation finishes."""
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

            result = subprocess.run(
                arguments,
                capture_output=True,
                check=False,
                input="",
                text=True,
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_task"})
        self.assertEqual(result.stdout, "")

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

            result = subprocess.run(
                arguments,
                capture_output=True,
                check=False,
                env=environment,
                input="zephyrine glimmerfast quokka",
                text=True,
            )

        self.assertEqual(result.returncode, 7)
        self.assertEqual(
            json.loads([line for line in result.stderr.splitlines() if line][-1]),
            {"error": "executor_failed", "executor_exit_code": 9},
        )
        self.assertNotIn("zephyrine", result.stdout + result.stderr)

    def test_broken_pipe_terminates_and_reaps_the_direct_child(self) -> None:
        """Breaks if post-spawn framing failure hangs or leaves its direct child alive."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            policy_path, manifest_path = self._write_inputs(directory, cleanup_seconds=1)
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
            environment["WEIGHTCLASS_FAKE_DELEGATION_MODE"] = "close-stdin-and-hang"
            started_at = time.monotonic()

            result = subprocess.run(
                arguments,
                capture_output=True,
                check=False,
                env=environment,
                input="🧪" * 20_000,
                text=True,
                timeout=8,
            )
            elapsed = time.monotonic() - started_at

        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertLess(elapsed, 8)
        pid_line = next(
            line for line in result.stdout.splitlines() if line.startswith("fake-runtime-pid:")
        )
        runtime_pid = int(pid_line.removeprefix("fake-runtime-pid:"))
        with self.assertRaises(ProcessLookupError):
            os.kill(runtime_pid, 0)
        self.assertEqual(
            json.loads([line for line in result.stderr.splitlines() if line][-1]),
            {"error": "executor_failed"},
        )


if __name__ == "__main__":
    unittest.main()
