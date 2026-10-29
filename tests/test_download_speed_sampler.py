"""The QUEUE button's download speed (bridge._sample_speed).

Qt-free: the sampler is bound onto a bare stub, and the engine's byte counter
and the clock are swapped for ones the test moves. What is pinned: the rate
over the window in megabits a second, a reading of 0 once nothing has
arrived for two seconds, and the timer stopping (and the reading dropping to
0) once no job is in flight. The engine's counter counts what segment
fetches write, from any thread.
"""

from __future__ import annotations

import threading
from collections import deque
from threading import Lock
from types import SimpleNamespace

from conftest import _Signal

from waves import download
from waves.waves_ui import backend


class _Poll:
    def __init__(self) -> None:
        self.active = True

    def stop(self) -> None:
        self.active = False


def _stub(running: bool = True, in_flight: int | None = None):
    b = SimpleNamespace(
        _running_qid=in_flight,
        _download_mbps=0.0,
        _speed_win=deque(),
        _speed_moved=0.0,
        _speed_poll=_Poll(),
        _queue_lock=Lock(),
        _queue=[{"status": "running" if running else "queued"}],
        downloadMbpsChanged=_Signal(),
    )
    b.sample = backend.WavesBridge._sample_speed.__get__(b)
    return b


def _drive(monkeypatch):
    clock = {"t": 100.0, "bytes": 0}
    monkeypatch.setattr(backend.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(backend._waves_download, "bytes_received", lambda: clock["bytes"])
    return clock


def test_the_speed_is_the_rate_over_the_window(monkeypatch):
    clock, b = _drive(monkeypatch), _stub()
    b.sample()
    assert b._download_mbps == 0.0
    for _ in range(4):
        clock["t"] += 0.5
        clock["bytes"] += 1_250_000  # 10 Mb in half a second
        b.sample()
    assert b._download_mbps == 20.0
    assert b.downloadMbpsChanged.emits


def test_the_speed_reads_nothing_once_the_stream_stops(monkeypatch):
    clock, b = _drive(monkeypatch), _stub()
    b.sample()
    clock["t"] += 0.5
    clock["bytes"] += 1_000_000
    b.sample()
    assert b._download_mbps > 0
    clock["t"] += 1.5
    b.sample()
    assert b._download_mbps > 0, "a short gap between segments still reads the window's rate"
    clock["t"] += 1.0
    b.sample()
    assert b._download_mbps == 0.0


def test_the_timer_stops_once_nothing_runs(monkeypatch):
    clock, b = _drive(monkeypatch), _stub()
    b.sample()
    clock["t"] += 0.5
    clock["bytes"] += 1_000_000
    b.sample()
    assert b._download_mbps > 0
    b._queue[0]["status"] = "done"
    clock["t"] += 0.5
    b.sample()
    assert b._download_mbps == 0.0
    assert not b._speed_poll.active
    assert not b._speed_win


def test_the_timer_survives_a_job_whose_row_is_still_queued(monkeypatch):
    """The job is handed its worker before its row turns running (the folder
    gate, a mix's fill). A first tick in that gap must not stop the timer, or
    the chart reads 0 for the whole job."""
    clock, b = _drive(monkeypatch), _stub(running=False, in_flight=7)
    b.sample()
    assert b._speed_poll.active
    b._queue[0]["status"] = "running"
    clock["t"] += 0.5
    clock["bytes"] += 1_000_000
    b.sample()
    assert b._download_mbps > 0
    b._running_qid = None
    b._queue[0]["status"] = "done"
    clock["t"] += 0.5
    b.sample()
    assert not b._speed_poll.active


def test_the_engine_counts_bytes_from_every_thread():
    before = download.bytes_received()
    threads = [threading.Thread(target=lambda: [download._count_bytes(3) for _ in range(1000)]) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert download.bytes_received() - before == 24_000
