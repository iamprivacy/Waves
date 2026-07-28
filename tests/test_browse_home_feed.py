"""Browse landing: the V2 home feed's personalized shelves.

Pins the rules ``_home_v2_rows`` adds on top of the shared row builder:
- rows parse through the REAL tidalapi V2 category parser, tolerantly: an
  unknown module type drops that row, never the whole feed,
- MIX rows fall away whole (they parse to MixV2, which tidaler's download
  path silently rejects, so a card for one would be a dead button),
- no row carries a ``more`` link or paging handle: the feed's view-all
  handles are V2 ``home/...`` paths the v1 page drill-in cannot open,
- ``_browse_root`` dedupes home rows by title against the editorial rows
  already inlined (first copy wins),
- ``_browse_root`` also dedupes rows by CONTAINMENT: TIDAL serves one set of
  covers under several headlines ("New Albums" / "Suggested new albums for
  you" / "New releases for you"), the smaller rows being strict subsets of
  the largest, so a contained row drops and the row carrying the most of the
  set survives.
"""

from __future__ import annotations

from threading import Lock
from types import SimpleNamespace

import tidalapi

from tidaler.waves_ui.backend import WavesBridge

# ----- fixture: a small but shape-faithful home/feed/static payload ---------


class _J(dict):
    """Fixture JSON: any key a tidalapi parser subscripts but the test does
    not care about reads as None (the parsers guard the Nones they get)."""

    def __missing__(self, key):
        return None


# No "type" on purpose: V2 home-feed artists ship without a role, tidalapi
# leaves Artist.roles as None, and get_album_artists must treat that as a
# main credit instead of crashing (which silently dropped every album row).
_ARTIST = {"id": 7, "name": "Aurelia"}

_ALBUM = _J(
    {
        "id": 101,
        "title": "Meridian Line",
        "numberOfTracks": 10,
        "releaseDate": "2024-05-01",
        "audioQuality": "LOSSLESS",
        "artist": _ARTIST,
        "artists": [_ARTIST],
    }
)

_PLAYLIST = _J(
    {
        "uuid": "pl-1",
        "title": "Essential Waves",
        "numberOfTracks": 40,
        "numberOfVideos": 0,
        "duration": 9000,
        "type": "EDITORIAL",
    }
)

_TRACK = _J(
    {
        "id": 201,
        "title": "Glass Hour",
        "duration": 200,
        "trackNumber": 1,
        "volumeNumber": 1,
        "audioQuality": "LOSSLESS",
        "artist": _ARTIST,
        "artists": [_ARTIST],
        "album": _J({"id": 101, "title": "Meridian Line", "artists": [_ARTIST]}),
    }
)

_IMG = {"url": "about:blank"}
_MIX = _J(
    {
        "id": "mix-1",
        "title": "Custom Mix 1",
        "subTitle": "",
        "mixType": "DAILY_MIX",
        "master": False,
        "images": {"SMALL": _IMG, "MEDIUM": _IMG, "LARGE": _IMG},
        "detailImages": {"SMALL": _IMG, "MEDIUM": _IMG, "LARGE": _IMG},
        "titleTextInfo": {"text": "Custom Mix 1", "color": "#fff"},
        "subTitleTextInfo": {"text": "", "color": "#fff"},
    }
)


def _feed() -> dict:
    def more(row):
        return {**row, "showMore": {"apiPath": "home/view-all/xyz", "title": "View all"}}

    return {
        "items": [
            more(
                {
                    "type": "HORIZONTAL_LIST",
                    "moduleId": "m1",
                    "title": "Custom mixes",
                    "items": [{"type": "MIX", "data": _MIX}],
                }
            ),
            more(
                {
                    "type": "HORIZONTAL_LIST",
                    "moduleId": "m2",
                    "title": "Essentials to explore",
                    "items": [{"type": "PLAYLIST", "data": _PLAYLIST}],
                }
            ),
            # A module type tidalapi has no parser for: must drop alone.
            {"type": "PROMO_BANNER_V9", "moduleId": "m3", "title": "Shiny"},
            more(
                {
                    "type": "TRACK_LIST",
                    "moduleId": "m4",
                    "title": "Recommended new tracks",
                    "items": [{"type": "TRACK", "data": _TRACK}],
                }
            ),
            more(
                {
                    "type": "HORIZONTAL_LIST",
                    "moduleId": "m5",
                    "title": "Albums you'll enjoy",
                    "items": [{"type": "ALBUM", "data": _ALBUM}],
                }
            ),
        ]
    }


def _offline_session(payload: dict) -> tidalapi.Session:
    session = tidalapi.Session(tidalapi.Config())
    session.request.request = lambda *a, **k: SimpleNamespace(json=lambda: payload)  # type: ignore[method-assign]
    return session


def _rows_bridge(payload: dict) -> WavesBridge:
    b = WavesBridge.__new__(WavesBridge)
    b._objs = {k: {} for k in ("album", "artist", "track", "playlist", "video", "mix")}
    b._objs_max = 50
    b._objs_lock = Lock()
    b._browse_lock = Lock()
    b.tidal = SimpleNamespace(session=_offline_session(payload))
    return b


def test_home_feed_rows_parse_drop_mixes_and_carry_no_more():
    rows = _rows_bridge(_feed())._home_v2_rows()
    titles = [r["title"] for r in rows]
    # The mix row and the unknown module are gone; the rest survive in order.
    assert titles == ["Essentials to explore", "Recommended new tracks", "Albums you'll enjoy"]
    kinds = {r["title"]: r["rowKind"] for r in rows}
    assert kinds["Recommended new tracks"] == "tracks"
    assert kinds["Essentials to explore"] == "cards"
    for r in rows:
        assert r["more"] == ""
        for key in ("data", "total", "offset", "modType"):
            assert key not in r
    # The cards normalized through the shared builders (ids intact).
    by_title = {r["title"]: r for r in rows}
    assert by_title["Essentials to explore"]["items"][0]["kind"] == "playlist"
    assert by_title["Essentials to explore"]["items"][0]["id"] == "pl-1"
    assert by_title["Albums you'll enjoy"]["items"][0]["kind"] == "album"
    assert by_title["Albums you'll enjoy"]["items"][0]["id"] == "101"


def _card(kind: str, cid: str) -> dict:
    return {"kind": kind, "id": cid}


def test_browse_root_dedupes_home_rows_by_title(monkeypatch):
    b = WavesBridge.__new__(WavesBridge)
    # Explore contributes no chips or quick links; For You contributes one row.
    monkeypatch.setattr(b, "_browse_fetch", lambda *a: SimpleNamespace(categories=[]), raising=False)
    monkeypatch.setattr(
        b,
        "_page_rows",
        lambda page: [
            {"rowKind": "cards", "title": "Essentials to explore", "items": [_card("playlist", "p1")], "more": ""}
        ],
        raising=False,
    )
    monkeypatch.setattr(
        b,
        "_home_v2_rows",
        lambda: [
            {"rowKind": "cards", "title": "Essentials to Explore", "items": [_card("playlist", "p2")], "more": ""},
            {"rowKind": "cards", "title": "Popular playlists on TIDAL", "items": [_card("playlist", "p3")], "more": ""},
        ],
        raising=False,
    )
    payload = b._browse_root()
    titles = [r["title"] for r in payload["sections"]]
    # Case-insensitive dedupe: the For You copy won, the new shelf appended.
    assert titles == ["Essentials to explore", "Popular playlists on TIDAL"]
    assert payload["sections"][0]["items"] == [_card("playlist", "p1")]


def _row(title: str, ids, kind: str = "cards", **extra) -> dict:
    return {
        "rowKind": kind,
        "title": title,
        "items": [_card("track" if kind == "tracks" else "album", i) for i in ids],
        "more": "",
        **extra,
    }


def test_browse_root_drops_rows_contained_in_a_bigger_row(monkeypatch):
    b = WavesBridge.__new__(WavesBridge)
    monkeypatch.setattr(b, "_browse_fetch", lambda *a: SimpleNamespace(categories=[]), raising=False)
    big = [f"a{n}" for n in range(25)]
    monkeypatch.setattr(
        b,
        "_page_rows",
        lambda page: [
            # The shape this account actually returns: an editorial row whose
            # albums all reappear in a bigger personalized row further down.
            _row("New Albums", big[:12], more="pages/new_albums"),
            _row("New releases for you", big, data="path/x", total=40, offset=25, modType="ALBUM_LIST"),
            _row("Suggested new albums for you", big[5:15]),
            # Overlapping but not contained (5 of 12 shared): two real shelves.
            _row("The Hits", big[:5] + [f"h{n}" for n in range(7)]),
            # Too small to read as a duplicate shelf: kept though contained.
            _row("Featured", big[:3]),
            # Same ids as a cards row is a different presentation, not a dupe.
            _row("New tracks", big[:10], kind="tracks"),
        ],
        raising=False,
    )
    monkeypatch.setattr(b, "_home_v2_rows", lambda: [], raising=False)
    titles = [r["title"] for r in b._browse_root()["sections"]]
    assert titles == ["New releases for you", "The Hits", "Featured", "New tracks"]


def test_browse_root_keeps_the_first_of_two_identical_rows():
    rows = [_row("New Albums", "abcd"), _row("Suggested new albums", "dcba")]
    kept = WavesBridge._drop_contained_rows(rows)
    # Mutual containment: exactly one row survives, and it is the first.
    assert [r["title"] for r in kept] == ["New Albums"]


def test_browse_root_drops_a_row_contained_in_one_that_is_itself_dropped():
    # A ⊆ B ⊆ C: only the row carrying the whole set is left, whatever the
    # order the chain arrives in.
    rows = [_row("B", "abcdef"), _row("A", "abcd"), _row("C", "abcdefgh")]
    assert [r["title"] for r in WavesBridge._drop_contained_rows(rows)] == ["C"]
