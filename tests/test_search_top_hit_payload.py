"""The search payload carries TIDAL's top hit, and never counts it.

THE BUG WE ARE FENCING OFF
--------------------------
TIDAL names one best match in every search reply (``topHit``); for a
specific query it is reliably the thing asked for, new single or not.
``search_results_all`` skipped it, so the UI could only pin a result by
re-sorting lists, and "Relevance" had become a popularity sort that buried
a brand-new single under older tracks sharing one word with the query.

HOW THIS STAYS FIXED
--------------------
- ``search_results_all`` carries the first page's ``top_hit`` through.
- ``_top_hit_dict`` tags it with its kind for the row delegate the mixed
  view pins above every section. An artist top hit is dropped: the artist
  strip already leads with that artist. A builder failure loses the pin,
  not the search.
- The status line's count is the per-type lists only; the pin is a pointer
  to a row that is already counted in its own section.
"""

from __future__ import annotations

from types import SimpleNamespace

from tidalapi.album import Album
from tidalapi.artist import Artist
from tidalapi.media import Track, Video
from tidalapi.playlist import Playlist

from tidaler.helper.tidal import search_results_all
from tidaler.waves_ui import backend


def _bridge():
    b = backend.WavesBridge.__new__(backend.WavesBridge)
    b._album_dict = lambda a: {"id": "al", "title": "A"}
    b._track_dict = lambda t: {"id": "tr", "title": "T"}
    b._video_dict = lambda v: {"id": "vi", "title": "V"}
    b._playlist_dict = lambda p: {"id": "pl", "title": "P"}
    return b


def _obj(cls):
    return cls.__new__(cls)


# --------------------------------------------------------------------------- #
# The kind tag
# --------------------------------------------------------------------------- #
def test_album_track_video_and_playlist_top_hits_are_tagged_with_their_kind():
    b = _bridge()
    assert b._top_hit_dict(_obj(Album)) == {"kind": "album", "id": "al", "title": "A"}
    assert b._top_hit_dict(_obj(Track)) == {"kind": "track", "id": "tr", "title": "T"}
    assert b._top_hit_dict(_obj(Video)) == {"kind": "video", "id": "vi", "title": "V"}
    assert b._top_hit_dict(_obj(Playlist)) == {"kind": "playlist", "id": "pl", "title": "P"}


def test_an_artist_top_hit_is_dropped_because_the_strip_already_leads_with_it():
    assert _bridge()._top_hit_dict(_obj(Artist)) is None


def test_no_top_hit_and_unknown_kinds_pin_nothing():
    b = _bridge()
    assert b._top_hit_dict(None) is None
    assert b._top_hit_dict(SimpleNamespace(name="mix-ish")) is None


def test_a_builder_failure_loses_the_pin_not_the_search():
    b = _bridge()

    def boom(_):
        raise RuntimeError("malformed")

    b._album_dict = boom
    assert b._top_hit_dict(_obj(Album)) is None


# --------------------------------------------------------------------------- #
# The count
# --------------------------------------------------------------------------- #
def test_the_result_count_is_the_lists_only_never_the_pin():
    payload = {
        "artists": [1, 2],
        "albums": [1],
        "tracks": [1, 2, 3],
        "videos": [],
        "playlists": [],
        "mixes": [],
        "top": {"kind": "album", "id": "al", "title": "A", "artist": "x"},
    }
    assert backend.WavesBridge._search_total(payload) == 6
    payload["top"] = None
    assert backend.WavesBridge._search_total(payload) == 6


# --------------------------------------------------------------------------- #
# The helper carries the first page's top hit through
# --------------------------------------------------------------------------- #
class _FakeSession:
    def __init__(self):
        self.calls = 0

    def search(self, query, models=None, limit=300, offset=0):
        self.calls += 1
        if offset == 0:
            return {"artists": ["a1"], "albums": ["b1", "b2"], "tracks": ["t1"], "top_hit": "TOP"}
        if offset == limit:
            # a second page: more rows, and TIDAL repeats its top hit
            return {"artists": [], "albums": ["b3"], "tracks": [], "top_hit": "TOP"}
        return {"artists": [], "albums": [], "tracks": [], "top_hit": None}


def test_search_results_all_keeps_the_first_pages_top_hit_beside_the_lists():
    s = _FakeSession()
    r = search_results_all(s, "needle")
    assert r["top_hit"] == "TOP"
    assert r["albums"] == ["b1", "b2", "b3"]  # paging still accumulates the lists
    assert r["artists"] == ["a1"] and r["tracks"] == ["t1"]
    assert s.calls == 3


def test_search_results_all_reports_a_missing_top_hit_as_none():
    class _Empty:
        def search(self, query, models=None, limit=300, offset=0):
            return {"artists": [], "albums": [], "tracks": [], "top_hit": None}

    r = search_results_all(_Empty(), "nothing")
    assert r["top_hit"] is None
    assert r["albums"] == []
