"""Guards for the verbose event-loop occupancy probe (diagnostics perf sampler).

The probe measures GUI-thread saturation by how late a fixed-interval timer fires,
and the sampler reports it as a coarse bucket, never a raw number tied to an
action, so a user's log can disambiguate "GUI thread starved" from "downlink
saturated" without leaking any identity, path, or content.
"""

from __future__ import annotations

import logging

from tidaler.waves_ui import diagnostics as D


def test_occupancy_buckets():
    assert D._occ_bucket(0.0) == "<10%"
    assert D._occ_bucket(0.099) == "<10%"
    assert D._occ_bucket(0.1) == "10-30%"
    assert D._occ_bucket(0.29) == "10-30%"
    assert D._occ_bucket(0.3) == "30-60%"
    assert D._occ_bucket(0.59) == "30-60%"
    assert D._occ_bucket(0.6) == "60-90%"
    assert D._occ_bucket(0.89) == "60-90%"
    assert D._occ_bucket(0.9) == ">90%"
    assert D._occ_bucket(1.0) == ">90%"


def test_stall_buckets():
    assert D._stall_bucket(0.0) == "<0.1s"
    assert D._stall_bucket(0.09) == "<0.1s"
    assert D._stall_bucket(0.1) == "0.1-0.25s"
    assert D._stall_bucket(0.25) == "0.25-0.5s"
    assert D._stall_bucket(0.5) == "0.5-1s"
    assert D._stall_bucket(1.0) == ">1s"
    assert D._stall_bucket(9.9) == ">1s"


def test_probe_tick_accumulates_overrun_only(monkeypatch):
    # A tick that fires exactly on schedule adds no busy time; one that fires
    # late adds only the overrun beyond the scheduled interval.
    ps = D._PerfSampler()
    interval = D._PROBE_INTERVAL_MS / 1000.0
    now = {"t": 100.0}
    monkeypatch.setattr(D.time, "monotonic", lambda: now["t"])
    ps._probe_last = now["t"]

    now["t"] += interval  # on time
    ps._probe_tick()
    assert ps._probe_busy == 0.0
    assert abs(ps._probe_wall - interval) < 1e-9

    now["t"] += interval + 0.2  # 0.2s late
    ps._probe_tick()
    assert abs(ps._probe_busy - 0.2) < 1e-9
    assert abs(ps._probe_max_stall - 0.2) < 1e-9


def test_sample_reports_occupancy_and_resets():
    cap: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda rec: cap.append(rec.getMessage())
    old_level = D.logger.level
    D.logger.addHandler(handler)
    D.logger.setLevel(logging.DEBUG)
    try:
        ps = D._PerfSampler()
        ps._probe_wall = 2.0
        ps._probe_busy = 1.2  # 60% occupancy
        ps._probe_max_stall = 0.6
        ps._sample()
        assert any("uiloop_busy=60-90%" in c and "uiloop_maxstall=0.5-1s" in c for c in cap)
        # Window drained so the next interval starts clean.
        assert ps._probe_wall == 0.0
        assert ps._probe_busy == 0.0
        assert ps._probe_max_stall == 0.0
    finally:
        D.logger.removeHandler(handler)
        D.logger.setLevel(old_level)
