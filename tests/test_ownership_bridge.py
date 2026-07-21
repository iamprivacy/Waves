"""Tests for the ownership recording wiring in the bridge (backend.py).

These import WavesBridge, so they collect only in the full runtime venv (PySide6
present), like tests/test_audit_backend.py. Two layers are covered:

  * the GUI-thread record sink (_track_lifecycle -> _record_ownership -> store)
    plus the ownershipOf query, exercised through a Qt-free _Stub that binds the
    real bridge methods onto a real OwnershipStore, and
  * the _TrackedDownload capture/pop logic (delivered quality stashed in
    _get_track_stream_info, popped onto the completion event by item()),
    exercised on a real _TrackedDownload built with __new__ so the heavy
    Download.__init__ (network/session) is bypassed and super() is stubbed.
"""

from __future__ import annotations

import os
from threading import Event, Lock, local
from types import SimpleNamespace

import tidaler.waves_ui.backend as backend
from tidaler.ownership import OwnershipStore
from tidaler.waves_ui.backend import WavesBridge, _stream_quality


class _Signal:
    """Records emit() calls so a test can assert what QML or the store would see."""

    def __init__(self):
        self.emits: list = []

    def emit(self, *args):
        self.emits.append(args if len(args) != 1 else args[0])


class _Relay:
    def __init__(self):
        self.track_event = _Signal()


class _SyncPool:
    """Runs 'pooled' work inline so cache-refresh tests are deterministic: the
    ownershipOf call that schedules a refresh still returns its pre-refresh
    answer (matching production), and the NEXT call reads the refreshed cache."""

    def start(self, worker):
        worker.run()


class _BridgeStub:
    """Bare stand-in for WavesBridge carrying only what the ownership sink and
    query touch, with the real bridge methods bound on and a real store."""

    def __init__(self, tmp_path, quality_audio="LOSSLESS"):
        self._job_tracks: dict = {}
        self._job_signals: dict = {}
        self.queueTrackState = _Signal()
        self.ownershipChanged = _Signal()
        self.collectionMembershipChanged = _Signal()
        self._ownership = OwnershipStore(str(tmp_path / "ownership.sqlite3"))
        self._own_cache: dict = {}
        self._own_pending: set = set()
        self._own_lock = Lock()
        self._own_pool = _SyncPool()
        self._OWN_TTL = WavesBridge._OWN_TTL
        self._OWN_TTL_BUSY = WavesBridge._OWN_TTL_BUSY
        self._downloads_running = lambda: False
        self._base_ok = ("", 0.0)
        self.settings = SimpleNamespace(
            data=SimpleNamespace(quality_audio=quality_audio, download_base_path="", symlink_to_track=False)
        )
        for name in (
            "_track_lifecycle",
            "_record_ownership",
            "_note_download_base_ok",
            "ownershipOf",
            "_own_refresh",
            "_target_quality_rank",
            "collectionMemberIds",
        ):
            setattr(self, name, getattr(WavesBridge, name).__get__(self, _BridgeStub))

    def own(self, tid):
        """ownershipOf with the async refresh settled: first call schedules the
        inline refresh, second call serves the refreshed cache."""
        self.ownershipOf(tid)
        return self.ownershipOf(tid)

    def expire(self, tid):
        """Age the cache entry past the TTL so the next query re-checks disk."""
        hit = self._own_cache.get(str(tid))
        if hit:
            self._own_cache[str(tid)] = (-1e9, hit[1])


def _new_tracked():
    """A real _TrackedDownload without running Download.__init__ (which needs a
    live tidal session). super().item()/_get_track_stream_info are monkeypatched
    per test; self stays a genuine _TrackedDownload so zero-arg super() resolves."""
    td = backend._TrackedDownload.__new__(backend._TrackedDownload)
    td._track_signals = None
    td._outcome_lock = Lock()
    td.ok_count = 0
    td.write_count = 0
    td.skip_count = 0
    td.fail_count = 0
    td._delivered = {}
    td._delivered_lock = Lock()
    td.event_abort = Event()
    td._ownership_of = None
    td._target_rank = 2  # LOSSLESS
    td._tls = local()
    td._skip_existing_base = False
    return td


def _make_file(tmp_path, name="song.flac"):
    p = tmp_path / name
    p.write_text("audio")
    return p


# --------------------------------------------------------------------------- #
# _stream_quality: normalize a stream's delivered fields.
# --------------------------------------------------------------------------- #
def test_stream_quality_extracts_and_normalizes():
    tier = SimpleNamespace(value="LOSSLESS")  # a Quality enum member stand-in
    mode = SimpleNamespace(value="DOLBY_ATMOS")  # an AudioMode enum member stand-in
    stream = SimpleNamespace(audio_quality=tier, audio_mode=mode, bit_depth=24, sample_rate=96000)
    info = SimpleNamespace(media_stream=stream, stream_manifest=SimpleNamespace(codecs="FLAC"))
    q = _stream_quality(info)
    assert q == {
        "tier": "LOSSLESS",
        "audio_mode": "DOLBY_ATMOS",
        "bit_depth": 24,
        "sample_rate": 96000,
        "codecs": "FLAC",
    }


def test_stream_quality_accepts_plain_strings():
    stream = SimpleNamespace(audio_quality="HIGH", audio_mode="STEREO", bit_depth=16, sample_rate=44100)
    info = SimpleNamespace(media_stream=stream, stream_manifest=SimpleNamespace(codecs="MP4A"))
    assert _stream_quality(info)["tier"] == "HIGH"


# --------------------------------------------------------------------------- #
# _TrackedDownload: capture delivered quality, pop it onto the completion event.
# --------------------------------------------------------------------------- #
def test_get_track_stream_info_captures_quality(monkeypatch):
    stream = SimpleNamespace(audio_quality="HI_RES_LOSSLESS", audio_mode="STEREO", bit_depth=24, sample_rate=96000)
    info = SimpleNamespace(media_stream=stream, stream_manifest=SimpleNamespace(codecs="FLAC"))
    monkeypatch.setattr(backend.Download, "_get_track_stream_info", lambda self, media: info)
    td = _new_tracked()
    out = td._get_track_stream_info(SimpleNamespace(id=99))
    assert out is info
    assert td._delivered["99"]["tier"] == "HI_RES_LOSSLESS"
    assert td._delivered["99"]["bit_depth"] == 24


def test_get_track_stream_info_no_stream_captures_nothing(monkeypatch):
    # A skip_existing short-circuit hands back a TrackStreamInfo with no stream.
    info = SimpleNamespace(media_stream=None, stream_manifest=None)
    monkeypatch.setattr(backend.Download, "_get_track_stream_info", lambda self, media: info)
    td = _new_tracked()
    td._get_track_stream_info(SimpleNamespace(id=99))
    assert td._delivered == {}


def _patch_item_helpers(monkeypatch, return_value):
    monkeypatch.setattr(backend, "name_builder_item", lambda m: "Song")
    monkeypatch.setattr(backend, "name_builder_title", lambda m: "Song Title")
    monkeypatch.setattr(backend, "_fmt_duration", lambda d: "3:00")
    monkeypatch.setattr(backend.Download, "item", lambda self, *a, media=None, event_stop=None, **k: return_value)


def test_item_attaches_path_and_quality_on_real_download(monkeypatch, tmp_path):
    fpath = _make_file(tmp_path)
    _patch_item_helpers(monkeypatch, (True, fpath))
    td = _new_tracked()
    td._track_signals = _Relay()
    td._delivered["42"] = {
        "tier": "LOSSLESS",
        "audio_mode": "STEREO",
        "bit_depth": 16,
        "sample_rate": 44100,
        "codecs": "FLAC",
    }
    ok, path = td.item(media=SimpleNamespace(id=42, track_num=1, volume_num=1, duration=180))
    assert ok is True
    done = [e for e in td._track_signals.track_event.emits if e["status"] == "done"]
    assert len(done) == 1
    assert done[0]["path"] == str(fpath)
    assert done[0]["quality"]["tier"] == "LOSSLESS"
    assert td._delivered == {}, "the captured quality must be popped after use"


def test_item_skip_carries_no_quality(monkeypatch, tmp_path):
    # ok=True but nothing was captured (a skip): the event must NOT carry a quality,
    # so the sink does not record an invented quality for a file it did not write.
    fpath = _make_file(tmp_path)
    _patch_item_helpers(monkeypatch, (True, fpath))
    td = _new_tracked()
    td._track_signals = _Relay()
    td.item(media=SimpleNamespace(id=42, track_num=1, volume_num=1, duration=180))
    done = next(e for e in td._track_signals.track_event.emits if e["status"] == "done")
    assert "path" not in done
    assert "quality" not in done


def test_item_failure_pops_capture(monkeypatch, tmp_path):
    fpath = _make_file(tmp_path)
    _patch_item_helpers(monkeypatch, (False, fpath))
    td = _new_tracked()
    td._track_signals = _Relay()
    td._delivered["42"] = {"tier": "LOSSLESS"}
    td.item(media=SimpleNamespace(id=42, track_num=1, volume_num=1, duration=180))
    failed = next(e for e in td._track_signals.track_event.emits if e["status"] == "failed")
    assert "quality" not in failed
    assert td._delivered == {}, "a non-done outcome still clears the stash (no leak)"


# --------------------------------------------------------------------------- #
# The GUI-thread record sink + the ownershipOf query.
# --------------------------------------------------------------------------- #
def test_done_event_records_and_is_queryable(tmp_path):
    stub = _BridgeStub(tmp_path)
    f = _make_file(tmp_path)
    ev = {"id": "42", "status": "done", "path": str(f), "quality": {"tier": "LOSSLESS", "bit_depth": 16}}
    stub._track_lifecycle(1, ev)
    info = stub.ownershipOf("42")
    assert info["owned"] is True
    assert info["quality_tier"] == "LOSSLESS"
    assert stub.ownershipChanged.emits == ["42"]


def test_done_without_quality_is_not_recorded(tmp_path):
    stub = _BridgeStub(tmp_path)
    f = _make_file(tmp_path)
    stub._track_lifecycle(1, {"id": "42", "status": "done", "path": str(f)})  # a skip: no quality
    assert stub.ownershipOf("42") == {"owned": False}
    assert stub.ownershipChanged.emits == []


def test_failed_event_is_not_recorded(tmp_path):
    stub = _BridgeStub(tmp_path)
    f = _make_file(tmp_path)
    stub._track_lifecycle(1, {"id": "42", "status": "failed", "path": str(f), "quality": {"tier": "LOSSLESS"}})
    assert stub.ownershipOf("42") == {"owned": False}


def test_symlink_records_resolved_target(tmp_path):
    stub = _BridgeStub(tmp_path)
    # realpath resolution is gated on symlink-to-track mode now (it is the
    # only mode that ever hands a link to the sink, and realpath costs a
    # network stat per path component on an SMB library).
    stub.settings.data.symlink_to_track = True
    real = _make_file(tmp_path, "real.flac")
    link = tmp_path / "link.flac"
    link.symlink_to(real)
    stub._track_lifecycle(1, {"id": "7", "status": "done", "path": str(link), "quality": {"tier": "HIGH"}})
    info = stub.ownershipOf("7")
    assert info["owned"] is True
    assert info["path"] == os.path.realpath(str(real)), "symlink mode must record the real file, not the link"


def test_ownership_of_unrecorded_is_not_owned(tmp_path):
    stub = _BridgeStub(tmp_path)
    assert stub.ownershipOf("nope") == {"owned": False}


# --------------------------------------------------------------------------- #
# Videos: a real fetch is captured in _get_media_urls (no track stream info).
# --------------------------------------------------------------------------- #
def _new_video(vid=7):
    v = backend.Video.__new__(backend.Video)
    v.id = vid
    return v


def test_video_url_fetch_captures_marker(monkeypatch):
    monkeypatch.setattr(backend.Download, "_get_media_urls", lambda self, media, stream_manifest=None: ["u1", "u2"])
    td = _new_tracked()
    td._get_media_urls(_new_video())
    assert td._delivered["7"] == {"tier": None}


def test_video_no_urls_captures_nothing(monkeypatch):
    monkeypatch.setattr(backend.Download, "_get_media_urls", lambda self, media, stream_manifest=None: [])
    td = _new_tracked()
    td._get_media_urls(_new_video())
    assert td._delivered == {}


def test_track_url_fetch_does_not_overwrite_capture(monkeypatch):
    # Tracks stash real delivered values in _get_track_stream_info; the URL hook
    # must not clobber them with a tier-less video marker.
    monkeypatch.setattr(backend.Download, "_get_media_urls", lambda self, media, stream_manifest=None: ["u1"])
    td = _new_tracked()
    td._delivered["42"] = {"tier": "LOSSLESS"}
    td._get_media_urls(SimpleNamespace(id=42))
    assert td._delivered["42"] == {"tier": "LOSSLESS"}


def test_video_done_event_records_ownership(monkeypatch, tmp_path):
    fpath = _make_file(tmp_path, "clip.mp4")
    _patch_item_helpers(monkeypatch, (True, fpath))
    td = _new_tracked()
    td._track_signals = _Relay()
    td._delivered["7"] = {"tier": None}  # as stashed by the URL hook
    td.item(media=SimpleNamespace(id=7, track_num=0, volume_num=1, duration=240))
    done = next(e for e in td._track_signals.track_event.emits if e["status"] == "done")
    assert done["path"] == str(fpath)
    stub_quality = done["quality"]
    stub = _BridgeStub(tmp_path)
    stub._track_lifecycle(1, {"id": "7", "status": "done", "path": str(fpath), "quality": stub_quality})
    info = stub.ownershipOf("7")
    assert info["owned"] is True
    assert info["quality_tier"] is None


# --------------------------------------------------------------------------- #
# The ownership gate: collection downloads skip tracks already on disk.
# --------------------------------------------------------------------------- #
def test_item_skips_owned_track_without_fetching(monkeypatch, tmp_path):
    fpath = _make_file(tmp_path)

    def _boom(self, *a, **k):
        raise AssertionError("super().item must not run for an owned track")

    monkeypatch.setattr(backend, "name_builder_item", lambda m: "Song")
    monkeypatch.setattr(backend, "name_builder_title", lambda m: "Song Title")
    monkeypatch.setattr(backend, "_fmt_duration", lambda d: "3:00")
    monkeypatch.setattr(backend.Download, "item", _boom)
    td = _new_tracked()
    td._track_signals = _Relay()
    td._ownership_of = lambda tid: {"path": str(fpath), "quality_tier": "LOSSLESS"}
    ok, path = td.item(media=SimpleNamespace(id=42, track_num=1, volume_num=1, duration=180))
    assert ok is True
    assert path == "", "a skip must not leak the owned path: items() would m3u its foreign folder"
    assert td.ok_count == 1, "an all-owned collection must still count as a success"
    ev = next(e for e in td._track_signals.track_event.emits if e["status"] == "skipped")
    assert ev["id"] == "42"
    assert "quality" not in ev and "path" not in ev, "a skip must record nothing new"


def test_item_downloads_when_not_owned(monkeypatch, tmp_path):
    fpath = _make_file(tmp_path)
    _patch_item_helpers(monkeypatch, (True, fpath))
    td = _new_tracked()
    td._track_signals = _Relay()
    td._ownership_of = lambda tid: None  # store: no live record (never had it, or deleted)
    td.item(media=SimpleNamespace(id=42, track_num=1, volume_num=1, duration=180))
    assert any(e["status"] == "done" for e in td._track_signals.track_event.emits)


def test_item_downloads_when_ownership_lookup_fails(monkeypatch, tmp_path):
    fpath = _make_file(tmp_path)
    _patch_item_helpers(monkeypatch, (True, fpath))
    td = _new_tracked()
    td._track_signals = _Relay()

    def _broken(tid):
        raise RuntimeError("store unavailable")

    td._ownership_of = _broken
    td.item(media=SimpleNamespace(id=42, track_num=1, volume_num=1, duration=180))
    assert any(e["status"] == "done" for e in td._track_signals.track_event.emits)


def test_item_skips_equal_or_better_quality(monkeypatch, tmp_path):
    fpath = _make_file(tmp_path)

    def _boom(self, *a, **k):
        raise AssertionError("equal-or-better owned quality must not re-fetch")

    monkeypatch.setattr(backend, "name_builder_item", lambda m: "Song")
    monkeypatch.setattr(backend, "name_builder_title", lambda m: "Song Title")
    monkeypatch.setattr(backend, "_fmt_duration", lambda d: "3:00")
    monkeypatch.setattr(backend.Download, "item", _boom)
    td = _new_tracked()  # targets LOSSLESS (rank 2)
    td._track_signals = _Relay()
    td._ownership_of = lambda tid: {"path": str(fpath), "quality_rank": 3}  # HI_RES on disk
    ok, _ = td.item(media=SimpleNamespace(id=42, track_num=1, volume_num=1, duration=180))
    assert ok is True
    assert any(e["status"] == "skipped" for e in td._track_signals.track_event.emits)


def test_item_upgrades_owned_lower_quality_in_place(monkeypatch, tmp_path):
    fpath = _make_file(tmp_path)
    seen = {}

    def _capture(self, *a, media=None, event_stop=None, **k):
        seen["skip_existing"] = self.skip_existing
        return True, fpath

    monkeypatch.setattr(backend, "name_builder_item", lambda m: "Song")
    monkeypatch.setattr(backend, "name_builder_title", lambda m: "Song Title")
    monkeypatch.setattr(backend, "_fmt_duration", lambda d: "3:00")
    monkeypatch.setattr(backend.Download, "item", _capture)
    td = _new_tracked()  # targets LOSSLESS (rank 2)
    td._track_signals = _Relay()
    td._skip_existing_base = True
    td._ownership_of = lambda tid: {"path": str(fpath), "quality_rank": 1}  # HIGH on disk
    ok, _ = td.item(media=SimpleNamespace(id=42, track_num=1, volume_num=1, duration=180))
    assert ok is True
    assert seen["skip_existing"] is False, "an upgrade must overwrite in place, not skip or uniquify"
    assert td.skip_existing is True, "the base path-collision safety is restored after the call"
    assert any(e["status"] == "done" for e in td._track_signals.track_event.emits)


def test_skipped_event_fills_bar_and_records_nothing(tmp_path):
    stub = _BridgeStub(tmp_path)
    stub._track_lifecycle(1, {"id": "42", "status": "skipped", "title": "Song", "desc": "d"})
    row = stub.queueTrackState.emits[-1][1]
    assert row["pct"] == 100.0
    assert stub.ownershipOf("42") == {"owned": False}
    assert stub.ownershipChanged.emits == []


def test_deleted_file_reads_as_not_owned(tmp_path):
    stub = _BridgeStub(tmp_path)
    f = _make_file(tmp_path)
    stub._track_lifecycle(1, {"id": "42", "status": "done", "path": str(f), "quality": {"tier": "LOSSLESS"}})
    assert stub.ownershipOf("42")["owned"] is True
    f.unlink()
    stub.expire("42")  # a fresh cache entry serves until the TTL passes
    assert stub.own("42") == {"owned": False}


def test_owned_copy_below_target_quality_is_not_up_to_date(tmp_path):
    stub = _BridgeStub(tmp_path, quality_audio="HI_RES_LOSSLESS")
    f = _make_file(tmp_path)
    stub._track_lifecycle(1, {"id": "42", "status": "done", "path": str(f), "quality": {"tier": "LOSSLESS"}})
    info = stub.ownershipOf("42")
    assert info["owned"] is True
    assert info["up_to_date"] is False, "a LOSSLESS copy under a HI_RES target must offer an upgrade"


def test_owned_copy_at_target_quality_is_up_to_date(tmp_path):
    stub = _BridgeStub(tmp_path, quality_audio="LOSSLESS")
    f = _make_file(tmp_path)
    stub._track_lifecycle(1, {"id": "42", "status": "done", "path": str(f), "quality": {"tier": "HI_RES_LOSSLESS"}})
    assert stub.ownershipOf("42")["up_to_date"] is True, "equal-or-better must read as current"


def test_tierless_video_record_is_always_up_to_date(tmp_path):
    stub = _BridgeStub(tmp_path, quality_audio="HI_RES_LOSSLESS")
    f = _make_file(tmp_path, "clip.mp4")
    stub._track_lifecycle(1, {"id": "7", "status": "done", "path": str(f), "quality": {"tier": None}})
    info = stub.ownershipOf("7")
    assert info["owned"] is True
    assert info["up_to_date"] is True, "videos have no quality tiers, owned means done"


def test_ownership_of_never_stats_on_the_calling_thread(tmp_path, monkeypatch):
    # The GUI-thread contract: a cache miss answers immediately from what is
    # known and defers the store lookup (which stats the disk) to the pool.
    stub = _BridgeStub(tmp_path)

    class _CapturePool:
        def __init__(self):
            self.started = 0

        def start(self, worker):
            self.started += 1  # deliberately NOT run: simulates a slow stat

    stub._own_pool = _CapturePool()

    def _boom(tid, **kw):
        raise AssertionError("the store must not be queried on the calling thread")

    monkeypatch.setattr(stub._ownership, "ownership_of", _boom)
    assert stub.ownershipOf("42") == {"owned": False}
    assert stub._own_pool.started == 1
    assert stub.ownershipOf("42") == {"owned": False}
    assert stub._own_pool.started == 1, "a pending refresh must not be scheduled twice"


# --------------------------------------------------------------------------- #
# Collection membership learned for free from the per-track download event
# stream: a collection job's tracks are remembered as belonging to its
# media_id as they are first seen, with zero extra network cost.
# --------------------------------------------------------------------------- #
def test_collection_job_learns_membership_from_track_events(tmp_path):
    stub = _BridgeStub(tmp_path)
    stub._job_signals[1] = SimpleNamespace(_media_id="album1", _collection=True)
    stub._track_lifecycle(1, {"id": "10", "status": "pending"})
    stub._track_lifecycle(1, {"id": "11", "status": "pending"})
    assert sorted(stub.collectionMemberIds("album1")) == ["10", "11"]
    assert stub.collectionMembershipChanged.emits == ["album1", "album1"]


def test_membership_is_learned_regardless_of_track_outcome(tmp_path):
    # Membership is "this track belongs to the collection", independent of
    # whether the download itself succeeded (a failed or skipped track is
    # still a real member, e.g. for an "all owned" rollup elsewhere).
    stub = _BridgeStub(tmp_path)
    stub._job_signals[1] = SimpleNamespace(_media_id="album1", _collection=True)
    stub._track_lifecycle(1, {"id": "10", "status": "failed"})
    assert stub.collectionMemberIds("album1") == ["10"]


def test_non_collection_job_does_not_record_membership(tmp_path):
    stub = _BridgeStub(tmp_path)
    stub._job_signals[1] = SimpleNamespace(_media_id="track10", _collection=False)
    stub._track_lifecycle(1, {"id": "10", "status": "pending"})
    assert stub.collectionMemberIds("track10") is None


def test_membership_recorded_once_per_track_not_per_event(tmp_path):
    # A track can report more than one lifecycle event (e.g. pending then
    # done); membership is only written on first sight to avoid redundant
    # writes, and repeats are harmless either way (INSERT OR IGNORE).
    stub = _BridgeStub(tmp_path)
    stub._job_signals[1] = SimpleNamespace(_media_id="album1", _collection=True)
    stub._track_lifecycle(1, {"id": "10", "status": "pending"})
    stub._track_lifecycle(1, {"id": "10", "status": "done"})
    assert stub.collectionMembershipChanged.emits == ["album1"]


def test_collection_member_ids_unknown_collection_is_none(tmp_path):
    stub = _BridgeStub(tmp_path)
    assert stub.collectionMemberIds("never-seen") is None
