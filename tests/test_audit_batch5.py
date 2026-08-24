"""Remaining Tier-2 fixes from the 2026-08-02 audit (findings 29-40).

Backend and updater fixes are exercised hermetically (real unbound methods
bound onto minimal stubs, no Qt, no network); the QML-side fixes (Browse
RETRY routing, recycled-row selection reset, UNC folder paths, the settings
refresh hook, the Back-restore latch) are pinned by source guards, the same
pattern as the scrim and plain-text guards.
"""

from __future__ import annotations

import pathlib
import sys
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import patch

from _dispatch_stub import arm_queue

from tidaler.waves_ui import updater as updater_mod
from tidaler.waves_ui.backend import WavesBridge, _link_tiles_of

QML_DIR = pathlib.Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui" / "qml"
MAIN_QML = (QML_DIR / "Main.qml").read_text(encoding="utf-8")
SETTINGS_QML = (QML_DIR / "SettingsPage.qml").read_text(encoding="utf-8")


class _Signal:
    def __init__(self):
        self.emits: list = []

    def emit(self, *args):
        self.emits.append(args)


class _InlinePool:
    @staticmethod
    def start(worker):
        worker.fn()


class _HeldPool:
    """Collects workers without running them, so in-flight windows are testable."""

    def __init__(self):
        self.workers: list = []

    def start(self, worker):
        self.workers.append(worker)


# Finding 30: an expanded album must never sit on "Loading tracks…" forever.


class _AlbumTracksStub:
    loadAlbumTracks = WavesBridge.loadAlbumTracks
    _start_album_tracks_fetch = WavesBridge._start_album_tracks_fetch
    _record_album_members = WavesBridge._record_album_members

    def __init__(self, session_album=None):
        self._album_tracks_cache = {}
        self._prefetch_lock = Lock()
        self._album_tracks_inflight: dict = {}
        self._album_tracks_unrecorded: set = set()
        self._objs = {"album": {}, "track": {}}
        self.threadpool = _InlinePool()
        self.albumTracksLoaded = _Signal()
        self.collectionMembershipChanged = _Signal()
        self._ownership = SimpleNamespace(record_members_replace=lambda *a: None)
        self.tidal = SimpleNamespace(session=SimpleNamespace(album=session_album))

    def _remember(self, bucket, key, obj):
        self._objs.setdefault(bucket, {})[key] = obj

    def _remember_album_tracks(self, album_id, out):
        self._album_tracks_cache[album_id] = out


def _track(tid, title):
    return SimpleNamespace(id=tid, full_name=title, name=title, duration=61, popularity=5, explicit=False)


def test_an_expired_album_object_is_refetched_for_its_tracks():
    fetched = SimpleNamespace(tracks=lambda: [_track("t1", "One")])
    stub = _AlbumTracksStub(session_album=lambda aid: fetched)
    stub.loadAlbumTracks("77")

    ((aid, rows),) = stub.albumTracksLoaded.emits
    assert aid == "77" and [r["id"] for r in rows] == ["t1"]
    assert stub._objs["album"]["77"] is fetched, "the refetched album is remembered"


def test_a_failed_album_refetch_still_answers_the_row():
    def boom(aid):
        raise OSError("offline")

    stub = _AlbumTracksStub(session_album=boom)
    stub.loadAlbumTracks("77")

    assert stub.albumTracksLoaded.emits == [("77", [])], "the row must leave 'Loading tracks…'"
    assert "77" not in stub._album_tracks_cache, "a failure is never cached"


# Finding 32: one updater at a time, and a pending cancel survives a re-click.


class _InstallStub:
    installAppUpdate = WavesBridge.installAppUpdate

    def __init__(self):
        self._app_update_inflight = False
        self._app_update_abort = Event()
        self.threadpool = _HeldPool()
        self.appUpdateStateChanged = _Signal()
        self.appUpdateProgress = _Signal()
        self.appUpdateStatusChanged = _Signal()
        self.installs = 0
        self._updater = SimpleNamespace(install=self._install)

    def _install(self, progress_cb=None, log_cb=None, abort=None):
        self.installs += 1
        return {"version": "9.9.9"}


def test_a_second_install_click_is_ignored_while_one_runs():
    stub = _InstallStub()
    stub.installAppUpdate()
    stub.installAppUpdate()
    assert len(stub.threadpool.workers) == 1, "one staging dir, one installer"

    stub.threadpool.workers[0].fn()
    assert stub.installs == 1
    assert stub._app_update_inflight is False, "the guard releases when the install ends"

    stub.installAppUpdate()  # after completion a new install may start
    assert len(stub.threadpool.workers) == 2


def test_a_pending_cancel_is_not_discarded_by_a_second_click():
    stub = _InstallStub()
    stub.installAppUpdate()
    stub._app_update_abort.set()  # the user cancels mid-download
    stub.installAppUpdate()  # a stray second click
    assert stub._app_update_abort.is_set(), "the cancel must survive"


# Finding 33: RETRY on a failed queue row must work after any search.


class _RetryStub:
    retryQueueItem = WavesBridge.retryQueueItem
    _retry_queue_refetch = WavesBridge._retry_queue_refetch
    _on_queue_retry_refetched = WavesBridge._on_queue_retry_refetched
    _queue_item = WavesBridge._queue_item
    _reindex_queue = WavesBridge._reindex_queue
    _remove_rows_where = WavesBridge._remove_rows_where
    _remove_row = WavesBridge._remove_row
    _row_object = WavesBridge._row_object
    _start_retry = WavesBridge._start_retry

    def __init__(self, session_track=None):
        self._queue = [
            {
                "qid": 5,
                "type": "track",
                "media_id": "99",
                "name": "Song",
                "template": "T/{track_title}",
                "collection": False,
                "status": "failed",
            }
        ]
        self._reindex_queue()
        self._objs = {"track": {}}
        self._refetch_inflight = set()
        self._logged_in = True
        self._browse_gen = 1
        self._browse_lock = Lock()
        self._queue_lock = Lock()
        self._merge_plans = {}
        self.threadpool = _InlinePool()
        self.statuses: list = []
        self.downloads: list = []
        self.tidal = SimpleNamespace(session=SimpleNamespace(track=session_track))
        # The GUI hop, inlined: emit dispatches straight to the handler.
        self._queueRetryRefetched = SimpleNamespace(emit=lambda *a: self._on_queue_retry_refetched(*a))
        arm_queue(self)

    def _set_status(self, text):
        self.statuses.append(text)

    def _remember(self, bucket, key, obj):
        self._objs.setdefault(bucket, {})[key] = obj

    def _emit_queue(self):
        pass

    def _download(self, obj, typ, name, template, collection, media_id, merge_plan=None):
        self.downloads.append((typ, name, template, collection, media_id))


def test_retry_refetches_a_vanished_object_and_keeps_the_row_fields():
    obj = SimpleNamespace(id="99")
    stub = _RetryStub(session_track=lambda tid: obj)
    stub.retryQueueItem(5)

    assert stub.downloads == [("track", "Song", "T/{track_title}", False, "99")], "the stored row fields survive"
    assert stub._queue == [], "the retried row leaves the queue"


def test_a_failed_retry_refetch_leaves_the_row_retryable():
    def boom(tid):
        raise OSError("gone")

    stub = _RetryStub(session_track=boom)
    stub.retryQueueItem(5)

    assert stub.downloads == []
    assert stub._queue and stub._queue[0]["status"] == "failed", "the row keeps its RETRY"
    assert stub._refetch_inflight == set(), "a later click may try again"
    assert "That item is no longer available" in stub.statuses


# Finding 34: an inherited $APPIMAGE must not make the updater clobber a foreign file.


def test_appimage_env_alone_is_not_trusted(monkeypatch):
    monkeypatch.setenv("APPIMAGE", "/home/u/Other.AppImage")
    monkeypatch.delenv("APPDIR", raising=False)
    assert updater_mod._running_appimage() == ""

    monkeypatch.setenv("APPDIR", "/tmp/some-other-mount")
    assert updater_mod._running_appimage() == "", "this process does not run out of that mount"


def test_appimage_claim_honoured_when_running_from_the_mount(monkeypatch):
    monkeypatch.setenv("APPIMAGE", "/home/u/Waves.AppImage")
    monkeypatch.setenv("APPDIR", str(pathlib.Path(sys.executable).parent))
    assert updater_mod._running_appimage() == "/home/u/Waves.AppImage"


# Finding 35: backend-persisted settings notify the Settings page.


def test_save_settings_notifies_the_settings_page():
    stub = SimpleNamespace(
        _restore_ffmpeg_flags=lambda: None,
        _restore_ffmpeg_path=lambda: None,
        settings=SimpleNamespace(save=lambda: None, data=SimpleNamespace()),
        settingsPersistedExternally=_Signal(),
        _ffmpeg_flag_prefs={},
        _settings_save_lock=Lock(),
    )
    WavesBridge._save_settings(stub)
    assert stub.settingsPersistedExternally.emits == [()]


def test_settings_page_listens_for_external_persists():
    assert "function onSettingsPersistedExternally()" in SETTINGS_QML
    assert SETTINGS_QML.count("refreshSchema()") >= 2


# Finding 37: a revalidated drill-in page re-emits its link-tile mosaics.


class _BrowsePageStub:
    openBrowsePage = WavesBridge.openBrowsePage

    def __init__(self, cached, fresh_sections):
        self._logged_in = True
        self._browse_pages = {"pages/labels": cached}
        self._browse_loading = set()
        self._browse_gen = 1
        self.threadpool = _InlinePool()
        self.browsePageLoaded = _Signal()
        self.sampled: list = []
        self._fresh_sections = fresh_sections

    def _browse_fetch(self, title, api_path):
        return SimpleNamespace(title="Labels")

    def _page_rows(self, page):
        return self._fresh_sections

    def _sample_links_art(self, links, gen, disk=None):
        self.sampled.append(links)

    def _save_page_cache(self):
        pass

    def _set_busy(self, on):
        pass

    def _set_status(self, text):
        pass


_LINKS_SECTION = {"rowKind": "links", "items": [{"title": "Label A", "path": "pages/label_a"}]}


def test_a_revalidated_page_still_fills_its_link_mosaics():
    cached = {"key": "pages/labels", "title": "Labels", "sections": [_LINKS_SECTION], "error": False}
    stub = _BrowsePageStub(cached, [_LINKS_SECTION])  # unchanged page: no re-emit, but art must flow
    stub.openBrowsePage("pages/labels", "Labels")

    assert stub.sampled == [[("Label A", "pages/label_a")]], "cache-served revisits must not render art-less"


def test_link_tiles_of_extracts_only_link_rows():
    payload = {
        "sections": [
            _LINKS_SECTION,
            {"rowKind": "cards", "items": [{"title": "X", "path": "pages/x"}]},
            {"rowKind": "links", "items": [{"title": "No path"}]},
        ]
    }
    assert _link_tiles_of(payload) == [("Label A", "pages/label_a")]
    assert _link_tiles_of({}) == []


# Finding 38: a category download warms the folder tree before queuing.


class _CategoryStub:
    downloadPlaylistCategory = WavesBridge.downloadPlaylistCategory

    def __init__(self):
        self._logged_in = True
        self._folder_groups = {}
        self._folder_lock = Lock()
        self.downloadState = _Signal()
        self.folderRemaining = _Signal()
        self.downloadProgress = _Signal()
        self.warm_calls: list = []
        self.statuses: list = []
        self._objs = {"playlist": {}}

    def _cached_category(self, api_path):
        return [SimpleNamespace(id="p1", name="PL")]

    def _download_gate(self):
        return "ok"

    def _ffmpeg_gate_holds(self, media_id, retry):
        return False

    def _needs_folder_tree(self):
        return True

    def _warm_folder_tree(self, then, media_id=""):
        self.warm_calls.append(media_id)
        return True

    def _set_status(self, text):
        self.statuses.append(text)


def test_category_download_warms_the_tree_under_the_rollup_id():
    stub = _CategoryStub()
    stub.downloadPlaylistCategory("pages/mood/chill")

    assert stub.warm_calls == ["cat:pages/mood/chill"], "the failed-sweep clear must target the cat: button"
    assert stub.downloadState.emits == [("cat:pages/mood/chill", "preparing")]
    assert stub._folder_groups == {}, "no rollup state is published before the tree is warm"


# Finding 39: best-of-both publishes button state before its edition scan.


class _BestOfBothStub:
    downloadAlbumBestOfBoth = WavesBridge.downloadAlbumBestOfBoth

    def __init__(self, plan=None, identity_id="a1", scan_raises=False, scan_complete=True):
        self._objs = {"album": {"a1": SimpleNamespace(id="a1")}}
        self._dl = object()
        self._merge_plans = {}
        self._merge_scanned: set = set()
        self._scan_pool = _InlinePool()
        self._scan_gen = 0  # the generation STOP bumps; never bumped here
        self._scans_in_flight = 0
        self._scan_count_lock = Lock()
        self.scanningChanged = _Signal()
        self.downloadState = _Signal()
        self._albumsQueued = _Signal()
        self.statuses: list = []
        self._plan = plan
        self._identity_id = identity_id
        self._scan_raises = scan_raises
        self._scan_complete = scan_complete

    def _set_status(self, text):
        self.statuses.append(text)

    def _sibling_editions(self, obj):
        if self._scan_raises:
            raise OSError("scan blew up")
        return [SimpleNamespace(id="a1"), SimpleNamespace(id=self._identity_id)], self._scan_complete

    def _merge_recs_factory(self):
        return lambda a: []

    def _merge_rank_fn(self):
        return lambda o: 0

    def _remember(self, bucket, key, obj):
        self._objs.setdefault(bucket, {})[key] = obj


def test_best_of_both_guards_the_button_before_the_scan():
    stub = _BestOfBothStub()
    with patch(
        "tidaler.waves_ui.backend._build_merge_plan",
        return_value=(SimpleNamespace(id="a2", full_name="Album DX"), {"a2": []}, ""),
    ):
        stub.downloadAlbumBestOfBoth("a1")

    assert stub.downloadState.emits[0] == ("a1", "preparing"), "published before the multi-request scan"
    assert ("a1", "") in stub.downloadState.emits, "the clicked button is handed back on the identity handoff"
    assert stub._albumsQueued.emits == [(0, ["a2"])]


def test_best_of_both_same_identity_keeps_the_button_waiting():
    stub = _BestOfBothStub()
    with patch(
        "tidaler.waves_ui.backend._build_merge_plan",
        return_value=(SimpleNamespace(id="a1", full_name="Album"), {"a1": []}, ""),
    ):
        stub.downloadAlbumBestOfBoth("a1")

    assert stub.downloadState.emits == [("a1", "preparing")], "the merge downloads under the clicked id"
    assert stub._albumsQueued.emits == [(0, ["a1"])]


def test_a_failed_edition_scan_marks_the_button_failed():
    stub = _BestOfBothStub(scan_raises=True)
    stub.downloadAlbumBestOfBoth("a1")

    assert stub.downloadState.emits == [("a1", "preparing"), ("a1", "failed")]
    assert stub._albumsQueued.emits == []


def test_a_failed_edition_scan_leaves_the_album_rescannable():
    # downloadAlbum marks the album before dispatching the scan, and nothing
    # will consume that mark now the scan queued nothing. Left set, the "try
    # again" the status line just invited downloaded the album plain instead.
    stub = _BestOfBothStub(scan_raises=True)
    stub._merge_scanned.add("a1")

    stub.downloadAlbumBestOfBoth("a1")

    assert "a1" not in stub._merge_scanned


def test_an_incomplete_sibling_scan_does_not_claim_no_richer_edition():
    # One artist bucket failed, so the scan never saw the whole catalogue. It
    # must fail visibly like a partial discography does, not report the absence
    # of something it did not look for.
    stub = _BestOfBothStub(scan_complete=False)

    stub.downloadAlbumBestOfBoth("a1")

    assert stub.downloadState.emits == [("a1", "preparing"), ("a1", "failed")]
    assert stub._albumsQueued.emits == []
    assert stub.statuses[-1] == "Could not scan editions, try again"


# QML source guards (findings 29, 31, 36, 40).


def test_browse_retry_routes_by_page_key():
    assert "function retryBrowsePage()" in MAIN_QML
    assert "root.retryBrowsePage()" in MAIN_QML
    assert "openBrowseLink(root.browsePageKey" not in MAIN_QML, "RETRY must not route pl:/item: keys to openBrowsePage"
    body = MAIN_QML.split("function retryBrowsePage()", 1)[1].split("\n    }", 1)[0]
    assert "openBrowsePlaylists" in body and "openBrowseItem" in body and "openBrowsePage" in body


def test_recycled_album_rows_reset_their_selection():
    assert "onAlbumIdChanged: sel = ({})" in MAIN_QML


def test_unc_folder_urls_stay_absolute():
    body = SETTINGS_QML.split("function urlToPath(u)", 1)[1].split("\n    }", 1)[0]
    assert 's = "//" + s' in body, "a host-authority file URL is a UNC share, never a relative path"


def test_back_restore_latch_clears_on_a_failed_artist_load():
    assert "function onArtistLoadFailed(id)" in MAIN_QML
    body = MAIN_QML.split("function onArtistLoadFailed(id)", 1)[1].split("\n        }", 1)[0]
    assert "_navRestoring = false" in body
