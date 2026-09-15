"""Local record of what has actually been downloaded, so Waves can answer "do
you already have this track, and at what quality" from reality rather than from
a history log.

The rule this store lives by: it DESCRIBES what was downloaded (the actual final
on-disk path and the delivered quality, keyed by the exact TIDAL track id); it
never DECIDES ownership on its own. Ownership is answered live, by re-checking
whether a recorded path still exists on disk right now, so a file the user
deleted and wants again is offered for re-download with no "clear history" step.
A history table that just says "downloaded before" would lie the moment a file
is deleted; re-checking the filesystem every time is what keeps it honest.

It also never looks anywhere but the folders the app hands it (set_roots): the
download folder and the library folder. A copy recorded somewhere else, such as
an earlier download folder, is not a copy the user has, so it is not checked.

Pure standard library (sqlite3), with no Qt and no tidalapi import, so it unit
tests without the GUI stack and never couples the download engine to the UI.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
import time
from threading import Lock, local

# Delivered-quality tiers, lowest to highest, keyed by the TIDAL tier string
# (tidalapi Quality values: LOW < HIGH < LOSSLESS < HI_RES_LOSSLESS). A caller
# can ask "is a better tier available than what is on disk" with a plain integer
# comparison, and the DB can ORDER BY the stored rank. Bit depth and sample rate
# are deliberately NOT used for ranking: TIDAL omits them for some tiers (they
# default to 16 / 44100), so the tier string is the only trustworthy signal.
logger = logging.getLogger("waves.ownership")

QUALITY_RANK = {"LOW": 0, "HIGH": 1, "LOSSLESS": 2, "HI_RES_LOSSLESS": 3}


def _nonempty_file(path: str) -> bool:
    """A recorded path counts as surviving only if it holds actual bytes."""
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def path_under(path: str, root: str) -> bool:
    """Does ``path`` lie at or under the folder ``root``? A string compare of
    the two spellings, never a stat (a stat on a dead network mount can hang
    for seconds). Both sides go through abspath, which is how a download's
    path is recorded, so a relative download folder still matches its files.

    Case is folded where the platform's volumes fold it by default: normcase
    does it on Windows, and macOS volumes (APFS and HFS+) are case-insensitive
    unless the user opted out, so /Music and /music name the same folder
    there. The opt-in exception is rare enough that no stat is spent on it."""
    p = (path or "").strip()
    r = (root or "").strip()
    if not p or not r:
        return False
    try:
        p = os.path.normcase(os.path.abspath(os.path.expanduser(p)))
        r = os.path.normcase(os.path.abspath(os.path.expanduser(r)))
    except Exception:
        return False
    if sys.platform == "darwin":
        p, r = p.lower(), r.lower()
    # A drive or volume root already ends in its separator ("N:\\", "/").
    prefix = r if r.endswith(os.sep) else r + os.sep
    return p == r or p.startswith(prefix)


# Columns beyond the primary key, with the type used to ADD them to an older DB.
# CREATE TABLE below carries the full schema; this list only drives the
# forward-compatible ALTER guard, so every entry must be nullable or defaulted
# (ALTER TABLE ADD COLUMN cannot add a bare NOT NULL column or a primary key).
_ADDED_COLUMNS = (
    ("quality_tier", "TEXT"),
    ("quality_rank", "INTEGER NOT NULL DEFAULT -1"),
    ("audio_mode", "TEXT"),
    ("bit_depth", "INTEGER"),
    ("sample_rate", "INTEGER"),
    ("codecs", "TEXT"),
    ("user_id", "TEXT"),
    ("recorded_at", "INTEGER NOT NULL DEFAULT 0"),
    # The quality rank this download RUN asked for, and the best rank TIDAL
    # advertised for the track at that moment. Together they let the upgrade
    # gate converge: "we already asked at this quality or better, and this is
    # what was served" is a skip, not an endless re-download (a track whose
    # best available master sits below the user's target would otherwise be
    # re-fetched on every run, forever).
    ("requested_rank", "INTEGER NOT NULL DEFAULT -1"),
    ("ceiling_rank", "INTEGER NOT NULL DEFAULT -1"),
    # How many times in a row this copy has come back BELOW the ceiling TIDAL
    # advertised for it. The ranks above cannot converge that case on their
    # own, and must not: a copy served under its own advertised ceiling is the
    # one case where a better master is provably there for the asking (issue
    # #2), so the upgrade deliberately stays open. But TIDAL can advertise
    # LOSSLESS and serve HIGH persistently, and then "stays open" means the
    # track is re-fetched and overwritten on every album click, forever, with
    # nothing on screen to say why. Counted, so the retry can be given up
    # after a couple of honest attempts. Reset to 0 by any delivery that lands
    # at or above the ceiling, so a master TIDAL really does fix is taken.
    ("degraded_tries", "INTEGER NOT NULL DEFAULT 0"),
)


def quality_rank(tier: str | None) -> int:
    """Rank of a delivered-quality tier string. Unknown or missing ranks below
    every real tier (-1), so it never wins a "best surviving copy" comparison."""
    return QUALITY_RANK.get((tier or "").upper(), -1)


def _best_surviving(rows, roots: list[str] | None = None) -> dict | None:
    """The first row (highest delivered quality first, then most recent) whose
    path still exists on disk, as the ownership record, or None.

    The existence check stats the disk, so it runs after the query, never
    inside one (a read must never hold up a worker-thread write). A zero-byte
    survivor is a truncation artifact, not a copy: skip it (not removed, like
    a deleted path) so the track reads as wanted again.

    With ``roots`` given, a row whose path is under none of them is skipped
    before any stat: it is never looked at, whatever is still on that disk.
    None means unscoped (a bare store nobody configured)."""
    for path, tier, rank, mode, depth, rate, codecs, recorded_at, requested, ceiling, degraded in rows:
        if not path:
            continue
        if roots is not None and not any(path_under(path, r) for r in roots):
            continue
        if _nonempty_file(path):
            return {
                "owned": True,
                "path": path,
                "quality_tier": tier,
                "quality_rank": rank,
                "audio_mode": mode,
                "bit_depth": depth,
                "sample_rate": rate,
                "codecs": codecs,
                "recorded_at": recorded_at,
                "requested_rank": requested,
                "ceiling_rank": ceiling,
                "degraded_tries": degraded,
            }
    return None


class OwnershipStore:
    """A small sqlite record of downloaded tracks: (track_id, final path) plus the
    delivered quality. One row per distinct on-disk path, so a re-download to a
    new location (a template change, or a higher-quality copy alongside the old
    one) adds a row rather than overwriting history. Ownership is always resolved
    against the live filesystem, never asserted from a row alone.

    Thread-safe: records are written from download worker threads while reads run
    on the GUI thread. The connection is opened with check_same_thread=False and
    every statement runs under an instance lock; WAL mode keeps a read from
    blocking behind a write.
    """

    def __init__(self, db_path: str) -> None:
        self._path = str(db_path)
        parent = os.path.dirname(self._path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        # Readers never queue behind a writer, or behind each other: each
        # thread that reads gets its own connection (WAL lets any number of
        # them read while one writes). Before this every read went through
        # the one connection under ``_lock``, so a card asking members_of on
        # the GUI thread waited for whichever refresh worker held the lock
        # for its own query; sampled live at launch, that wait was most of
        # the time the cards' creation spent blocked (the launch water
        # dropping frames on it). An in-memory database cannot be shared
        # across connections, so that one case keeps the shared connection.
        self._readers = local()
        self._closed = False
        # The folders ownership answers are confined to (see set_roots).
        self._roots = None
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("""CREATE TABLE IF NOT EXISTS downloads (
                       track_id     TEXT    NOT NULL,
                       path         TEXT    NOT NULL,
                       quality_tier TEXT,
                       quality_rank INTEGER NOT NULL DEFAULT -1,
                       audio_mode   TEXT,
                       bit_depth    INTEGER,
                       sample_rate  INTEGER,
                       codecs       TEXT,
                       user_id      TEXT,
                       recorded_at  INTEGER NOT NULL DEFAULT 0,
                       requested_rank INTEGER NOT NULL DEFAULT -1,
                       ceiling_rank   INTEGER NOT NULL DEFAULT -1,
                       degraded_tries INTEGER NOT NULL DEFAULT 0,
                       PRIMARY KEY (track_id, path)
                   )""")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_downloads_track ON downloads(track_id)")
            self._conn.execute("""CREATE TABLE IF NOT EXISTS collection_members (
                       collection_id TEXT    NOT NULL,
                       track_id      TEXT    NOT NULL,
                       recorded_at   INTEGER NOT NULL DEFAULT 0,
                       PRIMARY KEY (collection_id, track_id)
                   )""")
            self._ensure_columns()
            self._conn.commit()

    def _ensure_columns(self) -> None:
        """Add any expected column missing from an older DB. A no-op once the DB
        matches the current schema; lets a future column land without a manual
        migration. Caller holds the lock.

        The lock is this process's own, and the config folder is shared: a
        double launch on the first run after an upgrade (or the packaged app
        beside a source run) can have both copies read the column list before
        either adds anything, and SQLite answers the loser's ALTER with
        "duplicate column name". That was an unhandled exception in the store's
        constructor, which is built unguarded while the bridge is being
        constructed, so the second copy died at startup instead of opening.
        Whoever got there first is a perfectly good answer, so the column being
        there already is not an error.
        """
        have = {row[1] for row in self._conn.execute("PRAGMA table_info(downloads)")}
        for name, decl in _ADDED_COLUMNS:
            if name in have:
                continue
            try:
                self._conn.execute(f"ALTER TABLE downloads ADD COLUMN {name} {decl}")
            except sqlite3.OperationalError:
                # Ask the FILE, do not read the message. The race this handles
                # can also surface as "database is locked" once sqlite's busy
                # timeout is exceeded, and that text carries no column name at
                # all, so matching on "duplicate column" re-raised precisely
                # the loser this exists to let through, under load, at startup.
                # The only question that matters is whether the column is there
                # now.
                try:
                    present = {row[1] for row in self._conn.execute("PRAGMA table_info(downloads)")}
                except sqlite3.Error:
                    present = set()
                if name not in present:
                    raise
                logger.debug("ownership: %s was added by another copy of Waves", name)

    def _read(self, sql: str, params: tuple = ()) -> list:
        """Run a read on this thread's own connection (see __init__). Falls
        back to the shared connection, under the lock, for an in-memory
        store or once the store is closed (so a late reader gets sqlite's
        own "closed" error rather than a fresh connection to nothing)."""
        if self._path == ":memory:" or self._closed:
            with self._lock:
                return self._conn.execute(sql, params).fetchall()
        conn = getattr(self._readers, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._path)
            self._readers.conn = conn
        return conn.execute(sql, params).fetchall()

    def record(
        self,
        track_id: str,
        path: str,
        quality_tier: str | None = None,
        *,
        audio_mode: str | None = None,
        bit_depth: int | None = None,
        sample_rate: int | None = None,
        codecs: str | None = None,
        user_id: str | None = None,
        requested_rank: int = -1,
        ceiling_rank: int = -1,
        degraded: bool = False,
    ) -> int:
        """Record that ``track_id`` was written to ``path`` at ``quality_tier``.

        Upserts on (track_id, path): re-recording the same file updates its
        quality and timestamp in place; a different path for the same track adds
        a row, so every known copy survives for the live ownership check.
        ``requested_rank`` is the quality rank the run asked for and
        ``ceiling_rank`` the best rank TIDAL advertised at the time (both -1
        when unknown); see backend's _copy_is_current for how they stop a
        forever-upgrade loop. ``degraded`` says this delivery came back BELOW
        that advertised ceiling: it bumps a consecutive counter (and any
        delivery that is not degraded resets it to zero), which is what lets
        the same gate give up on a track TIDAL persistently under-serves
        instead of re-fetching it on every click for good.

        Returns:
            int: This row's consecutive degraded-delivery count after the
                write, so the caller can report which attempt this was.
        """
        tier = (quality_tier or "").upper() or None
        row = (
            str(track_id),
            str(path),
            tier,
            quality_rank(tier),
            audio_mode,
            bit_depth,
            sample_rate,
            codecs,
            user_id,
            int(time.time()),
            int(requested_rank),
            int(ceiling_rank),
            1 if degraded else 0,
        )
        with self._lock:
            self._conn.execute(
                """INSERT INTO downloads
                       (track_id, path, quality_tier, quality_rank, audio_mode,
                        bit_depth, sample_rate, codecs, user_id, recorded_at,
                        requested_rank, ceiling_rank, degraded_tries)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(track_id, path) DO UPDATE SET
                       quality_tier = excluded.quality_tier,
                       quality_rank = excluded.quality_rank,
                       audio_mode   = excluded.audio_mode,
                       bit_depth    = excluded.bit_depth,
                       sample_rate  = excluded.sample_rate,
                       codecs       = excluded.codecs,
                       user_id      = excluded.user_id,
                       recorded_at  = excluded.recorded_at,
                       requested_rank = excluded.requested_rank,
                       ceiling_rank   = excluded.ceiling_rank,
                       degraded_tries = CASE
                           WHEN excluded.degraded_tries > 0 THEN downloads.degraded_tries + 1
                           ELSE 0
                       END""",
                row,
            )
            self._conn.commit()
            # Read back under the same lock, so the caller can say how many
            # attempts this makes without a second query racing another
            # worker's write (and without ownership_of's disk check, which
            # would stat a network mount for a file just written here).
            got = self._conn.execute(
                "SELECT degraded_tries FROM downloads WHERE track_id = ? AND path = ?",
                (str(track_id), str(path)),
            ).fetchone()
        return int(got[0]) if got else 0

    def stamp_ceiling(self, track_id: str, path: str, ceiling_rank: int) -> bool:
        """Write the best rank TIDAL advertises for ``track_id`` onto the row
        for ``path``, only where that row has none yet (a copy recorded before
        ceilings were kept, or by a fetch whose tags were unknown).

        A ceiling learned later is as true as one learned at the fetch, and
        without it such a row settles only for a caller holding the track
        (the download gate) while every caller that holds an id alone (the
        button, the album card) keeps offering an upgrade that cannot come.
        Stamping it once lets the stored ranks answer for good. Never
        overwrites a ceiling already there: what the fetch saw stands.

        Returns:
            bool: Whether a row was changed.
        """
        rank = int(ceiling_rank)
        if rank < 0:
            return False
        with self._lock:
            cur = self._conn.execute(
                """UPDATE downloads SET ceiling_rank = ?
                   WHERE track_id = ? AND path = ? AND (ceiling_rank IS NULL OR ceiling_rank < 0)""",
                (rank, str(track_id), str(path)),
            )
            self._conn.commit()
            return int(cur.rowcount or 0) > 0

    def record_members_replace(self, collection_id: str, track_ids: list[str]) -> None:
        """Remember the exact, current track ids that make up ``collection_id``
        (an album, playlist or mix), replacing any previous record for it.

        Called wherever Waves already has the full track list in hand for a
        reason other than this (opening the item's page, expanding an album
        panel), so a later "is this fully owned" question elsewhere in the app
        (e.g. a collapsed row that has never been opened) can be answered from
        this local table alone, no re-fetch. A playlist's contents can change,
        so this is a full replace, not an add.
        """
        cid = str(collection_id)
        ids = [str(t) for t in track_ids if t]
        now = int(time.time())
        with self._lock:
            self._conn.execute("DELETE FROM collection_members WHERE collection_id = ?", (cid,))
            self._conn.executemany(
                "INSERT OR IGNORE INTO collection_members (collection_id, track_id, recorded_at) VALUES (?, ?, ?)",
                [(cid, tid, now) for tid in ids],
            )
            self._conn.commit()

    def record_members_add(self, collection_id: str, track_ids: list[str]) -> None:
        """Additively remember that ``track_ids`` belong to ``collection_id``,
        without touching any other membership already recorded for it.

        Called incrementally as a collection download progresses (see
        ``record_members_replace`` for the alternative, authoritative case):
        Waves observes each track as it is queued, so membership for a
        downloaded album/playlist is learned for free, from data already
        flowing through the download, no extra network call.
        """
        cid = str(collection_id)
        ids = [str(t) for t in track_ids if t]
        if not ids:
            return
        now = int(time.time())
        with self._lock:
            self._conn.executemany(
                "INSERT OR IGNORE INTO collection_members (collection_id, track_id, recorded_at) VALUES (?, ?, ?)",
                [(cid, tid, now) for tid in ids],
            )
            self._conn.commit()

    def members_of(self, collection_id: str) -> list[str] | None:
        """Known member track ids for ``collection_id``, or None if Waves has
        never observed this collection's contents (never opened, never
        downloaded): distinct from an empty list, so a caller can tell
        "unknown" apart from a genuinely empty collection.

        A plain indexed lookup against Waves' own local database, not the
        user's music folder: unlike ownership_of, there is no live filesystem
        stat here, so this never risks hanging on a dropped network mount and
        is safe to call directly.
        """
        rows = self._read(
            "SELECT track_id FROM collection_members WHERE collection_id = ?",
            (str(collection_id),),
        )
        if not rows:
            return None
        return [r[0] for r in rows]

    def set_roots(self, provider) -> None:
        """Confine every ownership answer to the folders ``provider()`` names
        (the app passes the download folder and the library folder). Asked on
        every lookup rather than stored, so a folder changed in Settings takes
        effect on the next question with no restart. A recorded copy outside
        all of them is not owned, and its path is never statted."""
        self._roots = provider

    def _scope(self) -> list[str] | None:
        """The folders a lookup may look in, or None when unscoped. A provider
        that fails confines the answer to nothing rather than to everywhere."""
        provider = self._roots
        if provider is None:
            return None
        try:
            return [str(r) for r in provider() or [] if str(r or "").strip()]
        except Exception:
            logger.debug("ownership: could not read the download and library folders", exc_info=True)
            return []

    def ownership_of(self, track_id: str, *, user_id: str | None = None) -> dict | None:
        """Best surviving copy of ``track_id`` that still exists on disk right now,
        or None if no recorded path survives (a wanted-again deleted file).

        Rows are considered highest delivered quality first, then most recent, and
        the first whose path passes a live existence check wins. The deleted-path
        row is skipped, not removed, so re-creating the file makes it own again.
        Only paths inside the configured folders are considered (set_roots).
        """
        if user_id is None:
            rows = self._read(
                """SELECT path, quality_tier, quality_rank, audio_mode, bit_depth,
                          sample_rate, codecs, recorded_at, requested_rank, ceiling_rank,
                          degraded_tries
                   FROM downloads WHERE track_id = ?
                   ORDER BY quality_rank DESC, recorded_at DESC""",
                (str(track_id),),
            )
        else:
            rows = self._read(
                """SELECT path, quality_tier, quality_rank, audio_mode, bit_depth,
                          sample_rate, codecs, recorded_at, requested_rank, ceiling_rank,
                          degraded_tries
                   FROM downloads WHERE track_id = ? AND user_id = ?
                   ORDER BY quality_rank DESC, recorded_at DESC""",
                (str(track_id), str(user_id)),
            )
        return _best_surviving(rows, self._scope())

    # sqlite's default variable limit is 999; a page of collections asks for
    # far fewer at a time, but the query is chunked regardless.
    _MANY_CHUNK = 400

    def ownership_of_many(self, track_ids) -> dict[str, dict | None]:
        """ownership_of for a whole batch of ids, one query per chunk instead
        of one per id, with exactly the same per-id answer.

        The landing's cards ask for the members of every collection they show
        (hundreds of ids at launch), and one query per id meant one prepared
        statement, one interpreter hold and one result conversion each, on
        the refresh pool, while the GUI thread waited for the interpreter to
        paint the launch water (sampled live: three pool threads inside
        sqlite for the whole build). The disk stat per surviving row is
        unchanged: it happens outside any query, per id, as before."""
        ids = [str(t) for t in dict.fromkeys(track_ids)]
        out: dict[str, dict | None] = {}
        roots = self._scope()
        for i in range(0, len(ids), self._MANY_CHUNK):
            chunk = ids[i : i + self._MANY_CHUNK]
            marks = ",".join("?" * len(chunk))
            # The only text spliced into the statement is the placeholder list;
            # every id travels as a bound parameter.
            rows = self._read(
                f"""SELECT track_id, path, quality_tier, quality_rank, audio_mode, bit_depth,
                          sample_rate, codecs, recorded_at, requested_rank, ceiling_rank,
                          degraded_tries
                   FROM downloads WHERE track_id IN ({marks})
                   ORDER BY quality_rank DESC, recorded_at DESC""",  # noqa: S608
                tuple(chunk),
            )
            by_id: dict[str, list] = {}
            for row in rows:
                by_id.setdefault(str(row[0]), []).append(row[1:])
            for tid in chunk:
                out[tid] = _best_surviving(by_id.get(tid, []), roots)
        return out

    def folder_names_under(self, base: str, limit: int = 5000) -> list[str]:
        """The first folder name under ``base`` of every path this store has
        recorded, newest first, deduplicated.

        These are not guesses. They are the exact directory names Waves itself
        wrote on that disk, which makes them the one seed list worth having when
        a share's directory listing is broken and the folders can only be found
        by asking for them by name (see LibraryIndex.probe_folders). A path that
        does not live under ``base`` is skipped, so switching library folders
        never leaks names from the old one."""
        base = os.path.normpath(os.path.expanduser(str(base or ""))).rstrip(os.sep)
        if not base:
            return []
        prefix = base + os.sep
        names: dict[str, None] = {}
        rows = self._read("SELECT path FROM downloads WHERE path IS NOT NULL ORDER BY recorded_at DESC")
        for (path,) in rows:
            text = str(path or "")
            if not text.startswith(prefix):
                continue
            head = text[len(prefix) :].split(os.sep, 1)[0].strip()
            if head and head not in (".", ".."):
                names.setdefault(head, None)
                if len(names) >= max(1, int(limit)):
                    break
        return list(names)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._conn.close()
        # Other threads' read connections close with their threads; this
        # thread's own goes now so the file handle is not held past quit.
        conn = getattr(self._readers, "conn", None)
        if conn is not None:
            self._readers.conn = None
            conn.close()
