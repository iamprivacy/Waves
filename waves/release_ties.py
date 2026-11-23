"""Which releases on a shared TIDAL profile belong to the library's artist.

Some TIDAL artist profiles hold several unrelated artists with the same name:
every release there carries the same artist id, so the follow alone cannot
tell them apart. The library pins some of the releases (its UPCs and its
album titles); a release is tied to those when it carries the same copyright
line or shares a co-credited artist with one of them. On a profile where many
releases stay untied, the untied ones are set apart: kept, but out of the
feed, notifications and downloads.

Pure: no Qt, no tidalapi, no logging. Rows are stored release rows (``id``,
``title``, ``copyright``, ``upc``, ``artists`` as ``[{id, name}, ...]``).

Measured on 25 live profiles (11-22-2026): on clean profiles these two rules
leave at most 10% of releases untied (5 of 52); on a shared profile 60% (21 of
35). The thresholds below sit in that gap.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field

from .artist_import import library_albums, title_key

# A profile counts as shared only with this many pinned releases (the
# library must vouch for it), this many untied ones, and this share untied.
MIN_PINNED = 2
MIN_UNTIED = 4
MIN_UNTIED_SHARE = 0.30
# Rows stored before copyright was kept carry none; the profile is judged
# only once most rows carry one, so an old row never reads as untied.
MIN_COPYRIGHT_SHARE = 0.80
# TIDAL's Various Artists profile ties nothing.
VARIOUS_IDS = frozenset({"2935"})
_VARIOUS_NAMES = frozenset({"various artists", "various"})


def norm_copyright(line: str) -> str:
    """A copyright line with its marks, years and punctuation dropped, so
    "(C) 2020 Jim Ward" and "℗ 2022 Jim Ward" meet. Letters of every script
    stay (a Cyrillic or Japanese label line ties like a Latin one); accents
    go, so "Beyoncé" and "Beyonce" meet."""
    t = unicodedata.normalize("NFKD", line or "")
    # Kana voicing marks (U+3099/U+309A) are part of the letter, not an accent.
    t = "".join(ch for ch in t if not unicodedata.combining(ch) or ch in "\u3099\u309a")
    t = unicodedata.normalize("NFC", t).lower()
    t = re.sub(r"[℗©]|\(p\)|\(c\)|\bp\b|\bc\b", " ", t)
    t = re.sub(r"\b(19|20)\d\d\b", " ", t)
    t = re.sub(r"[\W_]+", " ", t)
    return " ".join(t.split())


def norm_upc(upc) -> str:
    return str(upc or "").strip().lstrip("0")


def _credits(row: dict, profile_id: str) -> set[str]:
    out = set()
    for a in row.get("artists") or []:
        aid = str((a or {}).get("id") or "")
        name = str((a or {}).get("name") or "").strip().lower()
        if aid and aid != profile_id and aid not in VARIOUS_IDS and name not in _VARIOUS_NAMES:
            out.add(aid)
    return out


@dataclass(frozen=True)
class Ties:
    """What ties a release to the library's artist on one profile."""

    profile_id: str
    albums: frozenset = field(default_factory=frozenset)
    upcs: frozenset = field(default_factory=frozenset)
    copies: frozenset = field(default_factory=frozenset)
    credits: frozenset = field(default_factory=frozenset)

    def pins(self, row: dict) -> bool:
        upc = norm_upc(row.get("upc"))
        return bool(upc and upc in self.upcs) or title_key(str(row.get("title") or "")) in self.albums

    def keeps(self, row: dict) -> bool:
        """Pinned by the library, or tied to a pinned release."""
        if self.pins(row):
            return True
        copy = norm_copyright(str(row.get("copyright") or ""))
        if copy and copy in self.copies:
            return True
        return bool(_credits(row, self.profile_id) & self.credits)

    def to_json(self) -> str:
        return json.dumps(
            {
                "profile_id": self.profile_id,
                "albums": sorted(self.albums),
                "upcs": sorted(self.upcs),
                "copies": sorted(self.copies),
                "credits": sorted(self.credits),
            },
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, text: str) -> Ties | None:
        try:
            d = json.loads(text or "")
        except ValueError:
            return None
        if not isinstance(d, dict) or not d.get("profile_id"):
            return None
        return cls(
            str(d["profile_id"]),
            frozenset(d.get("albums") or ()),
            frozenset(d.get("upcs") or ()),
            frozenset(d.get("copies") or ()),
            frozenset(d.get("credits") or ()),
        )


@dataclass(frozen=True)
class Verdict:
    """``state`` is ``shared``, ``clean`` or ``unknown`` (too little to judge
    yet). ``apart`` is the release ids set apart (empty unless shared)."""

    state: str
    apart: frozenset
    ties: Ties | None
    sig: str

    @property
    def shared(self) -> bool:
        return self.state == "shared"


def _sig(state: str, apart, ties: Ties | None) -> str:
    h = hashlib.blake2b(digest_size=8)
    h.update(state.encode())
    h.update(json.dumps(sorted(apart)).encode())
    h.update((ties.to_json() if ties else "").encode())
    return h.hexdigest()


def judge(profile_id: str, name: str, rows: list[dict], library: dict, upcs=()) -> Verdict:
    """Judge one followed profile from its stored rows and what the library
    holds under ``name`` (``{"albums": [...], "tracks": [...]}``, as
    ``artist_import.library_albums`` reads it) and the library's UPCs under
    that name."""
    pid = str(profile_id)
    albums, _songs, weak = library_albums(name, library or {})
    base = Ties(pid, frozenset(k for k in albums if k not in weak), frozenset(norm_upc(u) for u in upcs if norm_upc(u)))
    rows = [r for r in rows if str(r.get("id") or "")]
    if not rows:
        return Verdict("unknown", frozenset(), None, _sig("unknown", (), None))
    pinned = [r for r in rows if base.pins(r)]
    # One album in two editions (standard and deluxe) is one voucher.
    pinned_albums = {title_key(str(r.get("title") or "")) for r in pinned}
    pinned_albums.discard("")  # a pin with no title names no album
    # A line with nothing left once its marks and years go ("℗ 2020") can
    # tie nothing, so it counts as no line.
    with_copy = sum(1 for r in rows if norm_copyright(str(r.get("copyright") or "")))
    if len(pinned_albums) < MIN_PINNED or with_copy < MIN_COPYRIGHT_SHARE * len(rows):
        return Verdict("unknown", frozenset(), None, _sig("unknown", (), None))
    copies = {norm_copyright(str(r.get("copyright") or "")) for r in pinned} - {""}
    cocredits: set[str] = set()
    for r in pinned:
        cocredits |= _credits(r, pid)
    ties = Ties(pid, base.albums, base.upcs, frozenset(copies), frozenset(cocredits))
    untied = frozenset(str(r["id"]) for r in rows if not ties.keeps(r))
    if len(untied) >= MIN_UNTIED and len(untied) >= MIN_UNTIED_SHARE * len(rows):
        return Verdict("shared", untied, ties, _sig("shared", untied, ties))
    return Verdict("clean", frozenset(), None, _sig("clean", (), None))
