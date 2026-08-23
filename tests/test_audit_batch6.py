"""Tier-3 fixes from the 2026-08-02 audit (findings 41-64).

Hermetic: real unbound WavesBridge/updater/helper functions bound onto minimal
stubs; no Qt, no network. One test per guard, so each fix can be
knockout-verified independently.
"""

from __future__ import annotations

import pathlib
import subprocess
import time
from threading import Lock, Thread
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from test_updater import _manifest, _prep

from tidaler.helper.path import FILENAME_LENGTH_MAX, format_path_media, path_file_uniquify
from tidaler.helper.tidal import name_builder_album_artist, user_media_lists
from tidaler.waves_ui import backend as backend_mod
from tidaler.waves_ui import signing
from tidaler.waves_ui.backend import WavesBridge, _graft_scroll_growth
from tidaler.waves_ui.updater import UpdateCancelled

BACKEND_SRC = pathlib.Path(backend_mod.__file__).read_text(encoding="utf-8")


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
    def __init__(self):
        self.workers: list = []

    def start(self, worker):
        self.workers.append(worker)


# Findings 41 + 43: logout drops the old account's live objects, and flips the
# logged-in flag BEFORE deleting the snapshot.


class _LogoutStub:
    logout = WavesBridge.logout

    def __init__(self):
        self.events: list = []
        # logout() ends the downloads before it touches the session (issue #30).
        self.stopAll = lambda: self.events.append("stopAll")
        self.tidal = SimpleNamespace(logout=lambda: None)
        self._reset_tidal_session = lambda: None
        self._lib_cache: dict = {}
        self._lib_loading: set = set()
        self._lib_sort: dict = {}
        self._fav_ids: dict = {}
        self._pending_lock = Lock()
        self._pending_downloads: list = []
        self._lib_gen = 0
        self._browse_root_cache = {}
        self._browse_pages: dict = {}
        self._browse_loading: set = set()
        self._category_pl: dict = {}
        self._browse_gen = 0
        self._browse_reval_ts = 1.0
        self._prefetch_lock = Lock()
        self._prefetch_key = None
        self._prefetch_claimed = False
        self._prefetch_unrecorded: set = set()
        self._album_tracks_inflight: dict = {}
        self._album_tracks_unrecorded: set = set()
        self._item_fetch_ts: dict = {}
        self._artist_cache: dict = {}
        self._artist_loading: set = set()
        self._album_tracks_cache: dict = {}
        self._home_cache = None
        self._home_loading = False
        self._home_reval_ts = 1.0
        self._lib_reval_ts: dict = {}
        self._media_lists_cache = None
        self._media_lists_lock = Lock()
        self._folder_tree = None
        self._tree_warm_waiting: list = []
        self._search_cache: dict = {}
        self._artist_pop_cache: dict = {}
        self._objs = {"album": {"a1": object()}, "track": {"t1": object()}}
        self._page_cache_path = "/nonexistent/page_cache.json"

    def _set_logged_in(self, on):
        self.events.append(("logged_in", on))

    def _set_status(self, text):
        pass


def test_logout_clears_the_old_accounts_live_objects():
    stub = _LogoutStub()
    stub.logout()
    assert stub._objs == {"album": {}, "track": {}}, "revisited ids must re-fetch through the NEW session"


def test_logout_flips_the_flag_before_deleting_the_snapshot():
    stub = _LogoutStub()
    with patch.object(backend_mod.os, "remove", side_effect=lambda p: stub.events.append(("remove", p))):
        stub.logout()
    assert stub.events.index(("logged_in", False)) < stub.events.index(
        ("remove", stub._page_cache_path)
    ), "a worker mid-save must already see logged_in False when the file goes"


# Finding 42: capped-cache eviction is serialized (two workers evicting
# concurrently raced dict iteration).


def test_concurrent_capped_inserts_never_race():
    stub = SimpleNamespace(_evict_lock=Lock())
    d: dict = {}
    errors: list = []

    def hammer(tag):
        try:
            for i in range(3000):
                WavesBridge._remember_capped(stub, d, f"{tag}{i}", i, 10)
        except Exception as exc:  # pragma: no cover - the failure mode under test
            errors.append(exc)

    threads = [Thread(target=hammer, args=(t,)) for t in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(d) == 10


# Finding 44: _browse_pages is capped like every sibling cache.


def test_browse_pages_writes_all_go_through_the_cap():
    import re

    assert "_BROWSE_PAGES_MAX" in BACKEND_SRC
    direct_writes = re.findall(r"self\._browse_pages\[[^\]]*\]\s*=", BACKEND_SRC)
    assert direct_writes == [], "every _browse_pages write must go through _remember_capped"


def test_remember_capped_evicts_oldest():
    stub = SimpleNamespace(_evict_lock=Lock())
    d: dict = {}
    for i in range(45):
        WavesBridge._remember_capped(stub, d, str(i), i, 40)
    assert len(d) == 40 and "0" not in d and "44" in d


# Finding 45: the tile-art memory cache honours the 7-day TTL.


class _TileArtStub:
    _sample_links_art = WavesBridge._sample_links_art
    _TILE_ART_TTL = WavesBridge._TILE_ART_TTL

    def __init__(self, mem, disk):
        self._tile_art_mem = mem
        self._disk = disk
        self._tile_art_running = False
        self._browse_gen = 1
        self._logged_in = True
        self.browseTileArt = _Signal()
        self.threadpool = _HeldPool()

    def _tile_art_disk(self):
        return self._disk


def test_a_fresh_mem_tile_entry_is_served():
    stub = _TileArtStub({"pages/x": (time.time(), ["u1"])}, {})
    stub._sample_links_art([("X", "pages/x")], 1)
    assert stub.browseTileArt.emits == [("pages/x", ["u1"])]
    assert stub.threadpool.workers == []


def test_an_expired_mem_tile_entry_is_resampled():
    old = time.time() - WavesBridge._TILE_ART_TTL - 10
    stub = _TileArtStub({"pages/x": (old, ["u1"])}, {"pages/x": {"ts": old, "arts": ["u1"]}})
    stub._sample_links_art([("X", "pages/x")], 1)
    assert stub.browseTileArt.emits == [], "week-old art must not be served forever in an always-on app"
    assert len(stub.threadpool.workers) == 1, "the stale tile goes back to the sampler"


# Finding 46: the ownership cache is bounded.


def test_own_cache_is_bounded():
    stub = SimpleNamespace(_own_cache={}, _OWN_CACHE_MAX=WavesBridge._OWN_CACHE_MAX)
    for i in range(WavesBridge._OWN_CACHE_MAX + 7):
        stub._own_cache[str(i)] = (0.0, None)
        WavesBridge._evict_own_cache_locked(stub)
    assert len(stub._own_cache) == WavesBridge._OWN_CACHE_MAX
    assert "0" not in stub._own_cache


# Finding 47: an in-flight scroll page is dropped when the sort changed.


class _MoreStub:
    loadMoreLibrary = WavesBridge.loadMoreLibrary

    def __init__(self):
        self._logged_in = True
        self._lib_cache = {"albums": {"items": [{"id": "old"}], "offset": 40, "more": True}}
        self._lib_loading: set = set()
        self._lib_gen = 1
        self._lib_sort: dict = {}
        self.threadpool = _HeldPool()
        self.libraryMore = _Signal()
        self.statuses: list = []

    def _library_page(self, category, offset, limit):
        return [{"id": "page2"}], True

    def _lib_count(self, category, items):
        return len(items)

    def _lib_status(self, category, shown, more):
        return f"{shown}"

    def _set_status(self, text):
        self.statuses.append(text)


def test_a_scroll_page_arriving_after_a_resort_is_dropped():
    stub = _MoreStub()
    stub.loadMoreLibrary("albums")
    # The user re-sorts while the page is in flight (setLibrarySort replaces
    # the cache and bumps the generation via loadLibrary).
    stub._lib_sort["albums"] = ("name", "asc")
    stub._lib_cache["albums"] = {"items": [{"id": "resorted"}], "offset": 0, "more": True}
    stub.threadpool.workers[0].fn()

    assert stub.libraryMore.emits == [], "the stale window must not splice into the re-sorted list"
    assert stub._lib_cache["albums"]["items"] == [{"id": "resorted"}]
    assert "albums" not in stub._lib_loading, "the next scroll may load again"


def test_a_scroll_page_with_unchanged_sort_still_appends():
    stub = _MoreStub()
    stub.loadMoreLibrary("albums")
    stub.threadpool.workers[0].fn()
    assert stub.libraryMore.emits == [("albums", [{"id": "page2"}], True)]
    assert [r["id"] for r in stub._lib_cache["albums"]["items"]] == ["old", "page2"]


# Finding 48: a walking caller never accepts the Mixes visit's treeless entry.


class _MediaListsStub:
    _media_lists = WavesBridge._media_lists
    _MEDIA_LISTS_TTL = WavesBridge._MEDIA_LISTS_TTL

    def __init__(self, cached_tree):
        self._media_lists_lock = Lock()
        self._media_lists_cache = (time.monotonic(), {"playlists": []}, cached_tree)
        self._folder_tree = cached_tree
        self.tidal = SimpleNamespace(session=object())
        self.swept = 0


def test_a_walking_caller_rejects_the_treeless_mixes_entry():
    stub = _MediaListsStub(cached_tree=None)
    tree = SimpleNamespace(nodes=[1], playlist_paths={}, partial=False)
    with (
        patch.object(backend_mod, "user_media_lists", return_value={"playlists": []}),
        patch.object(backend_mod, "walk_playlist_tree", return_value=tree),
    ):
        _fresh, got = stub._media_lists(refresh=True, walk=True)
    assert got is tree, "Playlists within the TTL must sweep, not render folder-less"


def test_a_treeless_entry_still_serves_non_walking_callers():
    stub = _MediaListsStub(cached_tree=None)
    fresh, got = stub._media_lists(refresh=True, walk=False)
    assert fresh == {"playlists": []} and got is None, "Mixes keeps its fast path"


# Finding 49: revalidation must not discard the user's endless-scroll growth.


def _row(items, data="pages/data/1", title="New tracks", offset=None, total=None):
    row = {"rowKind": "cards", "title": title, "data": data, "items": items}
    if offset is not None:
        row["offset"] = offset
    if total is not None:
        row["total"] = total
    return row


def test_scroll_growth_is_grafted_onto_an_unchanged_revalidate():
    fresh = {"sections": [_row([{"id": 1}, {"id": 2}], offset=12)]}
    cached = {"sections": [_row([{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}], offset=62, total=100)]}
    _graft_scroll_growth(fresh, cached)
    assert fresh == cached, "the user's own scrolling must not read as changed content"


def test_a_really_changed_row_keeps_the_fresh_content():
    fresh = {"sections": [_row([{"id": 9}, {"id": 1}])]}
    cached = {"sections": [_row([{"id": 1}, {"id": 2}, {"id": 3}])]}
    _graft_scroll_growth(fresh, cached)
    assert fresh["sections"][0]["items"] == [{"id": 9}, {"id": 1}], "a new ordering wins over stale growth"


# Findings 50 + 51: the remount heal needs an identity check, and a failed
# probe cleanup is not a dead folder.


def test_remount_heal_requires_the_library_folder_to_exist(tmp_path):
    vroot = tmp_path / "Volumes"
    (vroot / "T7 1").mkdir(parents=True)  # the OTHER drive: no Music tree
    (vroot / "T7").mkdir()
    dead = vroot / "T7" / "Music"
    dead.mkdir()
    dead.chmod(0o500)  # the original volume went read-only ("dead")
    try:
        verdict, _path = WavesBridge._probe_folder_verdict(str(dead), volumes_root=str(vroot))
    finally:
        dead.chmod(0o700)
    assert verdict == "dead", "a same-stem drive WITHOUT the library folder is a different drive"
    assert not (vroot / "T7 1" / "Music").exists(), "healing must never create the tree on a candidate"


def test_remount_heal_still_follows_a_genuine_remount(tmp_path):
    vroot = tmp_path / "Volumes"
    (vroot / "T7 1" / "Music").mkdir(parents=True)  # the remount carries the library
    (vroot / "T7").mkdir()
    dead = vroot / "T7" / "Music"
    dead.mkdir()
    dead.chmod(0o500)
    try:
        verdict, path = WavesBridge._probe_folder_verdict(str(dead), volumes_root=str(vroot))
    finally:
        dead.chmod(0o700)
    assert verdict == "healed" and path == str(vroot / "T7 1" / "Music")


def test_a_failed_probe_cleanup_still_reads_as_writable(tmp_path):
    with patch.object(pathlib.Path, "unlink", side_effect=OSError("EACCES")):
        verdict, _ = WavesBridge._probe_folder_verdict(str(tmp_path), volumes_root=str(tmp_path / "none"))
    assert verdict == "ok", "the write proved the folder works; a failed cleanup must not block downloads"


# Finding 52: a healed folder reaches the in-flight job.


def test_the_download_worker_follows_a_healed_base():
    assert (
        "dl.path_base = self.settings.data.download_base_path" in BACKEND_SRC
    ), "after the gate passes, the job must adopt the (possibly healed) setting"


# Finding 53: liveness is stamped for the path that was proven, and a landing
# track only vouches for the folder it landed in.


def test_liveness_stamps_the_proven_path():
    # _remember_share_origin rides on proof of life (share-remount feature);
    # here only the stamp itself is under test.
    stub = SimpleNamespace(_base_ok=("", 0.0), _remember_share_origin=lambda base: None)
    WavesBridge._note_download_base_ok(stub, "/Volumes/A/Music")
    assert stub._base_ok[0] == "/Volumes/A/Music"


class _LifecycleStub:
    _track_lifecycle = WavesBridge._track_lifecycle

    def __init__(self, base):
        self._job_tracks: dict = {}
        self._job_signals: dict = {}
        self._own_pool = _HeldPool()
        self.queueTrackState = _Signal()
        self.stamps: list = []
        self.settings = SimpleNamespace(data=SimpleNamespace(download_base_path=base))

    def _note_download_base_ok(self, base):
        self.stamps.append(base)

    def _prune_job_tracks(self):
        pass

    # _track_lifecycle also rolls the registry up onto the job's queue row.
    # These tests are about the liveness stamp and drive it with no queue, so
    # the row lookup answers None and the rollup no-ops.
    def _queue_item(self, qid):
        return None

    def _emit_queue(self):
        pass


def test_a_track_landing_in_the_old_folder_does_not_vouch_for_the_new_one(tmp_path):
    stub = _LifecycleStub(base=str(tmp_path / "NewLibrary"))
    stub._track_lifecycle(1, {"id": "t1", "status": "done", "path": str(tmp_path / "OldLibrary" / "a.flac")})
    assert stub.stamps == [], "changing the folder mid-download must not skip the new folder's probe"


def test_a_track_landing_under_the_current_folder_stamps_liveness(tmp_path):
    base = tmp_path / "Library"
    stub = _LifecycleStub(base=str(base))
    stub._track_lifecycle(1, {"id": "t1", "status": "done", "path": str(base / "a.flac")})
    assert stub.stamps == [str(base)]


# Finding 55: the progress relay survives until queued track events drain.


def test_release_job_signals_defers_the_pop_through_the_queued_hop():
    stub = SimpleNamespace(_job_signals={}, _jobSignalsReleased=_Signal())
    dropped: list = []
    sig = SimpleNamespace(deleteLater=lambda: dropped.append("deleted"))
    stub._job_signals[7] = sig

    WavesBridge._release_job_signals(stub, 7)
    assert 7 in stub._job_signals, "the relay must survive the worker's release call"
    assert stub._jobSignalsReleased.emits == [(7,)]

    WavesBridge._drop_job_signals(stub, 7)
    assert stub._job_signals == {} and dropped == ["deleted"]


# Finding 56: a failed preview remux removes its orphaned output temp.


class _RemuxStub:
    _remux_preview = WavesBridge._remux_preview


def test_a_failed_remux_removes_the_output_temp(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    import tempfile

    tempfile.tempdir = None  # re-read TMPDIR
    stub = _RemuxStub()
    with pytest.raises(subprocess.CalledProcessError):
        stub._remux_preview("/usr/bin/false", "https://cdn/x", None, whole=False)
    leftovers = list(tmp_path.glob("waves_preview_*"))
    assert leftovers == [], "an orphan here is invisible to every shutdown sweep"
    tempfile.tempdir = None


def test_a_successful_remux_keeps_the_clip(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    import tempfile

    tempfile.tempdir = None
    stub = _RemuxStub()
    out = stub._remux_preview("/usr/bin/true", "https://cdn/x", None, whole=False)
    assert pathlib.Path(out).exists(), "on success the file IS the preview; cleanup must not be a finally"
    tempfile.tempdir = None


# Findings 57 + 58: shutdown drains the ownership pool before closing its
# store, never clears it, and aborts an in-flight FFmpeg install.


class _Pool:
    def __init__(self, log, name):
        self.log, self.name = log, name

    def clear(self):
        self.log.append(("clear", self.name))

    def waitForDone(self, ms=0):
        self.log.append(("wait", self.name))


class _ShutdownStub:
    shutdown = WavesBridge.shutdown

    def __init__(self):
        from threading import Event

        self.log: list = []
        self._event_abort = Event()
        self._event_run = Event()
        self._ffmpeg_abort = Event()
        self._job_aborts: dict = {}
        self.dl_pool = _Pool(self.log, "dl")
        self._scan_pool = _Pool(self.log, "scan")
        self.threadpool = _Pool(self.log, "main")
        self._own_pool = _Pool(self.log, "own")
        self._ownership = SimpleNamespace(close=lambda: self.log.append(("close", "ownership")))
        self._preview_clips: dict = {}


def test_shutdown_drains_the_ownership_pool_before_closing_the_store():
    stub = _ShutdownStub()
    stub.shutdown()
    assert stub.log.index(("wait", "own")) < stub.log.index(("close", "ownership"))
    assert ("clear", "own") not in stub.log, "a queued ownership write is a real record and must land"
    assert stub._ffmpeg_abort.is_set(), "a quit mid-FFmpeg-install must not orphan a partial archive"


# Findings 59 + 60: updater Cancel reaches the apply phase, and a failed apply
# leaves no extracted app copy behind.


def test_install_cancel_after_extraction_is_honoured_and_staging_swept(monkeypatch, tmp_path):
    from threading import Event

    pub, priv = signing.keygen()
    payload = b"new-waves-binary"
    manifest = _manifest(payload)
    up, _applied = _prep(
        monkeypatch, tmp_path, payload=payload, manifest=manifest, signature=signing.sign(manifest, priv), pubkey=pub
    )

    def cancelling_apply(self, p, rel, log, abort=None):
        # The user clicks Cancel while the apply phase runs: only a forwarded
        # abort can be honoured here (pre-setting it would trip the earlier
        # install() checks and never reach this phase at all).
        staged = self.staging_dir / "staged"
        staged.mkdir(parents=True, exist_ok=True)
        (staged / "Waves").write_bytes(b"extracted app copy")
        assert abort is not None, "abort was not forwarded into _apply"
        abort.set()
        raise UpdateCancelled()

    monkeypatch.setattr(type(up), "_apply", cancelling_apply, raising=True)
    with pytest.raises(UpdateCancelled):
        up.install(session=object(), abort=Event())
    assert not (up.staging_dir / "staged").exists(), "no full app copy may linger in the config dir"


def test_a_failed_apply_sweeps_the_staged_copy(monkeypatch, tmp_path):
    pub, priv = signing.keygen()
    payload = b"new-waves-binary"
    manifest = _manifest(payload)
    up, _applied = _prep(
        monkeypatch, tmp_path, payload=payload, manifest=manifest, signature=signing.sign(manifest, priv), pubkey=pub
    )

    def failing_apply(self, p, rel, log, abort=None):
        staged = self.staging_dir / "staged"
        staged.mkdir(parents=True, exist_ok=True)
        (staged / "Waves").write_bytes(b"extracted app copy")
        raise OSError("swap blew up")

    monkeypatch.setattr(type(up), "_apply", failing_apply, raising=True)
    with pytest.raises(OSError):
        up.install(session=object())
    assert not (up.staging_dir / "staged").exists()


# Finding 61: a token with no value substitutes "", never a literal brace.


def _album(release_date=None, available=None):
    import datetime

    from tidalapi import Album

    a = Album.__new__(Album)
    a.id = 1
    a.name = "Old Album"
    a.release_date = release_date
    # year/available_release_date are derived properties; the TIDAL release
    # stamp is the writable field feeding {album_year} when release_date is None.
    a.tidal_release_date = available or datetime.datetime(2011, 1, 1)
    return a


def test_album_date_without_a_value_never_writes_literal_braces():
    out = format_path_media("{album_year} {album_date}/x", _album())
    assert "{album_date}" not in out
    assert out.startswith("2011")


def test_isrc_without_a_value_never_writes_literal_braces():
    from tidalapi import Track

    t = Track.__new__(Track)
    t.id = 2
    t.name = "Song"
    t.isrc = None
    out = format_path_media("{isrc}/song", t)
    assert out == "song"


# Finding 62: {album_artist} with no credits must not IndexError.


def test_album_artist_with_no_credits_is_empty_not_a_crash():
    with patch("tidaler.helper.tidal.get_album_artists", return_value=[]):
        assert name_builder_album_artist(SimpleNamespace(), first_only=True) == ""


# Finding 63: a mixes failure must not throw away the playlist sweep.


def _session_with_mixes(mixes_result):
    favorites = SimpleNamespace(
        playlists_paginated=lambda: [SimpleNamespace(id="p1")],
        playlist_folders=lambda limit, offset, parent_folder_id: [],
    )
    return SimpleNamespace(user=SimpleNamespace(favorites=favorites), mixes=mixes_result)


def test_a_mixes_failure_keeps_the_playlists():
    def boom():
        raise RuntimeError("mixes endpoint changed shape")

    out = user_media_lists(_session_with_mixes(boom))
    assert [p.id for p in out["playlists"]] == ["p1"], "the paid-for playlist paging must survive"
    assert out["mixes"] == []


def test_empty_mix_categories_keep_the_playlists():
    out = user_media_lists(_session_with_mixes(lambda: SimpleNamespace(categories=[])))
    assert [p.id for p in out["playlists"]] == ["p1"]
    assert out["mixes"] == []


# Finding 64: uniquify bounds the FILENAME, not the whole path.


def test_uniquify_leaves_a_short_name_at_a_deep_path_alone():
    deep = pathlib.Path("/" + "/".join(["d" * 10] * 40)) / "song.flac"
    with patch("tidaler.helper.path.file_unique_suffix", return_value="_01"):
        out = path_file_uniquify(deep)
    assert out.name == "song_01.flac", "path depth must never shred a short filename"


def test_uniquify_still_bounds_a_maximal_filename():
    long_name = "x" * FILENAME_LENGTH_MAX + ".flac"
    with patch("tidaler.helper.path.file_unique_suffix", return_value="_01"):
        out = path_file_uniquify(pathlib.Path("/music") / long_name)
    assert len(out.name) <= FILENAME_LENGTH_MAX
    assert out.name.endswith("_01.flac")
