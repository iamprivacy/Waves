"""The library scanner as its own process.

The scan of the music folder (the directory walk, the tag reads, the sqlite
writes, the by-name probes of an untrusted share and its fresh-mount relist)
used to run on pool threads inside the app. Those threads share one
interpreter with the GUI thread, and every QML binding that reaches the
bridge, every queued signal and every timer slot waited its turn for it
behind the walk: the launch water dropped frames for the whole sweep
(probed live, 2026-09-01 and again 2026-09-11). Deferring the sweep until
after the launch look only moved the stutter later.

Here the scan runs in a child process with an interpreter of its own. The
app's process only ever READS the cache (see LibraryIndex.presence_facts);
this process is the one writer. The two talk over pipes, one JSON object per
line:

  in  {"op": "scan", "id": N, "cache": PATH, "root": DIR, "force_full": B,
       "root_is_local": B|null, "config_dir": DIR, "recover": B, "lock_wait": S}
      {"op": "probe", "id": N, "cache": PATH, "root": DIR, "names": [..],
       "spellings": {name: [..]}}
      {"op": "quit"}
  out {"ev": "ready"}
      {"ev": "progress", "id": N, "event": {..}}     the scanner's own sink
      {"ev": "done", "id": N, "count": C, "status": S, "partial": B,
       "shape": [held, distinct], "reconciled": B, "recovered": R}
      {"ev": "probe_done", "id": N, "found": C|null}
      {"ev": "error", "id": N, "message": M}
      {"ev": "log", "level": L, "name": LOGGER, "msg": M}

The parent kills this process to cancel a job (a library folder change), so
a job never has to poll for supersession; EOF on stdin (the parent gone)
ends it. Nothing here imports Qt: the entry points check for
``--library-worker`` before the app's own imports (waves.py, waves_ui's
__main__), and the source launcher is ``python -m waves.library_worker``.

Only protocol goes to the real stdout. sys.stdout is pointed at stderr as
the first thing, so a stray print anywhere below cannot corrupt the stream.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
from collections.abc import Callable

from waves.library_index import READ_GAUGE, WALK_GAUGE, LibraryIndex
from waves.library_recover import recover_untrusted

logger = logging.getLogger("waves.library.worker")


class _EventWriter:
    """Serialised JSON lines on the protocol stream."""

    def __init__(self, stream) -> None:
        self._stream = stream
        self._lock = threading.Lock()

    def send(self, payload: dict) -> None:
        line = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self._lock:
            try:
                self._stream.write(line)
                self._stream.flush()
            except (BrokenPipeError, OSError, ValueError):
                # The parent is gone: nothing to tell, and nothing to do but
                # finish quietly (the stdin EOF ends the loop).
                pass


class _LogForwarder(logging.Handler):
    """Every INFO+ record the scanner logs travels to the parent as a log
    event, where it is logged again under the same name through the app's
    own redacting handlers, so the crash trail keeps the scan's breadcrumbs."""

    def __init__(self, writer: _EventWriter) -> None:
        super().__init__(level=logging.INFO)
        self._writer = writer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:
            return
        self._writer.send({"ev": "log", "level": record.levelname, "name": record.name, "msg": msg})


class _Worker:
    def __init__(self, writer: _EventWriter, alive: Callable[[], bool] = lambda: True) -> None:
        # A job's continue rule: False once the parent is gone (stdin EOF),
        # so a walk of a large share never outlives the app it served.
        self._alive = alive
        self._writer = writer
        self._libs: dict[str, LibraryIndex] = {}

    def _lib(self, path: str) -> LibraryIndex:
        lib = self._libs.get(path)
        if lib is None:
            lib = LibraryIndex(path)
            self._libs[path] = lib
        return lib

    def close(self) -> None:
        for lib in self._libs.values():
            try:
                lib.close()
            except Exception:
                logger.debug("closing the scan cache failed", exc_info=True)
        self._libs.clear()

    def scan(self, job: dict) -> None:
        job_id = job.get("id")
        lib = self._lib(str(job["cache"]))
        root = str(job.get("root") or "")
        alive = self._alive

        def on_progress(event: dict) -> None:
            # The walk and read gauges live in whichever process does the
            # scanning, so their readings ride along with the progress the
            # app already asks for and the app mirrors them onto its own.
            # Integers only, nothing about the library (see waves.poolgauge).
            self._writer.send(
                {
                    "ev": "progress",
                    "id": job_id,
                    "event": dict(event),
                    "gauges": {
                        name: [g.activeThreadCount(), g.maxThreadCount(), g.peak]
                        for name, g in (("walk", WALK_GAUGE), ("read", READ_GAUGE))
                    },
                }
            )

        # The keys of rows older than the key columns are filled by refresh()
        # itself, so the in-process fallback gets them too and a quit stops
        # the backfill the same way it stops the walk.
        count = lib.refresh(
            root,
            should_continue=alive,
            on_progress=on_progress,
            force_full=bool(job.get("force_full", False)),
            root_is_local=job.get("root_is_local"),
        )
        recovered = 0
        if job.get("recover", True):
            recovered = recover_untrusted(
                lib,
                root,
                str(job.get("config_dir") or os.path.dirname(str(job["cache"]))),
                alive,
                on_progress,
                lock_wait=float(job.get("lock_wait", 20.0)),
            )
        shape = lib.untrusted_listing_shape()
        self._writer.send(
            {
                "ev": "done",
                "id": job_id,
                "count": int(count or 0),
                "status": str(lib.last_scan_status),
                "partial": bool(lib.last_scan_partial),
                "shape": [int(shape[0]), int(shape[1])],
                "reconciled": bool(lib.last_listing_reconciled),
                "recovered": int(recovered),
            }
        )

    def probe(self, job: dict) -> None:
        job_id = job.get("id")
        lib = self._lib(str(job["cache"]))
        spellings = {str(k): [str(x) for x in v] for k, v in (job.get("spellings") or {}).items()}
        names = [str(n) for n in job.get("names") or []]
        found = lib.probe_folders(
            str(job.get("root") or ""),
            names,
            self._alive,
            candidates=lambda name: tuple(spellings.get(name) or (name,)),
            timeout=float(job.get("timeout", 20.0)),
        )
        self._writer.send({"ev": "probe_done", "id": job_id, "found": None if found is None else int(found)})


def _serve_one(worker: _Worker, writer: _EventWriter, raw: bytes) -> bool:
    """Run the job on one protocol line; False when it was the quit."""
    line = raw.strip()
    if not line:
        return True
    try:
        job = json.loads(line)
    except ValueError:
        job = None
    if not isinstance(job, dict):
        writer.send({"ev": "error", "id": None, "message": "unreadable job"})
        return True
    op = job.get("op")
    if op == "quit":
        return False
    try:
        if op == "scan":
            worker.scan(job)
        elif op == "probe":
            worker.probe(job)
        else:
            writer.send({"ev": "error", "id": job.get("id"), "message": f"unknown op {op!r}"})
    except Exception as exc:  # the job failed; the process serves on
        logger.debug("library worker job failed", exc_info=True)
        writer.send({"ev": "error", "id": job.get("id"), "message": f"{type(exc).__name__}: {exc}"})
    return True


def serve(stdin, stdout) -> int:
    """Serve jobs from ``stdin`` (a binary line stream) until EOF or quit."""
    writer = _EventWriter(stdout)
    root_logger = logging.getLogger("waves")
    root_logger.setLevel(logging.INFO)
    forwarder = _LogForwarder(writer)
    root_logger.addHandler(forwarder)
    # Our own records leave by the protocol and are logged again on the far
    # side, so they must not also reach the stderr handler main() installs:
    # the app reads that stream too, and every scan line would arrive twice.
    # What stays on stderr is what the protocol cannot carry, which is the
    # point of keeping it: tracebacks, import failures, other libraries.
    propagated, root_logger.propagate = root_logger.propagate, False
    # The jobs arrive through a pump thread so that EOF (the parent gone,
    # cleanly or not) is noticed WHILE a job runs: the job's continue rule
    # reads the flag, and a scan of a large share stops within its next
    # check instead of running on as an orphan for minutes.
    jobs: queue.Queue = queue.Queue()
    gone = threading.Event()

    def pump() -> None:
        try:
            for raw in stdin:
                jobs.put(raw)
        except (OSError, ValueError):
            pass
        finally:
            gone.set()
            jobs.put(None)

    threading.Thread(target=pump, name="library-worker-stdin", daemon=True).start()
    worker = _Worker(writer, alive=lambda: not gone.is_set())
    writer.send({"ev": "ready"})
    try:
        while True:
            raw = jobs.get()
            if raw is None:
                break
            if not _serve_one(worker, writer, raw):
                break
    finally:
        worker.close()
        root_logger.removeHandler(forwarder)
        root_logger.propagate = propagated
    return 0


def main(argv=None) -> int:
    # The protocol owns the real stdout; everything else goes to stderr.
    out = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    sys.stdout = sys.stderr
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    code = serve(sys.stdin.buffer, out)
    # Leave WITHOUT finalising the interpreter. The stdin pump is a daemon
    # thread parked inside a buffered read, so it holds that reader's lock,
    # and finalisation trying to close the reader aborts the process:
    #   Fatal Python error: _enter_buffered_busy: could not acquire lock
    # Every polite quit ended that way, which the parent now reads on the
    # child's stderr and files in the crash trail of an ordinary app exit.
    # Nothing is left to flush: the protocol stream is unbuffered and flushed
    # per line, and the cache is closed in serve's finally.
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
