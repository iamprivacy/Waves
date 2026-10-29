"""The queue's missing-tracks export (issue #54).

WHAT THIS FENCES OFF
--------------------
A long playlist can finish with a handful of tracks that failed or that TIDAL
no longer carries, and since issue #35 an unavailable track does not even
redden the row: the expanded ledger was the only place naming them, and past
500 tracks it could not show them at all. The export writes them to a CSV so
they can be looked up elsewhere.

Pinned here: what counts as missing (failed and unavailable, never a stop),
the order (the collection's own), what each line names (artist, album, ISRC,
a TIDAL link, which the track events now carry so a row nobody expanded can
still be exported), the file itself (BOM, header, a formula never left live,
one export never replacing another), the drawer-wide export including a failed
single that has no track registry, the live count the footer button reads,
and the factory wipe knowing the file's name.
"""

from __future__ import annotations

import csv
import os
from threading import Lock
from types import SimpleNamespace

from waves import missing_tracks
from waves.waves_ui import backend
from waves.waves_ui.backend import WavesBridge


class _Signal:
    def __init__(self):
        self.calls = []

    def emit(self, *a):
        self.calls.append(a)


class _Stub:
    """Enough bridge for the registry, the count and the export: two queue
    rows (a playlist, qid 1, and a failed single, qid 2)."""

    def __init__(self, folder: str):
        self._queue = [
            {"qid": 1, "media_id": "p1", "status": "done", "type": "playlist", "collection": True, "name": "Road Trip"},
            {
                "qid": 2,
                "media_id": "555",
                "status": "failed",
                "type": "track",
                "collection": False,
                "name": "Lone Song",
                "artist": "Solo Act",
            },
        ]
        self._queue_index = {it["qid"]: it for it in self._queue}
        self._queue_lock = Lock()
        self._qdirty_changed: dict = {}
        self._job_tracks: dict = {}
        self._job_owned: dict = {}
        self._job_fetched: dict = {}
        self._job_objs: dict = {}
        self._job_signals: dict = {}
        self._missing_by_qid: dict = {}
        self._factory_reset = False
        self.queueTrackState = _Signal()
        self.missingTracksChanged = _Signal()
        self.missingTracksExported = _Signal()
        self._missingTracksWritten = _Signal()
        self._emit_queue = lambda: None
        self._own_pool = SimpleNamespace(start=lambda w: None)
        self.threadpool = SimpleNamespace(start=lambda w: w.run())
        self.settings = SimpleNamespace(
            file_path=os.path.join(folder, "settings.json"), data=SimpleNamespace(download_base_path="")
        )
        for name in (
            "_track_lifecycle",
            "_queue_item",
            "_queue_mark_changed",
            "_prune_job_tracks",
            "_missing_rows",
            "exportMissingTracks",
        ):
            setattr(self, name, getattr(WavesBridge, name).__get__(self, _Stub))

    def _emit_from_worker(self, name, *args):
        getattr(self, name).emit(*args)


def _ev(tid, status, pos, **kw):
    return {
        "id": tid,
        "title": f"Song {tid}",
        "num": 1,
        "pos": pos,
        "duration": "3:00",
        "artist": f"Artist {tid}",
        "album": f"Album {tid}",
        "isrc": f"USX{tid}",
        "kind": "track",
        "status": status,
        **kw,
    }


def _run_playlist(b):
    for tid, pos in (("103", 3), ("101", 1), ("102", 2), ("104", 4)):
        b._track_lifecycle(1, _ev(tid, "running", pos))
    b._track_lifecycle(1, _ev("103", "failed", 3))
    b._track_lifecycle(1, _ev("101", "unavailable", 1))
    b._track_lifecycle(1, _ev("102", "done", 2))
    b._track_lifecycle(1, _ev("104", "cancelled", 4))


def test_only_failed_and_unavailable_are_missing_in_collection_order(tmp_path):
    b = _Stub(str(tmp_path))
    _run_playlist(b)
    lines, sources = b._missing_rows(1)
    assert [(r["id"], r["status"], r["num"]) for r in lines] == [("101", "unavailable", 1), ("103", "failed", 3)]
    assert sources == 1
    assert all(r["collection"] == "Road Trip" for r in lines)


def test_the_count_follows_statuses_and_leaves_with_the_row(tmp_path):
    b = _Stub(str(tmp_path))
    _run_playlist(b)
    assert b._missing_by_qid == {1: 2}
    # A retried track that lands stops being missing.
    b._track_lifecycle(1, _ev("103", "running", 3))
    assert b._missing_by_qid == {1: 1}
    n = len(b.missingTracksChanged.calls)
    b._prune_job_tracks([1])
    assert b._missing_by_qid == {}
    assert len(b.missingTracksChanged.calls) == n + 1


def test_export_writes_a_csv_with_every_field(tmp_path):
    b = _Stub(str(tmp_path))
    _run_playlist(b)
    b.exportMissingTracks(1)
    qid, count, path = b._missingTracksWritten.calls[-1]
    assert (qid, count) == (1, 2)
    assert os.path.dirname(path) == os.path.join(str(tmp_path), backend.REPORTS_DIR)
    assert missing_tracks.NAME_RE.match(os.path.basename(path))
    with open(path, "rb") as f:
        raw = f.read()
    assert raw.startswith(b"\xef\xbb\xbf")
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == list(missing_tracks.COLUMNS)
    assert rows[1] == [
        "Not available on TIDAL",
        "Artist 101",
        "Song 101",
        "Album 101",
        "Road Trip",
        "1",
        "3:00",
        "USX101",
        "101",
        "https://tidal.com/browse/track/101",
    ]
    assert rows[2][0] == "Failed"


def test_the_whole_queue_includes_a_failed_single(tmp_path):
    b = _Stub(str(tmp_path))
    _run_playlist(b)
    lines, sources = b._missing_rows(-1)
    assert sources == 2
    single = lines[-1]
    assert (single["title"], single["artist"], single["id"], single["collection"]) == (
        "Lone Song",
        "Solo Act",
        "555",
        "",
    )


def test_nothing_to_export_writes_nothing(tmp_path):
    b = _Stub(str(tmp_path))
    b.exportMissingTracks(1)
    assert b.missingTracksExported.calls == [(1, 0, "")]
    assert not os.path.exists(os.path.join(str(tmp_path), backend.REPORTS_DIR))


def test_no_export_during_a_factory_reset(tmp_path):
    b = _Stub(str(tmp_path))
    _run_playlist(b)
    b._factory_reset = True
    b.exportMissingTracks(1)
    assert b.missingTracksExported.calls == [(1, 2, "")]


def test_a_formula_is_never_left_live_and_links_name_the_kind():
    cells = missing_tracks.row_cells(
        {"status": "failed", "artist": "=HYPERLINK(1)", "title": "+x", "album": "-a", "id": "77", "kind": "video"}
    )
    assert cells[1:4] == ["'=HYPERLINK(1)", "'+x", "'-a"]
    assert cells[-1] == "https://tidal.com/browse/video/77"
    assert missing_tracks.tidal_link("plan-3") == ""


def test_one_export_never_replaces_another(tmp_path):
    first = missing_tracks.write_new_report(str(tmp_path), [{"status": "failed", "id": "1"}], when=1_700_000_000)
    second = missing_tracks.write_new_report(str(tmp_path), [{"status": "failed", "id": "2"}], when=1_700_000_000)
    assert first != second and second.endswith("-2.csv")
    assert all(missing_tracks.NAME_RE.match(os.path.basename(p)) for p in (first, second))


def test_the_factory_wipe_knows_the_report_name():
    patterns = next(p for rel, _f, p in backend._FACTORY_WIPE_SUBDIRS if rel == backend.REPORTS_DIR)
    assert any(pat.match("missing-tracks-20261014-101500.csv") for pat in patterns)
    assert any(pat.match("missing-tracks-20261014-101500-2.csv") for pat in patterns)
    for foreign in (
        "missing-tracks-mine.csv",
        "missing-tracks-20261014-101500.csv.bak",
        "missing-tracks-20261014-101500-1.csv",
        "missing-tracks-20261014-101500-1000.csv",
        "my-missing-tracks-20261014-101500.csv",
    ):
        assert not any(pat.match(foreign) for pat in patterns), foreign


def test_track_events_carry_the_export_fields():
    album = SimpleNamespace(name="Some Album")
    media = SimpleNamespace(id=9, isrc=" usabc1 ", album=album, artists=[], artist=None, name="t")
    detail = backend._track_detail(media)
    assert detail["album"] == "Some Album" and detail["isrc"] == "USABC1" and detail["kind"] == "track"
    # An album-less playlist track still describes itself, it never raises.
    assert backend._track_detail(SimpleNamespace(id=1, album=None))["album"] == ""


def test_a_merge_seeded_row_learns_the_fields_from_its_events(tmp_path):
    b = _Stub(str(tmp_path))
    b._job_tracks[1] = {"7": {"id": "7", "title": "Seeded", "num": 1, "status": "pending", "pct": 0.0}}
    b._track_lifecycle(1, _ev("7", "running", 1))
    b._track_lifecycle(1, {"id": "7", "status": "failed"})
    row = b._job_tracks[1]["7"]
    assert (row["artist"], row["album"], row["isrc"], row["status"]) == ("Artist 7", "Album 7", "USX7", "failed")


def test_a_failed_merge_whose_seeded_tracks_never_started_is_still_named(tmp_path):
    """A best-of-both merge row is seeded with every track pending, so a job
    that fails before its first track has a registry and still no missing
    track: the release itself is the line, never left out of the file."""
    b = _Stub(str(tmp_path))
    b._queue[0]["status"] = "failed"
    b._job_tracks[1] = backend._seed_merge_registry(
        [(SimpleNamespace(id=11, name="One", duration=60, album=None), 1, 1, None)]
    )
    lines, sources = b._missing_rows(1)
    assert sources == 1
    assert [(r["status"], r["collection"], r["title"]) for r in lines] == [("failed", "Road Trip", "")]


def test_a_merge_row_numbers_by_plan_position_not_disc_track(tmp_path):
    """Disc 1 track 3 and disc 2 track 3 of a merge both fail: they must
    stay apart, in plan order, under the plan's own positions."""
    b = _Stub(str(tmp_path))
    plan = [
        (SimpleNamespace(id=100 + i, name=f"t{i}", duration=60, album=None), (i % 3) + 1, i // 3 + 1, None)
        for i in range(6)
    ]
    b._job_tracks[1] = backend._seed_merge_registry(plan)
    for tid in ("105", "102"):
        b._track_lifecycle(1, {"id": tid, "status": "running"})
        b._track_lifecycle(1, {"id": tid, "status": "failed"})
    lines, _ = b._missing_rows(1)
    assert [(r["id"], r["num"]) for r in lines] == [("102", 3), ("105", 6)]
