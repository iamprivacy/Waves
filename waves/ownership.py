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

Pure standard library (sqlite3), with no Qt and no tidalapi import, so it unit
tests without the GUI stack and never couples the download engine to the UI.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from threading import Lock

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
        with self._lock:
            rows = self._conn.execute(
                "SELECT track_id FROM collection_members WHERE collection_id = ?",
                (str(collection_id),),
            ).fetchall()
        if not rows:
            return None
        return [r[0] for r in rows]

    def ownership_of(self, track_id: str, *, user_id: str | None = None) -> dict | None:
        """Best surviving copy of ``track_id`` that still exists on disk right now,
        or None if no recorded path survives (a wanted-again deleted file).

        Rows are considered highest delivered quality first, then most recent, and
        the first whose path passes a live existence check wins. The deleted-path
        row is skipped, not removed, so re-creating the file makes it own again.
        """
        with self._lock:
            if user_id is None:
                rows = self._conn.execute(
                    """SELECT path, quality_tier, quality_rank, audio_mode, bit_depth,
                              sample_rate, codecs, recorded_at, requested_rank, ceiling_rank,
                              degraded_tries
                       FROM downloads WHERE track_id = ?
                       ORDER BY quality_rank DESC, recorded_at DESC""",
                    (str(track_id),),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT path, quality_tier, quality_rank, audio_mode, bit_depth,
                              sample_rate, codecs, recorded_at, requested_rank, ceiling_rank,
                              degraded_tries
                       FROM downloads WHERE track_id = ? AND user_id = ?
                       ORDER BY quality_rank DESC, recorded_at DESC""",
                    (str(track_id), str(user_id)),
                ).fetchall()
        # Existence check is intentionally OUTSIDE the lock: it can stat the disk,
        # and a read must never hold up a worker-thread write behind it. A
        # zero-byte survivor is a truncation artifact, not a copy: skip it (not
        # removed, like a deleted path) so the track reads as wanted again.
        for path, tier, rank, mode, depth, rate, codecs, recorded_at, requested, ceiling, degraded in rows:
            if path and _nonempty_file(path):
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

    def close(self) -> None:
        with self._lock:
            self._conn.close()
