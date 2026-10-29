"""Genre tags for new downloads, from MusicBrainz.

TIDAL's catalog carries no genre on a track or an album (tidalapi only knows
the genre browse pages), so the Genre tag has always been left empty, and a
user who wants one fills it in by hand or with a tagger. MusicBrainz knows
genres for most releases (community voted, CC0 data), and Waves already reads
it for the Releases check and the library lookup, so a download can ask the
same service the same way and write what it answers.

Scope, on purpose: the genres MusicBrainz lists for a release (the artist's
when the release has none, and, with its own setting on, when MusicBrainz
does not list the release at all yet, as with a single out this week),
written into the files THIS download creates, when the setting is on. Nothing here reads or rewrites a file already in the
library, chooses a vocabulary, or ranks beyond MusicBrainz's own votes; that
is a tagger's job.

The asking is one request a second like every other MusicBrainz use (the
shared pacer) and it never holds a download: the lookup starts in the
background the moment a track passes its skip check, one per release however
many workers the job runs, queued in the job's order on one thread (a track
waiting at the tag step goes first, and one whose track went on without it
is dropped before it spends a request), and the tag step takes the answer if it is there,
waiting a few seconds at most on the first track of a release. A release is
asked once: its MusicBrainz group goes on record under the TIDAL album id
(scope "album" in the service's link table) and the reads themselves are
cached for months; a release MusicBrainz cannot match is remembered for a
day and not asked again.

Privacy: a request carries the release's barcode, or its title and artist
as search terms, never an account id, a token or a local path. Log lines
carry counts and outcomes only.

No Qt and no tidalapi, so it unit tests without the GUI stack: the HTTP
layer is the service's injectable ``fetch``.
The engine never imports this module (the MusicBrainz stack reaches the
presence matcher, which the engine must never pull in): the bridge builds
the lookup and hands it to the job, and the tag text itself is composed by
metadata.join_genres.
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.parse
from collections import deque
from collections.abc import Callable

from waves.artist_import import name_key
from waves.mb_service import DEFINITE_TTL_SEC, MB_BASE, NEGATIVE_TTL_SEC, MBService, Pacer, quote
from waves.metadata import DEFAULT_GENRE_DELIMITER, genre_label, join_genres
from waves.musicbrainz import ARTIST_TTL_SEC, SEARCH_TTL_SEC, title_key

logger = logging.getLogger("waves.genres")

__all__ = ["DEFAULT_GENRE_DELIMITER", "SCOPE", "WAIT_SEC", "GenreLookup", "genre_label", "join_genres"]

# The link-table scope keying a TIDAL album id to its MusicBrainz release group.
SCOPE = "album"
# Releases a search returns; the barcode names one, a title a handful.
SEARCH_LIMIT = 5
# How long the tag step waits for an answer still in flight. Only the first
# track of a release can wait at all (its lookup left when the track started,
# a stream fetch ago); the rest find the answer on record.
WAIT_SEC = 6.0


class GenreLookup:
    """The genres of each release a job downloads, asked of MusicBrainz once
    in the background and handed to the tag step when it asks.

    One instance per job. Production hands in the process's one ``service``;
    without one (tests) the lookup builds its own over ``fetch`` and
    ``pacer``. ``alive`` is the setting's own switch: once it reads false no
    request leaves, though what is on record still answers. ``artist_fallback``
    is the second setting: while it reads true, a release MusicBrainz does
    not list gets the genres of the artist of that name (none when several
    artists share it).
    """

    def __init__(
        self,
        service: MBService | None = None,
        *,
        fetch: Callable[[str], tuple[int, str]] | None = None,
        pacer: Pacer | None = None,
        alive: Callable[[], bool] | None = None,
        artist_fallback: Callable[[], bool] | bool = False,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._svc = service or MBService(None, fetch=fetch, pacer=pacer)
        self._alive = alive
        self._artist_fallback = artist_fallback
        self._now = now
        self._answers: dict[str, _Answer] = {}
        self._queue: deque[str] = deque()
        self._working = False
        self._lock = threading.Lock()

    # ---- the job's side ----------------------------------------------------

    def prefetch(self, album_id: str, *, upc: str = "", artist: str = "", title: str = "") -> None:
        """Queue the release's lookup if it is not queued; returns at once."""
        self._answer(album_id, upc, artist, title)

    def genres(
        self, album_id: str, *, upc: str = "", artist: str = "", title: str = "", wait: float = WAIT_SEC
    ) -> list[str]:
        """The release's genres, best voted first, waiting up to ``wait``
        seconds for a lookup still in flight; [] when there are none or the
        answer is not back in time (the tag step writes without). A lookup
        still queued goes first, as a track is waiting on it; one nobody
        waits on any more once this gives up is dropped before it spends a
        request (a later track of the release queues it again). Without the
        drop, a playlist of many albums piled up lookups faster than one a
        second could answer them, so every track waited its whole budget and
        most still came back empty."""
        answer = self._answer(album_id, upc, artist, title)
        wait = max(0.0, float(wait))
        with self._lock:
            if not answer.done.is_set():
                answer.waiters += 1
                if answer.key in self._queue:
                    self._queue.remove(answer.key)
                    self._queue.appendleft(answer.key)
            else:
                return list(answer.genres)
        done = answer.done.wait(wait)
        with self._lock:
            answer.waiters -= 1
            answer.gave_up = answer.gave_up or not done
        if not done:
            logger.info("genre answer not back in time for a release; tagging without it")
            return []
        return list(answer.genres)

    def _answer(self, album_id: str, upc: str, artist: str, title: str) -> _Answer:
        key = str(album_id or "") or f"{title_key(title)}|{artist}"
        with self._lock:
            answer = self._answers.get(key)
            if answer is None:
                answer = self._answers[key] = _Answer(key, (str(album_id or ""), upc, artist, title))
                self._queue.append(key)
                if not self._working:
                    # One daemon thread per job, never a pool: the pacer
                    # serves one request at a time anyway, and a read still
                    # waiting on the network at quit must not hold the
                    # process open.
                    self._working = True
                    threading.Thread(target=self._work, name="waves-genre", daemon=True).start()
            return answer

    def _work(self) -> None:
        """Answer the queued releases one at a time, front first."""
        while True:
            with self._lock:
                answer = None
                while self._queue:
                    candidate = self._answers[self._queue.popleft()]
                    if candidate.waiters == 0 and candidate.gave_up:
                        # The track that wanted it went on without it: no
                        # request for nobody. Off the record of this job too,
                        # so a later track of the release asks again.
                        del self._answers[candidate.key]
                        candidate.done.set()
                        continue
                    answer = candidate
                    break
                if answer is None:
                    self._working = False
                    return
            try:
                album_id, upc, artist, title = answer.facts
                answer.genres = self.genres_for(album_id, upc=upc, artist=artist, title=title)
            finally:
                answer.done.set()

    # ---- the asking ----------------------------------------------------------

    def genres_for(self, album_id: str, *, upc: str = "", artist: str = "", title: str = "") -> list[str]:
        """The release's genres, asked now on this thread: what is on record
        first, one search and one or two reads otherwise. [] when MusicBrainz
        has none, cannot match the release, or could not be asked (nothing is
        put on record then, so the next job asks again)."""
        try:
            return self._genres_for(album_id, upc, artist, title)
        except Exception:
            logger.debug("genre lookup failed", exc_info=True)
            return []

    def _genres_for(self, album_id: str, upc: str, artist: str, title: str) -> list[str]:
        known = self._svc.linked(SCOPE, album_id) if album_id else None
        if known is not None:
            if known["mbid"]:
                return self._genres_of_group(known["mbid"]) or []
            if self._now() - float(known["at"]) < NEGATIVE_TTL_SEC:
                return self._genres_of_named_artist(artist)
        found = self._find_group(upc, artist, title)
        if found is None:
            logger.info("genre lookup could not ask MusicBrainz; the next job asks again")
            return []
        rgid, how = found
        if album_id:
            self._svc.link(SCOPE, album_id, title, rgid, how)
        if not rgid:
            genres = self._genres_of_named_artist(artist)
            if genres:
                logger.info("no MusicBrainz release matched; the artist's genres stand in: %d", len(genres))
            else:
                logger.info("no MusicBrainz release matched; no genre written")
            return genres
        genres = self._genres_of_group(rgid) or []
        logger.info("genres for a release: %d (matched by %s)", len(genres), how)
        return genres

    def _find_group(self, upc: str, artist: str, title: str) -> tuple[str, str] | None:
        """The release group of (barcode, or artist + title): ``(rgid, how)``,
        ``("", "none")`` when MusicBrainz lists no such release, None when it
        could not be asked."""
        code = _barcode_key(upc)
        if code:
            releases = self._search(f"barcode:{quote(code)}")
            if releases is None:
                return None
            for release in releases:
                if _barcode_key(release.get("barcode")) == code and _group_id(release):
                    return _group_id(release), "barcode"
        wanted = title_key(title)
        if not wanted or not artist:
            return "", "none"
        releases = self._search(f'release:"{quote(title)}" AND artist:"{quote(artist)}"')
        if releases is None:
            return None
        for release in releases:
            if title_key(str(release.get("title") or "")) == wanted and _group_id(release):
                return _group_id(release), "search"
        return "", "none"

    def _search(self, query: str) -> list[dict] | None:
        url = f"{MB_BASE}/release/?" + urllib.parse.urlencode({"query": query, "fmt": "json", "limit": SEARCH_LIMIT})
        body = self._svc.get(
            url,
            ttl=DEFINITE_TTL_SEC,
            unanswered=lambda b: not _as_list(b.get("releases")),
            foreground=True,
            alive=self._alive,
        )
        if body is None:
            return None
        return [r for r in _as_list(body.get("releases")) if isinstance(r, dict)]

    def _genres_of_group(self, rgid: str) -> list[str] | None:
        """The group's genres, or its credited artist's when it has none;
        None when the group could not be read."""
        base = f"{MB_BASE}/release-group/{urllib.parse.quote(rgid)}?"
        plain = base + urllib.parse.urlencode({"inc": "artists", "fmt": "json"})
        url = base + urllib.parse.urlencode({"inc": "artists+genres+tags", "fmt": "json"})
        body = self._svc.get(
            url, ttl=DEFINITE_TTL_SEC, foreground=True, plain_url=plain, extras_kind="release-group", alive=self._alive
        )
        if body is None:
            return None
        genres = _ranked(body.get("genres"))
        if genres:
            return genres
        for credit in _as_list(body.get("artist-credit")):
            mbid = str(((credit.get("artist") if isinstance(credit, dict) else None) or {}).get("id") or "")
            if mbid:
                genres = self._genres_of_artist(mbid)
                if genres:
                    return genres
        return []

    def _genres_of_named_artist(self, name: str) -> list[str]:
        """The fallback for a release MusicBrainz does not list: the genres
        of the artist of that name. First the one another feature already
        proved under the name: the library lookup (Look up your artists on
        MusicBrainz), which tells namesakes apart by the albums the library
        holds, then the Releases check's proof for a followed artist; their
        genres are on record, so no request leaves. Otherwise the one artist
        filed under ``name`` (exact name, namesakes get nothing: no way to
        tell which one made the release). [] with the setting off or no such
        artist."""
        on = self._artist_fallback() if callable(self._artist_fallback) else bool(self._artist_fallback)
        key = name_key(name)
        if not on or not key:
            return []
        for scope in ("library", "tidal"):
            proved = self._svc.confirmed(scope, name)
            if proved:
                logger.info("artist fallback: the %s lookup's artist stands in", scope)
                return self._genres_of_artist(proved)
        q = urllib.parse.urlencode({"query": f'artist:"{quote(name)}"', "fmt": "json", "limit": SEARCH_LIMIT})
        body = self._svc.get(f"{MB_BASE}/artist/?{q}", ttl=SEARCH_TTL_SEC, foreground=True, alive=self._alive)
        if body is None:
            return []
        same = [
            str(a.get("id") or "")
            for a in _as_list(body.get("artists"))
            if isinstance(a, dict) and a.get("id") and name_key(str(a.get("name") or "")) == key
        ]
        if len(same) != 1:
            logger.info("artist fallback: %d artists of that name; no genre written", len(same))
            return []
        return self._genres_of_artist(same[0])

    def _genres_of_artist(self, mbid: str) -> list[str]:
        """The artist's genres: from the record when a read by id put them
        there (the library lookup reads every library artist that way),
        else asked."""
        known = self._svc.artist_on_record(mbid)
        if known is not None and _ranked(known.get("genres")):
            return _ranked(known.get("genres"))
        base = f"{MB_BASE}/artist/{urllib.parse.quote(mbid)}?"
        plain = base + urllib.parse.urlencode({"fmt": "json"})
        url = base + urllib.parse.urlencode({"inc": "genres+tags", "fmt": "json"})
        body = self._svc.get(
            url, ttl=ARTIST_TTL_SEC, foreground=True, plain_url=plain, extras_kind="artist", alive=self._alive
        )
        return _ranked(body.get("genres")) if body is not None else []


class _Answer:
    """One release's genres, and whether the lookup has come back. ``facts``
    are what it is asked with (album id, barcode, artist, title);
    ``waiters`` counts the tag steps waiting on it now, and ``gave_up``
    says one stopped waiting before it came back."""

    def __init__(self, key: str, facts: tuple[str, str, str, str]) -> None:
        self.key = key
        self.facts = facts
        self.done = threading.Event()
        self.genres: list[str] = []
        self.waiters = 0
        self.gave_up = False


def _ranked(genres) -> list[str]:
    """Genre names most voted first (ties by name); the unvoted ones only
    when nothing is voted."""
    rows = [
        (int(g.get("count") or 0), str(g.get("name") or ""))
        for g in _as_list(genres)
        if isinstance(g, dict) and g.get("name")
    ]
    voted = [row for row in rows if row[0] > 0] or rows
    return [name for _count, name in sorted(voted, key=lambda row: (-row[0], row[1]))]


def _group_id(release: dict) -> str:
    group = release.get("release-group")
    return str(group.get("id") or "") if isinstance(group, dict) else ""


def _barcode_key(code) -> str:
    """A barcode as MusicBrainz matches it: digits only, leading zeros
    dropped (a UPC-A and the EAN-13 it sits in are the same code)."""
    digits = "".join(ch for ch in str(code or "") if ch.isdigit())
    return digits.lstrip("0")


def _as_list(value) -> list:
    return value if isinstance(value, list) else []
