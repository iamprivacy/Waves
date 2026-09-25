"""Pins for the v0.1.31 audit, best-of-both and ownership half: RETRY ALL
rebinds a signed-out merge plan, a delisted borrowed track no longer fails the
rebind, only a refused slot is rescued at the lower tier, an adopted file never
overwrites a measured row, an old Atmos-only copy learns the fact at the gate,
two cuts a second apart meet in one dedup group, and the plan rebind runs
outside the browse lock."""

from __future__ import annotations

import contextlib
import threading
from threading import Event, Lock, local
from types import SimpleNamespace

import pytest
from tidalapi.exceptions import ObjectNotFound
from tidalapi.media import Quality, Track

from waves.helper.exceptions import DownloadIncomplete
from waves.ownership import OwnershipStore
from waves.waves_ui import backend
from waves.waves_ui.backend import WavesBridge, _PlanEntry, _TrackedDownload


class _Signal:
    def __init__(self):
        self.calls: list = []

    def emit(self, *a):
        self.calls.append(a)


class _InlinePool:
    def start(self, worker):
        worker.run()


def _bind(obj, *names):
    for name in names:
        setattr(obj, name, getattr(WavesBridge, name).__get__(obj, type(obj)))


def _track(tid, duration=200):
    return SimpleNamespace(id=tid, name=tid, duration=duration, track_num=1, volume_num=1)


# ---- RETRY ALL sends a signed-out merge through the rebind ------------------
class _RetryAllStub:
    def __init__(self):
        self._queue = [
            {"qid": 1, "status": "cancelled", "type": "album", "media_id": "merged"},
            {"qid": 2, "status": "cancelled", "type": "album", "media_id": "plain"},
        ]
        self._queue_lock = Lock()
        self._merge_plans: dict = {}
        self._merge_plans_unbound = {"merged": [("201", 1, 1, "101")]}
        self.refetched: list = []
        self.started: list = []
        self.removed: list = []
        self._row_object = lambda item: object()  # the artist page re-remembered every album
        self._retry_queue_refetch = lambda item: self.refetched.append(item["qid"])
        self._start_retry = lambda item, obj: self.started.append(item["qid"])
        self._queue_batch = contextlib.nullcontext
        self._remove_rows_where = lambda pred: self.removed.extend(q["qid"] for q in self._queue if pred(q))
        self._set_status = lambda text: None
        _bind(self, "_retry_all_with_status")


def test_retry_all_rebinds_an_unbound_merge_plan_even_when_the_album_is_back():
    stub = _RetryAllStub()
    stub._retry_all_with_status("cancelled")
    assert stub.refetched == [1], "the merge row goes through the rebind, not a plain retry"
    assert stub.started == [2] and stub.removed == [2]


# ---- the plan rebind: a delisted borrowed track takes the identity's cut ----
class _Session:
    def __init__(self, tracks: dict, gone: set, lock=None):
        self.tracks = tracks
        self.gone = gone
        self.lock = lock
        self.track_calls_locked: list = []
        self.album_calls_locked: list = []

    def track(self, tid):
        if self.lock is not None:
            self.track_calls_locked.append(self.lock.locked())
        if int(tid) in self.gone:
            raise ObjectNotFound("gone")
        return self.tracks[int(tid)]

    def album(self, aid):
        if self.lock is not None:
            self.album_calls_locked.append(self.lock.locked())
        return SimpleNamespace(id=aid, name="Album")


def _rebind_stub(unbound):
    stub = SimpleNamespace(_merge_plans_unbound=unbound)
    _bind(stub, "_rebind_merge_plan")
    return stub


def test_a_delisted_borrowed_track_rebinds_to_the_identity_editions_own_cut():
    own = _track("101")
    kept = _track("202")
    session = _Session({101: own, 202: kept}, gone={201})
    stub = _rebind_stub({"m": [("201", 1, 1, "101"), ("202", 2, 1, "102")]})
    plan = stub._rebind_merge_plan("album", "m", session)
    assert plan == [_PlanEntry(own, 1, 1, "101"), _PlanEntry(kept, 2, 1, "102")]


def test_a_slot_that_cannot_be_rebuilt_either_way_is_not_reported_as_the_album_gone():
    session = _Session({}, gone={201, 101})
    stub = _rebind_stub({"m": [("201", 1, 1, "101")]})
    with pytest.raises(Exception) as err:
        stub._rebind_merge_plan("album", "m", session)
    assert not isinstance(err.value, ObjectNotFound), "ObjectNotFound reads as 'no longer on TIDAL'"


def _refetch_stub(session):
    stub = SimpleNamespace(
        _refetch_inflight=set(),
        _logged_in=True,
        _browse_gen=0,
        _browse_lock=Lock(),
        tidal=SimpleNamespace(session=session),
        threadpool=_InlinePool(),
        _merge_plans={},
        _merge_plans_unbound={"55": [("201", 1, 1, "101"), ("202", 2, 1, "102")]},
        _queueRetryRefetched=_Signal(),
        statuses=[],
        remembered=[],
    )
    session.lock = stub._browse_lock
    stub._set_status = stub.statuses.append
    stub._remember = lambda bucket, mid, obj: stub.remembered.append((bucket, mid))
    _bind(stub, "_retry_queue_refetch")
    return stub


def test_a_retry_after_sign_out_survives_a_delisted_borrowed_track_and_rebinds_outside_the_lock():
    session = _Session({101: _track("101"), 202: _track("202")}, gone={201})
    stub = _refetch_stub(session)
    stub._retry_queue_refetch({"type": "album", "media_id": "55", "qid": 7})
    assert backend._ITEM_GONE not in stub.statuses
    assert stub._queueRetryRefetched.calls == [("album", "55", 7)], "the retry goes ahead"
    assert [e.src.id for e in stub._merge_plans["55"]] == ["101", "202"]
    assert "55" not in stub._merge_plans_unbound
    assert session.album_calls_locked == [True], "the album fetch keeps the browse lock"
    assert session.track_calls_locked and not any(session.track_calls_locked), "slot fetches run outside it"


def test_a_slot_lost_both_ways_says_could_not_fetch_and_keeps_the_plan_for_later():
    session = _Session({202: _track("202")}, gone={201, 101})
    stub = _refetch_stub(session)
    stub._retry_queue_refetch({"type": "album", "media_id": "55", "qid": 7})
    assert stub.statuses[-1] == backend._ITEM_FETCH_FAILED
    assert stub._queueRetryRefetched.calls == [] and "55" in stub._merge_plans_unbound


# ---- the fan-out rescues a refusal, never a failure --------------------------
class _FakeSettings:
    data = SimpleNamespace(downloads_concurrent_max=2, download_delay=False)


class _Engine:
    """Refuses the ids in ``refuse`` (counted, the way _TrackedDownload.item
    tallies a refusal) and fails the ids in ``fail`` outright."""

    _landed_paths = staticmethod(backend.Download._landed_paths)

    def __init__(self, refuse=(), fail=()):
        self.refuse = set(refuse)
        self.fail = set(fail)
        self.unavailable_count = 0
        self.fetched: list = []
        self._lock = threading.Lock()

    def item(self, **kw):
        media = kw["media"]
        with self._lock:
            self.fetched.append(str(media.id))
            if str(media.id) in self.refuse:
                self.unavailable_count += 1
                return False, ""
        if str(media.id) in self.fail:
            return False, ""
        return True, f"/out/{media.id}"

    def _playlist_for_collection(self, media, template, paths):
        self.playlist = list(paths)


def _merge_bridge(session_tracks: dict):
    bridge = WavesBridge.__new__(WavesBridge)
    bridge.settings = _FakeSettings()
    bridge.tidal = SimpleNamespace(session=SimpleNamespace(track=lambda tid: session_tracks[int(tid)]))
    return bridge


def _run(bridge, engine, plan):
    bridge._download_merge_plan(engine, SimpleNamespace(list_item=_Signal()), Event(), object(), "t", plan)


def test_a_borrowed_slot_that_failed_is_not_fetched_at_the_lower_tier():
    bridge = _merge_bridge({101: _track("dlx-1")})
    engine = _Engine(fail={"std-1"})
    plan = [_PlanEntry(_track("std-1"), 1, 1, "101"), _PlanEntry(_track("dlx-2"), 2, 1, "102")]
    with pytest.raises(DownloadIncomplete, match="1 of 2 tracks failed"):
        _run(bridge, engine, plan)
    assert "dlx-1" not in engine.fetched, "a failure keeps its plan for RETRY, it is not settled at a lower cut"


def test_a_rescued_refusal_does_not_hide_a_different_slots_failure():
    bridge = _merge_bridge({101: _track("dlx-1")})
    engine = _Engine(refuse={"std-1"}, fail={"dlx-2"})
    plan = [_PlanEntry(_track("std-1"), 1, 1, "101"), _PlanEntry(_track("dlx-2"), 2, 1, "102")]
    with pytest.raises(DownloadIncomplete, match="1 of 2 tracks failed"):
        _run(bridge, engine, plan)
    assert "dlx-1" in engine.fetched, "the refused slot was still rescued"


class _MarkedEngine(_Engine):
    """Speaks the engine's per-thread refusal mark, and makes the failing
    item wait until the refusal was counted, so a counter read across the
    call would take the failure for a refusal."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self._tls = local()
        self.counted = Event()

    def item(self, **kw):
        media = kw["media"]
        if str(media.id) in self.fail:
            self.counted.wait(2)
        ok, path = super().item(**kw)
        self._tls.last_refused = str(media.id) in self.refuse
        if str(media.id) in self.refuse:
            self.counted.set()
        return ok, path

    def took_refusal(self):
        return bool(getattr(self._tls, "last_refused", False))


def test_the_engines_own_mark_decides_which_slot_was_refused():
    bridge = _merge_bridge({101: _track("dlx-1"), 102: _track("dlx-2")})
    engine = _MarkedEngine(refuse={"std-1"}, fail={"std-2"})
    plan = [_PlanEntry(_track("std-1"), 1, 1, "101"), _PlanEntry(_track("std-2"), 2, 1, "102")]
    with pytest.raises(DownloadIncomplete, match="1 of 2 tracks failed"):
        _run(bridge, engine, plan)
    assert "dlx-1" in engine.fetched and "dlx-2" not in engine.fetched


def _tracked(monkeypatch, engine_item):
    monkeypatch.setattr(backend, "name_builder_title", lambda m: "Song")
    monkeypatch.setattr(backend, "_fmt_duration", lambda d: "3:00")
    monkeypatch.setattr(backend.Download, "item", engine_item)
    td = _TrackedDownload.__new__(_TrackedDownload)
    td._track_signals = SimpleNamespace(track_event=_Signal())
    td._outcome_lock = Lock()
    td.ok_count = td.write_count = td.skip_count = td.fail_count = td.unavailable_count = 0
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
    return td


def _media(tid=42):
    media = Track.__new__(Track)
    media.id = tid
    media.track_num = 1
    media.volume_num = 1
    media.duration = 180
    media.audio_modes = []
    media.media_metadata_tags = []
    return media


def test_the_tracked_download_marks_a_refusal_for_its_own_thread_only(monkeypatch):
    def refused(self, *a, media=None, **k):
        self._note_unavailable(media)
        return False, ""

    td = _tracked(monkeypatch, refused)
    td.item(media=_media())
    assert td.took_refusal() is True and td.unavailable_count == 1
    seen = []
    worker = threading.Thread(target=lambda: seen.append(td.took_refusal()))
    worker.start()
    worker.join()
    assert seen == [False], "another pool thread never reads this one's refusal"
    monkeypatch.setattr(backend.Download, "item", lambda self, *a, **k: (False, ""))
    td.item(media=_media())
    assert td.took_refusal() is False, "a plain failure clears the mark"


# ---- an adopted file never overwrites a measured row ------------------------
def _record_stub(store):
    stub = SimpleNamespace(
        settings=SimpleNamespace(data=SimpleNamespace(symlink_to_track=False)),
        _ownership=store,
        _own_lock=Lock(),
        _own_cache={},
        ownershipChanged=_Signal(),
        _downloadRecorded=_Signal(),
    )
    stub._evict_own_cache_locked = lambda: None
    _bind(stub, "_record_ownership")
    return stub


_ADOPTED = {"tier": None, "requested_rank": -1, "ceiling_rank": -1, "adopted": True}


def test_an_adopted_twin_leaves_the_measured_row_as_it_was(tmp_path):
    store = OwnershipStore(str(tmp_path / "own.db"))
    song = tmp_path / "song.flac"
    song.write_bytes(b"\0")
    stub = _record_stub(store)
    stub._record_ownership({"id": "7", "path": str(song), "quality": {"tier": "HI_RES_LOSSLESS", "requested_rank": 4}})
    measured = store.ownership_of("7")
    stub._own_cache.clear()
    stub._record_ownership({"id": "7", "path": str(song), "quality": dict(_ADOPTED)})
    assert store.ownership_of("7")["quality_tier"] == measured["quality_tier"] == "HI_RES_LOSSLESS"
    assert store.ownership_of("7")["quality_rank"] == measured["quality_rank"]
    assert stub._own_cache == {}, "no tier-less answer is asserted over the measured one"


def test_an_adopted_file_with_no_row_is_still_recorded_tier_unknown(tmp_path):
    store = OwnershipStore(str(tmp_path / "own.db"))
    song = tmp_path / "old.flac"
    song.write_bytes(b"\0")
    stub = _record_stub(store)
    stub._record_ownership({"id": "8", "path": str(song), "quality": dict(_ADOPTED)})
    rec = store.ownership_of("8")
    assert rec is not None and rec["quality_tier"] is None and rec["quality_rank"] == -1
    assert stub._own_cache["8"][1]["owned"] is True


# ---- an Atmos-only copy recorded before the column learns it at the gate ----
def test_the_gate_stamps_atmos_only_onto_an_old_atmos_record(tmp_path):
    store = OwnershipStore(str(tmp_path / "own.db"))
    song = tmp_path / "song.m4a"
    song.write_bytes(b"\0")
    store.record("101", str(song), "HIGH", audio_mode="DOLBY_ATMOS")  # atmos_only defaults to 0
    atmos_off = SimpleNamespace(settings=SimpleNamespace(data=SimpleNamespace(download_dolby_atmos=False)))
    _bind(atmos_off, "_would_refetch_atmos")
    assert atmos_off._would_refetch_atmos(store.ownership_of("101")) is False, "the button's state before"
    td = _TrackedDownload.__new__(_TrackedDownload)
    td._ownership_of = store.ownership_of
    td._ownership_stamp = None
    td._ownership_stamp_atmos = store.stamp_atmos_only
    td._target_rank = backend.quality_rank("HI_RES_LOSSLESS")
    td.settings = atmos_off.settings
    media = _media(101)
    media.audio_modes = ["DOLBY_ATMOS"]
    verdict, rec = td._ownership_decision(media)
    assert verdict == "skip" and rec["atmos_only"] is True
    assert store.ownership_of("101")["atmos_only"] is True
    assert atmos_off._would_refetch_atmos(store.ownership_of("101")) is True, "the id-only button settles too"


def test_a_stereo_row_is_never_stamped_atmos_only(tmp_path):
    store = OwnershipStore(str(tmp_path / "own.db"))
    song = tmp_path / "song.flac"
    song.write_bytes(b"\0")
    store.record("5", str(song), "LOSSLESS", audio_mode="STEREO")
    assert store.stamp_atmos_only("5", str(song)) is False
    assert store.ownership_of("5")["atmos_only"] is False


# ---- two cuts a second apart meet in one dedup group ------------------------
def _cut(tid, dur, explicit):
    t = Track.__new__(Track)
    t.id = tid
    t.name = "Song"
    t.full_name = "Song"
    t.artist = SimpleNamespace(name="Artist", id=1)
    t.artists = [t.artist]
    t.audio_modes = ["STEREO"]
    t.audio_quality = Quality.high_lossless
    t.media_metadata_tags = None
    t.explicit = explicit
    t.duration = dur
    return t


def _dedup_bridge(mode):
    b = WavesBridge.__new__(WavesBridge)
    b.settings = SimpleNamespace(data=SimpleNamespace(quality_audio=Quality.hi_res_lossless))
    b._waves_prefs = {"explicit_mode": mode}
    _bind(b, "_track_key", "_max_quality_rank", "_dedup_tracks")
    return b


def test_a_clean_and_an_explicit_cut_across_a_bucket_edge_follow_the_preference():
    explicit, clean = _cut(1, 187, True), _cut(2, 188, False)
    assert round(187 / 15) != round(188 / 15), "the pair straddles a fixed bucket edge"
    assert [t.id for t in _dedup_bridge("clean")._dedup_tracks([explicit, clean])] == [2]
    assert [t.id for t in _dedup_bridge("explicit")._dedup_tracks([explicit, clean])] == [1]


def test_two_same_titled_recordings_minutes_apart_still_both_survive():
    intro, other = _cut(1, 62, False), _cut(2, 201, False)
    assert [t.id for t in _dedup_bridge("explicit")._dedup_tracks([intro, other])] == [1, 2]
