"""A download announces 0% before it announces running.

WHAT THIS FENCES OFF
--------------------
The download button's progress readout is visible from the "running" frame,
sitting in a slot reserved for its widest value ("100%"), and its first real
progress tick can be seconds out: a collection lists its tracks and settles
its library claims before the first segment lands. Until this fix the worker
emitted ``downloadState(id, "running")`` alone, so the readout opened on the
"…" placeholder in that slot, which read as blank space beside the bar (a
livetest report on a Browse playlist card). A re-run of the same id also
inherited the previous run's 100% until its own first tick.

The worker now emits ``downloadProgress(id, 0.0)`` immediately BEFORE
``downloadState(id, "running")``, the order the folder and discography
rollups already use, so the running frame reads 0%.

HOW THIS STAYS FIXED
--------------------
The real ``WavesBridge._download`` runs against a bare stand-in that records
its ``downloadProgress`` and ``downloadState`` emits into ONE ordered list;
the assertion is on the order, not just on presence.
"""

from __future__ import annotations

from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import patch

from _dispatch_stub import arm_dispatch

from waves.waves_ui import backend
from waves.waves_ui.backend import WavesBridge


class _Recorder:
    """A signal stand-in that appends (name, args) to a shared ordered log."""

    def __init__(self, name: str, log: list) -> None:
        self._name = name
        self._log = log

    def emit(self, *args) -> None:
        self._log.append((self._name, args))


class _InlinePool:
    def start(self, worker) -> None:
        worker.run()


class _FakeDownload:
    path_base = ""
    unavailable_count = 0

    def item(self, **kwargs):
        return True, "/tmp/song.flac"


class _Stub:
    """Just the attributes ``_download`` and its worker touch on the happy
    path of a single track; every gate answers "go"."""

    def __init__(self, log: list) -> None:
        self._logged_in = True
        self._job_aborts: dict[int, Event] = {}
        self._job_signals: dict = {}
        self._job_dls: dict = {}
        self._job_tracks: dict = {}
        self._merge_plans: dict = {}
        self._redownload_overrides: set = set()
        self._library_claim_overrides: set = set()
        self._queue: list = []
        self._queue_lock = Lock()
        self.settings = SimpleNamespace(data=SimpleNamespace(download_base_path="/tmp/waves-out", download_delay=False))
        self.dl_pool = _InlinePool()
        self.downloadState = _Recorder("state", log)
        self.downloadProgress = _Recorder("progress", log)
        arm_dispatch(self)
        self.statuses: list[tuple[int, str]] = []
        self.status_line: list[str] = []

    def _download_gate(self) -> str:
        return "ok"

    # The per-item quality choice _download reads at queue time (issue #36):
    # none here, so the ask is the setting's.
    def _ask_quality_for(self, obj, type_media, media_id):
        return ("LOSSLESS", "LOSSLESS")

    def _row_ask(self, qid):
        return None

    def _ffmpeg_gate_holds(self, media_id, retry) -> bool:
        return False

    def _enqueue(self, *args, **kwargs) -> int:
        return 41

    def _build_download(self, signals, **kwargs):
        self.built_with = kwargs
        return _FakeDownload()

    def _job_quality(self, qid):
        # The real bridge pins the row's queued audio quality onto its job;
        # this stub keeps no rows, so it answers "no pin".
        return None

    def _gate_reachability(self, retry, media_id) -> bool:
        return True

    def _set_queue_status(self, qid, status, reason: str = "") -> None:
        self.statuses.append((qid, status))

    def _set_queue_progress(self, qid, pct) -> None:
        pass

    def _set_status(self, msg) -> None:
        self.status_line.append(msg)

    def _bump_download_groups(self, media_id, pct, status) -> None:
        pass

    def _release_job_signals(self, qid) -> None:
        self._job_signals.pop(qid, None)


def _track():
    return SimpleNamespace(
        id="t-1",
        name="Song",
        artist=SimpleNamespace(name="Artist"),
        artists=[],
        audio_quality=None,
        album=None,
        duration=200,
    )


def _run(media_id: str = "t-1") -> list:
    log: list = []
    stub = _Stub(log)
    # The per-job relay is a QObject parented to the bridge; the stand-in is
    # not one, and the relay's wiring is not what this test is about.
    with patch.object(backend, "_ProgressSignals", lambda *a, **k: object()):
        WavesBridge._download(stub, _track(), "track", "Song", "{title}", False, media_id)
    assert stub.statuses[-1] == (41, "done"), stub.statuses
    return log


def test_zero_percent_lands_before_running():
    log = _run()
    kinds = [(name, args[1]) for name, args in log]
    assert ("progress", 0.0) in kinds, log
    assert ("state", "running") in kinds, log
    assert kinds.index(("progress", 0.0)) < kinds.index(
        ("state", "running")
    ), "the running frame must already read 0%, not the placeholder: " + repr(log)


def test_running_and_zero_percent_are_about_the_same_id():
    log = _run("t-99")
    assert ("progress", ("t-99", 0.0)) in log, log
    assert ("state", ("t-99", "running")) in log, log


def test_the_zero_comes_after_queued_and_before_done():
    """The whole shape of a run, so the new emit cannot drift into the queued
    acknowledgement (the button would show 0% while still waiting) or past
    the finish."""
    log = _run()
    order = [(name, args[1]) for name, args in log]
    assert order.index(("state", "queued")) < order.index(("progress", 0.0)) < order.index(("state", "done")), order
