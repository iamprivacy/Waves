"""Busy-latch discipline in the browse/search workers, and sign-out fences.

Two families of holes, both found by the gap round of the 2026-08-29 audit:

* loadArtist, search, _open_url and loadArtistLibrary built their result
  payloads OUTSIDE any try. The dict builders can choke on a partial tidalapi
  object (the code's own _top_hit_dict guard concedes as much), Worker.run
  only logs an escape, and nothing else cleared busy or the status line.
  loadArtist was worst: the artist id stayed in _artist_loading, so the dedup
  guard silently refused every later click on that artist for the whole
  session.

* logout never superseded the in-flight fetch workers. A search still running
  when the user signed out emitted after "Signed out" and refilled the caches
  logout had just cleared with objects bound to the dead session; the
  album-tracks and playlist-tracks workers had no generation guard at all.
"""

from __future__ import annotations

import inspect
from threading import Lock
from types import SimpleNamespace

from waves.waves_ui import backend
from waves.waves_ui.backend import WavesBridge


class _Signal:
    def __init__(self):
        self.emits: list = []

    def emit(self, *args):
        self.emits.append(args)


class _InlinePool:
    @staticmethod
    def start(worker, priority: int = 0):
        worker.fn()


class _StubBase:
    """The attributes every slot under test shares."""

    def __init__(self):
        self.threadpool = _InlinePool()
        self.statuses: list[str] = []
        self.busy: list[bool] = []
        self._browse_gen = 0
        self._search_gen = 0

    def _set_status(self, text):
        self.statuses.append(text)

    def _set_busy(self, on):
        self.busy.append(bool(on))


class _Artist:
    name = "Art"

    def get_bio(self):
        return ""

    def get_albums(self):
        return []

    def get_ep_singles(self):
        return []

    def get_top_tracks(self, limit=10):
        return [SimpleNamespace(id="t1")]

    def get_videos(self, limit=None):
        return []


# --------------------------------------------------------------------------- #
# loadArtist: a choking builder must not strand the artist or latch busy
# --------------------------------------------------------------------------- #
class _LoadArtistStub(_StubBase):
    loadArtist = WavesBridge.loadArtist

    def __init__(self, artist):
        super().__init__()
        self._artist = artist
        self._artist_cache: dict = {}
        self._artist_loading: set = set()
        self.artistLoaded = _Signal()
        self.artistLoadFailed = _Signal()

    def _get_artist(self, artist_id):
        return self._artist

    def _dedup_albums(self, albums):
        return albums

    def _dedup_tracks(self, tracks):
        return tracks

    def _dedup_videos(self, videos):
        return videos

    def _album_dict(self, a):
        return {"id": a.id}

    def _track_dict(self, t):
        raise RuntimeError("partial payload: the builder chokes")


def test_a_choking_builder_never_strands_the_artist():
    stub = _LoadArtistStub(_Artist())

    stub.loadArtist("a1")

    # The failure is REPORTED (status, busy released, QML told), and the
    # dedup set is released so the next click on this artist tries again.
    assert stub._artist_loading == set()
    assert stub.busy == [True, False]
    assert stub.statuses[-1] == "Could not load artist"
    assert stub.artistLoadFailed.emits == [("a1",)]
    assert stub.artistLoaded.emits == []

    # And the retry is actually accepted, not refused by a stale entry.
    stub.loadArtist("a1")
    assert stub.busy == [True, False, True, False]


def test_an_unresolvable_artist_still_reports_failure():
    stub = _LoadArtistStub(_Artist())
    stub._get_artist = lambda artist_id: None

    stub.loadArtist("a1")

    assert stub._artist_loading == set()
    assert stub.busy == [True, False]
    assert stub.statuses[-1] == "Could not load artist"
    assert stub.artistLoadFailed.emits == [("a1",)]


# --------------------------------------------------------------------------- #
# search: a choking builder fails THIS search visibly
# --------------------------------------------------------------------------- #
class _SearchStub(_StubBase):
    search = WavesBridge.search
    _search_total = WavesBridge._search_total

    def __init__(self):
        super().__init__()
        self._logged_in = True
        self._search_cache: dict = {}
        self._objs_lock = Lock()
        self._objs: dict = {"artist": {}, "album": {}, "track": {}, "video": {}, "playlist": {}, "mix": {}}
        self.tidal = SimpleNamespace(session=object())
        self.searchResults = _Signal()
        self.artistMetaLoaded = _Signal()

    def _remember(self, kind, key, obj):
        self._objs[kind][key] = obj

    def _dedup_albums(self, albums):
        return list(albums)

    def _dedup_tracks(self, tracks):
        return list(tracks)

    def _dedup_videos(self, videos):
        return list(videos)

    def _album_dict(self, a):
        raise RuntimeError("partial payload: the builder chokes")


def test_a_choking_search_build_clears_busy_and_says_so(monkeypatch):
    stub = _SearchStub()
    monkeypatch.setattr(backend, "search_results_all", lambda session, needle: {"albums": [SimpleNamespace(id="x")]})

    stub.search("aphex")

    assert stub.busy == [True, False]
    assert stub.statuses[-1] == "Search failed"
    assert stub.searchResults.emits == []
    assert stub._search_cache == {}


# --------------------------------------------------------------------------- #
# _open_url: same discipline for the pasted-link resolver
# --------------------------------------------------------------------------- #
class _OpenUrlStub(_StubBase):
    _open_url = WavesBridge._open_url

    def __init__(self):
        super().__init__()
        self._objs: dict = {"artist": {}, "album": {}}
        self._objs_lock = Lock()
        self.tidal = SimpleNamespace(session=object())
        self.searchResults = _Signal()

    def _album_dict(self, a):
        raise RuntimeError("partial payload: the builder chokes")


def test_a_choking_link_payload_clears_busy(monkeypatch):
    stub = _OpenUrlStub()
    monkeypatch.setattr(backend, "get_tidal_media_type", lambda url: backend.MediaType.ALBUM)
    monkeypatch.setattr(backend, "get_tidal_media_id", lambda url: "42")
    monkeypatch.setattr(backend, "instantiate_media", lambda session, mt, mid: SimpleNamespace(id="42"))

    stub._open_url("https://tidal.com/album/42")

    assert stub.busy == [True, False]
    assert stub.statuses[-1] == "Could not open that link"
    assert stub.searchResults.emits == []


# --------------------------------------------------------------------------- #
# loadArtistLibrary: same discipline for the library-scoped page
# --------------------------------------------------------------------------- #
class _LibraryArtistStub(_StubBase):
    loadArtistLibrary = WavesBridge.loadArtistLibrary

    def __init__(self):
        super().__init__()
        self.artistLoaded = _Signal()

    def _get_artist(self, artist_id):
        artist = _Artist()
        artist.get_albums = lambda: [SimpleNamespace(id="al1")]
        return artist

    def _favorite_ids(self, kind):
        return {"al1"}

    def _dedup_albums(self, albums):
        return list(albums)

    def _dedup_tracks(self, tracks):
        return list(tracks)

    def _album_dict(self, a):
        raise RuntimeError("partial payload: the builder chokes")


def test_a_choking_library_page_clears_busy():
    stub = _LibraryArtistStub()

    stub.loadArtistLibrary("a1")

    assert stub.busy == [True, False]
    assert stub.statuses[-1] == "Could not load artist"
    assert stub.artistLoaded.emits == []


# --------------------------------------------------------------------------- #
# Sign-out fences
# --------------------------------------------------------------------------- #
def test_logout_supersedes_every_inflight_search():
    # The workers guard on _search_gen; logout must bump it or a search still
    # in flight emits after "Signed out" and repoisons the caches logout just
    # cleared. Behavior is pinned at the source because logout touches half
    # the bridge and stubbing it whole would test the stub.
    source = inspect.getsource(WavesBridge.logout)
    assert "_search_gen += 1" in source


class _AlbumTracksStub(_StubBase):
    _start_album_tracks_fetch = WavesBridge._start_album_tracks_fetch

    def __init__(self, album):
        super().__init__()
        self._objs = {"album": {"al1": album}}
        self._prefetch_lock = Lock()
        self._album_tracks_inflight: dict = {"al1": True}
        self._album_tracks_unrecorded: set = set()
        self.albumTracksLoaded = _Signal()
        self.cached: list = []
        self.members: list = []

    def _remember(self, kind, key, obj):
        pass

    def _remember_album_tracks(self, album_id, rows):
        self.cached.append(album_id)

    def _record_album_members(self, album_id, rows):
        self.members.append(album_id)


def test_album_tracks_landing_after_logout_are_dropped():
    stub_holder: list = []

    class _Album:
        def tracks(self):
            # Logout lands mid-fetch: it clears the inflight registrations.
            stub_holder[0]._album_tracks_inflight.clear()
            return [SimpleNamespace(id="t1", name="T", duration=1)]

    stub = _AlbumTracksStub(_Album())
    stub_holder.append(stub)

    stub._start_album_tracks_fetch("al1")

    # Nothing built on the dead session may be cached, recorded, or emitted.
    assert stub.cached == [] and stub.members == []
    assert stub.albumTracksLoaded.emits == []


class _PlaylistTracksStub(_StubBase):
    loadPlaylistTracks = WavesBridge.loadPlaylistTracks

    def __init__(self):
        super().__init__()
        self._objs = {"playlist": {"p1": object()}}
        self.playlistTracksLoaded = _Signal()

    def _remember(self, kind, key, obj):
        pass


def test_playlist_tracks_landing_after_logout_are_dropped(monkeypatch):
    stub = _PlaylistTracksStub()

    def fetch(obj):
        stub._browse_gen += 1  # logout mid-fetch
        return [], True

    monkeypatch.setattr(backend, "_all_playlist_items", fetch)

    stub.loadPlaylistTracks("p1")

    assert stub.playlistTracksLoaded.emits == []
