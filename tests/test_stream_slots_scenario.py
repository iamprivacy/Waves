"""A collection's tracks hand their download slot back at stream end.

THE BUG
-------
``_execute_collection_downloads`` ran ``downloads_concurrent_max`` workers and
each ran the whole item pipeline: stream, convert, tag, copy onto the library
volume, politeness sleep. A track held its slot from first byte to last step,
so once three tracks were copying onto a slow volume (minutes, cross-volume)
nothing streamed: the header chart sat at 0 Mb/s while the row said
"finishing" (installed-app log, 2026-10-30).

THE FIX
-------
The slot counts the STREAM only (waves/download_slots.py). At stream end a
track moves to a finishing seat if one is free and gives its slot back, so
the next track starts streaming while it finishes; the fan-out runs slots
plus seats worth of workers so there is a thread to take that slot. With zero
seats everything behaves as before. These tests drive the real ``item()``
pipeline through the real fan-out with the stream and finish steps faked.
"""

from __future__ import annotations

import threading
import time
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from tidalapi import Track

from waves import download as engine
from waves import download_slots
from waves.download import Download
from waves.download_slots import StreamSlots
from waves.progress import Progress


@pytest.fixture(autouse=True)
def _fresh_ledger(monkeypatch):
    """Each test gets its own ledger (the real one is process-wide) and a
    temp volume with room."""
    ledger = StreamSlots()
    monkeypatch.setattr(engine, "STREAM_SLOTS", ledger)
    monkeypatch.setattr(download_slots, "_temp_free_bytes", lambda: 10 * 1024**3)
    return ledger


def _track(i: int) -> Track:
    media = Track.__new__(Track)
    media.id = str(i)
    media.audio_modes = None
    return media


class _Stage:
    """What the fakes record: the moment each track started streaming and
    the gate each one waits at before its finish is allowed to complete."""

    def __init__(self) -> None:
        self.lock = Lock()
        self.streamed: list[str] = []
        self.finished: list[str] = []
        self.release: dict[str, Event] = {}
        self.streaming = Event()  # set every time a stream starts

    def gate(self, media_id: str) -> Event:
        with self.lock:
            return self.release.setdefault(media_id, Event())

    def streams(self) -> list[str]:
        with self.lock:
            return list(self.streamed)

    def wait_streams(self, n: int, timeout: float = 5.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if len(self.streams()) >= n:
                return True
            self.streaming.wait(0.05)
            self.streaming.clear()
        return len(self.streams()) >= n

    def let_finish(self, *ids: str) -> None:
        for i in ids:
            self.gate(i).set()


def _bare(streams: int, seats: int, tmp_path) -> Download:
    dl = Download.__new__(Download)
    dl.settings = SimpleNamespace(
        data=SimpleNamespace(
            downloads_concurrent_max=streams,
            downloads_finishing_max=seats,
            api_rate_limit_batch_size=0,
            api_rate_limit_delay_sec=0.0,
            download_delay_sec_min=0.0,
            download_delay_sec_max=0.0,
            download_dolby_atmos=False,
        )
    )
    dl.event_abort = Event()
    dl.event_run = Event()
    dl.event_run.set()
    dl.fn_logger = MagicMock()
    dl.progress_gui = None
    dl.skip_existing = False
    dl.path_base = str(tmp_path)
    dl._paced_items = 0
    dl._pace_lock = Lock()
    dl._pace_gate = Event()
    dl._pace_gate.set()
    dl._pace_holds = 0
    dl._pace_until = 0.0
    return dl


def _fakes(stage: _Stage, tmp_path):
    """Patch every step of item() except the slot logic under test."""

    def _paths(self, media, *a, **k):
        return tmp_path / f"{media.id}.flac", ".flac", False, False

    def _actual(self, media, dst, manifest, extract, is_parent, stream, event_stop=None, ticket=None):
        with stage.lock:
            stage.streamed.append(media.id)
        stage.streaming.set()
        time.sleep(0.02)  # the bytes
        if ticket is not None:
            ticket.try_finishing_seat()
        stage.gate(media.id).wait(10)  # the finish: convert, tag, copy
        with stage.lock:
            stage.finished.append(media.id)
        return True, dst

    return (
        patch.object(Download, "_validate_and_prepare_media", side_effect=lambda media, *a, **k: media),
        patch.object(Download, "_prepare_file_paths_and_skip_logic", autospec=True, side_effect=_paths),
        patch.object(Download, "_genre_prefetch", return_value=None),
        patch.object(Download, "_adjust_quality_settings", return_value=(None, None)),
        patch.object(Download, "_get_stream_info", return_value=(object(), ".flac", False, None)),
        patch.object(Download, "_ensure_directory", return_value=None),
        patch.object(Download, "_perform_actual_download", autospec=True, side_effect=_actual),
        patch.object(Download, "_perform_post_processing", return_value=None),
    )


def _run_collection(dl: Download, items: list[Track], out: list) -> threading.Thread:
    progress = Progress()
    task = progress.add_task("list", total=len(items))

    def run():
        out.append(
            Download._execute_collection_downloads(
                dl, items, "{track_title}", None, None, False, True, len(items), progress, task, True
            )
        )

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_the_fourth_stream_starts_while_the_first_three_are_still_finishing(tmp_path):
    """3 slots + 3 seats: six streams start although no track has finished."""
    stage = _Stage()
    dl = _bare(3, 3, tmp_path)
    items = [_track(i) for i in range(1, 8)]
    out: list = []
    with _nested(_fakes(stage, tmp_path)):
        t = _run_collection(dl, items, out)
        assert stage.wait_streams(6), f"only {stage.streams()} streamed"
        assert stage.finished == []
        # The seventh waits: every seat is taken, so tracks 4 to 6 keep their
        # slots through their finish and there is no slot left for it.
        time.sleep(0.3)
        assert len(stage.streams()) == 6
        stage.let_finish(*(str(i) for i in range(1, 8)))
        t.join(10)
        assert not t.is_alive()
    assert out[0] == [tmp_path / f"{i}.flac" for i in range(1, 8)], "results keep the list order"
    assert stage.streams() == [str(i) for i in range(1, 8)], "streams start in list order"


def test_zero_seats_keeps_a_track_in_its_slot_until_it_is_done(tmp_path):
    """The old behavior, by setting: three stream, nothing more until one
    finishes."""
    stage = _Stage()
    dl = _bare(3, 0, tmp_path)
    items = [_track(i) for i in range(1, 7)]
    out: list = []
    with _nested(_fakes(stage, tmp_path)):
        t = _run_collection(dl, items, out)
        assert stage.wait_streams(3)
        time.sleep(0.3)
        assert len(stage.streams()) == 3
        stage.let_finish("1")
        assert stage.wait_streams(4)
        time.sleep(0.2)
        assert len(stage.streams()) == 4
        stage.let_finish("2", "3", "4", "5", "6")
        t.join(10)
        assert not t.is_alive()
    assert out[0] == [tmp_path / f"{i}.flac" for i in range(1, 7)]


def test_a_stop_wakes_a_worker_waiting_for_a_slot(tmp_path):
    stage = _Stage()
    dl = _bare(1, 0, tmp_path)
    items = [_track(1), _track(2)]
    out: list = []
    with _nested(_fakes(stage, tmp_path)):
        t = _run_collection(dl, items, out)
        assert stage.wait_streams(1)
        time.sleep(0.2)
        assert stage.streams() == ["1"], "track 2 waits for the one slot"
        dl.event_abort.set()
        stage.let_finish("1")
        t.join(5)
        assert not t.is_alive(), "the waiter did not wake for the stop"
    assert stage.streams() == ["1"]
    assert out[0] == [tmp_path / "1.flac"]


def test_a_later_jobs_stream_waits_for_the_earlier_jobs_streams_to_start(tmp_path, _fresh_ledger):
    """Two queued items overlapping: job 2's first stream never starts
    before job 1 has reported that all of its streams have started, even
    with slots free."""
    stage = _Stage()
    ledger = _fresh_ledger
    job1 = _bare(3, 3, tmp_path)
    job2 = _bare(3, 3, tmp_path)
    job1._job_token = ledger.job_enter()
    job2._job_token = ledger.job_enter()
    out1: list = []
    out2: list = []
    with _nested(_fakes(stage, tmp_path)):
        t1 = _run_collection(job1, [_track(1), _track(2)], out1)
        assert stage.wait_streams(2)
        t2 = _run_collection(job2, [_track(9)], out2)
        time.sleep(0.3)
        assert stage.streams() == ["1", "2"], "job 2 streamed before job 1 was done starting"
        ledger.job_streams_started(job1._job_token)
        assert stage.wait_streams(3)
        assert stage.streams() == ["1", "2", "9"]
        stage.let_finish("1", "2", "9")
        t1.join(5)
        t2.join(5)
        ledger.job_leave(job1._job_token)
        ledger.job_leave(job2._job_token)
    assert out1[0] == [tmp_path / "1.flac", tmp_path / "2.flac"]
    assert out2[0] == [tmp_path / "9.flac"]


def test_the_cooldown_is_the_slots_and_starts_at_stream_end(tmp_path, _fresh_ledger):
    """With the delay on, the slot a finishing track hands back cools for the
    delay before the next stream, and no worker sleeps: the finish runs on
    through the cooldown."""
    stage = _Stage()
    dl = _bare(1, 1, tmp_path)
    dl.settings.data.download_delay_sec_min = 0.4
    dl.settings.data.download_delay_sec_max = 0.4
    out: list = []
    with _nested(_fakes(stage, tmp_path)):
        progress = Progress()
        task = progress.add_task("list", total=2)
        started = time.monotonic()

        def run():
            out.append(
                Download._execute_collection_downloads(
                    dl, [_track(1), _track(2)], "{track_title}", None, None, True, True, 2, progress, task, True
                )
            )

        t = threading.Thread(target=run, daemon=True)
        t.start()
        assert stage.wait_streams(1)
        for _ in range(100):  # the fake streams for 20 ms before it asks for its seat
            if _fresh_ledger.cooling() == 1:
                break
            time.sleep(0.01)
        assert _fresh_ledger.cooling() == 1, "track 1 handed its slot back at stream end, cooling"
        assert stage.wait_streams(2)
        second_at = time.monotonic() - started
        assert second_at >= 0.4, f"the second stream started after {second_at:.2f}s, inside the cooldown"
        assert stage.finished == [], "the cooldown held no worker: track 1 is still at its finish gate"
        stage.let_finish("1", "2")
        t.join(5)
    assert out[0] == [tmp_path / "1.flac", tmp_path / "2.flac"]


class _nested:
    """Enter several patchers as one context."""

    def __init__(self, patchers) -> None:
        self._patchers = list(patchers)

    def __enter__(self):
        for p in self._patchers:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patchers):
            p.stop()
        return False
