"""The fresh-releases hint: which followed artists likely released something.

The sweep (``bridge_releases``) re-reads every follow's TIDAL catalog in a
slow round, so a release that lands just after an artist's turn waits for
the next one. This module narrows that wait with one cheap, global read:
ListenBrainz's fresh-releases list (``/1/explore/fresh-releases``), every
release MusicBrainz knows of dated in the last few days, in one keyless
request (a quarter megabyte gzipped for a week). The list is matched
against the follows on this machine, by MusicBrainz artist id where the
follow has one and by the library's own artist key otherwise, and the
artists it names are checked on TIDAL next, ahead of the round.

A hint is only a hint: TIDAL stays the source of every release on record,
a namesake costs one extra check, and an artist MusicBrainz has not caught
up with yet is still found by the round. Nothing about the user travels:
the request carries no name, id or account, only the day window, and the
matching happens here.

TIDAL's own feed is the second list (``parse_tidal_feed``): the new albums,
EPs and singles of the account's favorite artists over the last month, in
one read-only request (about 90 KB) on the session Waves already holds. It
names TIDAL artist and album ids, so it is exact and has no MusicBrainz lag,
but it covers only TIDAL favorites. It is only ever read: nothing here marks
an activity seen, so the TIDAL apps' own new-release dot is left alone.

Pure standard library over one injectable ``fetch``, so it unit tests with
canned answers and never touches the network.
"""

from __future__ import annotations

import datetime as _dt
import gzip
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable

from waves.artist_import import name_key
from waves.tls import tls_context

logger = logging.getLogger("waves.releases.fresh")

LB_FRESH_URL = "https://api.listenbrainz.org/1/explore/fresh-releases/"
# TIDAL's feed: a path on the v2 API (no trailing slash: that one is a 404).
TIDAL_V2 = "https://api.tidal.com/v2/"
TIDAL_FEED_PATH = "feed/activities"
TIMEOUT_SEC = 30
# The service's own ceiling on the window.
MAX_DAYS = 90
# An answer bigger than this is not the list (a week is ~1 MB unpacked).
MAX_BYTES = 16 * 1024 * 1024
# The least pause after a refusal or a failure, whatever the answer says.
MIN_BACKOFF_SEC = 1800
# A read that never reached the service (the network not back yet after a
# wake) tries again this soon instead.
OFFLINE_RETRY_SEC = 120

# What joins the artists of one credit ("A feat. B", "A & B", "A x B"). Not
# "and": too many band names hold it ("Florence and the Machine").
_JOINERS = re.compile(
    r"\s*(?:,|&|\+|/|;|\bfeat\.?|\bft\.?|\bfeaturing\b|\bwith\b|\bx\b|\bvs\.?)\s*",
    re.IGNORECASE,
)


class FetchError(Exception):
    """The read failed; ``retry_after`` is how long to stay away (seconds).
    ``offline`` when the service was never reached (no network yet, as just
    after a wake): that asks nothing of it, so the wait is short."""

    def __init__(self, message: str, retry_after: float = MIN_BACKOFF_SEC, *, offline: bool = False) -> None:
        super().__init__(message)
        self.offline = bool(offline)
        if self.offline:
            self.retry_after = float(OFFLINE_RETRY_SEC)
        else:
            self.retry_after = max(float(MIN_BACKOFF_SEC), float(retry_after or 0))


def http_fetch(url: str, user_agent: str) -> bytes:
    """GET ``url`` gzipped; the body unpacked. Raises FetchError with the
    pause the service asked for on a refusal (429, 503) or a failure."""
    req = urllib.request.Request(  # noqa: S310 (https only: the URL is built on LB_FRESH_URL)
        url, headers={"User-Agent": user_agent, "Accept": "application/json", "Accept-Encoding": "gzip"}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC, context=tls_context()) as resp:  # noqa: S310
            raw = resp.read(MAX_BYTES + 1)
            encoding = str(resp.headers.get("Content-Encoding") or "").lower()
    except urllib.error.HTTPError as e:
        wait = _retry_after(e.headers)
        raise FetchError(f"HTTP {int(e.code)}", wait) from None  # noqa: TRY003
    except OSError as e:
        raise FetchError(type(e).__name__, offline=True) from None
    except ValueError as e:
        raise FetchError(type(e).__name__) from None
    if len(raw) > MAX_BYTES:
        raise FetchError("answer too large")  # noqa: TRY003
    if encoding == "gzip":
        try:
            raw = gzip.decompress(raw)
        except (OSError, EOFError) as e:
            raise FetchError(type(e).__name__) from None
    return raw


def _retry_after(headers) -> float:
    """The pause a refusal names: Retry-After, else ListenBrainz's own
    X-RateLimit-Reset-In; 0 when neither reads as a number."""
    for key in ("Retry-After", "X-RateLimit-Reset-In"):
        try:
            value = float(str((headers or {}).get(key) or "").strip())
        except (TypeError, ValueError):
            continue
        if value >= 0:
            return value
    return 0.0


def fresh_url(days: int) -> str:
    days = max(1, min(MAX_DAYS, int(days)))
    return LB_FRESH_URL + "?" + urllib.parse.urlencode({"days": days, "past": "true", "future": "true"})


def read_fresh(days: int, fetch: Callable[[str], bytes]) -> list[dict]:
    """The fresh releases of the last ``days`` days (and the few dated just
    ahead), each ``{rgid, day, credit, mbids}``. Raises FetchError when the
    read fails or the answer is not the list."""
    raw = fetch(fresh_url(days))
    try:
        body = json.loads(raw.decode("utf-8", "replace"))
        releases = body["payload"]["releases"]
    except (ValueError, KeyError, TypeError, AttributeError):
        raise FetchError("not the fresh-releases list") from None  # noqa: TRY003
    if not isinstance(releases, list):
        raise FetchError("not the fresh-releases list")  # noqa: TRY003
    out = []
    for r in releases:
        if not isinstance(r, dict):
            continue
        day = str(r.get("release_date") or "")[:10]
        rgid = str(r.get("release_group_mbid") or r.get("release_mbid") or "")
        if not rgid or len(day) != 10:
            continue
        mbids = r.get("artist_mbids")
        out.append(
            {
                "rgid": rgid,
                "day": day,
                "credit": str(r.get("artist_credit_name") or ""),
                "mbids": [str(m) for m in mbids if m] if isinstance(mbids, list) else [],
            }
        )
    return out


def parse_tidal_feed(body) -> list[dict]:
    """The new releases in a TIDAL feed answer, each ``{rgid, day, credit,
    mbids, tidal_ids, release_id}`` (``rgid`` is the album id with a ``t:``
    prefix, so it never meets a MusicBrainz one). Raises FetchError when the
    answer is not the feed."""
    try:
        acts = body["activities"]
    except (KeyError, TypeError):
        raise FetchError("not the TIDAL feed") from None  # noqa: TRY003
    if not isinstance(acts, list):
        raise FetchError("not the TIDAL feed")  # noqa: TRY003
    out = []
    for a in acts:
        fa = a.get("followableActivity") if isinstance(a, dict) else None
        if not isinstance(fa, dict) or fa.get("activityType") != "NEW_ALBUM_RELEASE":
            continue
        album = fa.get("album") if isinstance(fa.get("album"), dict) else {}
        rid = str(album.get("id") or "")
        day = str(album.get("releaseDate") or fa.get("occurredAt") or "")[:10]
        main = album.get("mainArtists") or album.get("artists") or []
        ids = [str(x.get("id")) for x in main if isinstance(x, dict) and x.get("id") is not None]
        if not rid or len(day) != 10 or not ids:
            continue
        out.append({"rgid": "t:" + rid, "day": day, "credit": "", "mbids": [], "tidal_ids": ids, "release_id": rid})
    return out


def credit_keys(credit: str) -> set[str]:
    """The artist keys a credit names: the whole credit, and each artist
    joined into it ("A feat. B" names A, B and "A feat. B")."""
    keys = {name_key(credit)}
    keys.update(name_key(part) for part in _JOINERS.split(credit or ""))
    keys.discard("")
    return keys


def day_start(day: str) -> int:
    """Local midnight opening ``day`` (YYYY-MM-DD), epoch seconds: releases go
    live at the listener's midnight. 0 for a day that does not parse."""
    try:
        d = _dt.date.fromisoformat(str(day)[:10])
    except ValueError:
        return 0
    try:
        return int(time.mktime((d.year, d.month, d.day, 0, 0, 0, 0, 0, -1)))
    except (OverflowError, ValueError):
        return 0


def hinted_artists(
    fresh: Iterable[dict],
    follows: Iterable[dict],
    *,
    today: str,
    known: set[str] | None,
    cap: int | None = None,
) -> tuple[list[str], set[str]]:
    """Which follows to check ahead of the round (the ``cap`` with the newest
    releases), and the release groups to remember as ``known`` for the next
    read: all the list holds, less those whose every hint fell past the cap
    (so a release filed late still counts as new next time).

    A follow is hinted by a release out by ``today`` that credits it (by
    TIDAL artist id, by MBID, or by name key when neither matches) when
    either the follow was
    last checked before that release's day began, or the release group is
    new to the list since the last read (MusicBrainz often files a release
    days after it came out). ``known`` is None on the first read: nothing
    is new then, only the day rule applies. A follow with an MBID is never
    matched by name: an MBID that did not match says it is someone else."""
    by_id, by_mbid, by_key = _index(follows)
    seen: set[str] = set()
    hinted: dict[str, int] = {}
    why: dict[str, set[str]] = {}
    for r in fresh:
        rgid = str(r.get("rgid") or "")
        day = str(r.get("day") or "")
        if not rgid:
            continue
        seen.add(rgid)
        if not day or day > today:
            continue
        matched = _matched(r, by_id, by_mbid, by_key)
        if not matched:
            continue
        new = known is not None and rgid not in known
        opened = day_start(day)
        for f in matched:
            if new or int(f.get("last_checked") or 0) < opened:
                aid = str(f["artist_id"])
                hinted[aid] = max(hinted.get(aid, 0), opened)
                why.setdefault(aid, set()).add(rgid)
    # The newest releases first: the cap keeps those.
    order = sorted(hinted, key=lambda a: (-hinted[a], a))
    if cap is not None and len(order) > cap:
        taken = set().union(*(why[a] for a in order[:cap]))
        dropped = set().union(*(why[a] for a in order[cap:]))
        seen -= dropped - taken
        order = order[:cap]
    return order, seen


def _matched(r: dict, by_id: dict, by_mbid: dict, by_key: dict) -> list[dict]:
    """The follows one listed release credits: by TIDAL id (the TIDAL feed),
    else by MBID, else by name key (never a follow with an MBID of its own)."""
    if r.get("tidal_ids"):
        return [by_id[i] for i in r["tidal_ids"] if i in by_id]
    matched = [f for m in r.get("mbids") or [] for f in by_mbid.get(str(m), [])]
    if not matched:
        keys = credit_keys(str(r.get("credit") or ""))
        matched = [f for k in keys for f in by_key.get(k, []) if not str(f.get("mbid") or "")]
    return matched


def _index(follows: Iterable[dict]) -> tuple[dict[str, dict], dict[str, list[dict]], dict[str, list[dict]]]:
    """The follows by TIDAL artist id, by MusicBrainz artist id and by name key."""
    by_id: dict[str, dict] = {}
    by_mbid: dict[str, list[dict]] = {}
    by_key: dict[str, list[dict]] = {}
    for f in follows:
        aid = str(f.get("artist_id") or "")
        if not aid:
            continue
        by_id[aid] = f
        mbid = str(f.get("mbid") or "")
        if mbid:
            by_mbid.setdefault(mbid, []).append(f)
        key = name_key(str(f.get("name") or ""))
        if key:
            by_key.setdefault(key, []).append(f)
    return by_id, by_mbid, by_key


def in_release_window(now: _dt.datetime) -> bool:
    """Release night, local time: Thursday 22:00 to Friday 06:00, when most
    releases go live (at the listener's midnight going into Friday)."""
    wd, hour = now.weekday(), now.hour
    return (wd == 3 and hour >= 22) or (wd == 4 and hour < 6)
