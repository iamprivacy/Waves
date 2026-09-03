"""A queue row keeps the "skip songs already in my library" setting it was queued with.

WHAT THIS FENCES OFF
--------------------
Two places asked whether the library scan may skip songs for a job: the run
itself, when it started fetching, and the expanded queue row, when someone
opened it to see what the download was going to do. Both asked the live
setting, at two different moments, so they disagreed the moment the setting
moved.

What a person saw: leave "skip songs already in my library" off, queue a long
playlist, then turn that setting on while the queue is still working through
it. Expand a row that has not started yet and every song the library already
holds is marked IN LIBRARY in gold. The download then fetches all of them
anyway.

The row now records the setting once, when it is queued, exactly the way it
already records the audio quality it will ask for: a change in Settings
retargets nothing already queued or running, it decides what is queued from
then on. Both readers, the run's own gate and the expanded row's prediction,
read that one recorded value (WavesBridge._job_library_skip), so they cannot
answer differently.

Everything below drives the real _enqueue, _job_library_skip, _predict_skips
and _download; nothing here recomputes the decision itself.
"""

from __future__ import annotations

import inspect
from threading import Lock
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from _dispatch_stub import arm_dispatch
from tidalapi.media import Quality

from waves.waves_ui import backend
from waves.waves_ui.backend import WavesBridge


class _Signal:
    def emit(self, *args) -> None:
        pass


class _HoldingPool:
    """Takes the job worker and never runs it. These tests are about the
    decision _download makes before it hands the job off."""

    def __init__(self) -> None:
        self.started = 0

    def start(self, worker) -> None:
        self.started += 1


class _Stub:
    """Just what _enqueue, _job_library_skip, _predict_skips and _download
    touch, with the real methods bound on wherever the answer is the thing
    under test. Every gate answers "go"."""

    # The production code under test, running on this carcass.
    _enqueue = WavesBridge._enqueue
    _queue_item = WavesBridge._queue_item
    _set_queue_status = WavesBridge._set_queue_status
    _job_library_skip = WavesBridge._job_library_skip
    _job_quality = WavesBridge._job_quality
    _queued_quality_value = WavesBridge._queued_quality_value
    _target_tier = WavesBridge._target_tier
    _target_quality_rank = WavesBridge._target_quality_rank
    _predict_skips = WavesBridge._predict_skips
    _download = WavesBridge._download
    # The per-item quality choice _download reads at queue time (issue #36);
    # this carcass holds none, so the ask is the setting's.
    _ask_quality_for = WavesBridge._ask_quality_for
    _quality_override_key = WavesBridge._quality_override_key
    _row_ask = WavesBridge._row_ask

    def __init__(self, pref) -> None:
        # `pref` is a zero-argument callable, so a test can move the live
        # setting between the moment a row is queued and the moment something
        # reads it back.
        self._pref = pref
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
        # Nothing is owned on disk here, so the prediction always reaches the
        # library claim gate, which is the gate these tests are about.
        self._ownership = SimpleNamespace(ownership_of=lambda tid: None)
        self.settings = SimpleNamespace(
            data=SimpleNamespace(
                quality_audio=Quality.high_lossless,
                download_dolby_atmos=False,
                download_base_path="/tmp/waves-out",
                download_delay=False,
                downloads_concurrent_max=2,
            )
        )
        self.dl_pool = _HoldingPool()
        self.downloadState = _Signal()
        self.downloadProgress = _Signal()
        self._track_poll = SimpleNamespace(isActive=lambda: True, start=lambda *a: None)
        arm_dispatch(self)
        self.built: list[dict] = []

    def _library_bulk_skip_on(self) -> bool:
        """The LIVE setting: the thing a queued row may consult exactly once,
        when it is created."""
        return self._pref()

    def _library_claim_media(self, media, album=None):
        """The scan's answer for one song, standing in for the index. Always a
        match, so a gate that is armed always marks and a gate that is not
        never does."""
        return {"local_class": "LOSSLESS"}

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


# ---- the live setting, in the states a person can leave it in ---------------


class _Live:
    """The live setting as a thing a person can move: call it to read it, set
    `.value` to change it."""

    def __init__(self, value: bool) -> None:
        self.value = value

    def __call__(self) -> bool:
        return self.value


def _read_once(value: bool):
    """Answers `value` once, then fails the test. Queueing a row is the one and
    only read a job is allowed, so anything reading it again is the bug."""
    calls: list[int] = []

    def pref() -> bool:
        calls.append(1)
        if len(calls) > 1:
            pytest.fail("the live setting was read again after the row was queued")
        return value

    return pref


def _flipped_after(value: bool):
    """Answers `value` once, then the opposite forever: the person changed the
    setting while the queue was still working through the list."""
    calls: list[int] = []

    def pref() -> bool:
        calls.append(1)
        return value if len(calls) == 1 else (not value)

    return pref


def _moves_on_every_read(value: bool):
    """Answers `value`, then the opposite, then back, and so on. Two readers
    that each asked the live setting would land on opposite answers."""
    calls: list[int] = []

    def pref() -> bool:
        calls.append(1)
        return value if len(calls) % 2 == 1 else (not value)

    return pref


# ---- fixtures the real code chews on ----------------------------------------


def _media():
    return SimpleNamespace(
        id="al-1",
        name="Album",
        artist=SimpleNamespace(name="Artist"),
        artists=[],
        audio_quality=None,
        album=None,
        duration=200,
    )


def _tracks(*ids):
    return [SimpleNamespace(id=i, name=f"t{i}", duration=200) for i in ids]


def _queue_album(stub) -> int:
    return stub._enqueue("Album", "album", "al-1", collection=True)


def _dispatch(stub):
    """Run the real _download as far as handing the job off, and return the
    library claim gate it wired into the engine."""
    with patch.object(backend, "_ProgressSignals", lambda *a, **k: object()):
        stub._download(_media(), "album", "Album", "{title}", True, "al-1")
    assert len(stub.built) == 1, stub.built
    return stub.built[0]["library_claim"]


# ---- the row records it ------------------------------------------------------


@pytest.mark.parametrize("setting_on", [True, False])
def test_the_row_records_the_setting_it_was_queued_with(setting_on):
    stub = _Stub(_Live(setting_on))
    qid = _queue_album(stub)
    assert stub._queue_index[qid]["askLibrarySkip"] is setting_on


def test_the_setting_is_pinned_the_same_way_the_audio_quality_is():
    """Both are settled when the row is created and carried for its whole life,
    so a row wears both from birth."""
    stub = _Stub(_Live(True))
    qid = _queue_album(stub)
    row = stub._queue_index[qid]
    assert row["askQuality"] == Quality.high_lossless.value
    assert row["askLibrarySkip"] is True


# ---- and never re-reads it ---------------------------------------------------


@pytest.mark.parametrize("pinned", [True, False])
def test_changing_the_setting_afterwards_does_not_reach_a_queued_row(pinned):
    """The whole point. Queue the row, then change the setting: the row still
    answers what it was queued with."""
    live = _Live(pinned)
    stub = _Stub(live)
    qid = _queue_album(stub)
    live.value = not pinned  # the person changed their mind, mid-queue
    assert stub._job_library_skip(qid) is pinned


def test_a_row_that_has_left_the_queue_claims_nothing():
    """No row, no pin, and nothing is skipped on a job that is gone."""
    stub = _Stub(_Live(True))
    assert stub._job_library_skip(999) is False


# ---- the expanded row's prediction reads the pin -----------------------------


def test_the_prediction_reads_the_rows_pin_and_not_the_live_setting():
    """Opening a queued row marks the songs the run will skip. It must ask what
    the row was queued with: the live setting here refuses a second read, so a
    prediction that went back to it cannot pass."""
    stub = _Stub(_read_once(True))
    qid = _queue_album(stub)
    marks = stub._predict_skips(qid, stub._queue_index[qid], _tracks("1", "2"))
    assert marks == {
        "1": {"kind": "claim", "tier": "LOSSLESS"},
        "2": {"kind": "claim", "tier": "LOSSLESS"},
    }, marks


def test_a_row_queued_with_the_setting_off_promises_no_skips():
    """The direction people actually hit: the row was queued before the setting
    was turned on, so the run will fetch every song and the drawer may not
    paint a single one as IN LIBRARY. The live setting refuses a second read
    here too."""
    stub = _Stub(_read_once(False))
    qid = _queue_album(stub)
    assert stub._predict_skips(qid, stub._queue_index[qid], _tracks("1", "2")) == {}


# ---- the run wires its own gate from the same pin ----------------------------


@pytest.mark.parametrize("pinned", [True, False])
def test_the_run_arms_its_claim_gate_from_the_rows_pin(pinned):
    """A row pinned on hands the engine a working gate (it really is the scan's
    lookup); a row pinned off hands it nothing at all."""
    stub = _Stub(_read_once(pinned))
    claim = _dispatch(stub)
    if pinned:
        assert callable(claim)
        assert claim(_tracks("1")[0]) == {"local_class": "LOSSLESS"}
    else:
        assert claim is None


@pytest.mark.parametrize("pinned", [True, False])
def test_moving_the_setting_between_queueing_and_starting_changes_nothing(pinned):
    """A job can sit in the queue for a long time. Whatever the setting says by
    the time a worker picks it up, the job fetches the way it was queued."""
    stub = _Stub(_flipped_after(pinned))
    claim = _dispatch(stub)
    assert (claim is not None) is pinned


# ---- the invariant the bug broke ---------------------------------------------


@pytest.mark.parametrize("pinned", [True, False])
def test_the_run_and_the_expanded_row_answer_the_same_question(pinned):
    """One row, one answer: the gate the download arms and the gate the drawer
    assumes are the same gate, so a song marked IN LIBRARY in the drawer is a
    song the run really skips.

    The live setting moves on every read here, so two readers that each asked
    it live would land on opposite answers and this would catch them."""
    stub = _Stub(_moves_on_every_read(pinned))
    claim = _dispatch(stub)
    qid = stub._queue[0]["qid"]
    marks = stub._predict_skips(qid, stub._queue_index[qid], _tracks("1"))
    assert (claim is not None) == bool(marks), (claim, marks)
    assert (claim is not None) is pinned, "and the answer they agree on is the one the row was queued with"


@pytest.mark.parametrize("overruled", [True, False])
def test_download_anyway_moves_the_run_and_the_drawer_together(overruled):
    """The same invariant on the other input that decides the gate. DOWNLOAD
    ANYWAY on an album the library claimed means the person saw the claim and
    overruled it, so the run fetches every song of it. The expanded row has to
    say the same, or a whole album is painted gold as IN LIBRARY while it
    downloads in full.

    The pin cannot carry this on its own: the row is queued with the setting on
    in both cases here and only the override moves, so a reader that dropped the
    override and kept the pin would still look right everywhere else."""
    stub = _Stub(_read_once(True))
    if overruled:
        stub._library_claim_overrides.add("al-1")
    claim = _dispatch(stub)
    qid = stub._queue[0]["qid"]
    marks = stub._predict_skips(qid, stub._queue_index[qid], _tracks("1"))
    assert (claim is not None) == bool(marks), (claim, marks)
    assert (claim is None) is overruled, "overruling the claim disarms both readers, and nothing else does"


def test_a_row_that_has_started_running_answers_as_it_did_queued():
    """People expand a row to watch it work, not only before it starts. The
    answer belongs to the row, not to how far the job has got: a run that is
    skipping songs you already have keeps showing them as IN LIBRARY once it is
    under way, and the gate the run is already holding cannot drift away from
    the drawer halfway through."""
    stub = _Stub(_read_once(True))
    claim = _dispatch(stub)
    qid = stub._queue[0]["qid"]
    queued_marks = stub._predict_skips(qid, stub._queue_index[qid], _tracks("1"))
    assert queued_marks == {"1": {"kind": "claim", "tier": "LOSSLESS"}}, queued_marks
    # Exactly what the job's own worker does the moment it picks the row up.
    stub._set_queue_status(qid, "running")
    running_marks = stub._predict_skips(qid, stub._queue_index[qid], _tracks("1"))
    assert stub._job_library_skip(qid) is True
    assert running_marks == queued_marks, (queued_marks, running_marks)
    assert (claim is not None) == bool(running_marks), (claim, running_marks)


def test_neither_reader_consults_the_live_setting():
    """The audit guard. Both readers go through the row's pin; a new live read
    in either of them puts the drawer and the run back out of step."""
    # The run's reader lives in _start_job now (the job body, built when the
    # row's turn comes); the pin itself is still taken at _enqueue time.
    for name in ("_predict_skips", "_start_job"):
        src = inspect.getsource(getattr(WavesBridge, name))
        assert "_job_library_skip(qid)" in src, f"{name} stopped reading the row's pin"
        assert "_library_bulk_skip_on" not in src, f"{name} went back to the live setting"
