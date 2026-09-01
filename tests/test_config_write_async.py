"""Config saves leave the GUI thread; snapshots win; quit still flushes.

THE COST THIS FENCES OFF
------------------------
waves.json and settings.json are written atomically WITH AN FSYNC, and both
were written synchronously from GUI-thread slots (every pref flip, every
debounced window-geometry save): the GUI paid a disk sync per save. Saves
now snapshot their payload on the calling thread (microseconds) and hand the
fsync-bearing disk work to ``_SingleFlightWriter``, one background thread
where consecutive submits per file coalesce to the newest snapshot.

Pinned here: the fsync happens off the submitting thread; a burst of submits
for one file executes first-plus-newest (latest snapshot wins, the same end
state the old synchronous ordering produced); ``flush`` (the shutdown hook)
gets everything pending onto disk, inline if the thread cannot finish in
time; ``_save_waves_prefs`` snapshots at submit time so later mutations
cannot leak into an earlier save; and ``_submit_settings_write`` serializes
before it returns, which is what keeps the transient ffmpeg re-injection
that follows it out of the file.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import ClassVar

from waves.waves_ui.backend import WavesBridge, _SingleFlightWriter


def test_a_submit_burst_runs_first_plus_newest():
    w = _SingleFlightWriter()
    gate = threading.Event()
    ran: list[str] = []

    def first():
        ran.append("first")
        gate.wait(5)

    w.submit("k", first)
    # Give the thread a beat to pick "first" up, then pile on while it blocks.
    for _ in range(100):
        if ran:
            break
        time.sleep(0.01)
    w.submit("k", lambda: ran.append("stale-1"))
    w.submit("k", lambda: ran.append("stale-2"))
    w.submit("k", lambda: ran.append("newest"))
    gate.set()
    w.flush()
    assert ran == ["first", "newest"], "coalescing must drop the overwritten middle submits"


def test_flush_runs_leftovers_inline_when_the_thread_cannot_finish():
    w = _SingleFlightWriter()
    gate = threading.Event()
    ran: list[str] = []
    w.submit("slow", lambda: (ran.append("slow"), gate.wait(5)))
    for _ in range(100):
        if ran:
            break
        time.sleep(0.01)
    w.submit("other", lambda: ran.append("other"))
    t0 = time.monotonic()
    w.flush(timeout=0.2)
    assert time.monotonic() - t0 < 2.0
    assert "other" in ran, "a pending write was lost because the thread was wedged"
    gate.set()


def test_prefs_save_fsyncs_off_the_calling_thread_and_snapshots(tmp_path, monkeypatch):
    fsync_threads: list[int] = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (fsync_threads.append(threading.get_ident()), real_fsync(fd))[1])

    stub = type("_S", (), {})()
    stub._factory_reset = False
    stub._waves_prefs = {"motion_background": True}
    stub._waves_prefs_path = str(tmp_path / "waves.json")
    stub._config_writer = _SingleFlightWriter()
    WavesBridge._save_waves_prefs(stub)
    # Mutating AFTER the save must not reach this save's file content.
    stub._waves_prefs["motion_background"] = False
    stub._config_writer.flush()

    with open(stub._waves_prefs_path) as f:
        on_disk = json.load(f)
    assert on_disk == {"motion_background": True}, "the save wrote live state, not its snapshot"
    assert fsync_threads, "the atomic write no longer fsyncs"
    assert threading.get_ident() not in fsync_threads, "the fsync ran on the calling thread"


def test_settings_write_serializes_before_returning():
    stub = type("_S", (), {})()

    class _Cfg:
        captured: ClassVar[list[str]] = []

        class data:
            @staticmethod
            def to_json():
                return json.dumps({"path_binary_ffmpeg": _Cfg.live_path})

        live_path = ""

        def write_serialized(self, data_json):
            _Cfg.captured.append(data_json)

    stub.settings = _Cfg()
    stub._config_writer = _SingleFlightWriter()

    WavesBridge._submit_settings_write(stub)
    # The re-injection that follows a save in the app: it must not be able to
    # reach the already-serialized snapshot.
    _Cfg.live_path = "/managed/ffmpeg"
    stub._config_writer.flush()

    assert len(_Cfg.captured) == 1
    assert json.loads(_Cfg.captured[0]) == {"path_binary_ffmpeg": ""}


def test_shutdown_flush_lands_a_last_moment_pref(tmp_path):
    stub = type("_S", (), {})()
    stub._factory_reset = False
    stub._waves_prefs = {"volume": 11}
    stub._waves_prefs_path = str(tmp_path / "waves.json")
    stub._config_writer = _SingleFlightWriter()
    WavesBridge._save_waves_prefs(stub)
    stub._config_writer.flush()
    with open(stub._waves_prefs_path) as f:
        assert json.load(f) == {"volume": 11}
