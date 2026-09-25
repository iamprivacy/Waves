"""Pins for the final audit of 2026-09-24, backend and QML half (report in
notes/, private). Behavioural where the harness allows; the QML rows are
text pins, the way the max-age timers are pinned."""

from __future__ import annotations

import os
import pathlib
import threading
import time
from threading import Event, Lock, local
from types import SimpleNamespace
from unittest.mock import patch

from tidalapi.media import Quality, Track

from waves import config as config_mod
from waves.ownership import OwnershipStore
from waves.waves_ui import backend
from waves.waves_ui.backend import (
    WavesBridge,
    _align_edition,
    _build_merge_plan,
    _collapse_editions_by_side,
    _copy_is_current,
    _merge_rec_title,
    _MergeRec,
    _network_share_anchors,
    _PlanEntry,
    _TrackedDownload,
)

QML = pathlib.Path(__file__).resolve().parents[1] / "waves" / "waves_ui" / "qml" / "Main.qml"


class _Signal:
    def __init__(self):
        self.calls: list = []

    def emit(self, *a):
        self.calls.append(a)


def _bind(obj, *names):
    for name in names:
        setattr(obj, name, getattr(WavesBridge, name).__get__(obj, type(obj)))


# ---- C17: a shared ISRC never pairs an album cut with a radio edit -----------
def _rec(tid, title, dur, isrc=None, explicit=False, rank=1):
    obj = SimpleNamespace(id=tid, name=title, duration=dur, isrc=isrc, track_num=1, volume_num=1, rank=rank)
    return _MergeRec(obj, _merge_rec_title(obj), dur, isrc, explicit)


def test_the_isrc_pass_refuses_a_candidate_whose_length_contradicts_the_code():
    template = [_rec("d1", "Song", 320, "USX1")]
    edit_only = [_rec("s1", "Song (Radio Edit)", 190, "USX1")]
    assert _align_edition(template, edit_only) == {}, "a 3:10 edit is not the 5:20 album cut"
    close = [_rec("s1", "Song", 322, "USX1")]
    assert list(_align_edition(template, close)) == [0], "a re-listing within seconds still pairs"
    unknown = [_rec("s1", "Song", None, "USX1")]
    assert list(_align_edition(template, unknown)) == [0], "an unknown length does not contradict the code"


def test_a_one_sided_radio_edit_declines_the_merge_instead_of_landing_under_the_album_cut():
    deluxe = SimpleNamespace(id="dlx", rank=1)
    standard = SimpleNamespace(id="std", rank=2)
    recs = {
        id(deluxe): [_rec("d1", "Song", 320, "USX1"), _rec("d2", "Other", 200, "USX2")],
        id(standard): [_rec("s1", "Song (Radio Edit)", 190, "USX1", rank=2)],
    }
    template, plan, reason = _build_merge_plan(
        [deluxe, standard], lambda a: recs[id(a)], lambda o: getattr(o, "rank", 1)
    )
    assert (template, plan, reason) == (None, None, "not_superset")


# ---- C16: a refused borrowed slot falls back to the identity's own cut ------
class _FakeSettings:
    data = SimpleNamespace(downloads_concurrent_max=2, download_delay=False)


class _Engine:
    """The little the merge fan-out asks of the engine, refusing every stream
    of the 'std' edition the way TIDAL withholds a listed release."""

    _landed_paths = staticmethod(backend.Download._landed_paths)

    def __init__(self):
        self.unavailable_count = 0
        self.fetched: list = []
        self._lock = threading.Lock()

    def item(self, **kw):
        media = kw["media"]
        with self._lock:
            self.fetched.append((str(media.id), kw["list_position"]))
            if str(media.id).startswith("std"):
                self.unavailable_count += 1
                return False, ""
        return True, f"/out/{media.id}"

    def _playlist_for_collection(self, media, template, paths):
        self.playlist = list(paths)


def _merge_bridge(session_tracks: dict):
    bridge = WavesBridge.__new__(WavesBridge)
    bridge.settings = _FakeSettings()
    bridge.tidal = SimpleNamespace(session=SimpleNamespace(track=lambda tid: session_tracks[int(tid)]))
    return bridge


def _track(tid):
    return SimpleNamespace(id=tid, name=tid, duration=200, track_num=1, volume_num=1)


def test_a_borrowed_slot_tidal_refuses_is_fetched_from_the_identity_edition():
    own = {101: _track("dlx-1"), 102: _track("dlx-2")}
    bridge = _merge_bridge(own)
    engine = _Engine()
    plan = [
        _PlanEntry(_track("std-1"), 1, 1, "101"),
        _PlanEntry(_track("std-2"), 2, 1, "102"),
        _PlanEntry(_track("dlx-3"), 3, 1, "103"),
    ]
    bridge._download_merge_plan(engine, SimpleNamespace(list_item=_Signal()), Event(), object(), "t", plan)
    assert ("dlx-1", 1) in engine.fetched and ("dlx-2", 2) in engine.fetched, "the album's own cut fills the slot"
    assert engine.playlist == [pathlib.Path("/out/dlx-1"), pathlib.Path("/out/dlx-2"), pathlib.Path("/out/dlx-3")]


def test_a_slot_refused_both_ways_counts_once_and_stays_a_refusal():
    bridge = _merge_bridge({101: _track("std-x")})  # the identity's own cut is withheld too
    engine = _Engine()
    plan = [_PlanEntry(_track("std-1"), 1, 1, "101"), _PlanEntry(_track("dlx-2"), 2, 1, "102")]
    bridge._download_merge_plan(engine, SimpleNamespace(list_item=_Signal()), Event(), object(), "t", plan)
    assert engine.unavailable_count == 2, "two refusals were counted by the engine"
    # and neither raised: one song of two is a refusal, not a failure


def test_a_slot_that_was_not_borrowed_has_no_fallback():
    bridge = _merge_bridge({})
    assert bridge._identity_recording(_track("101"), "101") is None
    assert bridge._identity_recording(_track("std-1"), "") is None


# ---- C18 + L27: the drawer lists every plan slot, at the source's tier -------
class _DrawerStub:
    def __init__(self):
        self._job_tracks = {}
        self._job_owned = {}
        self._job_fetched = {}
        self._queue = [{"qid": 1, "media_id": "m1", "status": "running"}]
        self._queue_index = {1: self._queue[0]}
        self._queue_lock = Lock()
        self.queueTracksLoaded = _Signal()
        _bind(self, "_merge_queue_tracks", "_queue_item")


def test_a_short_album_read_still_lists_the_registry_slots_it_missed():
    stub = _DrawerStub()
    stub._job_tracks[1] = {
        "b": {"id": "b", "title": "B", "num": 2, "vol": 1, "status": "failed", "pct": 0.0, "expected": "HI-RES"},
        "c": {"id": "c", "title": "C", "num": 3, "vol": 1, "status": "done", "pct": 100.0, "expected": "LOSSLESS"},
    }
    stub._merge_queue_tracks(1, [{"id": "a", "num": 1, "title": "A", "duration": "3:00", "expected": "LOSSLESS"}])
    rows = stub.queueTracksLoaded.calls[-1][1]
    assert [r["id"] for r in rows] == ["a", "b", "c"], "the failed slot past the short read is visible"
    assert rows[1]["status"] == "failed" and rows[1]["num"] == 2


def test_a_borrowed_slot_predicts_the_source_editions_tier():
    stub = _DrawerStub()
    stub._job_tracks[1] = {
        "a": {"id": "a", "title": "A", "num": 1, "vol": 1, "status": "pending", "expected": "HI-RES"}
    }
    stub._merge_queue_tracks(1, [{"id": "a", "num": 1, "title": "A", "duration": "3:00", "expected": "LOSSLESS"}])
    assert stub.queueTracksLoaded.calls[-1][1][0]["expected"] == "HI-RES"


def test_the_drawer_reads_the_long_album_list():
    import inspect

    src = inspect.getsource(WavesBridge.loadQueueTracks)
    assert "_album_tracks_full(obj)" in src and "obj.tracks()" not in src


# ---- C38: the completeness collapse never crosses the clean/explicit divide -
def _edition(name, aid):
    return SimpleNamespace(name=name, id=aid)


def _songs(prefix, explicit, *rows):
    return [_MergeRec(SimpleNamespace(id=f"{prefix}{i}"), t, d, None, explicit) for i, (t, d) in enumerate(rows)]


def _collapse(mode):
    clean = _edition("Album [Clean]", "cln")
    deluxe = _edition("Album (Deluxe) [Explicit]", "dlx")
    recs = {
        id(clean): _songs("c", False, ("one", 200), ("two", 240)),
        id(deluxe): _songs("d", True, ("one", 200), ("two", 240), ("bonus", 180)),
    }
    with patch("waves.waves_ui.backend._edition_base_key", lambda a: "one release"):
        kept = _collapse_editions_by_side(
            [clean, deluxe],
            lambda a: recs[id(a)],
            lambda a: [(r.title, r.dur) for r in recs[id(a)]],
            lambda a: 1,
            "keep_both",
            None,
            mode,
        )
    return [a.id for a in kept]


def test_a_clean_preference_keeps_the_clean_album_over_the_explicit_deluxe():
    assert _collapse("clean") == ["cln"]


def test_an_explicit_preference_keeps_the_explicit_deluxe_only():
    assert _collapse("explicit") == ["dlx"]


def test_both_keeps_one_edition_per_side():
    assert _collapse("both") == ["cln", "dlx"]


def test_the_sweep_and_the_artist_page_both_split_before_collapsing():
    import inspect

    assert "_collapse_editions_by_side" in inspect.getsource(WavesBridge._collapse_editions)
    src = inspect.getsource(WavesBridge._hide_subset_editions)
    assert src.count("_collapse_editions_by_side") == 1, "the non-merge branch splits too"


# ---- C39: same-titled guest spots of different length are different songs --
def test_the_guest_track_key_tells_two_intros_apart_by_length():
    stub = SimpleNamespace()
    _bind(stub, "_track_key")
    a = SimpleNamespace(id=1, name="Intro", duration=62, artist=SimpleNamespace(name="Head"), artists=[])
    b = SimpleNamespace(id=2, name="Intro", duration=201, artist=SimpleNamespace(name="Head"), artists=[])
    c = SimpleNamespace(id=3, name="Intro", duration=63, artist=SimpleNamespace(name="Head"), artists=[])
    assert stub._track_key(a) != stub._track_key(b)
    assert stub._track_key(a) == stub._track_key(c), "a re-listing of one recording still collapses"


# ---- L00: a parked mix page re-asks TIDAL on the max-age tick ----------------
def test_a_mix_revalidate_forgets_the_retrieved_mix_first():
    stub = SimpleNamespace(
        _logged_in=True,
        _browse_pages={"item:mix:7": {"key": "item:mix:7"}},
        _item_fetch_ts={},
        _prefetch_lock=Lock(),
        _browse_loading=set(),
        _objs={"mix": {"7": object()}},
        _objs_lock=Lock(),
        _ITEM_FRESH_S=60.0,
        builds=[],
    )
    stub._start_browse_item_build = lambda *a: stub.builds.append(a)
    _bind(stub, "refreshBrowseItem")
    stub.refreshBrowseItem("mix", "7")
    assert "7" not in stub._objs["mix"], "the build fetches a fresh Mix and its items"
    assert stub.builds and stub.builds[0][4] is True


# ---- C22: the presence pill asks with the audio count ------------------------
def test_the_card_dressing_asks_presence_with_the_audio_count():
    stub = SimpleNamespace(
        asked=[], _library_stamp=0, _own_generation=0, _CARD_DRESS_KINDS=WavesBridge._CARD_DRESS_KINDS
    )
    stub.libraryAlbumPresence = lambda *a: stub.asked.append(a) or {"present": False}
    stub.collectionOwnership = lambda cid: {"verdict": "no"}
    _bind(stub, "_dress_card")
    stub._dress_card({"kind": "album", "id": "1", "title": "T", "tracks": 14, "audio_tracks": 12})
    assert stub.asked[0][3] == 12
    stub._dress_card({"kind": "album", "id": "1", "title": "T", "tracks": 14})  # an older snapshot
    assert stub.asked[1][3] == 14


def test_the_qml_pills_prefer_the_audio_count():
    src = QML.read_text(encoding="utf-8")
    assert src.count("a.audio_tracks !== undefined ? a.audio_tracks : a.tracks") == 3
    assert src.count("c.audio_tracks !== undefined ? c.audio_tracks : c.tracks") == 2
    assert "a.tracks || 0, a.duration_sec" not in src and "c.tracks || 0, c.duration_sec" not in src


# ---- C37: an owned Atmos-only copy reads current with Atmos off --------------
class _InlinePool:
    def start(self, worker):
        worker.run()


def _own_bridge(store, *, quality, atmos_on):
    b = WavesBridge.__new__(WavesBridge)
    b._ownership = store
    b._own_cache = {}
    b._own_lock = Lock()
    b._own_pending = set()
    b._own_pool = _InlinePool()
    b._announce_ownership = lambda tid: None
    b._downloads_running = lambda: False
    b.settings = SimpleNamespace(data=SimpleNamespace(quality_audio=quality, download_dolby_atmos=atmos_on))
    _bind(b, "ownershipOf", "_would_refetch_atmos", "_target_quality_rank", "_own_refresh", "_evict_own_cache_locked")
    return b


def test_an_atmos_only_copy_stays_current_under_a_max_setting_with_atmos_off(tmp_path):
    store = OwnershipStore(str(tmp_path / "own.db"))
    p = tmp_path / "song.m4a"
    p.write_text("audio")
    store.record("101", str(p), "HIGH", audio_mode="DOLBY_ATMOS", atmos_only=True)
    assert store.ownership_of("101")["atmos_only"] is True, "the fact round-trips the store"
    b = _own_bridge(store, quality=Quality.hi_res_lossless, atmos_on=False)
    b.ownershipOf("101")
    assert b.ownershipOf("101")["up_to_date"] is True
    # A dual-mode Atmos copy (a stereo stream exists) still reads stale, as before.
    store.record("102", str(p), "HIGH", audio_mode="DOLBY_ATMOS", atmos_only=False)
    b.ownershipOf("102")
    assert b.ownershipOf("102")["up_to_date"] is False


def test_the_stream_capture_records_whether_the_track_is_atmos_only():
    td = _TrackedDownload.__new__(_TrackedDownload)
    td._delivered = {}
    td._delivered_lock = Lock()
    td._target_rank = 2
    td.settings = SimpleNamespace(data=SimpleNamespace(download_dolby_atmos=False))
    td.session = SimpleNamespace(audio_quality=None)
    td._tls = local()
    media = Track.__new__(Track)
    media.id = "5"
    media.audio_modes = ["DOLBY_ATMOS"]
    media.media_metadata_tags = []
    info = SimpleNamespace(media_stream=SimpleNamespace(audio_quality="HIGH", audio_mode="DOLBY_ATMOS"))
    with patch.object(backend.Download, "_get_track_stream_info", lambda self, m: info):
        td._get_track_stream_info(media)
    assert td._delivered[td._delivered_key(media)]["atmos_only"] is True


# ---- C54: a file the engine adopts off the disk is recorded, tier unknown ---
def test_a_tierless_audio_record_is_not_current_but_a_video_record_is():
    assert _copy_is_current({"path": "/m/x.flac", "quality_rank": -1}, 2, False) is False
    assert _copy_is_current({"path": "/m/x.mp4", "quality_rank": -1}, 2, False) is True
    assert _copy_is_current({"path": "/m/x.flac", "quality_tier": "LOSSLESS", "quality_rank": -1}, 2, False) is True


def test_an_adopted_file_is_reported_with_its_path_so_the_button_learns_it(monkeypatch):
    class _Relay:
        track_event = _Signal()

    monkeypatch.setattr(backend, "name_builder_title", lambda m: "Song")
    monkeypatch.setattr(backend, "_fmt_duration", lambda d: "3:00")
    monkeypatch.setattr(backend.Download, "item", lambda self, *a, **k: (True, "/music/song.flac"))
    td = _TrackedDownload.__new__(_TrackedDownload)
    td._track_signals = _Relay()
    td._outcome_lock = Lock()
    td.ok_count = td.write_count = td.skip_count = td.fail_count = 0
    td._delivered = {}
    td._delivered_lock = Lock()
    td.event_abort = Event()
    td._ownership_of = None
    td._target_rank = 2
    td._tls = local()
    td._skip_existing_base = False
    td.settings = SimpleNamespace(data=SimpleNamespace(download_dolby_atmos=False))
    td._library_claim = None
    td._force_redownload = False
    media = Track.__new__(Track)
    media.id = 42
    media.track_num = 1
    media.volume_num = 1
    media.duration = 180
    media.audio_modes = []
    media.media_metadata_tags = []
    td.item(media=media)
    done = next(e for e in _Relay.track_event.calls if e[0]["status"] == "done")[0]
    assert done["path"] == "/music/song.flac" and done["quality"]["adopted"] is True
    assert done["quality"]["tier"] is None


# ---- L05: an editorial page path never leaves TIDAL's API -------------------
def test_only_relative_pages_paths_are_fetched():
    ok = WavesBridge._page_path_ok
    assert ok("pages/explore") and ok("pages/data/abc?x=1")
    for bad in ("https://other.example/x", "//other.example/x", "pages//x", "pages/x\\\\y", "/pages/x", "", "explore"):
        assert not ok(bad), bad


# ---- L06: a failed boot save never crash-loops the launch --------------------
def test_the_video_flag_migration_survives_a_settings_save_failure():
    def boom():
        raise OSError("read-only")

    stub = SimpleNamespace(
        _waves_prefs={}, settings=SimpleNamespace(data=SimpleNamespace(video_download=True), save=boom)
    )
    stub._save_waves_prefs = lambda: None
    _bind(stub, "_migrate_video_flag")
    stub._migrate_video_flag()
    assert stub.settings.data.video_download is False
    assert stub._waves_prefs["video_flag_migrated"] is True


# ---- L07: a save already past its gate cannot recreate a removed snapshot ---
def test_a_sign_out_during_the_serialize_leaves_no_snapshot_behind(tmp_path):
    stub = SimpleNamespace(
        _logged_in=True,
        _lib_cache={},
        _lib_sort={},
        _browse_root_cache={},
        _browse_pages={},
        _artist_cache={},
        _home_cache={},
        _search_cache={},
        _page_cache_path=str(tmp_path / "page_cache.json"),
        _search_cache_path=str(tmp_path / "search_cache.json"),
        tidal=SimpleNamespace(session=SimpleNamespace(user=SimpleNamespace(id="u"))),
    )

    class _Lock:
        def __enter__(self):
            stub._logged_in = False  # the sign-out landed while serializing

        def __exit__(self, *a):
            return False

    stub._page_cache_lock = _Lock()
    _bind(stub, "_save_page_cache", "_cache_user_id")
    stub._save_page_cache()
    assert not (tmp_path / "page_cache.json").exists() and not (tmp_path / "search_cache.json").exists()


# ---- L09: the JSON caches survive a transient Windows lock -------------------
def test_an_atomic_text_write_retries_a_sharing_violation(tmp_path, monkeypatch):
    real = os.replace
    tries = []

    def flaky(src, dst):
        tries.append(1)
        if len(tries) == 1:
            raise PermissionError(32, "in use")
        real(src, dst)

    monkeypatch.setattr(config_mod.os, "replace", flaky)
    monkeypatch.setattr(config_mod, "_REPLACE_RETRY_DELAY_SEC", 0.0)
    target = tmp_path / "waves.json"
    backend._write_text_atomic(str(target), "{}")
    assert target.read_text() == "{}" and len(tries) == 2
    assert list(tmp_path.iterdir()) == [target], "no temp litter"


# ---- L11: a hidden, idle window stops touching the share ---------------------
def test_a_hidden_window_slows_the_keep_warm_and_defers_the_ownership_age(monkeypatch):
    touches = []
    monkeypatch.setattr(backend.os, "listdir", lambda p: touches.append(p))
    stub = SimpleNamespace(
        settings=SimpleNamespace(data=SimpleNamespace(download_base_path="/Volumes/Share/Music")),
        _keepwarm_inflight=False,
        _downloads_running=lambda: False,
        _remember_share_origin=lambda base: None,
        _own_lock=Lock(),
        _own_cache={"1": (0.0, {"owned": True})},
        _KEEPWARM_HIDDEN_EVERY=WavesBridge._KEEPWARM_HIDDEN_EVERY,
        aged=[],
    )
    stub._forget_ownership_answers = lambda: stub.aged.append(1)
    _bind(stub, "windowShown", "_keepwarm_tick", "_age_ownership_answers")
    stub.windowShown(False)
    for _ in range(9):
        stub._keepwarm_tick()
    time.sleep(0.05)
    assert touches == [], "hidden and idle, the share is left alone"
    stub._keepwarm_tick()
    time.sleep(0.05)
    assert len(touches) == 1, "every tenth tick still keeps the session warm"
    stub._age_ownership_answers()
    assert stub.aged == []
    stub.windowShown(True)
    assert stub.aged == [1], "one age runs on re-show so a parked page catches up"
    stub._age_ownership_answers()
    assert stub.aged == [1, 1]


def test_the_window_tells_the_bridge_when_it_leaves_the_screen():
    src = QML.read_text(encoding="utf-8")
    assert "onWindowUpChanged: if (waves && waves.windowShown) waves.windowShown(windowUp)" in src
    # onScreen keeps the presentation-stall term: decorative clocks pause on a
    # stalled window, as they did before the bridge hook joined this spot.
    assert "readonly property bool onScreen: windowUp && !presentStalled" in src


# ---- L13: side entries leave with the cache entry they describe -------------
def test_an_evicted_page_takes_its_stamp_and_hover_mark_with_it():
    stub = SimpleNamespace(
        _evict_lock=Lock(),
        _item_fetch_ts={"a": 1.0, "b": 2.0},
        _artist_reval_ts={},
        _prefetch_unrecorded={"a"},
        _album_tracks_unrecorded=set(),
    )
    _bind(stub, "_remember_capped")
    d = stub._browse_pages = {"a": 1}  # the side entries belong to the page cache
    stub._remember_capped(d, "b", 2, 1)
    assert d == {"b": 2}
    assert stub._item_fetch_ts == {"b": 2.0} and stub._prefetch_unrecorded == set()


# ---- C53: at most one probe thread waits on a hung mount --------------------
def test_a_hung_probe_does_not_spawn_a_thread_per_tick(monkeypatch):
    gate = Event()
    started = []

    def hang(path, volumes_root="/Volumes"):
        started.append(1)
        gate.wait(5)
        return ("ok", path)

    monkeypatch.setattr(WavesBridge, "_probe_folder_verdict", staticmethod(hang))
    stub = SimpleNamespace(
        settings=SimpleNamespace(data=SimpleNamespace(download_base_path="/Volumes/Share/Music")),
        _remount_download_share=lambda path: False,
        _probe_folder_verdict=hang,
    )
    _bind(stub, "_probe_download_base")
    try:
        assert stub._probe_download_base(timeout_s=0.05)[0] == "timeout"
        assert stub._probe_download_base(timeout_s=0.05)[0] == "timeout"
        assert len(started) == 1, "the stuck thread is the answer; no second one is started"
    finally:
        gate.set()


# ---- C31: a share's server and share name are hidden on every platform -----
def test_network_share_anchors_cover_unc_and_gvfs_spellings():
    unc = _network_share_anchors("\\\\nas\\music\\Artist")
    assert "\\\\nas\\music" in unc and "//nas/music" in unc and "//nas" in unc
    assert "//nas/music" in _network_share_anchors("//nas/music/Artist")
    gv = _network_share_anchors("/run/user/1000/gvfs/smb-share:server=nas.local,share=music/Artist")
    assert "smb-share:server=nas.local,share=music" in gv and "server=nas.local" in gv
    assert _network_share_anchors("/home/x/Music") == [] and _network_share_anchors("Z:\\Music") == []


def test_a_unc_download_folder_registers_its_share_as_a_secret(monkeypatch):
    seen = []
    monkeypatch.setattr(backend.diagnostics, "register_secret", lambda v, ph: seen.append((v, ph)))
    monkeypatch.setattr(backend.sys, "platform", "win32")
    stub = SimpleNamespace(_share_origin_noted=set())
    _bind(stub, "_remember_share_origin")
    stub._remember_share_origin("\\\\nas\\music\\Artist\\Album")
    assert ("\\\\nas\\music", "‹mount-point›") in seen
    stub._remember_share_origin("\\\\nas\\music\\Other")
    assert len(seen) == 4, "each spelling once per session"


# ---- QML text pins: C52, L10, L12, L24 ---------------------------------------
def test_the_artist_map_is_reset_at_sign_out():
    src = QML.read_text(encoding="utf-8")
    logout = src[src.index("root.searchSaved = null\n") :]
    assert "root.artistsById = ({})" in logout[:600]


def test_decorative_clocks_stop_while_the_window_is_hidden():
    src = QML.read_text(encoding="utf-8")
    assert "running: statusUpdate.visible && root.onScreen" in src
    assert "Timer { running: btt.on && root.onScreen; interval: 120" in src
    assert "running: emptyHint.visible && root.onScreen" in src


def test_a_settled_rows_holder_is_released_when_the_row_leaves():
    src = QML.read_text(encoding="utf-8")
    assert "function dlHoldersRelease(ids)" in src
    drop = src[src.index("function queueRowsDrop(qids)") :]
    assert "if (mids) root.dlHoldersRelease(mids)" in drop[:2600]


def test_the_finishing_step_comments_no_longer_mention_decrypting():
    src = QML.read_text(encoding="utf-8")
    assert "merge, decrypt" not in src and "decrypt, tag" not in src
