"""The album/playlist roll-up bar folds in-flight track fractions in
(_bump_group_progress): the bar creeps between track completions instead of
jumping once per finished track, only ever moves forward, and stays within
0..100.

Same hermetic pattern as test_audit_backend.py: the real, unbound methods are
bound onto a minimal stub so no Qt app or network session is needed.
"""

from __future__ import annotations

from tidaler.waves_ui.backend import WavesBridge


class _Signal:
    def __init__(self):
        self.emits: list = []

    def emit(self, *args):
        self.emits.append(args if len(args) != 1 else args[0])


class _Stub:
    """Carries exactly what _bump_group_progress and its callees touch."""

    _queue_item = WavesBridge._queue_item
    _set_queue_progress = WavesBridge._set_queue_progress
    _bump_group_progress = WavesBridge._bump_group_progress

    def __init__(self, item: dict):
        self._queue = [item]
        self.queueItemProgress = _Signal()
        self.reported: list = []

    # Recorder for the media-id fan-out path (real _report_pct also feeds the
    # media buttons and artist groups, which need more of the bridge).
    def _report_pct(self, media_id, qid, pct):
        self.reported.append((media_id, qid, pct))


def _item(**over):
    base = {
        "qid": 7,
        "collection": True,
        "tracks": 4,
        "progress": 0.0,
        "media_id": "alb1",
    }
    base.update(over)
    return base


def _reg(*rows):
    return {str(i): r for i, r in enumerate(rows)}


def test_inflight_fraction_creeps_the_bar():
    stub = _Stub(_item(progress=50.0))
    reg = _reg(
        {"status": "done", "pct": 100.0},
        {"status": "done", "pct": 100.0},
        {"status": "running", "pct": 50.0},
    )
    stub._bump_group_progress(7, reg)
    # (2 consumed * 100 + 50 running) / 4 tracks = 62.5
    assert stub.reported == [("alb1", 7, 62.5)]


def test_never_moves_backwards():
    stub = _Stub(_item(progress=70.0))
    reg = _reg({"status": "done", "pct": 100.0}, {"status": "running", "pct": 40.0})
    stub._bump_group_progress(7, reg)  # smooth would be 35.0
    assert stub.reported == []


def test_failed_and_cancelled_count_as_consumed():
    # Matches the completion counter: every processed future advances the bar.
    stub = _Stub(_item(progress=0.0))
    reg = _reg(
        {"status": "failed", "pct": 30.0},
        {"status": "cancelled", "pct": 0.0},
        {"status": "running", "pct": 50.0},
    )
    stub._bump_group_progress(7, reg)
    assert stub.reported == [("alb1", 7, 62.5)]


def test_capped_at_100():
    stub = _Stub(_item(tracks=1, progress=0.0))
    reg = _reg({"status": "done", "pct": 100.0}, {"status": "running", "pct": 80.0})
    stub._bump_group_progress(7, reg)
    assert stub.reported == [("alb1", 7, 100.0)]


def test_non_collection_and_unknown_total_are_ignored():
    for item in (_item(collection=False), _item(tracks=0)):
        stub = _Stub(item)
        stub._bump_group_progress(7, _reg({"status": "running", "pct": 50.0}))
        assert stub.reported == []
        assert stub.queueItemProgress.emits == []


def test_without_media_id_updates_only_the_queue_row():
    stub = _Stub(_item(media_id="", progress=0.0))
    reg = _reg({"status": "running", "pct": 100.0})
    stub._bump_group_progress(7, reg)
    assert stub.reported == []
    assert stub.queueItemProgress.emits == [(7, 25.0)]
    assert stub._queue[0]["progress"] == 25.0
