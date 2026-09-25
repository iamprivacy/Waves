"""My Tidal > Tracks DOWNLOAD ALL queues every favourite track (issue #43).

``downloadFavoriteTracks`` is the Tracks tab's bulk download. It pages the
whole favourites list (a short window must never truncate it), applies the
library claim like every other bulk action, registers a folder-style rollup
under the fixed ``fav:tracks`` id so the header button and its badge follow
the queued / running / done / failed lifecycle, and hands the keys to the GUI
thread in ONE ``_tracksQueued`` batch. It follows the partial-scan rule: a
failed page or a STOP queues nothing rather than reporting clean success over
a set it never saw. ``resolveFavoriteTracks`` is the count behind the confirm.
"""

from __future__ import annotations

import re
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

import pytest

from waves.waves_ui.backend import _FAV_TRACKS_GROUP_ID, _LIBRARY_PAGE, WavesBridge, _ScanStopped

QML = (Path(__file__).parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml").read_text(encoding="utf-8")


class _Signal:
    def __init__(self):
        self.emits: list = []

    def emit(self, *args):
        self.emits.append(args if len(args) != 1 else args[0])


class _OrderedSignal(_Signal):
    def __init__(self, tag: str, log: list):
        super().__init__()
        self._tag = tag
        self._log = log

    def emit(self, *args):
        super().emit(*args)
        self._log.append(self._tag)


class _InlinePool:
    @staticmethod
    def start(worker):
        worker.fn()


class _Favorites:
    """Serves favourites a window at a time, the way the endpoint does."""

    def __init__(self, tracks, fail_page: int | None = None, count_fails: bool = False):
        self._tracks = tracks
        self._fail_page = fail_page
        self._count_fails = count_fails
        self.calls = 0

    def get_tracks_count(self):
        if self._count_fails:
            raise RuntimeError("503")
        return len(self._tracks)

    def tracks(self, limit=None, offset=0):
        self.calls += 1
        if self._fail_page is not None and offset // _LIBRARY_PAGE == self._fail_page:
            raise RuntimeError("429")
        window = limit if limit is not None else len(self._tracks)
        return list(self._tracks[offset : offset + window])


class _Stub:
    downloadFavoriteTracks = WavesBridge.downloadFavoriteTracks
    resolveFavoriteTracks = WavesBridge.resolveFavoriteTracks
    _all_favorites = WavesBridge._all_favorites

    def __init__(
        self,
        favorites,
        gate: str = "ok",
        claim_on: bool = False,
        claimed: set | None = None,
    ):
        self._dl = object()
        self._logged_in = True
        self.tidal = SimpleNamespace(session=SimpleNamespace(user=SimpleNamespace(favorites=favorites)))
        self._folder_groups: dict = {}
        self._folder_lock = Lock()
        self._scan_pool = _InlinePool()
        self.threadpool = _InlinePool()
        self._scan_gen = 0
        self._lib_gen = 0
        self._browse_gen = 0
        self._scans_in_flight = 0
        self._scan_count_lock = Lock()
        self._browse_loading: set = set()
        self.scanningChanged = _Signal()
        self.order: list = []
        self.downloadProgress = _Signal()
        self.downloadState = _OrderedSignal("state", self.order)
        self.folderRemaining = _OrderedSignal("badge", self.order)
        self._tracksQueued = _Signal()
        self.favoriteTracksResolved = _Signal()
        self.statuses: list = []
        self.remembered: list = []
        self.stashed: list = []
        self._gate = gate
        self._claim_on = claim_on
        self._claimed = claimed or set()

    def _download_gate(self):
        return self._gate

    def _stash_pending_download(self, media_id, retry):
        self.stashed.append(media_id)

    def _ffmpeg_gate_holds(self, media_id, retry):
        return False

    def _gate_reachability(self, retry, media_id):
        return True

    def _set_status(self, text):
        self.statuses.append(text)

    def _library_bulk_skip_on(self):
        return self._claim_on

    def _library_claim_media(self, media, album=None):
        return str(media.id) in self._claimed

    def _remember(self, bucket, key, obj):
        self.remembered.append((bucket, key))


GID = _FAV_TRACKS_GROUP_ID


def _tracks(n):
    return [SimpleNamespace(id=f"t{i}", album=None) for i in range(n)]


def test_it_pages_the_whole_list_into_one_batch():
    count = _LIBRARY_PAGE * 2 + 20
    stub = _Stub(_Favorites(_tracks(count)))
    stub.downloadFavoriteTracks()
    assert len(stub._tracksQueued.emits) == 1, "one batched delivery, not one per track"
    gen, queued = stub._tracksQueued.emits[0]
    assert gen == 0
    assert len(queued) == count, "the scan stopped at the first window"
    assert queued[0] == "t0" and queued[-1] == f"t{count - 1}"
    assert stub.remembered[0] == ("track", "t0")
    assert any(f"{count} tracks" in s for s in stub.statuses)


def test_the_rollup_is_registered_and_published_badge_first():
    stub = _Stub(_Favorites(_tracks(3)))
    stub.downloadFavoriteTracks()
    grp = stub._folder_groups[GID]
    assert grp["keys"] == {"t0", "t1", "t2"} and grp["total"] == 3
    assert grp["weights"] == {"t0": 1, "t1": 1, "t2": 1}
    assert (GID, 3, 3) in stub.folderRemaining.emits
    assert (GID, "queued") in stub.downloadState.emits
    # The badge reads the remaining map as soon as the state flips, so the
    # count must land before QUEUED.
    assert stub.order == ["state", "badge", "state"], "running, then the count, then QUEUED"


def test_a_failed_page_queues_nothing():
    stub = _Stub(_Favorites(_tracks(_LIBRARY_PAGE + 5), fail_page=1))
    stub.downloadFavoriteTracks()
    assert stub._tracksQueued.emits == []
    assert GID not in stub._folder_groups
    assert stub.downloadState.emits[-1] == (GID, "")
    assert any("try again" in s for s in stub.statuses)


def test_a_stop_mid_scan_queues_nothing(monkeypatch):
    stub = _Stub(_Favorites(_tracks(_LIBRARY_PAGE * 3)))
    ticks = {"n": 0}

    def stop_check_for(bridge):
        def check():
            ticks["n"] += 1
            if ticks["n"] >= 3:  # after page 0 has landed, so a partial list exists to leak
                raise _ScanStopped()

        return check

    monkeypatch.setattr("waves.waves_ui.backend._stop_check_for", stop_check_for)
    stub.downloadFavoriteTracks()
    assert stub.tidal.session.user.favorites.calls >= 1, "the stop must land mid-scan, not before it"
    assert stub._tracksQueued.emits == []
    assert GID not in stub._folder_groups
    assert stub.downloadState.emits[-1] == (GID, "")


def test_a_bulk_scan_without_a_count_refuses_rather_than_guessing():
    """A short window is not the end of the list (tidalapi drops delisted
    items inside it). Without a count the id cache may stop there; the bulk
    download must not, or the rest of the favourites are quietly left out."""
    stub = _Stub(_Favorites(_tracks(_LIBRARY_PAGE * 2), count_fails=True))
    stub.downloadFavoriteTracks()
    assert stub._tracksQueued.emits == []
    assert GID not in stub._folder_groups
    assert any("try again" in s for s in stub.statuses)


def test_zero_favourites_says_so():
    stub = _Stub(_Favorites([]))
    stub.resolveFavoriteTracks()
    assert stub.favoriteTracksResolved.emits == [0]
    assert stub.statuses[-1] == "No favourite tracks yet"


def test_a_folder_nudge_stashes_the_retry_and_publishes_no_group():
    stub = _Stub(_Favorites(_tracks(2)), gate="nudge")
    stub.downloadFavoriteTracks()
    assert stub.stashed == [GID]
    assert stub._tracksQueued.emits == [] and stub._folder_groups == {}


def test_an_empty_list_and_a_fully_claimed_list_say_so():
    stub = _Stub(_Favorites([]))
    stub.downloadFavoriteTracks()
    assert stub._tracksQueued.emits == []
    assert stub.statuses[-1] == "No tracks to download"
    assert stub.downloadState.emits[-1] == (GID, "")

    stub = _Stub(_Favorites(_tracks(2)), claim_on=True, claimed={"t0", "t1"})
    stub.downloadFavoriteTracks()
    assert stub._tracksQueued.emits == []
    assert "already in your library" in stub.statuses[-1]


def test_the_library_claim_skips_only_what_it_owns():
    stub = _Stub(_Favorites(_tracks(3)), claim_on=True, claimed={"t1"})
    stub.downloadFavoriteTracks()
    assert stub._tracksQueued.emits[0][1] == ["t0", "t2"]
    assert stub._folder_groups[GID]["total"] == 2
    assert "1 already in your library" in stub.statuses[-1]


def test_a_single_click_off_claim_queues_everything():
    stub = _Stub(_Favorites(_tracks(2)), claim_on=False, claimed={"t0", "t1"})
    stub.downloadFavoriteTracks()
    assert stub._tracksQueued.emits[0][1] == ["t0", "t1"]


def test_resolve_emits_the_count_and_minus_one_on_failure():
    stub = _Stub(_Favorites(_tracks(7)))
    stub.resolveFavoriteTracks()
    assert stub.favoriteTracksResolved.emits == [7]
    assert stub._browse_loading == set(), "the in-flight key must clear"

    stub = _Stub(_Favorites([], count_fails=True))
    stub.resolveFavoriteTracks()
    assert stub.favoriteTracksResolved.emits == [-1]
    assert any("try again" in s for s in stub.statuses)


def test_resolve_drops_a_count_from_a_previous_account():
    stub = _Stub(_Favorites(_tracks(7)))

    class _BumpPool:
        @staticmethod
        def start(worker):
            stub._browse_gen += 1  # logout landed while the count was in flight
            worker.fn()

    stub.threadpool = _BumpPool()
    stub.resolveFavoriteTracks()
    assert stub.favoriteTracksResolved.emits == []
    assert stub._browse_loading == set(), "a dropped count must still release the key"


def test_a_category_switch_during_the_count_does_not_kill_the_button():
    """_lib_gen moves on every My Tidal category click, revalidate and Back.
    A count tagged with it was dropped by an Albums click mid-count, and the
    load key it never released made every later DOWNLOAD ALL click a no-op
    until logout."""
    stub = _Stub(_Favorites(_tracks(7)))

    class _SwitchPool:
        @staticmethod
        def start(worker):
            stub._lib_gen += 1  # the reader clicked Albums while the count was in flight
            worker.fn()

    stub.threadpool = _SwitchPool()
    stub.resolveFavoriteTracks()
    assert stub.favoriteTracksResolved.emits == [7]
    assert stub._browse_loading == set()
    stub.threadpool = _InlinePool()
    stub.resolveFavoriteTracks()
    assert stub.favoriteTracksResolved.emits == [7, 7], "the next click must count again"


def test_resolve_needs_a_login():
    stub = _Stub(_Favorites(_tracks(7)))
    stub._logged_in = False
    stub.resolveFavoriteTracks()
    assert stub.favoriteTracksResolved.emits == []


@pytest.mark.parametrize(
    "needle",
    [
        f'mediaId: "{GID}"',  # the button and the backend share one group id
        'visible: root.libraryCategory === "tracks"',
        "waves.resolveFavoriteTracks()",
        "waves.downloadFavoriteTracks()",
        "function onFavoriteTracksResolved(count)",
    ],
)
def test_the_qml_is_wired(needle):
    assert needle in QML


def test_the_button_is_gated_on_the_tracks_tab():
    block = re.search(r'Item \{\s*visible: root\.libraryCategory === "tracks".*?objectName: "favTracksBtn"', QML, re.S)
    assert block, "the DOWNLOAD ALL button must live inside the tracks-only Item"


def test_logout_drops_a_pending_count():
    handler = re.search(r"onLoggedInChanged.*?root\.catDlPrompt = null\s*\n(.*?)\n", QML, re.S)
    assert handler and "favTracksPending = false" in handler.group(1)


def test_the_confirm_dialog_names_tracks_and_calls_the_slot():
    gate = re.search(r"id: catDlGate.*?label: \"Cancel\"", QML, re.S)
    assert gate
    body = gate.group(0)
    assert '" track?" : " tracks?"' in body
    assert 'p.kind === "favTracks"' in body and "waves.downloadFavoriteTracks()" in body
