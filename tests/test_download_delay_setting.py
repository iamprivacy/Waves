"""The "Download delay" setting actually reaches the engine.

THE BUG
-------
``download_delay`` is a Waves setting: it appears in ``_FLAG_FIELDS``, on the
Settings page under Downloads, and its two companion fields "Minimum/Maximum
download delay (s)" were live. The flag itself was never read by anything in
``waves/``.

The engine takes it as a PARAMETER and relies on the caller to forward it.
Waves forwarded nothing, so each dispatch fell back to a different default and
the toggle was inert in both directions:

  * a collection got ``items()``' default of True, so turning the delay OFF
    still slept a random 3 to 5 seconds after every written track, roughly
    27 seconds of pure sleep on a 20-track album;
  * a single track got ``item()``' default of False, so turning the delay ON
    never delayed anything;
  * the "best of both" fan-out stands in for ``items()`` and forwarded nothing
    either, so the setting was honored on a plain album and ignored on a merged
    one.

THE FIX forwards ``settings.data.download_delay`` at all three dispatch sites.
"""

from __future__ import annotations

from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import patch

from _dispatch_stub import arm_dispatch

from waves.download import Download
from waves.waves_ui import backend
from waves.waves_ui.backend import WavesBridge


class _Signal:
    def emit(self, *args) -> None:
        pass


class _InlinePool:
    def start(self, worker) -> None:
        worker.run()


class _RecordingDownload:
    """Records the kwargs each dispatch hands the engine."""

    path_base = ""
    unavailable_count = 0
    # The merge fan-out ends the way items() does, through these two engine
    # methods; the path collector is the real one (pure), the playlist step is
    # a no-op here because this file is about the delay flag, not the file.
    _landed_paths = staticmethod(Download._landed_paths)

    def __init__(self) -> None:
        self.item_calls: list[dict] = []
        self.items_calls: list[dict] = []

    def item(self, **kwargs):
        self.item_calls.append(kwargs)
        return True, "/tmp/song.flac"

    def items(self, **kwargs):
        self.items_calls.append(kwargs)
        self.write_count = 1
        self.ok_count = 1
        return None

    def _playlist_for_collection(self, media, file_template, result_paths) -> None:
        pass

    write_count = 0
    ok_count = 0
    fail_count = 0
    list_unavailable = False


class _Stub:
    """Just what _download and its worker touch on the happy path; every gate
    answers "go". Mirrors tests/test_download_start_readout.py's stand-in."""

    def __init__(self, delay: bool) -> None:
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
        self.settings = SimpleNamespace(
            data=SimpleNamespace(
                download_base_path="/tmp/waves-out",
                download_delay=delay,
                downloads_concurrent_max=2,
            )
        )
        self.dl_pool = _InlinePool()
        self.downloadState = _Signal()
        self.downloadProgress = _Signal()
        self.dl = _RecordingDownload()
        self._track_poll = SimpleNamespace(isActive=lambda: True, start=lambda *a: None)
        arm_dispatch(self)

    def _download_gate(self) -> str:
        return "ok"

    def _job_library_skip(self, qid: int) -> bool:
        # The claim gate this row pinned when it was queued. Off, so the delay
        # is all these tests are looking at.
        return False

    def _ffmpeg_gate_holds(self, media_id, retry) -> bool:
        return False

    def _enqueue(self, *args) -> int:
        return 41

    def _build_download(self, signals, **kwargs):
        return self.dl

    def _job_quality(self, qid):
        return None

    def _gate_reachability(self, retry, media_id) -> bool:
        return True

    def _set_queue_status(self, qid, status, reason: str = "") -> None:
        pass

    def _set_queue_progress(self, qid, pct) -> None:
        pass

    def _set_status(self, msg) -> None:
        pass

    def _bump_download_groups(self, media_id, pct, status) -> None:
        pass

    def _release_job_signals(self, qid) -> None:
        self._job_signals.pop(qid, None)


def _media():
    return SimpleNamespace(
        id="t-1",
        name="Song",
        artist=SimpleNamespace(name="Artist"),
        artists=[],
        audio_quality=None,
        album=None,
        duration=200,
    )


def _run(*, delay: bool, collection: bool) -> _RecordingDownload:
    stub = _Stub(delay)
    with patch.object(backend, "_ProgressSignals", lambda *a, **k: object()):
        WavesBridge._download(
            stub,
            _media(),
            "album" if collection else "track",
            "Song",
            "{title}",
            collection,
            "t-1",
        )
    return stub.dl


# --- a collection ------------------------------------------------------------


def test_a_collection_forwards_the_delay_when_it_is_on():
    dl = _run(delay=True, collection=True)
    assert [c["download_delay"] for c in dl.items_calls] == [True]


def test_a_collection_forwards_the_delay_when_it_is_off():
    """The expensive direction. items() defaults to True, so a caller that
    forwards nothing sleeps 3 to 5 seconds per track for a setting the user
    switched off."""
    dl = _run(delay=False, collection=True)
    assert [c["download_delay"] for c in dl.items_calls] == [False]


# --- a single track ----------------------------------------------------------


def test_a_single_track_forwards_the_delay_when_it_is_on():
    """The other direction. item() defaults to False, so a caller that forwards
    nothing never delays even with the setting on."""
    dl = _run(delay=True, collection=False)
    assert [c["download_delay"] for c in dl.item_calls] == [True]


def test_a_single_track_forwards_the_delay_when_it_is_off():
    dl = _run(delay=False, collection=False)
    assert [c["download_delay"] for c in dl.item_calls] == [False]


# --- the best-of-both fan-out ------------------------------------------------


def _run_merge(delay: bool) -> _RecordingDownload:
    """_download_merge_plan stands in for items() over an explicit track list,
    so it owes the same forwarding."""
    stub = _Stub(delay)
    plan = [(_media(), 1, 1, "i-1"), (_media(), 2, 1, "i-2")]
    with patch.object(backend, "_as_member_of", lambda src, *a: src):
        WavesBridge._download_merge_plan(
            stub,
            stub.dl,
            SimpleNamespace(list_item=_Signal(), track_event=_Signal()),
            Event(),
            SimpleNamespace(id="a-1"),
            "{title}",
            plan,
        )
    return stub.dl


def test_the_merge_fanout_forwards_the_delay():
    dl = _run_merge(True)
    assert len(dl.item_calls) == 2
    assert all(c["download_delay"] is True for c in dl.item_calls)


def test_the_merge_fanout_forwards_the_delay_when_it_is_off():
    dl = _run_merge(False)
    assert len(dl.item_calls) == 2
    assert all(c["download_delay"] is False for c in dl.item_calls)


# --- nothing dispatches without it -------------------------------------------


def test_every_engine_dispatch_forwards_the_delay():
    """The audit guard. A new dispatch that forgets the argument silently gets
    the engine's own default, which differs between item() and items(), so the
    setting goes quietly inert again."""
    import inspect
    import re

    source = inspect.getsource(WavesBridge)
    # The three ways a job reaches the engine: a collection, a single item, and
    # the merge fan-out's pool.submit(dl.item, ...).
    dispatches = len(re.findall(r"\bdl\.items?\(|\bdl\.item,", source))
    forwards = len(re.findall(r"download_delay=", source))
    assert dispatches == 3, (
        f"found {dispatches} engine dispatches, expected 3 (collection, single item, merge fan-out). "
        "A new one must forward download_delay, or the setting goes inert on that path."
    )
    assert forwards == dispatches, (
        f"{dispatches} engine dispatches but {forwards} forward download_delay. "
        "item() defaults to False and items() to True, so an unforwarded dispatch "
        "silently ignores the user's setting in one direction or the other."
    )

    # The dispatches live in _start_job (the job body, built when a row's
    # turn comes) and the merge fan-out.
    for name in ("_start_job", "_download_merge_plan"):
        assert "download_delay" in inspect.getsource(getattr(WavesBridge, name)), f"{name} stopped forwarding it"
