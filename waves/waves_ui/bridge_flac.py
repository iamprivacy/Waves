"""The FLAC signature check (Settings, FFmpeg & FLAC section).

A FLAC file whose header carries no audio signature fails ``flac -t`` and is
refused by strict players even though its audio is intact (see
``waves.flac_verify`` for the format detail and the three guarantees). This
mixin is the Qt layer over that module: a scan of the user's folders on a
worker thread, a fix over what the scan found, a CSV report of every file
after each, and the state the Settings card renders.

Scope, chosen on the card and remembered as Waves prefs:

* ``flac_check_source == "waves"`` (the default): the Waves library, meaning
  the download folder plus the library folder when the library scan is on
  and configured (the same two folders the ownership badge may look at).
* ``flac_check_source == "folder"``: the one folder in ``flac_check_folder``,
  which only ever comes from the system folder dialog on the card (the user
  asked for this tool to reach a folder of their choosing; no other check in
  the app does).

Nothing else is ever walked, and a link that points outside the chosen
folders is skipped by the walk. A fix acts only on the rows of the last scan,
and only while the selection still names the folders that scan walked.

A fix that stops before it finishes (the app quit or crashed, or the user
canceled) is remembered in the ``flac_check_resume`` pref, the scope key of
the folders it was writing in. RESUME on the card (through Main.qml's
confirmation, like FIX) selects those folders again, scans them, and fixes
what the scan finds: a file the earlier fix signed reads as signed, so the
run picks up where the other left off. Never started on its own: a fix that
crashed the app would otherwise crash it again on every launch. The marker
is dropped only once every folder the unfinished fix could have written in
was actually walked: a scan or fix that could not open one of them (a share
not mounted, a drive unplugged, a folder the system will not let Waves
list) keeps it. A folder the scan behind that fix could not read either
(a ``lost+found`` at a drive's root, say) never held its files, so it does
not hold the marker; once the app has restarted that scan is not known, and
only a selected folder that cannot be opened keeps it.

A folder that cannot be opened is never mistaken for an empty one. When
none of the selected folders can be opened the run fails and says so; when
some can, it finishes over those and the state's ``folders_unreadable``
says how many folders (selected ones and subfolders) were not read, and the
report lists them.

Threading: the slots run on the GUI thread and only move state there. The
work runs on the bridge's pool, posts progress and its outcome through
``_flacCheckEvent`` (queued, so the handler runs on the GUI thread), and
each run carries a generation stamp so a stale worker's events are dropped.
One run at a time: a slot called while one is running is a no-op.

A mixin over WavesBridge like ``bridge_library``; this module must never
import backend.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import sys
import threading
import time

from PySide6.QtCore import Signal, Slot

from waves import flac_verify
from waves.waves_ui.ffmpeg_manager import working_system_ffmpeg
from waves.worker import Worker

from . import diagnostics

logger = logging.getLogger("waves.flac")

# Progress events are rate-limited to one per this many seconds: a header
# scan reads thousands of files a second, and each event re-renders a card.
_PROGRESS_MIN_S = 0.25
# The time left is offered once a run has been going this long: before that
# the pace is one or two files, and one long track would skew it.
_ETA_MIN_S = 5.0
# Reports go under the config folder, next to the logs, never into a music
# folder (the factory wipe knows this folder; see backend._FACTORY_WIPE_SUBDIRS).
REPORTS_DIR = "reports"


def _idle_state() -> dict:
    return {
        "phase": "idle",  # idle | scanning | fixing | done | canceled | failed
        "running": False,
        "mode": "header",  # header | deep (what the last scan did)
        "last": "",  # scan | fix: which run the counts describe
        "files": 0,  # files worked through so far (a fix: candidates only)
        "total": 0,  # files the run works through; 0 while a scan is still walking the folders
        "found": 0,  # files the walk has found so far (a scan, before total is known)
        "eta": -1,  # seconds left at the pace so far; -1 while there is no pace to go by
        "counts": {},  # status -> count (waves.flac_verify statuses)
        "fixable": 0,  # files a fix would act on (UNSET rows of the last scan)
        "report": "",  # path of the last report written ("" = none)
        "roots": 0,  # folders walked
        "ffmpeg": False,  # a decoder was available when the run started
        "error": "",
        "scope": "",  # the folders the last scan walked, as a key (see _flac_check_scope)
        # Folders (selected or below) the last scan could not open or list:
        # their files were not checked. A fix carries its scan's figure.
        "folders_unreadable": 0,
        # Files whose verdict an earlier scan had already found (unchanged
        # since, so not read again; see flac_verify.VerdictCache).
        "cached": 0,
    }


SOURCE_WAVES = "waves"
SOURCE_FOLDER = "folder"


def _time_left(elapsed: float, done: float, total: float) -> int:
    """Seconds left at the pace so far, or -1 until there is a pace to go by."""
    if total <= 0 or done <= 0 or done >= total or elapsed < _ETA_MIN_S:
        return -1
    return round(elapsed * (total - done) / done)


def _scope_roots(key: str) -> list[str]:
    """The folders a scope key names (see FlacCheckMixin._flac_check_scope)."""
    source, _sep, rest = key.partition(":")
    if source == SOURCE_FOLDER:
        return [rest] if rest else []
    return [r for r in rest.split("\n") if r]


def _under_any(folder: str, parents: list[str]) -> bool:
    folder = os.path.normpath(folder)
    for parent in parents:
        parent = os.path.normpath(parent)
        if folder == parent or folder.startswith(parent.rstrip(os.sep) + os.sep):
            return True
    return False


def _unopened(chosen: bool, roots: int) -> str:
    """What the card says when none of the selected folders could be opened."""
    if chosen:
        return "The chosen folder could not be opened."
    if roots == 1:
        return "The Waves library folder could not be opened."
    return "The Waves library folders could not be opened."


class _Progress:
    """What a running scan or fix posts to the card: counts per status, the
    files worked through out of the total, and the time left. Rate-limited.

    The time left is the elapsed time scaled by the work still to do. A scan
    weighs every file the same; a fix weighs each by its size (``sizes``),
    since a long hi-res track takes many times what a short one does."""

    def __init__(self, post, total: int = 0, sizes: dict[str, int] | None = None) -> None:
        self._post = post
        self._sizes = sizes
        self._counts: dict[str, int] = {}
        self._seen = 0
        self._done = 0
        self._total = sum(sizes.values()) if sizes else total
        self._started = time.monotonic()
        self._last = 0.0

    def _due(self) -> bool:
        now = time.monotonic()
        if now - self._last < _PROGRESS_MIN_S:
            return False
        self._last = now
        return True

    def on_walk(self, found: int, done: bool) -> None:
        """The scan's walk: files found so far, then the total. The time left
        counts from the end of the walk, the first file read."""
        if done:
            self._total = found
            self._started = time.monotonic()
            self._post({"found": found, "total": found})
        elif self._due():
            self._post({"found": found})

    def on_row(self, row: flac_verify.Row) -> None:
        self._seen += 1
        self._counts[row.status] = self._counts.get(row.status, 0) + 1
        self._done += self._sizes.get(row.path, 0) if self._sizes else 1
        if row.status == flac_verify.SEALED:
            logger.info("FLAC check: signature written: %s", diagnostics.content(os.path.basename(row.path)))
        elif row.status in (
            flac_verify.MISMATCH,
            flac_verify.LENGTH_DIFFERS,
            flac_verify.WRITE_FAILED,
            flac_verify.DECODE_FAILED,
        ):
            logger.warning(
                "FLAC check: %s: %s",
                flac_verify.STATUS_LABELS[row.status],
                diagnostics.content(os.path.basename(row.path)),
            )
        if self._due():
            eta = _time_left(time.monotonic() - self._started, self._done, self._total)
            self._post({"files": self._seen, "counts": dict(self._counts), "eta": eta})


class FlacCheckMixin:
    # The check's state moved (started, progressed, finished); Settings re-reads flacCheckState().
    flacCheckChanged = Signal()
    _flacCheckEvent = Signal(int, dict)  # internal: (generation, state fields) posted by the worker

    def _flac_check_init(self) -> None:
        """Create the family's state (called from WavesBridge.__init__)."""
        self._flac_check_gen = 0
        self._flac_check_cancel = threading.Event()
        # Clear while a scan holds the remembered verdicts open: a factory
        # reset waits on it so the wipe can take the file (Windows refuses
        # to delete a file that is open).
        self._flac_check_cache_free = threading.Event()
        self._flac_check_cache_free.set()
        self._flac_check_rows: list[flac_verify.Row] = []
        self._flac_check_unreadable: list[str] = []  # the folders the scan behind the rows could not read
        self._flac_check_result: tuple[int, list[flac_verify.Row], list[str]] = (0, [], [])
        self._flac_check_state = _idle_state()
        self._flac_check_resuming = False  # a RESUME's scan is on; its outcome starts the fix
        # The running fix inherits an unfinished fix's marker: finishing it
        # does not finish what the other left in a folder this one missed.
        self._flac_check_keep_resume = False
        # The folders the scan behind the marker's fix could not read (that
        # fix never wrote there); None when the marker predates this launch.
        self._flac_check_resume_unread: list[str] | None = None
        self._flacCheckEvent.connect(self._on_flac_check_event)

    # ---- what the card reads ---------------------------------------------

    @Slot(result="QVariant")
    def flacCheckState(self) -> dict:
        """The check's current state, a plain dict (see _idle_state), plus
        the fix RESUME would pick up: ``resume_source`` ("waves", "folder",
        or "" when there is none) and ``resume_folders``, and ``reports``:
        whether any report is on disk for VIEW REPORTS to show."""
        state = dict(self._flac_check_state)
        state["reports"] = flac_verify.has_report(self._flac_check_report_dir())
        state["counts"] = dict(state.get("counts") or {})
        target = self._flac_check_resume_target()
        state["resume_source"] = target[0] if target else ""
        state["resume_folders"] = list(target[2]) if target else []
        return state

    # ---- the folders and the decoder -------------------------------------

    def _flac_check_prefs(self) -> dict:
        # getattr: partial test stubs drive the mixin without the prefs store.
        return getattr(self, "_waves_prefs", None) or {}

    def _flac_check_waves_roots(self) -> list[str]:
        """The Waves library: the download folder plus the library folder when
        one is configured and the library scan is on."""
        roots = [(self.settings.data.download_base_path or "").strip()]
        library_root = getattr(self, "_library_root", None)
        lib = (library_root() if library_root is not None else "") or ""
        if lib:
            roots.append(lib)
        return flac_verify.effective_roots(roots)

    def _flac_check_scope(self) -> tuple[str, str, list[str], str]:
        """What the card's selection names: (source, scope key, roots, error).
        The key names the folders exactly, so a scan's rows can be matched to
        the selection later; ``error`` is set (and roots empty) when there is
        nothing to walk. Nothing is stat'ed here (GUI thread)."""
        prefs = self._flac_check_prefs()
        if prefs.get("flac_check_source") == SOURCE_FOLDER:
            folder = str(prefs.get("flac_check_folder") or "").strip()
            if not folder or not os.path.isabs(folder):
                return SOURCE_FOLDER, "", [], "Choose a folder first."
            return SOURCE_FOLDER, f"{SOURCE_FOLDER}:{folder}", [folder], ""
        roots = self._flac_check_waves_roots()
        if not roots:
            return SOURCE_WAVES, "", [], "Set a download folder first."
        return SOURCE_WAVES, f"{SOURCE_WAVES}:" + "\n".join(roots), roots, ""

    def _flac_check_resume_target(self) -> tuple[str, str, list[str]] | None:
        """The fix that stopped before it finished, as (source, chosen folder,
        folders), or None. A Waves library fix whose folders have moved since
        is not offered: a scan of the new folders is a new start, not a
        resume. Nothing is stat'ed here (GUI thread)."""
        marker = str(self._flac_check_prefs().get("flac_check_resume") or "")
        source, _sep, rest = marker.partition(":")
        if source == SOURCE_FOLDER and os.path.isabs(rest):
            return SOURCE_FOLDER, rest, [rest]
        if source == SOURCE_WAVES:
            roots = self._flac_check_waves_roots()
            if roots and marker == f"{SOURCE_WAVES}:" + "\n".join(roots):
                return SOURCE_WAVES, "", roots
        return None

    def _flac_check_ffmpeg(self) -> str:
        """The decoder the fix and the deep check use: the configured or managed
        ffmpeg, else one on PATH; "" when there is none."""
        resolve = getattr(self, "_resolve_ffmpeg", None)
        if resolve is not None:
            with contextlib.suppress(Exception):
                resolve()
        path = (getattr(self.settings.data, "path_binary_ffmpeg", "") or "").strip() or working_system_ffmpeg(
            retry_slow=True
        )
        return path if path and os.path.isfile(path) else ""

    def _flac_check_report_dir(self) -> str:
        return os.path.join(os.path.dirname(self.settings.file_path), REPORTS_DIR)

    # ---- the folder choice (remembered) ----------------------------------

    @Slot(result="QVariant")
    def flacCheckSources(self) -> dict:
        """What the card's folder row shows: the selected source ("waves" or
        "folder"), the chosen folder, the scope key the selection names (the
        card compares it with flacCheckState().scope), and the Waves library
        folders a "waves" scan would walk."""
        source, key, _roots, _error = self._flac_check_scope()
        return {
            "source": source,
            "folder": str(self._flac_check_prefs().get("flac_check_folder") or ""),
            "key": key,
            "waves": self._flac_check_waves_roots(),
        }

    def _flac_check_set_pref(self, key: str, value: str) -> None:
        prefs = getattr(self, "_waves_prefs", None)
        if prefs is None or prefs.get(key) == value:
            return
        prefs[key] = value
        save = getattr(self, "_save_waves_prefs", None)
        if save is not None:
            save()
        self.flacCheckChanged.emit()

    @Slot(str)
    def flacCheckSetSource(self, source: str) -> None:
        """Choose what the next scan walks: "waves" (the Waves library) or
        "folder" (the folder picked with Browse). Ignored while a run is on."""
        if self._flac_check_state.get("running") or source not in (SOURCE_WAVES, SOURCE_FOLDER):
            return
        self._flac_check_set_pref("flac_check_source", source)

    @Slot(str)
    def flacCheckSetFolder(self, folder: str) -> None:
        """Remember the folder the system dialog returned and select it. Only
        an absolute path is taken; ignored while a run is on."""
        folder = (folder or "").strip()
        if self._flac_check_state.get("running") or not folder or not os.path.isabs(folder):
            return
        self._flac_check_set_pref("flac_check_folder", folder)
        self._flac_check_set_pref("flac_check_source", SOURCE_FOLDER)

    # ---- the slots (GUI thread) ------------------------------------------

    def _flac_check_begin(self, phase: str, **fields) -> int:
        """Stamp a new run, clear the cancel flag and publish its start."""
        self._flac_check_gen += 1
        self._flac_check_cancel.clear()
        self._flac_check_state.update(_idle_state() | {"phase": phase, "running": True} | fields)
        self.flacCheckChanged.emit()
        return self._flac_check_gen

    def _flac_check_fail(self, error: str) -> None:
        self._flac_check_state.update({"phase": "failed", "running": False, "error": error})
        self.flacCheckChanged.emit()

    @Slot(bool)
    @Slot(bool, bool)
    def flacCheckScan(self, deep: bool = False, recheck: bool = False) -> None:
        """Walk the two folders and read every FLAC header (``deep`` also
        decodes each file and compares). A file unchanged since an earlier
        scan takes that scan's verdict unless ``recheck`` asks for every file
        to be read again. Reads only; writes one CSV report under the config
        folder when done."""
        if self._flac_check_state.get("running"):
            return
        source, key, roots, error = self._flac_check_scope()
        if error:
            self._flac_check_fail(error)
            return
        ffmpeg = self._flac_check_ffmpeg()
        if deep and not ffmpeg:
            self._flac_check_fail("A deep check needs FFmpeg (see the Processing section).")
            return
        gen = self._flac_check_begin(
            "scanning", mode="deep" if deep else "header", roots=len(roots), ffmpeg=bool(ffmpeg), scope=key
        )
        self._flac_check_rows = []
        self._flac_check_unreadable = []
        logger.info(
            "FLAC check: %s scan of %d folder(s) started (%s%s)",
            "deep" if deep else "header",
            len(roots),
            "chosen folder" if source == SOURCE_FOLDER else "Waves library",
            ", every file read again" if recheck else "",
        )
        self.threadpool.start(
            Worker(self._flac_check_scan_job, gen, roots, ffmpeg, bool(deep), source == SOURCE_FOLDER, bool(recheck))
        )

    @Slot()
    def flacCheckFix(self) -> None:
        """Write the signature into the files the last scan found without one.
        Each is re-read, decoded and checked again right before its 34 header
        bytes are written (see waves.flac_verify.seal_file); nothing else in
        any file is touched, and a file with a signature is never rewritten."""
        if self._flac_check_state.get("running"):
            return
        rows = list(self._flac_check_rows)
        if not any(r.status == flac_verify.UNSET for r in rows):
            return
        # The rows belong to the folders the last scan walked; once the card
        # names other folders, writing into those rows is not what the user
        # is looking at, so the fix waits for a scan of the selection. Said
        # on the card (the emit also re-reads the selection there): a folder
        # changed elsewhere in Settings while the card was open would
        # otherwise leave FIX offered and silently doing nothing.
        _source, key, _roots, _error = self._flac_check_scope()
        scanned = self._flac_check_state.get("scope") or ""
        if not key or key != scanned:
            logger.info("FLAC check: fix skipped, the folder selection changed since the scan")
            self._flac_check_fail("The folders changed since the scan. Scan again first.")
            return
        ffmpeg = self._flac_check_ffmpeg()
        if not ffmpeg:
            self._flac_check_fail("Writing signatures needs FFmpeg (see the Processing section).")
            return
        candidates = sum(r.status == flac_verify.UNSET for r in rows)
        unreadable = list(getattr(self, "_flac_check_unreadable", []))
        # fixable stays the candidate count while the fix runs; the outcome
        # replaces it with what is left. total is what "Writing N of M" reads.
        gen = self._flac_check_begin(
            "fixing",
            mode=self._flac_check_state.get("mode", "header"),
            ffmpeg=True,
            fixable=candidates,
            total=candidates,
            scope=key,
            folders_unreadable=len(unreadable),
        )
        # Remembered until the fix finishes, so a quit or a crash part way
        # leaves RESUME on the card (cleared in _flac_check_settled). An
        # unfinished fix's marker outlives this one when the scan missed
        # folders that fix could have written in (see _flac_check_missed).
        prior = str(self._flac_check_prefs().get("flac_check_resume") or "")
        self._flac_check_keep_resume = prior == key
        if prior != key:
            self._flac_check_resume_unread = list(unreadable)
        self._flac_check_set_pref("flac_check_resume", key)
        logger.info("FLAC check: fix of %d file(s) started", candidates)
        self.threadpool.start(Worker(self._flac_check_fix_job, gen, rows, ffmpeg, candidates, unreadable))

    @Slot()
    def flacCheckResume(self) -> None:
        """Pick up the fix that stopped before it finished: select the folders
        it was writing in, scan them again, and fix what that scan finds (a
        file it already signed reads as signed). Main.qml's confirmation is
        the only caller. Nothing to resume, or a run on: a no-op."""
        if self._flac_check_state.get("running"):
            return
        target = self._flac_check_resume_target()
        if target is None:
            return
        source, folder, _roots = target
        if source == SOURCE_FOLDER:
            self._flac_check_set_pref("flac_check_folder", folder)
        self._flac_check_set_pref("flac_check_source", source)
        logger.info("FLAC check: resuming the last fix")
        self._flac_check_resuming = True
        self.flacCheckScan(False)
        if not self._flac_check_state.get("running"):
            # The scan was refused (no folder): it said so, and nothing follows.
            self._flac_check_resuming = False

    @Slot()
    def flacCheckCancel(self) -> None:
        """Stop the running scan or fix after the file it is on. A fix never
        leaves a file half-written: the header write is one 34-byte call."""
        if self._flac_check_state.get("running"):
            self._flac_check_cancel.set()

    @Slot()
    def flacCheckRevealReports(self) -> None:
        """Show the reports folder in the file manager, at any time (a scan
        may be running); nothing when no report has been written yet. On
        macOS Finder selects the newest report in it; elsewhere the folder
        opens (handing a report itself over would open it in a spreadsheet)."""
        folder = self._flac_check_report_dir()
        path = flac_verify.newest_report(folder)
        if path:
            self._reveal_in_file_manager(path if sys.platform == "darwin" else folder)

    # ---- the workers (pool thread) ---------------------------------------

    def _flac_check_post(self, gen: int, fields: dict) -> None:
        """Hand state fields to the GUI thread (dropped if the bridge is gone)."""
        self._emit_from_worker("_flacCheckEvent", gen, fields)

    def _flac_check_alive(self, gen: int):
        return lambda: not self._flac_check_cancel.is_set() and self._flac_check_gen == gen

    def _flac_check_progress(self, gen: int, total: int = 0, sizes: dict[str, int] | None = None) -> _Progress:
        """The run's progress poster (see _Progress)."""
        return _Progress(lambda fields: self._flac_check_post(gen, fields), total, sizes)

    def _flac_check_finish(
        self,
        gen: int,
        kind: str,
        rows: list[flac_verify.Row],
        cancelled: bool,
        files: int | None = None,
        unreadable: list[str] | None = None,
    ) -> None:
        """Write the report, park the rows for a fix, publish the outcome.
        ``files`` is what the run worked on (a fix: its candidates; a scan:
        every row), the number the card's note reports. ``unreadable`` is
        the folders the scan could not read (listed in the report)."""
        unreadable = list(unreadable or [])
        report = ""
        # No report once a factory reset is under way (it wipes the config
        # folder, and a report lists every file the run saw) or once a newer
        # run has taken over; the outcome is dropped at the event handler too.
        if getattr(self, "_factory_reset", False) or gen != self._flac_check_gen:
            logger.info("FLAC check: %s outcome dropped, the run was superseded", kind)
        else:
            try:
                report = flac_verify.write_new_report(self._flac_check_report_dir(), kind, rows, unreadable)
            except Exception:
                logger.exception("FLAC check: the report could not be written")
        counts = flac_verify.summarize(rows)
        total = counts.pop("files", 0)
        self._flac_check_result = (gen, rows, unreadable)
        logger.info(
            "FLAC check: %s %s: %d file(s), %d without a signature, %d written, %d mismatched, %d folder(s) unread",
            kind,
            "cancelled" if cancelled else "finished",
            total,
            counts.get(flac_verify.UNSET, 0),
            counts.get(flac_verify.SEALED, 0),
            counts.get(flac_verify.MISMATCH, 0),
            len(unreadable),
        )
        self._flac_check_post(
            gen,
            {
                "final": True,
                "phase": "cancelled" if cancelled else "done",
                "running": False,
                "last": kind,
                "files": total if files is None else files,
                "eta": -1,
                "counts": counts,
                "fixable": counts.get(flac_verify.UNSET, 0),
                "report": report,
                "folders_unreadable": len(unreadable),
                "cached": sum(r.cached for r in rows) if kind == "scan" else 0,
            },
        )

    def _flac_check_open_cache(self) -> flac_verify.VerdictCache | None:
        """(worker) What earlier scans found, or None: none during a factory
        reset (the wipe takes the file), and none when it cannot be opened
        (the scan then reads every file). Clears ``_flac_check_cache_free``
        BEFORE looking at the reset flag, so a reset either sees the cache
        busy and waits, or this sees the flag and opens nothing."""
        free = self._flac_check_cache_event()
        free.clear()
        cache = self._flac_check_open_cache_file()
        if cache is None:
            free.set()
        return cache

    def _flac_check_cache_event(self) -> threading.Event:
        free = getattr(self, "_flac_check_cache_free", None)
        if free is None:  # partial test stubs skip _flac_check_init
            free = self._flac_check_cache_free = threading.Event()
            free.set()
        return free

    def _flac_check_cache_path(self) -> str:
        return os.path.join(os.path.dirname(self.settings.file_path), flac_verify.CACHE_NAME)

    def _flac_check_close_cache(self, cache: flac_verify.VerdictCache | None) -> None:
        """(worker) Close the remembered verdicts and mark them free. The file
        is deleted when a read found it damaged (the next scan starts it
        over) or when a factory reset came while it was open."""
        try:
            if cache is None:
                return
            cache.close()
            reset = getattr(self, "_factory_reset", False)
            if cache.damaged or reset:
                if cache.damaged and not reset:
                    logger.warning("FLAC check: the remembered verdicts were damaged; starting them over")
                path = self._flac_check_cache_path()
                for suffix in ("", "-journal", "-wal", "-shm"):
                    with contextlib.suppress(OSError):
                        os.remove(path + suffix)
        finally:
            self._flac_check_cache_event().set()

    def _flac_check_open_cache_file(self) -> flac_verify.VerdictCache | None:
        if getattr(self, "_factory_reset", False):
            return None
        path = self._flac_check_cache_path()
        for attempt in (1, 2):
            try:
                return flac_verify.VerdictCache(path)
            except (sqlite3.Error, OSError) as exc:
                # "Not a database" (a crash mid-write, a bad sector): the file
                # only holds what earlier scans found, so it is started over
                # once. Any other failure (locked, a folder that cannot be
                # written) leaves it be.
                if attempt == 1 and type(exc) is sqlite3.DatabaseError:
                    logger.warning("FLAC check: the remembered verdicts were damaged; starting them over")
                    with contextlib.suppress(OSError):
                        os.remove(path)
                    continue
                logger.warning("FLAC check: the remembered verdicts could not be opened; every file is read this run")
                return None
        return None

    def _flac_check_scan_job(
        self, gen: int, roots: list[str], ffmpeg: str, deep: bool, chosen: bool = False, recheck: bool = False
    ) -> None:
        alive = self._flac_check_alive(gen)
        rows: list[flac_verify.Row] = []
        unreadable: list[str] = []
        cache = self._flac_check_open_cache()
        try:
            # Every folder is opened here, on the worker: a share that stopped
            # answering can hang the first look for the mount's timeout.
            progress = self._flac_check_progress(gen)
            rows = flac_verify.scan(
                roots,
                ffmpeg,
                deep=deep,
                should_continue=alive,
                on_row=progress.on_row,
                on_walk=progress.on_walk,
                unreadable=unreadable,
                cache=cache,
                recheck=recheck,
            )
        except Exception:
            logger.exception("FLAC check: the scan failed")
            self._flac_check_post(gen, {"phase": "failed", "running": False, "error": "The scan failed; see the log."})
            return
        finally:
            self._flac_check_close_cache(cache)
        if unreadable:
            logger.warning(
                "FLAC check: %d folder(s) could not be opened, their files were not checked", len(unreadable)
            )
        if roots and all(r in unreadable for r in roots) and alive():
            # Nothing could be walked (a drive unplugged, a share down, a
            # folder the system will not let Waves list): said so instead of
            # reporting an empty, clean scan.
            self._flac_check_post(gen, {"phase": "failed", "running": False, "error": _unopened(chosen, len(roots))})
            return
        self._flac_check_finish(gen, "scan", rows, cancelled=not alive(), unreadable=unreadable)

    def _flac_check_fix_job(
        self, gen: int, rows: list[flac_verify.Row], ffmpeg: str, candidates: int, unreadable: list[str] | None = None
    ) -> None:
        alive = self._flac_check_alive(gen)
        sizes = {r.path: max(r.size, 0) for r in rows if r.status == flac_verify.UNSET}
        try:
            out = flac_verify.fix_rows(
                rows,
                ffmpeg,
                should_continue=alive,
                on_row=self._flac_check_progress(gen, candidates, sizes).on_row,
                # Read at call time, so the settle window is one knob.
                settle_seconds=flac_verify.SETTLE_SECONDS,
            )
        except Exception:
            logger.exception("FLAC check: the fix failed")
            self._flac_check_post(gen, {"phase": "failed", "running": False, "error": "The fix failed; see the log."})
            return
        self._flac_check_finish(gen, "fix", out, cancelled=not alive(), files=candidates, unreadable=unreadable)

    # ---- back on the GUI thread ------------------------------------------

    @Slot(int, dict)
    def _on_flac_check_event(self, gen: int, fields: dict) -> None:
        """(GUI thread) Merge a worker's fields into the state; a stale run's
        events (a newer run has started) are dropped."""
        if gen != self._flac_check_gen:
            return
        fields = dict(fields)
        final = fields.pop("final", False)
        if final:
            result_gen, rows, unreadable = self._flac_check_result
            if result_gen == gen:
                self._flac_check_rows = rows
                self._flac_check_unreadable = unreadable
        self._flac_check_state.update(fields)
        self.flacCheckChanged.emit()
        if not self._flac_check_state.get("running"):
            self._flac_check_settled(final)

    def _flac_check_settled(self, final: bool) -> None:
        """(GUI thread) A run ended. A fix that finished, or a scan of the
        same folders that finds nothing left to fix, drops the resume marker;
        a canceled or failed run keeps it, and so does a scan that could not
        read a folder the unfinished fix could have written in (what it left
        there was never looked at) or a fix that inherited the marker over
        such a scan. A RESUME's scan goes on to the fix when it found files
        still without a signature."""
        resuming, self._flac_check_resuming = self._flac_check_resuming, False
        inherited, self._flac_check_keep_resume = getattr(self, "_flac_check_keep_resume", False), False
        st = self._flac_check_state
        if not final or st.get("phase") != "done":
            return
        marker = str(self._flac_check_prefs().get("flac_check_resume") or "")
        scope = str(st.get("scope") or "")
        missed = self._flac_check_missed(scope)
        # A fix inherits only what it missed; a scan also needs nothing left.
        finished = not (inherited and missed) if st.get("last") == "fix" else not (st.get("fixable") or missed)
        if marker and marker == scope and finished:
            self._flac_check_resume_unread = None
            self._flac_check_set_pref("flac_check_resume", "")
        if resuming and st.get("last") == "scan" and st.get("fixable"):
            self.flacCheckFix()

    def _flac_check_missed(self, scope: str) -> bool:
        """(GUI thread) Whether the last run could not read a folder the
        unfinished fix could have written in. A folder the scan behind that
        fix could not read either never held its work; when that scan is not
        known (the marker is from before this launch), only a selected folder
        that did not open counts, so a folder no scan can ever list (a drive
        root's ``lost+found``) cannot hold RESUME on the card for good."""
        unreadable = list(getattr(self, "_flac_check_unreadable", []))
        if not unreadable:
            return False
        before = getattr(self, "_flac_check_resume_unread", None)
        if before is None:
            return any(_under_any(r, unreadable) for r in _scope_roots(scope))
        return any(not _under_any(u, before) for u in unreadable)
