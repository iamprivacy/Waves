"""The library lookup: what MusicBrainz knows about the artists in the
user's library, asked once and kept (the opt-in ``library_mb_lookup``).

For each library artist, most tracks first, it settles which MusicBrainz
artist they are and puts on record (in the shared MusicBrainz file, see
``waves.mb_service``) the artist read by id with its aliases, genres,
tags, links and rating, and every album, EP and single with their genres
and tags. Nothing is shown from it yet: it is there so a later feature
answers from the file instead of asking, and it stays current because
every artist is looked at again after LOOKUP_AGAIN_S and every answer
dates its own edits.

Which artist, in order, the first that answers:

1. The user's own files: when every file of the artist that carries a
   MusicBrainz album-artist id (Picard and its kin write one) carries the
   same one, that is the artist. No search is spent.
2. The rule the Releases check uses (``musicbrainz.resolve_artist``): a
   name match plus two release titles shared with the albums the library
   holds of them; a thin artist needs a lone exact name.
3. Confirmation from the Releases check: when the rule is not sure and a
   followed TIDAL artist of the same name was proved (TIDAL's releases and
   MusicBrainz's lined up), that proof settles it. The Releases check leans
   on this lookup the same way, the other direction.

Tracks are not asked one by one (a request a second over a whole library
would take days); the release groups carry the genres and tags, and a
track's own MusicBrainz ids are already in its file tags.

Pure logic over a ``MusicBrainzClient``, so it unit tests with canned
answers. Requests carry library artist names and MusicBrainz ids, never a
path; log lines carry counts and outcomes.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from waves import musicbrainz

logger = logging.getLogger("waves.mblibrary")

# Each library artist is looked at again after this long, so a genre or an
# album MusicBrainz gained reaches the record.
LOOKUP_AGAIN_S = 30 * 86400
# An artist the last pass could not ask (MusicBrainz unreachable) goes again
# after this long, not on the very next pass.
LOOKUP_RETRY_S = 3600


def due(client: musicbrainz.MusicBrainzClient, key: str, now: float) -> bool:
    """Whether a library artist is owed a look: never looked at, or looked
    at longer ago than LOOKUP_AGAIN_S (LOOKUP_RETRY_S after a failed one)."""
    seen = client._svc.linked("library", key)
    if seen is None:
        return True
    wait = LOOKUP_RETRY_S if seen["how"] == "retry" else LOOKUP_AGAIN_S
    return now - seen["at"] >= wait


def _retry(client: musicbrainz.MusicBrainzClient, key: str, name: str, alive: Callable[[], bool] | None) -> None:
    if alive is None or alive():
        client.link("library", key, name, "", "retry")


def look_up(
    client: musicbrainz.MusicBrainzClient,
    artist: dict,
    albums: list[dict],
    tag_id: str = "",
    alive: Callable[[], bool] | None = None,
) -> dict | None:
    """One library artist: ``artist`` a ``distinct_artists`` row (key,
    name), ``albums`` what the library holds of them (title, year),
    ``tag_id`` the id their files agree on ('' when none). Returns
    ``{mbid, how, groups}``, how one of "file_tag", "match" (the title
    rule), "follow" (the Releases check's proof) or "none" (no sure match,
    mbid ''),
    or None when MusicBrainz could not be asked; the outcome is put on
    record either way so the next pass knows when to look again, unless
    ``alive`` has turned false (the setting went off or a reset: a call
    refused for that is no failure to retry on)."""
    key = str(artist.get("key") or "")
    name = str(artist.get("name") or "")
    if not key or not name:
        return {"mbid": "", "how": "none", "groups": 0}
    groups: list[dict] | None = None
    if tag_id:
        mbid, how = tag_id, "file_tag"
    else:
        rows = [{"id": str(i), "title": a.get("title"), "day": a.get("year")} for i, a in enumerate(albums)]
        leaned: list[str] = []

        def confirm() -> str:
            # The Releases check's proof under this name, unless it leaned on
            # this lookup itself.
            got = client.confirmed("tidal", name, refuse=frozenset({"library"}))
            leaned.append(got)
            return got

        found = musicbrainz.resolve_artist(client, name, rows, confirm=confirm)
        if found is None:
            _retry(client, key, name, alive)
            return None
        mbid, groups = found
        how = ("follow" if leaned and leaned[-1] == mbid else "match") if mbid else "none"
    if mbid:
        if client.artist(mbid) is None:
            _retry(client, key, name, alive)
            return None
        if groups is None:
            groups = client.release_groups(mbid)
            if groups is None:
                _retry(client, key, name, alive)
                return None
    client.link("library", key, name, mbid, how)
    return {"mbid": mbid, "how": how, "groups": len(groups or [])}


def run_pass(
    client: musicbrainz.MusicBrainzClient,
    artists: list[dict],
    albums_of: Callable[[str], list[dict]],
    tag_ids: dict[str, str],
    alive: Callable[[], bool],
    now: Callable[[], float] = time.time,
) -> dict:
    """One pass over the library's artists, most tracks first, looking at
    every one that is due. Stops at the first artist MusicBrainz could not
    be asked about (the next pass resumes) or when ``alive`` turns false
    (the setting went off, a reset, a shutdown). Returns counts."""
    out = {"looked": 0, "known": 0, "unsure": 0, "stopped": False}
    for artist in sorted(artists, key=lambda a: (-int(a.get("tracks") or 0), str(a.get("name") or "").casefold())):
        if not alive():
            out["stopped"] = True
            break
        key = str(artist.get("key") or "")
        if not key or not due(client, key, now()):
            continue
        result = look_up(client, artist, albums_of(key), tag_ids.get(key, ""), alive)
        if result is None:
            out["stopped"] = True
            break
        out["looked"] += 1
        out["known" if result["mbid"] else "unsure"] += 1
    return out
