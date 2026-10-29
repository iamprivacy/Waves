"""The "what's new" release-delta core (Waves-owned, stdlib-only).

Pure functions the follow store (``waves.follows``) builds its baseline logic
on, kept apart from the SQL so the decisions are testable on their own and
any later consumer (an import list, a label feed) reuses the exact same
rules instead of re-implementing them:

- **Edition groups.** A release dict may carry ``sibling_ids``: the other
  editions of the same album that the caller's settings-dependent dedup
  collapsed away. The delta is judged and absorbed per GROUP: a release is new
  only if NO edition of it has been seen, and seeing one edition marks them
  all. Without this, changing the quality cap or explicit preference makes the
  same album resolve to a different edition id and a check would report (and
  auto-download) an old album as new.
- **Seeded baselines.** A follow's first non-empty catalog IS the baseline
  and is absorbed silently; announcing a whole back catalog as "new" on the
  first check is the classic import-list failure. An empty first fetch is
  never trusted as a baseline (it usually means the fetch failed).

Pure functions over plain dicts and id sets; the store keeps its own SQL and
locking. Each release dict needs at least ``id``; everything else rides along.
"""

from __future__ import annotations

import re
from collections.abc import Iterable


def edition_group_ids(release: dict) -> set[str]:
    """Every id identifying ``release``: its own plus any ``sibling_ids`` (the
    other editions of the same album the caller's dedup collapsed away). The
    seen baseline is judged and grown per group so the delta does not depend on
    which edition the caller's current settings happened to keep."""
    ids = {str(release.get("id"))} if release.get("id") is not None else set()
    ids.update(str(s) for s in release.get("sibling_ids") or [] if s is not None and str(s))
    return ids


def all_group_ids(releases: Iterable[dict]) -> set[str]:
    """The union of every release's edition group: the flat id set a seeding
    pass absorbs as the silent baseline."""
    out: set[str] = set()
    for r in releases:
        out |= edition_group_ids(r)
    return out


def split_fresh(releases: Iterable[dict], seen: Iterable[str]) -> tuple[list[dict], set[str]]:
    """Diff ``releases`` against the ``seen`` baseline, per edition group.

    Returns ``(fresh, absorbed)``: the genuinely-new releases in input order,
    and the full id set the caller should add to its persisted baseline (every
    group id of every input release, fresh or not). The working baseline grows
    as the walk proceeds, so two editions of the same new album arriving in one
    batch count once. Releases without an ``id`` are skipped entirely."""
    fresh: list[dict] = []
    absorbed: set[str] = set()
    known = set(seen)
    for r in releases:
        if r.get("id") is None:
            continue
        group = edition_group_ids(r)
        fresh_group = not (group & known)
        known.update(group)
        absorbed.update(group)
        if fresh_group:
            fresh.append(r)
    return fresh, absorbed


# A "(Dolby Atmos)" tail on a twin's title; most twins carry the plain title.
_ATMOS_TAIL = re.compile(r"\s*[(\[]\s*dolby\s+atmos\s*[)\]]\s*$", re.IGNORECASE)


def _twin_key(r: dict) -> tuple[str, str, str, int]:
    title = _ATMOS_TAIL.sub("", str(r.get("title") or "")).casefold().strip()
    artist = str(r.get("artist_name") or r.get("artist") or "").casefold().strip()
    day = str(r.get("listed") or r.get("date") or "")[:10]
    return title, artist, day, int(r.get("tracks") or 0)


def _with_ids(have, more, skip: str = "") -> list[str]:
    """``have`` plus each id of ``more`` it lacks, in order, as strings;
    empty ids and ``skip`` are left out."""
    ids = [str(i) for i in have or [] if i is not None and str(i)]
    for i in more:
        if i is not None and str(i) and str(i) != skip and str(i) not in ids:
            ids.append(str(i))
    return ids


def collapse_atmos_twins(releases: Iterable[dict]) -> list[dict]:
    """One row per release for a feed. TIDAL ships Dolby Atmos as a separate
    release with its own id (quality ``ATMOS``), and an artist page shows it
    as its own row beside the stereo edition on purpose; a feed that did the
    same showed the same album twice, with nothing on the cards to tell them
    apart. A twin (same title, artist, day and track count as a stereo row)
    folds into the stereo row: its ids join that row's ``sibling_ids`` (so
    the baseline covers both and the stereo row is the one announced), its
    own id joins ``atmos_ids``, and a fresh twin keeps the row fresh. An Atmos release with no stereo twin
    stays. Input order is kept; the kept dicts are updated in place."""
    rows = list(releases)
    stereo: dict[tuple, dict] = {}
    for r in rows:
        if str(r.get("quality") or "").upper() != "ATMOS":
            stereo.setdefault(_twin_key(r), r)
    out: list[dict] = []
    for r in rows:
        keeper = stereo.get(_twin_key(r)) if str(r.get("quality") or "").upper() == "ATMOS" else None
        if keeper is None:
            out.append(r)
            continue
        twin_group = (r.get("id"), *(r.get("sibling_ids") or []))
        own = str(keeper.get("id")) if keeper.get("id") is not None else ""
        keeper["sibling_ids"] = _with_ids(keeper.get("sibling_ids"), twin_group, skip=own)
        # The twin's own id, kept apart: a download that wants Dolby Atmos
        # fetches it beside the stereo edition.
        keeper["atmos_ids"] = _with_ids(keeper.get("atmos_ids"), (r.get("id"),))
        # Every id of the twin's own edition group (its clean or explicit
        # sibling too), so a stereo row listed after its twin is known as one.
        keeper["atmos_group_ids"] = _with_ids(keeper.get("atmos_group_ids"), twin_group)
        if r.get("fresh"):
            keeper["fresh"] = True
    return out
