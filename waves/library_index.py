"""Local music-library index: answers "do I already have this album, and how
many of its tracks" from the user's own files on disk.

This is what lets the ownership badge recognise a whole library, not only what
Waves downloaded. It walks a configured library folder, treats every directory
that directly holds audio files as an album, reads that album's identity from
the TAGS of one representative file (album / album-artist / date), and counts
the audio files for the track total. The raw per-album facts are cached in a
small sqlite file so a relaunch is a cheap stat sweep instead of a full re-scan.

It never DECIDES ownership matching itself: it just enumerates album facts. The
caller feeds those facts to ``waves.matching``, which is where every "is this
the same album?" question in the app is answered, so the matching brain stays
in one place.

Nothing here reads album art or serves a browsable view of the library: the
index exists to answer "do I have this", and one file open per album folder is
the whole cost of a scan.

Identity comes from tags, never the folder name: the download path is lossily
sanitised on write (a "?" or "/" in a title is dropped), so the folder cannot be
trusted to reconstruct the real album/artist strings. Tags hold the truth.

Pure standard library plus mutagen for tag reads, with no Qt and no tidalapi, so
it unit tests without the GUI stack.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import sqlite3
import stat as stat_mod
import time
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from queue import SimpleQueue
from threading import Lock

from waves.poolgauge import PoolGauge

# Child of the "waves" logger so scan milestones feed the breadcrumb ring crash
# reports are stitched from. Only outcomes, counts and timings are logged here:
# never the library path, folder names, or an album's artist/title.
logger = logging.getLogger("waves.library")

# Audio file extensions we treat as album tracks. A superset of what Waves
# writes (see waves.constants.AudioExtensionsValid), widened to the common
# formats a pre-existing, ripped-or-purchased library actually contains, so an
# album Waves did not create is still recognised.
AUDIO_EXTS = frozenset(
    {
        ".flac",
        ".m4a",
        ".mp4",
        ".mp3",
        ".ogg",
        ".oga",
        ".opus",
        ".spx",
        ".alac",
        ".aac",
        ".wav",
        ".aiff",
        ".aif",
        ".aifc",
        ".wma",
        ".ape",
        ".wv",
        ".mpc",
        ".tta",
        ".tak",
        ".ofr",
        ".dsf",
        ".dff",
    }
)

# Directory names that never hold an album and must not be walked. Two groups:
# Waves' own non-album exports (so a playlist/mix/video folder is never mistaken
# for an album), and the metadata/thumbnail/recycle folders a NAS or OS writes
# under EVERY folder. The second group matters enormously: a Synology writes an
# @eaDir under every directory, each holding a thumbnail subfolder per media
# file, so a library of ~18k real folders walks as ~85k without this prune (the
# extra dirs hold no audio, so "albums found" stayed correct while "checked"
# ballooned and the walk pegged a core crawling phantom folders).
_SKIP_DIR_NAMES = frozenset(
    {
        # Waves' own exports under the download root.
        "Playlists",
        "Playlist",
        "Mix",
        "Mixes",
        "Videos",
        "Video",
        # NAS + OS metadata / thumbnail / recycle folders (dot-prefixed ones such
        # as .@__thumb, .Spotlight-V100, .Trashes are already skipped below).
        "@eaDir",  # Synology thumbnail/metadata, one under every folder
        "@Recycle",  # QNAP recycle bin
        "@Transcode",  # QNAP transcode cache
        "#recycle",  # Synology recycle bin
        "#snapshot",  # Synology snapshots
        "$RECYCLE.BIN",  # Windows recycle bin
        "System Volume Information",  # Windows volume metadata
        "lost+found",  # Linux fsck orphans
    }
)

# Concurrent tag reads during a cold scan. The reads are pure IO latency (open +
# header read of one file per album), so on a NAS this multiplies throughput
# nearly linearly; 8 stays polite to an SMB server while cutting a multi-
# thousand-album first scan from the better part of an hour to minutes.
_READ_WORKERS = 8

# Concurrent directory listings during discovery, for the same reason: each
# folder is one network round trip, and a big library has thousands of them.
_WALK_WORKERS = 8

# Both pools on a NETWORK root. Sixteen threads hammering a cold SMB share is a
# thundering herd: macOS in particular funnels every process that touches a
# wedged mount into uninterruptible kernel I/O, and a share driven to the point
# of stalling froze not just the scan but the whole desktop (Finder, the Dock,
# the volume machinery all touch /Volumes). A share is latency-bound anyway, so
# two workers keep the linear win over one while staying far from the cliff.
_NETWORK_WORKERS = 2

# How many directory rows to accumulate before flushing the persistent walk to
# disk. The flush is what makes discovery RESUMABLE: a scan killed mid-walk
# leaves committed checkpoints, so the next launch skips the folders already
# listed instead of re-crawling the whole tree.
_WALK_COMMIT_EVERY = 512

# How long a folder short of one track row per counted file RESTS before its
# re-read is retried. A missing row usually means a transient NAS hiccup, and
# retrying is how it heals; but a permanently unreadable file (a 0-byte
# interrupted rip, a corrupt header, a broken symlink named like audio) can
# never yield its row, and an unbounded retry re-read the ENTIRE folder's tags
# on every scan, forever, over the network. One bounded retry per window keeps
# the healing and caps the cost.
_UNREADABLE_RETRY_S = 24 * 3600.0

# The wall-clock spread two empty-walk strikes need before an empty library is
# believed and pruned (see _confirm_empty). Counting SCANS was not a duration:
# the container poll runs one every five minutes, so a mount that dropped over
# lunch had "two consecutive empty scans" in ten minutes flat.
_EMPTY_STRIKE_GAP_S = 30 * 60.0


#: The scanner's three pools, as gauges (waves.poolgauge, the gauge class the
#: scanner's private one was promoted into so the download engine shares it).
#: Module-level because the executors are per-scan and the sampler needs one
#: stable object per pool.
WALK_GAUGE = PoolGauge(_WALK_WORKERS)
READ_GAUGE = PoolGauge(_READ_WORKERS)
POLL_GAUGE = PoolGauge(_WALK_WORKERS)

# Outcome of the last refresh(), so the UI can tell "your library is empty" from
# "the folder exists but the OS won't let me read it" (a silently-swallowed
# permission error otherwise looks identical to an empty folder and blanks every
# badge). Stored on the instance as ``last_scan_status`` after each refresh.
SCAN_OK = "ok"  # the root was listed; the index reflects it
SCAN_UNSET = "unset"  # no library folder is configured
SCAN_MISSING = "missing"  # the configured folder is absent (offline drive, wrong path)
SCAN_UNREADABLE = "unreadable"  # the folder exists but the OS denied listing it (permissions)


def root_comparison_key(root: str) -> str:
    """A library root as a filesystem would compare it: expanduser, trailing
    separators of BOTH kinds dropped (a Windows "C:/Music/" and "C:\\Music"
    name one folder), then NFC + casefold (APFS and NTFS treat two case or
    normalisation spellings as one folder).

    The cache FILENAME hashes this key, so every comparison against a stored
    ``scan_root`` must go through the same key. Comparing raw spellings while
    the filename compared keys is how a respelled root (a file dialog handing
    back NFD, a casing change) landed on the RIGHT cache file but then refused
    to adopt the legacy cache into it, refused the launch badge seed from it,
    and wiped its dirs tree as a "root change"."""
    norm = os.path.expanduser(str(root or "")).rstrip(os.sep + (os.altsep or ""))
    return unicodedata.normalize("NFC", norm).casefold()


def cache_file_for_root(config_dir: str, root: str) -> str:
    """The cache file that belongs to ``root``: one sqlite file PER library
    folder, so choosing a new folder never costs the old folder its scan.

    The old single-file design wiped on a root change (DELETE FROM dirs, then
    the generation prune), so a user who tried another folder and came back
    started their thousands-of-albums NAS scan from zero. Keying the file by
    the root keeps every scanned library warm: switching back reopens its file
    and the next scan is the usual cheap stat sweep. Only the file for the
    CURRENT root is ever opened, so badges never show another library's albums.

    The key is the root as a filesystem would compare it (expanduser, trailing
    separator dropped, NFC + casefold, see name_comparison_key's rationale):
    APFS and NTFS treat two case spellings as one folder, so both must map to
    one cache. Hashed so no piece of the user's folder path appears in a
    filename inside the config directory.

    The first call that finds no per-root file but a legacy ``library.sqlite3``
    scanned against this very root ADOPTS it (a rename, sidecars included), so
    an existing user's cache survives the upgrade instead of rescanning.

    An empty root answers the legacy name: nothing is ever scanned into it
    while no library is configured, and it keeps the constructor total.
    """
    legacy = os.path.join(config_dir, "library.sqlite3")
    key = root_comparison_key(root)
    if not key:
        return legacy
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    path = os.path.join(config_dir, f"library-{digest}.sqlite3")
    if not os.path.exists(path) and os.path.exists(legacy):
        _adopt_legacy_cache(legacy, path, key)
    return path


def _adopt_legacy_cache(legacy: str, path: str, key: str) -> None:
    """One-time upgrade rename of the single-file cache into its per-root name.

    Only a legacy file whose committed ``scan_root`` is this very root
    (compared by root_comparison_key, the same key the target filename hashes,
    so a respelling of one folder still adopts) is adopted; any other (a
    different folder's data, an unreadable file) is left alone and the per-root
    file starts empty, which is the pre-existing cost of a root change, not a
    new one. Probed read-only through sqlite so a corrupt file cannot raise
    past here, and the WAL/SHM sidecars move with the database or recent
    commits would be left behind under the old name.
    Best-effort throughout: a failed adoption costs one rescan, never startup.
    """
    try:
        conn = sqlite3.connect(legacy)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key = 'scan_root'").fetchone()
            adopt = bool(row) and root_comparison_key(str(row[0] or "")) == key
            if adopt:
                # Fold the WAL into the main file BEFORE any rename: the
                # database is then self-contained, so a crash between the
                # renames below cannot separate recent commits (in a -wal
                # still under the old name) from the database that owns them.
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        if not adopt:
            return
        os.replace(legacy, path)
        # Belt and braces: after the checkpoint these are normally gone, but a
        # leftover sidecar under the old name must not shadow a future legacy
        # file, so sweep whatever remains across too.
        for ext in ("-wal", "-shm"):
            if os.path.exists(legacy + ext):
                os.replace(legacy + ext, path + ext)
        logger.info("adopted the legacy library cache into its per-root file")
    except Exception:
        logger.debug("Legacy library cache was not adoptable; starting this root fresh", exc_info=True)


# How many of a folder's audio files are read to decide whether they really are
# ONE album. Identity comes from the representative (first) file, but the track
# count used to come from the raw file count, which is only the album's track
# count when every file in the folder belongs to that album. A "Singles",
# "Inbox" or iTunes-dump folder broke that badly: 30 unrelated tracks whose
# first file happened to be tagged "Discovery" indexed as a 30-track Discovery,
# which then satisfied every completeness test and claimed a full copy of an
# album the user owned one song of. The scan used to sample 3 files per folder
# to keep a cold NAS scan cheap; since the track-level presence pill needs the
# TITLE of every file anyway, every file is now read once, and the same reads
# double as a full-folder agreement check. On disagreement the folder is still
# indexed (its identity and quality are real) but with a track count of 0,
# which makes the matcher decline to claim it rather than claim it wrongly.

# (dirpath, representative file, dir mtime, audio count, all audio files)
_Candidate = tuple[str, str, float, int, tuple[str, ...]]


def _numbered(value: str) -> tuple[int, int]:
    """A "3/12" style tag value as ``(number, total)``, either half 0 when
    absent or unparseable. FLAC keeps the two halves in separate tags
    (tracknumber + tracktotal), ID3 and MP4 pack them into one field, so both
    shapes have to read the same way."""

    def one(part: str) -> int:
        try:
            return max(0, int(str(part).strip() or 0))
        except (TypeError, ValueError):
            return 0

    number, _, total = str(value or "").partition("/")
    return one(number), one(total)


def _read_album_tags(path: str) -> dict | None:
    """Album / album-artist / date from one audio file's tags, via mutagen's
    format-neutral 'easy' interface (standard frames across FLAC/MP3/MP4/OGG), so
    files ripped by any tool read the same way. Also captures the stream's
    quality facts (codec, bitrate, bit depth, sample rate), free with the same
    file open, so the ownership badge can say WHAT you have (MP3 128 vs FLAC
    24-bit) and flag upgrades. Returns None if unreadable."""
    try:
        import mutagen

        m = mutagen.File(path, easy=True)
    except Exception:
        return None
    if m is None:
        return None

    def first(*keys: str) -> str:
        for key in keys:
            try:
                val = m.get(key)
            except Exception:
                val = None
            if val:
                return str(val[0] if isinstance(val, list) else val).strip()
        return ""

    album = first("album")
    # album-artist is the album's identity; fall back to the track artist when a
    # ripper left album-artist blank (common on single-artist albums).
    artist = first("albumartist") or first("artist")
    # The file's OWN identity, for the per-track rows: its title, and its track
    # artist first (a featured guest is credited there, not in album-artist).
    title = first("title")
    track_artist = first("artist") or first("albumartist")
    date = first("date", "originaldate", "year")
    # The release's OWN claim about its shape: how many tracks it has, and
    # which disc of how many this file is. Counting the audio files in a folder
    # says what the user HOLDS; only this says what the release CONTAINS, which
    # is the difference between a complete copy and a copy short a track, and
    # the difference between a missing disc and a one-disc release.
    track_total = _numbered(first("tracknumber", "track"))[1] or _numbered(first("tracktotal", "totaltracks"))[0]
    disc_no, disc_total = _numbered(first("discnumber", "disc"))
    disc_total = disc_total or _numbered(first("disctotal", "totaldiscs"))[0]
    info = getattr(m, "info", None)
    codec = os.path.splitext(path)[1].lower().lstrip(".")
    if codec in ("m4a", "mp4"):
        # The .m4a container holds either lossy AAC or lossless ALAC; mutagen's
        # MP4 info exposes which.
        codec = "alac" if str(getattr(info, "codec", "") or "").startswith("alac") else "aac"
    elif codec in ("ogg", "oga"):
        # The Ogg container holds Vorbis, Opus, FLAC or Speex; mutagen's class
        # name says which, so a lossless OggFLAC is never misfiled as lossy.
        kind = type(m).__name__.lower()
        codec = "opus" if "opus" in kind else "flac" if "flac" in kind else "speex" if "speex" in kind else "vorbis"
    elif codec in ("aif", "aifc"):
        codec = "aiff"
    elif codec == "spx":
        codec = "speex"
    return {
        "album": album,
        "artist": artist,
        "title": title,
        "track_artist": track_artist,
        "date": date,
        "track_total": track_total,
        "disc_no": disc_no,
        "disc_total": disc_total,
        "codec": codec,
        "bitrate": int(getattr(info, "bitrate", 0) or 0) // 1000,  # kbps
        "bits": int(getattr(info, "bits_per_sample", 0) or 0),
        "rate": int(getattr(info, "sample_rate", 0) or 0),
        # The stream's play length in whole seconds, free with the same open.
        # This is the one identity fact the FILE knows that no tag has to say:
        # summed over a folder it is close to a fingerprint of the release, so
        # the matcher can prove (or refute) an otherwise undated match.
        "length": int(round(float(getattr(info, "length", 0) or 0))),
    }


def _is_audio(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in AUDIO_EXTS


class _RateLimitedEmit:
    """The scan's progress-event gate: forwards to the callback at most once per
    ``interval`` seconds, so a live per-item count cannot flood the GUI with
    thousands of signals on a fast scan. Milestones (phase changes, database
    flushes, completion) pass ``force=True`` to bypass the limit so nothing the
    UI must not miss lands stale. A None callback swallows everything."""

    def __init__(self, callback: Callable[[dict], None] | None, interval: float) -> None:
        self._callback = callback
        self._interval = interval
        self._last = 0.0

    def __call__(self, payload: dict, *, force: bool = False) -> None:
        if not self._callback:
            return
        now = time.monotonic()
        if not force and now - self._last < self._interval:
            return
        self._last = now
        self._callback(payload)


class LibraryIndex:
    """A sqlite cache of album folders found under a library root: one row per
    folder that directly holds audio, storing the tag-read album/artist/date, the
    audio-file count, and the folder's mtime for incremental re-scan.

    A second table, ``dirs``, persists the whole directory TREE (every folder
    walked, its parent, its mtime, and whether it has been fully listed). This is
    what makes discovery both INCREMENTAL and RESUMABLE: a warm relaunch stats
    each folder and re-lists only the ones whose mtime changed (a container whose
    mtime is unchanged had no direct child added/removed/renamed, so its listing
    is reused from the table), and a scan killed mid-walk resumes from its last
    committed checkpoint instead of re-crawling everything. A per-scan generation
    stamp drives pruning: folders not visited this scan (deleted, moved, or a
    whole previous root) are dropped at the end.

    Thread-safe like OwnershipStore: opened check_same_thread=False under an
    instance lock, WAL mode. The scan's pool workers only touch the filesystem
    (stat + scandir), so no shared state needs locking beyond the connection
    guard. Reads DO come from other threads (the bridge's startup badge seed
    runs beside a scan), and the guard makes each of those atomic on its own; a
    caller that needs two reads to describe ONE moment has to say so, since a
    commit can land between them.

    The tag reader and audio predicate are injectable so the scan logic unit
    tests without real audio files.
    """

    def __init__(
        self,
        db_path: str,
        *,
        read_tags: Callable[[str], dict | None] = _read_album_tags,
        is_audio: Callable[[str], bool] = _is_audio,
    ) -> None:
        self._path = str(db_path)
        parent = os.path.dirname(self._path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._read_tags = read_tags
        self._is_audio = is_audio
        # Outcome of the most recent refresh() (see the SCAN_* constants). Read by
        # the backend on the same worker that refreshed, so no cross-thread race.
        self.last_scan_status = SCAN_UNSET
        # Pool sizes for the scan phases, re-sized by every refresh() from its
        # root_is_local verdict; full-size here so direct calls into the walk
        # or read phase (tests) behave as they always did.
        self._workers_walk = _WALK_WORKERS
        self._workers_read = _READ_WORKERS
        self._lock = Lock()
        self._closed = False
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("""CREATE TABLE IF NOT EXISTS albums (
                       folder_path TEXT    NOT NULL PRIMARY KEY,
                       album       TEXT,
                       artist      TEXT,
                       year        TEXT,
                       track_count INTEGER NOT NULL DEFAULT 0,
                       dir_mtime   REAL    NOT NULL DEFAULT 0,
                       recorded_at INTEGER NOT NULL DEFAULT 0,
                       codec       TEXT,
                       bitrate     INTEGER NOT NULL DEFAULT 0,
                       bits        INTEGER NOT NULL DEFAULT 0,
                       rate        INTEGER NOT NULL DEFAULT 0,
                       declared    INTEGER,
                       disc_no     INTEGER,
                       disc_total  INTEGER,
                       runtime     INTEGER
                   )""")
            # Migrate a pre-quality cache in place: the added columns default to
            # NULL codec, which _unchanged treats as "needs a re-read", so the
            # old rows backfill their quality on the next scan and nothing is
            # lost. ALTER on an already-migrated file raises "duplicate column".
            # The declared-shape columns migrate the same way and for the same
            # reason: NULL there means "read before this folder's files were
            # ever asked what the release claims to be", which is one re-read.
            for col in (
                "codec TEXT",
                "bitrate INTEGER NOT NULL DEFAULT 0",
                "bits INTEGER NOT NULL DEFAULT 0",
                "rate INTEGER NOT NULL DEFAULT 0",
                "declared INTEGER",
                "disc_no INTEGER",
                "disc_total INTEGER",
                # The folder's summed play length in seconds. NULL is a row
                # from before duration capture (one backfill re-read, like
                # declared); 0 means the files were read but could not all
                # report a length, so the sum would lie and is not stored.
                "runtime INTEGER",
                # The walk's raw audio-file count as of the last read. This is
                # what _unchanged compares against the walk, NOT track_count:
                # track_count can be a mixed-folder majority VERDICT smaller
                # than the raw count, and comparing the verdict made a grown
                # folder whose mtime never moved (an unreliable network mount)
                # read as unchanged forever, the exact blindness force_full
                # exists to cure.
                "raw_count INTEGER",
            ):
                with contextlib.suppress(sqlite3.OperationalError):  # column already exists
                    self._conn.execute(f"ALTER TABLE albums ADD COLUMN {col}")
            # Backfill a pre-raw_count row from its stored count instead of
            # forcing a whole-library tag re-read on upgrade: for a clean
            # folder track_count IS the raw count it was read at. A majority
            # verdict backfills smaller than its true raw count, so exactly
            # the mixed folders compare changed once and re-read, which
            # records their real raw_count.
            self._conn.execute("UPDATE albums SET raw_count = track_count WHERE raw_count IS NULL")
            # The directory tree behind incremental + resumable discovery. One row
            # per folder walked: mtime as of its last listing, ``listed`` (1 once
            # fully scandir'd; 0 marks a discovered-but-not-yet-listed folder, the
            # resume frontier after a crash), ``is_album`` (holds audio directly),
            # and ``seen_gen`` (the scan generation that last visited it, for
            # pruning vanished folders). Absent from a pre-dirs cache, so created
            # here; an empty dirs table simply forces one full cold walk.
            self._conn.execute("""CREATE TABLE IF NOT EXISTS dirs (
                       path      TEXT    NOT NULL PRIMARY KEY,
                       parent    TEXT,
                       mtime     REAL    NOT NULL DEFAULT 0,
                       listed    INTEGER NOT NULL DEFAULT 0,
                       is_album  INTEGER NOT NULL DEFAULT 0,
                       seen_gen  INTEGER NOT NULL DEFAULT 0
                   )""")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_dirs_parent ON dirs(parent)")
            # One row per audio FILE read, behind the track-level presence pill:
            # the file's own title and (track) artist plus its stream quality.
            # Created empty on a pre-tracks cache; an album row with no track
            # rows is owed one backfill re-read even when otherwise unchanged
            # (see _unchanged and _load_album_mtimes), which is how an existing
            # cache migrates without being thrown away.
            self._conn.execute("""CREATE TABLE IF NOT EXISTS tracks (
                       folder_path TEXT NOT NULL,
                       title       TEXT,
                       artist      TEXT,
                       codec       TEXT,
                       bitrate     INTEGER NOT NULL DEFAULT 0,
                       bits        INTEGER NOT NULL DEFAULT 0,
                       rate        INTEGER NOT NULL DEFAULT 0,
                       length      INTEGER NOT NULL DEFAULT 0
                   )""")
            # A pre-length tracks table migrates in place; its rows read length
            # 0 ("the file never said") until the album row's NULL runtime
            # forces the folder's one backfill re-read, which rewrites them.
            with contextlib.suppress(sqlite3.OperationalError):  # column already exists
                self._conn.execute("ALTER TABLE tracks ADD COLUMN length INTEGER NOT NULL DEFAULT 0")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_folder ON tracks(folder_path)")
            # Small key/value store: the scan root last walked (a change wipes the
            # tree so the new root walks fresh) and the monotonic scan generation.
            self._conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            self._conn.commit()

    @staticmethod
    def _probe_root(root: str) -> str:
        """Classify a (already normalised) scan root before walking it. Returns
        ``SCAN_OK`` to go ahead, or the SCAN_* reason a scan would find nothing.

        A blank root must NOT become os.sep ("/") and walk the whole disk. os.walk
        silently swallows a top-level permission error and yields nothing, which
        is indistinguishable from an empty library and would blank every badge, so
        probe readability here: on a TCC-gated network/external volume ``stat``
        (hence ``isdir``) succeeds while ``scandir`` raises EPERM.
        """
        if not root:
            return SCAN_UNSET
        if not os.path.isdir(root):
            return SCAN_MISSING
        try:
            with os.scandir(root) as probe:
                next(probe, None)
        except PermissionError:
            return SCAN_UNREADABLE
        except OSError:
            return SCAN_MISSING
        return SCAN_OK

    def refresh(
        self,
        root: str,
        *,
        should_continue: Callable[[], bool] | None = None,
        on_progress: Callable[[dict], None] | None = None,
        progress_interval: float = 0.15,
        force_full: bool = False,
        root_is_local: bool | None = None,
    ) -> int:
        """Incrementally re-scan ``root`` into the cache and return the number of
        album folders now indexed.

        Discovery is INCREMENTAL and RESUMABLE (see the ``dirs`` table): a warm
        relaunch re-lists only folders whose mtime changed, and a scan killed
        mid-walk resumes from its last checkpoint instead of re-crawling the whole
        tree. Only album folders whose mtime (or audio-file count) changed since
        last time are re-read, so a warm relaunch is a stat sweep. Folders that
        vanished are pruned. If ``root`` is missing or unreadable the cache is left
        untouched (a temporarily-offline NAS must not wipe every badge).
        ``should_continue`` is polled so a superseded scan (library-folder change,
        shutdown) can bail without corrupting the cache. ``last_scan_status``
        records why a scan found nothing, so the UI can tell an empty library from
        a folder it cannot read. ``force_full`` re-lists every folder regardless of
        the mtime cache (still re-reading only changed albums): the manual Rescan
        uses it as a catch-all for anything the incremental sweep could miss, such
        as a network mount that fails to bump a folder's mtime on a change.

        ``on_progress`` receives a dict describing the scan as it advances, so the
        UI can show live progress, an ETA and the album under the needle instead
        of a bare spinner: ``{"phase": "walk", "found": n, "checked": c}`` while
        album folders are being discovered ("checked" counts directory listings,
        the number that moves even while breadth-first exploration has not yet
        reached album depth), then ``{"phase": "read", "done": d, "total": t,
        "indexed": c, "artist": a, "album": b, "committed": bool}`` as tags are
        read ("committed" marks a database flush, so the caller knows when the
        partial results are queryable). Events are rate-limited to one per
        ``progress_interval`` seconds (milestones always emit), so a live count
        costs a handful of callbacks per second regardless of library size. A
        COLD scan of a big library on a NAS is latency-bound in BOTH phases (one
        directory listing per folder, then one tag read per album, each a network
        round trip), so both the walk and the reads run on small thread pools;
        this and the progress events keep a first scan of thousands of albums in
        minutes, with badges lighting up while it runs instead of only at the end.

        ``root_is_local`` sizes those pools: False (the caller classified the
        root as a network mount, see the bridge's _library_root_is_local) drops
        both to _NETWORK_WORKERS so the scan never becomes the herd that wedges
        an SMB share; True or None (unknown, e.g. a direct caller) keeps the
        full-size pools.
        """
        root = os.path.expanduser(str(root or "")).rstrip(os.sep)
        # Sized per scan, before the walk AND before the probe: only a POSITIVE
        # network verdict throttles (unknown keeps full speed, matching the
        # classifier's "confidently local" framing in reverse: only confidence
        # changes behavior). Sizing before the probe matters because the
        # container poll borrows this size between scans: a launch against an
        # offline NAS returns at the probe below, and sizing after it left the
        # poll to greet the returning share with the full-size herd the
        # throttle exists to hold back. The gauges follow so the perf sampler
        # reports saturation against the cap this scan actually runs under.
        throttled = root_is_local is False
        self._workers_walk = _NETWORK_WORKERS if throttled else _WALK_WORKERS
        self._workers_read = _NETWORK_WORKERS if throttled else _READ_WORKERS
        WALK_GAUGE.limit(self._workers_walk)
        READ_GAUGE.limit(self._workers_read)
        # Classify the root before walking (see _probe_root): an unset/missing/
        # unreadable root leaves the cache intact (no wiped badges) and records
        # why, so the UI can tell "empty" from "can't read".
        self.last_scan_status = self._probe_root(root)
        if self.last_scan_status != SCAN_OK:
            logger.info("library scan skipped: root is %s", self.last_scan_status)
            return self._count()

        started = time.monotonic()
        logger.info("library scan started (full=%s, workers=%d)", bool(force_full), self._workers_walk)
        emit = _RateLimitedEmit(on_progress, progress_interval)
        alive = should_continue or (lambda: True)
        # A superseded scan must die BEFORE _begin_scan, not merely inside the
        # walk: a worker that sat queued across a library-folder switch would
        # otherwise open a scan of the OLD root against whatever index object it
        # reaches, and _begin_scan's root-change wipe would empty that cache's
        # dirs tree and stamp the old root into it before the walk's first
        # liveness poll could bail.
        if not alive():
            # Status stays as probed, exactly like a mid-walk supersede: the
            # caller's generation guard discards this scan's outcome anyway.
            logger.info("library scan superseded before it began")
            return self._count()
        # A superseded scan must die BEFORE _begin_scan, not merely inside the
        # walk: a worker that sat queued across a library-folder switch would
        # otherwise open a scan of the OLD root against whatever index object it
        # reaches, and _begin_scan's root-change wipe would empty that cache's
        # dirs tree and stamp the old root into it before the walk's first
        # liveness poll could bail.
        # A root switch wipes the tree so the new root walks fresh; every scan
        # gets a fresh generation stamp so vanished folders can be pruned.
        gen = self._begin_scan(root)
        walked = self._walk_album_dirs(root, alive, emit, gen, force_full=force_full)
        if walked is None:
            # Superseded or the root dropped offline mid-walk: leave the cache
            # as-is (a partial tree resumes on the next scan).
            logger.info(
                "library scan bailed after %.1fs (status %s)", time.monotonic() - started, self.last_scan_status
            )
            return self._count()
        candidates, albums_seen, condemned = walked
        if albums_seen:
            # A walk that saw albums also witnesses which FILESYSTEM the root
            # sits on: the device id is what tells a later empty walk apart
            # from the ghost an unmounted share leaves behind (below).
            self._clear_empty_streak(root_dev=self._root_dev(root))
        elif self._count() > 0:
            # The walk found no albums at all, but the cache holds some. A share
            # that unmounts often leaves its mountpoint behind as an EMPTY local
            # directory, which probes perfectly readable, walks to nothing, and
            # would take the prune below straight through the whole index while
            # reporting a successful scan. Two independent guards, because
            # deleting a library really can happen and a refusal would leave
            # its badges forever:
            #
            # 1. Device identity. The albums were last seen on a filesystem
            #    whose device id was recorded above; the ghost directory sits
            #    on the PARENT volume, so its id can never match. An empty walk
            #    on the wrong device is the missing mount, full stop, and is
            #    never counted toward believing the library empty (a genuinely
            #    emptied library empties in place, same device).
            # 2. The empty-walk streak. Only a SECOND consecutive empty scan is
            #    believed and allowed to prune, and the strikes must be spread
            #    in wall-clock time (see _confirm_empty): the container poll
            #    turns downtime into scans every five minutes, and two guards
            #    that both count scans expire together.
            stored_dev = self._meta_get_locked("root_dev")
            cur_dev = self._root_dev(root)
            ghost = stored_dev is not None and cur_dev is not None and stored_dev != str(cur_dev)
            if ghost or not self._confirm_empty():
                self.last_scan_status = SCAN_MISSING
                logger.info(
                    "library scan found nothing where %d albums were cached (%s); not pruning",
                    self._count(),
                    "mount is absent" if ghost else "first empty walk",
                )
                return self._count()
        # Only changed albums are read, on a forced sweep exactly as on an
        # incremental one. What force_full buys is the LISTING above: every
        # folder is re-scandir'd, so a move-in an unreliable mount hid from the
        # mtime cache is still discovered. Re-reading the tags of albums that
        # then compare unchanged buys nothing for it, and cost the manual
        # Rescan a full tag read of the whole library (thousands of files over
        # a network mount) to find the handful the listing had already found.
        # An album that failed to read has no row at all, so it is never
        # "unchanged" and a Rescan still retries it. One query answers for
        # every candidate (see _unchanged_rows); a candidate with no album row
        # gets None and the verdict re-reads it, exactly as before.
        known = self._unchanged_rows()
        to_read = [c for c in candidates if not self._unchanged_verdict(known.get(c[0]), c[2], c[3])]
        if to_read:
            # The walk is done, so the read phase's size is now known: announce it
            # up front so the UI can show "0 of N" (and an ETA once reads flow).
            emit({"phase": "read", "done": 0, "total": len(to_read), "indexed": self._count()}, force=True)
        self._read_and_upsert(to_read, alive, emit)
        if alive():  # never prune on a bail: unvisited folders are not "gone"
            self._prune_by_gen(gen, condemned)
            # Record when a FULL re-list last completed. A ``force_full`` sweep
            # re-scandirs every folder, so it is the only kind that catches a
            # change an unreliable mount hid from the mtime cache; stamping it lets
            # the launch check fall back to one periodically. The first successful
            # scan also stamps the baseline (it lists everything anyway), so a fresh
            # install is not forced into a redundant full sweep on its next launch.
            with self._lock:
                if force_full or self._meta_get("last_full_scan") is None:
                    self._meta_set("last_full_scan", str(time.time()))
                    self._conn.commit()
        count = self._count()
        logger.info(
            "library scan finished in %.1fs: %d albums indexed, %d re-read",
            time.monotonic() - started,
            count,
            len(to_read),
        )
        return count

    def _begin_scan(self, root: str) -> int:
        """Open a scan of ``root`` and return its generation stamp. A change of
        root from the last scan wipes the directory tree (the new root walks fresh
        and the old root's albums fall out in the generation prune). The scan
        generation is a monotonic counter persisted in ``meta`` so a relaunch
        keeps pruning correctly."""
        with self._lock:
            prev = self._meta_get("scan_root")
            # Compared by key, not spelling: a respelling of one folder (NFD
            # vs NFC, a casing change) is the SAME root arriving at the same
            # cache file, and wiping its dirs tree for it threw away the warm
            # walk for nothing.
            if prev is not None and root_comparison_key(prev) != root_comparison_key(root):
                self._conn.execute("DELETE FROM dirs")
            elif prev is not None and prev != root:
                # Same root, new spelling: every stored path still carries the
                # OLD spelling as its prefix, so without this rewrite the walk
                # missed every known row (a full cold re-read) and, until the
                # prune caught up, iter_albums served BOTH spellings of every
                # album, doubling the artist rollup. Rewriting the prefix up
                # front keeps the cache warm under the new spelling. OR IGNORE:
                # if both spellings somehow coexist, the old row simply waits
                # for the generation prune instead of colliding.
                cut = len(prev) + 1
                self._conn.execute(
                    "UPDATE OR IGNORE dirs SET path = ? || substr(path, ?)" " WHERE path = ? OR substr(path, 1, ?) = ?",
                    (root, cut, prev, cut, prev + os.sep),
                )
                self._conn.execute(
                    "UPDATE OR IGNORE dirs SET parent = ? || substr(parent, ?)"
                    " WHERE parent = ? OR substr(parent, 1, ?) = ?",
                    (root, cut, prev, cut, prev + os.sep),
                )
                self._conn.execute(
                    "UPDATE OR IGNORE albums SET folder_path = ? || substr(folder_path, ?)"
                    " WHERE folder_path = ? OR substr(folder_path, 1, ?) = ?",
                    (root, cut, prev, cut, prev + os.sep),
                )
                self._conn.execute(
                    "UPDATE OR IGNORE tracks SET folder_path = ? || substr(folder_path, ?)"
                    " WHERE folder_path = ? OR substr(folder_path, 1, ?) = ?",
                    (root, cut, prev, cut, prev + os.sep),
                )
            self._meta_set("scan_root", root)
            gen = int(self._meta_get("scan_gen") or 0) + 1
            self._meta_set("scan_gen", str(gen))
            self._conn.commit()
        return gen

    def _confirm_empty(self) -> bool:
        """Record that this scan walked to nothing, and say whether an empty
        library is now BELIEVED. False the first time (the folder is treated as
        temporarily unavailable and nothing is pruned), True once a second
        counted empty scan also found nothing, which is a library that really
        was emptied in place.

        Strikes must be separated by _EMPTY_STRIKE_GAP_S of wall-clock time to
        count: the container poll manufactures a scan every five minutes, so
        two scan-counted strikes were only ever ten minutes of downtime, and
        the guard existed to survive far longer outages than that. An empty
        walk inside the gap changes nothing (the first strike keeps its
        timestamp), so however often the poll fires, believing an empty
        library takes at least one gap of persistent emptiness."""
        with self._lock:
            try:
                streak = int(self._meta_get("empty_walks") or 0)
            except (TypeError, ValueError):
                streak = 0
            try:
                last = float(self._meta_get("empty_walk_at") or 0)
            except (TypeError, ValueError):
                last = 0.0
            now = time.time()
            if streak and now - last < _EMPTY_STRIKE_GAP_S:
                return False
            streak += 1
            self._meta_set("empty_walks", str(streak))
            self._meta_set("empty_walk_at", str(now))
            self._conn.commit()
        return streak >= 2

    def _clear_empty_streak(self, root_dev: int | None = None) -> None:
        """A walk found albums, so any earlier empty result was a blip. Also
        records the device id the albums were seen on (when the caller could
        read one): the witness that lets a later empty walk tell an unmounted
        share's leftover mountpoint from a library emptied in place."""
        with self._lock:
            changed = False
            if self._meta_get("empty_walks") not in (None, "0"):
                self._meta_set("empty_walks", "0")
                changed = True
            if root_dev is not None and self._meta_get("root_dev") != str(root_dev):
                self._meta_set("root_dev", str(root_dev))
                changed = True
            if changed:
                self._conn.commit()

    @staticmethod
    def _root_dev(root: str) -> int | None:
        """The device id of the filesystem holding ``root``, or None when it
        cannot be read (the guards that consume it then stand down)."""
        try:
            return os.stat(root).st_dev
        except OSError:
            return None

    def _meta_get_locked(self, key: str) -> str | None:
        with self._lock:
            return self._meta_get(key)

    def _meta_get(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _meta_set(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    def seconds_since_full_scan(self) -> float | None:
        """Seconds since a full re-list last completed, or None if one never has.
        A full re-list is the only sweep that catches a change an unreliable mount
        hid from folder mtimes, so this drives the launch-time fallback below."""
        with self._lock:
            v = self._meta_get("last_full_scan")
        if v is None:
            return None
        try:
            return max(0.0, time.time() - float(v))
        except (TypeError, ValueError):
            return None

    def due_for_full_scan(self, max_age_s: float) -> bool:
        """True if it has been longer than ``max_age_s`` since the last full
        re-list (or there has never been one): the caller passes
        ``force_full=True`` to refresh() so an add/remove/replace the incremental
        sweep or a mount with unreliable mtimes could miss is still caught."""
        age = self.seconds_since_full_scan()
        return age is None or age > max_age_s

    def matches_scan_root(self, root: str) -> bool:
        """True if the committed cache was last scanned against ``root``
        (compared by root_comparison_key, the same key the cache filename
        hashes, so a respelling of one folder still matches). Lets a caller
        seed badges from the cache only when it is for this very folder, so
        switching library folders never briefly shows the previous library's
        badges. False before anything has ever been scanned."""
        with self._lock:
            stored = self._meta_get("scan_root")
        return stored is not None and root_comparison_key(stored) == root_comparison_key(root)

    def _walk_album_dirs(
        self,
        root: str,
        alive: Callable[[], bool],
        emit: Callable[..., None],
        gen: int,
        force_full: bool = False,
    ) -> tuple[list[_Candidate], int, list[str]] | None:
        """Concurrent, incremental, resumable discovery of album folders.

        Returns the album folders that need a tag read AND how many album folders
        this walk saw in total. The two differ on every warm scan: an unchanged
        album is seen but not re-listed, so the read list is empty while the
        library is perfectly healthy. Only the total says whether anything is
        there, which is what the empty-library guard in refresh() must judge.

        Loads the persisted directory tree once, then walks from ``root`` on a
        thread pool. For each folder a worker stats it (one round trip on a NAS):
        if the folder is known and its mtime is unchanged, the listing is REUSED
        from the tree (no scandir) and its known children are re-queued; otherwise
        the folder is scandir'd, its row and any newly discovered child rows are
        written, and an album candidate ``(dirpath, first_audio, mtime,
        count)`` is collected if it holds audio. Every visited folder is stamped ``gen`` so the
        caller can prune whatever was not seen. Directory rows are flushed in
        checkpoints, so a scan killed mid-walk resumes rather than restarting.

        All database access is on this thread; workers only touch the filesystem.
        Completions arrive via a queue (O(1) per folder), not a rescan of the
        pending set, so a huge tree does not peg a core. Returns the album
        candidates that need consideration, or None if superseded mid-walk (the
        partial tree is checkpointed first, so the next scan resumes).
        """
        known, children = self._load_dir_tree()
        album_mtimes = self._load_album_mtimes()

        def expected_mtime(path: str) -> float | None:
            """The mtime that lets ``path`` be skipped without a listing, or None
            to force a scandir (unknown, not yet listed, or an album still needing
            a tag read). ``force_full`` returns None for everything, so the manual
            Rescan re-lists the whole tree regardless of the mtime cache (the
            escape hatch for a network mount that does not update folder mtimes)."""
            if force_full:
                return None
            row = known.get(path)
            if row is None:
                return None  # never seen: must list
            mtime, listed, is_album = row
            if not listed:
                return None  # discovered but not yet listed (resume frontier)
            if is_album:
                # An album's read state is authoritative in the albums table, not
                # here: skip only if its row exists with this mtime and a known
                # codec (a NULL codec is a pre-quality row still owed one re-read).
                amt = album_mtimes.get(path)
                return amt[0] if (amt and amt[1]) else None
            return mtime  # container: unchanged mtime means unchanged listing

        seen_albums: set[str] = set()
        candidates: list[_Candidate] = []
        writes: list[tuple] = []
        checked = 0
        saw_vanished = False
        # Folders POSITIVELY gone: their parent was freshly listed and they were
        # not in it. Distinct from an ENOENT on a cached path, which is also
        # what a mount dropping mid-walk looks like and only earns a row its
        # one-generation grace (see _prune_by_gen); a fresh listing is direct
        # evidence, so these prune this very scan.
        condemned: list[str] = []

        pool = ThreadPoolExecutor(max_workers=self._workers_walk)
        done_q: SimpleQueue = SimpleQueue()
        outstanding = 0

        def probe(path: str, mtime):
            # Wrapped so the perf gauge counts this listing as in flight (see
            # WALK_GAUGE); the probe itself is unchanged.
            with WALK_GAUGE.working():
                return self._probe(path, mtime)

        pool_dead = False

        def submit(path: str) -> None:
            nonlocal outstanding, pool_dead
            if pool_dead:
                return
            try:
                fut = pool.submit(probe, path, expected_mtime(path))
            except RuntimeError:
                # Quitting Waves mid-scan tears the interpreter down under us,
                # and from that moment EVERY submit raises "cannot schedule new
                # futures after interpreter shutdown". alive() cannot see it
                # (the generation is untouched: nobody asked the scan to stop,
                # the process is simply going away), so without this the walk
                # died on an unhandled RuntimeError and the quit was reported
                # as a failed scan, ERROR level, in the crash trail. It is the
                # same situation as losing the alive() race: stop feeding the
                # frontier and let the loop checkpoint what was walked. On a
                # library big enough for the scan to still be running at quit
                # time, which is exactly the NAS case, this was the normal exit.
                pool_dead = True
                return
            fut.add_done_callback(done_q.put)
            outstanding += 1

        try:
            submit(root)
            while outstanding:
                if not alive() or pool_dead:
                    self._commit_dirs(writes)  # checkpoint the frontier, then bail
                    return None
                res = done_q.get().result()
                outstanding -= 1
                checked += 1
                path = res["path"]
                parent = os.path.dirname(path)
                gone = res.get("gone")
                error = res.get("error")
                if (gone or error) and path == root:
                    # The root itself vanished or went unreadable after the initial
                    # probe passed (a network mount dropping mid-walk). Leave the
                    # cache intact and mark it offline rather than let the prune wipe
                    # every badge: this is the temporarily-offline-NAS invariant.
                    self._commit_dirs(writes)
                    self.last_scan_status = SCAN_MISSING
                    return None
                if gone:
                    # A child genuinely vanished (ENOENT) between discovery and
                    # listing: leave it unstamped so the generation prune drops it
                    # (and its subtree). Remember that it happened: a mount that
                    # drops MID-walk answers ENOENT for every folder not yet
                    # reached, which is indistinguishable here from one deleted
                    # album, so the root is re-probed before anything is pruned.
                    saw_vanished = True
                    emit({"phase": "walk", "found": len(seen_albums), "checked": checked})
                    continue
                if error:
                    # A TRANSIENT stat/listing failure must never look like an empty
                    # or vanished folder: that would orphan a real subtree and the
                    # generation prune would delete it permanently. Preserve a known
                    # folder's cached state, re-stamping this gen so it survives the
                    # prune, and keep its OLD mtime so the still-pending change is
                    # re-listed once the error clears; re-queue its known children so
                    # they are re-stamped too. An unknown folder keeps the listed=0
                    # frontier row its parent already wrote (stamped this gen) and is
                    # retried on the next scan.
                    row = known.get(path)
                    if row is not None:
                        old_mtime, listed, is_album = row
                        writes.append((path, parent, old_mtime, listed, int(is_album), gen))
                        if is_album:
                            seen_albums.add(path)
                        for child in children.get(path, ()):
                            submit(child)
                    emit({"phase": "walk", "found": len(seen_albums), "checked": checked})
                    continue
                if res.get("unchanged"):
                    is_album = bool(known.get(path, (0, 0, 0))[2])
                    writes.append((path, parent, res["mtime"], 1, int(is_album), gen))
                    if is_album:
                        seen_albums.add(path)
                    for child in children.get(path, ()):  # known listing, re-queue it
                        submit(child)
                else:
                    candidate = res["candidate"]
                    is_album = candidate is not None
                    writes.append((path, parent, res["mtime"], 1, int(is_album), gen))
                    if is_album:
                        seen_albums.add(path)
                        candidates.append(candidate)
                    fresh = set(res["subdirs"])
                    for child in children.get(path, ()):
                        if child not in fresh:
                            condemned.append(child)
                    for sd in res["subdirs"]:
                        if sd not in known:
                            # Record the frontier before descending so a crash here
                            # still knows this child exists and must be listed.
                            writes.append((sd, path, 0.0, 0, 0, gen))
                        submit(sd)
                # "checked" is the number that visibly moves the whole time:
                # breadth-first reaches every artist folder before any album
                # folder, so "found" alone sits at zero during the heaviest stretch
                # and reads as a hang.
                emit({"phase": "walk", "found": len(seen_albums), "checked": checked})
                if len(writes) >= _WALK_COMMIT_EVERY:
                    self._commit_dirs(writes)
                    writes = []
            self._commit_dirs(writes)
            # The rate limiter may have swallowed the last burst; land the final
            # walk numbers so the UI never understates what was discovered.
            emit({"phase": "walk", "found": len(seen_albums), "checked": checked}, force=True)
            if saw_vanished:
                # Folders went missing during this walk. That is normal when the
                # user deleted an album, and it is also exactly what a network
                # share dropping mid-walk looks like from below: the root was
                # probed once before the walk and is never re-stat'd, so every
                # not-yet-reached folder reports ENOENT and the prune would delete
                # the whole library. Re-probe the root now, while the answer still
                # means something, and bail rather than prune if it is no longer
                # there.
                status = self._probe_root(root)
                if status != SCAN_OK:
                    self.last_scan_status = status
                    logger.info("library scan bailed: root became %s during the walk", status)
                    return None
        finally:
            # A superseded walk must not keep hammering the NAS in the background.
            pool.shutdown(wait=False, cancel_futures=True)
        return candidates, len(seen_albums), condemned

    def _probe(self, path: str, expected: float | None) -> dict:
        """(pool worker) Stat ``path`` and, unless its mtime matches ``expected``
        (so its listing can be reused), list it. Returns a small result dict.
        Three outcomes the caller must tell apart, because they are NOT the same:
        ``{exists: False}`` (genuinely gone, ENOENT: prune it), ``{error: True}``
        (a TRANSIENT stat/listing failure such as an SMB timeout or a stale handle:
        keep the cached subtree, never treat it as empty or vanished), and success
        (``{mtime, unchanged}`` plus, when listed, ``{subdirs, candidate}``). Pure
        filesystem: no database, no shared state."""
        try:
            st = os.stat(path)
        except FileNotFoundError:
            return {"path": path, "exists": True, "gone": True}  # truly absent: prune
        except OSError:
            return {"path": path, "exists": True, "error": True}  # transient: keep the cache
        if not stat_mod.S_ISDIR(st.st_mode):
            # The path exists but is no longer a DIRECTORY (a folder replaced by
            # a same-named file). Positive evidence, not a hiccup: without this,
            # the scandir below fails with NotADirectoryError, which the OSError
            # catch reads as transient, so the ghost row was re-stamped alive on
            # every scan forever. Report it gone so the prune retires it.
            return {"path": path, "exists": True, "gone": True}
        mtime = st.st_mtime
        if expected is not None and mtime == expected:
            return {"path": path, "exists": True, "mtime": mtime, "unchanged": True}
        listed = self._scandir_one(path, mtime)
        if listed is None:
            # The stat succeeded but the listing failed: a transient error, not an
            # empty folder. Do not surface a fresh mtime (the caller must keep the
            # old one so the change is re-listed once the error clears).
            return {"path": path, "exists": True, "error": True}
        subdirs, candidate = listed
        return {
            "path": path,
            "exists": True,
            "mtime": mtime,
            "unchanged": False,
            "subdirs": subdirs,
            "candidate": candidate,
        }

    def _scandir_one(self, d: str, mtime: float) -> tuple[list[str], _Candidate | None] | None:
        """One directory's listing: its walkable subfolders (skip rules applied,
        symlinks not followed) and, if it directly holds audio, the album candidate
        (dirpath, first audio file, the already-known dir mtime, audio count, and
        the full sorted audio listing for the per-file read).
        Returns None (distinct from an empty ``([], None)``) when the directory
        cannot be read, so a transient failure is never mistaken for a real empty
        folder and never orphans a cached subtree."""
        subdirs: list[str] = []
        audio: list[str] = []
        try:
            with os.scandir(d) as it:
                for entry in it:
                    name = entry.name
                    # Dot-prefixed entries are skipped whatever their type. For
                    # folders that is the usual hidden-directory rule; for FILES it
                    # is load-bearing: macOS writes AppleDouble "._Track.flac"
                    # sidecars beside every file on filesystems without native
                    # extended attributes (exFAT, most SMB shares). They carry an
                    # audio extension, they sort before every real track (so one
                    # was chosen as the folder's representative file), and mutagen
                    # cannot parse them, so such a library indexed as ZERO albums
                    # and re-listed every folder on every scan forever.
                    if name.startswith("."):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if name in _SKIP_DIR_NAMES:
                            continue
                        # A non-UTF-8 folder name cannot be stored in sqlite; skip it
                        # rather than let one bad name crash (and re-crash) the whole
                        # scan. Identity comes from tags, never the folder name, so
                        # nothing the index could use is lost.
                        try:
                            entry.path.encode("utf-8")
                        except UnicodeEncodeError:
                            continue
                        subdirs.append(entry.path)
                    elif self._is_audio(name):
                        audio.append(name)
        except OSError:
            return None
        if not audio:
            return subdirs, None
        audio.sort()
        return subdirs, (d, audio[0], mtime, len(audio), tuple(audio))

    def _load_dir_tree(self) -> tuple[dict[str, tuple[float, int, int]], dict[str, list[str]]]:
        """The persisted tree as two in-memory maps: ``{path: (mtime, listed,
        is_album)}`` for O(1) skip decisions, and ``{parent: [child, ...]}`` so an
        unchanged folder's children can be re-queued without a listing."""
        known: dict[str, tuple[float, int, int]] = {}
        children: dict[str, list[str]] = defaultdict(list)
        with self._lock:
            rows = self._conn.execute("SELECT path, parent, mtime, listed, is_album FROM dirs").fetchall()
        for path, parent, mtime, listed, is_album in rows:
            known[path] = (mtime, int(listed), int(is_album))
            if parent:
                children[parent].append(path)
        return known, children

    def _load_album_mtimes(self) -> dict[str, tuple[float, bool]]:
        """``{folder_path: (dir_mtime, complete)}`` for the walk's album-skip
        check. ``complete`` is False for a row predating quality capture (NULL
        codec), predating the release's declared shape (NULL declared), or short
        of one track row per counted file (predating per-track capture), which
        forces the one-time backfill re-read even when the folder is otherwise
        unchanged. A short row whose read is RECENT counts
        complete: a permanently unreadable file can never close the deficit,
        and without the rest window its folder was re-read in full on every
        scan forever (see _UNREADABLE_RETRY_S)."""
        out: dict[str, tuple[float, bool]] = {}
        now = time.time()
        with self._lock:
            rows = self._conn.execute("""SELECT folder_path, dir_mtime, codec, track_count, recorded_at,
                          (SELECT COUNT(*) FROM tracks WHERE tracks.folder_path = albums.folder_path),
                          declared, runtime
                   FROM albums""").fetchall()
        for path, mtime, codec, count, recorded, tracks, declared, runtime in rows:
            resting = now - float(recorded or 0) < _UNREADABLE_RETRY_S
            fresh = codec is not None and declared is not None and runtime is not None
            out[path] = (mtime, fresh and tracks > 0 and (tracks >= count or resting))
        return out

    def _commit_dirs(self, writes: list[tuple]) -> None:
        """Flush a batch of directory rows. A folder's own row (listed=1) and its
        newly discovered child rows (listed=0) are appended together, so a commit
        boundary never splits a folder from its frontier."""
        if not writes:
            return
        with self._lock:
            self._conn.executemany(
                """INSERT INTO dirs (path, parent, mtime, listed, is_album, seen_gen)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET
                       parent=excluded.parent, mtime=excluded.mtime,
                       listed=excluded.listed, is_album=excluded.is_album,
                       seen_gen=excluded.seen_gen""",
                writes,
            )
            self._conn.commit()

    def _read_and_upsert(
        self,
        to_read: list[_Candidate],
        alive: Callable[[], bool],
        emit: Callable[..., None],
    ) -> None:
        """Read the collected folders' tags on a thread pool and commit them in
        batches. Tag reads dominate a cold scan and are pure latency on a NAS
        (open + header read per file), so concurrent reads multiply throughput;
        mutagen parses independent files with no shared state, and each worker
        only touches its own file. Results are committed in submission order,
        every 200. Every completed read drives a (rate-limited) ``emit`` carrying
        done/total plus the artist/album just read, so the UI can show a live
        counter and the album under the needle; a database flush forces an event
        with ``committed`` True so the caller knows the partial index moved."""
        if not to_read:
            return
        total = len(to_read)
        done = 0
        batch: list[tuple] = []

        def progress(row: tuple | None, *, committed: bool = False) -> None:
            # Prefer the tags; a tagless folder still shows something readable.
            name = os.path.basename(row[0]) if row else ""
            emit(
                {
                    "phase": "read",
                    "done": done,
                    "total": total,
                    "indexed": self._count(),
                    "artist": (row[2] if row else "") or "",
                    "album": (row[1] if row else "") or name,
                    "committed": committed,
                },
                force=committed or done >= total,
            )

        pool = ThreadPoolExecutor(max_workers=self._workers_read)
        try:

            def read_one(job):
                with READ_GAUGE.working():  # perf gauge only; the read is unchanged
                    return self._read_row(job, alive)

            for row in pool.map(read_one, to_read):
                if not alive():
                    break
                done += 1
                if row is None:
                    continue
                batch.append(row)
                if len(batch) >= 200:
                    self._upsert(batch)
                    batch = []
                    progress(row, committed=True)
                else:
                    progress(row)
        finally:
            # A superseded scan must not keep hammering the NAS in the background.
            pool.shutdown(wait=False, cancel_futures=True)
        if alive():
            self._upsert(batch)
            if batch:
                progress(batch[-1], committed=True)

    @staticmethod
    def _track_row(dirpath: str, tags: dict) -> tuple:
        """One file's row for the tracks table: its own title and track artist
        (falling back to the album artist when the per-track credit is blank)
        plus its stream quality. An untagged file still gets a row, empty title
        and all: the row's EXISTENCE is what tells the backfill gate this folder
        has had its per-file read, and an empty title honestly matches nothing."""
        return (
            dirpath,
            str(tags.get("title", "") or ""),
            str(tags.get("track_artist", "") or tags.get("artist", "") or ""),
            str(tags.get("codec", "") or ""),
            int(tags.get("bitrate", 0) or 0),
            int(tags.get("bits", 0) or 0),
            int(tags.get("rate", 0) or 0),
            int(tags.get("length", 0) or 0),
        )

    def _read_row(self, job: _Candidate, alive: Callable[[], bool]) -> tuple | None:
        """(pool worker) One album folder's row for the upsert batch, or None
        when the representative file could not be read at all (an exception or
        an unparseable file). No row is written for a failed read ON PURPOSE:
        a transient open/read hiccup (routine on a cold NAS under scan load)
        would otherwise be persisted as an empty-identity album that no later
        scan ever re-reads, permanently hiding its badge. With no row, the
        folder simply retries on the next scan. A readable file with no tags is
        different: it still yields its codec (from the stream), its row
        persists, and honestly never matches anything.

        Every other audio file in the folder is read too, for the per-track
        rows (the last tuple element), and those same reads double as the
        folder's album-agreement check, MAJORITY-RULED: one stray file whose
        album tag positively disagrees (a leftover single, one mis-tagged rip)
        no longer hides the whole album; the count becomes the files that
        positively voted for the majority album, so coverage stays honest
        (unreadable and untagged files lose their benefit of the doubt too,
        because the folder is proven mixed and they could be anything). The majority must be overwhelming (a single
        dissenter, or nine agreeing files in ten) AND the representative must
        be on the majority's side, or the count is zeroed as before: an
        "Inbox" dump whose first file says "Discovery" must not index as a
        full Discovery. Any disagreement at all silences the folder's declared
        shape and its runtime, because a sum over mixed contents is exactly
        the undercount/overcount those witnesses must never carry. Unreadable
        or untagged files never count as disagreement, and never cost a track
        row beyond their own: only positive evidence rejects."""
        dirpath, first_audio, mtime, count, audio = job
        if not alive():
            return None
        # The walk's raw file count, kept apart from the verdict below: it is
        # the number _unchanged compares against the next walk, so a folder
        # that grows without an mtime bump still reads as changed.
        raw = count
        try:
            tags = self._read_tags(os.path.join(dirpath, first_audio))
        except Exception:
            tags = None
        if not tags:
            return None
        tracks = [self._track_row(dirpath, tags)]
        want = str(tags.get("album", "") or "").strip().casefold()
        # What the folder's files SAY the release is, believed only when they
        # speak with one voice. Silence is not disagreement (the same principle
        # as the album check below: only positive evidence rejects), so a stray
        # untagged file costs nothing, but two different answers mean the folder
        # cannot be asked and the claim is dropped. Disc numbers included: files
        # declaring different discs are not a folder in conflict, they are a set
        # sitting flat in one folder, and its own file count already covers it.
        shape = {k: {int(tags.get(k, 0) or 0)} for k in ("track_total", "disc_no", "disc_total")}
        agree, dissent = (1 if want else 0), 0
        for name in audio:
            if name == first_audio:
                continue
            if not alive():
                # Bail with NO row at all: a partial track list must never
                # persist as this folder's finished read (the backfill gate
                # would believe it forever). The folder retries next scan.
                return None
            other = None
            with contextlib.suppress(Exception):
                other = self._read_tags(os.path.join(dirpath, name))
            if not other:
                continue
            got = str(other.get("album", "") or "").strip().casefold()
            if want and got:
                if got == want:
                    agree += 1
                else:
                    dissent += 1
            for key, seen in shape.items():
                seen.add(int(other.get(key, 0) or 0))
            tracks.append(self._track_row(dirpath, other))
        if dissent:
            # Only the files that positively voted for the majority album are
            # counted. Subtracting the dissenters from the raw file count
            # instead kept every unreadable and untagged file in the total,
            # and a folder proven mixed has forfeited that benefit of the
            # doubt: 10 agreeing files + 1 stray + 1 unreadable landed on
            # exactly the release's 11 tracks and the bulk gate skipped an
            # album the user holds 10 of.
            overwhelming = dissent == 1 or dissent <= (agree + dissent) * 0.1
            count = agree if overwhelming and agree > dissent else 0

        def agreed(key: str) -> int:
            if dissent:
                # A folder speaking with two voices declares nothing: its
                # tracktotal or disc numbers may belong to the strays.
                return 0
            seen = shape[key] - {0}
            return seen.pop() if len(seen) == 1 else 0

        return (
            dirpath,
            tags.get("album", ""),
            tags.get("artist", ""),
            tags.get("date", ""),
            count,
            mtime,
            int(time.time()),
            str(tags.get("codec", "") or ""),
            int(tags.get("bitrate", 0) or 0),
            int(tags.get("bits", 0) or 0),
            int(tags.get("rate", 0) or 0),
            agreed("track_total"),
            agreed("disc_no"),
            agreed("disc_total"),
            # The folder's summed play length, believed only when EVERY read
            # file reported one (only positive evidence, like agreed() above)
            # and no file disputed the album: a sum missing a file's minutes,
            # or carrying a stray's, would refute true matches. 0 says "the
            # files never said", NULL stays the pre-capture sentinel.
            (
                sum(t[7] for t in tracks)
                if not dissent and len(tracks) == len(audio) and all(t[7] > 0 for t in tracks)
                else 0
            ),
            raw,
            tuple(tracks),
        )

    def iter_albums(self) -> Iterator[dict]:
        """Yield each indexed album as a raw dict the bridge can normalise:
        ``{title, artist, year, tracks, id}`` (id is the folder path, so the
        badge can reveal it in the file manager) plus the representative file's
        quality facts ``{codec, bitrate, bits, rate}`` for the badge's quality
        readout and upgrade hint.

        Also the release's own declared shape, ``{declared, disc_no,
        disc_total}``, all 0 when its files never said or did not agree.
        ``tracks`` counts the files the folder HOLDS; ``declared`` is how many
        the release says it has, which is the only way to tell a complete copy
        of a smaller edition from a copy short a track."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT album, artist, year, track_count, folder_path, codec, bitrate, bits, rate,"
                " declared, disc_no, disc_total, runtime FROM albums"
            ).fetchall()
        for (
            album,
            artist,
            year,
            tracks,
            path,
            codec,
            bitrate,
            bits,
            rate,
            declared,
            disc_no,
            disc_total,
            runtime,
        ) in rows:
            yield {
                "title": str(album or ""),
                "artist": str(artist or ""),
                "year": str(year or ""),
                "tracks": int(tracks or 0),
                "id": str(path or ""),
                "codec": str(codec or ""),
                "bitrate": int(bitrate or 0),
                "bits": int(bits or 0),
                "rate": int(rate or 0),
                "declared": int(declared or 0),
                "disc_no": int(disc_no or 0),
                "disc_total": int(disc_total or 0),
                # Summed play length in seconds, 0 when the files never said.
                "runtime": int(runtime or 0),
            }

    def iter_tracks(self) -> Iterator[dict]:
        """Yield each indexed track as a raw dict for the track-level presence
        index: ``{title, artist, id}`` (id is the album FOLDER path, the same
        reveal target the album pill uses) plus the file's own quality facts
        ``{codec, bitrate, bits, rate}`` so the pill's colour reports the copy
        the user actually holds.

        Also carries the HOLDING FOLDER's identity, ``{album, album_year}``,
        joined from the albums row that owns the folder. A track has no year of
        its own in this table and its title alone proves nothing (a title plus
        artist match any edition, any compilation, any re-recording sharing the
        name), so the folder's identity is the only evidence a track match can
        offer. decide_track_presence weighs it exactly as the album matcher
        weighs its own, which is what lets a track inside a proven album drop
        the badge's "?"."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT t.title, t.artist, t.folder_path, t.codec, t.bitrate, t.bits, t.rate, t.length, "
                "a.album, a.year "
                "FROM tracks t LEFT JOIN albums a ON a.folder_path = t.folder_path"
            ).fetchall()
        for title, artist, path, codec, bitrate, bits, rate, length, album, album_year in rows:
            yield {
                "title": str(title or ""),
                "artist": str(artist or ""),
                "id": str(path or ""),
                "codec": str(codec or ""),
                "bitrate": int(bitrate or 0),
                "bits": int(bits or 0),
                "rate": int(rate or 0),
                "album": str(album or ""),
                "album_year": str(album_year or ""),
                # The file's own play length in seconds, 0 when it never said.
                "length": int(length or 0),
            }

    def poll_containers_changed(self, root: str) -> bool | None:
        """Cheap change check for the watcher's background poll: stat ONLY the
        container folders (the root, artist folders, and any folder that can gain
        or lose a child folder) and report whether any differs from its stored
        mtime, so a full incremental refresh() runs only when something structural
        actually changed. This is the network-safe half of the watcher: filesystem
        change events do not cross an SMB/NFS mount, but a moved-in album still
        bumps its parent's mtime, and there are only ~1-2k containers to stat, not
        the whole ~18k-folder tree.

        Returns True if a rescan is warranted, False if nothing changed, or None
        if the root is not currently readable (an offline NAS), so the caller does
        not fan ~1-2k blocking stats into the void, or if nothing has been scanned
        yet (a cold refresh will handle that).

        READ-ONLY on the cache by design: it must never write the fresh mtime
        back. refresh() is the sole writer of ``dirs.mtime``; if the poll stored
        the new mtime, the refresh it triggers would see stored == current and
        skip re-listing, silently dropping the very change just detected.
        """
        root = os.path.expanduser(str(root or "")).rstrip(os.sep)
        if self._probe_root(root) != SCAN_OK:
            return None
        with self._lock:
            # Every listed folder that can hold child folders: is_album=0 covers
            # the root and pure containers; the parent-subquery also catches a
            # HYBRID folder (loose audio marks it is_album=1, yet it still has
            # album subdirs that could come or go). Childless album leaves are
            # excluded, keeping this to ~1-2k rows served by idx_dirs_parent.
            rows = self._conn.execute("""SELECT path, mtime FROM dirs
                   WHERE listed = 1 AND (is_album = 0
                         OR path IN (SELECT parent FROM dirs WHERE parent IS NOT NULL))""").fetchall()
        if not rows:
            return None  # nothing indexed yet; leave it to a normal refresh

        def changed(row: tuple) -> bool:
            with POLL_GAUGE.working():  # perf gauge only; the stat is unchanged
                return _changed(row)

        def _changed(row: tuple) -> bool:
            path, stored = row
            try:
                # Compare with !=, not >: a container is "changed" if its mtime
                # moved at all, including an NFS server clock stepping backwards.
                return os.stat(path).st_mtime != stored
            except OSError:
                return True  # a container that vanished is a change worth a rescan

        # The last refresh's verdict sizes this too: the poll runs between scans
        # against the same root, and a network mount deserves the same restraint
        # from a thousand stats as from the walk. The gauge follows for the same
        # reason its siblings do: unlimited, a throttled poll reports 2/8 and
        # reads as underdriven when it is running exactly as intended.
        POLL_GAUGE.limit(self._workers_walk)
        with ThreadPoolExecutor(max_workers=self._workers_walk) as pool:
            return any(pool.map(changed, rows))

    def container_paths(self) -> list[str]:
        """The container folders a local-disk watcher should watch: the root,
        artist folders, and hybrid folders that can gain or lose a child folder,
        excluding childless album leaves. Same set poll_containers_changed stats,
        so the watcher and the poll agree on what "structural" means."""
        with self._lock:
            rows = self._conn.execute("""SELECT path FROM dirs
                   WHERE listed = 1 AND (is_album = 0
                         OR path IN (SELECT parent FROM dirs WHERE parent IS NOT NULL))""").fetchall()
        return [r[0] for r in rows]

    def _unchanged(self, folder_path: str, mtime: float, count: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                """SELECT dir_mtime, track_count, codec, recorded_at,
                          (SELECT COUNT(*) FROM tracks WHERE tracks.folder_path = albums.folder_path),
                          declared, runtime, raw_count
                   FROM albums WHERE folder_path = ?""",
                (folder_path,),
            ).fetchone()
        return self._unchanged_verdict(row, mtime, count)

    def _unchanged_rows(self) -> dict[str, tuple]:
        """Every album's _unchanged row in ONE query, for the scan's verdict
        pass. Asking _unchanged per candidate cost one round-trip with a
        correlated subquery each, 18k of them on a warm scan of a big library;
        the same rows come out of a single LEFT JOIN against a grouped track
        count, and the verdict itself is plain Python (_unchanged_verdict)."""
        with self._lock:
            rows = self._conn.execute("""SELECT albums.folder_path, dir_mtime, track_count, codec, recorded_at,
                          COALESCE(tc.n, 0), declared, runtime, raw_count
                   FROM albums
                   LEFT JOIN (SELECT folder_path, COUNT(*) AS n FROM tracks
                              GROUP BY folder_path) tc
                     ON tc.folder_path = albums.folder_path""").fetchall()
        return {r[0]: r[1:] for r in rows}

    @staticmethod
    def _unchanged_verdict(row: tuple | None, mtime: float, count: int) -> bool:
        # A NULL codec is a row from before quality capture existed, a NULL
        # declared one from before the release's own shape was read, and a row
        # short of one track row per counted file is from before per-track
        # capture (or lost files to transient read failures): re-read any of
        # them once so it backfills, even though nothing changed. The short-row
        # retry rests between attempts (see _UNREADABLE_RETRY_S): a permanently
        # unreadable file never closes the deficit, and retrying every scan
        # re-read the whole folder's tags forever.
        return (
            bool(row)
            and row[0] == mtime
            # Compared against raw_count, the file count the folder was READ
            # at, never against track_count: that one can be a mixed-folder
            # majority VERDICT smaller than the raw count (see _read_row), and
            # an inequality against it re-read every "Inbox" dump on every
            # Rescan, while accepting any smaller stored value made a folder
            # that GREW without an mtime bump (an unreliable network mount)
            # read as unchanged forever, the very blindness force_full exists
            # to cure. Exact match or the folder is re-read.
            and row[7] is not None
            and row[7] == count
            and row[2] is not None
            and row[5] is not None
            # NULL runtime is a row from before duration capture; one re-read
            # backfills it (0, "the files never said", is a finished answer).
            and row[6] is not None
            and row[4] > 0
            and (row[4] >= row[1] or time.time() - float(row[3] or 0) < _UNREADABLE_RETRY_S)
        )

    def _upsert(self, batch: list[tuple]) -> None:
        if not batch:
            return
        with self._lock:
            self._conn.executemany(
                """INSERT INTO albums
                       (folder_path, album, artist, year, track_count, dir_mtime, recorded_at,
                        codec, bitrate, bits, rate, declared, disc_no, disc_total, runtime, raw_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(folder_path) DO UPDATE SET
                       album=excluded.album, artist=excluded.artist, year=excluded.year,
                       track_count=excluded.track_count, dir_mtime=excluded.dir_mtime,
                       recorded_at=excluded.recorded_at, codec=excluded.codec,
                       bitrate=excluded.bitrate, bits=excluded.bits, rate=excluded.rate,
                       declared=excluded.declared, disc_no=excluded.disc_no,
                       disc_total=excluded.disc_total, runtime=excluded.runtime,
                       raw_count=excluded.raw_count""",
                [row[:16] for row in batch],
            )
            # Replace, not merge: the read is of the whole folder, so its track
            # rows are the whole truth for that folder and stale rows (a renamed
            # or re-tagged file) must not linger beside the fresh ones.
            self._conn.executemany("DELETE FROM tracks WHERE folder_path = ?", [(row[0],) for row in batch])
            self._conn.executemany(
                "INSERT INTO tracks (folder_path, title, artist, codec, bitrate, bits, rate, length)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [t for row in batch for t in row[16]],
            )
            self._conn.commit()

    def _prune_by_gen(self, gen: int, condemned: list[str] | None = None) -> None:
        """After a completed walk, drop the ``condemned`` subtrees (folders a
        freshly-listed parent no longer contains: positive evidence, pruned this
        very scan) and folders unseen for TWO consecutive generations, then any
        album row no longer backed by an album folder in the tree. Only runs on
        a clean finish: a bailed walk leaves the tree partial on purpose so the
        next scan resumes it.

        Two generations of absence, not one, for the same reason _confirm_empty
        exists: a share that unmounts MID-walk can leave its mountpoint behind
        as a readable empty directory, so the root re-probe passes, the stamped
        early part of the walk defeats the empty-walk guard, and a one-miss
        prune deleted every subtree the walk had not reached. An ENOENT on a
        cached path is exactly what that drop looks like, so it only earns a
        row its one-generation grace; an ordinary deletion re-lists the parent
        and lands in ``condemned``, so its badge still clears on the next scan,
        and every folder a walk actually sees is re-stamped (unchanged,
        changed, and transient-error rows alike), so the grace costs nothing
        live."""
        with self._lock:
            if condemned:
                doomed = [
                    p
                    for (p,) in self._conn.execute("SELECT path FROM dirs").fetchall()
                    for c in condemned
                    if p == c or p.startswith(c + os.sep)
                ]
                self._conn.executemany("DELETE FROM dirs WHERE path = ?", [(p,) for p in doomed])
            self._conn.execute("DELETE FROM dirs WHERE seen_gen < ?", (gen - 1,))
            self._conn.execute("DELETE FROM albums WHERE folder_path NOT IN (SELECT path FROM dirs WHERE is_album = 1)")
            self._conn.execute("DELETE FROM tracks WHERE folder_path NOT IN (SELECT folder_path FROM albums)")
            self._conn.commit()

    def _count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0])

    @property
    def is_closed(self) -> bool:
        """True once close() ran. A scan worker wedged in a network stat can
        outlive the bounded shutdown wait and hit the closed connection when
        the stat finally returns; this is how its caller tells that orderly
        end from a real failure worth an ERROR in the crash trail."""
        return self._closed

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._conn.close()
