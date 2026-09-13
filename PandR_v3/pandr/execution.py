"""Managed execution of explicitly trusted operator Python.

This is a resource/lifecycle manager, not a Python sandbox. A script has the
permissions of the current user. No script is executed merely by loading JSON.
"""
from __future__ import annotations

import codecs
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable


class _WindowsJob:
    """Kill every member of an owned Windows Job when its handle closes."""

    def __init__(self, process: subprocess.Popen) -> None:
        import ctypes
        from ctypes import wintypes

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters)] + [
                (name, ctypes.c_size_t) for name in (
                    "ProcessMemoryLimit", "JobMemoryLimit", "PeakProcessMemoryUsed",
                    "PeakJobMemoryUsed")]

        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        self._kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel.SetInformationJobObject.argtypes = (
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        self._kernel.SetInformationJobObject.restype = wintypes.BOOL
        self._kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        self._kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self._kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._kernel.CloseHandle.restype = wintypes.BOOL
        self._handle = self._kernel.CreateJobObjectW(None, None)
        if not self._handle:
            raise OSError(ctypes.get_last_error(), "Could not create a Windows process job")
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if not self._kernel.SetInformationJobObject(
            self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ) or not self._kernel.AssignProcessToJobObject(
            self._handle, wintypes.HANDLE(int(process._handle))
        ):
            error = ctypes.get_last_error()
            self.close()
            raise OSError(error, "Could not own the script's complete process tree")

    def close(self) -> None:
        if self._handle:
            self._kernel.CloseHandle(self._handle)
            self._handle = None


class ExecutionService:
    """Thread-safe synchronous runner; call it from a GUI worker thread.

    Output is UTF-8, merged stdout/stderr, streamed in bounded blocks. Exceeding
    the output budget terminates the run. ``cancel_all`` also kills descendants.
    Each invocation returns an immutable-run result rather than updating session
    state on its own; the caller can persist this result through Runtime.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._running: dict[int, tuple[subprocess.Popen, _WindowsJob | None, threading.Event]] = {}
        self._generation = 0

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._running)

    def cancel_all(self) -> None:
        with self._lock:
            self._generation += 1
            running = list(self._running.values())
            for process, job, cancelled in running:
                cancelled.set()
                self._terminate(process, job)

    cancel = cancel_all
    close = cancel_all

    @staticmethod
    def _terminate(process: subprocess.Popen, job: _WindowsJob | None) -> None:
        if job is not None:
            job.close()
        elif os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass

    def run_script(
        self, source: str, session_path: str | Path, cwd: str | Path | None = None,
        timeout: float = 30, max_output: int = 262144,
        on_output: Callable[[str], None] | None = None,
    ) -> dict:
        if not isinstance(source, str) or not source.strip():
            raise ValueError("A nonempty Python source string is required")
        if len(source.encode("utf-8")) > 1_048_576:
            raise ValueError("Script source exceeds the 1 MiB limit")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 86400:
            raise ValueError("Timeout must be greater than zero and at most 86400 seconds")
        if type(max_output) is not int or not 1 <= max_output <= 4_194_304:
            raise ValueError("Output budget must be an integer from 1 to 4194304 bytes")
        session = Path(session_path).resolve()
        directory = Path(cwd).resolve() if cwd is not None else session.parent
        if not directory.is_dir():
            raise ValueError("Script working directory must exist")
        started = time.monotonic()
        deadline = started + timeout
        source_digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        output = bytearray()
        output_limit = threading.Event()
        cancelled = threading.Event()
        status = "error"
        error = None
        process = None
        job = None
        reader = None
        with self._lock:
            generation = self._generation
        try:
            with tempfile.TemporaryDirectory(prefix="pandr-script-") as temp_dir:
                source_path = Path(temp_dir) / "operator.py"
                source_path.write_text(source, encoding="utf-8", newline="\n")
                # A one-byte gate prevents any operator code (or child spawning)
                # before the process has joined its Windows Job Object.
                runner = (
                    "import sys,runpy; "
                    "gate=sys.stdin.buffer.read(1); "
                    "sys.stdin.close(); "
                    "gate == b'1' or sys.exit(125); "
                    "runpy.run_path(sys.argv[1],run_name='__main__')"
                )
                environment = os.environ.copy()
                environment["PANDR_SESSION"] = str(session)
                package_root = str(Path(__file__).resolve().parent.parent)
                environment["PYTHONPATH"] = os.pathsep.join(filter(None, (
                    package_root, environment.get("PYTHONPATH", ""))))
                environment["PYTHONUTF8"] = "1"
                environment["PYTHONIOENCODING"] = "utf-8:replace"
                options = {"start_new_session": True} if os.name != "nt" else {
                    "creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
                process = subprocess.Popen(
                    [sys.executable, "-u", "-c", runner, str(source_path)],
                    cwd=str(directory), env=environment, shell=False,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    bufsize=0, **options,
                )
                if os.name == "nt":
                    job = _WindowsJob(process)
                with self._lock:
                    self._running[process.pid] = (process, job, cancelled)
                    if generation != self._generation:
                        cancelled.set()
                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

                def retain(block: bytes) -> None:
                    remaining = max_output - len(output)
                    kept = block[:max(0, remaining)]
                    output.extend(kept)
                    if on_output and kept:
                        try:
                            on_output(decoder.decode(kept))
                        except Exception:
                            pass  # A consumer of logs must not break lifecycle cleanup.
                    if len(block) > remaining:
                        output_limit.set()

                def capture() -> None:
                    try:
                        while True:
                            block = process.stdout.read(8192)
                            if not block:
                                break
                            retain(block)
                    except (OSError, ValueError):
                        pass
                    finally:
                        if on_output:
                            tail = decoder.decode(b"", final=True)
                            if tail:
                                try:
                                    on_output(tail)
                                except Exception:
                                    pass

                reader = threading.Thread(target=capture, name=f"pandr-output-{process.pid}", daemon=True)
                reader.start()
                if not cancelled.is_set():
                    process.stdin.write(b"1")
                    process.stdin.flush()
                process.stdin.close()
                while True:
                    if cancelled.is_set():
                        status = "cancelled"
                        break
                    if output_limit.is_set():
                        status = "output_limit"
                        break
                    if time.monotonic() >= deadline:
                        status = "timeout"
                        break
                    if process.poll() is not None:
                        status = "success" if process.returncode == 0 else "error"
                        break
                    cancelled.wait(min(0.02, max(0, deadline - time.monotonic())))
                self._terminate(process, job)  # Reap descendants even after normal exit.
                process.wait(timeout=3)
                reader.join(timeout=3)
                if output_limit.is_set() and status not in ("cancelled", "timeout"):
                    status = "output_limit"
        except (OSError, subprocess.SubprocessError, ValueError) as exception:
            error = f"{type(exception).__name__}: {exception}"
        finally:
            if process is not None:
                with self._lock:
                    self._terminate(process, job)
                    self._running.pop(process.pid, None)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
                if reader is not None:
                    reader.join(timeout=1)
                for stream in (process.stdin, process.stdout):
                    if stream is not None:
                        stream.close()
        result = {
            "status": status, "output": output.decode("utf-8", errors="replace"),
            "returncode": process.returncode if process else None,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "source_digest": source_digest, "interpreter": sys.executable,
            "python_version": sys.version.split()[0], "session_path": str(session),
            "working_directory": str(directory), "output_truncated": output_limit.is_set(),
            "trusted_python": True, "construction_replayable": False,
        }
        if error:
            result["error"] = error
        return result
