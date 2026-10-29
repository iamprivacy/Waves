"""The one door to MusicBrainz: pacing, caching and what every answer taught.

Two features ask MusicBrainz, the library arbiter (``waves.mb_arbiter``,
"Ask MusicBrainz when unsure") and the Releases check (``waves.musicbrainz``).
They can run at the same moment, a page's "?" pill while the sweep reads a
discography, so they share this service rather than keep a pacer each:

* ``Pacer``: one request a second for the whole process, on the monotonic
  clock. A foreground request (a pill on screen) takes the next slot ahead
  of any background one waiting, so a long discography read never makes a
  page wait. When MusicBrainz asks for a pause (429, 503) the pacer holds
  every caller, not only the one that was told.
* ``MBService.get``: a paced GET with definite-only caching in a local
  sqlite file. A transient failure is never cached; a not-found, or a 200
  that holds no answer yet, is kept briefly; a definite answer is kept for
  as long as its caller says.
* What every answer taught: each 200 body is taken apart into artists,
  release groups and releases (with the raw JSON of each, merged across
  answers), whichever feature asked. The next question about the same
  thing reads the file first: the arbiter skips a release read whose
  length is already on record, and the Releases check weighs artists it
  already met before it searches.

The file holds service data only (names, titles, MusicBrainz ids, the
request URLs built from them): never a local path, an account id or a
token. Log lines carry outcomes and counts.

Pure standard library (urllib, json, sqlite3) over one injectable ``fetch``
returning ``(status, body)``, so tests drive it with canned answers, fake
clocks, and never touch the network.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from threading import Condition, Lock

from waves import db
from waves.artist_import import name_key
from waves.tls import tls_context

logger = logging.getLogger("waves.musicbrainz")

MB_BASE = "https://musicbrainz.org/ws/2"
# MusicBrainz allows one request per second to the public web service.
MIN_INTERVAL_SEC = 1.0
# Reads run on workers; a stalled link must not hold the pacer for long.
TIMEOUT_SEC = 15
# A 429 or 503 is MusicBrainz asking for a pause: the pacer holds every
# caller this long, longer each time the same request is refused again.
BACKOFF_SEC = (2.0, 5.0, 15.0)
# A definitive not-found, or a 200 with nothing in it yet, is a real answer
# MusicBrainz routinely changes, so a day, not forever.
NEGATIVE_TTL_SEC = 24 * 3600
_NEGATIVE_STATUSES = frozenset({400, 404, 410})
# Expired cache rows are swept on open and every this many writes.
_PRUNE_EVERY = 500
# A definite answer is still asked again after this long: MusicBrainz is
# edited every day, and an answer kept for good could never notice.
DEFINITE_TTL_SEC = 90 * 86400
# What each kind of read also asks for in the same single request: genres,
# tags, aliases, labels, links, notes. If MusicBrainz refuses one of these
# lists (HTTP 400) the read goes again without it, and that kind asks for
# no extras for the rest of the session.
EXTRAS = {
    "release": "labels+genres+tags+url-rels+annotation+aliases",
    "release-group": "genres+tags",
    "artist": "aliases+genres+tags+url-rels+annotation+ratings",
}
# Keys of an answer that move without the thing itself changing.
_VOLATILE_KEYS = frozenset({"score", "count", "offset", "created"})


def default_user_agent() -> str:
    """The descriptive User-Agent MusicBrainz requires. Must never raise:
    one construction site sits on the GUI thread."""
    try:
        from waves import version_app

        v = version_app()
    except Exception:
        v = "dev"
    return f"Waves/{v} (https://getwaves.dev)"


class Pacer:
    """One request a second. Monotonic, so a clock change can neither stall
    nor flood. ``wait(foreground=True)`` takes the next slot ahead of every
    background caller already waiting."""

    def __init__(
        self,
        min_interval: float = MIN_INTERVAL_SEC,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._min_interval = float(min_interval)
        self._sleep = sleep
        self._monotonic = monotonic
        self._cond = Condition()
        self._busy = False
        self._fg_waiting = 0
        self._waiting = 0
        self._last: float | None = None

    def wait(self, foreground: bool = False) -> None:
        with self._cond:
            self._waiting += 1
            if foreground:
                self._fg_waiting += 1
            try:
                while self._busy or (not foreground and self._fg_waiting > 0):
                    self._cond.wait()
                self._busy = True
            finally:
                self._waiting -= 1
                if foreground:
                    self._fg_waiting -= 1
        try:
            # The slot is taken under the lock. A pause asked for while this
            # caller slept (hold) moves _last, and then the caller sleeps out
            # the rest of it instead of overwriting it with its own slot.
            while True:
                with self._cond:
                    now = self._monotonic()
                    due = 0.0 if self._last is None else self._min_interval - (now - self._last)
                    if due <= 0:
                        self._last = now
                        break
                    mark = self._last
                self._sleep(due)
                with self._cond:
                    if self._last == mark:
                        self._last = self._monotonic()
                        break
        finally:
            with self._cond:
                self._busy = False
                self._cond.notify_all()

    def hold(self, seconds: float) -> None:
        """MusicBrainz asked for a pause: no caller's next slot comes
        sooner than ``seconds`` from now."""
        with self._cond:
            until = self._monotonic() + float(seconds) - self._min_interval
            self._last = until if self._last is None else max(self._last, until)

    def waiting(self) -> int:
        """Callers queued for a slot (tests and the perf sampler)."""
        with self._cond:
            return self._waiting


_SHARED = Pacer()


def shared_pacer() -> Pacer:
    """The pacer every service in the process shares by default."""
    return _SHARED


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS responses (url TEXT PRIMARY KEY, body TEXT, expires REAL)",
    """CREATE TABLE IF NOT EXISTS artists (
           mbid TEXT PRIMARY KEY, name TEXT, name_key TEXT, disambiguation TEXT,
           json TEXT, seen REAL, changed REAL)""",
    "CREATE INDEX IF NOT EXISTS artists_by_key ON artists (name_key)",
    """CREATE TABLE IF NOT EXISTS release_groups (
           rgid TEXT PRIMARY KEY, artist_mbid TEXT, title TEXT, first_date TEXT,
           primary_type TEXT, secondary TEXT, json TEXT, seen REAL, changed REAL)""",
    "CREATE INDEX IF NOT EXISTS release_groups_by_artist ON release_groups (artist_mbid)",
    """CREATE TABLE IF NOT EXISTS releases (
           mbid TEXT PRIMARY KEY, rgid TEXT, title TEXT, date TEXT,
           track_count INTEGER, length INTEGER, json TEXT, seen REAL, changed REAL, length_at REAL)""",
    """CREATE TABLE IF NOT EXISTS release_artists (
           release_mbid TEXT, artist_mbid TEXT, PRIMARY KEY (release_mbid, artist_mbid))""",
    # Which MusicBrainz artist a thing IS, as some feature proved it, so the
    # others can lean on it: scope "tidal" keys a followed TIDAL artist id
    # (the Releases check), scope "library" a library artist key (the library
    # lookup). ``how`` says by what: "titles" (shared release titles),
    # "file_tag" (the user's own files carry the id), "follow" or "library"
    # (the other scope's proof confirmed it), "none" (asked, no sure match:
    # mbid ''). ``name_key`` lets one scope find the other's by name.
    """CREATE TABLE IF NOT EXISTS artist_links (
           scope TEXT NOT NULL, key TEXT NOT NULL, name_key TEXT, mbid TEXT, how TEXT, at REAL,
           PRIMARY KEY (scope, key))""",
    "CREATE INDEX IF NOT EXISTS artist_links_by_name ON artist_links (name_key)",
)

# Per table: the key column and the columns kept beside the raw JSON.
_TABLES = {
    "artists": ("mbid", ("name", "name_key", "disambiguation")),
    "release_groups": ("rgid", ("artist_mbid", "title", "first_date", "primary_type", "secondary")),
    "releases": ("mbid", ("rgid", "title", "date", "track_count", "length", "length_at")),
}


class MBService:
    """Paced, cached access to MusicBrainz for every feature in the process.
    ``db_path`` None keeps nothing (no cache, no facts): every get asks.
    Thread-safe: one sqlite connection under a lock, the pacer serializes
    the requests."""

    def __init__(
        self,
        db_path: str | None = None,
        *,
        fetch: Callable[[str], tuple[int, str]] | None = None,
        pacer: Pacer | None = None,
        user_agent: str | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._path = None if db_path is None else str(db_path)
        self._ua = user_agent or default_user_agent()
        self._fetch = fetch or self._http_fetch
        self.pacer = pacer or _SHARED
        self._now = now
        self._db_lock = Lock()
        self._conn: sqlite3.Connection | None = None
        self._writes = 0
        # Kinds whose extras MusicBrainz refused this session (see EXTRAS).
        self._refused: set[str] = set()
        # close() is final: a straggler still holding this service must not
        # send a request or lazily reopen the file (a settings wipe deletes
        # it, and a reopen would resurrect it).
        self._closed = False

    @property
    def closed(self) -> bool:
        with self._db_lock:
            return self._closed

    # ---- the request ------------------------------------------------------

    def extras(self, kind: str) -> str:
        """The extra includes a read of ``kind`` asks for, '' once refused."""
        return "" if kind in self._refused else EXTRAS.get(kind, "")

    def get(
        self,
        url: str,
        *,
        ttl: float | None = None,
        unanswered: Callable[[dict], bool] | None = None,
        foreground: bool = False,
        not_found_answers: bool = False,
        plain_url: str | None = None,
        extras_kind: str = "",
        alive: Callable[[], bool] | None = None,
    ) -> dict | None:
        """One paced GET, parsed, or None when MusicBrainz could not be
        asked (never cached; the caller tries again later).

        ``ttl`` is how long a definite answer is kept (None: for good).
        ``unanswered`` marks a 200 body that holds no answer yet (an empty
        search): it is kept for NEGATIVE_TTL_SEC only. With
        ``not_found_answers`` a 400, 404 or 410 is an answer too, ``{}``,
        kept as briefly; without it, it is a None like any other failure.

        A background request that MusicBrainz asks to pause waits the pause
        out and goes again a few times; a foreground one gives up at once
        (its page asks again on the next view), but the pause still holds
        every other caller.

        ``plain_url`` is the same read without ``extras_kind``'s extras: a
        400 to ``url`` is MusicBrainz refusing the extras, so the plain read
        goes instead and the kind asks for none again this session.

        ``alive`` is the caller's own switch (its setting): once it reads
        false no request leaves for that caller, even one already waiting
        for its slot. What is on record still answers."""
        if self.closed:
            return None
        cached = self._cache_read(url)
        if cached is not None:
            return cached
        for attempt in range(len(BACKOFF_SEC) + 1):
            self.pacer.wait(foreground)
            # A close() that landed during the wait wins: no request leaves
            # after it returned. So does the caller switching itself off.
            if self.closed or (alive is not None and not alive()):
                return None
            try:
                status, text = self._fetch(url)
            except Exception:
                logger.info("MusicBrainz request failed (transient)")
                return None
            if status in (429, 503):
                if self._paused(int(status), attempt, foreground):
                    continue
                return None
            if int(status) == 400 and plain_url and plain_url != url:
                self._refused.add(extras_kind)
                logger.info("MusicBrainz refused the extras for a %s read: asking without them", extras_kind or "")
                return self.get(
                    plain_url,
                    ttl=ttl,
                    unanswered=unanswered,
                    foreground=foreground,
                    not_found_answers=not_found_answers,
                    alive=alive,
                )
            return self._answer(url, int(status), text, ttl=ttl, unanswered=unanswered, not_found=not_found_answers)
        return None

    def _paused(self, status: int, attempt: int, foreground: bool) -> bool:
        """MusicBrainz asked for a pause: hold every caller, and say whether
        this request goes again (background, attempts left)."""
        pause = BACKOFF_SEC[min(attempt, len(BACKOFF_SEC) - 1)]
        self.pacer.hold(pause)
        if foreground or attempt >= len(BACKOFF_SEC):
            logger.info("MusicBrainz asked for a pause (HTTP %d): giving up for now", status)
            return False
        logger.info("MusicBrainz asked for a pause (HTTP %d): waiting %gs", status, pause)
        return True

    def _answer(self, url, status, text, *, ttl, unanswered, not_found) -> dict | None:
        """A response that is not a pause, parsed and remembered when it is
        an answer, None when it is not."""
        if status == 200:
            try:
                body = json.loads(text)
            except ValueError:
                logger.info("MusicBrainz answered unparseable JSON")
                return None
            if not isinstance(body, dict):
                return None
            empty = unanswered is not None and unanswered(body)
            self._remember(url, text, body, ttl=NEGATIVE_TTL_SEC if empty else ttl)
            return body
        if not_found and status in _NEGATIVE_STATUSES:
            self._remember(url, "{}", {}, ttl=NEGATIVE_TTL_SEC)
            return {}
        logger.info("MusicBrainz request HTTP %d (transient)", status)
        return None

    def _http_fetch(self, url: str) -> tuple[int, str]:
        req = urllib.request.Request(  # noqa: S310 (https only: every URL is built on MB_BASE)
            url, headers={"User-Agent": self._ua, "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SEC, context=tls_context()) as resp:  # noqa: S310
                return int(resp.status), resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return int(e.code), ""

    # ---- what the file knows ------------------------------------------------

    def release_facts(self, mbid: str, max_age: float | None = None) -> dict | None:
        """A release as on record: ``{title, date, track_count, length}``
        (length the summed recording seconds, 0 until a read with every
        track's length was seen). None when nothing is on record, or when
        its length was last read longer than ``max_age`` ago (so a reader
        asks again and notices an edit)."""
        if not mbid:
            return None
        row = self._query_one(
            "SELECT title, date, track_count, length, length_at FROM releases WHERE mbid = ?", (str(mbid),)
        )
        if row is None:
            return None
        if max_age is not None and int(row[3] or 0) > 0 and self._now() - float(row[4] or 0) > max_age:
            return None
        return {
            "title": row[0] or "",
            "date": row[1] or "",
            "track_count": int(row[2] or 0),
            "length": int(row[3] or 0),
        }

    def artist_on_record(self, mbid: str) -> dict | None:
        """An artist as on record: the union of every answer's view of it
        (the library lookup's read by id carries its genres), None when
        nothing is."""
        if not mbid:
            return None
        row = self._query_one("SELECT json FROM artists WHERE mbid = ?", (str(mbid),))
        try:
            body = json.loads(row[0]) if row and row[0] else None
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    def artists_named(self, name: str) -> list[str]:
        """The MusicBrainz ids of the artists on record under this name (by
        the library's artist key), the most credited first: those credited
        on a release on record, or the one artist of the name when only one
        is."""
        key = name_key(name)
        if not key:
            return []
        with self._db_lock:
            conn = self._db()
            if conn is None:
                return []
            rows = conn.execute(
                "SELECT a.mbid, COUNT(ra.release_mbid) AS n FROM artists a"
                " LEFT JOIN release_artists ra ON ra.artist_mbid = a.mbid"
                " WHERE a.name_key = ? GROUP BY a.mbid ORDER BY n DESC, a.mbid",
                (key,),
            ).fetchall()
        # Artists met only in searches carry no credits, so their order says
        # nothing: several such namesakes name no one; a lone one is the
        # only artist of that name on record.
        if len(rows) > 1 and not rows[0][1]:
            return []
        return [str(r[0]) for r in rows if r[1] or len(rows) == 1]

    def link(self, scope: str, key: str, name: str, mbid: str, how: str) -> None:
        """Put on record which MusicBrainz artist ``key`` is (see the
        artist_links table): ``mbid`` '' with ``how`` "none" for a question
        asked with no sure answer."""
        if not key:
            return
        with self._db_lock:
            conn = self._db()
            if conn is None:
                return
            conn.execute(
                "INSERT OR REPLACE INTO artist_links (scope, key, name_key, mbid, how, at) VALUES (?, ?, ?, ?, ?, ?)",
                (scope, str(key), name_key(name), str(mbid or ""), how, self._now()),
            )
            conn.commit()

    def linked(self, scope: str, key: str) -> dict | None:
        """``{mbid, how, at}`` as on record for ``key``, or None."""
        row = self._query_one("SELECT mbid, how, at FROM artist_links WHERE scope = ? AND key = ?", (scope, str(key)))
        return None if row is None else {"mbid": row[0] or "", "how": row[1] or "", "at": float(row[2] or 0)}

    def confirmed(self, scope: str, name: str, *, refuse: frozenset = frozenset()) -> str:
        """The one MusicBrainz artist ``scope`` has proved under this name,
        '' when it proved none or more than one (namesakes: no way to tell).
        ``refuse`` leaves out proofs of those kinds, so a confirmation that
        itself leaned on the asking side never comes back as its own proof."""
        key = name_key(name)
        if not key:
            return ""
        with self._db_lock:
            conn = self._db()
            if conn is None:
                return ""
            rows = conn.execute(
                "SELECT DISTINCT mbid, how FROM artist_links WHERE scope = ? AND name_key = ? AND mbid != ''",
                (scope, key),
            ).fetchall()
        ids = {str(m) for m, how in rows if how not in refuse}
        return ids.pop() if len(ids) == 1 else ""

    def counts(self) -> dict:
        """How many of each thing the file holds (logs and tests)."""
        out = {}
        with self._db_lock:
            conn = self._db()
            for table in ("responses", "artists", "release_groups", "releases"):
                sql = f"SELECT COUNT(*) FROM {table}"  # noqa: S608 (fixed names)
                out[table] = 0 if conn is None else int(conn.execute(sql).fetchone()[0])
        return out

    def close(self) -> None:
        with self._db_lock:
            self._closed = True
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ---- sqlite ----------------------------------------------------------------

    def _db(self) -> sqlite3.Connection | None:
        """The connection, opened on first use (caller holds the lock).
        None when closed or keeping nothing."""
        if self._closed or self._path is None:
            return None
        if self._conn is None:
            conn = db.connect(self._path, kind=db.CACHE)
            for stmt in _SCHEMA:
                conn.execute(stmt)
            for table in _TABLES:  # a file from before change tracking grows the column
                with contextlib.suppress(sqlite3.OperationalError):
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN changed REAL")
            # ...and one from before lengths were dated grows that, each length
            # dated by when its release was last read (not all due at once).
            try:
                conn.execute("ALTER TABLE releases ADD COLUMN length_at REAL")
            except sqlite3.OperationalError:
                pass
            else:
                conn.execute("UPDATE releases SET length_at = seen WHERE length > 0")
            conn.execute("DELETE FROM responses WHERE expires IS NOT NULL AND expires < ?", (self._now(),))
            conn.commit()
            self._conn = conn
        return self._conn

    def _query_one(self, sql: str, params: tuple):
        with self._db_lock:
            conn = self._db()
            return None if conn is None else conn.execute(sql, params).fetchone()

    def _cache_read(self, url: str) -> dict | None:
        row = self._query_one("SELECT body, expires FROM responses WHERE url = ?", (url,))
        if not row:
            return None
        body, expires = row
        if expires is not None and self._now() > float(expires):
            return None
        try:
            parsed = json.loads(body)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _remember(self, url: str, text: str, body: dict, *, ttl: float | None) -> None:
        """Cache the answer and put what it says on record, in one write."""
        expires = (self._now() + ttl) if ttl is not None else None
        with self._db_lock:
            conn = self._db()
            if conn is None:
                return
            try:
                conn.execute(
                    "INSERT INTO responses (url, body, expires) VALUES (?, ?, ?) "
                    "ON CONFLICT(url) DO UPDATE SET body=excluded.body, expires=excluded.expires",
                    (url, text, expires),
                )
                learned, edited = _harvest(conn, url, body, self._now())
                conn.commit()
            except sqlite3.Error:
                conn.rollback()
                logger.info("MusicBrainz cache write failed")
                return
            self._writes += 1
            if self._writes % _PRUNE_EVERY == 0:
                conn.execute("DELETE FROM responses WHERE expires IS NOT NULL AND expires < ?", (self._now(),))
                conn.commit()
        if edited:
            logger.info("MusicBrainz answer: %d thing(s) on record had changed since last asked", edited)
        if learned:
            logger.debug("MusicBrainz answer put %d thing(s) on record", learned)


# ---- taking an answer apart ---------------------------------------------------


def _harvest(conn: sqlite3.Connection, url: str, body: dict, now: float) -> tuple[int, int]:
    """Every artist, release group and release an answer names, on record.
    Recognized by shape, so any endpoint's answer teaches what it carries.
    Returns how many entities were written and how many of those already
    on record came back changed."""
    edits = [0]
    n = 0
    parts = urllib.parse.urlsplit(url)
    path = parts.path.rstrip("/")
    query = dict(urllib.parse.parse_qsl(parts.query))
    for a in _as_list(body.get("artists")):
        n += _artist(conn, a, now, edits)
    browse_artist = str(query.get("artist") or "")
    for g in _as_list(body.get("release-groups")):
        n += _group(conn, g, now, edits, artist_mbid=browse_artist)
    for r in _as_list(body.get("releases")):
        n += _release(conn, r, now, edits)
    # One thing read by id: /release/<mbid>, /artist/<mbid>, /release-group/<mbid>.
    head, _, tail = path.rpartition("/")
    if tail and head.endswith("/release") and "releases" not in body:
        n += _release(conn, {"id": tail, **body}, now, edits)
    elif tail and head.endswith("/artist") and "artists" not in body:
        n += _artist(conn, {"id": tail, **body}, now, edits)
    elif tail and head.endswith("/release-group") and "release-groups" not in body:
        n += _group(conn, {"id": tail, **body}, now, edits)
    return n, edits[0]


def _artist(conn, a, now: float, edits: list) -> int:
    if not isinstance(a, dict) or not a.get("id"):
        return 0
    name = str(a.get("name") or "")
    edits[0] += _upsert(
        conn,
        "artists",
        str(a["id"]),
        {"name": name, "name_key": name_key(name), "disambiguation": str(a.get("disambiguation") or "")},
        a,
        now,
    )
    return 1


def _group(conn, g, now: float, edits: list, *, artist_mbid: str = "") -> int:
    if not isinstance(g, dict) or not g.get("id"):
        return 0
    credited = _credited(g)
    edits[0] += _upsert(
        conn,
        "release_groups",
        str(g["id"]),
        {
            "artist_mbid": artist_mbid or (credited[0] if credited else ""),
            "title": str(g.get("title") or ""),
            "first_date": str(g.get("first-release-date") or "")[:10],
            "primary_type": str(g.get("primary-type") or "").lower(),
            "secondary": ",".join(str(s).lower() for s in _as_list(g.get("secondary-types"))),
        },
        g,
        now,
    )
    return 1 + _credits(conn, g, now, edits)


def _release(conn, r, now: float, edits: list) -> int:
    if not isinstance(r, dict) or not r.get("id"):
        return 0
    mbid = str(r["id"])
    n = 1
    group = r.get("release-group")
    rgid = ""
    credited = _credited(r)
    if isinstance(group, dict) and group.get("id"):
        rgid = str(group["id"])
        n += _group(conn, group, now, edits, artist_mbid="" if _credited(group) else (credited[0] if credited else ""))
    media = [m for m in _as_list(r.get("media")) if isinstance(m, dict)]
    tracks = _int(r.get("track-count")) or sum(_int(m.get("track-count")) for m in media)
    length = summed_length_sec(r)
    edits[0] += _upsert(
        conn,
        "releases",
        mbid,
        {
            "rgid": rgid,
            "title": str(r.get("title") or ""),
            "date": str(r.get("date") or "")[:10],
            "track_count": tracks,
            "length": length,
            # When a read last carried every track's length (a search never
            # does, so meeting the release there does not refresh it).
            "length_at": now if length > 0 else 0,
        },
        r,
        now,
    )
    for artist in credited:
        conn.execute("INSERT OR IGNORE INTO release_artists (release_mbid, artist_mbid) VALUES (?, ?)", (mbid, artist))
    return n + _credits(conn, r, now, edits)


def _credits(conn, entity: dict, now: float, edits: list) -> int:
    n = 0
    for c in _as_list(entity.get("artist-credit")):
        if isinstance(c, dict):
            n += _artist(conn, c.get("artist"), now, edits)
    return n


def _credited(entity: dict) -> list[str]:
    out = []
    for c in _as_list(entity.get("artist-credit")):
        a = c.get("artist") if isinstance(c, dict) else None
        if isinstance(a, dict) and a.get("id"):
            out.append(str(a["id"]))
    return out


def _upsert(conn, table: str, key: str, cols: dict, raw: dict, now: float) -> int:
    """Write one entity, merged with what is on record: a column the new
    answer leaves empty keeps its old value, and the raw JSON is the union
    of every answer's view of it (a search sees less than a read by id).
    ``changed`` moves when a value the new answer carries differs from the
    one on record (a part it does not carry is not a change), so a later
    reader can tell what MusicBrainz edited since. Returns 1 for a change
    to something already on record, else 0."""
    key_col, names = _TABLES[table]
    old = conn.execute(
        f"SELECT {', '.join(names)}, json, changed FROM {table} WHERE {key_col} = ?",  # noqa: S608 (fixed names)
        (key,),
    ).fetchone()
    merged = dict(cols)
    blob = dict(raw)
    changed = now
    edited = 0
    if old is not None:
        for i, name in enumerate(names):
            if not merged.get(name):
                merged[name] = old[i]
        try:
            prior = json.loads(old[-2] or "{}")
        except ValueError:
            prior = {}
        if isinstance(prior, dict):
            fresh = {k: v for k, v in raw.items() if v not in (None, "", [], {})}
            edited = int(
                any(k not in _VOLATILE_KEYS and k in prior and _differs(prior[k], v) for k, v in fresh.items())
            )
            blob = {**prior, **fresh}
        changed = now if edited else old[-1]
    conn.execute(
        f"INSERT OR REPLACE INTO {table} ({key_col}, {', '.join(names)}, json, seen, changed)"  # noqa: S608 (fixed names)
        f" VALUES ({', '.join(['?'] * (len(names) + 4))})",
        (key, *(merged.get(n) for n in names), json.dumps(blob, separators=(",", ":")), now, changed),
    )
    return edited


def _differs(old, new) -> bool:
    """Whether two views of one value disagree. A search and a read by id
    show the same thing with different detail (a medium's track count vs
    its whole track list), so only what both carry is compared: the shared
    keys of two objects, and list items matched by id or name where they
    have one. Vote counts and search scores never count."""
    if isinstance(old, dict) and isinstance(new, dict):
        return any(k not in _VOLATILE_KEYS and _differs(old[k], new[k]) for k in old.keys() & new.keys())
    if isinstance(old, list) and isinstance(new, list):
        a, b = _keyed(old), _keyed(new)
        if a is not None and b is not None:
            return a.keys() != b.keys() or any(_differs(a[k], b[k]) for k in a)
        return len(old) != len(new) or any(_differs(x, y) for x, y in zip(old, new, strict=False))
    return old != new


def _keyed(items: list) -> dict | None:
    """List items by their id (or name), or None when they have none."""
    out = {}
    for it in items:
        key = it.get("id") or it.get("name") if isinstance(it, dict) else None
        if not key:
            return None
        out[str(key)] = it
    return out


def drop_legacy_cache(folder: str) -> None:
    """Remove the arbiter's own cache from before this shared file (it
    held MusicBrainz URLs built from library titles and is never read)."""
    for suffix in ("", "-wal", "-shm"):
        with contextlib.suppress(OSError):
            os.remove(os.path.join(folder, "mbarbiter.sqlite3" + suffix))


def summed_length_sec(body: dict) -> int:
    """The release's recording seconds summed across its media, 0 unless
    EVERY track reported one: a sum missing any track's length would refute
    true matches, the same all-or-nothing honesty the scanner applies to
    runtime."""
    total_ms = 0
    complete = True
    for medium in _as_list(body.get("media")):
        if not isinstance(medium, dict):
            continue
        for track in _as_list(medium.get("tracks")):
            if not isinstance(track, dict):
                continue
            ms = track.get("length")
            if not ms:
                complete = False
                continue
            try:
                total_ms += int(ms)
            except (TypeError, ValueError):
                complete = False
    return total_ms // 1000 if complete and total_ms > 0 else 0


def quote(term: str) -> str:
    """A term fit for a quoted Lucene phrase."""
    return str(term or "").replace("\\", " ").replace('"', " ").strip()


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
