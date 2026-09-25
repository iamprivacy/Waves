"""A My Tidal row carries its library verdict, the way a browse card does.

WHAT THIS FENCES OFF
--------------------
A browse page's album cards are dressed on the worker that built the page
with the answer each card would otherwise ask the bridge for at creation
(see test_browse_card_dressing). A My Tidal category page was not: every
row's pill asked live, one QML->Python call per row on the GUI thread, for a
page whose answers were all knowable before it was emitted. The rows now
arrive with ``lib`` and ``libStamp`` (the publish the verdict came from,
compared by the pill before it trusts the answer), keyed by category since a
category row has no ``kind``. The cached page stays undressed: it is
persisted, and a persisted verdict is a stale one on the next launch.
"""

from __future__ import annotations

from datetime import date
from threading import Lock
from types import SimpleNamespace

from waves.waves_ui.backend import WavesBridge, _album_row_ident


class _Stub:
    _LIBRARY_DRESSED = WavesBridge._LIBRARY_DRESSED
    _dress_library_row = WavesBridge._dress_library_row
    _dress_library_rows = WavesBridge._dress_library_rows
    _dress_panel_rows = WavesBridge._dress_panel_rows

    def __init__(self, stamp=3, fail=False):
        self._library_stamp = stamp
        self._fail = fail
        self.calls: list = []

    def libraryAlbumPresence(self, *args):
        if self._fail:
            raise RuntimeError("no index")
        self.calls.append(("album", args))
        return {"present": True, "local_class": "hires"}

    def libraryTrackPresence(self, *args):
        self.calls.append(("track", args))
        return {"present": False}

    def artistLibraryPresence(self, name):
        self.calls.append(("artist", (name,)))
        return {"present": True, "albums": 2, "tracks": 9}


def _album(**over):
    row = {"id": "a1", "title": "Blue", "artist": "Joni", "year": "1971", "tracks": 10, "duration_sec": 2160}
    row.update(over)
    return row


def test_an_album_row_bakes_the_pill_verdict_and_its_publish():
    s = _Stub(stamp=3)
    (out,) = s._dress_library_rows("albums", [_album()])
    assert out["lib"] == {"present": True, "local_class": "hires"}
    assert out["libStamp"] == 3
    # The pill's own identity, in the pill's own argument order (the row
    # carries no explicit flag, so it asks with the unknown -1).
    assert s.calls == [("album", ("Joni", "Blue", "1971", 10, 2160, -1))]


def test_a_track_row_asks_the_track_index_with_its_release():
    s = _Stub()
    row = {"id": "t1", "title": "River", "artist": "Joni", "album": "Blue", "year": "1971", "duration_sec": 241}
    (out,) = s._dress_library_rows("tracks", [row])
    assert out["lib"] == {"present": False} and out["libStamp"] == 3
    assert s.calls == [("track", ("Joni", "River", "Blue", "1971", 241, -1))]


def test_an_artist_row_bakes_the_strip_rollup():
    s = _Stub()
    (out,) = s._dress_library_rows("artists", [{"id": "ar1", "name": "Joni"}])
    assert out["lib"] == {"present": True, "albums": 2, "tracks": 9}
    assert s.calls == [("artist", ("Joni",))]


def test_the_cached_page_is_left_undressed():
    s = _Stub()
    cached = [_album()]
    out = s._dress_library_rows("albums", cached)
    assert out is not cached and out[0] is not cached[0]
    assert "lib" not in cached[0] and "libStamp" not in cached[0]


def test_other_categories_come_back_as_they_are():
    s = _Stub()
    rows = [{"id": "p1", "kind": "playlist", "title": "Mix"}]
    assert s._dress_library_rows("playlists", rows) is rows
    assert s._dress_library_rows("videos", rows) is rows
    assert s.calls == []


def test_a_row_with_nothing_to_ask_about_is_untouched():
    s = _Stub()
    blank = _album(title="")
    (out,) = s._dress_library_rows("albums", [blank])
    assert out is blank and s.calls == []
    (out,) = s._dress_library_rows("artists", [{"id": "x", "name": ""}])
    assert "lib" not in out


def test_a_publish_landing_mid_verdict_leaves_the_old_stamp_on_the_row():
    """Same order as _dress_card: stamp first, verdict second, so a publish
    in between leaves an old stamp on the row and its pill asks live."""
    s = _Stub(stamp=0)
    verdict = s.libraryAlbumPresence

    def presence_then_publish(*args):
        s._library_stamp = 1
        return verdict(*args)

    s.libraryAlbumPresence = presence_then_publish
    (out,) = s._dress_library_rows("albums", [_album()])
    assert out["libStamp"] == 0, "a verdict from the old index must carry the old stamp"


def test_a_failing_lookup_leaves_the_row_asking_live():
    s = _Stub(fail=True)
    (out,) = s._dress_library_rows("albums", [_album()])
    assert "lib" not in out and "libStamp" not in out


# ---- the emit paths ---------------------------------------------------------


class _Signal:
    def __init__(self):
        self.emits: list = []

    def emit(self, *args):
        self.emits.append(args)


class _InlinePool:
    @staticmethod
    def start(worker, priority: int = 0):
        worker.fn()


class _LoadStub(_Stub):
    loadLibrary = WavesBridge.loadLibrary
    loadMoreLibrary = WavesBridge.loadMoreLibrary
    _lib_status = WavesBridge._lib_status
    _lib_count = staticmethod(WavesBridge._lib_count)

    def __init__(self, page):
        super().__init__(stamp=5)
        self._logged_in = True
        self._lib_gen = 0
        self._lib_cache: dict = {}
        self._lib_loading: set = set()
        self._lib_reval_ts: dict = {}
        self._lib_sort: dict = {}
        self.threadpool = _InlinePool()
        self.libraryLoaded = _Signal()
        self.libraryMore = _Signal()
        self._page = page

    def _set_busy(self, on):
        pass

    def _set_status(self, text):
        pass

    def _library_page(self, category, offset, limit, order_override=None):
        return self._page

    def _save_page_cache(self):
        pass


def test_a_fresh_page_is_emitted_dressed_and_cached_undressed():
    s = _LoadStub(([_album()], True))
    s.loadLibrary("albums")
    cat, rows, more = s.libraryLoaded.emits[-1]
    assert cat == "albums" and rows[0]["libStamp"] == 5 and rows[0]["lib"]["present"] is True
    assert "lib" not in s._lib_cache["albums"]["items"][0]


def test_the_cached_revisit_is_emitted_dressed_too():
    s = _LoadStub(([_album()], True))
    s.loadLibrary("albums")
    s._lib_loading.add("albums")  # keep the revalidate out of the way
    s.loadLibrary("albums")
    _cat, rows, _more = s.libraryLoaded.emits[-1]
    assert rows[0]["libStamp"] == 5
    assert "lib" not in s._lib_cache["albums"]["items"][0]


def test_the_next_page_is_emitted_dressed_and_appended_undressed():
    s = _LoadStub(([_album()], True))
    s.loadLibrary("albums")
    s._page = ([_album(id="a2", title="Hejira")], False)
    s.loadMoreLibrary("albums")
    _cat, rows, _more = s.libraryMore.emits[-1]
    assert rows[0]["title"] == "Hejira" and rows[0]["libStamp"] == 5
    assert all("lib" not in r for r in s._lib_cache["albums"]["items"])
    assert len(s._lib_cache["albums"]["items"]) == 2


# ---- the expanded panels ----------------------------------------------------


def test_a_panel_row_is_dressed_as_a_track_and_a_video_is_left_alone():
    s = _Stub()
    rows = [
        {"id": "t1", "title": "River", "artist": "Joni", "album": "Blue", "year": "1971", "duration_sec": 241},
        {"id": "v1", "kind": "video", "title": "River (live)", "artist": "Joni"},
    ]
    out = s._dress_panel_rows(rows)
    assert out[0]["lib"] == {"present": False} and out[0]["libStamp"] == 3
    assert out[1] is rows[1] and "lib" not in rows[0]
    assert s.calls == [("track", ("Joni", "River", "Blue", "1971", 241, -1))]


def _release():
    joni = SimpleNamespace(name="Joni", id=1, roles=None)
    return SimpleNamespace(
        id="al1",
        name="Blue",
        num_tracks=1,
        release_date=date(1971, 6, 22),
        artists=[joni],
        artist=joni,
        tracks=lambda: [SimpleNamespace(id="t1", name="River", duration=241, popularity=5, explicit=False)],
    )


class _PanelStub(_Stub):
    loadAlbumTracks = WavesBridge.loadAlbumTracks
    _start_album_tracks_fetch = WavesBridge._start_album_tracks_fetch

    def __init__(self, album):
        super().__init__(stamp=5)
        self._album_tracks_cache: dict = {}
        self._prefetch_lock = Lock()
        self._album_tracks_inflight: dict = {}
        self._album_tracks_unrecorded: set = set()
        self._objs = {"album": {"al1": album}, "track": {}}
        self.threadpool = _InlinePool()
        self.albumTracksLoaded = _Signal()

    def _remember(self, kind, key, obj):
        self._objs.setdefault(kind, {})[key] = obj

    def _remember_album_tracks(self, aid, rows):
        self._album_tracks_cache[aid] = rows

    def _record_album_members(self, aid, rows):
        pass


def test_an_expanded_album_names_its_release_on_every_row_and_arrives_dressed():
    s = _PanelStub(_release())
    s.loadAlbumTracks("al1")
    ((aid, rows),) = s.albumTracksLoaded.emits
    r = rows[0]
    assert aid == "al1" and (r["artist"], r["album"], r["year"], r["duration_sec"]) == ("Joni", "Blue", "1971", 241)
    assert r["lib"] == {"present": False} and r["libStamp"] == 5
    # Exactly what the panel's control would have asked live, in its order,
    # the track's own clean flag included.
    assert s.calls == [("track", ("Joni", "River", "Blue", "1971", 241, 0))]
    cached = s._album_tracks_cache["al1"][0]
    assert "lib" not in cached and cached["album"] == "Blue"


def test_a_re_expansion_from_the_session_cache_is_dressed_again():
    s = _PanelStub(_release())
    s.loadAlbumTracks("al1")
    s.loadAlbumTracks("al1")
    emits = s.albumTracksLoaded.emits
    assert len(emits) == 2 and emits[1][1][0]["libStamp"] == 5
    assert "lib" not in s._album_tracks_cache["al1"][0]


def test_a_release_that_cannot_be_named_yields_an_empty_ident():
    assert _album_row_ident(object()) == ("", "", "")
