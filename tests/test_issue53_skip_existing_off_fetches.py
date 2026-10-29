"""With "Skip existing" off, a download fetches every track (issue #53).

WHAT THIS FENCES OFF
--------------------
The reporter turned "Skip existing" off and downloaded a playlist into a new
folder, to have every song of it in one place for DJing. Every song Waves had
already downloaded earlier, into an album folder, was skipped anyway: the new
folder got neither the file nor a playlist entry for it, and the row read
IN LIBRARY.

The setting was only ever read by the engine's checks at the destination path.
The gate that runs before the engine, the ownership ledger (a copy Waves
recorded under the download or library folder) followed by the library scan's
tag claim, never asked it. Now a job queued with the setting off skips nothing:
neither gate is consulted and every track is fetched. With it on, nothing
changed.

The setting is pinned on the queue row the way askLibrarySkip is, so the run's
gate and the expanded row's prediction answer alike however Settings moves
while the queue works through.

Everything below drives the real _TrackedDownload.item / _claim_decision and
the real _enqueue, _job_skip_existing, _predict_skips and _download.
"""

from __future__ import annotations

import inspect
from threading import Event, Lock, local
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from _dispatch_stub import arm_dispatch
from tidalapi.media import Quality

from waves.waves_ui import backend
from waves.waves_ui.backend import WavesBridge, _TrackedDownload

# An owned copy at the quality every job here asks for (LOSSLESS), so with the
# gate armed it is current and skips.
_OWNED = {"path": "/music/Album Folder/01 Song.flac", "quality_rank": 2, "quality_tier": "LOSSLESS"}


# ---- the run's own gate ------------------------------------------------------


def _tracked(monkeypatch, *, skip_existing: bool, owned=_OWNED, claim=None):
    """A real _TrackedDownload without Download.__init__ (which needs a live
    session). The engine's item() is replaced by a recorder, so a test sees
    whether the gate let the track through to be fetched."""
    fetched: list = []

    def engine_item(self, *a, media=None, **k):
        fetched.append(media.id)
        return True, "/music/DJ Set/01 Song.flac"

    monkeypatch.setattr(backend.Download, "item", engine_item)
    td = _TrackedDownload.__new__(_TrackedDownload)
    td._track_signals = None
    td._outcome_lock = Lock()
    td.ok_count = td.write_count = td.skip_count = td.fail_count = td.unavailable_count = 0
    td._delivered = {}
    td._delivered_lock = Lock()
    td.event_abort = Event()
    td._ownership_of = (lambda tid: dict(owned)) if owned else (lambda tid: None)
    td._target_rank = 2  # LOSSLESS
    td._tls = local()
    td._skip_existing_base = skip_existing
    td.settings = SimpleNamespace(data=SimpleNamespace(download_dolby_atmos=False))
    td._library_claim = claim
    td._force_redownload = False
    return td, fetched


def _track(tid=42):
    return SimpleNamespace(id=tid, track_num=1, volume_num=1, duration=180)


def test_off_fetches_a_track_owned_in_another_folder(monkeypatch):
    """The report itself: the copy an album download left elsewhere no longer
    stops the playlist's own copy from landing."""
    td, fetched = _tracked(monkeypatch, skip_existing=False)
    ok, path = td.item(media=_track())
    assert fetched == [42], "the track must reach the engine and be fetched"
    assert ok is True and path == "/music/DJ Set/01 Song.flac"
    assert td.skip_count == 0


def test_on_still_skips_a_track_owned_in_another_folder(monkeypatch):
    """The control: with the setting on (its default) an owned copy at current
    quality is skipped without a fetch, as it always was."""
    td, fetched = _tracked(monkeypatch, skip_existing=True)
    ok, path = td.item(media=_track())
    assert fetched == []
    assert ok is True and path == ""
    assert td.skip_count == 1


def test_off_never_asks_the_library_claim(monkeypatch):
    """The second gate the report could hit: a tag match from the library scan.
    With the setting off it is not even consulted."""
    asked: list = []

    def claim(media):
        asked.append(media.id)
        return {"local_class": "LOSSLESS"}

    td, fetched = _tracked(monkeypatch, skip_existing=False, owned=None, claim=claim)
    td.item(media=_track())
    assert asked == []
    assert fetched == [42]


def test_on_still_honors_the_library_claim(monkeypatch):
    td, fetched = _tracked(monkeypatch, skip_existing=True, owned=None, claim=lambda m: {"local_class": "LOSSLESS"})
    td.item(media=_track())
    assert fetched == []
    assert td.skip_count == 1


def test_off_fetches_a_merge_member_owned_at_its_own_destination(monkeypatch):
    """Off means every item, merge members included, even one whose owned copy
    already sits in the merged folder: the engine replaces it there."""
    td, fetched = _tracked(monkeypatch, skip_existing=False)
    monkeypatch.setattr(_TrackedDownload, "_owned_at_destination", lambda self, *a, **k: True)
    media = _track()
    media.waves_identity_id = "7"
    td.item(media=media, file_template="{album}/{title}")
    assert fetched == [42]


def test_a_redownload_still_forces_with_the_setting_off(monkeypatch):
    td, _fetched = _tracked(monkeypatch, skip_existing=False)
    td._force_redownload = True
    assert td._claim_decision(_track())[0] == "force"


# ---- the queue row pins the setting ------------------------------------------


class _Signal:
    def emit(self, *args) -> None:
        pass


class _HoldingPool:
    def start(self, worker) -> None:
        pass


class _Stub:
    """Just what _enqueue, _job_skip_existing, _predict_skips and _download
    touch, with the real methods bound wherever the answer is under test."""

    _enqueue = WavesBridge._enqueue
    _queue_item = WavesBridge._queue_item
    _set_queue_status = WavesBridge._set_queue_status
    _job_library_skip = WavesBridge._job_library_skip
    _job_skip_existing = WavesBridge._job_skip_existing
    _skip_existing_on = WavesBridge._skip_existing_on
    _job_quality = WavesBridge._job_quality
    _job_atmos = WavesBridge._job_atmos
    _queued_quality_value = WavesBridge._queued_quality_value
    _target_tier = WavesBridge._target_tier
    _target_quality_rank = WavesBridge._target_quality_rank
    _predict_skips = WavesBridge._predict_skips
    _download = WavesBridge._download
    _ask_quality_for = WavesBridge._ask_quality_for
    _ask_atmos_for = WavesBridge._ask_atmos_for
    _quality_override_key = WavesBridge._quality_override_key
    _row_ask = WavesBridge._row_ask

    def __init__(self, skip_existing: bool) -> None:
        self._logged_in = True
        self._queue_seq = 0
        self._queue: list = []
        self._queue_index: dict = {}
        self._queue_lock = Lock()
        self._job_aborts: dict = {}
        self._job_signals: dict = {}
        self._job_dls: dict = {}
        self._job_tracks: dict = {}
        self._merge_plans: dict = {}
        self._redownload_overrides: set = set()
        self._library_claim_overrides: set = set()
        self._objs = {"album": {}}
        # Every track is owned in some other folder, at the job's quality.
        self._ownership = SimpleNamespace(ownership_of=lambda tid: dict(_OWNED))
        self.settings = SimpleNamespace(
            data=SimpleNamespace(
                quality_audio=Quality.high_lossless,
                download_dolby_atmos=False,
                download_base_path="/tmp/waves-out",
                download_delay=False,
                downloads_concurrent_max=2,
                skip_existing=skip_existing,
            )
        )
        self.dl_pool = _HoldingPool()
        self.downloadState = _Signal()
        self.downloadProgress = _Signal()
        self._track_poll = SimpleNamespace(isActive=lambda: True, start=lambda *a: None)
        self._speed_poll = SimpleNamespace(isActive=lambda: True, start=lambda *a: None)
        arm_dispatch(self)
        self.built: list[dict] = []

    def _library_bulk_skip_on(self) -> bool:
        return False

    def _library_claim_media(self, media, album=None):
        return None

    def _emit_queue(self) -> None:
        pass

    def _download_gate(self) -> str:
        return "ok"

    def _ffmpeg_gate_holds(self, media_id, retry) -> bool:
        return False

    def _build_download(self, signals, **kwargs):
        self.built.append(kwargs)
        return SimpleNamespace(path_base="")

    def _set_status(self, msg) -> None:
        pass


def _playlist():
    return SimpleNamespace(
        id="pl-1",
        name="DJ Set",
        artist=None,
        artists=[],
        audio_quality=None,
        album=None,
        duration=200,
    )


def _tracks(*ids):
    return [SimpleNamespace(id=i, name=f"t{i}", duration=200) for i in ids]


def _queue_playlist(stub) -> int:
    return stub._enqueue("DJ Set", "playlist", "pl-1", collection=True)


def _dispatch(stub) -> dict:
    with patch.object(backend, "_ProgressSignals", lambda *a, **k: object()):
        stub._download(_playlist(), "playlist", "DJ Set", "{title}", True, "pl-1")
    assert len(stub.built) == 1, stub.built
    return stub.built[0]


@pytest.mark.parametrize("setting_on", [True, False])
def test_the_row_records_the_setting_it_was_queued_with(setting_on):
    stub = _Stub(setting_on)
    qid = _queue_playlist(stub)
    assert stub._queue_index[qid]["askSkipExisting"] is setting_on


@pytest.mark.parametrize("pinned", [True, False])
def test_changing_the_setting_afterwards_does_not_reach_a_queued_row(pinned):
    stub = _Stub(pinned)
    qid = _queue_playlist(stub)
    stub.settings.data.skip_existing = not pinned
    assert stub._job_skip_existing(qid) is pinned


def test_a_row_from_before_the_field_reads_the_setting():
    stub = _Stub(False)
    qid = _queue_playlist(stub)
    del stub._queue_index[qid]["askSkipExisting"]
    assert stub._job_skip_existing(qid) is False


@pytest.mark.parametrize("pinned", [True, False])
def test_the_run_is_built_with_the_rows_pin(pinned):
    """The job's download is built with what the row says, read through
    _job_skip_existing: the setting is moved the moment the row is created,
    so a build that read Settings instead would land on the opposite."""
    stub = _Stub(pinned)
    enqueue = stub._enqueue

    def enqueue_then_flip(*a, **k):
        qid = enqueue(*a, **k)
        stub.settings.data.skip_existing = not pinned
        return qid

    stub._enqueue = enqueue_then_flip
    assert _dispatch(stub)["skip_existing"] is pinned


def test_a_row_queued_off_promises_no_skips():
    """The drawer must not paint IN LIBRARY on a track the run will fetch."""
    stub = _Stub(False)
    qid = _queue_playlist(stub)
    stub.settings.data.skip_existing = True  # moved after queueing: no effect
    assert stub._predict_skips(qid, stub._queue_index[qid], _tracks("1", "2")) == {}


def test_a_row_queued_on_still_predicts_the_owned_skips():
    stub = _Stub(True)
    qid = _queue_playlist(stub)
    marks = stub._predict_skips(qid, stub._queue_index[qid], _tracks("1"))
    assert set(marks) == {"1"} and marks["1"]["kind"] == "own", marks


@pytest.mark.parametrize("pinned", [True, False])
def test_the_run_and_the_expanded_row_answer_alike(pinned):
    stub = _Stub(pinned)
    built = _dispatch(stub)
    stub.settings.data.skip_existing = not pinned  # the person changed their mind
    qid = stub._queue[0]["qid"]
    marks = stub._predict_skips(qid, stub._queue_index[qid], _tracks("1"))
    assert built["skip_existing"] is pinned
    assert bool(marks) is pinned, marks


def test_neither_reader_consults_the_live_setting():
    """The audit guard: both readers go through the row's pin."""
    for name in ("_predict_skips", "_start_job"):
        src = inspect.getsource(getattr(WavesBridge, name))
        assert "_job_skip_existing(qid)" in src, f"{name} stopped reading the row's pin"
        assert "settings.data.skip_existing" not in src, f"{name} went back to the live setting"
