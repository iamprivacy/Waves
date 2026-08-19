"""Each queue row follows its OWN track's progress, not a namesake's.

THE BUG
-------
The engine registers one rich progress task per downloading item, described as
``[blue]Item '<display name>'`` with the name cut to 30 characters. The bridge
mirrored that string onto each queue row and polled percentages with
``{task.description: task.percentage}``.

A description built from ``"<every artist>, ... - <title>"`` is not unique. Any
release whose joined artist credit already runs 30 characters (classical
credits, three-way features) truncates EVERY one of its tracks to the same
string, and a dict keyed on it keeps only the task added last. So all in-flight
rows showed one sibling's percentage, ``_bump_group_progress`` summed those
mirrored values into the album roll-up, and because that roll-up only ever
rises, the inflated number stuck for the rest of the job: a 10-track album
reading 28% while three tracks were at 5%, 5% and 95%.

Rich never removes a finished task either, so a completed track's 100% went on
owning the key for every later track that collided with it.

THE FIX: the engine hands the bridge the TaskID through a ``_note_progress_task``
hook, filed under the queue row the item is being downloaded for, and the poller
reads each row through its own TaskID. Descriptions are for display.
"""

from __future__ import annotations

import pathlib
from threading import Lock, local
from unittest.mock import MagicMock, patch

from rich.progress import Progress
from tidalapi.media import Track

from tidaler import download as download_mod
from tidaler.download import Download
from tidaler.waves_ui.backend import WavesBridge, _TrackedDownload

# A credit that eats the whole 30-character budget on its own, so every track of
# the release truncates to a single identical description. Real shape, not a
# contrived string: this is what a classical or multi-feature release looks like.
LONG_CREDIT = "Berliner Philharmoniker, Herbert von Karajan"


class _Signal:
    def __init__(self):
        self.emits: list = []

    def emit(self, *args):
        self.emits.append(args if len(args) != 1 else args[0])


def _bare_tracked(progress: Progress) -> _TrackedDownload:
    """A real _TrackedDownload with only the attributes the hook and the poller
    touch, built with __new__ so Download.__init__ (network, session) is skipped."""
    dl = _TrackedDownload.__new__(_TrackedDownload)
    dl._tls = local()
    dl._row_tasks = {}
    dl._row_tasks_lock = Lock()
    dl.progress = progress
    return dl


class _PollStub:
    """Stand-in for WavesBridge carrying only what _poll_track_progress touches,
    with the real poller and the real roll-up bound on."""

    def __init__(self, dl, reg, total):
        self._job_dls = {7: dl}
        self._job_tracks = {7: reg}
        self._track_poll = MagicMock()
        self._pct_last = {}
        self.queueTrackPct = _Signal()
        self._queue_index = {7: {"collection": True, "tracks": total, "progress": 0.0}}
        self._rollups: list = []
        for name in ("_poll_track_progress", "_bump_group_progress"):
            setattr(self, name, getattr(WavesBridge, name).__get__(self, _PollStub))

    def _queue_item(self, qid):
        return self._queue_index.get(qid)

    def _report_pct(self, _media_id, _qid, pct):
        self._rollups.append(pct)

    def _set_queue_progress(self, _qid, pct):
        self._rollups.append(pct)


def _register(dl, progress, row_key: str, title: str, pct: float):
    """What the engine does for one item: build the task through the engine's
    own _setup_progress (so the real description and the real truncation apply),
    announce it through the hook, then advance it."""
    media_name = f"{LONG_CREDIT} - {title}"
    with patch.object(download_mod, "name_builder_item", return_value=media_name):
        # Two urls means a segment count, so _setup_progress takes no HTTP path.
        p_task, _total, _block = Download._setup_progress(dl, media_name, ["u1", "u2"], False)
    dl._tls.row_key = row_key
    dl._note_progress_task(MagicMock(spec=Track), p_task)
    dl._tls.row_key = ""
    progress.update(p_task, completed=pct / 50.0)  # total is 2 urls
    return p_task


def _running_rows(*ids):
    return {i: {"id": i, "status": "running", "pct": 0.0} for i in ids}


# --- the collision is real ---------------------------------------------------


def test_a_long_credit_gives_every_track_the_same_description():
    """The premise. If this ever stops being true the rest is moot, so pin it
    against the engine's own description builder rather than assuming it."""
    progress = Progress()
    dl = _bare_tracked(progress)
    _register(dl, progress, "1", "Symphony No. 5, First Movement", 10.0)
    _register(dl, progress, "2", "Symphony No. 9, Fourth Movement", 90.0)
    descriptions = {t.description for t in progress.tasks}
    assert len(progress.tasks) == 2
    assert len(descriptions) == 1, "two tracks, one description: the old key could not tell them apart"


# --- each row reads its own task ---------------------------------------------


def test_each_row_reads_its_own_percentage():
    progress = Progress()
    dl = _bare_tracked(progress)
    _register(dl, progress, "1", "Symphony No. 5, First Movement", 10.0)
    _register(dl, progress, "2", "Symphony No. 9, Fourth Movement", 90.0)
    stub = _PollStub(dl, _running_rows("1", "2"), total=2)
    stub._poll_track_progress()
    assert stub.queueTrackPct.emits == [
        (7, {"1": 10.0, "2": 90.0})
    ], "each row must read its own track, not whichever task registered last"


def test_the_album_rollup_is_not_inflated_by_the_mirrored_values():
    """The damage the old key did was cumulative: three rows all reporting the
    last task's percentage, summed into the roll-up, which never comes back
    down. True progress here is (5 + 5 + 95) / 10 tracks = 10.5%."""
    progress = Progress()
    dl = _bare_tracked(progress)
    _register(dl, progress, "1", "Symphony No. 5, First Movement", 5.0)
    _register(dl, progress, "2", "Symphony No. 9, Fourth Movement", 5.0)
    _register(dl, progress, "3", "Symphony No. 3, Second Movement", 95.0)
    stub = _PollStub(dl, _running_rows("1", "2", "3"), total=10)
    stub._poll_track_progress()
    assert stub._rollups, "the roll-up has to be reported"
    assert abs(stub._rollups[-1] - 10.5) < 0.01, f"inflated roll-up: {stub._rollups[-1]}"


def test_a_finished_track_does_not_own_a_later_track_s_reading():
    """Rich never removes a task, so a completed 100% task stays in the list.
    Under the old key it kept answering for every later namesake."""
    progress = Progress()
    dl = _bare_tracked(progress)
    _register(dl, progress, "1", "Symphony No. 5, First Movement", 100.0)
    _register(dl, progress, "2", "Symphony No. 9, Fourth Movement", 20.0)
    reg = _running_rows("2")
    reg["1"] = {"id": "1", "status": "done", "pct": 100.0}
    stub = _PollStub(dl, reg, total=2)
    stub._poll_track_progress()
    assert stub.queueTrackPct.emits == [(7, {"2": 20.0})]


def test_a_row_with_no_task_yet_holds_its_value():
    """Between the running event and the engine sizing the stream there is no
    task. The row must hold, not read a stranger's percentage."""
    progress = Progress()
    dl = _bare_tracked(progress)
    _register(dl, progress, "1", "Symphony No. 5, First Movement", 60.0)
    stub = _PollStub(dl, _running_rows("1", "2"), total=2)
    stub._poll_track_progress()
    assert stub.queueTrackPct.emits == [(7, {"1": 60.0})], "row 2 has no task and must be left alone"


# --- the engine really does announce it --------------------------------------


def test_the_engine_announces_the_task_it_created():
    """The contract test. Everything above stands on the engine calling the
    hook, so drive the engine's own _download() and patch only what surrounds
    the announcement. If the engine ever stops announcing, this is what notices."""
    dl = _bare_tracked(Progress())
    dl.progress_gui = None
    seen: list = []
    dl._note_progress_task = lambda media, p_task: seen.append(int(p_task))
    track = MagicMock(spec=Track)
    with (
        patch.object(download_mod, "name_builder_item", return_value="Artist - Song"),
        patch.object(Download, "_get_media_urls", return_value=["u1", "u2"]),
        patch.object(Download, "_setup_progress", return_value=(4242, 2, None)),
        patch.object(Download, "_download_segments", return_value=(True, [])),
        patch.object(Download, "_download_postprocess", return_value=(True, pathlib.Path("/tmp/a.flac"))),
    ):
        Download._download(dl, track, pathlib.Path("/tmp/a.flac"))
    assert seen == [4242], "the engine must hand its caller the task it just created"
