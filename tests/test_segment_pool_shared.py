"""One segment executor per download job, sized to the connection pool.

THE COST THIS FENCES OFF
------------------------
``_download_segments`` built a fresh ThreadPoolExecutor PER TRACK (executor
churn: thread spawn and teardown for every track of every album), and with
``downloads_concurrent_max`` items in flight the per-track clamp still
allowed items x clamp threads against ``_HTTP_POOL_MAXSIZE`` pooled sockets
(pool_block=True), so most of those threads only ever parked in the
connection pool's queue. The engine now keeps ONE executor per Download
(one instance = one queued job), built lazily at the same clamp, shared by
every concurrent item, and shut down explicitly when the job ends
(close_segment_pool, called from the bridge's job-finally). Segment work
reports into SEGMENT_GAUGE so the verbose perf sampler sees saturation.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from waves.download import SEGMENT_GAUGE, Download


def _engine(per_track_max=20):
    d = Download.__new__(Download)
    d.settings = SimpleNamespace(data=SimpleNamespace(downloads_simultaneous_per_track_max=per_track_max))
    d._segment_executor = None
    d._segment_executor_lock = threading.Lock()
    return d


def test_one_executor_serves_the_whole_job():
    d = _engine()
    try:
        first = d._segment_pool()
        assert d._segment_pool() is first, "a second track built a second executor"
    finally:
        d.close_segment_pool()


def test_the_pool_is_clamped_to_the_connection_pool():
    d = _engine(per_track_max=20)
    try:
        assert d._segment_pool()._max_workers == Download._HTTP_POOL_MAXSIZE
    finally:
        d.close_segment_pool()
    d2 = _engine(per_track_max=3)
    try:
        assert d2._segment_pool()._max_workers == 3
    finally:
        d2.close_segment_pool()


def test_close_shuts_down_and_a_later_ask_rebuilds():
    d = _engine()
    first = d._segment_pool()
    d.close_segment_pool()
    assert first._shutdown, "close_segment_pool left the executor running"
    d.close_segment_pool()  # idempotent: the job-finally may run after an abort already closed it
    fresh = d._segment_pool()
    try:
        assert fresh is not first
        assert not fresh._shutdown
    finally:
        d.close_segment_pool()


def test_segment_gauge_counts_in_flight_work():
    d = _engine(per_track_max=4)
    gate = threading.Event()
    started = threading.Event()

    def job():
        with SEGMENT_GAUGE.working():
            started.set()
            gate.wait(5)

    before_peak = SEGMENT_GAUGE.peak
    try:
        fut = d._segment_pool().submit(job)
        assert started.wait(5)
        assert SEGMENT_GAUGE.activeThreadCount() >= 1
        gate.set()
        fut.result(timeout=5)
        for _ in range(100):
            if SEGMENT_GAUGE.activeThreadCount() == 0:
                break
            time.sleep(0.01)
        assert SEGMENT_GAUGE.activeThreadCount() == 0
        assert SEGMENT_GAUGE.peak >= max(1, before_peak)
    finally:
        d.close_segment_pool()
