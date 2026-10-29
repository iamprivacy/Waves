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

Politeness and privacy live in ``waves.mb_service``, shared with the
Releases check: one process-wide request a second (an arbitration is a
foreground request, so it goes ahead of a discography read in progress), a
descriptive User-Agent, and definite-only caching (a transient network
error is never cached, so a blip cannot poison an album forever; a
definitive not-found expires after a day). Every answer is also put on
record there, so a release whose length any earlier answer told is not
read again. Requests carry artist and release-title search terms, service
data and user-chosen text, never an account id, token or local path; log
lines carry outcomes and counts only.

No Qt and no tidalapi, so it unit tests without the GUI stack. The HTTP
layer is one injectable ``fetch`` callable returning ``(status, body)``
tuples, so tests drive the whole arbiter with canned responses and never
touch the network or sleep.
"""

from __future__ import annotations

import logging
import time
import urllib.parse
from collections.abc import Callable

from waves.matching import _album_duration_tol, same_edition, to_year_int
from waves.mb_service import (
    DEFINITE_TTL_SEC,
    MB_BASE,
    MIN_INTERVAL_SEC,
    NEGATIVE_TTL_SEC,
    MBService,
    Pacer,
    quote,
    summed_length_sec,
)

logger = logging.getLogger("waves.mbarbiter")

# One search plus at most this many per-release reads per arbitration, so an
# album with dozens of editions cannot turn into a crawl at 1 req/s.
MAX_RELEASE_READS = 3
# The release read asks for everything it can carry in the same single
# request (groups, credits, ISRCs, and the service's extras: labels,
# genres, tags, links), so later questions find it on record.
_RELEASE_INC = "recordings+release-groups+artist-credits+isrcs"
__all__ = ["MAX_RELEASE_READS", "NEGATIVE_TTL_SEC", "MBArbiter"]


class MBArbiter:
    """Ask MusicBrainz whether an unproven library match is the release on
    screen. Thread-safe: lookups run on a background worker; the shared
    service serializes the requests and guards its file.

    Production hands in the process's one ``service``; without one (tests)
    the arbiter builds its own over ``db_path`` with the given clock."""

    def __init__(
        self,
        db_path: str | None = None,
        *,
        service: MBService | None = None,
        user_agent: str | None = None,
        fetch: Callable[[str], tuple[int, str]] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], float] = time.time,
        min_interval: float = MIN_INTERVAL_SEC,
    ) -> None:
        self._owns = service is None
        self._svc = service or MBService(
            db_path,
            fetch=fetch,
            pacer=Pacer(min_interval, sleep=sleep, monotonic=monotonic),
            user_agent=user_agent,
            now=now,
        )

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
        lucene = f'release:"{quote(title)}" AND artist:"{quote(artist)}"'
        q = urllib.parse.urlencode({"query": lucene, "fmt": "json", "limit": 25})
        # An empty releases array is the search endpoint's not-found: a real
        # answer, but one MusicBrainz gains, so it must expire (see
        # NEGATIVE_TTL_SEC), never sit in the file forever.
        body = self._get(f"{MB_BASE}/release/?{q}", unanswered=lambda b: not _as_list(b.get("releases")))
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
        # Already on record with every length (from any earlier answer,
        # whichever feature asked): no request.
        # Only while that answer is recent: an edit to a length is noticed.
        known = self._svc.release_facts(mbid, max_age=DEFINITE_TTL_SEC)
        if known and known["length"] > 0:
            return {"length": known["length"], "year": known["date"][:4]}
        base = f"{MB_BASE}/release/{urllib.parse.quote(mbid)}?"
        plain = base + urllib.parse.urlencode({"inc": _RELEASE_INC, "fmt": "json"})
        extra = self._svc.extras("release")
        full = base + urllib.parse.urlencode({"inc": f"{_RELEASE_INC}+{extra}", "fmt": "json"}) if extra else plain
        # A release whose recordings carry no lengths cannot vouch for anyone
        # TODAY, but contributors add lengths: cache it like a not-found, so
        # the day they land the badge can still upgrade.
        body = self._get(full, unanswered=lambda b: summed_length_sec(b) <= 0, plain_url=plain)
        if body is None:
            return None
        return {
            "length": summed_length_sec(body),
            "year": str(body.get("date", "") or "")[:4],
        }

    # ---- the service -------------------------------------------------------

    def _get(self, url: str, *, unanswered: Callable[[dict], bool], plain_url: str | None = None) -> dict | None:
        """A foreground read: a pill on screen waits for it. A definite
        answer is kept DEFINITE_TTL_SEC (then asked again, so an edit on
        MusicBrainz reaches the cache), a not-found or an answer with nothing
        in it yet for a day (see waves.mb_service.get)."""
        return self._svc.get(
            url,
            ttl=DEFINITE_TTL_SEC,
            unanswered=unanswered,
            foreground=True,
            not_found_answers=True,
            plain_url=plain_url,
            extras_kind="release",
        )

    def close(self) -> None:
        """Final for an arbiter that built its own service; the shared one
        is closed by its owner."""
        if self._owns:
            self._svc.close()


def _as_list(value) -> list:
    return value if isinstance(value, list) else []
