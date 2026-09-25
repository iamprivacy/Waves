"""Every My Tidal tab gets DOWNLOAD ALL, like Tracks (issue #43).

``downloadFavoriteAlbums`` pages every favourite album and runs the sweep the
playlist page's "Download full albums" runs, then queues one batch under the
``fav:albums`` rollup. ``downloadFavoriteArtists`` pages every favourite
artist and starts each one's own discography download; its ``fav:artists``
rollup counts whole artists (members keyed ``artist:<id>`` so an artist id is
never mistaken for an album or track id), settling each as its discography
does, including a discography scan that queued nothing or failed. The
stranded-group reaper must not eat that rollup while its artists' groups are
alive, since a discography holds no queue row of its own.

Playlists, Mixes and Videos queue their rows as they are. Playlists takes
every playlist My Tidal lists, the ones inside folders at any depth included,
and refuses a folder walk a rate limit cut short.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

import pytest

from waves.waves_ui.backend import (
    _ARTIST_ROLLUP_MEMBER,
    _FAV_ALBUMS_GROUP_ID,
    _FAV_ARTISTS_GROUP_ID,
    _FAV_MIXES_GROUP_ID,
    _FAV_PLAYLISTS_GROUP_ID,
    _FAV_VIDEOS_GROUP_ID,
    _LIBRARY_PAGE,
    WavesBridge,
    _ScanStopped,
)

QML = (Path(__file__).parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml").read_text(encoding="utf-8")


class _Signal:
    def __init__(self):
        self.emits: list = []

    def emit(self, *args):
        self.emits.append(args if len(args) != 1 else args[0])


class _InlinePool:
    @staticmethod
    def start(worker):
        worker.fn()


class _Favorites:
    def __init__(self, albums=(), artists=(), videos=(), fail: bool = False):
        self._rows = {"albums": list(albums), "artists": list(artists), "videos": list(videos)}
        self._fail = fail

    def _page(self, kind, limit, offset):
        if self._fail:
            raise RuntimeError("429")
        rows = self._rows[kind]
        return list(rows[offset : offset + (limit or len(rows))])

    def albums(self, limit=None, offset=0):
        return self._page("albums", limit, offset)

    def artists(self, limit=None, offset=0):
        return self._page("artists", limit, offset)

    def videos(self, limit=None, offset=0):
        return self._page("videos", limit, offset)

    def get_videos_count(self):
        return len(self._rows["videos"])

    def get_albums_count(self):
        return len(self._rows["albums"])

    def get_artists_count(self):
        return len(self._rows["artists"])


class _Stub:
    downloadFavoriteAlbums = WavesBridge.downloadFavoriteAlbums
    downloadFavoriteArtists = WavesBridge.downloadFavoriteArtists
    resolveFavoriteAlbums = WavesBridge.resolveFavoriteAlbums
    resolveFavoriteArtists = WavesBridge.resolveFavoriteArtists
    _enqueue_artists = WavesBridge._enqueue_artists
    _enqueue_collections = WavesBridge._enqueue_collections
    downloadFavoritePlaylists = WavesBridge.downloadFavoritePlaylists
    downloadFavoriteMixes = WavesBridge.downloadFavoriteMixes
    downloadFavoriteVideos = WavesBridge.downloadFavoriteVideos
    resolveFavoritePlaylists = WavesBridge.resolveFavoritePlaylists
    _all_favorites = WavesBridge._all_favorites
    _bump_folder_group = WavesBridge._bump_folder_group
    _bump_artist_group = WavesBridge._bump_artist_group
    _reap_stranded_groups = WavesBridge._reap_stranded_groups

    def __init__(self, favorites, claimed: set | None = None, lists=None, tree=None):
        self._lists = lists or {"playlists": [], "mixes": []}
        self._tree = tree
        self._dl = object()
        self._logged_in = True
        self.tidal = SimpleNamespace(session=SimpleNamespace(user=SimpleNamespace(favorites=favorites)))
        self.settings = SimpleNamespace(data=SimpleNamespace(download_dolby_atmos=True))
        self._folder_groups: dict = {}
        self._folder_lock = Lock()
        self._artist_groups: dict = {}
        self._artist_lock = Lock()
        self._scan_pool = _InlinePool()
        self.threadpool = _InlinePool()
        self._scan_gen = 0
        self._browse_gen = 0
        self._scans_in_flight = 0
        self._scan_count_lock = Lock()
        self._browse_loading: set = set()
        self._merge_plans: dict = {}
        self._merge_scanned: set = set()
        self._queue: list = []
        self._queue_lock = Lock()
        self._pending_downloads: list = []
        self._pending_lock = Lock()
        self._stranded_once: set = set()
        self.scanningChanged = _Signal()
        self.downloadProgress = _Signal()
        self.downloadState = _Signal()
        self.folderRemaining = _Signal()
        self._albumsQueued = _Signal()
        self._artistsQueued = _Signal()
        self._collectionsQueued = _Signal()
        self._videosQueued = _Signal()
        self.favoritePlaylistsResolved = _Signal()
        self.started: list = []
        self.favoriteAlbumsResolved = _Signal()
        self.favoriteArtistsResolved = _Signal()
        self.statuses: list = []
        self.remembered: list = []
        self.artist_clicks: list = []
        self._claimed = claimed or set()

    def _download_gate(self):
        return "ok"

    def _stash_pending_download(self, media_id, retry):
        raise AssertionError("no nudge in these tests")

    def _ffmpeg_gate_holds(self, media_id, retry):
        return False

    def _gate_reachability(self, retry, media_id):
        return True

    def _set_status(self, text):
        self.statuses.append(text)

    def _dedup_albums(self, albums):
        return list(albums)

    def _waves_pref_bool(self, key):
        return False

    def _library_bulk_skip_on(self):
        return bool(self._claimed)

    def _library_claims_album(self, album):
        return str(album.id) in self._claimed

    def _remember(self, bucket, key, obj):
        self.remembered.append((bucket, key))

    def downloadArtist(self, artist_id):
        self.artist_clicks.append(artist_id)

    def _media_lists(self, refresh, walk=True):
        return self._lists, (self._tree if walk else None)

    def downloadPlaylist(self, key):
        self.started.append(("playlist", key))

    def downloadMix(self, key):
        self.started.append(("mix", key))

    def _queue_batch(self):
        return contextlib.nullcontext()


def _albums(n):
    return [SimpleNamespace(id=f"a{i}") for i in range(n)]


def _artists(n):
    return [SimpleNamespace(id=f"r{i}") for i in range(n)]


def test_albums_page_the_whole_list_into_one_batch_under_the_rollup():
    count = _LIBRARY_PAGE + 7
    stub = _Stub(_Favorites(albums=_albums(count)))
    stub.downloadFavoriteAlbums()
    assert len(stub._albumsQueued.emits) == 1
    gen, keys = stub._albumsQueued.emits[0]
    assert gen == 0 and len(keys) == count
    grp = stub._folder_groups[_FAV_ALBUMS_GROUP_ID]
    assert grp["total"] == count and grp["keys"] == set(keys)
    assert stub.downloadState.emits[-1] == (_FAV_ALBUMS_GROUP_ID, "queued")
    # Edition handling already ran in the sweep; downloadAlbum must not
    # divert these into its own scan, which never bumps the rollup.
    assert stub._merge_scanned == set(keys)


def test_albums_the_library_owns_are_left_out():
    stub = _Stub(_Favorites(albums=_albums(3)), claimed={"a1"})
    stub.downloadFavoriteAlbums()
    assert stub._albumsQueued.emits[0][1] == ["a0", "a2"]
    assert "1 already in your library" in stub.statuses[-1]


def test_albums_a_failed_page_queues_nothing():
    stub = _Stub(_Favorites(albums=_albums(3), fail=True))
    stub.downloadFavoriteAlbums()
    assert stub._albumsQueued.emits == [] and stub._folder_groups == {}
    assert stub.downloadState.emits[-1] == (_FAV_ALBUMS_GROUP_ID, "")


def test_artists_start_one_discography_each_under_a_namespaced_rollup():
    stub = _Stub(_Favorites(artists=[*_artists(3), SimpleNamespace(id="r1")]))
    stub.downloadFavoriteArtists()
    gen, ids = stub._artistsQueued.emits[0]
    assert ids == ["r0", "r1", "r2"], "one discography per artist, duplicates dropped"
    grp = stub._folder_groups[_FAV_ARTISTS_GROUP_ID]
    assert grp["keys"] == {_ARTIST_ROLLUP_MEMBER + i for i in ids}
    assert (_FAV_ARTISTS_GROUP_ID, 3, 3) in stub.folderRemaining.emits
    stub._enqueue_artists(gen, ids)
    assert stub.artist_clicks == ids


def test_a_batch_stop_overtook_starts_no_discography():
    stub = _Stub(_Favorites(artists=_artists(2)))
    stub._enqueue_artists(stub._scan_gen - 1, ["r0", "r1"])
    assert stub.artist_clicks == []


def test_artists_a_stop_mid_scan_registers_nothing(monkeypatch):
    def stop_check_for(bridge):
        def check():
            raise _ScanStopped()

        return check

    monkeypatch.setattr("waves.waves_ui.backend._stop_check_for", stop_check_for)
    stub = _Stub(_Favorites(artists=_artists(2)))
    stub.downloadFavoriteArtists()
    assert stub._artistsQueued.emits == [] and stub._folder_groups == {}
    assert stub.downloadState.emits[-1] == (_FAV_ARTISTS_GROUP_ID, "")


def _fav_artists_group(stub, ids):
    keys = [_ARTIST_ROLLUP_MEMBER + i for i in ids]
    stub._folder_groups[_FAV_ARTISTS_GROUP_ID] = {
        "keys": set(keys),
        "done": set(),
        "failed": set(),
        "prog": {},
        "weights": dict.fromkeys(keys, 1),
        "total": len(keys),
    }


def test_a_finished_discography_settles_its_artist_in_the_rollup():
    stub = _Stub(_Favorites())
    _fav_artists_group(stub, ["r0", "r1"])
    for aid, album in (("r0", "x0"), ("r1", "x1")):
        stub._artist_groups[aid] = {"keys": {album}, "done": set(), "failed": set(), "prog": {}}
    # The album numbered like an artist must not credit that artist.
    stub._bump_folder_group("r1", None, "done")
    assert stub._folder_groups[_FAV_ARTISTS_GROUP_ID]["done"] == set()
    stub._bump_artist_group("x0", 50.0, None)
    assert stub._folder_groups[_FAV_ARTISTS_GROUP_ID]["prog"][_ARTIST_ROLLUP_MEMBER + "r0"] == 50.0
    stub._bump_artist_group("x0", None, "done")
    assert (_FAV_ARTISTS_GROUP_ID, 1, 2) in stub.folderRemaining.emits
    stub._bump_artist_group("x1", None, "failed")
    assert _FAV_ARTISTS_GROUP_ID not in stub._folder_groups
    assert stub.downloadState.emits[-1] == (_FAV_ARTISTS_GROUP_ID, "failed")


def test_the_reaper_keeps_the_rollup_while_a_discography_is_alive():
    stub = _Stub(_Favorites())
    _fav_artists_group(stub, ["r0"])
    stub._artist_groups["r0"] = {"keys": {"x0"}, "done": set(), "failed": set(), "prog": {}}
    stub._queue = [{"media_id": "x0", "status": "queued"}]
    stub._reap_stranded_groups()
    stub._reap_stranded_groups()
    assert _FAV_ARTISTS_GROUP_ID in stub._folder_groups
    # With the discography gone, the net still catches a stranded rollup.
    stub._artist_groups.clear()
    stub._queue = []
    stub._reap_stranded_groups()
    stub._reap_stranded_groups()
    assert _FAV_ARTISTS_GROUP_ID not in stub._folder_groups


class _ArtistScanStub:
    """Just enough bridge for downloadArtist to end without queueing."""

    downloadArtist = WavesBridge.downloadArtist
    _bump_folder_group = WavesBridge._bump_folder_group

    def __init__(self, artist):
        self._dl = object()
        self._artist = artist
        self.settings = SimpleNamespace(data=SimpleNamespace(download_dolby_atmos=True, video_download=False))
        self._scan_pool = _InlinePool()
        self._scan_gen = 0
        self._scans_in_flight = 0
        self._scan_count_lock = Lock()
        self.scanningChanged = _Signal()
        self.downloadProgress = _Signal()
        self.downloadState = _Signal()
        self.folderRemaining = _Signal()
        self._folder_groups: dict = {}
        self._folder_lock = Lock()
        self.statuses: list = []

    def _download_gate(self):
        return "ok"

    def _ffmpeg_gate_holds(self, media_id, retry):
        return False

    def _gate_reachability(self, retry, media_id):
        return True

    def _set_status(self, text):
        self.statuses.append(text)

    def _get_artist(self, artist_id):
        return self._artist

    def _artist_releases(self, artist):
        return [], [], True

    def _dedup_albums(self, albums):
        return list(albums)

    def _waves_pref_bool(self, key):
        return False

    def _library_bulk_skip_on(self):
        return False


@pytest.mark.parametrize(("artist", "verdict"), [(SimpleNamespace(id="r0"), "done"), (None, "failed")])
def test_a_discography_that_queues_nothing_still_settles_its_artist(artist, verdict):
    stub = _ArtistScanStub(artist)
    _fav_artists_group(stub, ["r0"])
    stub.downloadArtist("r0")
    assert _FAV_ARTISTS_GROUP_ID not in stub._folder_groups, "the rollup waited on an artist forever"
    assert stub.downloadState.emits[-1] == (_FAV_ARTISTS_GROUP_ID, verdict)


def test_resolve_counts_albums_and_artists():
    stub = _Stub(_Favorites(albums=_albums(4), artists=_artists(2)))
    stub.resolveFavoriteAlbums()
    stub.resolveFavoriteArtists()
    assert stub.favoriteAlbumsResolved.emits == [4]
    assert stub.favoriteArtistsResolved.emits == [2]
    assert stub._browse_loading == set()


@pytest.mark.parametrize(
    ("cat", "btn", "gid", "resolve", "slot", "kind"),
    [
        (
            "albums",
            "favAlbumsBtn",
            _FAV_ALBUMS_GROUP_ID,
            "resolveFavoriteAlbums",
            "downloadFavoriteAlbums",
            "favAlbums",
        ),
        (
            "artists",
            "favArtistsBtn",
            _FAV_ARTISTS_GROUP_ID,
            "resolveFavoriteArtists",
            "downloadFavoriteArtists",
            "favArtists",
        ),
        (
            "playlists",
            "favPlaylistsBtn",
            _FAV_PLAYLISTS_GROUP_ID,
            "resolveFavoritePlaylists",
            "downloadFavoritePlaylists",
            "favPlaylists",
        ),
        ("mixes", "favMixesBtn", _FAV_MIXES_GROUP_ID, "resolveFavoriteMixes", "downloadFavoriteMixes", "favMixes"),
        (
            "videos",
            "favVideosBtn",
            _FAV_VIDEOS_GROUP_ID,
            "resolveFavoriteVideos",
            "downloadFavoriteVideos",
            "favVideos",
        ),
    ],
)
def test_the_qml_is_wired(cat, btn, gid, resolve, slot, kind):
    block = re.search(rf'Item \{{\s*visible: root\.libraryCategory === "{cat}".*?objectName: "{btn}"', QML, re.S)
    assert block, f"the {cat} DOWNLOAD ALL button must live inside the {cat}-only Item"
    assert f'mediaId: "{gid}"' in QML and f"waves.{resolve}()" in QML
    gate = re.search(r"id: catDlGate.*?label: \"Cancel\"", QML, re.S)
    assert gate and f'p.kind === "{kind}"' in gate.group(0) and f"waves.{slot}()" in gate.group(0)
    logout = re.search(r"onLoggedInChanged.*?root\.catDlPrompt = null\s*\n(.*?)\n\s*//", QML, re.S)
    assert logout and f"{kind}Pending = false" in logout.group(1)


class _Node:
    def __init__(self, nid, parent, playlists):
        self.id, self.parent_id, self.playlists = nid, parent, playlists


def _tree(partial=False):
    from waves.helper.folders import FolderTree

    return FolderTree(
        nodes=[
            _Node("f1", "root", [SimpleNamespace(id="p2", num_tracks=5)]),
            _Node("f2", "f1", [SimpleNamespace(id="p3", num_tracks=1), SimpleNamespace(id="p1", num_tracks=9)]),
        ],
        partial=partial,
    )


def _root_lists():
    folder = SimpleNamespace(id="f1")  # a root folder row carries no track count
    return {
        "playlists": [folder, SimpleNamespace(id="p1", num_tracks=9)],
        "mixes": [SimpleNamespace(id="m1"), SimpleNamespace(id="m2")],
    }


def test_playlists_take_every_folder_at_any_depth_once():
    stub = _Stub(_Favorites(), lists=_root_lists(), tree=_tree())
    stub.downloadFavoritePlaylists()
    gen, kind, keys = stub._collectionsQueued.emits[0]
    assert kind == "playlist" and keys == ["p1", "p2", "p3"]
    grp = stub._folder_groups[_FAV_PLAYLISTS_GROUP_ID]
    assert grp["weights"] == {"p1": 9, "p2": 5, "p3": 1}, "the bar is track-weighted, like a folder's"
    stub._enqueue_collections(gen, kind, keys)
    assert stub.started == [("playlist", "p1"), ("playlist", "p2"), ("playlist", "p3")]
    stub.resolveFavoritePlaylists()
    assert stub.favoritePlaylistsResolved.emits == [3]


def test_playlists_refuse_a_folder_walk_cut_short():
    stub = _Stub(_Favorites(), lists=_root_lists(), tree=_tree(partial=True))
    stub.downloadFavoritePlaylists()
    assert stub._collectionsQueued.emits == [] and stub._folder_groups == {}
    assert stub.downloadState.emits[-1] == (_FAV_PLAYLISTS_GROUP_ID, "")
    assert "try again" in stub.statuses[-1]


def test_mixes_queue_each_mix_and_a_stale_batch_starts_nothing():
    stub = _Stub(_Favorites(), lists=_root_lists())
    stub.downloadFavoriteMixes()
    gen, kind, keys = stub._collectionsQueued.emits[0]
    assert kind == "mix" and keys == ["m1", "m2"]
    assert _FAV_MIXES_GROUP_ID in stub._folder_groups
    stub._enqueue_collections(gen - 1, kind, keys)
    assert stub.started == []


def test_videos_page_every_favourite_video():
    count = _LIBRARY_PAGE + 3
    stub = _Stub(_Favorites(videos=[SimpleNamespace(id=f"v{i}") for i in range(count)]))
    stub.downloadFavoriteVideos()
    _gen, keys = stub._videosQueued.emits[0]
    assert len(keys) == count
    assert stub._folder_groups[_FAV_VIDEOS_GROUP_ID]["total"] == count


def test_an_empty_tab_says_so_and_publishes_no_group():
    stub = _Stub(_Favorites())
    stub.downloadFavoriteMixes()
    assert stub._folder_groups == {} and stub.statuses[-1] == "No mixes to download"
