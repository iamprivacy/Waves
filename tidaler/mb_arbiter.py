"""MusicBrainz arbitration for library matches the scan cannot prove.

The presence matcher proves identity from what the user's files can say:
years, edition-qualified titles, counts and play lengths. When neither side
can testify (an undated folder beside a release TIDAL reports no length for),
the pill wears its "?" forever. MusicBrainz knows the missing facts: every
edition of a release, each with its date, its track count and its recordings'
lengths, on CC0 data. This module asks, compares, and answers strictly
True (proven), False (asked and not provable) or None (could not ask, try
again later).

The proof rule is deliberately conservative, in the matcher's own currency:
among MusicBrainz's releases of (artist, title), find one whose edition-
qualified title matches (same_edition), whose track count equals the LOCAL
copy's, and whose total length matches the LOCAL copy's summed seconds within
the matcher's own album tolerance. That names the local copy's edition. It
proves the match only when that same release also agrees with the release ON
SCREEN on every axis TIDAL offered (track count when given, year within one
when given, total length within the same tolerance when given). Nothing
looser: a lookup that cannot pin BOTH sides to one MusicBrainz release proves
nothing.

Politeness and privacy follow the genre resolver that shipped on the harbor
branch: a process-wide one-request-per-second gate, a descriptive User-Agent
(MusicBrainz blocks anonymous clients), definite-only caching in a local
sqlite file (a transient network error is never cached, so a blip cannot
poison an album forever), and a short negative TTL for definitive not-founds.
Requests carry artist and release-title search terms, service data and
user-chosen text, never an account id, token or local path; log lines carry
outcomes and counts only.

Pure standard library (urllib, json, sqlite3), no Qt and no tidalapi, so it
unit tests without the GUI stack. The HTTP layer is one injectable ``fetch``
callable returning ``(status, body)`` tuples, so tests drive the whole
arbiter with canned responses and never touch the network or sleep.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from threading import Lock

from tidaler.matching import _album_duration_tol, same_edition, to_year_int

logger = logging.getLogger("waves.mbarbiter")

MB_BASE = "https://musicbrainz.org/ws/2"
# MusicBrainz allows one request per second to the public web service.
MIN_INTERVAL_SEC = 1.0
# Arbitration is best-effort enrichment on a background worker; a flaky link
# must not hold the (serialised) request gate for long.
LOOKUP_TIMEOUT_SEC = 10
# One search plus at most this many per-release reads per arbitration, so an
# album with dozens of editions cannot turn into a crawl at 1 req/s.
MAX_RELEASE_READS = 3
# A definitive not-found is a real answer worth remembering, but MusicBrainz
# does occasionally gain it, so a day, not forever. This covers BOTH shapes a
# not-found arrives in: the negative HTTP statuses below, and the search
# endpoint's polite one, HTTP 200 with an empty releases array (a release
# whose recordings carry no lengths is the same kind of answer: nothing to
# vouch with today, but MusicBrainz routinely gains it).
NEGATIVE_TTL_SEC = 24 * 3600
_NEGATIVE_STATUSES = frozenset({400, 404, 410})


def _default_user_agent() -> str:
    """The descriptive User-Agent MusicBrainz requires. Version via tidaler's
    own metadata so it stays current, with a static fallback for source runs."""
    try:
        from importlib.metadata import version

        v = version("tidaler")
    except Exception:
        v = "dev"
    return f"Waves/{v} ( https://github.com/iamprivacy/Waves )"


class MBArbiter:
    """Ask MusicBrainz whether an unproven library match is the release on
    screen. Thread-safe: lookups run on a background worker; the sqlite cache
    is opened check_same_thread=False under an instance lock, and a second
    lock serialises the outbound request rate."""

    def __init__(
        self,
        db_path: str,
        *,
        user_agent: str | None = None,
        fetch: Callable[[str], tuple[int, str]] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], float] = time.time,
        min_interval: float = MIN_INTERVAL_SEC,
    ) -> None:
        self._path = str(db_path)
        self._ua = user_agent or _default_user_agent()
        self._fetch = fetch or self._http_fetch
        self._sleep = sleep
        self._monotonic = monotonic
        self._now = now
        self._min_interval = float(min_interval)
        self._rate_lock = Lock()
        self._last_request: float | None = None
        self._db_lock = Lock()
        self._conn: sqlite3.Connection | None = None
        # close() is final: a straggler arbitration still holding this
        # instance must not lazily reopen the cache (a settings wipe deletes
        # the file, and a reopen would resurrect it).
        self._closed = False

    # ---- public ---------------------------------------------------------

    def arbitrate(self, want: dict, local: dict) -> bool | None:
        """One arbitration. ``want`` is the release on screen ``{title,
        artist, year, tracks, duration}`` (year a string, possibly empty;
        tracks and duration ints, 0 when TIDAL never said). ``local`` is the
        matched copy ``{tracks, runtime}`` (runtime the folder's summed
        seconds, 0 when its files never said). The copy's year tag is
        deliberately not consulted: remasters are routinely tagged with the
        original release's year, and count plus seconds pin harder.

        Returns True when one MusicBrainz release pins both sides, False when
        MusicBrainz answered and could not, None when it could not be asked
        (nothing cached; the caller may retry later)."""
        title = str(want.get("title", "") or "")
        artist = str(want.get("artist", "") or "")
        local_tracks = int(local.get("tracks", 0) or 0)
        runtime = int(local.get("runtime", 0) or 0)
        if not title or not artist or local_tracks <= 0 or runtime <= 0:
            # Without the local copy's own shape and seconds there is nothing
            # to pin it BY; asking would burn the rate gate for a guess.
            return False
        releases = self._search_releases(artist, title)
        if releases is None:
            return None
        # Candidate editions: same edition-qualified title, and exactly the
        # local copy's track count (its length is about to be compared, and a
        # different count makes the comparison meaningless).
        candidates = [
            r
            for r in releases
            if int(r.get("track_count", 0) or 0) == local_tracks and same_edition(r.get("title", ""), title)
        ]
        tt = int(want.get("tracks", 0) or 0)
        if tt > 0 and tt != local_tracks:
            # The screen's own count already disagrees with the copy; no
            # third-party fact can make them the same release.
            return False
        wy = to_year_int(want.get("year"))
        wd = int(want.get("duration", 0) or 0)
        tol = _album_duration_tol(local_tracks)
        asked = 0
        verdict: bool | None = False
        for r in candidates:
            if asked >= MAX_RELEASE_READS:
                break
            asked += 1
            ident = self._release_identity(str(r.get("id", "")))
            if ident is None:
                # Cannot ask right now; report transient unless a later
                # candidate proves outright.
                verdict = None
                continue
            length = int(ident.get("length", 0) or 0)
            ry = to_year_int(ident.get("year"))
            # The local copy is pinned by its own physics alone: exact track
            # count (the candidate filter) and summed seconds within the
            # matcher's tolerance. Its year tag deliberately gets no veto,
            # because remasters are routinely tagged with the ORIGINAL
            # release's year and the length is the stronger witness.
            pins_local = length > 0 and abs(length - runtime) <= tol
            # Every axis TIDAL offered must agree, the length included: a
            # release that pins the local copy but contradicts the screen's
            # own seconds names a DIFFERENT edition than the one on screen,
            # and proving with it would overrule the matcher's refutation.
            pins_want = (wy is None or ry is None or abs(ry - wy) <= 1) and (wd <= 0 or abs(length - wd) <= tol)
            if pins_local and pins_want:
                return True
        return verdict

    # ---- MusicBrainz reads ------------------------------------------------

    def _search_releases(self, artist: str, title: str) -> list[dict] | None:
        """All releases MusicBrainz files under this artist and title:
        ``[{id, title, track_count, year}]``. None on a transient failure."""
        lucene = f'release:"{_quote(title)}" AND artist:"{_quote(artist)}"'
        q = urllib.parse.urlencode({"query": lucene, "fmt": "json", "limit": 25})
        # An empty releases array is the search endpoint's not-found: a real
        # answer, but one MusicBrainz gains, so it must expire (see
        # NEGATIVE_TTL_SEC), never sit in the file forever.
        body = self._cached_get(f"{MB_BASE}/release/?{q}", unanswered=lambda b: not _as_list(b.get("releases")))
        if body is None:
            return None
        out = []
        for r in _as_list(body.get("releases")):
            if not isinstance(r, dict):
                continue
            out.append(
                {
                    "id": str(r.get("id", "") or ""),
                    "title": str(r.get("title", "") or ""),
                    "track_count": int(r.get("track-count", 0) or 0),
                    "year": str(r.get("date", "") or "")[:4],
                }
            )
        return out

    def _release_identity(self, mbid: str) -> dict | None:
        """One release's identity facts: ``{length, year}`` with length the
        summed recording seconds (0 when MusicBrainz has no lengths for it).
        None on a transient failure or a junk id."""
        if not mbid:
            return None
        q = urllib.parse.urlencode({"inc": "recordings", "fmt": "json"})
        # A release whose recordings carry no lengths cannot vouch for anyone
        # TODAY, but contributors add lengths: cache it like a not-found, so
        # the day they land the badge can still upgrade.
        body = self._cached_get(
            f"{MB_BASE}/release/{urllib.parse.quote(mbid)}?{q}",
            unanswered=lambda b: _summed_length_sec(b) <= 0,
        )
        if body is None:
            return None
        return {
            "length": _summed_length_sec(body),
            "year": str(body.get("date", "") or "")[:4],
        }

    # ---- HTTP + cache ------------------------------------------------------

    def _cached_get(self, url: str, *, unanswered: Callable[[dict], bool] | None = None) -> dict | None:
        """GET with definite-only caching: a parsed 200 body and definitive
        not-founds (under a short TTL) are stored forever/briefly; transient
        failures are returned as None and never cached. ``unanswered`` lets
        the caller classify a 200 body that holds NO definite positive answer
        (the search endpoint says not-found as 200 with an empty releases
        array): those cache under NEGATIVE_TTL_SEC like the negative statuses,
        never forever, or an album MusicBrainz catalogues next month could
        never upgrade its badge for the life of the cache file. A closed
        arbiter answers None outright: close() is the settings wipe's word
        that no request should go out and no file should reappear."""
        with self._db_lock:
            if self._closed:
                return None
        cached = self._cache_read(url)
        if cached is not None:
            return cached
        fetched = self._rate_gated_fetch(url)
        if fetched is None:
            return None
        status, text = fetched
        if status == 200:
            try:
                body = json.loads(text)
            except ValueError:
                logger.info("MusicBrainz lookup returned unparseable JSON")
                return None
            if isinstance(body, dict):
                ttl = NEGATIVE_TTL_SEC if unanswered is not None and unanswered(body) else None
                self._cache_write(url, text, ttl=ttl)
                return body
            return None
        if status in _NEGATIVE_STATUSES:
            # A real answer: remember it briefly so re-browsing the same album
            # does not repeat the round-trip behind the 1 req/s gate.
            self._cache_write(url, "{}", ttl=NEGATIVE_TTL_SEC)
            return {}
        logger.info("MusicBrainz lookup HTTP %s (transient)", int(status))
        return None

    def _rate_gated_fetch(self, url: str) -> tuple[int, str] | None:
        """One outbound request behind the serialised 1 req/s gate, or None on
        a transient failure (never cached) or a closed arbiter. The closed
        flag is re-checked after any rate wait and immediately before the
        request: a close() landing between _cached_get's entry check and this
        point (it has a whole rate-gate sleep to land in) must not see one
        last request depart after it returned."""
        with self._rate_lock:
            if self._last_request is not None:
                wait = self._min_interval - (self._monotonic() - self._last_request)
                if wait > 0:
                    self._sleep(wait)
            self._last_request = self._monotonic()
            with self._db_lock:
                if self._closed:
                    return None
            try:
                return self._fetch(url)
            except Exception:
                logger.info("MusicBrainz lookup failed (transient)")
                return None

    def _http_fetch(self, url: str) -> tuple[int, str]:
        req = urllib.request.Request(  # noqa: S310 (https only: every URL is built on MB_BASE)
            url, headers={"User-Agent": self._ua, "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=LOOKUP_TIMEOUT_SEC) as resp:  # noqa: S310 (https, fixed host)
                return int(resp.status), resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return int(e.code), ""

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("CREATE TABLE IF NOT EXISTS responses (url TEXT PRIMARY KEY, body TEXT, expires REAL)")
            self._conn.commit()
        return self._conn

    def _cache_read(self, url: str) -> dict | None:
        with self._db_lock:
            if self._closed:
                return None
            row = self._db().execute("SELECT body, expires FROM responses WHERE url = ?", (url,)).fetchone()
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

    def _cache_write(self, url: str, body: str, *, ttl: float | None) -> None:
        expires = (self._now() + ttl) if ttl is not None else None
        with self._db_lock:
            if self._closed:
                return
            self._db().execute(
                "INSERT INTO responses (url, body, expires) VALUES (?, ?, ?) "
                "ON CONFLICT(url) DO UPDATE SET body=excluded.body, expires=excluded.expires",
                (url, body, expires),
            )
            self._db().commit()

    def close(self) -> None:
        with self._db_lock:
            self._closed = True
            if self._conn is not None:
                self._conn.close()
                self._conn = None


def _summed_length_sec(body: dict) -> int:
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


def _quote(term: str) -> str:
    """Escape a term for embedding in a quoted Lucene phrase."""
    return str(term or "").replace("\\", " ").replace('"', " ").strip()


def _as_list(value) -> list:
    return value if isinstance(value, list) else []
