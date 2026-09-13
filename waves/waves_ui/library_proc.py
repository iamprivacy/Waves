"""The app's side of the library scanner process (waves.library_worker).

One long-lived child, spawned on the first job and kept for the session,
runs the scan and the by-name probes so they never hold the app's
interpreter. Jobs are sequential: the bridge's scan machinery already
serialises them (one scan at a time, one probe drainer). Cancel is a kill:
the child keeps no state between jobs, and the cache is a WAL sqlite file
that a killed writer leaves consistent.

Failure is never fatal: a child that cannot be spawned, or that dies twice,
hands the job back and the bridge runs it in-process exactly as before,
once at INFO.
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import subprocess
import sys
import threading

from waves.library_index import READ_GAUGE, WALK_GAUGE

from . import proc

logger = logging.getLogger("waves.library")

_QUIT_WAIT_S = 2.0
_MAX_CRASHES = 2
_POLL_S = 0.25
# Lines of child stderr that reach the log before the rest is suppressed. A
# failing child says why in its first few lines; one stuck in a warning loop
# would otherwise flood the breadcrumb ring a crash report is stitched from.
_STDERR_LINE_CAP = 40


def default_command() -> list[str]:
    """How to start the worker: the frozen app re-executes its own binary
    with the flag (a Nuitka bundle has no python binary, and
    ``sys.executable`` there is a phantom), a source run uses the venv's
    interpreter with the module."""
    from . import updater

    if updater.is_frozen():
        return [str(updater._current_exe()), "--library-worker"]
    return [sys.executable, "-m", "waves.library_worker"]


def _source_root() -> str | None:
    """The checkout root for a source run, so ``-m waves.library_worker``
    resolves whatever the app's own working directory is."""
    try:
        import waves

        return os.path.dirname(os.path.dirname(os.path.abspath(waves.__file__)))
    except Exception:
        return None


class WorkerFailed(Exception):
    """The child could not take or finish the job; run it in-process."""


class LibraryWorker:
    def __init__(self, command: list[str] | None = None, *, on_log=None, spawn=subprocess.Popen) -> None:
        self._command = command
        self._on_log = on_log
        self._spawn = spawn
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._results: queue.Queue = queue.Queue()
        self._job_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._next_id = 1
        self._current_id: int | None = None
        self._crashes = 0
        self._disabled = False
        self._said_fallback = False
        # Set by cancel(), cleared when the next job spawns a child. A cancel
        # kills the child, so the waiting job reads the same EOF a crash
        # produces; without this flag a user changing the library folder
        # twice looked like two crashes and retired the scanner for the
        # session, with the log claiming it had died.
        self._stopping = False

    # ----- lifecycle -------------------------------------------------------
    @property
    def disabled(self) -> bool:
        return self._disabled

    def _log_fallback(self, why: str) -> None:
        if not self._said_fallback:
            self._said_fallback = True
            logger.info("library scanner process unavailable (%s); scanning in-process", why)

    def _ensure(self) -> subprocess.Popen:
        with self._state_lock:
            if self._disabled:
                raise WorkerFailed("disabled")
            if self._proc is not None and self._proc.poll() is None:
                return self._proc
            command = list(self._command or default_command())
            env = dict(os.environ)
            env["WAVES_LIBRARY_WORKER"] = "1"
            cwd = None if self._command else _source_root()
            try:
                self._proc = self._spawn(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    # Captured, not discarded: a child that dies before it can
                    # speak the protocol (a missing module in a frozen build,
                    # say) says why here and nowhere else. Drained on its own
                    # thread below so a chatty child can never fill the pipe
                    # and wedge itself.
                    stderr=subprocess.PIPE,
                    env=env,
                    cwd=cwd,
                    creationflags=proc.NO_WINDOW,
                )
            except (OSError, ValueError) as exc:
                self._disabled = True
                self._log_fallback(f"{type(exc).__name__}")
                raise WorkerFailed(str(exc)) from exc
            self._results = queue.Queue()
            self._stopping = False
            self._reader = threading.Thread(
                target=self._read, args=(self._proc,), name="waves-library-worker", daemon=True
            )
            self._reader.start()
            threading.Thread(
                target=self._read_stderr,
                args=(self._proc,),
                name="waves-library-worker-stderr",
                daemon=True,
            ).start()
            logger.info("library scanner process started")
            return self._proc

    def _read_stderr(self, process: subprocess.Popen) -> None:
        """Drain the child's stderr into this process's log. Anything here is
        something the protocol could not carry: a traceback, an import error,
        a warning from a library the child loaded. Capped, because a child
        looping on a warning must not fill the crash trail."""
        stream = process.stderr
        if stream is None:
            return
        said = 0
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip()
                if not line:
                    continue
                said += 1
                if said <= _STDERR_LINE_CAP:
                    logger.warning("[scanner] %s", line)
                elif said == _STDERR_LINE_CAP + 1:
                    logger.warning("[scanner] further output suppressed")
        except (OSError, ValueError):
            pass
        finally:
            with contextlib.suppress(Exception):
                stream.close()

    def _read(self, process: subprocess.Popen) -> None:
        import json

        stream = process.stdout
        try:
            for raw in stream:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                ev = msg.get("ev")
                if ev == "log":
                    self._relay_log(msg)
                elif ev in ("progress", "done", "probe_done", "error"):
                    # Progress goes on the queue like every other reply rather
                    # than being handled here. The scan's handler republishes
                    # both presence indexes on a committed flush, and running
                    # that on THIS thread stopped the pipe being drained for
                    # its whole duration, so the child blocked on its next
                    # write and the scan waited for the app. The thread that
                    # asked for the scan is parked on the queue below with
                    # nothing else to do, which is where the work belongs.
                    self._results.put(msg)
                # "ready" and anything unknown: nothing to do
        except (OSError, ValueError):
            pass
        finally:
            # The stream closed: the child is gone. Whatever job was waiting
            # gets told.
            self._results.put({"ev": "exit"})

    @staticmethod
    def _mirror_gauges(gauges) -> None:
        """Take the scanner's walk and read saturation onto this process's
        gauges, which the perf sampler reads. The work moved to the child, so
        without this the two busiest pools in the app report idle through
        every scan (and the verbose report quietly says nothing happened).
        Three integers each, nothing about the library."""
        if not isinstance(gauges, dict):
            return
        for name, gauge in (("walk", WALK_GAUGE), ("read", READ_GAUGE)):
            reading = gauges.get(name)
            if not isinstance(reading, list | tuple) or len(reading) != 3:
                continue
            try:
                gauge.mirror(int(reading[0]), int(reading[1]), int(reading[2]))
            except (TypeError, ValueError):
                continue

    def _relay_log(self, msg: dict) -> None:
        level = logging.getLevelName(str(msg.get("level", "INFO")))
        if not isinstance(level, int):
            level = logging.INFO
        name = str(msg.get("name") or "waves.library")
        if not name.startswith("waves"):
            name = "waves.library"
        logging.getLogger(name).log(level, "[scanner] %s", str(msg.get("msg", "")))
        if self._on_log is not None:
            with_exc = None
            try:
                self._on_log(level, name, str(msg.get("msg", "")))
            except Exception:
                with_exc = True
            if with_exc:
                logger.debug("log relay hook failed", exc_info=True)

    def _kill(self) -> None:
        with self._state_lock:
            process, self._proc = self._proc, None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=_QUIT_WAIT_S)
        except Exception:
            logger.debug("killing the scanner process failed", exc_info=True)
        for stream in (process.stdin, process.stdout, process.stderr):
            with contextlib.suppress(Exception):  # a pipe already gone
                if stream is not None:
                    stream.close()

    def cancel(self) -> None:
        """Abandon the running job (a library folder change): the child is
        killed; the next job spawns a fresh one. The kill is announced first
        so the job waiting on the child reads the EOF as a stop we asked for
        rather than as a crash."""
        with self._state_lock:
            self._stopping = True
        self._kill()

    def close(self) -> None:
        """Quit at app exit: ask an idle child nicely, kill a busy one.
        Announced like a cancel, for the same reason: a job still waiting
        reads the EOF, and a quit is not something to hold against the
        scanner on the next run.

        Only an IDLE child is asked. The child serves one job at a time on one
        thread, so a quit line sent while a scan is running is not read until
        that scan ends, and the polite wait then costs the quitting app the
        whole timeout with its window still on screen (shutdown runs on the
        GUI thread). A busy child is killed outright, which is what cancel
        already does and what the protocol is built for: no state between
        jobs, and a WAL cache a killed writer leaves consistent."""
        with self._state_lock:
            self._stopping = True
            process = self._proc
            busy = self._current_id is not None
        if process is None:
            return
        try:
            if not busy and process.poll() is None and process.stdin is not None:
                process.stdin.write(b'{"op":"quit"}\n')
                process.stdin.flush()
                process.wait(timeout=_QUIT_WAIT_S)
        except Exception:
            logger.debug("quitting the scanner process failed", exc_info=True)
        self._kill()

    # ----- jobs ------------------------------------------------------------
    def _run(self, job: dict, *, alive, on_progress=None, kinds: tuple[str, ...]) -> dict:
        """Send one job and wait for its reply, relaying progress meanwhile.
        Raises WorkerFailed when the child could not be used (spawn failure,
        a crash mid-job), and returns None when ``alive`` turned false (the
        job was superseded; the child is killed)."""
        with self._job_lock:
            process = self._ensure()
            with self._state_lock:
                job_id = self._next_id
                self._next_id += 1
                self._current_id = job_id
            self._send(process, dict(job, id=job_id))
            try:
                while True:
                    if alive is not None and not alive():
                        self._kill()
                        return None
                    try:
                        msg = self._results.get(timeout=_POLL_S)
                    except queue.Empty:
                        continue
                    if msg.get("ev") == "exit":
                        if self._stop_was_asked(alive):
                            return None
                        self._crashed("exit")
                        raise WorkerFailed("exit")
                    if msg.get("id") != job_id:
                        continue
                    if msg.get("ev") == "progress":
                        self._hand_progress(msg, on_progress)
                        continue
                    if msg.get("ev") == "error":
                        # The child's own report of a failed job: the process
                        # is healthy, so this is not a crash, but it is the
                        # one path that silently sends the whole scan back
                        # in-process, so it is said out loud.
                        logger.warning("the library scanner refused a job: %s", msg.get("message", "error"))
                        raise WorkerFailed(str(msg.get("message", "error")))
                    if msg.get("ev") in kinds:
                        with self._state_lock:
                            self._crashes = 0
                        return msg
            finally:
                with self._state_lock:
                    self._current_id = None

    def _send(self, process: subprocess.Popen, job: dict) -> None:
        """One job down the pipe. A write that fails is the child already
        gone (it died between _ensure and here), which is a crash: the pipe
        is the only way to reach it, so there is nothing to retry."""
        import json

        try:
            process.stdin.write((json.dumps(job, ensure_ascii=False) + "\n").encode("utf-8"))
            process.stdin.flush()
        except (OSError, ValueError) as exc:
            self._crashed("write")
            raise WorkerFailed(str(exc)) from exc

    def _stop_was_asked(self, alive) -> bool:
        """Whether an EOF from the child is one this side asked for. A cancel
        and a crash both arrive that way, and only the unasked-for one may
        count against the scanner, or the feature retires itself on ordinary
        use (two library folder changes did exactly that)."""
        with self._state_lock:
            if self._stopping:
                return True
        return alive is not None and not alive()

    def _hand_progress(self, msg: dict, on_progress) -> None:
        """Mirror the scan's gauges and give its handler one progress
        snapshot, on the thread that asked for the scan rather than on the
        pipe reader (see _read)."""
        self._mirror_gauges(msg.get("gauges"))
        if on_progress is None:
            return
        try:
            on_progress(msg.get("event") or {})
        except Exception:
            logger.debug("scan progress handler failed", exc_info=True)

    def _crashed(self, how: str) -> None:
        self._kill()
        self._crashes += 1
        if self._crashes >= _MAX_CRASHES:
            self._disabled = True
            self._log_fallback(f"died {self._crashes} times, last on {how}")

    def run_scan(self, job: dict, *, alive, on_progress) -> dict | None:
        return self._run(dict(job, op="scan"), alive=alive, on_progress=on_progress, kinds=("done",))

    def run_probe(self, job: dict, *, alive) -> dict | None:
        """None, at once, while a scan holds the child: the same "never
        asked" answer probe_folders gives for a cache a scan is walking, so
        the bridge defers the names to the publish that follows."""
        if self._job_lock.locked():
            return None
        return self._run(dict(job, op="probe"), alive=alive, kinds=("probe_done",))
