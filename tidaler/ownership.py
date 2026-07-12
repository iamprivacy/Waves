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
QUALITY_RANK = {"LOW": 0, "HIGH": 1, "LOSSLESS": 2, "HI_RES_LOSSLESS": 3}

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
                       PRIMARY KEY (track_id, path)
                   )""")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_downloads_track ON downloads(track_id)")
            self._ensure_columns()
            self._conn.commit()

    def _ensure_columns(self) -> None:
        """Add any expected column missing from an older DB. A no-op once the DB
        matches the current schema; lets a future column land without a manual
        migration. Caller holds the lock."""
        have = {row[1] for row in self._conn.execute("PRAGMA table_info(downloads)")}
        for name, decl in _ADDED_COLUMNS:
            if name not in have:
                self._conn.execute(f"ALTER TABLE downloads ADD COLUMN {name} {decl}")

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
    ) -> None:
        """Record that ``track_id`` was written to ``path`` at ``quality_tier``.

        Upserts on (track_id, path): re-recording the same file updates its
        quality and timestamp in place; a different path for the same track adds
        a row, so every known copy survives for the live ownership check.
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
        )
        with self._lock:
            self._conn.execute(
                """INSERT INTO downloads
                       (track_id, path, quality_tier, quality_rank, audio_mode,
                        bit_depth, sample_rate, codecs, user_id, recorded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(track_id, path) DO UPDATE SET
                       quality_tier = excluded.quality_tier,
                       quality_rank = excluded.quality_rank,
                       audio_mode   = excluded.audio_mode,
                       bit_depth    = excluded.bit_depth,
                       sample_rate  = excluded.sample_rate,
                       codecs       = excluded.codecs,
                       user_id      = excluded.user_id,
                       recorded_at  = excluded.recorded_at""",
                row,
            )
            self._conn.commit()

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
                              sample_rate, codecs, recorded_at
                       FROM downloads WHERE track_id = ?
                       ORDER BY quality_rank DESC, recorded_at DESC""",
                    (str(track_id),),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT path, quality_tier, quality_rank, audio_mode, bit_depth,
                              sample_rate, codecs, recorded_at
                       FROM downloads WHERE track_id = ? AND user_id = ?
                       ORDER BY quality_rank DESC, recorded_at DESC""",
                    (str(track_id), str(user_id)),
                ).fetchall()
        # Existence check is intentionally OUTSIDE the lock: it can stat the disk,
        # and a read must never hold up a worker-thread write behind it.
        for path, tier, rank, mode, depth, rate, codecs, recorded_at in rows:
            if path and os.path.exists(path):
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
                }
        return None

    def close(self) -> None:
        with self._lock:
            self._conn.close()
