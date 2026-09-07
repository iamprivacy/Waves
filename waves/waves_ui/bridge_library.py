"""The local music-library family: scanning the user's folder, watching it for
changes, and answering "do you already have this?" from what it found.

This is the source behind the ownership badge on every album and artist. It owns
the scan lifecycle (cold scan, incremental refresh, deep sweep), the freshness
machinery that keeps a weeks-running app honest about a folder that changed
underneath it, and the in-memory presence index the badge is answered from.

The division of labour, three modules deep, each replaceable on its own:
``waves.library_index`` walks the disk and enumerates album facts;
``waves.matching`` decides whether two records name the same album; this mixin
is the Qt layer over both (threads, timers, signals, GUI-thread rules).

A mixin over WavesBridge rather than a section of backend.py: the class name,
method names and signatures deliberately mirror the redesign branch's
bridge/library.py, so when that branch lands this file resolves as a
whole-file dedupe instead of hunk surgery. This module must never import
backend.

State this family owns on the bridge (created in WavesBridge.__init__, which
stays there because it owns the object's lifecycle): ``_library`` (the
LibraryIndex store), ``_library_index`` (the presence index, or None until a scan
finishes), its ``_library_track_index`` twin and ``_library_artist_index``
rollup, ``_library_scan_status``,
``_library_index_building`` / ``_library_index_pending``, ``_library_gen``, the
watcher and its debounce/pending-add state, and the scan timers.

THREADING (the rule this family exists to respect): scans run on a worker pool,
but the QFileSystemWatcher and every timer live on the GUI thread. Worker results
cross back over ``_librarySyncWatch`` / ``_libraryPollDone``, never by touching
watcher state directly.
"""

from __future__ import annotations

import contextlib
import logging
import os
import pathlib
import sys
import time

from PySide6 import QtCore, QtGui
from PySide6.QtCore import Signal, Slot

import waves.matching as matching
from waves.helper.path import folder_name_candidates, safe_filename_replacement, safe_filename_replacement_map
from waves.library_index import SCAN_MISSING, SCAN_OK, SCAN_UNREADABLE, root_comparison_key
from waves.worker import Worker

from . import smb_relist

# The subsystem child logger (per the diagnostics conventions): propagates into
# the root "waves" breadcrumb ring while letting verbose logs slice per subsystem.
logger = logging.getLogger("waves.library")

# --- Library-watch cadence + local-disk classification ------------------------
# The ownership scan is kept fresh by four triggers into the one incremental
# rebuild: (1) a universal container-mtime POLL every few minutes (works on every
# platform and over a network mount, since a moved-in album bumps its parent's
# mtime); (2) the existing hourly full incremental sweep (catches a track added
# inside an album, which bumps only the album leaf); (3) a DEEP force_full sweep
# that re-lists ignoring mtime, to heal a mount whose folders never change mtime;
# and (4) a QFileSystemWatcher on LOCAL disks only, for near-instant updates.
# File change events do not cross an SMB/NFS mount on macOS/Linux, so the watcher
# is a best-effort accelerator and the poll is the source of truth.
_LIBRARY_POLL_MS = 5 * 60 * 1000
_LIBRARY_DEEP_SWEEP_MS = 12 * 60 * 60 * 1000
# Mid-scan partial publishes (badges lighting up while a scan runs) rebuild the
# whole presence index from a table read, so they are rate-limited to one per
# this many seconds; the first commit and the scan's final publish always land.
_SCAN_PUBLISH_MIN_S = 2.5
# The presence-verdict memos (one per badge slot) hold this many answers, FIFO.
# Sized for several screens of rows; a verdict dict is small, so the bound is
# about forgetting a session's long tail, not about memory pressure.
_PRESENCE_MEMO_MAX = 4096
_LIBRARY_WATCH_DEBOUNCE_MS = 3000  # coalesce a watcher event burst (copying an album)
_LIBRARY_WATCH_MAX_DEBOUNCE_S = 30.0  # but flush at least this often during a long import
_LIBRARY_WATCH_CHUNK = 200  # add this many watch paths per event-loop tick (no UI stall)
_LIBRARY_DL_DEBOUNCE_MS = 15 * 1000  # coalesce a bulk download's per-track ownership records
# The launch sweep waits for the boot overlay to reveal (its walk starves the
# GUI thread of the interpreter, and the boot water drops frames): this timer
# is the failsafe that starts it anyway if the reveal never reports, sized
# past the launch sequence's own worst case (1.9s hold + 8s handover cap +
# drain + zoom) so it only ever fires when QML is gone or wedged.
_BOOT_LIBRARY_SCAN_FAILSAFE_MS = 15 * 1000
# ...but flush at least this often, because that debounce RESTARTS per track and
# a sustained download lands tracks faster than it, so without a ceiling the
# rebuild never runs and badges freeze for the whole batch. Longer than the
# watcher's 30s: this fires while downloads are actively competing for the same
# disk, so once every two minutes is the right trade against scanning cost.
_LIBRARY_DL_MAX_DEBOUNCE_S = 120.0
# The probe by name (see the "Probe by name" section): how long a miss for one
# artist is remembered before the disk is asked again (a folder can appear in
# the meantime, so it is a rest, not a verdict), and how long a download gate
# waits for a running scan to release the cache before deciding without it.
_LIBRARY_PROBE_MEMO_S = 300.0
_LIBRARY_PROBE_GATE_WAIT_S = 15.0
# How long a BULK GATE waits before asking again after a scan told it the cache
# was busy. Deliberately not the memo window and deliberately not in the memo:
# a deferral must leave the memo clean so a badge stays free to ask the moment
# it wants to. This stamp is read only by the download gates, which are the
# callers that would otherwise line up fifty deep behind the same wait for the
# same non-answer. _library_probe_rearm drops it as soon as a publish lands.
_LIBRARY_GATE_COOLDOWN_S = 30.0
# Names per probe_folders call. Big enough that a page of results is one
# question, small enough that a scan waiting behind the cache lock is not
# held off for long (the lock is released between batches).
_LIBRARY_PROBE_CHUNK = 192
# How long the untrusted-listing recovery waits for the cache lock. The scan it
# runs behind has just released it, so this is only ever paid when another
# caller slipped in between, and giving up simply leaves the recovery to the
# next scan.
_LIBRARY_RELIST_LOCK_WAIT_S = 20.0
# Distinct spellings kept per artist key, and the ceiling on the whole queue:
# a runaway page must never turn into unbounded state or unbounded disk asks.
_LIBRARY_PROBE_SPELLINGS = 3
_LIBRARY_PROBE_QUEUE_MAX = 4000
# Where an artist name sits in each kind of catalogue row (see
# LibraryMixin._library_probe_page). Playlists and mixes are absent on
# purpose: their titles are not artists and not folder names.
_PAGE_ARTIST_FIELDS = (
    ("artists", "name"),
    ("albums", "artist"),
    ("eps", "artist"),
    ("tracks", "artist"),
    ("videos", "artist"),
)


def _nonblank(value) -> bool:
    """A usable name: a string with something in it."""
    return isinstance(value, str) and bool(value.strip())


def _page_artist_names(payload: dict) -> list[str]:
    """Every artist named by the per-kind lists of a results payload."""
    return [
        row[field]
        for group, field in _PAGE_ARTIST_FIELDS
        for row in payload.get(group) or ()
        if isinstance(row, dict) and _nonblank(row.get(field))
    ]


def _shelf_artist_names(items) -> list[str]:
    """Every artist named by one browse shelf. A shelf mixes kinds, so the
    artist sits under "artist" on an album or track tile and under "name" on an
    artist tile; a playlist or mix tile names neither and is skipped."""
    out: list[str] = []
    for item in items or ():
        if not isinstance(item, dict):
            continue
        if _nonblank(item.get("artist")):
            out.append(item["artist"])
        elif item.get("kind") == "artist" and _nonblank(item.get("name")):
            out.append(item["name"])
    return out


# Filesystem-type prefixes (as QStorageInfo reports them, lowercased) that mean a
# LOCAL disk, where the QFileSystemWatcher is worth attaching. Anything else
# (smbfs/cifs/nfs/afpfs/webdav/sshfs/fuse*, or an unrecognised type) is treated as
# network, so only the poll runs there. Pure + module-level so it unit-tests
# without Qt. Classifying by mount TYPE (not by QFileSystemWatcher.addPaths()'s
# return, which reports success for an SMB path on macOS and a kernel NFS/CIFS
# path on Linux while delivering no events) is what keeps the design correct.
_LOCAL_FS_PREFIXES = (
    "apfs",
    "hfs",
    "ntfs",
    "refs",
    "ext2",
    "ext3",
    "ext4",
    "xfs",
    "btrfs",
    "zfs",
    "exfat",
    "vfat",
    "fat",
    "msdos",
)


# The other side of the same question, and a separate list on purpose: "not in
# the local list" is not the same claim as "on the network". A type in neither
# list (a FUSE mount, a container overlay, a filesystem newer than this table)
# is UNKNOWN, and the two callers want opposite answers about it: the watcher
# refuses to attach without confidence, while the scan runs at full speed
# unless it has a reason not to.
_NETWORK_FS_PREFIXES = (
    "smb",
    "cifs",
    "afp",
    "nfs",
    "webdav",
    "davfs",
    "sshfs",
    "fuse.sshfs",
    "ftp",
    "9p",
)


def _is_local_fstype(fstype: str) -> bool:
    """True if a filesystem-type name denotes a local disk (see _LOCAL_FS_PREFIXES)."""
    fstype = (fstype or "").strip().lower()
    return bool(fstype) and any(fstype.startswith(p) for p in _LOCAL_FS_PREFIXES)


def _is_network_fstype(fstype: str) -> bool:
    """True if a filesystem-type name denotes a network share (see _NETWORK_FS_PREFIXES)."""
    fstype = (fstype or "").strip().lower()
    return bool(fstype) and any(fstype.startswith(p) for p in _NETWORK_FS_PREFIXES)


def _is_remote_windows_device(device: str) -> bool:
    """True when a QStorageInfo device string names a UNC share: the backing of
    a Windows MAPPED network drive (Z: -> \\\\nas\\music), whose fileSystemType
    reports the REMOTE volume's format (often "NTFS") and so passes the local
    check above. A real local volume's device is \\\\?\\Volume{guid} (note the
    \\\\?\\ prefix) or a /dev node, never a bare \\\\server\\share."""
    d = str(device or "").strip()
    return d.startswith("\\\\") and not d.startswith("\\\\?\\")


def _listing_shape(lib) -> tuple[int, int]:
    """(entries handed over, distinct names) for the worst listing the cache's
    last scan met, or (0, 0). Read off the index object on the scan worker and
    mirrored onto the bridge, so a slot never reaches into the cache."""
    reader = getattr(lib, "untrusted_listing_shape", None)
    if reader is None:
        return (0, 0)
    try:
        handed, distinct = reader()
    except Exception:
        logger.debug("Library listing shape unavailable", exc_info=True)
        return (0, 0)
    return (int(handed), int(distinct)) if handed > distinct >= 0 else (0, 0)


def _listing_reconciled(lib) -> bool:
    """Has the recovery filled in everything the untrusted listing left out?
    Read off the index object on the scan worker and mirrored onto the bridge,
    the same way _listing_shape is, so a slot never reaches into the cache."""
    return bool(getattr(lib, "last_listing_reconciled", False))


def _rearm(bridge) -> None:
    """Re-ask whatever a running scan made the probe defer. Guarded because the
    partial stubs the bridge tests build bind a fixed list of methods."""
    hook = getattr(bridge, "_library_probe_rearm", None)
    if hook is not None:
        hook()


class LibraryMixin:
    """The local music-library scan, watch and presence family, mixed into
    WavesBridge (see the module docstring)."""

    # revealLibraryAlbum resolved its target on a worker (the ancestor walk can
    # stat a dead network mount); openUrl must run on the GUI thread.
    _revealResolved = Signal(str)
    libraryPresenceChanged = Signal()  # the local library-presence index (re)built; QML re-queries the badge
    libraryScanStatusChanged = Signal()  # the last library-scan outcome changed; Settings re-reads libraryScanStatus()
    librarySourceChanged = (
        Signal()
    )  # a library pref committed (switch, source or folder); the Settings card re-reads its saved-state mirrors
    # Internal, worker->GUI marshalling for the library scan. _librarySyncWatch
    # asks the GUI thread to realign the QFileSystemWatcher after a scan (the
    # watcher lives on the GUI thread), and carries (is_local, container_paths)
    # ALREADY RESOLVED on the worker: deciding local-vs-network stats the volume
    # and listing containers reads sqlite, and a dead network mount can hang a
    # stat for many seconds, which on the GUI thread is a frozen window.
    # _libraryPollDone carries (generation, changed) from a container poll worker
    # back to the GUI thread. _downloadRecorded crosses from the ownership pool
    # (a downloaded file just landed) to the GUI thread, where the debounce timer
    # may be started.
    _librarySyncWatch = Signal(bool, list)
    _libraryPollDone = Signal(int, bool)
    _downloadRecorded = Signal()

    def _library_root(self) -> str:
        """The folder scanned for the ownership badge, resolved from the chosen
        library source: the download folder when the user has said their library
        lives there, otherwise the separate folder they picked. An empty result
        means no library is configured, so nothing is scanned: every automatic
        trigger (the launch rebuild, the hourly and deep sweeps, the container
        poll, the watcher, the download debounce) funnels through this answer.
        The library_enabled master switch (off by default) is checked first, so
        while the feature is off NO folder resolves, whatever the source prefs
        say, and the download folder is never indexed on its own."""
        if not self._waves_pref_bool("library_enabled"):
            return ""
        if self._waves_prefs.get("library_source") == "download":
            return (self.settings.data.download_base_path or "").strip()
        return (self._waves_prefs.get("library_folder") or "").strip()

    def _invalidate_library_index(self) -> None:
        """Drop the current badges and stop watching the old folder after the
        user changes where their library lives (or turns the scan off). No scan
        is dispatched here: for a saved configuration change applySettings starts
        the new folder's first scan itself, and a disabled or unconfigured
        library resolves no root, so the sweeps stay inert. Bumps the generation
        so an in-flight scan of the old folder is discarded."""
        self._teardown_library_watch()
        # The poll's in-flight guard is deliberately NOT reset here: a poll
        # worker wedged in a dead mount's stat is still occupying the pool, and
        # clearing the flag let the next timer tick fan a second herd of stats
        # into the same sick share. _on_library_poll_done always clears it
        # (stale generations included), so the guard cannot wedge shut.
        # Swap to the NEW root's own cache file (one file per root, see
        # cache_file_for_root): the old folder's scan stays on disk for the day
        # the user switches back, and a scan still in flight against the old
        # object keeps writing into the old file, where its work is preserved
        # rather than racing this one (its results are generation-discarded).
        # The bump and the swap happen under the index lock, paired with the
        # rebuild capturing (gen, index object) under the same lock: a rebuild
        # can then never hold the NEW object with the OLD generation's root,
        # which is the combination that let a stale queued worker begin a scan
        # of the old folder inside the new root's cache file, wiping its dirs
        # tree and stamping the wrong scan_root into it.
        with self._library_index_lock:
            self._library_gen += 1
            retired = self._library
            # getattr: partial test stubs drive this slot without the full init.
            scanning = getattr(self, "_library_scanning", None)
            self._library = self._open_library_index()
        # Close the retired index's sqlite connection, or every folder change
        # leaked one for the session. NOT while a scan still holds it: that
        # scan keeps writing its work into the old file (preserved for the day
        # the user switches back), and it closes its own object when it
        # notices its generation is gone (see _rebuild_library_index).
        if retired is not None and retired is not scanning:
            with contextlib.suppress(Exception):
                retired.close()
        self._library_index = None
        self._library_track_index = None
        self._library_scan_status = "unset"
        self._library_scan_partial = False
        self._library_listing_shape = (0, 0)
        self._library_listing_reconciled = False
        gate = getattr(self, "_library_probe_gate", None)  # absent on partial test stubs
        with gate if gate is not None else contextlib.nullcontext():
            self._library_probe_memo = {}
            self._library_probe_inflight = set()
            # Questions asked about the folder being left behind must not be
            # answered against the new one. The drainer is left to notice the
            # empty queue and stand down by itself.
            self._library_probe_pending = {}
            self._library_probe_deferred = {}
        self._library_scan_progress = {}
        self.libraryPresenceChanged.emit()
        self.libraryScanStatusChanged.emit()

    def _publish_artist_rollup(self, idx: dict) -> None:
        """(worker thread) Derive the per-artist rollup for a just-published
        album index BEFORE libraryPresenceChanged fires. The QML handlers that
        signal wakes re-ask artistLibraryPresence synchronously on the GUI
        thread, and that slot's lazy derive is a full pass over the album
        index, so leaving it to the slot put the whole pass inside a frame.
        The slot keeps the lazy derive as the race fallback. Guarded so a
        publish that lost the last-writer race never caches a rollup for an
        index that is no longer the published one."""
        try:
            rollup = matching.build_artist_rollup(idx)
        except Exception:
            logger.debug("Artist rollup precompute failed; the slot derives it lazily", exc_info=True)
            return
        with self._library_index_lock:
            if self._library_index is idx:
                self._library_artist_index = rollup
                self._library_artist_index_src = idx

    def _build_presence_indexes(self, lib) -> tuple[dict, dict]:
        """Both presence indexes (albums, tracks) from one committed cache
        read, so every publish lands them as a pair and the two pills always
        describe the same scan. Reads only ``lib`` (handed in, never
        self._library at use time: see the generation notes in
        _rebuild_library_index), so the launch seed and the scan share it."""
        local: dict = {}
        for a in lib.iter_albums():
            # A Various-Artists credit is refused HERE, on the raw tag,
            # because the artist folds can move a marker out of the
            # detectors' reach ("V / A" splits at the spaced slash to a
            # key of "v", which then rolled up as a real artist and could
            # answer for one). The streaming side already refuses VA
            # queries, so these rows were unreachable dead weight anyway.
            if matching.is_various_artists(a["artist"]):
                continue
            local.setdefault(matching.presence_key(a["title"], a["artist"]), []).append(
                {
                    # The raw tagged title, kept because the key it is
                    # grouped under has its edition qualifiers peeled off
                    # and that is too loose to gate a download on.
                    "title": a["title"],
                    "year": a["year"],
                    "tracks": a["tracks"],
                    "id": a["id"],
                    # Quality facts ride along so the badge can name the
                    # copy ("MP3 128").
                    "codec": a.get("codec", ""),
                    "bitrate": a.get("bitrate", 0),
                    "bits": a.get("bits", 0),
                    "rate": a.get("rate", 0),
                    # What the release SAYS it is, as opposed to what the
                    # folder holds: its own track count and which disc of
                    # how many this folder is. Lets the verdict tell a
                    # complete copy of a smaller edition from a copy short
                    # a track, and joins the disc sets no folder name spells
                    # out.
                    "declared": a.get("declared", 0),
                    "disc_no": a.get("disc_no", 0),
                    "disc_total": a.get("disc_total", 0),
                    # The folder's summed play length in seconds (0 when
                    # its files never said), the identity witness no tag
                    # has to carry: it can prove an undated match and
                    # refute a same-count impostor.
                    "runtime": a.get("runtime", 0),
                }
            )
        by_track: dict = {}
        for t in lib.iter_tracks():
            if not t["title"] or not t["artist"]:
                continue  # an untagged file honestly matches nothing
            if matching.is_various_artists(t["artist"]):
                continue  # same raw-tag refusal as the album rows above
            by_track.setdefault(matching.track_key(t["title"], t["artist"]), []).append(
                {
                    "id": t["id"],
                    "codec": t.get("codec", ""),
                    "bitrate": t.get("bitrate", 0),
                    "bits": t.get("bits", 0),
                    "rate": t.get("rate", 0),
                    # The holding folder's identity, the only evidence a
                    # track match can be proven against (see the track
                    # matcher). Carried per row rather than looked up later,
                    # because the album index is keyed by presence key and
                    # cannot be asked "what is at this path".
                    "album": t.get("album", ""),
                    "album_year": t.get("album_year", ""),
                    # The file's play length in seconds (0 when it never
                    # said): a second, tag-free identity witness.
                    "length": t.get("length", 0),
                    # The featuring credit the key deliberately strips,
                    # kept as evidence: different guests are different
                    # recordings and must never claim each other.
                    "guests": sorted(matching.feat_guests(t["title"], t["artist"])),
                }
            )
        return local, by_track

    def _seed_library_badges_job(self, gen: int, lib, root: str) -> None:
        """(POOL THREAD) Publish the previous scan's badges straight from the
        committed cache, so a freshly launched window is never badge-less
        while the change-check runs underneath.

        Dispatched as its OWN job, ahead of any scan, because the scan shares
        a pool with downloads and art fetches: a search rendered while the
        scan's turn was still queued showed every result with no badge at
        all, then flipped all of them in one frame when the first publish
        finally landed. This is a read of a local sqlite file, so it is cheap
        enough to jump the queue.

        Only when nothing is shown yet (the first build after launch): a
        coalesced rebuild already has a live index and must not be reset to
        an older one. Gated on the committed cache being for this very
        folder, so switching library folders never briefly flashes the
        previous library's badges."""
        if self._library_index is not None or gen != self._library_gen:
            return
        try:
            if not lib.matches_scan_root(root):
                return
            seeded, seeded_tracks = self._build_presence_indexes(lib)
        except Exception:
            # The scan behind this one publishes the real thing either way;
            # a failed seed costs the head start, never the badges.
            logger.debug("Library badge seed failed; leaving it to the scan", exc_info=True)
            return
        if not seeded or gen != self._library_gen:
            return
        # Re-checked AFTER the build, which the scan's own copy of this
        # never had to do: this can run BESIDE a scan, and a scan that
        # finishes first has published a newer index over the window this
        # spent assembling an older one. Last writer must not be the stale
        # one. Check and set under the lock every publisher takes, because
        # a bare check here is a promise with a hole in it: the scan can
        # publish between this line and the assignment below, and the
        # badges then visibly fall BACK to the pre-scan picture until the
        # hourly sweep (the container poll sees no change, the scan having
        # already stamped the mtimes it compares against).
        with self._library_index_lock:
            if self._library_index is not None:
                return
            self._library_index = seeded
            self._library_track_index = seeded_tracks
            self._library_probe_memo = {}
        # The cache remembers what its last scan could not trust, so a badge
        # seeded from it can already ask the disk by name (see the probe
        # section) while the change-check scan runs underneath.
        self._library_scan_partial = bool(getattr(lib, "last_scan_partial", False))
        self._library_listing_shape = _listing_shape(lib)
        self._library_listing_reconciled = _listing_reconciled(lib)
        self._publish_artist_rollup(seeded)
        self._emit_from_worker("libraryPresenceChanged")
        _rearm(self)

    def _seed_library_badges(self) -> None:
        """(GUI thread, launch) Light the badges from the committed cache
        WITHOUT dispatching a scan. The launch sweep itself is held until the
        boot overlay has revealed (_start_boot_library_scan): its directory
        walk runs on pool threads that compete with the GUI thread for the
        interpreter, and the only motion on screen during boot (the wave
        loop) paid for that in dropped frames (probe 2026-09-01: 59-73 ms GUI
        stalls with _walk_album_dirs/_scandir_one busy, against a 42 ms frame
        budget). The seed is a sqlite read, cheap enough to keep."""
        with self._library_index_lock:
            gen = self._library_gen
            lib = self._library
        root = self._library_root()
        if not root:
            return
        self.threadpool.start(Worker(lambda: self._seed_library_badges_job(gen, lib, root)), 10)

    def _start_boot_library_scan(self) -> None:
        """(GUI thread) Release the launch library sweep. Called by the boot
        reveal (bootRevealed) and by the failsafe timer armed at construction,
        whichever comes first; one-shot, so the loser is a no-op. The
        force_full decision is made HERE, not at construction: it reads the
        cache's clock, and the answer cannot go stale in the seconds the
        reveal takes."""
        if not getattr(self, "_boot_library_scan_pending", False):
            return
        self._boot_library_scan_pending = False
        timer = getattr(self, "_boot_library_scan_timer", None)
        if timer is not None:
            timer.stop()
        logger.info("boot library sweep released")
        self._rebuild_library_index(force_full=self._library.due_for_full_scan(_LIBRARY_DEEP_SWEEP_MS / 1000.0))

    def _rebuild_library_index(self, force_full: bool = False) -> None:  # noqa: C901 (see below)
        """(Re)build the local library-presence index off the GUI thread by
        scanning the configured library folder. Generation-guarded so a library-
        folder change discards an in-flight scan; coalesced so rapid triggers (a
        download batch) collapse to one trailing rebuild. A failed scan keeps the
        last good index rather than blanking every badge. ``force_full`` re-lists
        every folder (the manual Rescan) instead of the cheap mtime-incremental
        sweep; a forced request that coalesces into a running scan still forces the
        trailing rebuild.

        (mccabe: this is the family's one state machine, and its branches are its
        guards: already-running, no source configured, generation superseded, scan
        failed, index empty. Kept whole and exempted, since the guards have to be
        read together to see which outcomes keep the last good index.)"""
        # Claiming the scan is a check-then-set from two threads (GUI triggers and
        # a finishing scan's own trailing rebuild), so it takes the lock: without
        # it both callers pass the check and two scans walk the same sqlite cache
        # at once, which was measured publishing a 60-album index as 0 albums.
        with self._library_index_lock:
            if self._library_index_building:
                self._library_index_pending = True
                self._library_force_full_pending = self._library_force_full_pending or force_full
                return
            self._library_index_building = True
            # Captured together, under the same lock the invalidation swaps
            # under, so this scan's generation and its index OBJECT always
            # belong to each other. The worker below must only ever touch
            # ``lib``: reading self._library at use time handed a stale queued
            # worker the NEW root's cache to scan the OLD root into (see
            # _invalidate_library_index).
            gen = self._library_gen
            lib = self._library
            # Marks ``lib`` as scan-held, so an invalidation that swaps it out
            # mid-scan leaves closing it to this scan's own finally.
            self._library_scanning = lib
        root = self._library_root()
        # A pref write precedes its own invalidation (setWavesPref stores and
        # saves the new folder, then _invalidate_library_index bumps the
        # generation), so a trailing rebuild dispatched from the pool in that
        # gap can capture the OLD generation and index while resolving the NEW
        # root, and _begin_scan would then wipe the old file's dirs tree and
        # stamp the new root into it. The pair is provably mismatched (the
        # index remembers which root it was opened for), so refuse it: the
        # invalidation that follows the pref write re-triggers with a matched
        # pair. Unknown ancestry (a partial test stub) passes untouched.
        expected = getattr(lib, "opened_for_key", None)
        if root and expected is not None and root_comparison_key(root) != expected:
            with self._library_index_lock:
                self._library_index_building = False
                self._library_scanning = None
            logger.info("library rebuild refused: index and root straddle a folder change")
            return
        if not root:
            # No library source is configured yet (the opt-in default): show no
            # badges and dispatch no scan, so the download folder is never indexed
            # without the user choosing it. Clear any badges left from a previous
            # folder and report 'unset' so Settings prompts for a source. Guarded
            # so the periodic sweeps that land here don't emit on every tick.
            changed = self._library_index is not None or self._library_scan_status != "unset"
            self._library_index = None
            self._library_track_index = None
            self._library_scan_status = "unset"
            self._library_scan_progress = {}
            with self._library_index_lock:
                self._library_index_building = False
                self._library_scanning = None
            if changed:
                self.libraryPresenceChanged.emit()
                self.libraryScanStatusChanged.emit()
            return
        # Settings shows "Scanning…" while the build runs (see libraryScanStatus).
        self.libraryScanStatusChanged.emit()

        def build_index() -> tuple[dict, dict]:
            # The shared builder, closed over THIS scan's ``lib`` (see the
            # generation notes above).
            return self._build_presence_indexes(lib)

        # When the LAST mid-scan partial publish rebuilt the indexes (None
        # means never, so the first committed flush always publishes; a zero
        # start would only mean "always" while the monotonic clock happens to
        # read past the window). Scan-local by construction: a new scan starts
        # its own clock.
        last_partial_publish: list[float | None] = [None]

        def on_progress(event: dict) -> None:
            # A COLD scan of a NAS library takes minutes, so relay the scanner's
            # (already rate-limited) live progress: walk discoveries, read
            # done/total, and the artist/album under the needle. The partial
            # presence index is republished only on committed flushes, when the
            # database actually moved, so badges light up during the scan without
            # rebuilding the index several times a second. Stale-guarded like the
            # final publish; emits queue safely across threads.
            if gen != self._library_gen:
                return
            p = {
                "phase": str(event.get("phase", "")),
                "found": int(event.get("found", 0)),
                "checked": int(event.get("checked", 0)),
                "done": int(event.get("done", 0)),
                "total": int(event.get("total", 0)),
                "indexed": int(event.get("indexed", 0)),
                "artist": str(event.get("artist", "")),
                "album": str(event.get("album", "")),
                "eta_secs": -1,
            }
            if p["phase"] == "read":
                if p["done"] <= 0:
                    self._library_scan_read_t0 = time.monotonic()  # reads start now
                else:
                    elapsed = max(0.001, time.monotonic() - self._library_scan_read_t0)
                    p["eta_secs"] = int((p["total"] - p["done"]) * elapsed / p["done"])
                # Mid-scan republish, throttled: every committed flush used to
                # rebuild BOTH presence indexes from a full table read, and a
                # cold scan of a big library commits every 200 albums, so the
                # rebuild cost itself grew quadratically as the table filled
                # (the badges only need to light up, not strobe). The FIRST
                # commit still publishes immediately; the scan's final publish
                # is unconditional in work() below, so nothing committed is
                # ever left unpublished at the end.
                if event.get("committed") and (
                    last_partial_publish[0] is None or time.monotonic() - last_partial_publish[0] >= _SCAN_PUBLISH_MIN_S
                ):
                    fresh, fresh_tracks = build_index()
                    # Re-check the generation AFTER the build, exactly as the
                    # startup seed below does. build_index() is a full table read
                    # and dict build, a wide enough window for the user to change
                    # library folders underneath it; assigning unconditionally
                    # republished the ABANDONED folder's badges, and since
                    # invalidation deliberately dispatches no new scan, nothing
                    # ever overwrote them.
                    if gen != self._library_gen:
                        return
                    last_partial_publish[0] = time.monotonic()
                    # Under the lock the seed beside this scan also takes, so
                    # its "nobody has published yet" check cannot straddle
                    # this publish and overwrite it with the older cache.
                    with self._library_index_lock:
                        self._library_index = fresh
                        self._library_track_index = fresh_tracks
                        self._library_probe_memo = {}
                    self._library_scan_partial = bool(getattr(lib, "last_scan_partial", False))
                    self._library_listing_shape = _listing_shape(lib)
                    self._library_listing_reconciled = _listing_reconciled(lib)
                    self._publish_artist_rollup(fresh)
                    self._emit_from_worker("libraryPresenceChanged")
                    _rearm(self)
            self._library_scan_progress = p
            self._emit_from_worker("libraryScanStatusChanged")

        def seed() -> None:
            # (POOL THREAD, dispatched AHEAD of the scan) The shared seed job:
            # see _seed_library_badges_job for why it exists and its guards.
            self._seed_library_badges_job(gen, lib, root)

        def work() -> None:
            index = None
            track_index = None
            status = None
            count = 0
            try:
                try:
                    self._library_share_remount(root)
                    # Classified HERE on the pool (the stat behind it can hang
                    # on a sick mount) and handed to refresh, which throttles
                    # its walk and read pools on a network root: sixteen
                    # concurrent round trips against a cold SMB share is the
                    # herd that can wedge the mount for the whole desktop. The
                    # three-answer classifier, not the watcher's boolean: only
                    # a POSITIVE network verdict may throttle, or an unnamed
                    # local filesystem would scan a cold library at a quarter
                    # speed for no reason.
                    count = lib.refresh(
                        root,
                        should_continue=lambda: gen == self._library_gen,
                        on_progress=on_progress,
                        force_full=force_full,
                        root_is_local=self._library_root_locality(root),
                    )
                    status = lib.last_scan_status
                    self._library_share_alive(root, status)
                    # Before the index is built, so a recovery lands in the
                    # very first publish rather than a second one.
                    self._library_recover_untrusted(lib, root, lambda: gen == self._library_gen, on_progress)
                    index, track_index = build_index()
                except Exception:
                    if getattr(lib, "is_closed", False):
                        # A quit (or factory reset) closed the cache while a
                        # walk sat wedged in a network stat past the bounded
                        # shutdown wait; the raise on the closed connection is
                        # that orderly end arriving late, not a failed scan,
                        # and must not land as an ERROR in the crash trail.
                        logger.info("library scan ended by shutdown")
                    else:
                        logger.exception("Library album-presence index build failed")
                    index = None  # keep the last good index; do not blank the badge
                if gen == self._library_gen:
                    # Publish only an index that belongs to THIS root: a failed
                    # probe of a changed root (an offline drive, a down NAS)
                    # returns early with the previous library's rows still in
                    # the database, and publishing those would light up the OLD
                    # folder's badges as if they were the new folder's.
                    if index is not None and (status == SCAN_OK or lib.matches_scan_root(root)):
                        # Same lock as the seed's check-and-set: whichever of
                        # the two runs last must be the one that stands, and
                        # this one is always allowed to stand.
                        with self._library_index_lock:
                            self._library_index = index
                            self._library_track_index = track_index
                            self._library_probe_memo = {}
                        self._publish_artist_rollup(index)
                        self._library_scan_progress = {"phase": "done", "indexed": count, "eta_secs": -1}
                        self._emit_from_worker("libraryPresenceChanged")
                        _rearm(self)
                    # Surface why a scan found nothing (unreadable folder vs empty) so
                    # Settings can explain a permission-blocked folder, not a silent blank.
                    if status is not None:
                        self._library_scan_status = status
                        # ...and whether the folder LISTED but could not be
                        # trusted (a share whose directory paging repeats),
                        # which is a successful scan that still needs the
                        # probe by name behind every badge miss.
                        self._library_scan_partial = bool(getattr(lib, "last_scan_partial", False))
                        self._library_listing_shape = _listing_shape(lib)
                        self._library_listing_reconciled = _listing_reconciled(lib)
            finally:
                # The building flag must clear on EVERY exit: the Worker wrapper
                # swallows exceptions, so a raise anywhere above would otherwise
                # wedge the flag True and freeze every future rebuild (badges,
                # Rescan, the timers) for the whole session, invisibly. The seed
                # beside this one never touches the flag: it does not claim the
                # scan, so it has nothing to release and cannot wedge anything.
                # Under the lock, so a GUI-thread trigger arriving at this
                # instant either claims the next scan or records itself as
                # pending, never both and never neither.
                with self._library_index_lock:
                    self._library_index_building = False
                    self._library_scanning = None
                    stale = gen != self._library_gen
                # A generation bump while this scan ran means ``lib`` is no
                # longer the live index: invalidation and factory reset swap in
                # a replacement (leaving closing the old one to us, see
                # _invalidate_library_index), and shutdown closes it itself, so
                # this second close is a harmless no-op there. A CURRENT
                # generation always leaves ``lib`` alone: it is self._library.
                if stale:
                    with contextlib.suppress(Exception):
                        lib.close()
                # Always re-announce at the end: the "scanning" state (derived from the
                # building flag) has just cleared even when the status string is
                # unchanged, and Settings must drop its "Scanning…" note.
                # Through the torn-down guard, like every emit this scan makes:
                # quitting gives the pools a bounded drain, so a scan still
                # walking a slow folder can reach this line after the bridge's
                # C++ object is gone, and a plain emit then raises and is
                # logged as a worker crash on the way out of a clean quit.
                self._emit_from_worker("libraryScanStatusChanged")
                # Refresh the local-disk watcher to match the now-current tree (add
                # newly-discovered artist folders, drop vanished ones). Marshalled to
                # the GUI thread via a queued signal because the QFileSystemWatcher
                # lives there; a no-op off a local disk. The two slow answers (is this
                # root local, and which containers exist) are resolved HERE, on the
                # pool: the local check stats the volume and a dead network mount can
                # hang that stat for many seconds, which on the GUI thread is a frozen
                # window. The GUI side is then only the watcher calls themselves.
                #
                # Inside the finally, and caught: this used to sit outside it,
                # where one raise skipped the coalescing tail below and silently
                # dropped a Rescan the user had already pressed, while leaving
                # the pending flag set to fire a spurious full rebuild later.
                try:
                    if gen == self._library_gen:
                        self._librarySyncWatch.emit(*self._resolve_watch_set(root))
                except Exception:
                    logger.debug("Watcher realignment after the scan failed", exc_info=True)
                # Read and clear the coalescing flags under the same lock: a GUI
                # trigger that set pending must not have it dropped on the floor
                # between the read and the reset, which would lose the rebuild it
                # asked for entirely.
                with self._library_index_lock:
                    trailing = self._library_index_pending
                    self._library_index_pending = False
                    trailing_full = self._library_force_full_pending
                    self._library_force_full_pending = False
                if trailing:
                    self._rebuild_library_index(force_full=trailing_full)

        # The seed goes first and at a raised priority, so the badge answer a
        # committed cache already holds is not queued behind the scan that is
        # only there to check whether it changed.
        self.threadpool.start(Worker(seed), 10)
        self.threadpool.start(Worker(work))

    def _poll_library_containers(self) -> None:
        """(GUI thread, poll timer) The cheap, network-safe change check: stat only
        the container folders on the pool and rebuild only if one moved. Skipped
        while a scan is building or a prior poll is still in flight."""
        if self._library_index_building or self._library_poll_in_flight:
            return
        root = self._library_root()
        if not root:
            return
        self._library_poll_in_flight = True
        gen = self._library_gen

        def work() -> None:
            # The emit is the only thing that releases the in-flight guard
            # (invalidation no longer resets it), so it must survive any raise.
            changed = False
            try:
                changed = self._library.poll_containers_changed(root) is True
            except Exception:
                logger.debug("Library container poll failed", exc_info=True)
            finally:
                self._libraryPollDone.emit(gen, changed)

        self.threadpool.start(Worker(work))

    @Slot(int, bool)
    def _on_library_poll_done(self, gen: int, changed: bool) -> None:
        """(GUI thread) Result of a container poll: clear the in-flight guard, and
        trigger an incremental rebuild if the poll saw a structural change and the
        library folder has not changed under us."""
        self._library_poll_in_flight = False
        if changed and gen == self._library_gen:
            self._rebuild_library_index()

    def _resolve_watch_set(self, root: str) -> tuple[bool, list[str]]:
        """(POOL THREAD) The two slow answers the watcher realignment needs:
        whether this root is on a local disk, and which container folders exist.

        Deliberately not on the GUI thread. Classifying the root stats the volume
        and a dead network mount can hang that stat for many seconds; listing
        containers reads sqlite. Either on the GUI thread is a frozen window, so
        both are resolved here and handed to _sync_library_watch as arguments.
        A failure means no watcher: the universal poll already covers that."""
        try:
            if not self._library_root_is_local(root):
                return False, []
            return True, self._library.container_paths()
        except Exception:
            logger.debug("Could not resolve the watch set; leaving it to the poll", exc_info=True)
            return False, []

    def _library_share_remount(self, root: str) -> None:
        """(POOL THREAD) Offer the library root the download folder's own
        remedy before a scan probes it. The library can live on a share macOS
        quietly ejects, and every probe then reads "missing" until something
        MOUNTS, which Finder navigation does and no amount of rescanning did:
        if the root's volume is gone from /Volumes and its origin is on
        record, ask macOS to mount it back. A present volume, a non-macOS
        path and a missing origin all no-op inside the shared machinery.
        getattr: partial test stubs drive the scan without the full init."""
        remount = getattr(self, "_remount_download_share", None)
        if remount is not None and remount(root):
            logger.info("library share was gone; mounted it back for the scan")

    def _library_share_alive(self, root: str, status: str) -> None:
        """(POOL THREAD) A scan that read its root (status SCAN_OK) is the
        library share's proof of life, the only safe moment to record its
        volume's origin URL (a statfs of a dead mount can hang); any other
        status records nothing. Downloads earn theirs on a landed file; the
        library earns it here. One statfs per volume per session inside the
        shared recorder. getattr: partial test stubs lack the machinery."""
        if status != SCAN_OK:
            return
        remember = getattr(self, "_remember_share_origin", None)
        if remember is not None:
            remember(root)

    def _library_root_locality(self, root: str) -> bool | None:
        """Where the library root lives: True local, False network, None when
        the evidence does not say. Classified by filesystem TYPE via
        QStorageInfo, never by the watcher's addPaths result (which reports
        success for an SMB path on macOS and a kernel NFS/CIFS path on Linux
        while delivering no events).

        Three answers, not two, because the two callers want opposite defaults
        for the third. The watcher must not attach without confidence, and the
        scan must not throttle itself to a quarter speed without a reason: a
        library on a filesystem this table has never heard of (a FUSE mount, an
        overlay in a container, something newer than this release) is a local
        disk far more often than not, and refresh() documents unknown as
        full speed."""
        if not root:
            return None
        if root.startswith("\\\\") or root.startswith("//"):
            return False  # a Windows UNC share is always network
        try:
            storage = QtCore.QStorageInfo(root)
            fstype = bytes(storage.fileSystemType()).decode("ascii", "ignore")
            device = bytes(storage.device()).decode("utf-8", "ignore")
        except Exception:
            return None
        # A Windows MAPPED drive (Z: -> \\nas\music) reports the remote volume's
        # format ("NTFS") as its type, so the type check alone would class it
        # local and attach a watcher that never fires. The device string (a UNC
        # share) and, belt-and-braces, GetDriveTypeW both unmask it.
        if _is_remote_windows_device(device):
            return False
        if sys.platform == "win32":
            try:
                import ctypes

                drive = os.path.splitdrive(os.path.abspath(root))[0]
                if drive and ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == 4:  # DRIVE_REMOTE
                    return False
            except Exception:
                logger.debug("Drive-type probe failed; trusting the fstype check", exc_info=True)
        if _is_network_fstype(fstype):
            return False
        return True if _is_local_fstype(fstype) else None

    def _library_root_is_local(self, root: str) -> bool:
        """True only when the library root is confidently on a LOCAL disk, so the
        QFileSystemWatcher is worth attaching. Anything unproven is treated as
        not local: the universal poll covers those."""
        return self._library_root_locality(root) is True

    @Slot(bool, list)
    def _sync_library_watch(self, is_local: bool, container_paths: list) -> None:
        """(GUI thread) Align the QFileSystemWatcher with the current container set.
        Attaches only on a confidently-local root (network mounts rely on the poll);
        adds watches for newly-discovered artist folders and drops vanished ones.
        The initial bulk add is chunked across event-loop ticks so a large local
        library never stalls the UI.

        Both arguments were resolved on the pool thread by the scan that emitted
        _librarySyncWatch, deliberately: classifying the root stats the volume and
        listing containers reads sqlite, and a dead network mount can hang that
        stat for many seconds. Nothing here touches the disk, so this slot cannot
        block the window however sick the mount is."""
        if not is_local or not self._library_root():
            self._teardown_library_watch()
            return
        if self._library_watcher is None:
            self._library_watcher = QtCore.QFileSystemWatcher(self)
            self._library_watcher.directoryChanged.connect(self._on_library_dir_changed)
        desired = set(container_paths)
        gone = self._watched_paths - desired
        if gone:
            self._library_watcher.removePaths(list(gone))
            self._watched_paths -= gone
        # Queue the not-yet-watched paths and add them in bounded chunks.
        self._library_watch_pending_add = list(desired - self._watched_paths)
        self._add_watch_chunk()

    def _add_watch_chunk(self) -> None:
        """(GUI thread) Add up to _LIBRARY_WATCH_CHUNK queued watch paths, then
        reschedule until drained, so adding ~1-2k paths on a big local library does
        not block the UI. Paths the platform could not watch (e.g. a Linux inotify
        watch-limit hit) are dropped from the tracked set and left to the poll."""
        watcher = self._library_watcher
        if watcher is None or not self._library_watch_pending_add:
            return
        chunk = self._library_watch_pending_add[:_LIBRARY_WATCH_CHUNK]
        self._library_watch_pending_add = self._library_watch_pending_add[_LIBRARY_WATCH_CHUNK:]
        failed = set(watcher.addPaths(chunk) or [])
        self._watched_paths.update(p for p in chunk if p not in failed)
        if self._library_watch_pending_add:
            QtCore.QTimer.singleShot(0, self._add_watch_chunk)

    def _on_library_dir_changed(self, _path: str) -> None:
        """(GUI thread) A watched container changed: (re)start the debounce so a
        burst of events (copying an album fires many) collapses to one rescan, but
        flush at least every _LIBRARY_WATCH_MAX_DEBOUNCE_S during a long import."""
        now = time.monotonic()
        if not self._library_watch_debounce.isActive():
            self._library_watch_burst_start = now
        if now - self._library_watch_burst_start >= _LIBRARY_WATCH_MAX_DEBOUNCE_S:
            self._library_watch_debounce.stop()
            self._on_library_watch_settled()
        else:
            self._library_watch_debounce.start()  # restart the settle window

    def _on_library_watch_settled(self) -> None:
        """(GUI thread) The watcher event burst settled: run one incremental rebuild."""
        self._rebuild_library_index()

    def _teardown_library_watch(self) -> None:
        """(GUI thread) Drop all watches (root not local, folder changed, or
        shutdown). Keeps the watcher object; an empty watcher costs nothing."""
        self._library_watch_pending_add = []
        if self._library_watcher is not None and self._watched_paths:
            self._library_watcher.removePaths(list(self._watched_paths))
        self._watched_paths.clear()

    def _on_download_recorded(self) -> None:
        """(GUI thread) A downloaded file landed on disk. When the library IS
        the download folder, schedule one debounced index rebuild so the album
        lights up soon after the last track of a bulk download, not once per
        track. A separate library folder cannot have changed because a download
        landed elsewhere; the poll and sweep timers keep it fresh.

        The debounce restarts on every track, so it has a CEILING, the same way
        the watcher's does. Without one, a queue that lands a track more often
        than the settle window (any sustained download does) kept pushing the
        deadline back and the timer never fired: badges froze for the whole
        batch, which on a big discography is hours, and the one case where they
        matter most is the one where they stopped."""
        if self._waves_pref_bool("library_enabled") and self._waves_prefs.get("library_source") == "download":
            now = time.monotonic()
            if not self._library_dl_debounce.isActive():
                self._library_dl_burst_start = now
            if now - self._library_dl_burst_start >= _LIBRARY_DL_MAX_DEBOUNCE_S:
                self._library_dl_debounce.stop()
                self._library_dl_burst_start = now
                self._rebuild_library_index()
            else:
                self._library_dl_debounce.start()  # restart the settle window

    @Slot(result=str)
    def libraryScanStatus(self) -> str:
        """The state of the music-library scan behind the ownership badge:
        'scanning' (a build is running now), else the last outcome: 'ok'
        (scanned), 'unset' (no folder set), 'missing' (folder absent/offline), or
        'unreadable' (exists but the OS denied listing it, e.g. a network or
        external drive without permission). Lets Settings explain a blank badge
        and show that a long first scan of a NAS library is making progress."""
        if self._library_index_building:
            return "scanning"
        return self._library_scan_status

    @Slot(result=bool)
    def libraryListingReconciled(self) -> bool:
        """True when the folders an untrusted listing left out have all been
        recovered through a fresh mount, so the library is complete even though
        the listing was not. Settings tells the two states apart: one is a
        warning that badges may be missing, the other is a note that they are
        not."""
        return bool(getattr(self, "_library_listing_reconciled", False))

    @Slot(result=bool)
    def libraryScanPartial(self) -> bool:
        """Did the last scan meet a folder listing it could not trust? True on
        a share whose directory enumeration repeats or truncates (the scan
        still reports 'ok': the folder WAS listed, just not completely). The
        badges then ask the disk by name behind every miss, and Settings says
        so in words, so a blank badge on such a share is explained instead of
        silently wrong."""
        return bool(self._library_scan_partial)

    @Slot(result="QVariant")
    def libraryListingShape(self) -> dict:
        """How badly the folder listing was truncated: {entries, distinct},
        the number of entries the system handed over and how many of them were
        different folders. Both 0 when every listing was trusted. This is what
        lets Settings say what "incomplete" actually means instead of leaving
        the user to guess."""
        handed, distinct = getattr(self, "_library_listing_shape", (0, 0))
        return {"entries": int(handed), "distinct": int(distinct)}

    @Slot(result=bool)
    def libraryIndexReady(self) -> bool:
        """Has the presence index answered even once yet? Every presence slot
        reports 'not present' until the first publish, so a page BUILT in that
        window renders with no badges at all and then lights every one of them
        in a single frame when the publish lands. A page that can wait (the
        search build veil) asks this first and holds.

        True when no library is configured, which is the factory default: an
        install that never opted in must never wait on a scan it will not run.
        Also true the moment a seed or a scan has published, including a scan
        that is still running: a partial index is a real answer, and the rest
        arrives as the scan finds it.

        A root that cannot be read is an answer too, and the only one coming.
        A folder that is absent or permission-blocked never publishes at all
        (refresh returns before it stamps a scan root, so nothing satisfies the
        publish gate), and reading this as "not yet" left every search for the
        rest of the session waiting out the veil's guard for badges that were
        never on their way. Settings already says so in words; this says the
        same thing to the page."""
        if not self._library_root():
            return True
        if self._library_index is not None:
            return True
        return self._library_scan_status in (SCAN_MISSING, SCAN_UNREADABLE)

    @Slot(result=bool)
    def downloadsInsideLibrary(self) -> bool:
        """Do fresh downloads land inside the scanned library folder? Drives
        the done-face wording: a finished download may only read IN LIBRARY
        when the library will actually contain it, either because downloads
        land inside the library root (this answer) or because the scan later
        proves the copy present (libPresent). With a separate download folder
        the face says DOWNLOADED instead, and moving the files into the
        library flips it through the normal rescan path.

        Pure string comparison on the configured paths, no filesystem access:
        this is read from QML bindings on the GUI thread, and a stat against a
        dead network mount can hang for many seconds. In download-source mode
        the library root IS the download folder, so the answer is trivially
        true whenever a root resolves."""
        root = self._library_root()
        if not root:
            return False
        base = (self.settings.data.download_base_path or "").strip()
        if not base:
            return False
        try:
            r = os.path.normcase(os.path.normpath(os.path.expanduser(root)))
            b = os.path.normcase(os.path.normpath(os.path.expanduser(base)))
        except Exception:
            return False
        if sys.platform == "darwin":
            # normcase only folds case on Windows, but macOS volumes are
            # case-insensitive by default (APFS and HFS+ both), so /Music and
            # /music name the SAME folder and the unfolded compare would call
            # a download folder separate from the library it sits in. This is
            # the same bet Windows already makes through normcase, on the same
            # grounds: the platform default wins, and the opt-in exception
            # (case-sensitive APFS, NTFS per-directory case sensitivity) is
            # rare enough that neither normcase nor this may stat to find out.
            r, b = r.lower(), b.lower()
        return b == r or b.startswith(r + os.sep)

    @Slot()
    def rescanLibrary(self) -> None:
        """Rescan the music library now (Settings' Rescan button), for when the
        user just added music from outside Waves and wants badges without waiting
        for the hourly sweep. Forces a FULL re-list (not the cheap mtime sweep the
        automatic rebuilds run), so it always finds a moved-in album even on a
        network mount that fails to bump the parent folder's mtime; only changed
        albums are re-read, so it stays quick. Coalesces with any scan already
        running (the in-flight scan finishes, then one more full pass runs)."""
        self._rebuild_library_index(force_full=True)

    @Slot(result="QVariant")
    def libraryScanProgress(self) -> dict:
        """Live progress of the running library scan, for the Settings note:
        {phase: ''|'walk'|'read'|'done', found, checked, done, total, indexed,
        artist, album, eta_secs}. 'walk' counts album folders discovered (found)
        and directory listings performed (checked, the number that moves the
        whole time); 'read' carries done/total tag reads, the artist/album just
        read, and an ETA in seconds (-1 while unknown) computed from the read
        rate so far. Empty dict before the first scan reports anything."""
        return dict(self._library_scan_progress)

    @Slot(str, str, str, int, result="QVariant")
    @Slot(str, str, str, int, int, result="QVariant")
    def libraryAlbumPresence(self, artist, title, year, num_tracks, duration=0):
        """Synchronous: is this TIDAL album already in your local music library?
        Answered from the finished in-memory scan index (no disk I/O on the GUI
        thread), so there is no staleness race. Returns {present, partial,
        local_album_id, local_tracks, local_year} plus the local_* quality
        readout, where local_album_id is the matched album's folder path. A
        not-built index returns present False, so the badge simply stays
        hidden.

        The optional duration is the release's total play length in seconds
        (TIDAL's number); the matcher weighs it against the folder's summed
        file lengths as a second identity witness. Callers without it get the
        four-argument overload and years remain the only proof."""
        idx = self._library_index
        if idx is None:
            self._library_probe_miss(artist)
            return {"present": False}
        # Memoized per index object: a republish makes EVERY visible pill
        # re-ask, and scrolling re-asks per row, all against the same index,
        # so the matcher kept re-deriving identical verdicts. The memo resets
        # exactly when the index is swapped (the moment libraryPresenceChanged
        # fires), so the always-on freshness rule holds; the MusicBrainz
        # overlay is applied OUTSIDE the memo (its verdict map can gain an
        # answer without the index changing). _mb_arbitrated never mutates the
        # verdict it is handed (it copies to overlay), so sharing the memoized
        # dict is safe.
        if self._presence_memo_src is not idx:
            self._presence_memo = {}
            self._presence_memo_src = idx
        key = (title, artist, year, num_tracks, duration)
        verdict = self._presence_memo.get(key)
        if verdict is None:
            verdict = matching.decide_presence(title, artist, year, num_tracks, idx, duration)
            if len(self._presence_memo) >= _PRESENCE_MEMO_MAX:
                self._presence_memo.pop(next(iter(self._presence_memo)))
            self._presence_memo[key] = verdict
        if not verdict.get("present"):
            # A miss on a share whose listing cannot be trusted is not an
            # answer yet: ask the disk by name (see the probe section). A
            # hit republishes and this pill re-asks; nothing else happens
            # on the GUI thread. getattr: partial test stubs bind this slot
            # without the probe family.
            probe = getattr(self, "_library_probe_async", None)
            if probe is not None:
                probe(artist)
        return self._mb_arbitrated(verdict, title, artist, year, num_tracks, duration)

    @Slot(str, str, result="QVariant")
    @Slot(str, str, str, str, result="QVariant")
    @Slot(str, str, str, str, int, result="QVariant")
    def libraryTrackPresence(self, artist, title, album="", album_year="", duration=0):
        """Synchronous: is this exact TRACK already in your local music library?
        Answered from the per-track index built in the same pass as the album
        one (no disk I/O on the GUI thread). Returns {present, sure,
        local_album_id, local_quality, local_class}, where local_album_id is
        the holding folder's path so the pill can reveal it. A not-built index
        returns present False, so the pill simply stays hidden. Two consumers,
        both display: the pill beside a track title, and the track button's
        claim face, whose click opens the claim gate rather than downloading.
        The engine never sees this answer. A track asked for by name is still
        never skipped, because the bulk claim gate rides only on collection
        jobs, so DOWNLOAD ANYWAY on the gate really downloads.

        The optional album/album_year name the release the track belongs to, so
        the track matcher can prove the identity of what it found; the optional
        duration (TIDAL's seconds) lets the file's own play length prove or
        refute it as a second witness. A caller that leaves them all out gets
        sure False and the badge keeps its "?"; the two-argument overload
        exists for exactly that case."""
        idx = self._library_track_index
        if idx is None:
            self._library_probe_miss(artist)
            return {"present": False, "sure": False}
        # Same memo shape as the album slot above, per track index object.
        if self._track_presence_memo_src is not idx:
            self._track_presence_memo = {}
            self._track_presence_memo_src = idx
        key = (title, artist, album, album_year, duration)
        verdict = self._track_presence_memo.get(key)
        if verdict is None:
            verdict = matching.decide_track_presence(title, artist, idx, album, album_year, duration)
            if len(self._track_presence_memo) >= _PRESENCE_MEMO_MAX:
                self._track_presence_memo.pop(next(iter(self._track_presence_memo)))
            self._track_presence_memo[key] = verdict
        if not verdict.get("present"):
            probe = getattr(self, "_library_probe_async", None)  # as the album pill above
            if probe is not None:
                probe(artist)
        return verdict

    # ---- MusicBrainz arbitration (library_mb_arbiter, default off) -----------
    # An opt-in second opinion for matches the scan cannot prove: MusicBrainz
    # knows every edition of a release with its date, track count and total
    # length, which is exactly what an undated folder beside a length-less
    # TIDAL page is missing. The arbiter (waves.mb_arbiter) may only ever
    # UPGRADE an unproven verdict to proven; it never creates presence, never
    # downgrades, and the engine never sees it (the bulk gate calls the
    # matcher directly, not this overlay). Lookups run on a worker behind a
    # 1 req/s gate with definite-only caching; the badge shows the unproven
    # verdict immediately and re-resolves via libraryPresenceChanged when an
    # answer lands. OFF by default: it sends artist and album-title search
    # terms to musicbrainz.org.

    def _mb_arbiter_on(self) -> bool:
        return self._waves_pref_bool("library_enabled") and self._waves_pref_bool("library_mb_arbiter")

    def _mb_arbitrated(self, verdict: dict, title, artist, year, num_tracks, duration) -> dict:
        """Overlay a stored MusicBrainz verdict onto an unproven presence
        answer, or queue the lookup that will produce one. Synchronous and
        network-free: only the in-memory verdict map is consulted here."""
        if not verdict.get("present") or verdict.get("sure"):
            return verdict
        if not self._mb_arbiter_on():
            return verdict
        # The key carries BOTH sides' identity facts, so a rescan that changes
        # the local copy (or a different edition on screen) simply misses and
        # re-arbitrates; the arbiter's own response cache makes that free.
        key = (
            matching.presence_key(title, artist),
            str(year or ""),
            int(num_tracks or 0),
            int(duration or 0),
            verdict.get("local_album_id", ""),
            int(verdict.get("local_tracks", 0) or 0),
            int(verdict.get("local_runtime", 0) or 0),
        )
        verdicts = getattr(self, "_mb_verdicts", None)
        if verdicts is None:
            verdicts = self._mb_verdicts = {}
        known = verdicts.get(key)
        if known is True:
            proven = dict(verdict)
            proven["sure"] = True
            proven["partial"] = not proven.get("full", False)
            return proven
        if known is False:
            return verdict
        self._mb_enqueue(key, title, artist, year, num_tracks, duration, verdict)
        return verdict

    def _mb_enqueue(self, key, title, artist, year, num_tracks, duration, verdict) -> None:
        """Queue one arbitration on the worker pool, deduplicated by key. The
        result lands in the verdict map and re-announces the presence index so
        every pill re-resolves through the overlay."""
        pending = getattr(self, "_mb_pending", None)
        if pending is None:
            pending = self._mb_pending = set()
        if key in pending:
            return
        pending.add(key)
        want = {
            "title": str(title or ""),
            "artist": str(artist or ""),
            "year": str(year or ""),
            "tracks": int(num_tracks or 0),
            "duration": int(duration or 0),
        }
        local = {
            "tracks": int(verdict.get("local_tracks", 0) or 0),
            "runtime": int(verdict.get("local_runtime", 0) or 0),
        }
        # Resolved HERE, on the GUI thread, so two first arbitrations racing
        # on the pool can never each build an arbiter (the loser's sqlite
        # connection would leak). Construction is cheap: the cache connects
        # lazily on first use, which stays on the worker.
        arbiter = self._mb_arbiter_instance()

        def work():
            answer = None
            try:
                answer = arbiter.arbitrate(want, local)
            except Exception:
                logger.debug("MusicBrainz arbitration failed", exc_info=True)
            finally:
                if answer is None:
                    # Transient: forget the attempt so a later view retries.
                    self._mb_pending.discard(key)
                else:
                    self._mb_verdicts[key] = answer
                    self._mb_pending.discard(key)
                    logger.info("MusicBrainz arbitration answered: %s", "proven" if answer else "not provable")
                    if answer:
                        self._emit_from_worker("libraryPresenceChanged")

        self.threadpool.start(Worker(work))

    def _mb_arbiter_instance(self):
        """The lazily-built arbiter, its response cache beside the settings
        file (service data only: MusicBrainz URLs and bodies, no local paths)."""
        inst = getattr(self, "_mb_arbiter", None)
        if inst is None:
            from waves.mb_arbiter import MBArbiter

            path = os.path.join(os.path.dirname(self.settings.file_path), "mbarbiter.sqlite3")
            inst = self._mb_arbiter = MBArbiter(path)
        return inst

    # ---- Bulk-download claim gate (library_bulk_skip) ------------------------
    # Bulk actions (a discography queue, a collection job's per-track fan-out)
    # consult these to leave out what the scan already claims. They answer from
    # the same in-memory indexes the badges read, so they are safe on worker
    # threads (the indexes are replaced atomically, never mutated in place) and
    # they inherit the badges' honesty rules: no index means no claims, and a
    # claim is only ever "don't fetch this", never "touch that file". On a
    # share whose listing cannot be trusted they first ask the disk by name
    # (the probe section below), which CAN write a found artist's subtree into
    # the scan cache; they are worker-only, so that write never sits on the
    # GUI thread, and it is the one write a gate makes.

    def _library_bulk_skip_on(self) -> bool:
        """Whether bulk downloads consult the library scan at all: the master
        switch and the bulk-skip pref (both saved state, the latter on by
        default) must agree. Index presence is checked per lookup, not here, so
        a scan finishing mid-queue starts answering without re-asking this."""
        return self._waves_pref_bool("library_enabled") and self._waves_pref_bool("library_bulk_skip")

    def _library_claims_album(self, album) -> bool:
        """Whether the scan FULLY claims this tidalapi album: present, and
        strict on BOTH axes of the presence verdict (identity proven and
        coverage complete). Bulk actions use it to leave the whole album out before
        anything is queued. A partial or absent match returns False, so nothing
        the scan is unsure of at album grain is ever skipped at album grain (its
        tracks still answer individually through _library_claims_track).

        Deliberately stricter than the button that says IN LIBRARY, which lights
        on coverage alone and hedges an unproven match as gold MAYBE: an album
        whose identity the matcher could not prove still queues here. The two
        bars differ because their mistakes cost different things. A wrong badge
        costs a re-click, so it can afford to speak up; a wrong skip costs the
        user an album they never find out was missing, so it may not."""
        if album is None:
            return False
        title = str(getattr(album, "name", "") or "")
        artist = str(getattr(getattr(album, "artist", None), "name", "") or "")
        if not title or not artist:
            return False
        # Before deciding on an index the listing may have left this artist
        # out of: a hit republishes, so the index is re-read after. getattr:
        # partial test stubs bind the gate without the probe family.
        probe = getattr(self, "_library_probe_sync", None)
        if probe is not None:
            probe(artist)
        idx = self._library_index
        if not idx:
            return False
        year = str(getattr(album, "year", "") or "")
        try:
            # duration rides along so the gate weighs the same length witness
            # the pill does: a mis-yeared remaster the seconds vouch for is
            # skipped, and a same-count copy minutes apart is refuted, both
            # the safe direction for a decision that costs a download.
            p = matching.decide_presence(
                title,
                artist,
                year,
                int(getattr(album, "num_tracks", 0) or 0),
                idx,
                int(getattr(album, "duration", 0) or 0),
            )
        except Exception:
            # Any doubt means download: a wrong skip costs an album.
            logger.debug("Album claim lookup failed; not gating", exc_info=True)
            return False
        return bool(p.get("present")) and not p.get("partial")

    def _library_claims_track(self, artist: str, title: str, album: str = "", album_year: str = "", duration=0) -> bool:
        return self._library_track_claim(artist, title, album, album_year, duration) is not None

    def _library_track_claim(self, artist: str, title: str, album: str = "", album_year: str = "", duration=0):
        """The presence verdict when the scan claims this track (None when it
        does not), so the caller also learns the local copy's class.

        Whether the scan holds this track ALREADY FILED UNDER the release
        being fetched: present, and proven on the identity axis. Bulk actions
        use it to skip one track inside a queued collection; single-track
        clicks never consult it.

        Presence alone is not enough, and reading it that way was the bug in
        issue #24. A title and artist match every compilation, best-of and
        re-release that share them, so "you own this song somewhere" was
        skipping tracks out of an album the user had explicitly asked for: the
        folder landed short and nothing on screen said which tracks were
        missing. The album this track belongs to is what makes the question
        answerable, so it is passed in and the proven axis is required.

        Same bar, same reasoning as _library_claims_album: a wrong skip costs
        the user a track they never find out was missing, so it may not
        happen on a guess."""
        if not title or not artist:
            return None
        probe = getattr(self, "_library_probe_sync", None)  # as in _library_claims_album
        if probe is not None:
            probe(artist)
        idx = self._library_track_index
        if not idx:
            return None
        try:
            v = matching.decide_track_presence(title, artist, idx, album, album_year, duration)
        except Exception:
            logger.debug("Track claim lookup failed; not gating", exc_info=True)
            return None
        return v if bool(v.get("present")) and bool(v.get("sure")) else None

    # ---- Probe by name: what an untrusted listing hides -------------------------
    # A network share whose directory paging is broken lists the same first
    # page of artist folders over and over (see library_index._scandir_one),
    # so the walk never sees the artists past it, and every badge and every
    # gate read "not in library" for a library the user owns: duplicates were
    # downloaded on that answer. The scan flags such a listing, and from then
    # on a MISS for an artist the index has never seen is answered by asking
    # the disk directly, by name, in the spellings the naming settings would
    # write (LibraryIndex.probe_folders). A hit indexes that artist's subtree
    # and republishes a NEW index object, so every surface (both pills, the
    # artist badge, the four gates) flips without a rescan and every memo
    # keyed on the index resets. Healthy libraries never enter this path: the
    # flag is the gate, so they pay nothing. The memo and in-flight set are
    # touched from the GUI thread (pill asks) and pool threads (the job's
    # finally, the gates); each touch is one dict or set operation, and the
    # worst a race can do is one extra stat.

    def _library_recover_untrusted(self, lib, root: str, alive, on_progress=None) -> int:
        """(POOL THREAD, straight after a scan) Ask a freshly mounted copy of
        the share for the folder names its own mount would not list, and index
        whatever comes back. Returns how many folders were recovered.

        Only ever reached when the scan just flagged a listing as untrusted, so
        a healthy library pays one boolean for this. Everything after that gate
        is best effort: no better listing, no credential, a server asleep, a
        fresh listing that repeated itself too, all return 0 and leave the app
        behaving exactly as it does today. The probe by name behind every badge
        miss stays in place either way, and still covers whatever this misses.

        The names go to probe_folders as their own spellings, because they came
        off the disk: no naming-settings guesswork is needed for a name the
        filesystem just handed over. That call stats each one under every
        flagged folder, skipping the ones the cache already holds, walks the
        subtree of each hit and writes it under the flagged folder as parent,
        which is exactly what the scan would have done had the listing named
        them. ``on_progress`` is the scan's own progress sink: a share's worth
        of recovered artists is minutes of reading, and the bar must keep
        moving through it."""
        if not bool(getattr(lib, "last_scan_partial", False)):
            return 0
        if not alive():
            return 0
        try:
            targets = list(lib.unreliable_dirs())
            if not targets:
                return 0
            config_dir = os.path.dirname(self.settings.file_path)
            # A mount point a crashed run left behind is cleaned up here rather
            # than at startup: this is the first moment one could get in the
            # way, and boot has better things to do than stat a directory for
            # a case almost nobody is in.
            smb_relist.sweep_stale(config_dir)
            recovered = smb_relist.relist_folders(
                targets,
                config_dir=config_dir,
                known={t: lib.child_names(t) for t in targets},
            )
            if not recovered or not alive():
                return 0
            wanted = sorted({name for names in recovered.values() for name in names})
            found = lib.probe_folders(
                root,
                wanted,
                alive,
                candidates=lambda name: (name,),
                timeout=_LIBRARY_RELIST_LOCK_WAIT_S,
                on_progress=on_progress,
            )
        except Exception:
            logger.debug("Recovering an untrusted listing failed; leaving the scan as it was", exc_info=True)
            return 0
        hits = int(found or 0)
        # Whether anything the fresh mount named is STILL missing from the
        # cache. The listing stays untrusted either way (the probe by name
        # stays armed behind every badge miss), but a folder that is fully
        # recovered is not an incomplete library, and Settings must not warn
        # about badges that are all there. Checked against the cache, not
        # against the hit count: most of these names were already indexed by
        # an earlier recovery, so a run that indexes nothing new is the normal
        # steady state, not a failure.
        try:
            complete = all(lib.listing_holds_all(target, names) for target, names in recovered.items())
        except Exception:
            logger.debug("Checking a recovered listing against the cache failed", exc_info=True)
            complete = False
        with contextlib.suppress(Exception):  # a stub index without the setter
            lib.note_listing_reconciled(complete)
        logger.info(
            "untrusted listing recovery: %d names read from a fresh mount, %d folders indexed, nothing left out: %s",
            len(wanted),
            hits,
            complete,
        )
        return hits

    def _library_probe_candidates(self, name: str) -> list[str]:
        """The folder spellings to try for an artist, from the naming settings
        this install writes with (so a folder Waves itself created is found
        first) plus the older and raw spellings; see folder_name_candidates."""
        try:
            data = self.settings.data
            replacement = safe_filename_replacement(getattr(data, "filename_illegal_replacement", "") or "")
            mapping = safe_filename_replacement_map(getattr(data, "filename_illegal_map", None) or {})
        except Exception:
            replacement, mapping = "", {}
        return folder_name_candidates(name, replacement, mapping)

    def _library_probe_wanted(self, name: str) -> str | None:
        """(any thread, lookups only) The artist key a probe is worth making
        for, or None: the last scan trusted its listings, the artist is
        already in the index, a probe for it is in flight, or one answered
        within the memo window. Various Artists is never an artist folder."""
        # getattr on the state: partial test stubs reach this through a slot
        # without the bridge's full init, and a missing flag means "trusted".
        if not getattr(self, "_library_scan_partial", False) or not name:
            return None
        if matching.is_various_artists(name):
            return None
        key = matching.norm_artist(matching.canon(name))
        if not key:
            return None
        idx = self._library_index
        if idx:
            if self._library_artist_index_src is not idx:
                # The rollup is precomputed at every publish; this is the same
                # race fallback artistLibraryPresence keeps.
                self._library_artist_index = matching.build_artist_rollup(idx)
                self._library_artist_index_src = idx
            if key in self._library_artist_index:
                return None
        if key in getattr(self, "_library_probe_inflight", ()):
            return None
        deadline = getattr(self, "_library_probe_memo", {}).get(key)
        if deadline is not None and deadline > time.monotonic():
            return None
        return key

    def _library_probe_run(self, batch: dict[str, str], timeout: float) -> bool:
        """(worker thread) Ask the cache to look for a WHOLE BATCH of artist
        names at once and, on a hit, republish both presence indexes. Returns
        whether the index changed.

        One call per batch is the point. probe_folders takes the cache lock,
        reads the directory tree and writes once per call, so twenty names cost
        barely more than one; twenty separate calls would instead have nineteen
        of them bounce off the lock and answer None, which is not an answer at
        all. A cache held by a running scan (None within ``timeout``) is not an
        answer either: those names go to the deferred set and are asked again
        the moment the scan publishes, so nothing is silently dropped."""
        with self._library_index_lock:
            gen = self._library_gen
            lib = self._library
        root = self._library_root()
        found: int | None = None
        try:
            if not root or lib is None:
                return False
            found = lib.probe_folders(
                root,
                [name for spellings in batch.values() for name in spellings],
                lambda: gen == self._library_gen,
                candidates=self._library_probe_candidates,
                timeout=timeout,
            )
            if not found or gen != self._library_gen:
                return False
            fresh, fresh_tracks = self._build_presence_indexes(lib)
            if gen != self._library_gen:
                return False
            with self._library_index_lock:
                self._library_index = fresh
                self._library_track_index = fresh_tracks
                self._library_probe_memo = {}
            self._publish_artist_rollup(fresh)
            self._emit_from_worker("libraryPresenceChanged")
        except Exception:
            logger.debug("Library probe by name failed; leaving the badge as it was", exc_info=True)
            return False
        else:
            return True
        finally:
            deadline = time.monotonic() + _LIBRARY_PROBE_MEMO_S
            with self._library_probe_gate:
                for key, spellings in batch.items():
                    if found is None:
                        # Never asked (no root, a scan still holding the cache,
                        # or a raise before the answer): remembering that as a
                        # miss would blind the badge for the memo window over a
                        # question the disk was never put. Hold it for the next
                        # publish instead.
                        if len(self._library_probe_deferred) < _LIBRARY_PROBE_QUEUE_MAX:
                            self._library_probe_deferred[key] = spellings
                    else:
                        self._library_probe_memo[key] = deadline
                    self._library_probe_inflight.discard(key)

    def _library_probe_enqueue(self, names) -> None:
        """(any thread) Queue every name worth asking about and make sure one
        drain worker is running. Cheap and lookup-only for a healthy library:
        _library_probe_wanted refuses everything when the last scan trusted its
        listings, so nothing is ever queued and no worker is ever started."""
        queued = False
        with self._library_probe_gate:
            if len(self._library_probe_pending) >= _LIBRARY_PROBE_QUEUE_MAX:
                return
            for name in names:
                key = self._library_probe_wanted(name)
                if key is None:
                    continue
                spellings = self._library_probe_pending.setdefault(key, [])
                text = str(name)
                if text in spellings or len(spellings) >= _LIBRARY_PROBE_SPELLINGS:
                    continue
                # One memo key covers several credits ("A", "A feat. B"), and the
                # folder is spelled like exactly one of them, so every distinct
                # spelling is asked rather than only the first one seen.
                spellings.append(text)
                queued = True
            if not queued or self._library_probe_draining:
                return
            self._library_probe_draining = True
        self.threadpool.start(Worker(self._library_probe_drain))

    def _library_probe_drain(self) -> None:
        """(worker thread) Ask about everything queued, in batches, until the
        queue is empty. Draining on ONE worker is deliberate: probe_folders is
        serialised on the cache anyway, so more workers would only produce more
        None answers, and a single drainer also lets a burst of misses coalesce
        into one question instead of one per badge."""
        try:
            while True:
                with self._library_probe_gate:
                    if not self._library_probe_pending:
                        self._library_probe_draining = False
                        return
                    batch = {}
                    for key in list(self._library_probe_pending)[:_LIBRARY_PROBE_CHUNK]:
                        batch[key] = self._library_probe_pending.pop(key)
                    self._library_probe_inflight.update(batch)
                self._library_probe_run(batch, 0.0)
        except Exception:
            logger.debug("Library probe drain failed", exc_info=True)
            with self._library_probe_gate:
                self._library_probe_draining = False

    def _library_probe_rearm(self) -> None:
        """(worker thread, after any publish) Re-ask whatever a running scan
        made us defer. The scan has just released the cache and republished, so
        these names can be answered now; without this they would wait for a
        badge to miss again, which is exactly what never happens on a page the
        user has already scrolled past."""
        with self._library_probe_gate:
            deferred = self._library_probe_deferred
            self._library_probe_deferred = {}
            # The cache has just been released, so the reason the bulk gate
            # was told to stop asking is gone. Clearing it here rather than
            # letting it lapse means a download started right after a scan
            # finishes gets a real answer instead of the tail of a cooldown.
            cooldown = getattr(self, "_library_gate_cooldown", None)
            if cooldown is not None:
                for key in deferred:
                    cooldown.pop(key, None)
        if deferred:
            self._library_probe_enqueue(name for spellings in deferred.values() for name in spellings)
        self._library_probe_backfill()

    def _library_probe_backfill(self) -> None:
        """(worker thread, once per session) Seed the queue with the artist
        folders Waves itself wrote into this library.

        A truncated listing hides folders, but the download record holds their
        exact names, so these are the artists the user is likeliest to be told
        they do not own when they do, and the ones a duplicate download would
        hurt most. Names already known to the cache cost nothing: probe_folders
        skips a spelling that matches a stored child before it stats anything,
        so a healthy or already-recovered library pays no disk asks at all."""
        if not self._library_scan_partial:
            return
        # Claimed under the gate: the launch seed and the scan both publish, on
        # different workers, and a plain check-then-set let both of them run the
        # whole seed (seen in the log as the same names queued twice).
        with self._library_probe_gate:
            if getattr(self, "_library_backfill_done", False):
                return
            self._library_backfill_done = True
        store = getattr(self, "_ownership", None)
        root = self._library_root()
        if store is None or not root:
            return
        # The download folder counts too, even when it is not the library. Waves
        # names an artist's folder the same wherever it writes it, so the names
        # it used while downloading are the names to look for inside the library.
        bases = [root]
        with contextlib.suppress(Exception):
            elsewhere = str(getattr(self.settings.data, "download_base_path", "") or "").strip()
            if elsewhere:
                bases.append(os.path.expanduser(elsewhere).rstrip(os.sep))
        names: list[str] = []
        try:
            for base in dict.fromkeys(bases):
                names.extend(store.folder_names_under(base))
        except Exception:
            logger.debug("Library backfill seed unavailable", exc_info=True)
            return
        if names:
            # Counts only. The names themselves are user content and are never logged.
            logger.info("library backfill: %d recorded folder names queued", len(names))
            self._library_probe_enqueue(names)

    def _library_probe_miss(self, name) -> None:
        """(any thread) A presence question that could not be answered from the
        index. Safe to call from anywhere and on any bridge, including the
        partial stubs the tests build, and free on a healthy library."""
        probe = getattr(self, "_library_probe_async", None)
        if probe is not None and isinstance(name, str) and name:
            probe(name)

    def _library_probe_async(self, name: str) -> None:
        """(GUI thread, from a pill's miss) Queue this artist for the batch
        drainer, never blocking the caller and never waiting on a scan."""
        self._library_probe_enqueue((name,))

    def _library_probe_many(self, names) -> None:
        """(any thread) Queue a whole page of names at once: every artist a set
        of search results, a browse shelf or a discography names. This is what
        keeps a badge correct without the user opening the artist first, and it
        is why the search page now answers the same as the artist page."""
        self._library_probe_enqueue(names)

    def _library_probe_page(self, payload) -> None:
        """(any thread) Ask about every artist a page of catalogue results
        names, as ONE batch.

        This is what makes a search answer the same as an artist page. A badge
        that misses can only ask about itself, and on a share whose listing is
        truncated the first thing the user does is search for an artist they
        own, look at a row with no mark on it and download it again. So the
        page asks on the whole set's behalf, before anything is clicked. Only
        the rows that carry an artist are used: a playlist or a mix names
        neither an artist nor a folder."""
        if not isinstance(payload, dict):
            return
        names = _page_artist_names(payload)
        # A browse page keeps its rows in shelves, each with its own items.
        for section in payload.get("sections") or ():
            if isinstance(section, dict):
                names.extend(_shelf_artist_names(section.get("items")))
        top = payload.get("top")
        if isinstance(top, dict):
            names.extend(top[f] for f in ("artist", "name") if _nonblank(top.get(f)))
        if names:
            self._library_probe_many(names)

    def _library_gate_ready(self, key: str) -> bool:
        """(worker thread) Whether a download gate should spend a wait on this
        artist, or has just been told the cache is busy. Only the gates consult
        this: a badge that misses may always ask again, which is why a deferral
        never touches the memo."""
        deadline = getattr(self, "_library_gate_cooldown", {}).get(key)
        return deadline is None or deadline <= time.monotonic()

    def _library_gate_defer(self, keys) -> None:
        """(worker thread) Remember that the cache was busy for these keys, so
        the rest of a bulk action stops paying the gate wait to be told the
        same thing. Only keys the run actually deferred are stamped."""
        cooldown = getattr(self, "_library_gate_cooldown", None)
        if cooldown is None:
            # A partial test stub reaches the gates without the bridge's full
            # init. Created on the writer rather than demanded of the caller,
            # so the suppression behaves the same wherever the gate runs.
            cooldown = {}
            self._library_gate_cooldown = cooldown
        deadline = time.monotonic() + _LIBRARY_GATE_COOLDOWN_S
        with self._library_probe_gate:
            for key in keys:
                if key in self._library_probe_deferred:
                    cooldown[key] = deadline

    def _library_probe_sync(self, name: str) -> None:
        """(worker thread only: the download gates) Probe before deciding,
        waiting a bounded time for a running scan to release the cache. Past
        the wait the gate decides on what it has, which is the direction a
        gate already errs in (a wrong skip costs an album, a download does
        not). Keyed by artist and memoised, so a discography loop pays one
        probe for all its albums."""
        key = self._library_probe_wanted(name)
        if key is None or not self._library_gate_ready(key):
            return
        with self._library_probe_gate:
            self._library_probe_pending.pop(key, None)
            self._library_probe_inflight.add(key)
        self._library_probe_run({key: [name]}, _LIBRARY_PROBE_GATE_WAIT_S)
        self._library_gate_defer([key])

    def _library_probe_sync_many(self, names) -> None:
        """(worker thread only: the bulk download gates) One probe for a whole
        set of artists, made before the gate is asked about the first album.

        _library_probe_sync is keyed per artist, so a discography loop already
        pays one probe for all its albums. A bulk action over a PLAYLIST is a
        different shape: sixty albums by sixty artists is sixty probes, each
        taking the cache lock on its own and each waiting out a running scan on
        its own. Asked together they cost one lock, one directory read and one
        wait, which is the same reason probe_folders takes a batch at all."""
        batch: dict[str, list[str]] = {}
        for name in names:
            key = self._library_probe_wanted(name)
            if key is None or not self._library_gate_ready(key):
                continue
            spellings = batch.setdefault(key, [])
            text = str(name)
            # One key covers several credits ("A", "A feat. B") and the folder
            # is spelled like exactly one of them, so each is asked.
            if text not in spellings and len(spellings) < _LIBRARY_PROBE_SPELLINGS:
                spellings.append(text)
        if not batch:
            return
        with self._library_probe_gate:
            for key in batch:
                self._library_probe_pending.pop(key, None)
                self._library_probe_inflight.add(key)
        self._library_probe_run(batch, _LIBRARY_PROBE_GATE_WAIT_S)
        self._library_gate_defer(batch)

    def _library_probe_track_rows(self, _ident, rows) -> None:
        """(any thread) Ask about every artist a flat list of track rows names.

        The playlist page is the one bulk-download surface whose rows never
        reached _library_probe_page: its signal carries a track list rather
        than a results payload, so the shape did not fit. It is also the
        surface where asking early pays most, since a playlist's tracks are by
        many different artists and the download gate would otherwise meet every
        one of them cold."""
        names = _shelf_artist_names(rows)
        if names:
            self._library_probe_many(names)

    @Slot(str, result="QVariant")
    def artistLibraryPresence(self, name):
        """Synchronous: how much of this artist do you already own locally?
        Answered from the same finished in-memory scan index as
        libraryAlbumPresence (no disk I/O on the GUI thread), rolled up per
        artist. Returns {present, albums, tracks, lossless}; a not-built index or
        an artist with nothing on disk returns present False, so the badge simply
        stays hidden. The rollup is derived lazily and cached until the album
        index is rebuilt (a fresh dict object), so repeat calls are a dict.get."""
        idx = self._library_index
        if not idx or not name:
            self._library_probe_miss(name)
            return {"present": False, "albums": 0, "tracks": 0}
        if self._library_artist_index_src is not idx:
            self._library_artist_index = matching.build_artist_rollup(idx)
            self._library_artist_index_src = idx
        if matching.is_various_artists(name):
            return {"present": False, "albums": 0, "tracks": 0}
        hit = self._library_artist_index.get(matching.norm_artist(matching.canon(name)))
        if hit:
            return hit
        probe = getattr(self, "_library_probe_async", None)  # a miss on an untrusted listing: ask the disk
        if probe is not None:
            probe(name)
        return {"present": False, "albums": 0, "tracks": 0}

    @Slot(str)
    def revealLibraryAlbum(self, path: str) -> None:
        """Open the OS file manager at a matched local album folder (badge click).
        Falls back to the nearest existing ancestor so it never fails. The
        ancestor walk stats what is often a network path, and a dead mount can
        hang a stat for many seconds, so it runs on a worker; only the final
        openUrl is marshalled back to the GUI thread (via _revealResolved),
        where Qt requires it."""
        raw = (path or "").strip()
        if not raw:
            return

        def work() -> None:
            target = pathlib.Path(raw).expanduser()
            while not target.exists() and target != target.parent:
                target = target.parent
            self._revealResolved.emit(str(target))

        self.threadpool.start(Worker(work))

    def _on_reveal_resolved(self, target: str) -> None:
        """(GUI thread) Open the file manager at the worker-resolved folder."""
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(target))

    # ---- Library source picker (Settings) -----------------------------------
    # The three backing prefs live in waves.json (library_enabled, off by
    # default, plus library_source / library_folder). The Settings card stages
    # edits into the page's editMap and SAVE CHANGES commits them together
    # through applySettings -> setWavesPref, whose per-key branches drop the
    # previous configuration's badges; applySettings then starts the first scan
    # of an enabled, configured library itself. Nothing here writes prefs.

    @Slot(result=str)
    def librarySource(self) -> str:
        """Where the ownership-badge scan looks: 'download' (the same folder Waves
        downloads to) or 'separate' (a folder the user picked, the default)."""
        return "download" if self._waves_prefs.get("library_source") == "download" else "separate"

    @Slot(result=str)
    def libraryDownloadFolder(self) -> str:
        """The current download folder, read live from settings, so the library
        card can show (and gate its hints and Rescan on) the folder a 'download' source
        would scan. Read-only: the download folder itself is edited through the
        normal Save flow. (Named library-scoped because the bridge already has a
        downloadFolder(folder_id) action slot.)"""
        return str(self.settings.data.download_base_path or "")
