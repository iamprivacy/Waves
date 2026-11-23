"""Which TIDAL profile is the library's artist: the library as the anchor.

TIDAL has many artists that share a name, and a follow (often mirrored from
the account's favorites) can point at the wrong one. An album the library
holds is the anchor: two unrelated artists with the same name almost never
share a release title, so a profile that carries the library's albums is the
library's artist, and one that carries none of them, while another profile of
the same name carries them, is not.

Two questions, both pure (no Qt, no tidalapi, no logging):

* ``verdict``: does the followed profile carry what the library holds under
  its name? ``anchored`` (it does), ``unanchored`` (the library holds two or
  more distinctive albums, the profile's whole catalog is known and none of
  them is on it, or the ids Waves wrote into downloaded files name one other
  profile), or ``silent`` (nothing to judge by).
* ``pick_move``: given the followed profile and the other profiles of the
  same name, is there exactly one the library vouches for? ``move`` when the
  evidence is strong enough to act on alone, ``suggest`` when a person
  should confirm, None when there is nothing to do.

``evidence_sig`` fingerprints a verdict's inputs, so a follow is judged again
only when what it was judged by has changed.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from waves.artist_import import clear_winner, library_albums, name_key, title_key, weigh

ANCHORED = "anchored"
UNANCHORED = "unanchored"
SILENT = "silent"

MOVE = "move"
SUGGEST = "suggest"

# A profile that carries none of this many of the library's distinctive
# albums is not the library's artist (one album might be missing from TIDAL).
_MIN_ALBUMS = 2
# A move made without asking needs at least this many distinctive albums on
# the winning profile.
_MOVE_ALBUMS = 2
# A candidate carrying this many distinctive albums stands alone even when
# another holds one stray song title of the library's.
_DOMINANT_ALBUMS = 3


def _settled_tag_id(tag_ids: Iterable[str]) -> str:
    """The one TIDAL artist id the library's files name, or "" when they name
    none or several (files from two profiles prove nothing)."""
    ids = {str(i).strip() for i in tag_ids or () if str(i or "").strip()}
    return next(iter(ids)) if len(ids) == 1 else ""


def verdict(
    name: str,
    library: dict,
    profile_titles: Iterable[str] | None,
    *,
    catalog_whole: bool,
    profile_id: str = "",
    tag_ids: Iterable[str] = frozenset(),
) -> dict:
    """Judge a followed profile against what the library holds under ``name``.

    ``library`` is ``{"albums": [...], "tracks": [...]}`` (titles under the
    name); ``profile_titles`` the titles stored for the profile (None when
    nothing was read); ``catalog_whole`` whether those titles are the
    profile's whole catalog; ``tag_ids`` the TIDAL artist ids Waves wrote
    into the library's files under the name.

    Returns ``{"state", "reason", "albums", "owned_n", "albums_n"}`` where
    ``albums`` is the count of the library's distinctive albums. Reasons:
    ``titles`` (judged by titles), ``tag`` (judged by file tags),
    ``no_library``, ``thin`` (too few distinctive albums to judge a miss) and
    ``partial`` (a miss on a catalog only partly known).
    """
    albums, songs, weak = library_albums(name, library)
    strong_n = len(albums) - len(weak)
    w = {"owned_n": 0, "albums_n": 0}
    if profile_titles is not None:
        w = weigh(name, library, [{"id": profile_id, "name": name, "releases": list(profile_titles)}])[0]
    out = {"albums": strong_n, "owned_n": int(w["owned_n"]), "albums_n": int(w["albums_n"])}
    if out["owned_n"]:
        return {"state": ANCHORED, "reason": "titles", **out}
    tag = _settled_tag_id(tag_ids)
    if tag and profile_id:
        if tag == str(profile_id):
            return {"state": ANCHORED, "reason": "tag", **out}
        return {"state": UNANCHORED, "reason": "tag", "tag_id": tag, **out}
    if not albums and not songs:
        return {"state": SILENT, "reason": "no_library", **out}
    if strong_n < _MIN_ALBUMS:
        return {"state": SILENT, "reason": "thin", **out}
    if profile_titles is None or not catalog_whole:
        return {"state": SILENT, "reason": "partial", **out}
    return {"state": UNANCHORED, "reason": "titles", **out}


def fold_key(name: str) -> str:
    """``name_key`` with every run of punctuation and spaces read as one
    space, so "Static-X" and "Static X" meet. A fallback only: the exact key
    is asked first."""
    k = name_key(name)
    out, gap = [], False
    for ch in k:
        if ch.isalnum():
            if gap and out:
                out.append(" ")
            out.append(ch)
            gap = False
        else:
            gap = True
    return "".join(out)


def same_name(a: str, b: str) -> bool:
    """Whether two artist names are the same name: equal keys, or equal once
    punctuation is folded away."""
    ka, kb = name_key(a), name_key(b)
    return bool(ka) and (ka == kb or (fold_key(a) != "" and fold_key(a) == fold_key(b)))


def _dominant(weighed: list[dict]) -> dict | None:
    """The one candidate that carries the library's distinctive albums while
    every other carries at most one stray song title (a common title like
    "Something Wonderful" on a stranger's single must not veto twenty
    albums). Every candidate must have been read."""
    if not weighed or not all(c.get("checked") for c in weighed):
        return None
    lead = [c for c in weighed if int(c.get("albums_n") or 0) >= _DOMINANT_ALBUMS]
    if len(lead) != 1:
        return None
    rest = [c for c in weighed if c is not lead[0]]
    if any(int(c.get("albums_n") or 0) or int(c.get("owned_n") or 0) > 1 for c in rest):
        return None
    return lead[0]


def pick_move(
    name: str,
    library: dict,
    current: dict,
    others: list[dict],
    *,
    cut: bool,
    ranks: dict | None = None,
) -> dict | None:
    """Pick the one profile of ``name`` the library vouches for, if it is not
    the followed one.

    ``current`` and each of ``others`` are ``{"id", "name", "releases"}``
    with ``releases`` the profile's titles, or None when they are not known
    whole (an unread profile makes every candidate decline, so a half-read
    followed profile never loses its follow). ``cut`` says the name search
    may hold more same-name profiles than were read. ``ranks`` maps a
    profile id to its place in TIDAL's search answer (1 is first).

    Returns None (nothing to do: no single winner, or the winner is the
    followed profile) or ``{"grade", "winner", "weighed"}``. ``grade`` is
    ``move`` only when nothing argues against acting alone: every candidate
    was read, the winner carries two or more of the library's distinctive
    albums, and following it loses nothing the user has. That last holds
    when the followed profile is empty, or when TIDAL itself ranks the
    winner above it (an artist split across two profiles keeps its main one
    on top, so a small duplicate holding the library's albums never takes a
    living catalog's follow away). Anything else is a ``suggest``.
    """
    cur_id = str(current.get("id") or "")
    rows = [current, *[o for o in others if str(o.get("id") or "") != cur_id]]
    weighed = weigh(name, library, rows)
    winner = clear_winner(weighed) or _dominant(weighed)
    if winner is None or str(winner.get("id") or "") == cur_id:
        return None
    ranks = ranks or {}
    far = 1 << 30
    outranks = int(ranks.get(str(winner.get("id")), far)) < int(ranks.get(cur_id, far))
    nothing_lost = current.get("releases") == [] or outranks
    strong = int(winner.get("albums_n") or 0) >= _MOVE_ALBUMS
    grade = MOVE if strong and not cut and nothing_lost else SUGGEST
    return {"grade": grade, "winner": winner, "weighed": weighed}


def evidence_sig(
    name: str,
    library: dict,
    profile_titles: Iterable[str] | None,
    *,
    catalog_whole: bool,
    tag_ids: Iterable[str] = frozenset(),
) -> str:
    """A short fingerprint of a verdict's inputs: the same inputs give the
    same string, so a stored verdict stays good until one of them changes."""
    albums, songs, _ = library_albums(name, library)
    titles = None if profile_titles is None else sorted({title_key(str(t)) for t in profile_titles} - {""})
    parts = [
        name_key(name),
        "|".join(sorted(albums)),
        "|".join(sorted(songs)),
        "-" if titles is None else "|".join(titles),
        "1" if catalog_whole else "0",
        _settled_tag_id(tag_ids),
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]
