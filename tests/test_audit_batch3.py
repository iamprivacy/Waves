"""Batch 3 of the 2026-08-02 audit: rollup stranding and transient failures
cached as authoritative (findings 13-22).

Same hermetic pattern as test_folder_rollup.py: the real unbound methods are
bound onto minimal stubs, no Qt app or network session.
"""

from __future__ import annotations

from threading import Lock
from types import SimpleNamespace

from tidalapi.album import Album

from tidaler.waves_ui.backend import _LIBRARY_PAGE, WavesBridge


class _Signal:
    def __init__(self):
        self.emits: list = []

    def emit(self, *args):
        self.emits.append(args if len(args) != 1 else args[0])


class _InlinePool:
    @staticmethod
    def start(worker):
        worker.fn()


# ---- finding 14: artist rollup must credit EVERY group holding a track ------


class _ArtistBumpStub:
    _bump_artist_group = WavesBridge._bump_artist_group

    def __init__(self, groups: dict):
        self._artist_groups = groups
        self._artist_lock = Lock()
        self.downloadProgress = _Signal()
        self.downloadState = _Signal()


def _agroup(keys):
    return {"keys": set(keys), "done": set(), "failed": set(), "prog": {}}


def test_a_shared_album_credits_every_artist_group():
    stub = _ArtistBumpStub({"artA": _agroup(["a1", "shared"]), "artB": _agroup(["shared", "b1"])})
    stub._bump_artist_group("a1", None, "done")
    stub._bump_artist_group("shared", None, "done")
    # A is finished and dropped; B must ALSO have been credited with the
    # shared album (a first-match lookup left B one member short forever).
    assert ("artA", "done") in stub.downloadState.emits
    assert stub._artist_groups["artB"]["done"] == {"shared"}
    stub._bump_artist_group("b1", None, "done")
    assert stub.downloadState.emits[-1] == ("artB", "done")
    assert stub._artist_groups == {}


# ---- finding 15: a block-gated download must not strand a lit button --------


class _BlockGateStub:
    _download = WavesBridge._download

    def __init__(self):
        self._logged_in = True
        self.downloadState = _Signal()
        self.statuses: list = []

    def _set_status(self, text):
        self.statuses.append(text)

    def _download_gate(self):
        return "block"


def test_the_block_gate_returns_the_button_to_idle():
    stub = _BlockGateStub()
    # The refetch path emits "running" before dispatching here; the block
    # branch must hand back "" or that button is dead for the session.
    stub._download(object(), "album", "X", "tpl", True, "alb-1")
    assert stub.downloadState.emits == [("alb-1", "")]


class _DismissStub:
    dismissDownloadFolderNudge = WavesBridge.dismissDownloadFolderNudge

    def __init__(self, pending):
        self._pending_lock = Lock()
        self._pending_downloads = pending
        self.downloadState = _Signal()


def test_dismissing_the_nudge_clears_stashed_buttons():
    stub = _DismissStub([("alb-1", lambda: None), ("", lambda: None)])
    stub.dismissDownloadFolderNudge()
    assert stub._pending_downloads == []
    assert stub.downloadState.emits == [("alb-1", "")]


# ---- finding 16: a partially failed artist page is never cached -------------


class _FlakyArtist:
    def __init__(self, albums_fail=False):
        self.name = "Art"
        self._albums_fail = albums_fail

    def get_bio(self):
        return ""

    def get_albums(self):
        if self._albums_fail:
            raise RuntimeError("429")
        return [SimpleNamespace(id="al1")]

    def get_ep_singles(self):
        return [SimpleNamespace(id="ep1")]

    def get_top_tracks(self, limit=10):
        return []


class _LoadArtistStub:
    loadArtist = WavesBridge.loadArtist

    def __init__(self, artist, cached=None):
        self._artist = artist
        self._artist_cache = dict(cached or {})
        self._artist_loading: set = set()
        self._browse_gen = 0
        self.threadpool = _InlinePool()
        self.artistLoaded = _Signal()
        self.remembered: list = []
        self.saved = 0

    def _set_status(self, text):
        pass

    def _set_busy(self, on):
        pass

    def _get_artist(self, artist_id):
        return self._artist

    def _dedup_albums(self, albums):
        return albums

    def _dedup_tracks(self, tracks):
        return tracks

    def _album_dict(self, a):
        return {"id": a.id}

    def _track_dict(self, t):
        return {"id": t.id}

    def _remember_artist_page(self, artist_id, payload):
        self.remembered.append(payload)
        self._artist_cache[artist_id] = payload

    def _save_page_cache(self):
        self.saved += 1


def test_a_partially_failed_artist_page_shows_but_is_not_cached():
    stub = _LoadArtistStub(_FlakyArtist(albums_fail=True))
    stub.loadArtist("a1")
    # First load: still shown (EPs made it), but never cached or persisted.
    assert stub.artistLoaded.emits and stub.artistLoaded.emits[-1]["eps"] == [{"id": "ep1"}]
    assert stub.remembered == [] and stub.saved == 0


def test_a_partially_failed_revalidate_never_wipes_the_grid_on_screen():
    good = {
        "id": "a1",
        "name": "Art",
        "art": "",
        "bio": "",
        "albums": [{"id": "al1"}],
        "eps": [{"id": "ep1"}],
        "tracks": [],
    }
    stub = _LoadArtistStub(_FlakyArtist(albums_fail=True), cached={"a1": good})
    stub.loadArtist("a1")
    # The cached page is emitted, then the gutted refetch must be dropped:
    # no refresh emit (which would clear the album grid), cache untouched.
    assert stub.artistLoaded.emits == [good]
    assert stub._artist_cache["a1"] == good and stub.saved == 0


def test_a_complete_artist_page_still_caches():
    stub = _LoadArtistStub(_FlakyArtist())
    stub.loadArtist("a1")
    assert stub.saved == 1 and stub.remembered[-1]["albums"] == [{"id": "al1"}]


# ---- finding 17: a failed track fetch must not wipe learned membership ------


class _OwnershipSpy:
    def __init__(self):
        self.replaced: list = []

    def record_members_replace(self, album_id, ids):
        self.replaced.append((album_id, ids))


class _AlbumTracksStub:
    loadAlbumTracks = WavesBridge.loadAlbumTracks

    def __init__(self, album):
        self._album_tracks_cache: dict = {}
        self._objs = {"album": {"alb1": album}, "track": {}}
        self.threadpool = _InlinePool()
        self._ownership = _OwnershipSpy()
        self.collectionMembershipChanged = _Signal()
        self.albumTracksLoaded = _Signal()
        self.cached: list = []

    def _remember(self, bucket, key, obj):
        self._objs[bucket][key] = obj

    def _remember_album_tracks(self, album_id, out):
        self.cached.append(album_id)


def _track(tid):
    return SimpleNamespace(full_name=f"T{tid}", id=tid, duration=60, popularity=1, explicit=False)


def test_a_failed_album_track_fetch_keeps_the_stored_membership():
    class _Boom:
        def tracks(self):
            raise RuntimeError("fetch failed")

    stub = _AlbumTracksStub(_Boom())
    stub.loadAlbumTracks("alb1")
    # The destructive replace (unconditional DELETE) must not run on failure.
    assert stub._ownership.replaced == [] and stub.cached == []
    assert stub.albumTracksLoaded.emits == [("alb1", [])]


def test_a_successful_album_track_fetch_still_records_membership():
    stub = _AlbumTracksStub(SimpleNamespace(tracks=lambda: [_track("t1"), _track("t2")]))
    stub.loadAlbumTracks("alb1")
    assert stub._ownership.replaced == [("alb1", ["t1", "t2"])]
    assert stub.cached == ["alb1"]


# ---- findings 19+20: favourites pagination and failure caching --------------


class _Favorites:
    """Windows keyed by offset; short windows mid-list mimic tidalapi dropping
    unavailable items inside a window."""

    def __init__(self, windows: dict, count=None, fail=False):
        self._windows = windows
        self._count = count
        self._fail = fail

    def albums(self, limit, offset):
        if self._fail:
            raise RuntimeError("network down")
        return self._windows.get(offset, [])

    def get_albums_count(self):
        if self._count is None:
            raise RuntimeError("no count")
        return self._count


class _FavStub:
    _favorite_ids = WavesBridge._favorite_ids
    _FAV_IDS_TTL = WavesBridge._FAV_IDS_TTL

    def __init__(self, favorites):
        self._fav_ids: dict = {}
        self.tidal = SimpleNamespace(session=SimpleNamespace(user=SimpleNamespace(favorites=favorites)))


def _fav_items(ids):
    return [SimpleNamespace(id=i) for i in ids]


def test_a_short_window_does_not_truncate_the_favourites_set():
    # 150 favourites, but the first window comes back short (unavailable items
    # dropped): completion must come from the count, not the window length.
    windows = {
        0: _fav_items(range(0, 90)),
        _LIBRARY_PAGE: _fav_items(range(100, 150)),
    }
    stub = _FavStub(_Favorites(windows, count=150))
    ids = stub._favorite_ids("albums")
    assert len(ids) == 140 and "149" in ids, "the second window must still be fetched"


def test_a_failed_first_favourites_load_is_not_cached():
    stub = _FavStub(_Favorites({}, fail=True))
    assert stub._favorite_ids("albums") == set()
    assert stub._fav_ids == {}, "an empty partial set must not sit behind the TTL"
    # The next call (network healed) fetches fresh instead of serving the blip.
    stub.tidal.session.user.favorites = _Favorites({0: _fav_items(["x"])}, count=1)
    assert stub._favorite_ids("albums") == {"x"}


# ---- finding 21: a failed first library load is not cached or persisted -----


class _LoadLibStub:
    loadLibrary = WavesBridge.loadLibrary
    _lib_status = WavesBridge._lib_status
    _lib_count = staticmethod(WavesBridge._lib_count)

    def __init__(self, page=None, fail=False):
        self._logged_in = True
        self._lib_gen = 0
        self._lib_cache: dict = {}
        self._lib_loading: set = set()
        self._lib_reval_ts: dict = {}
        self.threadpool = _InlinePool()
        self.libraryLoaded = _Signal()
        self.statuses: list = []
        self.saved = 0
        self._page = page
        self._fail = fail

    def _set_busy(self, on):
        pass

    def _set_status(self, text):
        self.statuses.append(text)

    def _library_page(self, category, offset, limit, order_override=None):
        if self._fail:
            raise RuntimeError("first load failed")
        return self._page

    def _save_page_cache(self):
        self.saved += 1


def test_a_failed_first_library_load_is_published_but_never_cached():
    stub = _LoadLibStub(fail=True)
    stub.loadLibrary("albums")
    assert stub.libraryLoaded.emits == [("albums", [], False)]
    assert stub._lib_cache == {} and stub.saved == 0, "the next tab visit must retry cold"


def test_a_successful_first_library_load_still_caches():
    stub = _LoadLibStub(page=([{"id": "r1"}], True))
    stub.loadLibrary("albums")
    assert stub._lib_cache["albums"]["items"] == [{"id": "r1"}]
    assert stub.saved == 1


# ---- finding 22: a partial discography scan must refuse to act --------------


def _release(aid):
    a = Album.__new__(Album)
    a.id = aid
    a.name = f"R{aid}"
    a.artist = SimpleNamespace(name="Art", id=1)
    a.artists = [a.artist]
    return a


class _ReleasesStub:
    _artist_releases = WavesBridge._artist_releases

    def __init__(self, prefs):
        self._prefs = prefs

    def _waves_pref_bool(self, key):
        return bool(self._prefs.get(key, False))


class _PartialArtist:
    def get_albums(self):
        raise RuntimeError("429")

    def get_ep_singles(self):
        return [_release("ep1")]


def test_a_failed_release_source_marks_the_scan_incomplete():
    stub = _ReleasesStub({"disco_albums": True, "disco_eps": True})
    own, _guest, complete = stub._artist_releases(_PartialArtist())
    assert [a.id for a in own] == ["ep1"]
    assert complete is False


def test_a_clean_scan_is_complete():
    stub = _ReleasesStub({"disco_eps": True})
    artist = SimpleNamespace(get_ep_singles=lambda: [_release("ep1")], get_albums=lambda: [])
    own, _guest, complete = stub._artist_releases(artist)
    assert complete is True and [a.id for a in own] == ["ep1"]


def test_a_disabled_source_failing_cannot_mark_incomplete():
    stub = _ReleasesStub({"disco_eps": True})  # albums source off
    own, _guest, complete = stub._artist_releases(_PartialArtist())
    assert complete is True and [a.id for a in own] == ["ep1"]


class _DownloadArtistStub:
    downloadArtist = WavesBridge.downloadArtist
    _ffmpeg_gate_holds = WavesBridge._ffmpeg_gate_holds
    _stash_pending_download = WavesBridge._stash_pending_download

    def __init__(self):
        self._dl = object()
        self._ffmpeg_gate_bypassed = True
        self._pending_lock = Lock()
        self._pending_downloads: list = []
        self._artist_groups: dict = {}
        self._artist_lock = Lock()
        self.threadpool = _InlinePool()
        self._scan_pool = _InlinePool()
        self.downloadProgress = _Signal()
        self.downloadState = _Signal()
        self.ffmpegMissingBlocked = _Signal()
        self.statuses: list = []

    def _download_gate(self):
        return "ok"

    def _ffmpeg_source_label(self):
        return "system"

    def _set_status(self, text):
        self.statuses.append(text)

    def _gate_reachability(self, retry, media_id):
        return True

    def _get_artist(self, artist_id):
        return object()

    def _artist_releases(self, artist):
        return [], [], False


def test_download_artist_refuses_a_partial_scan():
    stub = _DownloadArtistStub()
    stub.downloadArtist("art1")
    # The button is handed back to idle, nothing is queued, no group is left
    # behind to strand the button at "running".
    assert stub.downloadState.emits == [("art1", "running"), ("art1", "")]
    assert stub._artist_groups == {}
    assert "Could not load the full discography, try again" in stub.statuses
