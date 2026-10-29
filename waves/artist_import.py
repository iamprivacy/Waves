"""Library import: resolve a pile of artist NAMES onto TIDAL artists.

Following one artist at a time is the follow dialog's job; this module is
the brain behind "follow everyone in my library" in one action. It takes
plain name rows (whatever the library index lists), asks TIDAL for
candidates through an injected search callable, and sorts every row into
one of five buckets:

* ``matched``   - exactly one TIDAL artist whose normalized name equals the
                  row's normalized name. Safe to pre-check in a review.
* ``fuzzy``     - no exact hit, but at least one candidate scores above the
                  caller's threshold. Carries the scored candidates; a person
                  picks, never this module.
* ``unmatched`` - TIDAL offered nothing usable. These rows go to the tray
                  so the tail of a big import is not lost.
* ``failed``    - the search itself failed (a dropped connection, a server
                  error). Nothing was learned, so these never reach the
                  tray and a later run asks again.
* ``skipped``   - not worth asking about: already followed, an empty name, a
                  'Various Artists' placeholder (its key collides across
                  unrelated compilations), a name the user dismissed, or a
                  duplicate of a name already resolved in this run.

A fuzzy row can be settled by what the library holds under that name: with
a ``catalog`` callable (a TIDAL artist's release titles) and a ``library``
callable (the row's album and track titles), each candidate is weighed by
how much of that catalog it has (``weigh``), and one that alone has any of
it is a match after all (``clear_winner``). The rest stay fuzzy, scored and
best first, so a person picks between namesakes with the evidence in view.

Pure standard library plus waves.matching: no Qt, no tidalapi, and it never
follows anyone itself: it decides, the bridge acts. The search callable owns
pacing and error policy; a search that raises files that row under
``failed`` rather than aborting the run, because a 2,000-artist import must
survive one flaky request. A streak of failures in a row is no longer
flakiness but a lost connection: the run stops and says so (``offline``),
instead of filing every remaining name as failed. ``stop`` ends a run early
(a cancel, a sign-out, a rate limit), returning what was resolved so far.

Candidate rows are plain dicts {"id", "name", "art"}; no catalog object
crosses this seam in either direction.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable, Iterable

from waves.matching import canon, is_various_artists, norm_artist, presence_key

# Below the floor a "candidate" is noise, whatever threshold a caller sets.
DEFAULT_THRESHOLD = 0.85
_FLOOR = 0.6
_MAX_CANDIDATES = 3
# This many failed searches in a row end the run as offline.
_ERROR_STREAK = 5
# A name's own likeness is this much of a candidate's confidence; the rest is
# how much of the library's catalog under that name the candidate has.
_NAME_WEIGHT = 0.2
# A hit that proves little on its own (a song of the library that is one of
# the candidate's singles, a self-titled or a generic album) counts for this
# much of an album.
_WEAK_HIT = 0.5
# Album titles too common to tell namesakes apart.
_GENERIC_TITLES = frozenset(
    {
        "greatest hits",
        "best of",
        "the best of",
        "live",
        "live album",
        "unplugged",
        "acoustic",
        "ep",
        "demo",
        "demos",
        "singles",
        "remixes",
        "untitled",
        "self titled",
        "christmas",
    }
)
# Matched titles a candidate carries for the review to show.
_OWNED_SHOWN = 3


def name_key(name: str) -> str:
    """The key an import compares artist names by: the library's own artist
    key (``norm_artist`` over ``canon``), so a library tagged "Bjork" or
    "Guns N’ Roses" meets TIDAL's "Björk" and "Guns N' Roses" exactly, as
    the library's presence answers already do. Also the key of the tray,
    the dismissals and the names an import looked at."""
    return norm_artist(canon(name or ""))


def _score(wanted_norm: str, candidate_name: str) -> float:
    return difflib.SequenceMatcher(None, wanted_norm, name_key(candidate_name)).ratio()


def title_key(title: str) -> str:
    """The key a library title and a TIDAL release title are compared by: the
    title half of the library's album key, so "Hollow (Deluxe Edition)" and
    "Hollow" meet as the presence answers already let them."""
    return presence_key(title or "", "")[0]


def weigh(name: str, library: dict, candidates: list[dict]) -> list[dict]:
    """Score each candidate against what the library holds under ``name``.

    ``library`` is ``{"albums": [title, ...], "tracks": [title, ...]}``; each
    candidate may carry ``"releases"``, the titles of its albums (and EPs and
    singles) on TIDAL, or None when they could not be read. Returns the
    candidates best first, each with ``conf`` (0-100), ``owned`` (up to three
    of the library's titles it has, as the library spells them), ``owned_n``
    (every hit), ``albums_n`` (the hits that carry weight: the library's
    albums, less self-titled and generic ones) and ``checked`` (whether its
    releases were read at all). ``releases`` is dropped: the review needs the
    verdict, not the catalog.
    """
    wanted = name_key(name)
    albums: dict[str, str] = {}
    for t in library.get("albums") or []:
        k = title_key(str(t))
        if k:
            albums.setdefault(k, str(t))
    songs: dict[str, str] = {}
    for t in library.get("tracks") or []:
        k = title_key(str(t))
        if k and k not in albums:
            songs.setdefault(k, str(t))
    weak_keys = {k for k in albums if k in _GENERIC_TITLES or name_key(albums[k]) == wanted}
    lib_n = len(albums)
    out = []
    for orig in candidates:
        releases = orig.get("releases")
        c = {k: v for k, v in orig.items() if k != "releases"}
        name_part = _score(wanted, str(c.get("name") or ""))
        if releases is None:
            out.append(
                {
                    **c,
                    "conf": round(100 * _NAME_WEIGHT * name_part),
                    "owned": [],
                    "owned_n": 0,
                    "albums_n": 0,
                    "checked": False,
                }
            )
            continue
        keys = {title_key(str(t)) for t in releases}
        keys.discard("")
        strong = [albums[k] for k in albums if k in keys and k not in weak_keys]
        weak = [albums[k] for k in albums if k in keys and k in weak_keys]
        sung = [songs[k] for k in songs if k in keys]
        hits = len(strong) + _WEAK_HIT * (len(weak) + len(sung))
        # Either view can carry it: the share of the library's albums the
        # candidate has (one of one is conclusive), or the sheer count of
        # hits (two of ten still vouch for a big catalog TIDAL lists only
        # part of).
        evidence = max(min(1.0, hits / max(1, lib_n)), 1.0 - 0.5**hits) if hits else 0.0
        out.append(
            {
                **c,
                "conf": round(100 * (_NAME_WEIGHT * name_part + (1 - _NAME_WEIGHT) * evidence)),
                "owned": (strong + weak + sung)[:_OWNED_SHOWN],
                "owned_n": len(strong) + len(weak) + len(sung),
                "albums_n": len(strong),
                "checked": True,
            }
        )
    out.sort(key=lambda c: (-c["conf"], -c["owned_n"], -c.get("score", 0.0)))
    return out


def clear_winner(weighed: list[dict]) -> dict | None:
    """The one candidate the library vouches for, or None: every candidate's
    releases were read, exactly one has any of the library's titles, and it
    has an album of them or at least two of the weak hits (one shared song
    title, or a self-titled album, proves too little on its own)."""
    if not weighed or not all(c.get("checked") for c in weighed):
        return None
    hits = [c for c in weighed if c.get("owned_n")]
    if len(hits) != 1:
        return None
    best = hits[0]
    return best if best.get("albums_n") or int(best.get("owned_n") or 0) >= 2 else None


def _read_titles(catalog, artist_id: str, singles: bool) -> list[str] | None:
    try:
        titles = catalog(artist_id, singles)
    except Exception:
        return None
    return None if titles is None else [str(t) for t in titles]


def _weigh_row(name: str, library: dict, candidates: list[dict], *, catalog, stop) -> tuple[list[dict], dict | None]:
    """Weigh a fuzzy row's candidates against the library: their albums
    first, then (only while no candidate stands alone) their EPs and singles
    too, so a row an album settles costs one read per candidate. Returns
    ``(weighed, winner)``; a stop mid-row ends the reads where they are."""
    titles: dict[str, list[str] | None] = {}
    for c in candidates:
        if stop is not None and stop():
            break
        titles[str(c.get("id"))] = _read_titles(catalog, str(c.get("id")), False)
    rows = [{**c, "releases": titles.get(str(c.get("id")))} for c in candidates]
    weighed = weigh(name, library, rows)
    winner = clear_winner(weighed)
    if winner is None and any(t is not None for t in titles.values()):
        for c in candidates:
            cid = str(c.get("id"))
            if titles.get(cid) is None:
                continue
            if stop is not None and stop():
                break
            more = _read_titles(catalog, cid, True)
            if more is not None:
                titles[cid] = [*titles[cid], *more]
        rows = [{**c, "releases": titles.get(str(c.get("id")))} for c in candidates]
        weighed = weigh(name, library, rows)
        winner = clear_winner(weighed)
    return weighed, winner


def resolve_one(
    name: str,
    candidates: list[dict],
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> dict:
    """Classify one name against its TIDAL candidates.

    Returns {"bucket": "matched"|"fuzzy"|"unmatched", ...}: matched carries
    the winning candidate under "artist"; fuzzy carries "candidates" (scored,
    best first, capped); unmatched carries only the name. Exact means the
    normalized names are equal; when SEVERAL candidates are exact-equal the
    row demotes to fuzzy, because picking between two artists that share a
    name (there are many on TIDAL) is a person's call, not a string's.
    """
    wanted = name_key(name)
    threshold = max(_FLOOR, min(1.0, float(threshold)))
    exact = [c for c in candidates if name_key(str(c.get("name") or "")) == wanted]
    if len(exact) == 1:
        return {"bucket": "matched", "name": name, "artist": exact[0]}
    scored = sorted(
        ({**c, "score": round(_score(wanted, str(c.get("name") or "")), 3)} for c in candidates),
        key=lambda c: c["score"],
        reverse=True,
    )
    # More namesakes than the review shows: the library can rank the ones
    # kept, but cannot vouch that the real one is among them.
    cut = sum(1 for c in scored if c["score"] >= _FLOOR) > _MAX_CANDIDATES
    scored = [c for c in scored[:_MAX_CANDIDATES] if c["score"] >= _FLOOR]
    if exact or (scored and scored[0]["score"] >= threshold):
        return {"bucket": "fuzzy", "name": name, "candidates": scored, **({"cut": True} if cut else {})}
    return {"bucket": "unmatched", "name": name, "candidates": scored}


def resolve(
    rows: Iterable[dict | str],
    *,
    search: Callable[[str], list[dict]],
    followed_ids: Iterable[str] = (),
    followed_names: Iterable[str] = (),
    dismissed_norms: Iterable[str] = (),
    threshold: float = DEFAULT_THRESHOLD,
    progress: Callable[[int, int], None] | None = None,
    stop: Callable[[], bool] | None = None,
    library: Callable[[dict], dict] | None = None,
    catalog: Callable[[str, bool], list[str] | None] | None = None,
) -> dict:
    """Resolve every row through ``search``, returning the four buckets.

    ``rows`` are strings or {"name": ...} dicts (extra keys ride along into
    the result rows). ``followed_ids`` / ``followed_names`` are the current
    follows (dedupe both ways: a matched candidate whose id is already
    followed is as done as a name equal to a followed artist's name).
    ``dismissed_norms`` are tray rows the user already waved off, as
    normalized names; they skip silently so a re-run never nags.
    ``progress(done, total)`` is called after every row, on the caller's
    thread. ``stop()`` is asked before every row; True ends the run and the
    result carries ``"stopped": True``. With both ``library(row)`` (the
    row's album and track titles) and ``catalog(artist_id, singles)`` (a
    candidate's release titles on TIDAL, None when unreadable), a fuzzy row
    is weighed against the library: the one candidate that has any of it is
    matched (``reason`` "library"), the rest stay fuzzy with their
    ``candidates`` scored best first (see ``weigh``).
    """
    rows = [r if isinstance(r, dict) else {"name": r} for r in rows]
    state = {
        "followed_ids": {str(i) for i in followed_ids},
        "followed_norms": {name_key(str(n)) for n in followed_names},
        "dismissed_norms": {str(n) for n in dismissed_norms},
        "seen_norms": set(),
    }
    out: dict = {
        "matched": [],
        "fuzzy": [],
        "unmatched": [],
        "failed": [],
        "skipped": [],
        "stopped": False,
        "offline": False,
    }
    total = len(rows)
    streak = 0
    for done, row in enumerate(rows, start=1):
        if stop is not None and stop():
            out["stopped"] = True
            break
        bucket, resolved = _resolve_row(
            row, state, search=search, threshold=threshold, library=library, catalog=catalog, stop=stop
        )
        out[bucket].append(resolved)
        if bucket == "failed":
            streak += 1
        elif bucket != "skipped":  # a skip asked nothing, so it proves nothing
            streak = 0
        if streak >= _ERROR_STREAK:
            out["stopped"] = out["offline"] = True
            break
        if progress is not None:
            progress(done, total)
    return out


def _skip_reason(name: str, wanted: str, state: dict) -> str:
    if not wanted:
        return "empty"
    if is_various_artists(name):
        return "various-artists"
    if wanted in state["followed_norms"]:
        return "already-followed"
    if wanted in state["dismissed_norms"]:
        return "dismissed"
    if wanted in state["seen_norms"]:
        return "duplicate"
    return ""


def _resolve_row(
    row: dict, state: dict, *, search, threshold: float, library=None, catalog=None, stop=None
) -> tuple[str, dict]:
    """One row's verdict: (bucket, result row). Mutates state["seen_norms"]."""
    name = str(row.get("name") or "")
    wanted = name_key(name)
    reason = _skip_reason(name, wanted, state)
    if reason:
        return "skipped", {**row, "reason": reason}
    state["seen_norms"].add(wanted)
    try:
        candidates = list(search(name) or [])
    except Exception:
        return "failed", {**row, "candidates": [], "reason": "error"}
    verdict = resolve_one(name, candidates, threshold=threshold)
    if verdict["bucket"] == "fuzzy" and library is not None and catalog is not None:
        verdict = _settle_by_library(name, row, verdict, library=library, catalog=catalog, stop=stop)
    if verdict["bucket"] == "matched" and str(verdict["artist"].get("id") or "") in state["followed_ids"]:
        return "skipped", {**row, "reason": "already-followed"}
    return verdict["bucket"], {**row, **{k: v for k, v in verdict.items() if k != "bucket"}}


def _settle_by_library(name: str, row: dict, verdict: dict, *, library, catalog, stop) -> dict:
    """A fuzzy verdict weighed against the library: a clear winner turns it
    into a match, else its candidates come back scored. A library with
    nothing under the name (or one that cannot be read) spends no reads."""
    try:
        lib = dict(library(row) or {})
    except Exception:
        return verdict
    if not (lib.get("albums") or lib.get("tracks")):
        return verdict
    weighed, winner = _weigh_row(name, lib, verdict["candidates"], catalog=catalog, stop=stop)
    if winner is None or verdict.get("cut"):
        return {**verdict, "candidates": weighed}
    artist = {k: winner.get(k) for k in ("id", "name", "art")}
    return {
        "bucket": "matched",
        "name": name,
        "artist": artist,
        "reason": "library",
        "conf": winner["conf"],
        "owned": winner["owned"],
        "owned_n": winner["owned_n"],
    }
