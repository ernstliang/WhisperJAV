"""
Job queue: one serial worker thread, one subprocess at a time.

Jobs are subprocesses of the *server*, never of a request, so closing the
browser has no effect on a running job. Running the pipeline in-process is not
an option:

  * whisperjav/main.py:2501-2505 calls os._exit(0) for the ctranslate2 modes,
    which would take the web server down with it.
  * whisperjav/utils/unified_progress.py:53-81 swaps sys.stdout/sys.stderr for
    StringIO during ASR, swallowing every other thread's output.
  * The pipelines have no cancellation: the token in
    whisperjav/utils/async_processor.py:209-222 is an admitted TODO stub.
    Killing the process tree is the only thing that actually works.
"""

import atexit
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from whisperjav.webserver.paths import REPO_ROOT
from whisperjav.webserver.progress import (
    ProgressTracker,
    normalize,
    split_stream_line,
)

LOG_RING_SIZE = 5000
MAX_RETAINED_JOBS = 50
TERMINAL_STATES = ("completed", "failed", "cancelled")

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# Windows reports an unhandled structured exception as the NTSTATUS value.
# Surfacing "exited with code 3221225620" tells the user nothing; naming the
# fault tells them it was a native crash, not a Python error they can read.
_NTSTATUS_NAMES = {
    0xC0000005: "ACCESS_VIOLATION (segfault in a native library)",
    0xC000001D: "ILLEGAL_INSTRUCTION",
    0xC0000094: "INTEGER_DIVIDE_BY_ZERO (native)",
    0xC000008E: "FLOAT_DIVIDE_BY_ZERO (native)",
    0xC00000FD: "STACK_OVERFLOW",
    0xC0000135: "DLL_NOT_FOUND",
    0xC0000139: "ENTRYPOINT_NOT_FOUND",
    0xC000013A: "CONTROL_C_EXIT",
    0xC0000374: "HEAP_CORRUPTION",
    0xC0000409: "STACK_BUFFER_OVERRUN",
    0xC0000417: "INVALID_CRT_PARAMETER",
}


def describe_exit_code(code: Optional[int]) -> str:
    """Human-readable failure reason for a process return code."""
    if code is None:
        return "Process ended without a return code"
    if sys.platform == "win32" and code < 0:
        code &= 0xFFFFFFFF
    name = _NTSTATUS_NAMES.get(code)
    if name:
        return (f"Process crashed: {name} [0x{code:08X}]. This is a native "
                f"fault inside a loaded library, so there is no Python "
                f"traceback; see the log tail for the last step reached.")
    if sys.platform != "win32" and code < 0:
        return f"Process killed by signal {-code}"
    return f"Exited with code {code}"


def _log_dir() -> Path:
    p = Path(tempfile.gettempdir()) / "whisperjav" / "webserver"
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class Job:
    id: str
    flow: str
    argv: List[str]
    command_display: str
    inputs: List[str]
    tracker: ProgressTracker
    output_dir: Optional[str] = None

    state: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    exit_code: Optional[int] = None
    error: Optional[str] = None
    pid: Optional[int] = None
    log_file: Optional[str] = None
    cancel_requested: bool = False

    _log: Deque[str] = field(default_factory=lambda: deque(maxlen=LOG_RING_SIZE))
    log_seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _proc: Optional[subprocess.Popen] = None
    _sink: Optional[Any] = None

    # -- logging ---------------------------------------------------------

    def append_log(self, line: str) -> None:
        text = line.rstrip("\r\n")
        with self._lock:
            self._log.append(text)
            self.log_seq += 1
            if self._sink is not None:
                try:
                    self._sink.write(text + "\n")
                except (OSError, ValueError):
                    self._sink = None

    def logs_since(self, since: int, limit: int = 2000) -> Dict[str, Any]:
        """`since` is an absolute sequence number, not a ring index."""
        with self._lock:
            total = self.log_seq
            buffered = len(self._log)
            oldest = total - buffered
            start = max(since, oldest)
            dropped = max(0, oldest - since) if since < oldest else 0
            offset = start - oldest
            lines = list(self._log)[offset:offset + limit]
            next_since = start + len(lines)
        return {
            "since": since, "next_since": next_since,
            "dropped": dropped, "lines": lines,
        }

    # -- views -----------------------------------------------------------

    @property
    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.ended_at if self.ended_at is not None else time.time()
        return max(0.0, end - self.started_at)

    def view(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "flow": self.flow,
            "state": self.state,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "elapsed": round(self.elapsed, 1),
            "exit_code": self.exit_code,
            "error": self.error,
            "pid": self.pid,
            "command": self.command_display,
            "inputs": list(self.inputs),
            "output_files": list(self.tracker.output_files),
            "log_file": self.log_file,
            "log_seq": self.log_seq,
            "progress": self.tracker.snapshot(),
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: "OrderedDict[str, Job]" = OrderedDict()
        self._queue: Deque[str] = deque()
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._stopping = False
        self._current: Optional[Job] = None

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._worker is not None:
                return
            self._stopping = False
            self._worker = threading.Thread(
                target=self._run_loop, name="whisperjav-web-worker", daemon=True)
            self._worker.start()
        atexit.register(self.shutdown)

    def shutdown(self, timeout: float = 8.0) -> None:
        """
        Kill the running tree and drain the queue.

        Without this, Ctrl-C on the server leaves an orphaned CUDA process
        holding VRAM with no owner — the worst failure this server could ship.
        """
        with self._lock:
            if self._stopping:
                return
            self._stopping = True
            queued = list(self._queue)
            self._queue.clear()
            current = self._current
        for job_id in queued:
            job = self._jobs.get(job_id)
            if job is not None and job.state == "queued":
                job.state = "cancelled"
                job.ended_at = time.time()
        if current is not None:
            current.cancel_requested = True
            self._terminate(current)
        self._wake.set()
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=timeout)

    # -- submission ------------------------------------------------------

    def submit(self, flow: str, argv: List[str], command_display: str,
               inputs: List[str], output_dir: Optional[str] = None,
               debug: bool = False) -> Job:
        job = Job(
            id=secrets.token_hex(4),
            flow=flow,
            argv=list(argv),
            command_display=command_display,
            inputs=list(inputs),
            tracker=ProgressTracker(flow, output_dir=output_dir),
            output_dir=output_dir,
        )
        if debug:
            job.log_file = str(_log_dir() / f"{job.id}.log")

        with self._lock:
            self._jobs[job.id] = job
            self._queue.append(job.id)
            self._evict_old()
        self._wake.set()
        return job

    def _evict_old(self) -> None:
        while len(self._jobs) > MAX_RETAINED_JOBS:
            for job_id, job in list(self._jobs.items()):
                if job.state in TERMINAL_STATES:
                    del self._jobs[job_id]
                    break
            else:
                return

    # -- queries ---------------------------------------------------------

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> List[Job]:
        with self._lock:
            return list(reversed(list(self._jobs.values())))

    def position(self, job_id: str) -> int:
        with self._lock:
            try:
                return self._queue.index(job_id)
            except ValueError:
                return 0

    def delete(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if job.state not in TERMINAL_STATES:
                raise RuntimeError("Job is still active.")
            del self._jobs[job_id]
            return True

    # -- cancellation ----------------------------------------------------

    def cancel(self, job_id: str) -> Optional[Job]:
        job = self.get(job_id)
        if job is None:
            return None
        if job.state in TERMINAL_STATES:
            return job

        job.cancel_requested = True

        # Decide by queue membership, not by job.state: a job can be popped
        # from the queue microseconds before _run publishes "running", and
        # marking it cancelled here would then be overwritten by the worker.
        with self._lock:
            try:
                self._queue.remove(job_id)
                still_queued = True
            except ValueError:
                still_queued = False

        if still_queued:
            job.state = "cancelled"
            job.ended_at = time.time()
            job.append_log("[CANCELLED] Removed from the queue before it started.")
            return job

        # If the process has not spawned yet this is a no-op; _run re-checks
        # cancel_requested immediately after Popen and terminates then.
        self._terminate(job)
        return job

    def _terminate(self, job: Job) -> None:
        proc = job._proc
        if proc is None or proc.poll() is not None:
            return
        pid = proc.pid
        try:
            from whisperjav.utils.process_manager import (
                PSUTIL_AVAILABLE,
                terminate_process_tree,
            )
        except ImportError:
            PSUTIL_AVAILABLE, terminate_process_tree = False, None

        # Tree kill is not optional: --async-processing and the ensemble path
        # spawn GPU worker children that would otherwise be stranded on VRAM.
        if PSUTIL_AVAILABLE and terminate_process_tree is not None:
            try:
                result = terminate_process_tree(pid, timeout=5.0, include_parent=True)
                killed = result.get("terminated", []) + result.get("killed", [])
                job.append_log(f"[CANCELLED] Terminated {len(killed)} process(es).")
                for err in result.get("errors", []):
                    job.append_log(f"[WARNING] {err}")
                return
            except Exception as exc:  # noqa: BLE001 - never let cancel raise
                job.append_log(f"[WARNING] Tree termination failed: {exc}")

        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        except Exception as exc:  # noqa: BLE001
            job.append_log(f"[WARNING] Termination failed: {exc}")
        job.append_log("[CANCELLED] Process terminated.")

    # -- worker ----------------------------------------------------------

    def _run_loop(self) -> None:
        while True:
            with self._lock:
                if self._stopping:
                    return
                job_id = self._queue.popleft() if self._queue else None
            if job_id is None:
                self._wake.wait(timeout=0.5)
                self._wake.clear()
                continue

            job = self.get(job_id)
            if job is None:
                continue
            if job.cancel_requested:
                job.state = "cancelled"
                job.ended_at = time.time()
                continue
            self._run(job)

    def _run(self, job: Job) -> None:
        with self._lock:
            self._current = job
        job.started_at = time.time()

        if job.log_file:
            try:
                job._sink = open(job.log_file, "w", encoding="utf-8", errors="replace")
            except OSError:
                job._sink = None

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8:replace"
        env["PYTHONUNBUFFERED"] = "1"
        # A native fault (SIGSEGV/SIGFPE) kills the child with no traceback.
        # faulthandler dumps the Python stack on the way down, which is the
        # difference between "crashed somewhere" and a named call site.
        env["PYTHONFAULTHANDLER"] = "1"

        job.append_log(f"[WEB] {job.command_display}")

        try:
            proc = subprocess.Popen(
                job.argv,
                cwd=str(REPO_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                bufsize=1,
                universal_newlines=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                creationflags=_CREATE_NO_WINDOW,
            )
        except Exception as exc:  # noqa: BLE001
            job.state = "failed"
            job.error = f"Failed to start process: {exc}"
            job.ended_at = time.time()
            job.append_log(f"[ERROR] {job.error}")
            self._finish(job)
            return

        job._proc = proc
        job.pid = proc.pid
        # Publish "running" only once the process exists. Flipping the state
        # before Popen returns lets a cancel arriving in that window find
        # _proc is None and silently do nothing, leaving the job to run out.
        job.state = "running"
        if job.cancel_requested:
            self._terminate(job)

        try:
            if proc.stdout is not None:
                for raw in proc.stdout:
                    for record in split_stream_line(raw):
                        job.append_log(record)
                        try:
                            job.tracker.feed(normalize(record))
                        except Exception:  # noqa: BLE001 - parsing never kills a job
                            pass
            proc.wait()
        except Exception as exc:  # noqa: BLE001
            job.append_log(f"[ERROR] While reading output: {exc}")
        finally:
            job.exit_code = proc.returncode
            job.ended_at = time.time()

        if job.cancel_requested:
            job.state = "cancelled"
        elif proc.returncode == 0:
            job.state = "completed"
            job.tracker.mark_complete()
        else:
            job.state = "failed"
            job.error = job.tracker.last_error or describe_exit_code(proc.returncode)

        self._sweep_outputs(job)
        self._finish(job)

    def _finish(self, job: Job) -> None:
        if job._sink is not None:
            try:
                job._sink.close()
            except OSError:
                pass
            job._sink = None
        job._proc = None
        with self._lock:
            if self._current is job:
                self._current = None
        self._wake.set()

    def _sweep_outputs(self, job: Job) -> None:
        """
        Backstop for output discovery.

        Flow 2 gets its path from the bare stdout line
        (whisperjav/translate/cli.py:849). Flows 1 and 3 write to output_dir,
        so scan it for anything newer than the job start.
        """
        out_dir = job.output_dir
        if not out_dir or job.started_at is None or not os.path.isdir(out_dir):
            return
        try:
            with os.scandir(out_dir) as it:
                for entry in it:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    if not entry.name.lower().endswith((".srt", ".vtt")):
                        continue
                    if entry.stat().st_mtime >= job.started_at - 1:
                        job.tracker._add_output(entry.path)
        except OSError:
            pass
