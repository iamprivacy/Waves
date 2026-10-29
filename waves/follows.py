"""The artists you follow and what they have released: the store behind the
Releases pane.

Three things live here, all in one sqlite file (``releases.sqlite3``, next to
the settings):

* ``followed``: the follow list. Waves keeps its own; a row says where it came
  from (``origin``: mirrored from the TIDAL favorites, imported from the
  local library, or followed by hand) and whether the user ever acted on it
  (``touched``), so the read-only TIDAL mirror can drop a favorite the user
  removed on TIDAL without ever dropping one they followed here themselves.
* ``seen``: the delta baseline per followed artist, which only ever grows. A
  release is announced exactly once and never again, even after it is
  downloaded or the file is deleted: "is this new" is about whether the user
  has been TOLD, not what is on disk, so it is a recorded fact, not a live
  check. The first complete, non-empty catalog a follow sees is absorbed
  silently (``seeded``), so a new follow never floods the badge with a back
  catalog, and a partial or empty fetch is never trusted as a baseline.
* ``releases``: the feed window, every release the sweep saw within the
  user's chosen number of months (plus anything dated in the future), kept
  by the followed artist that fetched it so a collaboration can stand under
  each of its artists and still show once (``feed`` picks one row per
  release id). ``acknowledged = 0`` marks the rows the badge counts.
* ``mb_releases``: with the opt-in MusicBrainz check, each followed artist's
  release groups as MusicBrainz lists them (albums, EPs and singles), each
  with the TIDAL row it was matched to or '' when TIDAL has none: those are
  the ``missing`` marks the Artists timeline draws in red. The follow row
  keeps the artist's MBID and when it was last checked (``mb_checked``).

Pure standard library (sqlite3), with no Qt and no tidalapi import, so it unit
tests without the GUI stack. Thread-safe: the sweep writes on a worker thread
while the GUI thread reads the feed and the badge; every statement runs under
one lock and WAL keeps a read from blocking behind a write. Only outcomes and
counts are logged here: never an artist's name or a release title.
"""

from __future__ import annotations

import calendar
import json
import logging
import os
import time
from collections.abc import Iterable
from threading import Lock

from waves import db, musicbrainz
from waves.matching import canon
from waves.release_delta import _twin_key, all_group_ids, collapse_atmos_twins, edition_group_ids, split_fresh

logger = logging.getLogger("waves.follows")

# What a follow row carries for the Following views (followed_artists): its
# releases newest first for the shelf of covers, which scrolls through every
# one, and the whole career on record for the timeline, each within a cap a
# very busy artist cannot blow past.
FOLLOW_CAREER_CAP = 400
FOLLOW_SHELF = FOLLOW_CAREER_CAP

# Music videos share the releases table and the baseline under ids of their
# own (``v:<video id>``): TIDAL numbers videos and albums apart, so a bare
# video id can equal an album id. Outside the store a video row carries the
# bare id and ``kind: "video"``.
VIDEO_PREFIX = "v:"
VIDEO_RTYPE = "video"
# How often a follow's videos are read: about once a day for an artist with a
# video out lately (VIDEO_ACTIVE_DAYS), once a week otherwise (most library
# artists). An hour short of each, so the every-other-visit rhythm of a
# twelve-hour round never slips a visit.
VIDEO_AGAIN_S = 23 * 3600
VIDEO_QUIET_AGAIN_S = 7 * 86400 - 3600
# An artist with a video out within this many days is read daily.
VIDEO_ACTIVE_DAYS = 365

# Featured releases (another artist's release a follow is a guest on) are read
# only for the auto-download, so they never enter the feed: their baseline
# lives in ``seen`` under ids of their own (``g:<album id>``), apart from the
# artist's own releases, and they are read about once a day.
GUEST_PREFIX = "g:"
GUEST_AGAIN_S = 23 * 3600


def video_rid(video_id) -> str:
    """The store's id for a video (see VIDEO_PREFIX)."""
    return VIDEO_PREFIX + str(video_id)


def bare_id(release_id) -> str:
    """A stored id as the rest of the app knows it: a video's without its prefix."""
    rid = str(release_id)
    return rid[len(VIDEO_PREFIX) :] if rid.startswith(VIDEO_PREFIX) else rid


def _fold(s: str) -> str:
    """Text as the feed's search compares it: accents off Latin letters,
    case folded, runs of space collapsed."""
    return " ".join(canon(str(s or "")).casefold().split())


def _day_epoch(day: str) -> float:
    """Midnight UTC of a "YYYY-MM-DD" day, in epoch seconds."""
    return float(calendar.timegm(time.strptime(str(day)[:10], "%Y-%m-%d")))


ORIGIN_TIDAL = "tidal"
ORIGIN_LIBRARY = "library"
ORIGIN_MANUAL = "manual"
_ORIGINS = frozenset({ORIGIN_TIDAL, ORIGIN_LIBRARY, ORIGIN_MANUAL})

# Auto-download bookkeeping on a release row.
DL_NONE = 0  # nothing planned
DL_WAITING = 1  # auto-download waits for the release day
DL_QUEUED = 2  # handed to the download queue

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS followed (
           artist_id    TEXT PRIMARY KEY,
           name         TEXT,
           art          TEXT,
           origin       TEXT NOT NULL DEFAULT 'manual',
           touched      INTEGER NOT NULL DEFAULT 0,
           tidal_fav    INTEGER NOT NULL DEFAULT 0,
           added_at     INTEGER NOT NULL DEFAULT 0,
           last_checked INTEGER NOT NULL DEFAULT 0,
           seeded       INTEGER NOT NULL DEFAULT 0,
           fail_count   INTEGER NOT NULL DEFAULT 0,
           empty_checks INTEGER NOT NULL DEFAULT 0,
           deep_read    INTEGER NOT NULL DEFAULT 0,
           mbid         TEXT NOT NULL DEFAULT '',
           mb_checked   INTEGER NOT NULL DEFAULT 0
       )""",
    """CREATE TABLE IF NOT EXISTS seen (
           artist_id  TEXT NOT NULL,
           release_id TEXT NOT NULL,
           PRIMARY KEY (artist_id, release_id)
       )""",
    """CREATE TABLE IF NOT EXISTS releases (
           artist_id         TEXT NOT NULL,
           release_id        TEXT NOT NULL,
           title             TEXT,
           artist_name       TEXT,
           primary_artist_id TEXT,
           artists_json      TEXT,
           art               TEXT,
           date              TEXT,
           listed            TEXT,
           year              TEXT,
           rtype             TEXT NOT NULL DEFAULT 'album',
           explicit          INTEGER NOT NULL DEFAULT 0,
           tracks            INTEGER NOT NULL DEFAULT 0,
           audio_tracks      INTEGER NOT NULL DEFAULT 0,
           duration_sec      INTEGER NOT NULL DEFAULT 0,
           quality           TEXT,
           atmos             INTEGER NOT NULL DEFAULT 0,
           sibling_json      TEXT,
           detected_at       INTEGER NOT NULL DEFAULT 0,
           acknowledged      INTEGER NOT NULL DEFAULT 1,
           dl_state          INTEGER NOT NULL DEFAULT 0,
           updated_at        INTEGER NOT NULL DEFAULT 0,
           PRIMARY KEY (artist_id, release_id)
       )""",
    "CREATE INDEX IF NOT EXISTS idx_rel_release ON releases(release_id, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_rel_unack ON releases(acknowledged)",
    """CREATE TABLE IF NOT EXISTS tidal_unfollowed (
           artist_id TEXT PRIMARY KEY,
           at        INTEGER NOT NULL DEFAULT 0
       )""",
    """CREATE TABLE IF NOT EXISTS import_tray (
           norm            TEXT PRIMARY KEY,
           name            TEXT,
           reason          TEXT,
           candidates_json TEXT,
           ts              INTEGER NOT NULL DEFAULT 0
       )""",
    """CREATE TABLE IF NOT EXISTS import_dismissed (
           norm TEXT PRIMARY KEY,
           ts   INTEGER NOT NULL DEFAULT 0
       )""",
    """CREATE TABLE IF NOT EXISTS meta (
           key   TEXT PRIMARY KEY,
           value TEXT
       )""",
    """CREATE TABLE IF NOT EXISTS joined_reported (
           release_id TEXT PRIMARY KEY,
           at         INTEGER NOT NULL DEFAULT 0
       )""",
    """CREATE TABLE IF NOT EXISTS mb_releases (
           artist_id  TEXT NOT NULL,
           rgid       TEXT NOT NULL,
           title      TEXT,
           date       TEXT,
           rtype      TEXT NOT NULL DEFAULT 'album',
           release_id TEXT NOT NULL DEFAULT '',
           PRIMARY KEY (artist_id, rgid)
       )""",
)

# Columns a store from before them grows on open (the way deep_read did).
_FOLLOWED_GROWN = (
    ("deep_read", "INTEGER NOT NULL DEFAULT 0"),
    ("mbid", "TEXT NOT NULL DEFAULT ''"),
    ("mb_checked", "INTEGER NOT NULL DEFAULT 0"),
    # The music video read: when it last ran, whether its first answer was
    # taken in as the baseline, and how many complete empty answers came
    # before that (the second seeds an empty baseline, as for releases).
    ("video_checked", "INTEGER NOT NULL DEFAULT 0"),
    ("video_seeded", "INTEGER NOT NULL DEFAULT 0"),
    ("video_empty", "INTEGER NOT NULL DEFAULT 0"),
    # The featured read, the same three for the releases a follow is a guest on.
    ("guest_checked", "INTEGER NOT NULL DEFAULT 0"),
    ("guest_seeded", "INTEGER NOT NULL DEFAULT 0"),
    ("guest_empty", "INTEGER NOT NULL DEFAULT 0"),
    # When a fresh-releases hint asked for this follow to be checked ahead of
    # the round (0 for none): see waves.fresh_releases. A check clears it.
    ("hinted", "INTEGER NOT NULL DEFAULT 0"),
)

_RELEASE_COLS = (
    "artist_id, release_id, title, artist_name, primary_artist_id, artists_json, art, date, listed, year, "
    "rtype, explicit, tracks, audio_tracks, duration_sec, quality, atmos, sibling_json, detected_at, "
    "acknowledged, dl_state, updated_at"
)
# A refreshed row keeps its acknowledged, dl_state and detected_at: those are
# the user's and the sweep's bookkeeping, not catalog facts.
_UPSERT_HEAD = "INSERT INTO releases (" + _RELEASE_COLS + ")"
_UPSERT_VALUES = " VALUES (" + ", ".join(["?"] * 22) + ")"
_UPSERT_SET = (
    " ON CONFLICT(artist_id, release_id) DO UPDATE SET"
    " title = excluded.title, artist_name = excluded.artist_name,"
    " primary_artist_id = excluded.primary_artist_id, artists_json = excluded.artists_json,"
    " art = excluded.art, date = excluded.date, listed = excluded.listed, year = excluded.year,"
    " rtype = excluded.rtype, explicit = excluded.explicit, tracks = excluded.tracks,"
    " audio_tracks = excluded.audio_tracks, duration_sec = excluded.duration_sec,"
    " quality = excluded.quality, atmos = excluded.atmos, sibling_json = excluded.sibling_json,"
    " updated_at = excluded.updated_at"
)
_UPSERT_RELEASE = _UPSERT_HEAD + _UPSERT_VALUES + _UPSERT_SET

# The feed's own generation: bumped by a write to a column the feed shows,
# never by a refresh that only restamps updated_at (every sweep step
# re-upserts each release it reads) or by the follows' check stamps. Kept in
# the connection's temp schema, so it lives and dies with the store.
_FEED_SHOWN = (
    "artist_id, release_id, title, artist_name, primary_artist_id, artists_json, art, date, listed, year, "
    "rtype, explicit, tracks, audio_tracks, duration_sec, quality, atmos, detected_at, acknowledged, dl_state"
)
_FEED_GEN_DDL = (
    "CREATE TEMP TABLE IF NOT EXISTS feed_gen (n INTEGER NOT NULL)",
    "INSERT INTO feed_gen (n) SELECT 0 WHERE NOT EXISTS (SELECT 1 FROM feed_gen)",
    "CREATE TEMP TRIGGER IF NOT EXISTS feed_gen_ins AFTER INSERT ON main.releases"
    " BEGIN UPDATE feed_gen SET n = n + 1; END",
    "CREATE TEMP TRIGGER IF NOT EXISTS feed_gen_del AFTER DELETE ON main.releases"
    " BEGIN UPDATE feed_gen SET n = n + 1; END",
    # Which rows are featured hangs on who is followed (see _GUEST_CLAUSE).
    "CREATE TEMP TRIGGER IF NOT EXISTS feed_gen_follow AFTER INSERT ON main.followed"
    " BEGIN UPDATE feed_gen SET n = n + 1; END",
    "CREATE TEMP TRIGGER IF NOT EXISTS feed_gen_unfollow AFTER DELETE ON main.followed"
    " BEGIN UPDATE feed_gen SET n = n + 1; END",
    "CREATE TEMP TRIGGER IF NOT EXISTS feed_gen_upd AFTER UPDATE ON main.releases WHEN "  # noqa: S608 (fixed columns)
    + " OR ".join(f"OLD.{c} IS NOT NEW.{c}" for c in _FEED_SHOWN.split(", "))
    + " BEGIN UPDATE feed_gen SET n = n + 1; END",
)


def _mb_day(date: str) -> str:
    """A MusicBrainz first-release date as a day the timeline can place:
    a bare year or month lands on its first day; nothing when unknown."""
    d = str(date or "")
    if len(d) >= 10:
        return d[:10]
    if len(d) == 7:
        return d + "-01"
    if len(d) == 4:
        return d + "-01-01"
    return ""


def _missing_marks(groups: list[dict], career: list[dict]) -> list[dict]:
    """The release groups TIDAL has no row for, as the timeline draws them,
    newest first within the career cap. Matched here again, against the
    career as it stands (TIDAL rows come and go between two checks); the
    match the check stored counts while its row is still on record."""
    if not groups:
        return []
    held = {str(e.get("id") or "") for e in career}
    stored = {str(g.get("rgid") or ""): str(g.get("release_id") or "") for g in groups}
    out = []
    for g in musicbrainz.match_groups(groups, career):
        day = _mb_day(g.get("date"))
        if g.get("release_id") or not day or stored.get(str(g["rgid"])) in held:
            continue
        out.append(
            {
                "id": "mb:" + str(g["rgid"]),
                "rgid": str(g["rgid"]),
                "title": g.get("title") or "",
                "day": day,
                # As much of the day as MusicBrainz knows ("2019", "2019-05"
                # or the whole day): ``day`` is padded for placing it.
                "shown": str(g.get("date") or "")[:10],
                "rtype": g.get("rtype") or "album",
                "mb": True,
            }
        )
    out.sort(key=lambda e: (e["day"], e["title"].lower(), e["rgid"]), reverse=True)
    return out[:FOLLOW_CAREER_CAP]


def _is_album(r: dict) -> bool:
    """Which section a release was fetched in: albums, or EPs and singles."""
    return str(r.get("rtype") or "album") == "album"


# The reads that can leave music videos out carry the clause
# " AND (? = 1 OR rtype != 'video')" with the show flag bound to it.
# Featured releases (TIDAL lists a release under a follow that another
# artist leads, one nobody follows here) are left out the same way, by
# _GUEST_CLAUSE with its show flag. A release led by another follow is that
# follow's own, so it stays. A row stored without a lead artist is kept.
_GUEST_CLAUSE = (
    " AND (? = 1 OR COALESCE(primary_artist_id, '') = '' OR primary_artist_id IN (SELECT artist_id FROM followed))"
)


def _date_key(r: dict) -> str:
    """The day a release sorts and buckets by: the listed day for a proven
    reissue, else the release date. Empty when TIDAL never said."""
    return str(r.get("listed") or r.get("date") or "")[:10]


def _kind_keeps(r: dict, kind: str, today: str) -> bool:
    """Whether a feed row passes the feed's filter as the pane applies it:
    a full day, out by ``today`` (or ahead of it for "upcoming"), and the
    kind asked for; every row when ``kind`` is empty."""
    if not kind:
        return True
    day = _date_key(r)
    if len(day) != 10:
        return False
    if kind == "upcoming":
        return day > today
    return day <= today and (kind == "all" or str(r.get("rtype") or "album") == kind)


def _order_day(r: dict) -> str:
    """The day TIDAL orders a section by (the release date, a reissue's
    original), for the span a newest-first fetch covers."""
    return str(r.get("date") or r.get("listed") or "")[:10]


class FollowStore:
    """See the module docstring."""

    def __init__(self, db_path: str) -> None:
        self._path = str(db_path)
        parent = os.path.dirname(self._path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = Lock()
        # Held around each read-modify-write of a waiting list kept in meta
        # (guest_waiting, atmos_waiting): a pool worker's retry and the GUI
        # thread's drain write the same list, and one must not undo the other.
        self._lists_lock = Lock()
        self._search_kept: tuple | None = None
        self._conn = db.connect(self._path, kind=db.STATE)
        with self._lock:
            self._conn.execute("PRAGMA busy_timeout=5000")
            # One transaction around the schema pass: sqlite3 runs DDL in
            # autocommit otherwise, and a crash between statements must roll
            # back to intact tables. On failure close explicitly rather than
            # leaving the half-open transaction to garbage collection.
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                for stmt in _SCHEMA:
                    self._conn.execute(stmt)
                # A store made before a column grows it.
                cols = {r[1] for r in self._conn.execute("PRAGMA table_info(followed)")}
                for col, decl in _FOLLOWED_GROWN:
                    if col not in cols:
                        self._conn.execute(f"ALTER TABLE followed ADD COLUMN {col} {decl}")
                for stmt in _FEED_GEN_DDL:
                    self._conn.execute(stmt)
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                self._conn.close()
                raise

    def drop_foreign_ids(self) -> int:
        """Remove every follow whose id TIDAL could never have issued (TIDAL
        artist ids are all digits), with its releases and marks. Only a test
        fixture writes such an id ("ar1", "im2"); the app opens its profile's
        store through here, so one that reached a real profile is gone at the
        next launch instead of sitting in the feed unchecked forever.
        Returns how many follows went."""
        foreign = "artist_id = '' OR artist_id GLOB '*[^0-9]*'"
        with self._lock, self._conn:
            ids = [
                r[0]
                for r in self._conn.execute(
                    "SELECT artist_id FROM followed WHERE artist_id = '' OR artist_id GLOB '*[^0-9]*'"
                )
            ]
            if not ids:
                return 0
            for table in ("releases", "seen", "mb_releases", "tidal_unfollowed"):
                self._conn.execute(f"DELETE FROM {table} WHERE {foreign}")  # noqa: S608 (fixed tables)
            self._conn.execute("DELETE FROM followed WHERE artist_id = '' OR artist_id GLOB '*[^0-9]*'")
        return len(ids)

    # ----- follows ----------------------------------------------------------

    def follow(self, artist_id: str, name: str, art: str, *, origin: str = ORIGIN_MANUAL, touched: bool = True) -> bool:
        """Follow ``artist_id``. Returns True when the row is new.

        Re-following refreshes the display name and art, never downgrades
        ``touched`` and never changes ``origin``: where a follow came from is a
        fact about its first appearance. A hand follow (``touched``) also
        clears any "unfollowed on Waves" memory, so the TIDAL mirror may carry
        the artist again. The baseline starts unseeded: the first complete
        catalog the sweep fetches is absorbed silently (see record_check),
        so following never floods the badge with a back catalog.
        """
        aid = str(artist_id)
        if not aid:
            return False
        if origin not in _ORIGINS:
            origin = ORIGIN_MANUAL
        now = int(time.time())
        with self._lock:
            existed = self._conn.execute("SELECT 1 FROM followed WHERE artist_id = ?", (aid,)).fetchone()
            self._conn.execute(
                """INSERT INTO followed (artist_id, name, art, origin, touched, tidal_fav, added_at)
                       VALUES (?, ?, ?, ?, ?, 0, ?)
                   ON CONFLICT(artist_id) DO UPDATE SET
                       name = CASE WHEN excluded.name != '' THEN excluded.name ELSE followed.name END,
                       art = CASE WHEN excluded.art != '' THEN excluded.art ELSE followed.art END,
                       touched = MAX(followed.touched, excluded.touched)""",
                (aid, name or "", art or "", origin, 1 if touched else 0, now),
            )
            if touched:
                self._conn.execute("DELETE FROM tidal_unfollowed WHERE artist_id = ?", (aid,))
            self._conn.commit()
        if existed is None:
            logger.info("follow added (%s)", origin)
        return existed is None

    def follow_many(self, rows: Iterable[dict], *, origin: str = ORIGIN_LIBRARY) -> int:
        """Follow every row ``{id, name, art}`` in ONE transaction (the library
        import's confirm, hundreds of artists at once). Rows already followed
        are left exactly as they are. Returns how many were new."""
        if origin not in _ORIGINS:
            origin = ORIGIN_MANUAL
        now = int(time.time())
        added = 0
        with self._lock:
            for r in rows:
                aid = str(r.get("id") or r.get("artist_id") or "")
                if not aid:
                    continue
                cur = self._conn.execute(
                    """INSERT OR IGNORE INTO followed (artist_id, name, art, origin, touched, tidal_fav, added_at)
                       VALUES (?, ?, ?, ?, 1, 0, ?)""",
                    (aid, r.get("name") or "", r.get("art") or "", origin, now),
                )
                added += int(cur.rowcount or 0)
                self._conn.execute("DELETE FROM tidal_unfollowed WHERE artist_id = ?", (aid,))
            self._conn.commit()
        logger.info("%d follow(s) added in bulk (%s)", added, origin)
        return added

    def unfollow(self, artist_id: str, *, remember_tidal: bool = False) -> None:
        """Stop following and forget everything for this artist (baseline and
        feed rows), so following again later starts from a clean slate.
        ``remember_tidal`` records that the user dropped a TIDAL favorite HERE,
        so the mirror does not bring it straight back on its next walk."""
        aid = str(artist_id)
        with self._lock:
            self._conn.execute("DELETE FROM followed WHERE artist_id = ?", (aid,))
            self._conn.execute("DELETE FROM seen WHERE artist_id = ?", (aid,))
            self._conn.execute("DELETE FROM releases WHERE artist_id = ?", (aid,))
            self._conn.execute("DELETE FROM mb_releases WHERE artist_id = ?", (aid,))
            if remember_tidal:
                self._conn.execute(
                    "INSERT OR REPLACE INTO tidal_unfollowed (artist_id, at) VALUES (?, ?)", (aid, int(time.time()))
                )
            self._conn.commit()
        logger.info("follow removed")

    def tidal_unfollowed_ids(self) -> set[str]:
        """Artists the user dropped here while they were TIDAL favorites."""
        with self._lock:
            return {r[0] for r in self._conn.execute("SELECT artist_id FROM tidal_unfollowed")}

    def is_followed(self, artist_id: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM followed WHERE artist_id = ?", (str(artist_id),)).fetchone()
        return row is not None

    def follow_info(self, artist_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT artist_id, name, art, origin, touched, tidal_fav, added_at, last_checked, seeded, mbid, mb_checked"
                " FROM followed WHERE artist_id = ?",
                (str(artist_id),),
            ).fetchone()
        return self._follow_row(row) if row else None

    @staticmethod
    def _follow_row(r) -> dict:
        return {
            "artist_id": r[0],
            "id": r[0],
            "name": r[1] or "",
            "art": r[2] or "",
            "origin": r[3] or ORIGIN_MANUAL,
            "touched": bool(r[4]),
            "tidal_fav": bool(r[5]),
            "added_at": int(r[6] or 0),
            "last_checked": int(r[7] or 0),
            "seeded": bool(r[8]),
            "mbid": str(r[9] or ""),
            "mb_checked": int(r[10] or 0),
        }

    def follows(self) -> list[dict]:
        """Every follow, by name, as the ``followed`` table holds it (id,
        name, origin, TIDAL flag): for callers that need only those, without
        the release history ``followed_artists`` dresses each one with."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT artist_id, name, art, origin, touched, tidal_fav, added_at, last_checked, seeded, mbid, mb_checked"
                " FROM followed ORDER BY name COLLATE NOCASE, artist_id"
            ).fetchall()
        return [self._follow_row(r) for r in rows]

    def followed_artists(
        self, today: str = "", *, videos: bool = True, guests: bool = True, marks: bool = True
    ) -> list[dict]:
        """Every follow, by name, each with the newest release on record for
        it: ``last_release`` (the day), ``last_title`` and ``last_rtype``,
        all empty before a first check or when nothing is kept.

        Music videos count as releases here (a video entry carries ``kind:
        "video"`` and its bare id), unless ``videos`` is off: then none is
        read at all, so the caps are never spent on hidden rows. ``total``,
        ``since`` and the MusicBrainz marks always count the music alone.
        With ``guests`` off the featured releases (see _GUEST_CLAUSE) are left
        out of all of it but the marks, which still match the whole career:
        a hidden release is no gap.

        Each row also carries what the Following views draw, one entry per
        release (an Atmos twin folded): ``releases``, the latest
        ``FOLLOW_SHELF`` by the day TIDAL listed them (the shelf covers);
        ``career``, every one on record by the day it first came out, so a
        reissue sits where the music did, newest first and capped at
        ``FOLLOW_CAREER_CAP`` (the timeline); ``total``, how many are on
        record; ``since``, the year of the oldest; ``missing``, the
        MusicBrainz release groups TIDAL has no row for (see ``record_mb``),
        each ``{id, rgid, title, day, rtype, mb: True}``, newest first and
        capped the same way, matched against the career as it stands now
        (a deep read that fills a gap clears its mark at once). With
        ``marks`` off they are not worked out at all (every ``missing`` is
        empty): only the Ledger with the setting on draws them."""
        if not today:
            today = time.strftime("%Y-%m-%d")
        with self._lock:
            rows = self._conn.execute(
                "SELECT artist_id, name, art, origin, touched, tidal_fav, added_at, last_checked, seeded, mbid, mb_checked"
                " FROM followed ORDER BY name COLLATE NOCASE, artist_id"
            ).fetchall()
            mb_rows = (
                self._conn.execute(
                    "SELECT artist_id, rgid, title, date, rtype, release_id FROM mb_releases"
                    " ORDER BY artist_id, date DESC, title COLLATE NOCASE, rgid"
                ).fetchall()
                if marks
                else []
            )
            # Bare-column rule again: the title and kind come from the MAX row.
            latest = {
                r[0]: (str(r[3] or ""), str(r[1] or ""), str(r[2] or "album"))
                for r in self._conn.execute(
                    "SELECT artist_id, title, rtype, MAX(COALESCE(NULLIF(listed, ''), date)) FROM releases"  # noqa: S608 (fixed clause)
                    " WHERE COALESCE(NULLIF(listed, ''), date) != '' AND (? = 1 OR rtype != 'video')"
                    + _GUEST_CLAUSE
                    + " GROUP BY artist_id",
                    (int(videos), int(guests)),
                )
            }
            # The featured rows come along when the marks are worked out
            # (they match the whole career), each told apart as shown or not.
            held = self._conn.execute(
                "SELECT artist_id, release_id, title, artist_name, art, date, listed, rtype, tracks, quality,"  # noqa: S608 (fixed clause)
                " (1 = 1" + _GUEST_CLAUSE + ")"
                " FROM releases WHERE COALESCE(NULLIF(listed, ''), date) != '' AND (? = 1 OR rtype != 'video')"
                + ("" if marks else _GUEST_CLAUSE)
                + " ORDER BY artist_id, COALESCE(NULLIF(listed, ''), date) DESC, title COLLATE NOCASE, release_id",
                (int(guests), int(videos), *(() if marks else (int(guests),))),
            ).fetchall()
        by_artist: dict[str, list[dict]] = {}
        whole: dict[str, list[dict]] = {}
        for h in held:
            if h[10]:
                by_artist.setdefault(h[0], []).append(self._held_row(h))
            if marks and h[7] != VIDEO_RTYPE:
                whole.setdefault(h[0], []).append(self._held_row(h))
        groups: dict[str, list[dict]] = {}
        for m in mb_rows:
            groups.setdefault(m[0], []).append(
                {"rgid": m[1], "title": m[2] or "", "date": m[3] or "", "rtype": m[4], "release_id": m[5] or ""}
            )
        out = []
        for r in rows:
            row = self._follow_row(r)
            day, title, rtype = latest.get(r[0], ("", "", ""))
            row.update({"last_release": day, "last_title": title, "last_rtype": rtype})
            kept, career, years = self._career(by_artist.get(r[0], []))
            mb = groups.get(r[0], [])
            row.update(
                {
                    "releases": kept,
                    "career": career[:FOLLOW_CAREER_CAP],
                    "total": len(years),
                    "since": min(years) if years else "",
                    "missing": _missing_marks(mb, self._career(whole.get(r[0], []))[1]) if mb else [],
                }
            )
            out.append(row)
        return out

    @staticmethod
    def _held_row(h) -> dict:
        return {
            "id": h[1],
            "title": h[2] or "",
            "artist_name": h[3] or "",
            "art": h[4] or "",
            "date": h[5] or "",
            "listed": h[6] or "",
            "rtype": h[7] or "album",
            "tracks": int(h[8] or 0),
            "quality": h[9] or "",
        }

    @staticmethod
    def _career(held: list[dict]) -> tuple[list[dict], list[dict], list[str]]:
        """One artist's rows (newest listed first) as the Following views
        draw them: the shelf entries, the whole career on first-release
        days newest first (uncapped), and the year of every dated row that
        is music (a video entry is marked ``kind: "video"``)."""
        kept, career, years = [], [], []
        for rel in collapse_atmos_twins(held):
            entry = {
                "id": bare_id(rel["id"]),
                "title": rel["title"],
                "art": rel["art"],
                "day": str(rel["listed"] or rel["date"])[:10],
                "rtype": rel["rtype"],
                "tracks": rel["tracks"],
            }
            video = rel["rtype"] == VIDEO_RTYPE
            if video:
                entry["kind"] = "video"
            if len(kept) < FOLLOW_SHELF:
                kept.append(entry)
            first = str(rel["date"] or rel["listed"])[:10]
            if not video:
                years.append(first[:4])
            if len(first) == 10:
                career.append({**entry, "day": first})
        career.sort(key=lambda e: (e["day"], e["id"]), reverse=True)
        return kept, career, years

    def career_rows(self, artist_id: str) -> list[dict]:
        """One follow's career as ``followed_artists`` builds it (every
        dated release on its first-release day, newest first, uncapped):
        what the MusicBrainz check matches release groups against."""
        with self._lock:
            return self._career_of(str(artist_id))

    def _career_of(self, aid: str) -> list[dict]:
        """(caller holds the lock) See career_rows."""
        held = self._conn.execute(
            "SELECT artist_id, release_id, title, artist_name, art, date, listed, rtype, tracks, quality"
            " FROM releases WHERE artist_id = ? AND COALESCE(NULLIF(listed, ''), date) != '' AND (? = 1 OR rtype != 'video')"
            " ORDER BY COALESCE(NULLIF(listed, ''), date) DESC, title COLLATE NOCASE, release_id",
            (aid, 0),
        ).fetchall()
        return self._career([self._held_row(h) for h in held])[1]

    def followed_ids(self) -> set[str]:
        with self._lock:
            rows = self._conn.execute("SELECT artist_id FROM followed").fetchall()
        return {r[0] for r in rows}

    def followed_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM followed").fetchone()
        return int(row[0] or 0)

    def touch(self, artist_id: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE followed SET touched = 1 WHERE artist_id = ?", (str(artist_id),))
            self._conn.commit()

    # ----- the TIDAL favorites mirror --------------------------------------

    def sync_tidal_favorites(self, favs: Iterable[dict], *, partial: bool = False) -> tuple[int, int]:
        """Reconcile the follow list with the account's favorite artists.

        Every favorite ``{id, name, art}`` not followed yet is followed as
        ``origin = tidal`` (untouched), unless the user unfollowed it here
        (``tidal_unfollowed``). Every favorite that is followed is marked
        ``tidal_fav``. With a FULL walk (``partial=False``) a mirrored row the
        user never touched that is no longer a favorite is dropped, and a
        suppression whose artist left the favorites is forgotten (favoriting
        again later mirrors again). A partial list (one page of the My Tidal
        artists category) only ever adds and marks. Returns ``(added, removed)``.
        """
        now = int(time.time())
        rows = [(str(f.get("id") or ""), f.get("name") or "", f.get("art") or "") for f in favs]
        rows = [r for r in rows if r[0]]
        ids = {r[0] for r in rows}
        added = removed = 0
        with self._lock:
            blocked = {r[0] for r in self._conn.execute("SELECT artist_id FROM tidal_unfollowed")}
            for aid, name, art in rows:
                if aid in blocked:
                    continue
                cur = self._conn.execute(
                    """INSERT OR IGNORE INTO followed (artist_id, name, art, origin, touched, tidal_fav, added_at)
                       VALUES (?, ?, ?, 'tidal', 0, 1, ?)""",
                    (aid, name, art, now),
                )
                added += int(cur.rowcount or 0)
                self._conn.execute(
                    """UPDATE followed SET tidal_fav = 1,
                           name = CASE WHEN name = '' THEN ? ELSE name END,
                           art = CASE WHEN art = '' THEN ? ELSE art END
                       WHERE artist_id = ?""",
                    (name, art, aid),
                )
            if not partial:
                gone = [
                    r[0]
                    for r in self._conn.execute("SELECT artist_id FROM followed WHERE tidal_fav = 1")
                    if r[0] not in ids
                ]
                for aid in gone:
                    self._conn.execute("UPDATE followed SET tidal_fav = 0 WHERE artist_id = ?", (aid,))
                    cur = self._conn.execute(
                        "DELETE FROM followed WHERE artist_id = ? AND origin = 'tidal' AND touched = 0", (aid,)
                    )
                    if cur.rowcount:
                        removed += 1
                        self._conn.execute("DELETE FROM seen WHERE artist_id = ?", (aid,))
                        self._conn.execute("DELETE FROM releases WHERE artist_id = ?", (aid,))
                        self._conn.execute("DELETE FROM mb_releases WHERE artist_id = ?", (aid,))
                for aid in blocked - ids:
                    self._conn.execute("DELETE FROM tidal_unfollowed WHERE artist_id = ?", (aid,))
            self._conn.commit()
        if added or removed:
            logger.info("TIDAL favorites mirrored: %d added, %d removed", added, removed)
        return added, removed

    def mark_tidal_fav(self, artist_id: str, value: bool) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE followed SET tidal_fav = ? WHERE artist_id = ?", (1 if value else 0, str(artist_id))
            )
            self._conn.commit()

    # ----- the sweep --------------------------------------------------------

    def next_due(self, before_ts: int) -> dict | None:
        """The followed artist whose last check is the oldest, if it is older
        than ``before_ts`` (never-checked rows, at 0, come first). A follow
        never checked goes first, then one a fresh-releases hint asked for
        (see ``hint``), whatever its last check."""
        with self._lock:
            row = self._conn.execute(
                "SELECT artist_id, name, art, origin, touched, tidal_fav, added_at, last_checked, seeded, mbid, mb_checked"
                " FROM followed WHERE last_checked < ? OR hinted > 0"
                " ORDER BY last_checked = 0 DESC, hinted > 0 DESC, last_checked ASC, artist_id LIMIT 1",
                (int(before_ts),),
            ).fetchone()
        return self._follow_row(row) if row else None

    def hint(self, artist_ids: Iterable[str]) -> int:
        """Ask for these follows to be checked ahead of the round; how many
        were not already waiting. An id no longer followed is skipped."""
        now = int(time.time())
        added = 0
        with self._lock:
            for aid in artist_ids:
                cur = self._conn.execute(
                    "UPDATE followed SET hinted = ? WHERE artist_id = ? AND hinted = 0", (now, str(aid))
                )
                added += max(0, cur.rowcount)
            self._conn.commit()
        return added

    def seen_pairs(self, release_ids: Iterable[str]) -> set[tuple[str, str]]:
        """The (artist_id, release_id) pairs among ``release_ids`` that some
        follow's baseline already holds."""
        ids = sorted({str(r) for r in release_ids if r})
        out: set[tuple[str, str]] = set()
        with self._lock:
            for i in range(0, len(ids), 500):
                chunk = ids[i : i + 500]
                marks = ",".join("?" * len(chunk))
                q = f"SELECT artist_id, release_id FROM seen WHERE release_id IN ({marks})"  # noqa: S608 (placeholders)
                out.update((str(a), str(r)) for a, r in self._conn.execute(q, chunk))
        return out

    def clear_hints(self) -> int:
        """Drop every waiting hint (the hint setting went off); how many."""
        with self._lock:
            n = self._conn.execute("UPDATE followed SET hinted = 0 WHERE hinted > 0").rowcount
            self._conn.commit()
        return int(n or 0)

    def hinted_count(self) -> int:
        """How many follows a hint is waiting on."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM followed WHERE hinted > 0").fetchone()
        return int(row[0] or 0)

    def never_checked_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM followed WHERE last_checked = 0").fetchone()
        return int(row[0] or 0)

    def due_count(self, before_ts: int) -> int:
        """How many follows were last checked before ``before_ts``."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM followed WHERE last_checked < ?", (int(before_ts),)
            ).fetchone()
        return int(row[0] or 0)

    def request_rescan(self, at: int) -> None:
        """Check now: every follow last checked before ``at`` is due again,
        its music videos with it (see rescan_at)."""
        self.meta_set("rescan_at", str(int(at)))
        logger.info("check of every follow asked for")

    def rescan_at(self) -> int:
        """When Check now was last asked for (epoch seconds), 0 for never. A
        stamp later than now (the clock was set back after it) reads as
        none: each check stamps the clock's own time, which would stay short
        of it and keep every follow due until the clock caught up."""
        try:
            at = int(self.meta_get("rescan_at", "0") or 0)
        except ValueError:
            return 0
        return 0 if at > int(time.time()) else at

    def video_due(self, artist_id: str, now: int, rescan_at: int = 0) -> bool:
        """Whether a follow's music videos are read on this visit: never read
        yet, read before a Check now, or read longer ago than its pace (a day
        while the artist has a video out within VIDEO_ACTIVE_DAYS, a week
        otherwise: most followed artists put out a video rarely, and a daily
        read for each would spend TIDAL's patience for nothing)."""
        aid = str(artist_id)
        since = time.strftime("%Y-%m-%d", time.localtime(int(now) - VIDEO_ACTIVE_DAYS * 86400))
        with self._lock:
            row = self._conn.execute(
                "SELECT video_checked, EXISTS(SELECT 1 FROM releases WHERE releases.artist_id = followed.artist_id"
                " AND rtype = ? AND date >= ?) FROM followed WHERE artist_id = ?",
                (VIDEO_RTYPE, since, aid),
            ).fetchone()
        if row is None:
            return False
        checked = int(row[0] or 0)
        if checked <= 0 or checked < int(rescan_at or 0):
            return True
        return int(now) - checked >= (VIDEO_AGAIN_S if row[1] else VIDEO_QUIET_AGAIN_S)

    def _grow_video_baseline(self, aid: str, rows: list[dict], *, seeded: bool, complete: bool) -> set[str]:
        """(caller holds the lock) The video side of _grow_baseline: the first
        answer (or the second empty one) is the silent baseline, after that
        the ids genuinely new."""
        if not seeded:
            if rows:
                self._conn.executemany(
                    "INSERT OR IGNORE INTO seen (artist_id, release_id) VALUES (?, ?)", [(aid, r["id"]) for r in rows]
                )
                if complete:
                    self._conn.execute("UPDATE followed SET video_seeded = 1 WHERE artist_id = ?", (aid,))
            elif complete:
                self._conn.execute("UPDATE followed SET video_empty = video_empty + 1 WHERE artist_id = ?", (aid,))
                self._conn.execute(
                    "UPDATE followed SET video_seeded = 1 WHERE artist_id = ? AND video_empty >= 2", (aid,)
                )
            return set()
        seen = {
            r[0]
            for r in self._conn.execute(
                "SELECT release_id FROM seen WHERE artist_id = ? AND release_id LIKE ?", (aid, VIDEO_PREFIX + "%")
            )
        }
        fresh_rows, absorbed = split_fresh(rows, seen)
        self._conn.executemany(
            "INSERT OR IGNORE INTO seen (artist_id, release_id) VALUES (?, ?)", [(aid, gid) for gid in sorted(absorbed)]
        )
        return {str(r["id"]) for r in fresh_rows}

    def _prune_videos(self, aid: str, held: list[str]) -> None:
        """(caller holds the lock) Drop this artist's videos a whole answer no
        longer lists."""
        marks = ", ".join("?" for _ in held) or "''"
        sql = "DELETE FROM releases WHERE artist_id = ? AND rtype = ?"
        sql = sql + " AND release_id NOT IN (" + marks + ")"
        self._conn.execute(sql, (aid, VIDEO_RTYPE, *held))

    def reset_video_seeding(self) -> None:
        """Music videos were hidden and show again: each follow's next video
        read is taken in silently, so what came out meanwhile is not
        announced (or downloaded) in a burst."""
        with self._lock:
            self._conn.execute("UPDATE followed SET video_seeded = 0, video_empty = 0")
            self._conn.commit()

    def record_videos(
        self, artist_id: str, videos: list[dict], *, complete: bool = True, whole: bool = False, keep_from: str = ""
    ) -> list[dict]:
        """Record a followed artist's music videos (bare TIDAL ids, as the
        sweep read them) and return the new ones, each with its bare id and
        ``kind: "video"``. The same rules as record_check, kept apart so a
        visit that skips the videos (they are read about once a day) never
        prunes them, and so a follow seeded before videos were read takes
        its first video answer in silently instead of announcing a back
        catalog. ``whole`` says the answer was the whole list (it came back
        short of the limit): only then is a video it no longer lists dropped,
        since the order TIDAL lists videos in is not relied on. A no-op for
        an artist no longer followed."""
        aid = str(artist_id)
        now = int(time.time())
        rows = [
            {**v, "id": video_rid(v["id"]), "rtype": VIDEO_RTYPE, "sibling_ids": [], "atmos_ids": []}
            for v in videos
            if v.get("id") is not None and str(v.get("id"))
        ]
        fresh: list[dict] = []
        with self._lock:
            row = self._conn.execute("SELECT video_seeded FROM followed WHERE artist_id = ?", (aid,)).fetchone()
            if row is None:
                return []
            fresh_ids = self._grow_video_baseline(aid, rows, seeded=bool(int(row[0] or 0)), complete=complete)
            for r in rows:
                rid = r["id"]
                day = _date_key(r)
                is_fresh, inherit = self._fresh_verdict(aid, rid, r, rid in fresh_ids, day, keep_from)
                if is_fresh:
                    fresh.append({**r, "id": bare_id(rid), "kind": "video", "fresh": True, "detected_at": now})
                if day:
                    self._upsert_release(aid, rid, r, is_fresh=is_fresh, now=now, inherit=inherit)
            if complete and whole:
                self._prune_videos(aid, [r["id"] for r in rows])
            self._conn.execute("UPDATE followed SET video_checked = ? WHERE artist_id = ?", (now, aid))
            self._conn.commit()
        if fresh:
            logger.info("sweep step: %d new music video(s) detected", len(fresh))
        return fresh

    def guest_due(self, artist_id: str, now: int, rescan_at: int = 0) -> bool:
        """Whether a follow's featured releases are read on this visit: never
        read yet, read before a Check now, or read GUEST_AGAIN_S ago."""
        with self._lock:
            row = self._conn.execute(
                "SELECT guest_checked FROM followed WHERE artist_id = ?", (str(artist_id),)
            ).fetchone()
        if row is None:
            return False
        checked = int(row[0] or 0)
        if checked <= 0 or checked < int(rescan_at or 0):
            return True
        return int(now) - checked >= GUEST_AGAIN_S

    def reset_guest_seeding(self) -> None:
        """The featured auto-download was switched on: each follow's next
        featured read is taken in silently, so what came out while it was
        off is never downloaded in a burst."""
        with self._lock:
            self._conn.execute("UPDATE followed SET guest_seeded = 0, guest_empty = 0")
            self._conn.commit()

    def record_guests(self, artist_id: str, releases: list[dict], *, keep_from: str = "") -> list[dict]:
        """Record the releases a follow is featured on (as the sweep read
        them, newest first) and return the new ones, each with its bare id
        and ``guest_of`` the follow. The first answer (or the second empty
        one) is the silent baseline, as for the artist's own releases; one
        new to the baseline but dated before ``keep_from`` is absorbed
        silently. Nothing enters the feed. A no-op for an artist no longer
        followed."""
        aid = str(artist_id)
        rows = [
            {
                **r,
                "id": GUEST_PREFIX + str(r["id"]),
                "sibling_ids": [GUEST_PREFIX + str(i) for i in r.get("sibling_ids") or [] if i],
            }
            for r in releases
            if r.get("id") is not None and str(r.get("id"))
        ]
        fresh: list[dict] = []
        with self._lock:
            row = self._conn.execute("SELECT guest_seeded FROM followed WHERE artist_id = ?", (aid,)).fetchone()
            if row is None:
                return []
            if not int(row[0] or 0):
                if rows:
                    self._conn.executemany(
                        "INSERT OR IGNORE INTO seen (artist_id, release_id) VALUES (?, ?)",
                        [(aid, rid) for rid in sorted(all_group_ids(rows))],
                    )
                    self._conn.execute("UPDATE followed SET guest_seeded = 1 WHERE artist_id = ?", (aid,))
                else:
                    self._conn.execute("UPDATE followed SET guest_empty = guest_empty + 1 WHERE artist_id = ?", (aid,))
                    self._conn.execute(
                        "UPDATE followed SET guest_seeded = 1 WHERE artist_id = ? AND guest_empty >= 2", (aid,)
                    )
            else:
                seen = {
                    r[0]
                    for r in self._conn.execute(
                        "SELECT release_id FROM seen WHERE artist_id = ? AND release_id LIKE ?",
                        (aid, GUEST_PREFIX + "%"),
                    )
                }
                fresh_rows, absorbed = split_fresh(rows, seen)
                self._conn.executemany(
                    "INSERT OR IGNORE INTO seen (artist_id, release_id) VALUES (?, ?)",
                    [(aid, gid) for gid in sorted(absorbed)],
                )
                for r in fresh_rows:
                    day = _date_key(r)
                    if keep_from and day and day < str(keep_from)[:10]:
                        continue
                    fresh.append({**r, "id": str(r["id"])[len(GUEST_PREFIX) :], "guest_of": aid})
            self._conn.execute("UPDATE followed SET guest_checked = ? WHERE artist_id = ?", (int(time.time()), aid))
            self._conn.commit()
        if fresh:
            logger.info("sweep step: %d new featured release(s) detected", len(fresh))
        return fresh

    def release_kind(self, release_id: str) -> str:
        """The kind (album, ep, single) a release is on record as, as one of
        a follow's own releases; empty when no follow lists it as its own."""
        with self._lock:
            row = self._conn.execute(
                "SELECT rtype FROM releases WHERE release_id = ? ORDER BY updated_at DESC LIMIT 1", (str(release_id),)
            ).fetchone()
        return str(row[0] or "") if row else ""

    def record_check(
        self,
        artist_id: str,
        releases: list[dict],
        *,
        complete: bool = True,
        keep_from: str = "",
        prune_from: str | None = None,
        full: dict | None = None,
        joined: list[dict] | None = None,
    ) -> list[dict]:
        """Record what a sweep step fetched for a followed artist and return the
        genuinely new releases. A no-op (``[]``) for an artist no longer
        followed, so a step racing an unfollow never resurrects it.

        Unseeded follow: a non-empty catalog is absorbed silently as the
        baseline (``seeded`` only once the fetch was ``complete``), and its rows
        enter the feed already acknowledged. A second complete empty answer
        seeds an empty baseline, so a debut is announced. Seeded follow: the delta is judged
        per edition group (``sibling_ids``) through ``split_fresh``; fresh rows
        enter the feed unacknowledged. Rows already present keep their
        acknowledged and download state. A release without a date never
        enters the feed (it cannot be placed), but still joins the baseline.

        Pruning, so the feed mirrors the catalog: after a complete fetch a
        row no longer listed is dropped when its day falls inside the span
        its own section's fetch covered (albums and EPs/singles are fetched
        apart, each newest first, so each covers its own span), and every row
        older than ``prune_from`` (``YYYY-MM-DD``, default ``keep_from``) goes.
        The feed window itself is applied when the feed is read (``feed``), so
        a wider window shows what the store already holds at once. ``full``
        says per section (True: albums) whether its fetch came back full:
        then rows sharing the span's oldest day may lie just past the cut
        and are kept; a section listed whole prunes that day too (unknown:
        kept).

        Announced once per release, not per artist: a release another followed
        artist's step already announced (a collaboration listed under both)
        is not fresh again; its new row takes the first one's acknowledged and
        download state. A release new to the baseline but dated before
        ``keep_from`` (a back catalog arriving on TIDAL, an older album
        rotating into the newest fetched) is absorbed silently: the feed
        could never show it, so it is never announced or downloaded either.

        ``joined``, when given, collects the Dolby Atmos editions listed after
        their release was already on record (see ``_joined_editions``).
        """
        aid = str(artist_id)
        now = int(time.time())
        fresh: list[dict] = []
        with self._lock:
            row = self._conn.execute("SELECT seeded FROM followed WHERE artist_id = ?", (aid,)).fetchone()
            if row is None:
                return []
            fresh_ids = self._grow_baseline(
                aid, releases, seeded=bool(int(row[0] or 0)), complete=complete, joined=joined
            )
            fetched_ids: set[str] = set()
            fetched_days: dict[bool, list[str]] = {True: [], False: []}
            for r in releases:
                if r.get("id") is None:
                    continue
                rid = str(r.get("id"))
                fetched_ids.add(rid)
                # A folded Atmos twin was listed too: a row stored for it
                # before the fold is not withdrawn.
                fetched_ids.update(str(t) for t in r.get("atmos_ids") or [] if t is not None)
                day = _date_key(r)
                is_fresh, inherit = self._fresh_verdict(aid, rid, r, rid in fresh_ids, day, keep_from)
                if is_fresh:
                    fresh.append({**r, "id": rid, "fresh": True, "detected_at": now})
                if not day:
                    continue
                fetched_days[_is_album(r)].append(_order_day(r))
                self._upsert_release(aid, rid, r, is_fresh=is_fresh, now=now, inherit=inherit)
            self._prune_after_check(
                aid,
                fetched_ids,
                fetched_days if complete else {},
                keep_from if prune_from is None else prune_from,
                full=full,
            )
            self._conn.execute(
                "UPDATE followed SET last_checked = ?, fail_count = 0, hinted = 0 WHERE artist_id = ?", (now, aid)
            )
            self._conn.commit()
        if fresh:
            logger.info("sweep step: %d new release(s) detected", len(fresh))
        return fresh

    def _grow_baseline(
        self, aid: str, releases: list[dict], *, seeded: bool, complete: bool, joined: list[dict] | None = None
    ) -> set[str]:
        """(caller holds the lock) Absorb the fetch into ``seen`` and return the
        ids that were genuinely new: none for an unseeded follow, whose first
        non-empty catalog becomes the baseline (seeded only once a fetch was
        complete), the per-edition-group delta for a seeded one."""
        if not seeded:
            if releases:
                self._conn.executemany(
                    "INSERT OR IGNORE INTO seen (artist_id, release_id) VALUES (?, ?)",
                    [(aid, rid) for rid in sorted(all_group_ids(releases))],
                )
                if complete:
                    self._conn.execute("UPDATE followed SET seeded = 1 WHERE artist_id = ?", (aid,))
            elif complete:
                self._seed_if_empty_twice(aid)
            return set()
        seen = {r[0] for r in self._conn.execute("SELECT release_id FROM seen WHERE artist_id = ?", (aid,))}
        if joined is not None:
            joined.extend(self._joined_editions(releases, seen))
        fresh_rows, absorbed = split_fresh(releases, seen)
        self._conn.executemany(
            "INSERT OR IGNORE INTO seen (artist_id, release_id) VALUES (?, ?)",
            [(aid, gid) for gid in sorted(absorbed)],
        )
        return {str(r.get("id")) for r in fresh_rows}

    def _joined_editions(self, releases: list[dict], seen: set[str]) -> list[dict]:
        """(caller holds the lock) The Dolby Atmos editions TIDAL listed after
        their release was already on record: a twin folded into a stereo row
        the baseline knows (the twin came days later), or a stereo row whose
        twin came first. Neither is fresh (the release was announced), so
        each carries the download state of the editions on record, for the
        auto-download to follow: ``{id, atmos, state, rtype, day, artist_id}``.
        An edition reported once is on record (``joined_reported``) and never
        reported again, so a collaboration listed under two follows is
        handled by whichever step runs first. Only a real report counts:
        another follow merely holding the edition in ``seen`` says nothing,
        since seeding a new follow writes its whole catalog there silently."""
        out: list[dict] = []
        for r in releases:
            if r.get("id") is None or str(r.get("quality") or "").upper() == "ATMOS":
                continue
            twins = [str(t) for t in r.get("atmos_ids") or [] if t is not None and str(t)]
            known = edition_group_ids(r) & seen
            if not twins or not known:
                continue  # no twin, or a new release (the fresh path has it)
            rid = str(r.get("id"))
            atmos_group = set(r.get("atmos_group_ids") or twins)
            late = [(rid, False)] if rid not in seen and known <= atmos_group else []
            late += [(t, True) for t in twins if t not in seen]
            late = [
                (eid, atmos)
                for eid, atmos in late
                if self._conn.execute("SELECT 1 FROM joined_reported WHERE release_id = ? LIMIT 1", (eid,)).fetchone()
                is None
            ]
            if not late:
                continue
            now = int(time.time())
            self._conn.executemany(
                "INSERT OR IGNORE INTO joined_reported (release_id, at) VALUES (?, ?)",
                [(eid, now) for eid, _ in late],
            )
            marks = ",".join("?" * len(known))
            q = f"SELECT MAX(dl_state) FROM releases WHERE release_id IN ({marks})"  # noqa: S608 (placeholders)
            state = self._conn.execute(q, sorted(known)).fetchone()[0]
            for eid, atmos in late:
                out.append(
                    {
                        "id": eid,
                        "atmos": atmos,
                        "state": int(state or 0),
                        "rtype": str(r.get("rtype") or "album"),
                        "day": str(r.get("listed") or r.get("date") or "")[:10],
                        "artist_id": str(r.get("artist_id") or ""),
                    }
                )
        return out

    def _seed_if_empty_twice(self, aid: str) -> None:
        """(caller holds the lock) A complete fetch came back empty. One empty
        answer can be a glitch, so it never becomes the baseline; the second
        one (a later check) does, as an empty baseline, so the artist's first
        release is announced instead of being absorbed silently."""
        self._conn.execute("UPDATE followed SET empty_checks = empty_checks + 1 WHERE artist_id = ?", (aid,))
        self._conn.execute("UPDATE followed SET seeded = 1 WHERE artist_id = ? AND empty_checks >= 2", (aid,))

    def _fresh_verdict(
        self, aid: str, rid: str, r: dict, new_to_baseline: bool, day: str, keep_from: str
    ) -> tuple[bool, tuple[int, int, int] | None]:
        """(caller holds the lock) Whether a release is news, and the state an
        earlier announcement of it hands this row (see record_check)."""
        if not new_to_baseline:
            return False, None
        inherit = self._announced_elsewhere(aid, rid, r)
        if inherit is not None or (keep_from and day and day < str(keep_from)[:10]):
            return False, inherit
        return True, None

    def _announced_elsewhere(self, aid: str, rid: str, r: dict) -> tuple[int, int, int] | None:
        """(caller holds the lock) ``(detected_at, acknowledged, dl_state)`` of
        another followed artist's row that announced this release (or an
        edition of it), else None. A row absorbed into a baseline was never
        announced, so it does not count."""
        ids = sorted({rid, *(str(i) for i in r.get("sibling_ids") or [] if i)})
        marks = ", ".join("?" for _ in ids)
        sql = "SELECT detected_at, MIN(acknowledged), MAX(dl_state) FROM releases"
        sql = sql + " WHERE artist_id != ? AND detected_at > 0 AND release_id IN (" + marks + ")"
        row = self._conn.execute(sql, (aid, *ids)).fetchone()
        if row is None or row[0] is None:
            return None
        return int(row[0]), int(row[1] or 0), int(row[2] or 0)

    def _upsert_release(
        self, aid: str, rid: str, r: dict, *, is_fresh: bool, now: int, inherit: tuple[int, int, int] | None = None
    ) -> None:
        """(caller holds the lock) One feed row from a release dict. A new row
        is fresh (detected now, unacknowledged), absorbed (acknowledged), or
        ``inherit``s another artist's announcement of the same release."""
        if inherit is not None:
            detected_at, acknowledged, dl_state = inherit
        else:
            detected_at, acknowledged, dl_state = (now, 0, 0) if is_fresh else (0, 1, 0)
        self._conn.execute(
            _UPSERT_RELEASE,
            (
                aid,
                rid,
                r.get("title") or "",
                r.get("artist_name") or r.get("artist") or "",
                str(r.get("artist_id") or ""),
                json.dumps(r.get("artists") or [], ensure_ascii=False),
                r.get("art") or "",
                str(r.get("date") or "")[:10],
                str(r.get("listed") or "")[:10],
                str(r.get("year") or ""),
                str(r.get("rtype") or "album"),
                1 if r.get("explicit") else 0,
                int(r.get("tracks") or 0),
                int(r.get("audio_tracks") or 0),
                int(r.get("duration_sec") or 0),
                r.get("quality") or "",
                1 if r.get("atmos") else 0,
                json.dumps([str(s) for s in r.get("sibling_ids") or []]),
                detected_at,
                acknowledged,
                dl_state,
                now,
            ),
        )

    def _prune_after_check(
        self, aid: str, fetched_ids: set[str], days_by_section: dict, floor: str, *, full: dict | None = None
    ) -> None:
        """(caller holds the lock) A complete fetch's withdrawn rows, section
        by section (pass no days for a partial one), then everything older
        than ``floor``."""
        for albums, days in days_by_section.items():
            if days:
                cut = full is None or bool(full.get(albums, True))
                self._prune_withdrawn(aid, fetched_ids, min(days), albums=albums, cut=cut)
        if floor:
            self._conn.execute(
                "DELETE FROM releases WHERE artist_id = ? AND COALESCE(NULLIF(listed, ''), date) < ?",
                (aid, str(floor)[:10]),
            )

    def _prune_withdrawn(
        self, aid: str, fetched_ids: set[str], span_from: str, *, albums: bool, cut: bool = True
    ) -> None:
        """(caller holds the lock) Drop this artist's rows of one section
        (albums, or EPs and singles) dated inside the span that section's
        complete fetch covered and that the fetch no longer listed."""
        marks = ", ".join("?" for _ in fetched_ids)
        # On the release date, TIDAL's own order: a reissue the deep read put
        # on record (released 1985, listed 2023) sits past the head however
        # new its listing. When the fetch was cut at its limit, past the
        # oldest day only: rows sharing that day may lie just past the cut.
        sql = "DELETE FROM releases WHERE artist_id = ? AND COALESCE(NULLIF(date, ''), listed) "
        sql = sql + ("> ?" if cut else ">= ?")
        # Music videos are a section of their own (see record_videos).
        sql = sql + (" AND rtype = 'album'" if albums else f" AND rtype NOT IN ('album', '{VIDEO_RTYPE}')")
        sql = sql + " AND release_id NOT IN (" + marks + ")"
        self._conn.execute(sql, (aid, span_from, *sorted(fetched_ids)))

    def deep_read_at(self, artist_id: str) -> int:
        """When this follow's catalog beyond the sweep's head was last read
        to its end (epoch seconds), 0 for never."""
        with self._lock:
            row = self._conn.execute("SELECT deep_read FROM followed WHERE artist_id = ?", (str(artist_id),)).fetchone()
        return int(row[0] or 0) if row else 0

    def record_deep(self, artist_id: str, releases: list[dict], *, complete: bool = True) -> int:
        """Record the older part of a followed artist's catalog, read page by
        page past the sweep's head (the timeline's first release). Nothing
        here is news: every row joins the baseline and goes on record
        acknowledged with no download planned, so a deep read never
        announces, prunes or downloads, and a row the sweep already holds is
        left exactly as it is (so is one that is another edition of it: the
        read starts from the top, so its groups span the head's rows).
        ``complete`` (every section read to its end)
        stamps the follow, so the read is not repeated until it ages out.
        Returns how many rows are new on record; 0 for an artist no longer
        followed."""
        aid = str(artist_id)
        now = int(time.time())
        added = 0
        with self._lock:
            if self._conn.execute("SELECT 1 FROM followed WHERE artist_id = ?", (aid,)).fetchone() is None:
                return 0
            self._conn.executemany(
                "INSERT OR IGNORE INTO seen (artist_id, release_id) VALUES (?, ?)",
                [(aid, rid) for rid in sorted(all_group_ids(releases))],
            )
            held = {r[0] for r in self._conn.execute("SELECT release_id FROM releases WHERE artist_id = ?", (aid,))}
            for r in releases:
                if r.get("id") is None or not _date_key(r):
                    continue
                rid = str(r.get("id"))
                if rid in held or any(str(s) in held for s in r.get("sibling_ids") or []):
                    continue
                self._upsert_release(aid, rid, r, is_fresh=False, now=now)
                held.add(rid)
                added += 1
            if complete:
                self._conn.execute("UPDATE followed SET deep_read = ? WHERE artist_id = ?", (now, aid))
            self._conn.commit()
        if added:
            logger.info("deep read: %d older release(s) on record", added)
        return added

    def deep_touch(self, artist_id: str, at: int) -> None:
        """Stamp the follow's deep read at ``at`` without a read to its end
        (a failed page): it goes again once that stamp ages out."""
        with self._lock:
            self._conn.execute("UPDATE followed SET deep_read = ? WHERE artist_id = ?", (int(at), str(artist_id)))
            self._conn.commit()

    # ----- the MusicBrainz check ---------------------------------------------

    def mb_next_due(self, before_ts: int) -> dict | None:
        """The follow whose MusicBrainz check is the oldest, if older than
        ``before_ts`` (never checked, at 0, first); carries ``mbid``."""
        with self._lock:
            row = self._conn.execute(
                "SELECT artist_id, name, art, origin, touched, tidal_fav, added_at, last_checked, seeded, mbid, mb_checked"
                " FROM followed WHERE mb_checked < ? ORDER BY mb_checked ASC, artist_id LIMIT 1",
                (int(before_ts),),
            ).fetchone()
        return self._follow_row(row) if row else None

    def mb_touch(self, artist_id: str, at: int) -> None:
        """Stamp a follow's MusicBrainz check at ``at`` without changing what
        is on record: a check that could not be made is tried again when
        the stamp ages out, not on the very next beat."""
        with self._lock:
            self._conn.execute("UPDATE followed SET mb_checked = ? WHERE artist_id = ?", (int(at), str(artist_id)))
            self._conn.commit()

    def record_mb(self, artist_id: str, mbid: str, groups: list[dict]) -> bool:
        """Record what the MusicBrainz check found for a followed artist:
        the artist it is (``mbid``, '' when no candidate was sure) and its
        release groups ``{rgid, title, date, rtype, release_id}``, each
        with the TIDAL row it was matched to or ''. The artist's earlier
        rows are replaced whole and the check is stamped. Returns whether
        the marks the timeline draws for the artist changed (judged as
        ``followed_artists`` draws them, against the career as it stands);
        False, and nothing recorded, for an artist no longer followed."""
        aid = str(artist_id)
        now = int(time.time())
        with self._lock:
            if self._conn.execute("SELECT 1 FROM followed WHERE artist_id = ?", (aid,)).fetchone() is None:
                return False
            career = self._career_of(aid)
            before = {m["rgid"] for m in _missing_marks(self._mb_rows_of(aid), career)}
            self._conn.execute("DELETE FROM mb_releases WHERE artist_id = ?", (aid,))
            kept = []
            for g in groups:
                rgid = str(g.get("rgid") or "")
                if not rgid:
                    continue
                row = {
                    "rgid": rgid,
                    "title": g.get("title") or "",
                    "date": str(g.get("date") or "")[:10],
                    "rtype": str(g.get("rtype") or "album"),
                    "release_id": str(g.get("release_id") or ""),
                }
                kept.append(row)
                self._conn.execute(
                    "INSERT OR REPLACE INTO mb_releases (artist_id, rgid, title, date, rtype, release_id)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (aid, rgid, row["title"], row["date"], row["rtype"], row["release_id"]),
                )
            self._conn.execute(
                "UPDATE followed SET mbid = ?, mb_checked = ? WHERE artist_id = ?", (str(mbid or ""), now, aid)
            )
            self._conn.commit()
            after = {m["rgid"] for m in _missing_marks(kept, career)}
        logger.info("MusicBrainz check: %d release group(s) on record, %d not on TIDAL", len(kept), len(after))
        return before != after

    def mb_rows(self, artist_id: str) -> list[dict]:
        """The MusicBrainz release groups on record for a follow, newest
        first, each with its matched TIDAL row ('' when none)."""
        with self._lock:
            return self._mb_rows_of(str(artist_id))

    def _mb_rows_of(self, aid: str) -> list[dict]:
        """(caller holds the lock) See mb_rows."""
        rows = self._conn.execute(
            "SELECT rgid, title, date, rtype, release_id FROM mb_releases WHERE artist_id = ?"
            " ORDER BY date DESC, title COLLATE NOCASE, rgid",
            (aid,),
        ).fetchall()
        return [
            {"rgid": r[0], "title": r[1] or "", "date": r[2] or "", "rtype": r[3], "release_id": r[4] or ""}
            for r in rows
        ]

    def record_failure(self, artist_id: str) -> None:
        """A step that could not fetch anything: counted, and the artist moves
        to the back of the line rather than being retried every step."""
        with self._lock:
            self._conn.execute(
                "UPDATE followed SET fail_count = fail_count + 1, last_checked = ?, hinted = 0 WHERE artist_id = ?",
                (int(time.time()), str(artist_id)),
            )
            self._conn.commit()

    # ----- the feed ---------------------------------------------------------

    def feed(self, keep_from: str = "", *, videos: bool = True, guests: bool = True) -> list[dict]:
        """Every release dated from ``keep_from`` on (the window; all when
        empty), one row per release id (the most recently refreshed one),
        newest day first; ``fresh`` says whether any row for that release is
        still unacknowledged. A Dolby Atmos twin recorded beside its stereo
        edition (rows from before the sweep folded them) shows once, see
        ``collapse_atmos_twins``. Music videos come in among the releases
        (``kind: "video"``, the bare video id) unless ``videos`` is off, and
        featured releases (see _GUEST_CLAUSE) unless ``guests`` is off."""
        day_from = str(keep_from or "")[:10]
        with self._lock:
            # Bare-column rule: with exactly one MAX() the other columns come
            # from that row, so a collaboration fetched under two followed
            # artists shows once, as its freshest copy.
            rows = self._conn.execute(
                "SELECT artist_id, release_id, title, artist_name, primary_artist_id, artists_json, art, date,"  # noqa: S608 (fixed clause)
                " listed, year, rtype, explicit, tracks, audio_tracks, duration_sec, quality, atmos,"
                " detected_at, dl_state, MAX(updated_at) FROM releases"
                " WHERE COALESCE(NULLIF(listed, ''), date) >= ? AND (? = 1 OR rtype != 'video')"
                + _GUEST_CLAUSE
                + " GROUP BY release_id"
                " ORDER BY COALESCE(NULLIF(listed, ''), date) DESC, title COLLATE NOCASE, release_id",
                (day_from, int(videos), int(guests)),
            ).fetchall()
            fresh = {
                r[0] for r in self._conn.execute("SELECT DISTINCT release_id FROM releases WHERE acknowledged = 0")
            }
        out = []
        for r in rows:
            try:
                artists = json.loads(r[5] or "[]")
            except ValueError:
                artists = []
            out.append(
                {
                    "kind": "video" if r[10] == VIDEO_RTYPE else "album",
                    "id": bare_id(r[1]),
                    "via": r[0],
                    "title": r[2] or "",
                    "artist": r[3] or "",
                    "artist_id": r[4] or "",
                    "artists": artists,
                    "art": r[6] or "",
                    "date": r[7] or "",
                    "listed": r[8] or "",
                    "year": r[9] or "",
                    "rtype": r[10] or "album",
                    "explicit": bool(r[11]),
                    "tracks": int(r[12] or 0),
                    "audio_tracks": int(r[13] or 0),
                    "duration_sec": int(r[14] or 0),
                    "quality": r[15] or "",
                    "atmos": bool(r[16]),
                    "detected_at": int(r[17] or 0),
                    "dl_state": int(r[18] or 0),
                    "fresh": r[1] in fresh,
                }
            )
        return collapse_atmos_twins(out)

    def search(
        self,
        needle: str,
        limit: int = 400,
        *,
        videos: bool = True,
        guests: bool = True,
        kind: str = "",
        today: str = "",
    ) -> tuple[list[dict], bool]:
        """The feed's search: every stored release, past the window too,
        whose title or artist names hold every word of ``needle`` (accents
        and case folded), newest first like ``feed``. At most ``limit`` rows,
        and whether more matched. ``kind`` is the feed's filter, applied
        before the limit counts: "all" (out by ``today``), "album", "ep",
        "single", "video", "upcoming"; empty for every row.

        The folded text of every release is worked out once and kept while
        the store is unchanged, so each keystroke of a search only matches
        words (see search_forget)."""
        words = _fold(needle).split()
        if not words:
            return [], False
        today = today or time.strftime("%Y-%m-%d")
        hits: list[dict] = []
        for hay, r in self._search_index(videos, guests):
            if _kind_keeps(r, kind, today) and all(w in hay for w in words):
                hits.append(dict(r))
                if len(hits) > limit:
                    break
        return hits[:limit], len(hits) > limit

    def _feed_gen(self) -> int:
        """(under the lock) The feed's generation (see _FEED_GEN_DDL)."""
        row = self._conn.execute("SELECT n FROM feed_gen").fetchone()
        return int(row[0]) if row else 0

    def _search_index(self, videos: bool, guests: bool = True) -> list:
        """Every feed row with its folded text, kept until a write changes
        what the feed shows (see _FEED_GEN_DDL): a sweep step that only
        restamps rows keeps it."""
        with self._lock:
            mark = (self._feed_gen(), bool(videos), bool(guests))
            kept = self._search_kept
        if kept is not None and kept[0] == mark:
            return kept[1]
        index = []
        for r in self.feed("", videos=videos, guests=guests):
            names = [a.get("name", "") if isinstance(a, dict) else str(a) for a in r.get("artists") or []]
            index.append((_fold(" ".join([r.get("title", ""), r.get("artist", ""), *names])), r))
        with self._lock:
            if self._feed_gen() == mark[0]:
                self._search_kept = (mark, index)
        return index

    def search_forget(self) -> None:
        """The search box emptied: the kept search index goes."""
        with self._lock:
            self._search_kept = None

    def has_older(self, before: str, keep_from: str = "", *, videos: bool = True, guests: bool = True) -> bool:
        """Whether any release is dated before ``before`` and from
        ``keep_from`` on: the feed has an older page to load."""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM releases WHERE COALESCE(NULLIF(listed, ''), date) < ?"  # noqa: S608 (fixed clause)
                " AND COALESCE(NULLIF(listed, ''), date) >= ? AND (? = 1 OR rtype != 'video')"
                + _GUEST_CLAUSE
                + " LIMIT 1",
                (str(before or "")[:10], str(keep_from or "")[:10], int(videos), int(guests)),
            ).fetchone()
        return row is not None

    def unseen_count(self, keep_from: str = "", *, videos: bool = True, guests: bool = True) -> int:
        """Unacknowledged releases the feed shows (dated from ``keep_from``),
        counted the way ``feed`` folds them, so a folded Atmos twin is not a
        second unseen release on the badge. Reads only the unacknowledged
        rows (and, for an Atmos one, the stereo rows of its day): the badge
        is asked on the GUI thread after every sweep step."""
        day_from = str(keep_from or "")[:10]
        with self._lock:
            unseen = self._conn.execute(
                "SELECT release_id, title, artist_name, listed, date, tracks, quality, MAX(updated_at)"  # noqa: S608 (fixed clause)
                " FROM releases WHERE COALESCE(NULLIF(listed, ''), date) >= ? AND (? = 1 OR rtype != 'video')"
                + _GUEST_CLAUSE
                + " AND release_id IN (SELECT release_id FROM releases WHERE acknowledged = 0)"
                " GROUP BY release_id",
                (day_from, int(videos), int(guests)),
            ).fetchall()
            days = sorted({str(r[3] or r[4] or "")[:10] for r in unseen if str(r[6] or "").upper() == "ATMOS"} - {""})
            stereo = []
            for day in days:
                stereo += self._conn.execute(
                    "SELECT release_id, title, artist_name, listed, date, tracks, quality, MAX(updated_at)"
                    " FROM releases WHERE COALESCE(NULLIF(listed, ''), date) = ? GROUP BY release_id"
                    " ORDER BY title COLLATE NOCASE, release_id",
                    (day,),
                ).fetchall()

        def row(r) -> dict:
            return {
                "id": r[0],
                "title": r[1] or "",
                "artist_name": r[2] or "",
                "listed": r[3] or "",
                "date": r[4] or "",
                "tracks": int(r[5] or 0),
                "quality": r[6] or "",
            }

        # The stereo row a twin folds into is the first of its key in feed
        # order (same day, so title then id).
        keeper: dict = {}
        for r in stereo:
            if str(r[6] or "").upper() != "ATMOS":
                keeper.setdefault(_twin_key(row(r)), str(r[0]))
        shown = set()
        for r in unseen:
            rid = str(r[0])
            if str(r[6] or "").upper() == "ATMOS":
                rid = keeper.get(_twin_key(row(r)), rid)
            shown.add(rid)
        return len(shown)

    def acknowledge_all(self) -> list[str]:
        """Clear the badge: every unseen release is marked seen by the user.
        Returns the release ids it just acknowledged (the pane keeps their
        dots for the visit), a music video's with its ``v:`` mark: a video's
        bare id can be an album's too, and the album must not light up."""
        with self._lock:
            ids = sorted({r[0] for r in self._conn.execute("SELECT release_id FROM releases WHERE acknowledged = 0")})
            if ids:
                self._conn.execute("UPDATE releases SET acknowledged = 1 WHERE acknowledged = 0")
                self._conn.commit()
        return list(ids)

    def set_dl_state(self, release_id: str, state: int, *, video: bool = False) -> None:
        """A release's auto-download state; ``video`` for a music video's bare id."""
        rid = video_rid(release_id) if video else str(release_id)
        with self._lock:
            self._conn.execute("UPDATE releases SET dl_state = ? WHERE release_id = ?", (int(state), rid))
            self._conn.commit()

    def clear_waiting(self) -> int:
        """Drop every release's wait for its day; returns how many waited."""
        with self._lock:
            n = self._conn.execute("UPDATE releases SET dl_state = 0 WHERE dl_state = ?", (DL_WAITING,)).rowcount
            self._conn.commit()
        with self._lists_lock:
            n = int(n or 0) + len(self._atmos_waiting()) + len(self._guest_waiting())
            self.meta_set("atmos_waiting", "{}")
            self.meta_set("guest_waiting", "{}")
        return n

    # The featured releases an auto-download still owes (they have no feed
    # row to wait in): ``{release id: [day, the follow featured on it]}``.
    def _guest_waiting(self) -> dict:
        try:
            got = json.loads(self.meta_get("guest_waiting", "{}") or "{}")
        except ValueError:
            got = {}
        return got if isinstance(got, dict) else {}

    def guest_wait(self, release_id: str, day: str, artist_id: str) -> None:
        """Keep a featured release for a later step: it waits for its day."""
        with self._lists_lock:
            waiting = self._guest_waiting()
            waiting[str(release_id)] = [str(day or "")[:10], str(artist_id)]
            self.meta_set("guest_waiting", json.dumps(waiting, sort_keys=True))

    def guests_due(self, upto: str) -> list[tuple[str, str]]:
        """The waiting featured releases whose day has come, as ``(release
        id, the follow featured on it)``, taken off the list."""
        with self._lists_lock:
            waiting = self._guest_waiting()
            due = sorted(
                (k, str(v[1]))
                for k, v in waiting.items()
                if isinstance(v, list) and len(v) == 2 and str(v[0]) <= upto[:10]
            )
            if due:
                gone = {k for k, *_ in due}
                self.meta_set(
                    "guest_waiting", json.dumps({k: v for k, v in waiting.items() if k not in gone}, sort_keys=True)
                )
        return due

    # The Dolby Atmos twins an auto-download still owes (folded into their
    # stereo row, so they have no row to wait in): ``{release id: [day, the
    # stereo row's kind]}`` (a bare day from before the kind was kept).
    def _atmos_waiting(self) -> dict:
        try:
            got = json.loads(self.meta_get("atmos_waiting", "{}") or "{}")
        except ValueError:
            got = {}
        return got if isinstance(got, dict) else {}

    def atmos_wait(self, twins: dict, rtype: str = "", lead: str = "") -> None:
        """Keep Atmos twins (``{id: day}``) for a later step: each waits for
        its day, with ``rtype`` its stereo row's kind and ``lead`` its lead
        artist, so a kind unticked or featured releases hidden meanwhile
        hold the twin back as they do the row."""
        if twins:
            with self._lists_lock:
                waiting = self._atmos_waiting()
                entry = [str(rtype or ""), str(lead or "")]
                waiting.update({str(k): [str(v or "")[:10], *entry] for k, v in twins.items()})
                self.meta_set("atmos_waiting", json.dumps(waiting, sort_keys=True))

    def atmos_due(self, upto: str) -> list[tuple[str, str, str]]:
        """The waiting Atmos twins whose day has come, as ``(id, kind,
        lead)`` (kind and lead "" when they were not kept), taken off the
        list."""
        with self._lists_lock:
            waiting = self._atmos_waiting()
            due = []
            for k, v in waiting.items():
                row = [*v, "", ""][:3] if isinstance(v, list) and v else [v, "", ""]
                day, kind, lead = row
                if str(day) <= str(upto)[:10]:
                    due.append((k, str(kind or ""), str(lead or "")))
            due.sort()
            if due:
                gone = {k for k, *_ in due}
                self.meta_set(
                    "atmos_waiting", json.dumps({k: v for k, v in waiting.items() if k not in gone}, sort_keys=True)
                )
        return due

    def pending_auto_downloads(self, upto: str) -> list[dict]:
        """Releases waiting for their day (``DL_WAITING``) whose day has come,
        one per release id; a music video's with its bare id and ``kind:
        "video"``."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT release_id, title, artist_name, rtype, MAX(updated_at), primary_artist_id FROM releases"
                " WHERE dl_state = ? AND COALESCE(NULLIF(listed, ''), date) <= ? GROUP BY release_id",
                (DL_WAITING, str(upto)[:10]),
            ).fetchall()
        return [
            {
                "id": bare_id(r[0]),
                "kind": "video" if r[3] == VIDEO_RTYPE else "album",
                "rtype": r[3] or "album",
                "title": r[1] or "",
                "artist": r[2] or "",
                # A release's lead artist, as on a sweep's fresh rows (see
                # _GUEST_CLAUSE): what the Featured releases setting reads.
                **({} if r[3] == VIDEO_RTYPE else {"artist_id": r[5] or ""}),
            }
            for r in rows
        ]

    # ----- the import tray --------------------------------------------------

    def tray_record(self, rows: Iterable[dict]) -> None:
        """Remember import rows the user still has to look at: ``{norm, name,
        reason, candidates}``. A row already in the tray keeps its place."""
        now = int(time.time())
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO import_tray (norm, name, reason, candidates_json, ts) VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        str(r.get("norm") or r.get("name") or ""),
                        r.get("name") or "",
                        r.get("reason") or "",
                        json.dumps(r.get("candidates") or [], ensure_ascii=False),
                        now,
                    )
                    for r in rows
                    if (r.get("norm") or r.get("name"))
                ],
            )
            self._conn.commit()

    def tray_rows(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT norm, name, reason, candidates_json, ts FROM import_tray ORDER BY name COLLATE NOCASE"
            ).fetchall()
        out = []
        for r in rows:
            try:
                cands = json.loads(r[3] or "[]")
            except ValueError:
                cands = []
            out.append(
                {"norm": r[0], "name": r[1] or "", "reason": r[2] or "", "candidates": cands, "ts": int(r[4] or 0)}
            )
        return out

    def tray_remove(self, norm: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM import_tray WHERE norm = ?", (str(norm),))
            self._conn.commit()

    def tray_dismiss(self, norm: str) -> None:
        """Drop a tray row for good: later imports skip the name."""
        with self._lock:
            self._conn.execute("DELETE FROM import_tray WHERE norm = ?", (str(norm),))
            self._conn.execute(
                "INSERT OR REPLACE INTO import_dismissed (norm, ts) VALUES (?, ?)", (str(norm), int(time.time()))
            )
            self._conn.commit()

    def tray_dismissed_norms(self) -> set[str]:
        with self._lock:
            rows = self._conn.execute("SELECT norm FROM import_dismissed").fetchall()
        return {r[0] for r in rows}

    def tray_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM import_tray").fetchone()
        return int(row[0] or 0)

    # ----- bookkeeping ------------------------------------------------------

    def meta_get(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (str(key),)).fetchone()
        return str(row[0]) if row and row[0] is not None else default

    def meta_set(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (str(key), str(value)))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
