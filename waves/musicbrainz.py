"""The MusicBrainz side of the Releases pane: what an artist has released
that TIDAL does not carry.

The sweep knows an artist's catalog as TIDAL lists it. MusicBrainz knows
the discography itself: every release group (an album, an EP, a single)
with the day it first came out, on CC0 data. With the opt-in setting the
Artists ledger asks it for each followed artist, lines the two up, and
draws the groups TIDAL has no row for in red on the timeline, so a career
reads whole and a gap says "not on TIDAL" rather than "never existed".

Three parts, all pure standard library (urllib, json) over one injectable
``fetch``, so the whole thing unit tests with canned answers and never
touches the network:

* ``MusicBrainzClient``: the two reads this needs, an artist search by
  name and an artist's release groups paged whole, through the process's
  one MusicBrainz service (``waves.mb_service``, shared with the library
  arbiter): a descriptive User-Agent, one request a second for the whole
  process, a backoff when MusicBrainz asks for a pause (429, 503), and
  every answer put on record. These reads are background ones, so a
  page's question goes ahead of them. Only albums, EPs and singles
  count; a group with any secondary type (a compilation, a live record,
  a remix set, a soundtrack, a mixtape, a DJ mix, a demo, a bootleg, an
  interview, an audiobook) is dropped, since TIDAL files those apart or
  not at all and a red mark for them would be noise.
* ``resolve_artist``: which MusicBrainz artist a follow IS. Names collide
  (there are a dozen artists called "Nirvana"), so a candidate is accepted
  only when its name matches the follow's (the library's own artist key)
  AND at least two of its release-group titles match releases TIDAL lists
  for the follow (the artist of that name most credited on record is
  weighed before a search is spent); a follow with fewer than two releases needs
  an exact name match with no namesake beside it. Anything less is "no
  sure match", recorded as such, so the mark is never drawn on a guess.
* ``match_groups``: which TIDAL row each release group is. By title, with
  parentheticals and brackets stripped (TIDAL's "(Deluxe Edition)" and
  "(feat. X)" against MusicBrainz's bare title) and punctuation collapsed,
  and the first-release year within one of TIDAL's. A group with no row
  is "not on TIDAL".

Privacy: a request carries a followed artist's name or an artist MBID,
never an account id, a token or a local path, and only ever when the
setting is on. Log lines carry outcomes and counts; a name is logged only
as user content, and an MBID never beside one.
"""

from __future__ import annotations

import logging
import re
import unicodedata
import urllib.parse
from collections.abc import Callable

from waves.artist_import import name_key
from waves.mb_service import BACKOFF_SEC, MB_BASE, MIN_INTERVAL_SEC, MBService, Pacer, quote, shared_pacer

logger = logging.getLogger("waves.musicbrainz")

__all__ = ["BACKOFF_SEC", "MB_BASE", "MIN_INTERVAL_SEC", "MusicBrainzClient", "Pacer", "shared_pacer"]

# Release groups a page: the service's maximum.
PAGE = 100
# Pages read for one artist: 2,000 release groups is past any discography
# TIDAL could mirror; a catalog longer than that is read this far.
MAX_PAGES = 20
# Artist candidates a search returns; a namesake ranks within the first few.
SEARCH_LIMIT = 10
# An artist search is kept a week, a page of release groups six hours: long
# enough that a check retried after a failed page does not read the early
# pages again, short enough that the monthly re-check sees new releases.
SEARCH_TTL_SEC = 7 * 86400
PAGE_TTL_SEC = 6 * 3600
# An artist read by id (aliases, genres, links) is asked again after a month.
ARTIST_TTL_SEC = 30 * 86400
# Namesakes weighed against the follow's releases before giving up: each
# costs a page or more at the pacer's rate.
CANDIDATES_WEIGHED = 3
# Release-group titles a candidate must share with the follow's TIDAL rows.
MIN_SHARED_TITLES = 2
# How far a group's first-release year may sit from TIDAL's and still be
# the same record (TIDAL often carries a later pressing's date).
YEAR_SLACK = 1
# The primary types that count, as MusicBrainz spells them, to the rtype
# the store uses.
PRIMARY_TYPES = {"album": "album", "ep": "ep", "single": "single"}


class MusicBrainzClient:
    """The two reads the Releases pane needs, through ``service`` (the
    process's one; without it, a service of its own that keeps nothing,
    over ``fetch`` and ``pacer``, for tests)."""

    def __init__(
        self,
        *,
        service: MBService | None = None,
        fetch: Callable[[str], tuple[int, str]] | None = None,
        pacer: Pacer | None = None,
        user_agent: str | None = None,
        alive: Callable[[], bool] | None = None,
    ) -> None:
        self._svc = service or MBService(None, fetch=fetch, pacer=pacer, user_agent=user_agent)
        self._pacer = self._svc.pacer
        # The owning feature's switch: off, no request of this client leaves.
        self._alive = alive

    # ---- reads -----------------------------------------------------------

    def search_artist(self, name: str, limit: int = SEARCH_LIMIT) -> list[dict] | None:
        """The artists MusicBrainz files under ``name``, best first:
        ``[{mbid, name, disambiguation, score}]``. None when it could not
        be asked (the caller tries again later)."""
        term = quote(name)
        if not term:
            return []
        q = urllib.parse.urlencode({"query": f'artist:"{term}"', "fmt": "json", "limit": int(limit)})
        body = self._svc.get(f"{MB_BASE}/artist/?{q}", ttl=SEARCH_TTL_SEC, alive=self._alive)
        if body is None:
            return None
        out = []
        for a in _as_list(body.get("artists")):
            if not isinstance(a, dict) or not a.get("id"):
                continue
            out.append(
                {
                    "mbid": str(a.get("id")),
                    "name": str(a.get("name") or ""),
                    "disambiguation": str(a.get("disambiguation") or ""),
                    "score": _int(a.get("score")),
                }
            )
        return out

    def release_groups(self, mbid: str) -> list[dict] | None:
        """Every album, EP and single release group of the artist, paged
        whole: ``[{rgid, title, date, rtype}]`` with ``date`` the first
        release day as MusicBrainz knows it ("YYYY-MM-DD", "YYYY-MM",
        "YYYY" or ""). Groups with a secondary type are left out. None when
        a page could not be read: a half-read catalog would mark releases
        missing that the unread pages hold, so nothing of it is kept."""
        mbid = str(mbid or "")
        if not mbid:
            return []
        out: list[dict] = []
        offset = 0
        for _page in range(MAX_PAGES):
            params = {"artist": mbid, "type": "album|ep|single", "fmt": "json", "limit": PAGE, "offset": offset}
            plain = f"{MB_BASE}/release-group?{urllib.parse.urlencode(params)}"
            extra = self._svc.extras("release-group")
            # Genres and tags ride along in the same page (see mb_service.EXTRAS).
            url = f"{MB_BASE}/release-group?{urllib.parse.urlencode({**params, 'inc': extra})}" if extra else plain
            body = self._svc.get(url, ttl=PAGE_TTL_SEC, plain_url=plain, extras_kind="release-group", alive=self._alive)
            if body is None:
                return None
            page = _as_list(body.get("release-groups"))
            for g in page:
                row = _group_row(g)
                if row is not None:
                    out.append(row)
            offset += len(page)
            if len(page) < PAGE or offset >= _int(body.get("release-group-count")):
                break
        return out

    def artist(self, mbid: str) -> dict | None:
        """One artist read by id, with the service's extras (aliases,
        genres, tags, links, rating) in the same request; on record after.
        None when it could not be read."""
        mbid = str(mbid or "")
        if not mbid:
            return None
        base = f"{MB_BASE}/artist/{urllib.parse.quote(mbid)}?"
        plain = base + urllib.parse.urlencode({"fmt": "json"})
        extra = self._svc.extras("artist")
        url = base + urllib.parse.urlencode({"inc": extra, "fmt": "json"}) if extra else plain
        return self._svc.get(url, ttl=ARTIST_TTL_SEC, plain_url=plain, extras_kind="artist", alive=self._alive)

    def link(self, scope: str, key: str, name: str, mbid: str, how: str) -> None:
        """Put a proof on record for the other features (MBService.link)."""
        self._svc.link(scope, key, name, mbid, how)

    def confirmed(self, scope: str, name: str, *, refuse: frozenset = frozenset()) -> str:
        """Another feature's proof under this name (MBService.confirmed)."""
        return self._svc.confirmed(scope, name, refuse=refuse)

    def known_artists(self, name: str) -> list[str]:
        """The MusicBrainz ids already on record under ``name`` (any
        feature's earlier answers), the most credited first."""
        return self._svc.artists_named(name)


# ---- matching ---------------------------------------------------------------

_PARENS = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")
_NONWORD = re.compile(r"[^0-9a-z]+")


def title_key(title: str) -> str:
    """The key two release titles are compared by: accents folded, lower
    case, every parenthetical and bracket stripped ("(Deluxe Edition)",
    "[Remastered]", "(feat. X)"), punctuation collapsed to single spaces.
    Empty when nothing is left."""
    s = unicodedata.normalize("NFKD", str(title or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower().replace("&", " and ")
    for _ in range(3):  # nested parentheticals, a level a pass
        stripped = _PARENS.sub(" ", s)
        if stripped == s:
            break
        s = stripped
    return " ".join(_NONWORD.sub(" ", s).split())


def _year(day) -> int | None:
    head = str(day or "")[:4]
    return int(head) if head.isdigit() else None


def match_groups(groups: list[dict], tidal_rows: list[dict]) -> list[dict]:
    """Each release group with ``release_id``: the TIDAL row it is (same
    title key, first-release year within ``YEAR_SLACK``; a group with no
    date matches on the title alone), or '' when TIDAL has none. A row
    already taken by another group is used again only when no free row
    fits, and among fitting rows the same kind wins."""
    by_key: dict[str, list[dict]] = {}
    for r in tidal_rows:
        k = title_key(r.get("title"))
        if k:
            by_key.setdefault(k, []).append(r)
    used: set[str] = set()
    out = []
    for g in groups:
        gy = _year(g.get("date"))
        best = None
        for r in by_key.get(title_key(g.get("title")), []):
            ry = _year(r.get("day") or r.get("date"))
            if gy is not None and ry is not None and abs(gy - ry) > YEAR_SLACK:
                continue
            rid = str(r.get("id") or "")
            rank = (rid in used, str(r.get("rtype") or "album") != str(g.get("rtype") or ""), rid)
            if best is None or rank < best[0]:
                best = (rank, rid)
        rid = best[1] if best else ""
        if rid:
            used.add(rid)
        out.append({**g, "release_id": rid})
    return out


def resolve_artist(
    client: MusicBrainzClient,
    name: str,
    tidal_rows: list[dict],
    confirm: Callable[[], str] | None = None,
) -> tuple[str, list[dict]] | None:
    """Which MusicBrainz artist the follow is, with its release groups:
    ``(mbid, groups)``; ``("", [])`` when no candidate is sure (recorded,
    so the question is not asked again for a while); None when MusicBrainz
    could not be asked (tried again sooner). See the module docstring for
    the acceptance rule.

    ``confirm`` is asked only when the rule alone is not sure: it names an
    artist another source already proved (the user's own files carrying the
    id, or the other feature's proof under the same name), or ''. That
    artist is taken, its groups paged, instead of "no sure match"."""
    found = _resolve(client, name, tidal_rows)
    if found is None or found[0] or confirm is None or not name_key(name):
        return found
    mbid = confirm()
    if not mbid:
        return found
    groups = client.release_groups(mbid)
    return None if groups is None else (mbid, groups)


def _resolve(client, name: str, tidal_rows: list[dict]) -> tuple[str, list[dict]] | None:
    key = name_key(name)
    if not key:
        return "", []
    titles = {title_key(r.get("title")) for r in tidal_rows} - {""}
    # The artist of this name most credited on record (met in the
    # arbiter's or an earlier check's answers) is weighed first, by the
    # same rule: when it proves itself the search is saved.
    weighed: set = set()
    if len(tidal_rows) >= MIN_SHARED_TITLES:
        known = _weigh_known(client, name, titles, weighed)
        if known is None or known:
            return known
    candidates = client.search_artist(name)
    if candidates is None:
        return None
    same = [c for c in candidates if name_key(c.get("name") or "") == key]
    if not same:
        return "", []
    if len(tidal_rows) < MIN_SHARED_TITLES:
        return _lone_name(client, same)
    # The known one spent part of the budget: the search's own candidates
    # get what is left, so a namesake-heavy name costs no more pages than
    # it did before anything was on record.
    budget = max(0, CANDIDATES_WEIGHED - len(weighed))
    for mbid in [c["mbid"] for c in same if c["mbid"] not in weighed][:budget]:
        found = _weigh(client, mbid, titles)
        if found is None:
            return None
        if found:
            return mbid, found
    return "", []


def _lone_name(client, same: list[dict]) -> tuple[str, list[dict]] | None:
    """A thin follow: taken only when it is the one artist of its name."""
    if len(same) != 1:
        return "", []
    groups = client.release_groups(same[0]["mbid"])
    if groups is None:
        return None
    return same[0]["mbid"], groups


def _weigh_known(client, name: str, titles: set, weighed: set) -> tuple | None:
    """The most credited artist of the name on record, weighed: ``(mbid,
    groups)`` when it proves itself, ``()`` when it does not or none is
    on record (its id joins ``weighed``), None when its groups could not
    be read."""
    known = getattr(client, "known_artists", None)
    for mbid in (known(name) if known else [])[:1]:
        weighed.add(mbid)
        found = _weigh(client, mbid, titles)
        if found is None:
            return None
        if found:
            return mbid, found
    return ()


def _weigh(client, mbid: str, titles: set) -> list[dict] | None:
    """The candidate's release groups when at least MIN_SHARED_TITLES of
    them are titles TIDAL lists for the follow, [] when fewer, None when
    they could not be read."""
    groups = client.release_groups(mbid)
    if groups is None:
        return None
    shared = {title_key(g.get("title")) for g in groups} & titles
    return groups if len(shared) >= MIN_SHARED_TITLES and groups else []


# ---- helpers ----------------------------------------------------------------


def _group_row(g) -> dict | None:
    if not isinstance(g, dict) or not g.get("id"):
        return None
    rtype = PRIMARY_TYPES.get(str(g.get("primary-type") or "").lower())
    if rtype is None or _as_list(g.get("secondary-types")):
        return None
    return {
        "rgid": str(g.get("id")),
        "title": str(g.get("title") or ""),
        "date": str(g.get("first-release-date") or "")[:10],
        "rtype": rtype,
    }


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
