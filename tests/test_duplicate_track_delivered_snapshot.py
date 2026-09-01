"""A collection may list the same track twice, and both copies run at once.

TIDAL allows a playlist to carry one track at two positions. With two or more
workers both occurrences are in flight together, and the delivered-quality
snapshot each one captures in _get_track_stream_info used to be filed under the
bare track id: one slot for two items.

Worker A writes its file and only claims its snapshot in item()'s epilogue,
after the deliberate inter-download delay. In that window worker B's
post-stream existing-file check finds A's file and drops "the" snapshot, which
by then is B's own capture sitting on top of A's. A's epilogue then finds
nothing, its done event carries no quality, and _record_ownership writes no
row: the track that really landed never enters the ledger, so it never reads
owned and every later run fetches the stream again just to skip it again.

The snapshot is now keyed by the worker thread as well as the track id.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

from tidalapi.media import Track

from waves import download as download_mod
from waves.waves_ui.backend import _TrackedDownload


def _track(tid="101"):
    t = Track.__new__(Track)
    t.id = tid
    t.media_metadata_tags = ["LOSSLESS"]
    return t


def _dl(monkeypatch, quality="LOSSLESS"):
    dl = _TrackedDownload.__new__(_TrackedDownload)
    dl._pinned_quality = None
    dl._target_rank = 3
    dl._delivered = {}
    dl._delivered_lock = threading.Lock()
    dl.settings = SimpleNamespace(data=SimpleNamespace(download_dolby_atmos=False))
    info = SimpleNamespace(
        media_stream=SimpleNamespace(audio_quality=quality, audio_mode="STEREO", bit_depth=16, sample_rate=44100),
        stream_manifest=SimpleNamespace(codecs="flac"),
    )
    monkeypatch.setattr(download_mod.Download, "_get_track_stream_info", lambda self, media: info)
    return dl


def test_a_twins_post_stream_skip_leaves_the_writers_snapshot_alone(monkeypatch):
    dl = _dl(monkeypatch)
    track = _track()
    captured: dict[str, object] = {}
    b_done = threading.Event()

    def worker_a() -> None:
        dl._get_track_stream_info(track)  # the copy that really writes
        b_done.wait(10)  # ...and sits in the politeness delay meanwhile
        captured["a"] = dl._delivered.pop(dl._delivered_key(track), None)

    def worker_b() -> None:
        dl._get_track_stream_info(track)
        dl._note_skipped_after_stream(track)  # A's file is already there
        captured["b"] = dl._delivered.pop(dl._delivered_key(track), None)
        b_done.set()

    a = threading.Thread(target=worker_a)
    b = threading.Thread(target=worker_b)
    a.start()
    b.start()
    a.join(15)
    b.join(15)

    assert captured["a"] is not None, "the writing worker lost its delivered quality to its twin"
    assert captured["a"]["tier"] == "LOSSLESS"
    assert captured["b"] is None, "the skipping worker must still drop its own snapshot"
    assert dl._delivered == {}, f"snapshots left behind: {dl._delivered}"


def test_two_workers_keep_separate_snapshots(monkeypatch):
    """The same id on two threads is two slots, and neither reads the other."""
    dl = _dl(monkeypatch)
    track = _track()
    keys: list[tuple] = []
    lock = threading.Lock()
    # All three really in flight at once: a thread that has already exited can
    # have its id handed to the next one, which is fine in the pool (every item
    # claims its own slot before it returns) but would make this prove nothing.
    together = threading.Barrier(3, timeout=15)

    def worker() -> None:
        dl._get_track_stream_info(track)
        with lock:
            keys.append(dl._delivered_key(track))
        together.wait()

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)

    assert len(set(keys)) == 3
    assert len(dl._delivered) == 3


def test_one_worker_reusing_a_thread_reuses_its_slot(monkeypatch):
    """The pool hands a thread out again: the key must not accumulate rows for
    every track a worker has ever run."""
    dl = _dl(monkeypatch)
    for tid in ("101", "102", "103"):
        t = _track(tid)
        dl._get_track_stream_info(t)
        assert dl._delivered.pop(dl._delivered_key(t)) is not None
    assert dl._delivered == {}
