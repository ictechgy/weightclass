import ctypes
import errno
import fcntl
import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, cast
from unittest import mock

from tests.runtime_guard import guarded_launch
from weightclass.delegation_conformance import (
    CONFORMANCE_CASES,
    ConformanceCase,
    _DeferredSigint,
    _linux_proc_stat_live_group_member,
    _observe_leader_exit,
    _open_leader_exit_queue,
    _run_driver_case,
    _wait_after_kill,
    run_conformance,
)
from weightclass.delegation_qualification import (
    QualificationInvalidInputError,
    build_qualification_candidate,
    load_packaged_qualification_registry,
)
from weightclass.process_context import (
    has_safe_sigchld_disposition as _has_safe_sigchld_disposition,
)

FAKE_DRIVER = Path(__file__).parent / "fixtures" / "fake_conformance_driver.py"
FIXED_SENTINEL_RUNTIME = Path(__file__).parent / "fixtures" / "fixed_conformance_sentinel.py"

_SIGNAL_CASE_RUNNER = r"""
import json
import signal
import sys
import time
from pathlib import Path

import weightclass.delegation_conformance as conformance

driver_path = Path(sys.argv[1])
runtime_path = Path(sys.argv[2])
workspace_path = Path(sys.argv[3])
handler_mode = sys.argv[4]
handler_path = Path(sys.argv[5])
cleanup_path = Path(sys.argv[6])
cleanup_observation_path = Path(sys.argv[7])

cleanup_state = {"completed": False}
original_cleanup = conformance._DriverCaseOwnership.cleanup

def observed_cleanup(ownership):
    if cleanup_path.name != "-":
        cleanup_path.write_text("started", encoding="ascii")
        time.sleep(0.3)
    original_before_final_reap = ownership.before_final_reap
    original_wait_after_kill = conformance._wait_after_kill
    observation = {}
    reap_calls = 0

    def observed_before_final_reap():
        process = ownership.process
        process_group_id = ownership.process_group_id
        assert process is not None
        assert process_group_id is not None
        assert process.stdin is not None
        assert process.stdout is not None
        reader_signal_target = ownership.reader_signal_target
        observation.update(
            {
                "group_gone_before_reap": not conformance._process_group_exists(
                    process_group_id
                ),
                "leader_unreaped_before_reap": process.returncode is None,
                "reader_signal_target_cleared_before_reap": (
                    reader_signal_target is not None
                    and reader_signal_target._process_group_id is None
                ),
                "streams_closed_before_reap": (
                    process.stdin.closed and process.stdout.closed
                ),
            }
        )
        if original_before_final_reap is not None:
            original_before_final_reap()
        deferred_signal_target = getattr(original_before_final_reap, "__self__", None)
        observation["deferred_signal_target_cleared_before_reap"] = (
            deferred_signal_target is not None
            and deferred_signal_target.process_group_id is None
        )

    def observed_wait_after_kill(process):
        nonlocal reap_calls
        reap_calls += 1
        return original_wait_after_kill(process)

    ownership.before_final_reap = observed_before_final_reap
    conformance._wait_after_kill = observed_wait_after_kill
    try:
        original_cleanup(ownership)
    finally:
        conformance._wait_after_kill = original_wait_after_kill
    process = ownership.process
    cleanup_state["completed"] = (
        ownership.cleaned
        and not ownership.group_cleanup_incomplete
        and ownership.process_group_id is None
        and (process is None or process.returncode is not None)
    )
    observation.update(
        {
            "reap_calls": reap_calls,
            "reaped_after_cleanup": process is not None and process.returncode is not None,
            "target_cleared_after_reap": ownership.process_group_id is None,
        }
    )
    cleanup_observation_path.write_text(
        json.dumps(observation, sort_keys=True),
        encoding="ascii",
    )

conformance._DriverCaseOwnership.cleanup = observed_cleanup

if handler_mode == "default":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
elif handler_mode == "ignore":
    signal.signal(signal.SIGINT, signal.SIG_IGN)
elif handler_mode == "callable":
    def previous_handler(signal_number, frame):
        del signal_number, frame
        with handler_path.open("a", encoding="ascii") as stream:
            stream.write("after-cleanup\n" if cleanup_state["completed"] else "before-cleanup\n")

    signal.signal(signal.SIGINT, previous_handler)
else:
    raise SystemExit(90)

try:
    passed = conformance._run_driver_case(
        driver_path,
        runtime_path,
        conformance.CONFORMANCE_CASES[0],
        workspace_path,
        timeout_seconds=0.5,
    )
except KeyboardInterrupt:
    raise SystemExit(130)

if handler_mode == "ignore" and signal.getsignal(signal.SIGINT) != signal.SIG_IGN:
    raise SystemExit(91)
raise SystemExit(0 if passed else 1)
"""

_SIGCHLD_CLI_RUNNER = r"""
import ctypes
import os
from pathlib import Path
import signal
import sys

import weightclass.delegation_conformance as conformance

mode = sys.argv[1]
spawn_marker_path = Path(sys.argv[2])
if mode == "ignore":
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)
elif mode == "callable":
    def sigchld_handler(signal_number, frame):
        del signal_number, frame

    signal.signal(signal.SIGCHLD, sigchld_handler)
elif mode == "default":
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
elif mode == "nocldwait":
    if sys.platform == "darwin":
        class Sigaction(ctypes.Structure):
            _fields_ = [
                ("handler", ctypes.c_void_p),
                ("mask", ctypes.c_uint32),
                ("flags", ctypes.c_int),
            ]

        no_child_wait = 0x20
    elif sys.platform.startswith("linux"):
        class Sigset(ctypes.Structure):
            _fields_ = [("values", ctypes.c_ubyte * 128)]

        class Sigaction(ctypes.Structure):
            _fields_ = [
                ("handler", ctypes.c_void_p),
                ("mask", Sigset),
                ("flags", ctypes.c_int),
                ("restorer", ctypes.c_void_p),
            ]

        no_child_wait = 0x02
    else:
        raise SystemExit(94)

    libc = ctypes.CDLL(None, use_errno=True)
    current = Sigaction()
    if libc.sigaction(signal.SIGCHLD, None, ctypes.byref(current)) != 0:
        raise SystemExit(91)
    installed = Sigaction()
    ctypes.memmove(ctypes.byref(installed), ctypes.byref(current), ctypes.sizeof(current))
    installed.handler = None
    installed.flags |= no_child_wait
    if libc.sigaction(signal.SIGCHLD, ctypes.byref(installed), None) != 0:
        raise SystemExit(92)
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        raise SystemExit(93)
    try:
        probe_pid = os.posix_spawn(
            sys.executable,
            (sys.executable, "-I", "-S", "-c", ""),
            {},
        )
    except (OSError, ValueError):
        raise SystemExit(95)
    while True:
        try:
            waited_pid, _wait_status = os.waitpid(probe_pid, 0)
        except InterruptedError:
            continue
        except ChildProcessError:
            break
        except OSError:
            raise SystemExit(95)
        if waited_pid == probe_pid:
            raise SystemExit(95)
        raise SystemExit(95)
else:
    raise SystemExit(90)

real_popen = conformance.subprocess.Popen

def marked_popen(*args, **kwargs):
    spawn_marker_path.write_text("spawn-attempted", encoding="ascii")
    return real_popen(*args, **kwargs)

conformance.subprocess.Popen = marked_popen

conformance.CONFORMANCE_CASES = conformance.CONFORMANCE_CASES[:1]
conformance.CASE_TIMEOUT_SECONDS = 0.1
raise SystemExit(conformance.main(sys.argv[3:]))
"""


def _write_executable(path: Path, contents: bytes) -> None:
    path.write_bytes(contents)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class DelegationConformanceRunnerTests(unittest.TestCase):
    @staticmethod
    def _mock_driver_process() -> subprocess.Popen[bytes]:
        process = mock.create_autospec(subprocess.Popen, instance=True)
        process.args = ("driver",)
        process.pid = 123
        process.returncode = None
        process.wait.return_value = 0
        process.stdin = mock.Mock(closed=False)
        process.stdout = mock.Mock(closed=False)
        process.stderr = None
        process.stdin.close.side_effect = lambda: setattr(process.stdin, "closed", True)
        process.stdout.close.side_effect = lambda: setattr(process.stdout, "closed", True)
        return cast(subprocess.Popen[bytes], process)

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

    def _wait_for_pid_publication(
        self,
        pid_path: Path,
        process: subprocess.Popen[str],
    ) -> tuple[int, int]:
        for _ in range(500):
            if pid_path.exists():
                pid = int(pid_path.read_text(encoding="ascii"))
                return pid, os.getpgid(pid)
            if process.poll() is not None:
                break
            time.sleep(0.01)
        self.fail("driver PID was not published")

    def _wait_for_path(self, path: Path, process: subprocess.Popen[str]) -> None:
        for _ in range(500):
            if path.exists():
                return
            if process.poll() is not None:
                break
            time.sleep(0.01)
        self.fail("expected subprocess marker was not published")

    def _cleanup_owned_process_group(
        self,
        process: subprocess.Popen[Any],
    ) -> None:
        """Clean up only a live, unreaped Popen that anchors its own group."""
        if process.returncode is None:
            try:
                anchor_unreaped = process.poll() is None
            except (ChildProcessError, OSError, ValueError):
                anchor_unreaped = False
            group_signaled = False
            if anchor_unreaped:
                try:
                    process_group_id = os.getpgid(process.pid)
                    if process_group_id == process.pid:
                        os.killpg(process_group_id, signal.SIGKILL)
                        group_signaled = True
                except (PermissionError, ProcessLookupError):
                    pass
                if not group_signaled:
                    try:
                        process.kill()
                    except (OSError, ValueError):
                        pass
            try:
                process.wait(timeout=1)
            except (ChildProcessError, OSError, ValueError, subprocess.TimeoutExpired):
                pass
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

    def _cleanup_test_process(self, process: subprocess.Popen[Any]) -> None:
        try:
            if process.returncode is None:
                process.kill()
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        try:
            process.communicate(timeout=1)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass

    def _wait_for_fixture_lock_release(
        self,
        lock_path: Path,
        *,
        allow_spawn_window: bool,
    ) -> None:
        deadline = time.monotonic() + 1
        absence_deadline = time.monotonic() + (0.1 if allow_spawn_window else 0)
        while True:
            try:
                lock_file = lock_path.open("rb")
            except FileNotFoundError:
                if time.monotonic() >= absence_deadline:
                    return
            else:
                with lock_file:
                    try:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        pass
                    else:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                        return
            if time.monotonic() >= deadline:
                self.fail("nested fixture retained its liveness lock")
            time.sleep(0.005)

    def _cleanup_nested_fixture_process(
        self,
        process: subprocess.Popen[Any],
        lock_path: Path,
        stop_path: Path,
    ) -> None:
        try:
            stop_path.write_text("stop", encoding="ascii")
            self._wait_for_fixture_lock_release(lock_path, allow_spawn_window=False)
        finally:
            try:
                self._cleanup_test_process(process)
            finally:
                self._wait_for_fixture_lock_release(lock_path, allow_spawn_window=True)

    def test_cleanup_closes_streams_without_signaling_reaped_anchor(self) -> None:
        anchor = mock.create_autospec(subprocess.Popen, instance=True)
        anchor.pid = 123
        anchor.returncode = 0
        anchor.poll.return_value = 0
        anchor.stdin = mock.Mock(closed=False)
        anchor.stdout = mock.Mock(closed=False)
        anchor.stderr = mock.Mock(closed=False)

        with (
            mock.patch.object(os, "getpgid") as getpgid,
            mock.patch.object(os, "killpg") as killpg,
            mock.patch.object(os, "kill") as kill,
        ):
            self._cleanup_owned_process_group(anchor)

        getpgid.assert_not_called()
        killpg.assert_not_called()
        kill.assert_not_called()
        anchor.poll.assert_not_called()
        anchor.kill.assert_not_called()
        anchor.wait.assert_not_called()
        anchor.stdin.close.assert_called_once_with()
        anchor.stdout.close.assert_called_once_with()
        anchor.stderr.close.assert_called_once_with()

    def test_cleanup_test_process_reaps_runner_and_closes_streams(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self._cleanup_test_process(process)

            self.assertIsNotNone(process.returncode)
            assert process.stdout is not None
            assert process.stderr is not None
            self.assertTrue(process.stdout.closed)
            self.assertTrue(process.stderr.closed)
        finally:
            if process.returncode is None:
                process.kill()
                process.communicate(timeout=1)

    def test_cleanup_owned_process_group_kills_nonleader_child_before_wait(self) -> None:
        anchor = mock.create_autospec(subprocess.Popen, instance=True)
        anchor.pid = 123
        anchor.returncode = None
        anchor.poll.return_value = None
        events: list[str] = []
        anchor.stdin = mock.Mock(closed=True)
        anchor.stdout = mock.Mock(closed=True)
        anchor.stderr = mock.Mock(closed=True)
        anchor.kill.side_effect = lambda: events.append("kill")
        anchor.wait.side_effect = lambda **_: events.append("wait")

        with (
            mock.patch.object(os, "getpgid", return_value=456) as getpgid,
            mock.patch.object(os, "killpg") as killpg,
            mock.patch.object(os, "kill") as kill,
        ):
            self._cleanup_owned_process_group(anchor)

        self.assertEqual(events, ["kill", "wait"])
        getpgid.assert_called_once_with(anchor.pid)
        killpg.assert_not_called()
        kill.assert_not_called()
        anchor.poll.assert_called_once_with()
        anchor.wait.assert_called_once_with(timeout=1)
        anchor.kill.assert_called_once_with()

    def test_cleanup_owned_process_group_kills_anchored_child_when_group_lookup_fails(
        self,
    ) -> None:
        anchor = mock.create_autospec(subprocess.Popen, instance=True)
        anchor.pid = 123
        anchor.returncode = None
        anchor.poll.return_value = None
        events: list[str] = []
        anchor.stdin = mock.Mock(closed=True)
        anchor.stdout = mock.Mock(closed=True)
        anchor.stderr = mock.Mock(closed=True)
        anchor.kill.side_effect = lambda: events.append("kill")
        anchor.wait.side_effect = lambda **_: events.append("wait")

        with (
            mock.patch.object(os, "getpgid", side_effect=ProcessLookupError) as getpgid,
            mock.patch.object(os, "killpg") as killpg,
            mock.patch.object(os, "kill") as kill,
        ):
            self._cleanup_owned_process_group(anchor)

        self.assertEqual(events, ["kill", "wait"])
        getpgid.assert_called_once_with(anchor.pid)
        killpg.assert_not_called()
        kill.assert_not_called()
        anchor.poll.assert_called_once_with()
        anchor.wait.assert_called_once_with(timeout=1)
        anchor.kill.assert_called_once_with()

    def test_cleanup_poll_reaps_dead_anchor_without_os_probe_or_signal(self) -> None:
        anchor = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        try:
            assert anchor.stdout is not None
            self.assertEqual(anchor.stdout.read(), b"")
            self.assertIsNone(anchor.returncode)

            with (
                mock.patch.object(
                    os,
                    "getpgid",
                    side_effect=AssertionError("released PID was probed"),
                ) as getpgid,
                mock.patch.object(
                    os,
                    "kill",
                    side_effect=AssertionError("released PID was signaled"),
                ) as raw_kill,
            ):
                self._cleanup_owned_process_group(anchor)

            getpgid.assert_not_called()
            raw_kill.assert_not_called()
            self.assertEqual(anchor.returncode, 0)
            self.assertTrue(anchor.stdout.closed)
        finally:
            self._cleanup_owned_process_group(anchor)

    def test_preexisting_stop_marker_ends_locked_hanging_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            workspace_path = directory / "workspace"
            pid_path = directory / "driver.pid"
            lock_path = directory / "driver.lock"
            stop_path = directory / "driver.stop"
            workspace_path.mkdir(mode=0o700)
            _write_executable(runtime_path, b"unqualified-test-runtime\n")
            stop_path.write_text("stop", encoding="ascii")
            environment = os.environ.copy()
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_MODE"] = "hang"
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_TARGET"] = CONFORMANCE_CASES[0].case_id
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_PID_PATH"] = str(pid_path)
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_LOCK_PATH"] = str(lock_path)
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_STOP_PATH"] = str(stop_path)
            process = subprocess.Popen(
                [str(FAKE_DRIVER), "--weightclass-conformance-driver", "1"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=workspace_path,
                env=environment,
                start_new_session=True,
                text=True,
            )
            request = json.dumps(
                {
                    "case": CONFORMANCE_CASES[0].case,
                    "case_id": CONFORMANCE_CASES[0].case_id,
                    "driver_protocol_version": 1,
                    "runtime_path": str(runtime_path),
                    "workspace_path": str(workspace_path),
                }
            )
            try:
                try:
                    stdout, stderr = process.communicate(request, timeout=0.5)
                except subprocess.TimeoutExpired:
                    self.fail("preexisting stop marker did not stop the fixture")
                self.assertEqual(process.returncode, 0, stderr)
                self.assertIn('"passed":true', stdout)
            finally:
                self._cleanup_owned_process_group(process)

            self.assertTrue(lock_path.is_file())
            with lock_path.open("rb") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def test_cleanup_owned_process_group_signals_live_anchor_before_wait(self) -> None:
        anchor = mock.create_autospec(subprocess.Popen, instance=True)
        anchor.pid = 123
        anchor.returncode = None
        anchor.poll.return_value = None
        events: list[str] = []
        anchor.stdin = mock.Mock(closed=False)
        anchor.stdout = mock.Mock(closed=False)
        anchor.stderr = mock.Mock(closed=False)
        anchor.wait.side_effect = lambda **_: events.append("wait")
        anchor.stdin.close.side_effect = lambda: events.append("stdin.close")
        anchor.stdout.close.side_effect = lambda: events.append("stdout.close")
        anchor.stderr.close.side_effect = lambda: events.append("stderr.close")

        with (
            mock.patch.object(os, "getpgid", return_value=anchor.pid) as getpgid,
            mock.patch.object(
                os,
                "killpg",
                side_effect=lambda *_: events.append("killpg"),
            ) as killpg,
            mock.patch.object(os, "kill") as kill,
        ):
            self._cleanup_owned_process_group(anchor)

        self.assertEqual(events, ["killpg", "wait", "stdin.close", "stdout.close", "stderr.close"])
        getpgid.assert_called_once_with(anchor.pid)
        killpg.assert_called_once_with(anchor.pid, signal.SIGKILL)
        kill.assert_not_called()
        anchor.poll.assert_called_once_with()
        anchor.wait.assert_called_once_with(timeout=1)
        anchor.kill.assert_not_called()

    def _start_signal_case(
        self,
        directory: Path,
        *,
        handler_mode: str,
        delayed_cleanup: bool = False,
    ) -> tuple[subprocess.Popen[str], Path, Path, Path, Path, Path]:
        runtime_path = directory / "runtime"
        workspace_path = directory / "workspace"
        pid_path = directory / "driver.pid"
        handler_path = directory / "handler.log"
        cleanup_path = directory / ("cleanup.started" if delayed_cleanup else "-")
        cleanup_observation_path = directory / "cleanup.observation.json"
        lock_path = directory / "driver.lock"
        stop_path = directory / "driver.stop"
        workspace_path.mkdir(mode=0o700)
        _write_executable(runtime_path, b"unqualified-test-runtime\n")
        environment = os.environ.copy()
        environment["WEIGHTCLASS_FAKE_CONFORMANCE_MODE"] = "hang"
        environment["WEIGHTCLASS_FAKE_CONFORMANCE_TARGET"] = CONFORMANCE_CASES[0].case_id
        environment["WEIGHTCLASS_FAKE_CONFORMANCE_PID_PATH"] = str(pid_path)
        environment["WEIGHTCLASS_FAKE_CONFORMANCE_LOCK_PATH"] = str(lock_path)
        environment["WEIGHTCLASS_FAKE_CONFORMANCE_STOP_PATH"] = str(stop_path)
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _SIGNAL_CASE_RUNNER,
                str(FAKE_DRIVER),
                str(runtime_path),
                str(workspace_path),
                handler_mode,
                str(handler_path),
                str(cleanup_path),
                str(cleanup_observation_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=environment,
            text=True,
        )
        return (
            process,
            pid_path,
            cleanup_path,
            cleanup_observation_path,
            lock_path,
            stop_path,
        )

    def _assert_production_cleanup_observed(self, observation_path: Path) -> None:
        self.assertTrue(observation_path.is_file(), "cleanup observation was not published")
        self.assertEqual(
            json.loads(observation_path.read_text(encoding="ascii")),
            {
                "deferred_signal_target_cleared_before_reap": True,
                "group_gone_before_reap": True,
                "leader_unreaped_before_reap": True,
                "reader_signal_target_cleared_before_reap": True,
                "reap_calls": 1,
                "reaped_after_cleanup": True,
                "streams_closed_before_reap": True,
                "target_cleared_after_reap": True,
            },
        )

    def test_nested_fixture_finalizer_releases_lock_when_body_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            (
                process,
                pid_path,
                _,
                _,
                lock_path,
                stop_path,
            ) = self._start_signal_case(directory, handler_mode="callable")

            with self.assertRaisesRegex(AssertionError, "forced body failure"):
                try:
                    self._wait_for_pid_publication(pid_path, process)
                    self._wait_for_path(lock_path, process)
                    raise AssertionError("forced body failure")
                finally:
                    self._cleanup_nested_fixture_process(process, lock_path, stop_path)

            self.assertIsNotNone(process.returncode)
            with lock_path.open("rb") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def test_nested_fixture_finalizer_handles_stop_before_driver_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            (
                process,
                _,
                _,
                _,
                lock_path,
                stop_path,
            ) = self._start_signal_case(
                Path(temporary_directory),
                handler_mode="callable",
            )

            self._cleanup_nested_fixture_process(process, lock_path, stop_path)

            self.assertIsNotNone(process.returncode)
            if lock_path.exists():
                with lock_path.open("rb") as lock_file:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _arguments(self, runtime_path: Path) -> list[str]:
        return [
            sys.executable,
            "-m",
            "weightclass.delegation_conformance",
            "--driver",
            str(FAKE_DRIVER),
            "--runtime",
            str(runtime_path),
            "--runtime-build-id",
            "opaque-runtime-build",
            "--adapter-id",
            "claude-native-v1",
            "--vendor-family",
            "claude",
        ]

    def _run_with_mode(
        self,
        directory: Path,
        *,
        mode: str,
        target: str,
        pid_path: Path | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        runtime_path = directory / "runtime"
        _write_executable(runtime_path, b"unqualified-test-runtime\n")
        environment = os.environ.copy()
        environment["WEIGHTCLASS_FAKE_CONFORMANCE_MODE"] = mode
        environment["WEIGHTCLASS_FAKE_CONFORMANCE_TARGET"] = target
        if pid_path is not None:
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_PID_PATH"] = str(pid_path)
        result = subprocess.run(
            self._arguments(runtime_path),
            capture_output=True,
            check=False,
            env=environment,
            text=True,
            timeout=15,
        )
        return result, runtime_path

    def _run_indistinguishability_mode(
        self,
        mode: str,
    ) -> tuple[dict[str, object], dict[str, object], bool]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            _write_executable(runtime_path, FIXED_SENTINEL_RUNTIME.read_bytes())
            environment = os.environ.copy()
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_MODE"] = mode
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_TARGET"] = (
                "matrix/orchestrator/implementation/workspace_read/allow"
            )
            result = subprocess.run(
                self._arguments(runtime_path),
                capture_output=True,
                check=False,
                env=environment,
                text=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            parsed: object = json.loads(result.stdout)
            self.assertIsInstance(parsed, dict)
            evidence = cast(dict[str, object], parsed)
            candidate = build_qualification_candidate(evidence, runtime_path)
            marker_present = runtime_path.with_suffix(".marker").is_file()
        return evidence, candidate, marker_present

    def test_v2_cannot_distinguish_runtime_invocation_skip_forgery_or_self_attestation(
        self,
    ) -> None:
        """Breaks if this known v2 observability gap is hidden or misstated."""
        self.assertTrue(FIXED_SENTINEL_RUNTIME.is_file())
        modes = (
            ("invoke-runtime", True),
            ("skip-runtime", False),
            ("forge-runtime-marker", True),
            ("self-attest", False),
        )
        evidence_documents: list[dict[str, object]] = []
        candidates: list[dict[str, object]] = []
        marker_observations: list[bool] = []

        for mode, _ in modes:
            evidence, candidate, marker_present = self._run_indistinguishability_mode(mode)
            evidence_documents.append(evidence)
            candidates.append(candidate)
            marker_observations.append(marker_present)

        self.assertEqual(marker_observations, [expected for _, expected in modes])
        self.assertTrue(all(evidence == evidence_documents[0] for evidence in evidence_documents))
        self.assertTrue(all(candidate == candidates[0] for candidate in candidates))
        self.assertEqual(load_packaged_qualification_registry().records, ())

    @guarded_launch("conformance")
    def test_full_run_emits_candidate_compatible_evidence_without_reading_stdin(self) -> None:
        """Breaks if the runner skips cases, consumes a task, or changes the evidence schema."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            _write_executable(runtime_path, b"unqualified-test-runtime\n")
            process = subprocess.Popen(
                self._arguments(runtime_path),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                return_code = process.wait(timeout=15)
                stdout, stderr = process.communicate(timeout=1)
            finally:
                self._cleanup_test_process(process)

            self.assertEqual(return_code, 0, stderr)
            self.assertEqual(stderr, "")
            evidence = json.loads(stdout)
            candidate = build_qualification_candidate(evidence, runtime_path)

        result_matrix = evidence["result_matrix"]
        scenario_results = evidence["scenario_results"]
        self.assertEqual(len(result_matrix), 54)
        self.assertEqual(len(scenario_results), 13)
        self.assertTrue(all(result["passed"] is True for result in result_matrix))
        self.assertTrue(all(result["passed"] is True for result in scenario_results))
        self.assertEqual(candidate["runtime_build_id"], "opaque-runtime-build")
        self.assertEqual(load_packaged_qualification_registry().records, ())

    def test_failed_case_produces_complete_rejected_evidence(self) -> None:
        """Breaks if one driver failure is hidden or converted into a candidate."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            result, runtime_path = self._run_with_mode(
                Path(temporary_directory),
                mode="fail",
                target="scenario/stage_order",
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr, '{"error": "conformance_failed"}\n')
            evidence = json.loads(result.stdout)

            with self.assertRaises(QualificationInvalidInputError):
                build_qualification_candidate(evidence, runtime_path)

        self.assertEqual(len(evidence["result_matrix"]), 54)
        self.assertEqual(len(evidence["scenario_results"]), 13)
        failed = [item for item in evidence["scenario_results"] if item["passed"] is False]
        self.assertEqual(failed, [{"id": "stage_order", "passed": False}])

    def test_evidence_is_bound_to_runtime_bytes_observed_by_runner(self) -> None:
        """Breaks if a different post-suite artifact can inherit passing evidence."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            result, runtime_path = self._run_with_mode(
                Path(temporary_directory),
                mode="pass",
                target="",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            evidence = json.loads(result.stdout)
            original_contents = runtime_path.read_bytes()
            runtime_path.write_bytes(b"U" + original_contents[1:])

            with self.assertRaises(QualificationInvalidInputError):
                build_qualification_candidate(evidence, runtime_path)

        self.assertEqual(evidence["artifact_size_bytes"], len(original_contents))
        self.assertEqual(
            evidence["artifact_sha256"],
            hashlib.sha256(original_contents).hexdigest(),
        )

    def test_spoofed_case_id_and_oversized_valid_json_fail_closed(self) -> None:
        """Breaks if a driver can answer another case or exceed the output contract."""
        for mode, target in (
            ("spoof", "scenario/reviewer_rejection"),
            ("oversized", "scenario/output_channel_separation"),
        ):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary_directory:
                result, _ = self._run_with_mode(
                    Path(temporary_directory),
                    mode=mode,
                    target=target,
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr, '{"error": "conformance_failed"}\n')
                evidence = json.loads(result.stdout)
                scenario_id = target.removeprefix("scenario/")
                failed = [
                    item for item in evidence["scenario_results"] if item["id"] == scenario_id
                ]
                self.assertEqual(failed, [{"id": scenario_id, "passed": False}])

    def test_deeply_nested_driver_response_is_redacted_failure(self) -> None:
        """Breaks if malformed JSON escapes the normal conformance failure path."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            result, _ = self._run_with_mode(
                Path(temporary_directory),
                mode="deep-response",
                target="scenario/output_channel_separation",
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr, '{"error": "conformance_failed"}\n')
            self.assertNotIn("Traceback", result.stderr)
            evidence = json.loads(result.stdout)

        failed = [
            item
            for item in evidence["scenario_results"]
            if item["id"] == "output_channel_separation"
        ]
        self.assertEqual(failed, [{"id": "output_channel_separation", "passed": False}])

    def test_runtime_mutation_during_suite_fails_artifact_integrity_scenario(self) -> None:
        """Breaks if the runner does not recheck the artifact after all driver cases."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            result, _ = self._run_with_mode(
                Path(temporary_directory),
                mode="mutate-runtime",
                target="scenario/stage_order",
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr, '{"error": "conformance_failed"}\n')
            evidence = json.loads(result.stdout)

        failed = [item for item in evidence["scenario_results"] if item["passed"] is False]
        self.assertEqual(
            failed,
            [{"id": "artifact_integrity_and_substitution", "passed": False}],
        )

    def test_descendant_leak_is_observed_failed_and_process_group_is_cleaned(self) -> None:
        """Breaks if driver self-report hides a live descendant or cleanup leaves it running."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            pid_path = directory / "descendant.pid"
            _write_executable(runtime_path, b"unqualified-test-runtime\n")
            environment = os.environ.copy()
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_MODE"] = "leak"
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_TARGET"] = "scenario/descendant_leakage"
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_PID_PATH"] = str(pid_path)
            real_popen = cast(Any, subprocess.Popen)
            from weightclass import delegation_conformance as conformance_module

            real_group_exists = conformance_module._process_group_exists
            spawned: list[tuple[subprocess.Popen[bytes], int]] = []
            anchors_by_group: dict[int, subprocess.Popen[bytes]] = {}
            observed_descendants: list[int] = []
            target_group_id: int | None = None
            group_observations: list[tuple[bool, int | None]] = []

            def capture_process(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
                process = cast(subprocess.Popen[bytes], real_popen(*args, **kwargs))
                process_group_id = os.getpgid(process.pid)
                spawned.append((process, process_group_id))
                anchors_by_group[process_group_id] = process
                return process

            def observe_group(process_group_id: int) -> bool:
                nonlocal target_group_id
                group_exists = real_group_exists(process_group_id)
                if pid_path.exists() and target_group_id is None:
                    descendant_pid = int(pid_path.read_text(encoding="ascii"))
                    observed_descendants.append(descendant_pid)
                    target_group_id = process_group_id
                if target_group_id == process_group_id:
                    group_observations.append(
                        (group_exists, anchors_by_group[process_group_id].returncode)
                    )
                return group_exists

            try:
                with (
                    mock.patch.dict(os.environ, environment, clear=True),
                    mock.patch(
                        "weightclass.delegation_conformance.subprocess.Popen",
                        side_effect=capture_process,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._process_group_exists",
                        side_effect=observe_group,
                    ),
                ):
                    evidence, passed = run_conformance(
                        FAKE_DRIVER,
                        runtime_path,
                        runtime_build_id="opaque-runtime-build",
                        adapter_id="claude-native-v1",
                        vendor_family="claude",
                    )

                self.assertFalse(passed)
                self.assertEqual(len(observed_descendants), 1)
                self.assertGreater(observed_descendants[0], 0)
                self.assertIsNotNone(target_group_id)
                self.assertTrue(group_observations)
                self.assertTrue(all(returncode is None for _, returncode in group_observations))
                self.assertIn((True, None), group_observations)
                self.assertEqual(group_observations[-1], (False, None))
                scenario_results = evidence["scenario_results"]
                assert isinstance(scenario_results, list)
                failed = [item for item in scenario_results if item["id"] == "descendant_leakage"]
                self.assertEqual(failed, [{"id": "descendant_leakage", "passed": False}])
            finally:
                for process, _ in spawned:
                    self._cleanup_owned_process_group(process)

    def test_hanging_driver_times_out_one_case_and_is_reaped(self) -> None:
        """Breaks if one stuck driver blocks the next case or survives its deadline."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            pid_path = directory / "driver.pid"
            _write_executable(runtime_path, b"unqualified-test-runtime\n")
            environment = os.environ.copy()
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_MODE"] = "hang"
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_TARGET"] = "scenario/runtime_deadline"
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_PID_PATH"] = str(pid_path)
            selected_case_ids = (
                CONFORMANCE_CASES[0].case_id,
                "scenario/runtime_deadline",
                "scenario/stage_order",
            )
            selected_cases = tuple(
                conformance_case
                for conformance_case in CONFORMANCE_CASES
                if conformance_case.case_id in selected_case_ids
            )
            self.assertEqual(
                tuple(conformance_case.case_id for conformance_case in selected_cases),
                selected_case_ids,
            )
            real_popen = cast(Any, subprocess.Popen)
            spawned: dict[int, tuple[subprocess.Popen[bytes], int]] = {}
            group_observations: list[tuple[int, bool, int | None]] = []
            from weightclass import delegation_conformance as conformance_module

            real_group_exists = conformance_module._process_group_exists
            real_signal_group = conformance_module._signal_process_group

            def capture_process(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
                process = cast(subprocess.Popen[bytes], real_popen(*args, **kwargs))
                spawned[process.pid] = (process, os.getpgid(process.pid))
                return process

            def observe_group(process_group_id: int) -> bool:
                group_exists = real_group_exists(process_group_id)
                process = spawned[process_group_id][0]
                group_observations.append((process_group_id, group_exists, process.returncode))
                return group_exists

            def observe_signal(process_group_id: int, signal_number: int) -> None:
                process = spawned[process_group_id][0]
                group_observations.append(
                    (process_group_id, real_group_exists(process_group_id), process.returncode)
                )
                real_signal_group(process_group_id, signal_number)

            def run_with_target_timeout(
                driver_path: Path,
                selected_runtime_path: Path,
                conformance_case: ConformanceCase,
                workspace_path: Path,
                *,
                timeout_seconds: float,
            ) -> bool:
                if conformance_case.case_id == "scenario/runtime_deadline":
                    timeout_seconds = 0.1
                return _run_driver_case(
                    driver_path,
                    selected_runtime_path,
                    conformance_case,
                    workspace_path,
                    timeout_seconds=timeout_seconds,
                )

            driver_pid: int | None = None
            driver_process_group_id: int | None = None
            try:
                started_at = time.monotonic()
                with (
                    mock.patch.dict(os.environ, environment, clear=True),
                    mock.patch(
                        "weightclass.delegation_conformance.subprocess.Popen",
                        side_effect=capture_process,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._run_driver_case",
                        side_effect=run_with_target_timeout,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance.CONFORMANCE_CASES",
                        selected_cases,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._process_group_exists",
                        side_effect=observe_group,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._signal_process_group",
                        side_effect=observe_signal,
                    ),
                ):
                    evidence, passed = run_conformance(
                        FAKE_DRIVER,
                        runtime_path,
                        runtime_build_id="opaque-runtime-build",
                        adapter_id="claude-native-v1",
                        vendor_family="claude",
                    )
                elapsed = time.monotonic() - started_at
                driver_pid = int(pid_path.read_text(encoding="ascii"))
                driver_process, driver_process_group_id = spawned[driver_pid]

                self.assertEqual(driver_process_group_id, driver_pid)
                driver_group_observations = [
                    (group_exists, returncode)
                    for group_id, group_exists, returncode in group_observations
                    if group_id == driver_pid
                ]
                self.assertTrue(driver_group_observations)
                self.assertTrue(
                    all(returncode is None for _, returncode in driver_group_observations)
                )
                self.assertIn((True, None), driver_group_observations)
                self.assertEqual(driver_group_observations[-1], (False, None))
                self.assertIsNotNone(driver_process.returncode)
                self.assertFalse(passed)
                self.assertLess(elapsed, 5)
                result_matrix = evidence["result_matrix"]
                scenario_results = evidence["scenario_results"]
                assert isinstance(result_matrix, list)
                assert isinstance(scenario_results, list)
                self.assertEqual(len(result_matrix), 1)
                self.assertTrue(
                    all(isinstance(item, dict) and item["passed"] is True for item in result_matrix)
                )
                self.assertEqual(
                    scenario_results,
                    [
                        {"id": "runtime_deadline", "passed": False},
                        {"id": "stage_order", "passed": True},
                    ],
                )
            finally:
                for process, _ in spawned.values():
                    self._cleanup_owned_process_group(process)

    def test_interrupt_during_thread_start_reaps_owned_driver_group(self) -> None:
        """Breaks if interruption before wait ownership starts abandons the driver."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            workspace_path = directory / "workspace"
            workspace_path.mkdir(mode=0o700)
            _write_executable(runtime_path, b"unqualified-test-runtime\n")
            real_popen = cast(Any, subprocess.Popen)
            spawned: list[tuple[subprocess.Popen[bytes], int]] = []
            cleanup_observations: list[tuple[bool, bool, bool, int | None]] = []
            from weightclass import delegation_conformance as conformance_module

            real_group_exists = conformance_module._process_group_exists
            real_wait_after_kill = conformance_module._wait_after_kill

            def capture_process(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
                process = cast(subprocess.Popen[bytes], real_popen(*args, **kwargs))
                spawned.append((process, os.getpgid(process.pid)))
                return process

            def observe_final_reap(process: subprocess.Popen[bytes]) -> int:
                spawned_process, process_group_id = spawned[0]
                assert process is spawned_process
                assert process.stdin is not None
                assert process.stdout is not None
                cleanup_observations.append(
                    (
                        real_group_exists(process_group_id),
                        process.stdin.closed,
                        process.stdout.closed,
                        process.returncode,
                    )
                )
                return real_wait_after_kill(process)

            try:
                with (
                    mock.patch(
                        "weightclass.delegation_conformance.subprocess.Popen",
                        side_effect=capture_process,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance.threading.Thread.start",
                        side_effect=KeyboardInterrupt,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._wait_after_kill",
                        side_effect=observe_final_reap,
                    ),
                    self.assertRaises(KeyboardInterrupt),
                ):
                    _run_driver_case(
                        FAKE_DRIVER,
                        runtime_path,
                        CONFORMANCE_CASES[0],
                        workspace_path,
                        timeout_seconds=5,
                    )

                self.assertEqual(len(spawned), 1)
                process, process_group_id = spawned[0]
                self.assertEqual(process_group_id, process.pid)
                self.assertEqual(cleanup_observations, [(False, True, True, None)])
                self.assertIsNotNone(process.returncode)
            finally:
                for process, _ in spawned:
                    self._cleanup_owned_process_group(process)

    def test_sigint_during_spawn_is_deferred_until_driver_group_is_owned(self) -> None:
        """Breaks if SIGINT can interrupt Popen before the new session is recorded."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            workspace_path = directory / "workspace"
            workspace_path.mkdir(mode=0o700)
            _write_executable(runtime_path, b"unqualified-test-runtime\n")
            real_popen = cast(Any, subprocess.Popen)
            spawned: list[tuple[subprocess.Popen[bytes], int]] = []
            cleanup_observations: list[tuple[bool, bool, bool, int | None]] = []
            from weightclass import delegation_conformance as conformance_module

            real_group_exists = conformance_module._process_group_exists
            real_wait_after_kill = conformance_module._wait_after_kill

            def interrupt_spawn(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
                process = cast(subprocess.Popen[bytes], real_popen(*args, **kwargs))
                spawned.append((process, os.getpgid(process.pid)))
                os.kill(os.getpid(), signal.SIGINT)
                return process

            def observe_final_reap(process: subprocess.Popen[bytes]) -> int:
                spawned_process, process_group_id = spawned[0]
                assert process is spawned_process
                assert process.stdin is not None
                assert process.stdout is not None
                cleanup_observations.append(
                    (
                        real_group_exists(process_group_id),
                        process.stdin.closed,
                        process.stdout.closed,
                        process.returncode,
                    )
                )
                return real_wait_after_kill(process)

            try:
                with (
                    mock.patch(
                        "weightclass.delegation_conformance.subprocess.Popen",
                        side_effect=interrupt_spawn,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._wait_after_kill",
                        side_effect=observe_final_reap,
                    ),
                    self.assertRaises(KeyboardInterrupt),
                ):
                    _run_driver_case(
                        FAKE_DRIVER,
                        runtime_path,
                        CONFORMANCE_CASES[0],
                        workspace_path,
                        timeout_seconds=5,
                    )

                self.assertEqual(len(spawned), 1)
                process, process_group_id = spawned[0]
                self.assertEqual(process_group_id, process.pid)
                self.assertEqual(cleanup_observations, [(False, True, True, None)])
                self.assertIsNotNone(process.returncode)
            finally:
                for process, _ in spawned:
                    self._cleanup_owned_process_group(process)

    @unittest.skipUnless(hasattr(signal, "SIGCHLD"), "requires SIGCHLD")
    def test_spawn_adjacent_sigchld_check_follows_sigint_arming(self) -> None:
        """Breaks if SIGINT setup can invalidate child ownership before spawn."""
        original_arm = _DeferredSigint.arm

        def arm_then_enable_auto_reap(deferred_sigint: _DeferredSigint) -> None:
            original_arm(deferred_sigint)
            signal.signal(signal.SIGCHLD, signal.SIG_IGN)

        previous_sigchld = signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        previous_sigint = signal.getsignal(signal.SIGINT)
        try:
            with (
                mock.patch.object(
                    _DeferredSigint,
                    "arm",
                    arm_then_enable_auto_reap,
                ),
                mock.patch(
                    "weightclass.delegation_conformance.subprocess.Popen",
                    side_effect=AssertionError("driver spawn must remain untouched"),
                ) as popen,
            ):
                passed = _run_driver_case(
                    FAKE_DRIVER,
                    FIXED_SENTINEL_RUNTIME,
                    CONFORMANCE_CASES[0],
                    Path.cwd(),
                    timeout_seconds=0.1,
                )

            self.assertFalse(passed)
            popen.assert_not_called()
            self.assertIs(signal.getsignal(signal.SIGINT), previous_sigint)
        finally:
            signal.signal(signal.SIGCHLD, previous_sigchld)
            signal.signal(signal.SIGINT, previous_sigint)

    def test_darwin_sigaction_inspection_failure_blocks_driver_spawn(self) -> None:
        """Breaks if the spawn-adjacent native inspection can fail open."""
        native_checks = 0

        class ReadableSigaction:
            @staticmethod
            def sigaction(_signal_number: int, _new_action: Any, current_action: Any) -> int:
                action = cast(Any, current_action)._obj
                action.handler = None
                action.flags = 0
                return 0

        def inspect_then_fail(*_args: Any, **_kwargs: Any) -> ReadableSigaction:
            nonlocal native_checks
            native_checks += 1
            if native_checks == 1:
                return ReadableSigaction()
            raise OSError()

        with (
            mock.patch("weightclass.process_context.sys.platform", "darwin"),
            mock.patch(
                "weightclass.process_context.ctypes.CDLL",
                side_effect=inspect_then_fail,
            ),
            mock.patch(
                "weightclass.delegation_conformance.subprocess.Popen",
                side_effect=AssertionError("driver spawn must remain untouched"),
            ) as popen,
        ):
            passed = _run_driver_case(
                FAKE_DRIVER,
                FIXED_SENTINEL_RUNTIME,
                CONFORMANCE_CASES[0],
                Path.cwd(),
                timeout_seconds=0.1,
            )

        self.assertFalse(passed)
        self.assertEqual(native_checks, 2)
        popen.assert_not_called()

    def test_darwin_sigaction_argument_error_fails_closed(self) -> None:
        """Breaks if an invalid native call escapes the shared safety gate."""
        from weightclass import process_context

        libc = mock.Mock()
        libc.sigaction.side_effect = ctypes.ArgumentError("invalid sigaction call")
        with (
            mock.patch("weightclass.process_context.sys.platform", "darwin"),
            mock.patch(
                "weightclass.process_context.signal.getsignal",
                return_value=signal.SIG_DFL,
            ),
            mock.patch(
                "weightclass.process_context.ctypes.CDLL",
                return_value=libc,
            ),
        ):
            self.assertFalse(process_context.has_safe_sigchld_disposition())

    def test_linux_glibc_sigaction_checks_native_no_child_wait_flag(self) -> None:
        """Breaks if Python-visible SIG_DFL hides glibc auto-reaping."""
        from weightclass import process_context

        for native_flags, expected_safe in ((0, True), (0x02, False)):
            with self.subTest(native_flags=native_flags):
                libc = self._mock_linux_glibc(native_flags=native_flags)
                with (
                    mock.patch(
                        "weightclass.process_context.sys.platform",
                        "linux",
                    ),
                    mock.patch(
                        "weightclass.process_context.signal.getsignal",
                        return_value=signal.SIG_DFL,
                    ),
                    mock.patch(
                        "weightclass.process_context.os.uname",
                        return_value=mock.Mock(machine="x86_64"),
                    ),
                    mock.patch(
                        "weightclass.process_context.ctypes.sizeof",
                        side_effect=self._ctypes_sizeof_with_pointer_size(8),
                    ),
                    mock.patch(
                        "weightclass.process_context.ctypes.CDLL",
                        return_value=libc,
                    ),
                ):
                    self.assertIs(
                        process_context.has_safe_sigchld_disposition(),
                        expected_safe,
                    )

                libc.sigaction.assert_called_once()
                libc.gnu_get_libc_version.assert_called_once_with()

    def test_linux_glibc_sigaction_layout_matches_reviewed_abi(self) -> None:
        """Breaks if the declared glibc layout drifts from the reviewed ABI."""
        from weightclass import process_context

        self.assertEqual(ctypes.sizeof(process_context._LinuxGlibcSigset), 128)
        self.assertEqual(process_context._LinuxGlibcSigaction.handler.offset, 0)
        self.assertEqual(process_context._LinuxGlibcSigaction.mask.offset, 8)
        self.assertEqual(process_context._LinuxGlibcSigaction.flags.offset, 136)
        self.assertEqual(process_context._LinuxGlibcSigaction.restorer.offset, 144)
        self.assertEqual(ctypes.sizeof(process_context._LinuxGlibcSigaction), 152)

    def test_linux_glibc_sigaction_layout_mismatch_fails_closed(self) -> None:
        """Breaks if Linux accepts a ctypes layout that was not reviewed."""
        from weightclass import process_context

        libc = self._mock_linux_glibc()
        real_sizeof = ctypes.sizeof

        def mismatched_layout_size(value: Any) -> int:
            if value is ctypes.c_void_p:
                return 8
            if value is process_context._LinuxGlibcSigaction:
                return 160
            return real_sizeof(value)

        with (
            mock.patch("weightclass.process_context.sys.platform", "linux"),
            mock.patch(
                "weightclass.process_context.signal.getsignal",
                return_value=signal.SIG_DFL,
            ),
            mock.patch(
                "weightclass.process_context.os.uname",
                return_value=mock.Mock(machine="x86_64"),
            ),
            mock.patch(
                "weightclass.process_context.ctypes.sizeof",
                side_effect=mismatched_layout_size,
            ),
            mock.patch(
                "weightclass.process_context.ctypes.CDLL",
                return_value=libc,
            ),
        ):
            self.assertFalse(process_context.has_safe_sigchld_disposition())

    def test_linux_glibc_version_call_must_return_nonempty_bytes(self) -> None:
        """Breaks if symbol presence alone is accepted as proof of glibc."""
        from weightclass import process_context

        cases = (None, b"", "2.36")
        for version in cases:
            libc = self._mock_linux_glibc(version=version)
            with (
                self.subTest(version=version),
                mock.patch("weightclass.process_context.sys.platform", "linux"),
                mock.patch(
                    "weightclass.process_context.signal.getsignal",
                    return_value=signal.SIG_DFL,
                ),
                mock.patch(
                    "weightclass.process_context.os.uname",
                    return_value=mock.Mock(machine="x86_64"),
                ),
                mock.patch(
                    "weightclass.process_context.ctypes.sizeof",
                    side_effect=self._ctypes_sizeof_with_pointer_size(8),
                ),
                mock.patch(
                    "weightclass.process_context.ctypes.CDLL",
                    return_value=libc,
                ),
            ):
                self.assertFalse(process_context.has_safe_sigchld_disposition())

            libc.gnu_get_libc_version.assert_called_once_with()

        failing_libc = self._mock_linux_glibc()
        failing_libc.gnu_get_libc_version.side_effect = OSError()
        with (
            mock.patch("weightclass.process_context.sys.platform", "linux"),
            mock.patch(
                "weightclass.process_context.signal.getsignal",
                return_value=signal.SIG_DFL,
            ),
            mock.patch(
                "weightclass.process_context.os.uname",
                return_value=mock.Mock(machine="x86_64"),
            ),
            mock.patch(
                "weightclass.process_context.ctypes.sizeof",
                side_effect=self._ctypes_sizeof_with_pointer_size(8),
            ),
            mock.patch(
                "weightclass.process_context.ctypes.CDLL",
                return_value=failing_libc,
            ),
        ):
            self.assertFalse(process_context.has_safe_sigchld_disposition())

    def test_unknown_posix_platform_sigchld_gate_fails_closed(self) -> None:
        """Breaks if an unsupported POSIX platform bypasses native inspection."""
        with (
            mock.patch(
                "weightclass.process_context.sys.platform",
                "freebsd14",
            ),
            mock.patch(
                "weightclass.process_context.signal.getsignal",
                return_value=signal.SIG_DFL,
            ),
            mock.patch("weightclass.process_context.ctypes.CDLL") as cdll,
        ):
            self.assertFalse(_has_safe_sigchld_disposition())

        cdll.assert_not_called()

    def test_linux_sigaction_unknown_abi_or_libc_fails_closed(self) -> None:
        """Breaks if an unreviewed Linux sigaction layout is treated as safe."""
        from weightclass import process_context

        cases = (
            ("i686", 8, self._mock_linux_glibc()),
            ("x86_64", 4, self._mock_linux_glibc()),
            ("x86_64", 8, object()),
        )
        for machine, pointer_size, libc in cases:
            with (
                self.subTest(machine=machine, pointer_size=pointer_size),
                mock.patch(
                    "weightclass.process_context.sys.platform",
                    "linux",
                ),
                mock.patch(
                    "weightclass.process_context.signal.getsignal",
                    return_value=signal.SIG_DFL,
                ),
                mock.patch(
                    "weightclass.process_context.os.uname",
                    return_value=mock.Mock(machine=machine),
                ),
                mock.patch(
                    "weightclass.process_context.ctypes.sizeof",
                    side_effect=self._ctypes_sizeof_with_pointer_size(pointer_size),
                ),
                mock.patch(
                    "weightclass.process_context.ctypes.CDLL",
                    return_value=libc,
                ),
            ):
                self.assertFalse(process_context.has_safe_sigchld_disposition())

    def test_linux_sigaction_inspection_failure_blocks_driver_spawn(self) -> None:
        """Breaks if the spawn-adjacent native inspection can fail open."""
        native_checks = 0
        libc = self._mock_linux_glibc()

        def inspect_then_fail(*_args: Any, **_kwargs: Any) -> Any:
            nonlocal native_checks
            native_checks += 1
            if native_checks == 1:
                return libc
            raise OSError()

        with (
            mock.patch("weightclass.process_context.sys.platform", "linux"),
            mock.patch(
                "weightclass.process_context.os.uname",
                return_value=mock.Mock(machine="x86_64"),
            ),
            mock.patch(
                "weightclass.process_context.ctypes.sizeof",
                side_effect=self._ctypes_sizeof_with_pointer_size(8),
            ),
            mock.patch(
                "weightclass.process_context.ctypes.CDLL",
                side_effect=inspect_then_fail,
            ),
            mock.patch(
                "weightclass.delegation_conformance.os.posix_spawn",
                return_value=321,
            ),
            mock.patch(
                "weightclass.delegation_conformance.os.waitpid",
                return_value=(321, 0),
            ),
            mock.patch(
                "weightclass.delegation_conformance.subprocess.Popen",
                side_effect=OSError(),
            ) as popen,
        ):
            passed = _run_driver_case(
                FAKE_DRIVER,
                FIXED_SENTINEL_RUNTIME,
                CONFORMANCE_CASES[0],
                Path.cwd(),
                timeout_seconds=0.1,
            )

        self.assertFalse(passed)
        self.assertEqual(native_checks, 2)
        popen.assert_not_called()

    def test_linux_supported_context_spawns_no_disposable_status_probe(self) -> None:
        """Breaks if conformance probes ownership by creating another child."""
        libc = self._mock_linux_glibc()
        with (
            mock.patch("weightclass.process_context.sys.platform", "linux"),
            mock.patch(
                "weightclass.process_context.os.uname",
                return_value=mock.Mock(machine="x86_64"),
            ),
            mock.patch(
                "weightclass.process_context.ctypes.sizeof",
                side_effect=self._ctypes_sizeof_with_pointer_size(8),
            ),
            mock.patch(
                "weightclass.process_context.ctypes.CDLL",
                return_value=libc,
            ),
            mock.patch(
                "weightclass.delegation_conformance.os.posix_spawn",
                side_effect=AssertionError("disposable probe child was spawned"),
            ) as posix_spawn,
            mock.patch(
                "weightclass.delegation_conformance.subprocess.Popen",
                side_effect=OSError(),
            ) as popen,
        ):
            passed = _run_driver_case(
                FAKE_DRIVER,
                FIXED_SENTINEL_RUNTIME,
                CONFORMANCE_CASES[0],
                Path.cwd(),
                timeout_seconds=0.1,
            )

        self.assertFalse(passed)
        posix_spawn.assert_not_called()
        popen.assert_called_once()

    def test_observer_echild_releases_every_numeric_signal_target(self) -> None:
        """Breaks if observer ECHILD leaves a reusable PID or PGID signalable."""
        process = self._mock_driver_process()

        with (
            mock.patch(
                "weightclass.delegation_conformance.subprocess.Popen",
                return_value=process,
            ),
            mock.patch("weightclass.delegation_conformance._open_leader_exit_queue"),
            mock.patch(
                "weightclass.delegation_conformance._wait_for_leader_exit",
                side_effect=ChildProcessError(),
            ),
            mock.patch("weightclass.delegation_conformance._write_request"),
            mock.patch("weightclass.delegation_conformance._read_response"),
            mock.patch("weightclass.delegation_conformance._signal_process_group") as killpg,
            mock.patch("weightclass.delegation_conformance.os.kill") as kill,
            mock.patch(
                "weightclass.delegation_conformance._process_group_exists",
                return_value=False,
            ) as probe,
            mock.patch("weightclass.delegation_conformance._wait_after_kill") as final_wait,
        ):
            passed = _run_driver_case(
                FAKE_DRIVER,
                FIXED_SENTINEL_RUNTIME,
                CONFORMANCE_CASES[0],
                Path.cwd(),
                timeout_seconds=0.1,
            )

        self.assertFalse(passed)
        killpg.assert_not_called()
        kill.assert_not_called()
        probe.assert_not_called()
        final_wait.assert_not_called()
        cast(Any, process.wait).assert_not_called()

    def test_kqueue_registration_esrch_preserves_status_until_final_reap(self) -> None:
        """Breaks if normal fast exit is mistaken for unavailable child status."""
        process = self._mock_driver_process()
        events: list[str] = []
        native_waitid = mock.Mock(return_value=0)
        libc = mock.Mock(waitid=native_waitid)
        exit_queue = mock.Mock()
        exit_queue.control.side_effect = ProcessLookupError(errno.ESRCH, "gone")

        def fail_write(_stream: Any, _request: bytes, state: Any) -> None:
            state.write_failed = True

        def record_group_probe(_process_group_id: int) -> bool:
            events.append("probe-group")
            return False

        def record_waitpid(process_id: int, flags: int) -> tuple[int, int]:
            self.assertEqual(process_id, process.pid)
            self.assertEqual(flags, 0)
            events.append("waitpid")
            return process.pid, 17 << 8

        with (
            mock.patch("weightclass.delegation_conformance.sys.platform", "darwin"),
            mock.patch.object(os, "waitid", None, create=True),
            mock.patch.multiple(
                "weightclass.process_context.select",
                KQ_FILTER_PROC=0,
                KQ_EV_ADD=0,
                KQ_EV_ONESHOT=0,
                KQ_NOTE_EXIT=0,
                create=True,
            ),
            mock.patch(
                "weightclass.process_context.select.kevent",
                return_value=object(),
                create=True,
            ),
            mock.patch(
                "weightclass.process_context.select.kqueue",
                return_value=exit_queue,
                create=True,
            ),
            mock.patch(
                "weightclass.delegation_conformance._has_safe_sigchld_disposition",
                return_value=True,
            ),
            mock.patch(
                "weightclass.process_context.ctypes.CDLL",
                return_value=libc,
            ),
            mock.patch(
                "weightclass.delegation_conformance.subprocess.Popen",
                return_value=process,
            ),
            mock.patch(
                "weightclass.delegation_conformance._write_request",
                side_effect=fail_write,
            ),
            mock.patch("weightclass.delegation_conformance._read_response"),
            mock.patch(
                "weightclass.delegation_conformance._signal_process_group",
                side_effect=lambda *_: events.append("signal-group"),
            ),
            mock.patch(
                "weightclass.delegation_conformance.os.kill",
                side_effect=lambda *_: events.append("signal-pid"),
            ),
            mock.patch(
                "weightclass.delegation_conformance._process_group_exists",
                side_effect=record_group_probe,
            ),
            mock.patch(
                "weightclass.delegation_conformance.os.waitpid",
                side_effect=record_waitpid,
            ) as waitpid,
        ):
            passed = _run_driver_case(
                FAKE_DRIVER,
                FIXED_SENTINEL_RUNTIME,
                CONFORMANCE_CASES[0],
                Path.cwd(),
                timeout_seconds=0.1,
            )

        self.assertFalse(passed)
        native_waitid.assert_called_once()
        native_waitid_arguments = native_waitid.call_args.args
        self.assertEqual(
            (
                native_waitid_arguments[0],
                native_waitid_arguments[1],
                native_waitid_arguments[3],
            ),
            (1, process.pid, 0x25),
        )
        exit_queue.close.assert_called_once_with()
        waitpid.assert_called_once_with(process.pid, 0)
        cast(Any, process.wait).assert_not_called()
        self.assertEqual(process.returncode, 17)
        wait_index = events.index("waitpid")
        self.assertEqual(
            events[:wait_index],
            ["probe-group", "signal-group", "signal-pid", "probe-group"],
        )
        self.assertFalse(
            any(
                event in {"signal-group", "signal-pid", "probe-group"}
                for event in events[wait_index + 1 :]
            ),
            events,
        )

    def test_kqueue_esrch_after_external_reap_releases_all_targets(self) -> None:
        """Breaks if kqueue ESRCH treats an already-reaped PID as an owned anchor."""
        from weightclass import delegation_conformance as conformance

        process = self._mock_driver_process()
        recorded_ownership: list[Any] = []
        original_record_process = conformance._DriverCaseOwnership.record_process
        native_waitid = mock.Mock()
        exit_queue = mock.Mock()
        exit_queue.control.side_effect = ProcessLookupError(errno.ESRCH, "gone")

        def report_echild(*_args: Any) -> int:
            ctypes.set_errno(errno.ECHILD)
            return -1

        native_waitid.side_effect = report_echild
        libc = mock.Mock(waitid=native_waitid)

        def record_ownership(ownership: Any, child: Any) -> None:
            original_record_process(ownership, child)
            recorded_ownership.append(ownership)

        with (
            mock.patch("weightclass.delegation_conformance.sys.platform", "darwin"),
            mock.patch.object(os, "waitid", None, create=True),
            mock.patch.multiple(
                "weightclass.process_context.select",
                KQ_FILTER_PROC=0,
                KQ_EV_ADD=0,
                KQ_EV_ONESHOT=0,
                KQ_NOTE_EXIT=0,
                create=True,
            ),
            mock.patch(
                "weightclass.process_context.select.kevent",
                return_value=object(),
                create=True,
            ),
            mock.patch(
                "weightclass.process_context.select.kqueue",
                return_value=exit_queue,
                create=True,
            ),
            mock.patch(
                "weightclass.delegation_conformance._has_safe_sigchld_disposition",
                return_value=True,
            ),
            mock.patch(
                "weightclass.process_context.ctypes.CDLL",
                return_value=libc,
            ),
            mock.patch(
                "weightclass.delegation_conformance.subprocess.Popen",
                return_value=process,
            ),
            mock.patch.object(
                conformance._DriverCaseOwnership,
                "record_process",
                autospec=True,
                side_effect=record_ownership,
            ),
            mock.patch("weightclass.delegation_conformance._write_request"),
            mock.patch("weightclass.delegation_conformance._read_response"),
            mock.patch("weightclass.delegation_conformance._signal_process_group") as killpg,
            mock.patch("weightclass.delegation_conformance.os.kill") as kill,
            mock.patch(
                "weightclass.delegation_conformance._process_group_exists",
                return_value=False,
            ) as probe,
            mock.patch("weightclass.delegation_conformance.os.waitpid") as waitpid,
            mock.patch("weightclass.delegation_conformance._wait_after_kill") as final_wait,
        ):
            passed = _run_driver_case(
                FAKE_DRIVER,
                FIXED_SENTINEL_RUNTIME,
                CONFORMANCE_CASES[0],
                Path.cwd(),
                timeout_seconds=0.1,
            )

        self.assertFalse(passed)
        native_waitid.assert_called_once()
        native_waitid_arguments = native_waitid.call_args.args
        self.assertEqual(
            (
                native_waitid_arguments[0],
                native_waitid_arguments[1],
                native_waitid_arguments[3],
            ),
            (1, process.pid, 0x25),
        )
        exit_queue.close.assert_called_once_with()
        killpg.assert_not_called()
        kill.assert_not_called()
        probe.assert_not_called()
        waitpid.assert_not_called()
        final_wait.assert_not_called()
        self.assertEqual(len(recorded_ownership), 1)
        ownership = recorded_ownership[0]
        self.assertTrue(ownership.child_status_lost)
        self.assertIsNone(ownership.process_group_id)
        reader_signal_target = ownership.reader_signal_target
        self.assertIsNotNone(reader_signal_target)
        assert reader_signal_target is not None
        self.assertIsNone(reader_signal_target._process_group_id)
        deferred_sigint = getattr(ownership.before_final_reap, "__self__", None)
        self.assertIsNotNone(deferred_sigint)
        assert deferred_sigint is not None
        self.assertIsNone(deferred_sigint.process_group_id)
        assert process.stdin is not None
        assert process.stdout is not None
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)
        cast(Any, process.wait).assert_not_called()

    def test_kqueue_esrch_native_argument_error_releases_all_targets(self) -> None:
        """Breaks if a malformed native waitid call can leave stale targets live."""
        from weightclass import delegation_conformance as conformance

        process = self._mock_driver_process()
        recorded_ownership: list[Any] = []
        original_record_process = conformance._DriverCaseOwnership.record_process
        native_waitid = mock.Mock(side_effect=ctypes.ArgumentError("invalid waitid call"))
        libc = mock.Mock(waitid=native_waitid)
        exit_queue = mock.Mock()
        exit_queue.control.side_effect = ProcessLookupError(errno.ESRCH, "gone")

        def record_ownership(ownership: Any, child: Any) -> None:
            original_record_process(ownership, child)
            recorded_ownership.append(ownership)

        escaped_error: ctypes.ArgumentError | None = None
        passed: bool | None = None
        with (
            mock.patch("weightclass.delegation_conformance.sys.platform", "darwin"),
            mock.patch.object(os, "waitid", None, create=True),
            mock.patch.multiple(
                "weightclass.process_context.select",
                KQ_FILTER_PROC=0,
                KQ_EV_ADD=0,
                KQ_EV_ONESHOT=0,
                KQ_NOTE_EXIT=0,
                create=True,
            ),
            mock.patch(
                "weightclass.process_context.select.kevent",
                return_value=object(),
                create=True,
            ),
            mock.patch(
                "weightclass.process_context.select.kqueue",
                return_value=exit_queue,
                create=True,
            ),
            mock.patch(
                "weightclass.delegation_conformance._has_safe_sigchld_disposition",
                return_value=True,
            ),
            mock.patch(
                "weightclass.process_context.ctypes.CDLL",
                return_value=libc,
            ),
            mock.patch(
                "weightclass.delegation_conformance.subprocess.Popen",
                return_value=process,
            ),
            mock.patch.object(
                conformance._DriverCaseOwnership,
                "record_process",
                autospec=True,
                side_effect=record_ownership,
            ),
            mock.patch("weightclass.delegation_conformance._write_request"),
            mock.patch("weightclass.delegation_conformance._read_response"),
            mock.patch("weightclass.delegation_conformance._signal_process_group") as killpg,
            mock.patch("weightclass.delegation_conformance.os.kill") as kill,
            mock.patch(
                "weightclass.delegation_conformance._process_group_exists",
                return_value=False,
            ) as probe,
            mock.patch("weightclass.delegation_conformance.os.waitpid") as waitpid,
            mock.patch("weightclass.delegation_conformance._wait_after_kill") as final_wait,
        ):
            try:
                passed = _run_driver_case(
                    FAKE_DRIVER,
                    FIXED_SENTINEL_RUNTIME,
                    CONFORMANCE_CASES[0],
                    Path.cwd(),
                    timeout_seconds=0.1,
                )
            except ctypes.ArgumentError as error:
                escaped_error = error

        self.assertIsNone(escaped_error)
        self.assertIs(passed, False)
        native_waitid.assert_called_once()
        exit_queue.close.assert_called_once_with()
        killpg.assert_not_called()
        kill.assert_not_called()
        probe.assert_not_called()
        waitpid.assert_not_called()
        final_wait.assert_not_called()
        self.assertEqual(len(recorded_ownership), 1)
        ownership = recorded_ownership[0]
        self.assertTrue(ownership.child_status_lost)
        self.assertIsNone(ownership.process_group_id)
        reader_signal_target = ownership.reader_signal_target
        self.assertIsNotNone(reader_signal_target)
        assert reader_signal_target is not None
        self.assertIsNone(reader_signal_target._process_group_id)
        deferred_sigint = getattr(ownership.before_final_reap, "__self__", None)
        self.assertIsNotNone(deferred_sigint)
        assert deferred_sigint is not None
        self.assertIsNone(deferred_sigint.process_group_id)
        assert process.stdin is not None
        assert process.stdout is not None
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)
        cast(Any, process.wait).assert_not_called()

    def test_kqueue_esrch_without_native_waitid_releases_all_targets(self) -> None:
        """Breaks if a missing Darwin ownership oracle retains stale targets."""
        process = self._mock_driver_process()

        with (
            mock.patch("weightclass.delegation_conformance.sys.platform", "darwin"),
            mock.patch.object(os, "waitid", None, create=True),
            mock.patch.multiple(
                "weightclass.process_context.select",
                KQ_FILTER_PROC=0,
                KQ_EV_ADD=0,
                KQ_EV_ONESHOT=0,
                KQ_NOTE_EXIT=0,
                create=True,
            ),
            mock.patch(
                "weightclass.process_context.select.kevent",
                side_effect=ProcessLookupError(errno.ESRCH, "gone"),
                create=True,
            ),
            mock.patch(
                "weightclass.delegation_conformance._has_safe_sigchld_disposition",
                return_value=True,
            ) as sigchld_checks,
            mock.patch(
                "weightclass.process_context.ctypes.CDLL",
                return_value=object(),
            ),
            mock.patch(
                "weightclass.delegation_conformance.subprocess.Popen",
                return_value=process,
            ),
            mock.patch("weightclass.delegation_conformance._write_request"),
            mock.patch("weightclass.delegation_conformance._read_response"),
            mock.patch("weightclass.delegation_conformance._signal_process_group") as killpg,
            mock.patch("weightclass.delegation_conformance.os.kill") as kill,
            mock.patch(
                "weightclass.delegation_conformance._process_group_exists",
                return_value=False,
            ) as probe,
            mock.patch("weightclass.delegation_conformance._wait_after_kill") as final_wait,
        ):
            passed = _run_driver_case(
                FAKE_DRIVER,
                FIXED_SENTINEL_RUNTIME,
                CONFORMANCE_CASES[0],
                Path.cwd(),
                timeout_seconds=0.1,
            )

        self.assertFalse(passed)
        self.assertEqual(sigchld_checks.call_count, 2)
        killpg.assert_not_called()
        kill.assert_not_called()
        probe.assert_not_called()
        final_wait.assert_not_called()
        cast(Any, process.wait).assert_not_called()

    @unittest.skipUnless(sys.platform == "darwin", "requires Darwin kqueue")
    def test_real_kqueue_fast_exit_remains_waitable_until_final_reap(self) -> None:
        """Breaks if kqueue ESRCH discards a normal unreaped zombie's status."""
        process = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            assert process.stdout is not None
            self.assertEqual(process.stdout.read(), b"")
            self.assertIsNone(process.returncode)
            with mock.patch.object(os, "waitid", None, create=True):
                exit_observer = _open_leader_exit_queue(process.pid)

            self.assertIsNotNone(exit_observer)
            self.assertTrue(_observe_leader_exit(process.pid, exit_observer))
            self.assertIsNone(process.returncode)
            self.assertEqual(_wait_after_kill(process), 0)
            self.assertEqual(process.returncode, 0)
        finally:
            if process.returncode is None:
                process.wait(timeout=1)
            if process.stdout is not None and not process.stdout.closed:
                process.stdout.close()

    @unittest.skipUnless(sys.platform == "darwin", "requires Darwin kqueue")
    def test_real_kqueue_external_reap_releases_every_signal_target(self) -> None:
        """Breaks if a real reaped Darwin child leaves stale signal targets."""
        from weightclass import delegation_conformance as conformance

        real_popen = cast(Any, subprocess.Popen)
        spawned: list[tuple[subprocess.Popen[bytes], int]] = []
        recorded_ownership: list[Any] = []
        original_record_process = conformance._DriverCaseOwnership.record_process

        def spawn_and_reap(
            _arguments: Any,
            *args: Any,
            **kwargs: Any,
        ) -> subprocess.Popen[bytes]:
            process = cast(
                subprocess.Popen[bytes],
                real_popen(("/usr/bin/true",), *args, **kwargs),
            )
            while True:
                try:
                    waited_pid, wait_status = os.waitpid(process.pid, 0)
                except InterruptedError:
                    continue
                break
            self.assertEqual(waited_pid, process.pid)
            spawned.append((process, wait_status))
            return process

        def record_ownership(ownership: Any, child: Any) -> None:
            original_record_process(ownership, child)
            recorded_ownership.append(ownership)

        try:
            with (
                tempfile.TemporaryDirectory() as temporary_directory,
                mock.patch.object(os, "waitid", None, create=True),
                mock.patch(
                    "weightclass.delegation_conformance.subprocess.Popen",
                    side_effect=spawn_and_reap,
                ),
                mock.patch.object(
                    conformance._DriverCaseOwnership,
                    "record_process",
                    autospec=True,
                    side_effect=record_ownership,
                ),
                mock.patch("weightclass.delegation_conformance._signal_process_group") as killpg,
                mock.patch("weightclass.delegation_conformance.os.kill") as kill,
                mock.patch(
                    "weightclass.delegation_conformance._process_group_exists",
                    return_value=False,
                ) as probe,
                mock.patch("weightclass.delegation_conformance._wait_after_kill") as final_wait,
            ):
                passed = _run_driver_case(
                    Path("/usr/bin/true"),
                    FIXED_SENTINEL_RUNTIME,
                    CONFORMANCE_CASES[0],
                    Path(temporary_directory),
                    timeout_seconds=0.1,
                )

            self.assertFalse(passed)
            killpg.assert_not_called()
            kill.assert_not_called()
            probe.assert_not_called()
            final_wait.assert_not_called()
            self.assertEqual(len(spawned), 1)
            self.assertEqual(len(recorded_ownership), 1)
            ownership = recorded_ownership[0]
            self.assertTrue(ownership.child_status_lost)
            self.assertIsNone(ownership.process_group_id)
            reader_signal_target = ownership.reader_signal_target
            self.assertIsNotNone(reader_signal_target)
            assert reader_signal_target is not None
            self.assertIsNone(reader_signal_target._process_group_id)
            deferred_sigint = getattr(ownership.before_final_reap, "__self__", None)
            self.assertIsNotNone(deferred_sigint)
            assert deferred_sigint is not None
            self.assertIsNone(deferred_sigint.process_group_id)
        finally:
            for process, wait_status in spawned:
                if process.returncode is None:
                    process.returncode = os.waitstatus_to_exitcode(wait_status)
                for stream in (process.stdin, process.stdout):
                    if stream is not None and not stream.closed:
                        stream.close()

    def test_status_echild_never_uses_popen_synthetic_zero(self) -> None:
        """Breaks if final ECHILD becomes a passing driver exit status."""
        process = self._mock_driver_process()
        events: list[str] = []

        def record_response(_stream: Any, _target: Any, state: Any) -> None:
            state.output.extend(
                b'{"case_id":"matrix/orchestrator/implementation/'
                b'workspace_read/allow","passed":true}'
            )

        def lose_status(_pid: int, _flags: int) -> tuple[int, int]:
            events.append("status-lost")
            raise ChildProcessError()

        def record_group_probe(_process_group_id: int) -> bool:
            events.append("probe-group")
            return False

        with (
            mock.patch(
                "weightclass.delegation_conformance.subprocess.Popen",
                return_value=process,
            ),
            mock.patch("weightclass.delegation_conformance._open_leader_exit_queue"),
            mock.patch(
                "weightclass.delegation_conformance._wait_for_leader_exit",
                return_value=True,
            ),
            mock.patch("weightclass.delegation_conformance._write_request"),
            mock.patch(
                "weightclass.delegation_conformance._read_response",
                side_effect=record_response,
            ),
            mock.patch(
                "weightclass.delegation_conformance._signal_process_group",
                side_effect=lambda *_: events.append("signal-group"),
            ),
            mock.patch(
                "weightclass.delegation_conformance.os.kill",
                side_effect=lambda *_: events.append("signal-pid"),
            ),
            mock.patch(
                "weightclass.delegation_conformance._process_group_exists",
                side_effect=record_group_probe,
            ),
            mock.patch(
                "weightclass.delegation_conformance.os.waitpid",
                side_effect=lose_status,
            ) as waitpid,
        ):
            passed = _run_driver_case(
                FAKE_DRIVER,
                FIXED_SENTINEL_RUNTIME,
                CONFORMANCE_CASES[0],
                Path.cwd(),
                timeout_seconds=0.1,
            )

        self.assertFalse(passed)
        waitpid.assert_called_once_with(process.pid, 0)
        cast(Any, process.wait).assert_not_called()
        lost_index = events.index("status-lost")
        self.assertFalse(
            any(
                event in {"signal-group", "signal-pid", "probe-group"}
                for event in events[lost_index + 1 :]
            ),
            events,
        )

    def test_sigint_with_default_disposition_cleans_group_before_termination(self) -> None:
        """Breaks if SIG_DFL terminates the runner before its owned group is cleaned."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            (
                process,
                pid_path,
                _,
                cleanup_observation_path,
                lock_path,
                stop_path,
            ) = self._start_signal_case(Path(temporary_directory), handler_mode="default")
            try:
                driver_pid, process_group_id = self._wait_for_pid_publication(pid_path, process)
                self.assertEqual(driver_pid, process_group_id)
                process.send_signal(signal.SIGINT)
                _, stderr = process.communicate(timeout=5)

                self.assertEqual(process.returncode, -signal.SIGINT)
                self.assertEqual(stderr, "")
                self._assert_production_cleanup_observed(cleanup_observation_path)
            finally:
                self._cleanup_nested_fixture_process(process, lock_path, stop_path)

    def test_sigint_handler_swap_dispatches_each_signal_once(self) -> None:
        """Breaks if restoring the previous handler loses a boundary SIGINT."""
        received_frames: list[Any] = []

        def previous_handler(signal_number: int, frame: Any) -> None:
            del signal_number
            received_frames.append(frame)

        real_signal = signal.signal
        original_handler = real_signal(signal.SIGINT, previous_handler)
        deferred_sigint = _DeferredSigint()
        deferred_sigint.arm()
        deferred_sigint.record_process_group(12345)
        deferred_sigint.clear_process_group()
        injected = False

        def signal_during_swap(signal_number: int, handler: Any) -> Any:
            nonlocal injected
            if signal_number == signal.SIGINT and handler is previous_handler and not injected:
                injected = True
                os.kill(os.getpid(), signal.SIGINT)
            return real_signal(signal_number, handler)

        try:
            with (
                mock.patch(
                    "weightclass.delegation_conformance.signal.signal",
                    side_effect=signal_during_swap,
                ),
                mock.patch(
                    "weightclass.delegation_conformance._signal_process_group"
                ) as signal_group,
            ):
                deferred_sigint.restore()

            self.assertTrue(injected)
            self.assertEqual(len(received_frames), 1)
            self.assertIsNotNone(received_frames[0])
            signal_group.assert_not_called()
            self.assertIs(signal.getsignal(signal.SIGINT), previous_handler)
            os.kill(os.getpid(), signal.SIGINT)
            self.assertEqual(len(received_frames), 2)
            self.assertIsNotNone(received_frames[1])
        finally:
            real_signal(signal.SIGINT, original_handler)

    def test_sigint_callable_runs_only_after_owned_group_cleanup(self) -> None:
        """Breaks if a previous callable handler observes the driver before cleanup."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            (
                process,
                pid_path,
                _,
                cleanup_observation_path,
                lock_path,
                stop_path,
            ) = self._start_signal_case(directory, handler_mode="callable")
            try:
                _driver_pid, _process_group_id = self._wait_for_pid_publication(pid_path, process)
                process.send_signal(signal.SIGINT)
                _, stderr = process.communicate(timeout=5)

                self.assertEqual(process.returncode, 1)
                self.assertEqual(stderr, "")
                self.assertEqual(
                    (directory / "handler.log").read_text(encoding="ascii").splitlines(),
                    ["after-cleanup"],
                )
                self._assert_production_cleanup_observed(cleanup_observation_path)
            finally:
                self._cleanup_nested_fixture_process(process, lock_path, stop_path)

    def test_sigint_ignore_disposition_is_preserved_through_timeout_cleanup(self) -> None:
        """Breaks if installing the deferred handler changes an ignored SIGINT."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            (
                process,
                pid_path,
                _,
                cleanup_observation_path,
                lock_path,
                stop_path,
            ) = self._start_signal_case(directory, handler_mode="ignore")
            try:
                _driver_pid, _process_group_id = self._wait_for_pid_publication(pid_path, process)
                started_at = time.monotonic()
                process.send_signal(signal.SIGINT)
                _, stderr = process.communicate(timeout=5)

                self.assertEqual(process.returncode, 1)
                self.assertEqual(stderr, "")
                self.assertGreaterEqual(time.monotonic() - started_at, 0.2)
                self.assertFalse((directory / "handler.log").exists())
                self._assert_production_cleanup_observed(cleanup_observation_path)
            finally:
                self._cleanup_nested_fixture_process(process, lock_path, stop_path)

    def test_two_sigints_during_cleanup_are_deferred_until_group_is_gone(self) -> None:
        """Breaks if a second SIGINT can interrupt the ownership cleanup scope."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            (
                process,
                pid_path,
                cleanup_path,
                cleanup_observation_path,
                lock_path,
                stop_path,
            ) = self._start_signal_case(
                directory,
                handler_mode="callable",
                delayed_cleanup=True,
            )
            try:
                _driver_pid, _process_group_id = self._wait_for_pid_publication(pid_path, process)
                process.send_signal(signal.SIGINT)
                self._wait_for_path(cleanup_path, process)
                process.send_signal(signal.SIGINT)
                _, stderr = process.communicate(timeout=5)

                self.assertEqual(process.returncode, 1)
                self.assertEqual(stderr, "")
                self.assertEqual(
                    (directory / "handler.log").read_text(encoding="ascii").splitlines(),
                    ["after-cleanup"],
                )
                self._assert_production_cleanup_observed(cleanup_observation_path)
            finally:
                self._cleanup_nested_fixture_process(process, lock_path, stop_path)

    def test_group_is_killed_and_checked_before_the_single_final_reap(self) -> None:
        """Breaks if wait reaps the leader before group ownership cleanup finishes."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            workspace_path = directory / "workspace"
            workspace_path.mkdir(mode=0o700)
            _write_executable(runtime_path, b"unqualified-test-runtime\n")
            real_popen = cast(Any, subprocess.Popen)
            spawned: list[tuple[subprocess.Popen[bytes], int]] = []
            events: list[tuple[str, int | None]] = []
            group_exists_results: list[bool] = []

            def capture_process(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
                process = cast(subprocess.Popen[bytes], real_popen(*args, **kwargs))
                spawned.append((process, os.getpgid(process.pid)))
                return process

            from weightclass import delegation_conformance as conformance_module

            real_group_exists = conformance_module._process_group_exists
            signal_group = conformance_module._signal_process_group
            wait_after_kill = conformance_module._wait_after_kill

            def observe_group(process_group_id: int) -> bool:
                group_exists = real_group_exists(process_group_id)
                group_exists_results.append(group_exists)
                events.append(("check", spawned[0][0].returncode))
                return group_exists

            def observe_signal(process_group_id: int, signal_number: int) -> None:
                events.append(("kill", spawned[0][0].returncode))
                signal_group(process_group_id, signal_number)

            def observe_reap(process: subprocess.Popen[bytes]) -> int:
                events.append(("reap", process.returncode))
                return wait_after_kill(process)

            try:
                with (
                    mock.patch(
                        "weightclass.delegation_conformance.subprocess.Popen",
                        side_effect=capture_process,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._process_group_exists",
                        side_effect=observe_group,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._signal_process_group",
                        side_effect=observe_signal,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._wait_after_kill",
                        side_effect=observe_reap,
                    ),
                ):
                    passed = _run_driver_case(
                        FAKE_DRIVER,
                        runtime_path,
                        CONFORMANCE_CASES[0],
                        workspace_path,
                        timeout_seconds=5,
                    )

                self.assertTrue(passed)
                self.assertEqual(sum(name == "reap" for name, _ in events), 1)
                reap_index = next(index for index, event in enumerate(events) if event[0] == "reap")
                self.assertLess(
                    next(i for i, event in enumerate(events) if event[0] == "kill"), reap_index
                )
                self.assertLess(
                    max(i for i, event in enumerate(events) if event[0] == "check"), reap_index
                )
                self.assertTrue(group_exists_results)
                self.assertFalse(group_exists_results[-1])
                self.assertTrue(
                    all(return_code is None for _, return_code in events),
                    events,
                )
            finally:
                for process, _ in spawned:
                    self._cleanup_owned_process_group(process)

    def test_sigint_at_final_reap_boundary_never_targets_released_group(self) -> None:
        """Breaks if deferred SIGINT can target a numeric PGID after final reap."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            workspace_path = directory / "workspace"
            workspace_path.mkdir(mode=0o700)
            _write_executable(runtime_path, b"unqualified-test-runtime\n")
            real_popen = cast(Any, subprocess.Popen)
            spawned: list[tuple[subprocess.Popen[bytes], int]] = []
            events: list[tuple[str, int | None]] = []
            group_checks = 0

            def capture_process(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
                process = cast(subprocess.Popen[bytes], real_popen(*args, **kwargs))
                spawned.append((process, os.getpgid(process.pid)))
                return process

            from weightclass import delegation_conformance as conformance_module

            real_group_exists = conformance_module._process_group_exists
            signal_group = conformance_module._signal_process_group
            wait_after_kill = conformance_module._wait_after_kill

            def observe_group(process_group_id: int) -> bool:
                nonlocal group_checks
                group_checks += 1
                if group_checks == 2:
                    os.kill(os.getpid(), signal.SIGINT)
                events.append(("check", spawned[0][0].returncode))
                return real_group_exists(process_group_id)

            def observe_signal(process_group_id: int, signal_number: int) -> None:
                events.append(("kill", spawned[0][0].returncode))
                signal_group(process_group_id, signal_number)

            def interrupt_reap_boundary(process: subprocess.Popen[bytes]) -> int:
                events.append(("reap_call", process.returncode))
                os.kill(os.getpid(), signal.SIGINT)
                return_code = wait_after_kill(process)
                events.append(("reap_return", process.returncode))
                os.kill(os.getpid(), signal.SIGINT)
                return return_code

            try:
                with (
                    mock.patch(
                        "weightclass.delegation_conformance.subprocess.Popen",
                        side_effect=capture_process,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._process_group_exists",
                        side_effect=observe_group,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._signal_process_group",
                        side_effect=observe_signal,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._wait_after_kill",
                        side_effect=interrupt_reap_boundary,
                    ),
                    self.assertRaises(KeyboardInterrupt),
                ):
                    _run_driver_case(
                        FAKE_DRIVER,
                        runtime_path,
                        CONFORMANCE_CASES[0],
                        workspace_path,
                        timeout_seconds=5,
                    )

                self.assertEqual(len(spawned), 1)
                self.assertEqual(
                    [event for event in events if event[0].startswith("reap_")],
                    [("reap_call", None), ("reap_return", 0)],
                )
                self.assertEqual(
                    [return_code for name, return_code in events if name == "kill"],
                    [None, None],
                    events,
                )
            finally:
                for process, _ in spawned:
                    self._cleanup_owned_process_group(process)

    def test_late_reader_signal_is_disabled_before_final_reap(self) -> None:
        """Breaks if a reader retains a signal target after PGID ownership ends."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            workspace_path = directory / "workspace"
            workspace_path.mkdir(mode=0o700)
            _write_executable(runtime_path, b"unqualified-test-runtime\n")
            real_popen = cast(Any, subprocess.Popen)
            real_thread_join = threading.Thread.join
            spawned: list[tuple[subprocess.Popen[bytes], int]] = []
            reader_started = threading.Event()
            release_reader = threading.Event()
            reader_finished = threading.Event()
            events: list[tuple[str, int | None]] = []
            stream_states_at_reap: list[tuple[bool, bool]] = []

            def capture_process(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
                process = cast(subprocess.Popen[bytes], real_popen(*args, **kwargs))
                spawned.append((process, os.getpgid(process.pid)))
                return process

            from weightclass import delegation_conformance as conformance_module

            signal_group = conformance_module._signal_process_group
            wait_after_kill = conformance_module._wait_after_kill

            def paused_reader(stream: Any, signal_target: Any, state: Any) -> None:
                del stream, state
                reader_started.set()
                release_reader.wait(timeout=5)
                if isinstance(signal_target, int):
                    conformance_module._signal_process_group(signal_target, signal.SIGKILL)
                else:
                    signal_target.signal(signal.SIGKILL)
                reader_finished.set()

            def observe_signal(process_group_id: int, signal_number: int) -> None:
                events.append(("kill", spawned[0][0].returncode))
                signal_group(process_group_id, signal_number)

            def observe_join(thread: threading.Thread, timeout: float | None = None) -> None:
                del timeout
                events.append(("join", spawned[0][0].returncode))
                real_thread_join(thread, timeout=0)

            def release_after_reap(process: subprocess.Popen[bytes]) -> int:
                assert process.stdin is not None
                assert process.stdout is not None
                stream_states_at_reap.append((process.stdin.closed, process.stdout.closed))
                events.append(("reap_call", process.returncode))
                return_code = wait_after_kill(process)
                events.append(("reaped", process.returncode))
                self.assertTrue(reader_started.wait(timeout=1))
                release_reader.set()
                self.assertTrue(reader_finished.wait(timeout=1))
                return return_code

            try:
                with (
                    mock.patch(
                        "weightclass.delegation_conformance.subprocess.Popen",
                        side_effect=capture_process,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._read_response",
                        side_effect=paused_reader,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._signal_process_group",
                        side_effect=observe_signal,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._wait_after_kill",
                        side_effect=release_after_reap,
                    ),
                    mock.patch.object(threading.Thread, "join", new=observe_join),
                ):
                    _run_driver_case(
                        FAKE_DRIVER,
                        runtime_path,
                        CONFORMANCE_CASES[0],
                        workspace_path,
                        timeout_seconds=5,
                    )

                self.assertEqual(
                    [return_code for name, return_code in events if name == "kill"],
                    [None],
                    events,
                )
                self.assertEqual(stream_states_at_reap, [(True, True)])
                self.assertTrue(
                    all(return_code is None for name, return_code in events if name == "join"),
                    events,
                )
                self.assertEqual(sum(name == "reaped" for name, _ in events), 1)
            finally:
                release_reader.set()
                reader_finished.wait(timeout=1)
                for process, _ in spawned:
                    self._cleanup_owned_process_group(process)

    def test_unsafe_sigchld_dispositions_fail_before_driver_spawn(self) -> None:
        """Breaks if inherited SIGCHLD handling can release the PGID anchor."""
        for mode in ("ignore", "callable"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary_directory:
                directory = Path(temporary_directory)
                runtime_path = directory / "runtime"
                pid_path = directory / "driver.pid"
                _write_executable(runtime_path, b"unqualified-test-runtime\n")
                environment = os.environ.copy()
                environment["WEIGHTCLASS_FAKE_CONFORMANCE_MODE"] = "hang"
                environment["WEIGHTCLASS_FAKE_CONFORMANCE_TARGET"] = CONFORMANCE_CASES[0].case_id
                environment["WEIGHTCLASS_FAKE_CONFORMANCE_PID_PATH"] = str(pid_path)
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        _SIGCHLD_CLI_RUNNER,
                        mode,
                        str(directory / "spawn-attempted"),
                        *self._arguments(runtime_path)[3:],
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=environment,
                    text=True,
                )
                try:
                    stdout, stderr = process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 2)
                    self.assertEqual(stdout, "")
                    self.assertEqual(stderr, '{"error": "invalid_input"}\n')
                    self.assertFalse(pid_path.exists())
                    self.assertFalse((directory / "spawn-attempted").exists())
                finally:
                    self._cleanup_test_process(process)

    @unittest.skipUnless(
        (sys.platform == "darwin" or sys.platform.startswith("linux"))
        and _has_safe_sigchld_disposition(),
        "requires the production native SIGCHLD oracle",
    )
    def test_hidden_sa_nocldwait_is_rejected_before_driver_spawn(self) -> None:
        """Breaks if Python's cached SIG_DFL hides native auto-reaping."""
        expected = (
            ("default", 0, True, ""),
            ("nocldwait", 2, False, '{"error": "invalid_input"}\n'),
        )
        for mode, return_code, spawn_expected, expected_stderr in expected:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary_directory:
                directory = Path(temporary_directory)
                runtime_path = directory / "runtime"
                spawn_marker = directory / "spawn-attempted"
                _write_executable(runtime_path, b"unqualified-test-runtime\n")
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        _SIGCHLD_CLI_RUNNER,
                        mode,
                        str(spawn_marker),
                        *self._arguments(runtime_path)[3:],
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                try:
                    stdout, stderr = process.communicate(timeout=5)
                    self.assertEqual(process.returncode, return_code, stderr)
                    self.assertEqual(stderr, expected_stderr)
                    self.assertEqual(spawn_marker.is_file(), spawn_expected)
                    self.assertEqual(bool(stdout), spawn_expected)
                finally:
                    self._cleanup_test_process(process)

    def test_linux_group_probe_ignores_only_dead_members_of_the_anchored_group(self) -> None:
        """Breaks if Linux mistakes the WNOWAIT zombie leader for a live descendant."""
        self.assertTrue(
            _linux_proc_stat_live_group_member(
                b"321 (driver ) name) S 1 321 321 0",
                321,
            )
        )
        self.assertFalse(
            _linux_proc_stat_live_group_member(
                b"321 (driver) Z 1 321 321 0",
                321,
            )
        )
        self.assertFalse(
            _linux_proc_stat_live_group_member(
                b"654 (unrelated) S 1 654 654 0",
                321,
            )
        )
        self.assertIsNone(_linux_proc_stat_live_group_member(b"malformed", 321))

    def test_interrupt_after_leader_wait_reaps_owned_descendant_group(self) -> None:
        """Breaks if interruption during leak inspection abandons a descendant."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            workspace_path = directory / "workspace"
            pid_path = directory / "descendant.pid"
            workspace_path.mkdir(mode=0o700)
            _write_executable(runtime_path, b"unqualified-test-runtime\n")
            environment = os.environ.copy()
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_MODE"] = "leak"
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_TARGET"] = CONFORMANCE_CASES[0].case_id
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_PID_PATH"] = str(pid_path)
            real_popen = cast(Any, subprocess.Popen)
            spawned: list[tuple[subprocess.Popen[bytes], int]] = []
            observed_descendant: list[int] = []
            group_observations: list[tuple[bool, int | None]] = []
            from weightclass import delegation_conformance as conformance_module

            real_group_exists = conformance_module._process_group_exists

            def capture_process(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
                process = cast(subprocess.Popen[bytes], real_popen(*args, **kwargs))
                spawned.append((process, os.getpgid(process.pid)))
                return process

            def interrupt_after_wait(process_group_id: int) -> bool:
                process = spawned[0][0]
                group_exists = real_group_exists(process_group_id)
                group_observations.append((group_exists, process.returncode))
                if not observed_descendant:
                    descendant_pid = int(pid_path.read_text(encoding="ascii"))
                    observed_descendant.append(descendant_pid)
                    raise KeyboardInterrupt
                return group_exists

            try:
                with (
                    mock.patch.dict(os.environ, environment, clear=True),
                    mock.patch(
                        "weightclass.delegation_conformance.subprocess.Popen",
                        side_effect=capture_process,
                    ),
                    mock.patch(
                        "weightclass.delegation_conformance._process_group_exists",
                        side_effect=interrupt_after_wait,
                    ),
                    self.assertRaises(KeyboardInterrupt),
                ):
                    _run_driver_case(
                        FAKE_DRIVER,
                        runtime_path,
                        CONFORMANCE_CASES[0],
                        workspace_path,
                        timeout_seconds=5,
                    )

                self.assertEqual(len(spawned), 1)
                process, process_group_id = spawned[0]
                self.assertEqual(process.returncode, 0)
                self.assertEqual(len(observed_descendant), 1)
                self.assertGreater(observed_descendant[0], 0)
                self.assertEqual(process_group_id, process.pid)
                self.assertTrue(group_observations)
                self.assertTrue(all(returncode is None for _, returncode in group_observations))
                self.assertIn((True, None), group_observations)
                self.assertEqual(group_observations[-1], (False, None))
            finally:
                for process, _ in spawned:
                    self._cleanup_owned_process_group(process)

    def test_interrupt_reaps_active_driver_without_traceback(self) -> None:
        """Breaks if Ctrl-C abandons the driver's new session or prints a traceback."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            runtime_path = directory / "runtime"
            pid_path = directory / "driver.pid"
            lock_path = directory / "driver.lock"
            stop_path = directory / "driver.stop"
            _write_executable(runtime_path, b"unqualified-test-runtime\n")
            environment = os.environ.copy()
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_MODE"] = "hang"
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_TARGET"] = (
                "matrix/orchestrator/implementation/workspace_read/allow"
            )
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_PID_PATH"] = str(pid_path)
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_LOCK_PATH"] = str(lock_path)
            environment["WEIGHTCLASS_FAKE_CONFORMANCE_STOP_PATH"] = str(stop_path)
            process = subprocess.Popen(
                self._arguments(runtime_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                text=True,
            )
            driver_pid: int | None = None
            process_group_id: int | None = None
            try:
                for _ in range(500):
                    if pid_path.exists():
                        driver_pid = int(pid_path.read_text(encoding="ascii"))
                        break
                    if process.poll() is not None:
                        break
                    time.sleep(0.01)
                self.assertIsNotNone(driver_pid)
                assert driver_pid is not None
                lock_published = False
                for _ in range(100):
                    if lock_path.exists() and lock_path.read_text(encoding="ascii") == "locked":
                        lock_published = True
                        break
                    if process.poll() is not None:
                        break
                    time.sleep(0.01)
                if not lock_published:
                    process.send_signal(signal.SIGINT)
                    process.communicate(timeout=5)
                self.assertTrue(lock_published, "driver liveness lock was not published")
                process_group_id = os.getpgid(driver_pid)
                self.assertEqual(process_group_id, driver_pid)
                process.send_signal(signal.SIGINT)
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 130)
                self.assertEqual(stdout, "")
                self.assertEqual(stderr, '{"error": "interrupted"}\n')
                with lock_path.open("rb") as lock_file:
                    try:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError as error:
                        raise AssertionError(
                            "driver retained its liveness lock after runner cleanup"
                        ) from error
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                self._cleanup_nested_fixture_process(process, lock_path, stop_path)


if __name__ == "__main__":
    unittest.main()
