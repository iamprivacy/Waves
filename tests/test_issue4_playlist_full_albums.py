"""The playlist page's "Download full albums" queues each track's whole album.

``downloadPlaylistAlbums`` (issue #4) walks the playlist, takes the distinct
source album of every track in playlist order (videos have no album and are
skipped), resolves each album on the worker and hands the set to the same
sweep a discography runs. Its rollup state lives in the shared artist-group
dict under a namespaced "albums:" id so it can never collide with the
"Download playlist" button on the same page. It follows the partial-scan rule:
a ceiling-hit playlist, a failed playlist fetch or a single album that will
not load refuses the whole set instead of queueing a truncated one.
"""

from __future__ import annotations

import re
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

from tidalapi.album import Album
from tidalapi.media import AudioMode, Quality, Track, Video

from waves.waves_ui.backend import _PLAYLIST_ALBUMS_GROUP_PREFIX, WavesBridge

QML = (Path(__file__).parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml").read_text(encoding="utf-8")
ATMOS = AudioMode.dolby_atmos.value


class _Signal:
    def __init__(self):
        self.emits: list = []

    def emit(self, *args):
        self.emits.append(args if len(args) != 1 else args[0])


class _InlinePool:
    @staticmethod
    def start(worker):
        worker.fn()


def _track(album_id):
    t = Track.__new__(Track)
    t.id = f"t-{album_id}-{id(t)}"
    t.album = SimpleNamespace(id=album_id) if album_id else None
    return t


def _video():
    v = Video.__new__(Video)
    v.id = "vid"
    v.album = SimpleNamespace(id="should-never-be-used")
    return v


def _album(aid, title="Album", modes=("STEREO",)):
    a = Album.__new__(Album)
    a.id = aid
    a.name = title
    a.artist = SimpleNamespace(name="Artist", id=7)
    a.artists = [a.artist]
    a.audio_modes = list(modes)
    a.audio_quality = Quality.high_lossless
    a.media_metadata_tags = None
    a.num_tracks = 10
    a.num_videos = 0
    a.explicit = False
    return a


class _FakePlaylist:
    """Serves items a 100-wide page at a time, the way the endpoint does."""

    def __init__(self, items, endless=False):
        self._items = items
        self._endless = endless

    def items(self, limit=100, offset=0):
        if self._endless:
            return [_track(f"{offset + i}") for i in range(limit)]
        return list(self._items[offset : offset + limit])


class _Session:
    def __init__(self, albums, playlist=None, fail_album=None):
        self._albums = {a.id: a for a in albums}
        self._playlist = playlist
        self._fail_album = fail_album
        self.album_fetches: list = []

    def album(self, album_id):
        self.album_fetches.append(str(album_id))
        if str(album_id) == self._fail_album:
            raise RuntimeError("404")
        return self._albums[str(album_id)]

    def playlist(self, playlist_id):
        if self._playlist is None:
            raise RuntimeError("404")
        return self._playlist


class _Lock:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Stub:
    downloadPlaylistAlbums = WavesBridge.downloadPlaylistAlbums

    def __init__(self, playlist, albums, *, atmos=True, cached=True, fail_album=None, bulk_skip=False, claimed=()):
        self._dl = object()
        self.settings = SimpleNamespace(data=SimpleNamespace(download_dolby_atmos=atmos))
        self.tidal = SimpleNamespace(session=_Session(albums, playlist, fail_album))
        self._objs = {"album": {}, "playlist": {"pl1": playlist} if cached else {}}
        self._browse_lock = _Lock()
        self._artist_groups: dict = {}
        self._artist_lock = Lock()
        self._scan_pool = _InlinePool()
        self._scan_gen = 0
        self._scans_in_flight = 0
        self._scan_count_lock = Lock()
        self.scanningChanged = _Signal()
        self._merge_scanned: set = set()
        self._merge_plans: dict = {}
        self.downloadProgress = _Signal()
        self.downloadState = _Signal()
        self._albumsQueued = _Signal()
        self.statuses: list = []
        self.remembered: list = []
        self._bulk_skip = bulk_skip
        self._claimed = set(claimed)
        # Order pin: the exemption must be in place when the batch emits.
        self.exempt_at_emit: set | None = None

    def _download_gate(self):
        return "ok"

    def _ffmpeg_gate_holds(self, media_id, retry):
        return False

    def _gate_reachability(self, retry, media_id):
        return True

    def _set_status(self, text):
        self.statuses.append(text)

    def _remember(self, bucket, key, obj):
        self.remembered.append((bucket, key))
        self._objs.setdefault(bucket, {})[key] = obj

    def _dedup_albums(self, albums):
        return list(albums)

    def _waves_pref_bool(self, key):
        return False

    def _merge_pref_on(self):
        return False

    def _library_bulk_skip_on(self):
        return self._bulk_skip

    def _library_claims_album(self, album):
        return album.id in self._claimed


class _OrderPinSignal(_Signal):
    def __init__(self, stub):
        super().__init__()
        self._stub = stub

    def emit(self, *args):
        self._stub.exempt_at_emit = set(self._stub._merge_scanned)
        super().emit(*args)


GID = _PLAYLIST_ALBUMS_GROUP_PREFIX + "pl1"


def test_it_queues_each_distinct_album_once_in_playlist_order():
    playlist = _FakePlaylist([_track("2"), _track("1"), _video(), _track("2"), _track(None), _track("3")])
    stub = _Stub(playlist, [_album("1"), _album("2"), _album("3")])
    stub.downloadPlaylistAlbums("pl1")
    assert stub._albumsQueued.emits == [(0, ["2", "1", "3"])]
    assert stub.tidal.session.album_fetches == ["2", "1", "3"], "one fetch per distinct album"
    assert any("3 albums" in s for s in stub.statuses)


def test_the_group_is_namespaced_away_from_the_playlist_button():
    stub = _Stub(_FakePlaylist([_track("1")]), [_album("1")])
    stub.downloadPlaylistAlbums("pl1")
    assert GID in stub._artist_groups
    assert "pl1" not in stub._artist_groups, "a bare playlist-id group would collide with the playlist button"
    assert stub._artist_groups[GID]["keys"] == {"1"}
    assert (GID, "running") in stub.downloadState.emits
    assert all(e[0] != "pl1" for e in stub.downloadState.emits)


def test_the_albums_are_exempt_from_the_edition_scan_before_the_batch_emits():
    """Without this, downloadAlbum would send each album through its own
    async edition scan, which exits by a path that never bumps the rollup."""
    stub = _Stub(_FakePlaylist([_track("1"), _track("2")]), [_album("1"), _album("2")])
    stub._albumsQueued = _OrderPinSignal(stub)
    stub.downloadPlaylistAlbums("pl1")
    assert stub.exempt_at_emit == {"1", "2"}


def test_a_long_playlist_is_paged_through_not_truncated():
    tracks = [_track(f"{i}") for i in range(230)]
    stub = _Stub(_FakePlaylist(tracks), [_album(f"{i}") for i in range(230)])
    stub.downloadPlaylistAlbums("pl1")
    assert len(stub._albumsQueued.emits[0][1]) == 230


def test_a_ceiling_hit_playlist_refuses_the_whole_set():
    stub = _Stub(_FakePlaylist([], endless=True), [])
    stub.downloadPlaylistAlbums("pl1")
    assert stub._albumsQueued.emits == []
    assert GID not in stub._artist_groups
    assert stub.downloadState.emits[-1] == (GID, "")
    assert any("try again" in s for s in stub.statuses)


def test_one_album_that_will_not_load_refuses_the_whole_set():
    stub = _Stub(_FakePlaylist([_track("1"), _track("2")]), [_album("1"), _album("2")], fail_album="2")
    stub.downloadPlaylistAlbums("pl1")
    assert stub._albumsQueued.emits == []
    assert GID not in stub._artist_groups
    assert stub._merge_scanned == set()
    assert stub.downloadState.emits[-1] == (GID, "")
    assert any("try again" in s for s in stub.statuses)


def test_a_playlist_gone_from_the_registry_is_refetched():
    playlist = _FakePlaylist([_track("1")])
    stub = _Stub(playlist, [_album("1")], cached=False)
    stub.downloadPlaylistAlbums("pl1")
    assert ("playlist", "pl1") in stub.remembered
    assert stub._albumsQueued.emits == [(0, ["1"])]


def test_a_playlist_that_will_not_load_settles_back_to_idle():
    stub = _Stub(None, [], cached=False)
    stub.downloadPlaylistAlbums("pl1")
    assert stub._albumsQueued.emits == []
    assert stub.downloadState.emits[-1] == (GID, "")
    assert any("Could not load that playlist" in s for s in stub.statuses)


def test_a_playlist_of_only_videos_settles_back_to_idle():
    stub = _Stub(_FakePlaylist([_video(), _video()]), [])
    stub.downloadPlaylistAlbums("pl1")
    assert stub._albumsQueued.emits == []
    assert stub.downloadState.emits[-1] == (GID, "")
    assert any("No albums" in s for s in stub.statuses)


def test_atmos_off_leaves_out_an_atmos_edition_beside_its_stereo_twin():
    stereo = _album("11", "Random Access Memories")
    atmos = _album("22", "Random Access Memories", [ATMOS])
    stub = _Stub(_FakePlaylist([_track("22"), _track("11")]), [stereo, atmos], atmos=False)
    stub.downloadPlaylistAlbums("pl1")
    assert stub._albumsQueued.emits == [(0, ["11"])]


def test_atmos_on_keeps_both_editions():
    stereo = _album("11", "Random Access Memories")
    atmos = _album("22", "Random Access Memories", [ATMOS])
    stub = _Stub(_FakePlaylist([_track("22"), _track("11")]), [stereo, atmos], atmos=True)
    stub.downloadPlaylistAlbums("pl1")
    assert stub._albumsQueued.emits == [(0, ["22", "11"])]


def test_library_bulk_skip_leaves_out_claimed_albums_and_says_so():
    stub = _Stub(_FakePlaylist([_track("1"), _track("2")]), [_album("1"), _album("2")], bulk_skip=True, claimed=["1"])
    stub.downloadPlaylistAlbums("pl1")
    assert stub._albumsQueued.emits == [(0, ["2"])]
    assert stub._artist_groups[GID]["keys"] == {"2"}
    assert any("1 already in your library" in s for s in stub.statuses)


def test_every_album_claimed_queues_nothing_and_hands_the_button_back():
    stub = _Stub(_FakePlaylist([_track("1")]), [_album("1")], bulk_skip=True, claimed=["1"])
    stub.downloadPlaylistAlbums("pl1")
    assert stub._albumsQueued.emits == []
    assert GID not in stub._artist_groups
    assert stub.downloadState.emits[-1] == (GID, "")


def test_stop_mid_scan_queues_nothing_and_hands_the_button_back():
    stub = _Stub(_FakePlaylist([_track("1")]), [_album("1")])
    session = stub.tidal.session
    real = session.album

    def album_then_stop(album_id):
        stub._scan_gen += 1  # STOP lands while the album is being fetched
        return real(album_id)

    session.album = album_then_stop
    stub.downloadPlaylistAlbums("pl1")
    assert stub._albumsQueued.emits == []
    assert GID not in stub._artist_groups
    assert stub.downloadState.emits[-1] == (GID, "")


# ---- QML wiring pins -------------------------------------------------------


# ---- the edition gate: the same four cells the discography sweep has --------


class _EditionStub(_Stub):
    """'Most-complete edition only' and 'Best of both' as the caller sets them;
    the edition scans themselves are stand-ins that report they ran."""

    def __init__(self, playlist, albums, *, collapse, merge):
        super().__init__(playlist, albums)
        self.collapse = collapse
        self.merge = merge
        self.calls: list = []

    def _waves_pref_bool(self, key):
        return self.collapse if key == "collapse_editions" else False

    def _merge_pref_on(self):
        return self.merge

    def _merge_editions(self, albums, stop_check=None):
        self.calls.append("merge")
        # Standard + Deluxe: one identity with a plan.
        return [], [(albums[1], [("plan",)])]

    def _collapse_editions(self, albums, stop_check=None):
        self.calls.append("collapse")
        return [albums[1]]


def _two_editions():
    playlist = _FakePlaylist([_track("1"), _track("2")])
    return playlist, [_album("1", "Album"), _album("2", "Album (Deluxe)")]


def test_with_the_switch_off_every_edition_downloads_whole_even_with_best_of_both_on():
    stub = _EditionStub(*_two_editions(), collapse=False, merge=True)
    stub.downloadPlaylistAlbums("pl1")
    assert stub.calls == [], "the sweep merged or collapsed with 'Most-complete edition only' off"
    assert stub._albumsQueued.emits == [(0, ["1", "2"])]
    assert stub._merge_plans == {}


def test_with_the_switch_off_and_best_of_both_off_nothing_is_scanned_either():
    stub = _EditionStub(*_two_editions(), collapse=False, merge=False)
    stub.downloadPlaylistAlbums("pl1")
    assert stub.calls == []
    assert stub._albumsQueued.emits == [(0, ["1", "2"])]


def test_with_the_switch_on_best_of_both_builds_the_one_edition():
    stub = _EditionStub(*_two_editions(), collapse=True, merge=True)
    stub.downloadPlaylistAlbums("pl1")
    assert stub.calls == ["merge"]
    assert stub._albumsQueued.emits == [(0, ["2"])]
    assert stub._merge_plans == {"2": [("plan",)]}
    assert any("Scanning editions" in s for s in stub.statuses)


def test_with_the_switch_on_and_best_of_both_off_the_plain_collapse_runs():
    stub = _EditionStub(*_two_editions(), collapse=True, merge=False)
    stub.downloadPlaylistAlbums("pl1")
    assert stub.calls == ["collapse"]
    assert stub._albumsQueued.emits == [(0, ["2"])]
    assert stub._merge_plans == {}


def test_a_plan_an_earlier_run_left_behind_does_not_merge_with_the_switch_off():
    # The same stale-plan rule the discography sweep has: a plan a stopped or
    # failed 'Best of both' run stashed must not turn a switch-off sweep's
    # plain album into a merge (downloadAlbum peeks the stash unconditionally).
    stub = _EditionStub(*_two_editions(), collapse=True, merge=True)
    stub.downloadPlaylistAlbums("pl1")
    assert stub._merge_plans == {"2": [("plan",)]}
    stub.collapse = False
    stub._albumsQueued.emits.clear()
    stub.downloadPlaylistAlbums("pl1")
    assert stub._albumsQueued.emits == [(0, ["1", "2"])]
    assert stub._merge_plans == {}


def test_the_playlist_header_carries_the_full_albums_button():
    head = re.search(r"id: browseItemHeader.*?Download full albums.*?\n\s*\}", QML, re.S)
    assert head, "the browse item header's full-albums button was not found"
    block = head.group(0)
    assert '"albums:" + (browseItemHeader.hd.id || "")' in block
    assert "waves.downloadPlaylistAlbums(browseItemHeader.hd.id)" in block
    assert re.search(
        r'visible: !!browseItemHeader\.hd && browseItemHeader\.hd\.kind === "playlist"', block
    ), "the button must hide on album and mix pages"
