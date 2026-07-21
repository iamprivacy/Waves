"""Regression guard for the false-"done" download bug.

THE BUG
-------
The download engine (``Download.item``) returns ``(False, path)`` WITHOUT raising
when it cannot fetch a stream URL, e.g. an unentitled/free TIDAL account whose
playback requests are rejected. The job worker used to discard that return and
emit ``downloadState(id, "done")`` whenever no exception propagated, so the UI
button flipped to a green DONE/check even though nothing was written.

THE FIX rests on ``_TrackedDownload`` tallying only tracks that actually wrote a
file (``ok_count``) and re-emitting the per-track ``failed`` status, so the job
worker can tell a silent failure from success. These tests pin that mechanism:
a ``(False, ...)`` item must NOT count as a success and must report ``failed``.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from tidaler.download import Download
from tidaler.waves_ui import backend
from tidaler.waves_ui.backend import _TrackedDownload


def _make_tracked() -> tuple[_TrackedDownload, MagicMock]:
    relay = MagicMock()  # stands in for _ProgressSignals (track_event.emit recorded)
    dl = _TrackedDownload(
        tidal_obj=MagicMock(),
        skip_existing=False,
        path_base="./tmp",
        fn_logger=MagicMock(),
        progress=MagicMock(),
        track_signals=relay,
    )
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()
    return dl, relay


def _media() -> MagicMock:
    m = MagicMock()
    m.id = "123"
    m.track_num = 1
    m.volume_num = 1
    m.duration = 100  # _fmt_duration does int(...) on this
    return m


def _statuses(relay: MagicMock) -> list[str]:
    return [call.args[0]["status"] for call in relay.track_event.emit.call_args_list]


@pytest.fixture(autouse=True)
def _stub_name_builders():
    with (
        patch.object(backend, "name_builder_item", return_value="Item Name"),
        patch.object(backend, "name_builder_title", return_value="Title"),
    ):
        yield


def test_written_track_counts_and_reports_done():
    dl, relay = _make_tracked()
    with patch.object(Download, "item", return_value=(True, "/tmp/song.flac")):
        ok, _ = dl.item(media=_media())
    assert ok is True
    assert dl.ok_count == 1
    assert _statuses(relay) == ["running", "done"]


def test_silent_failure_does_not_count_and_reports_failed():
    """The bug trigger: engine returns (False, path) WITHOUT raising."""
    dl, relay = _make_tracked()
    with patch.object(Download, "item", return_value=(False, "/tmp/song.flac")):
        ok, _ = dl.item(media=_media())
    assert ok is False
    assert dl.ok_count == 0  # must NOT be mistaken for a success
    assert _statuses(relay) == ["running", "failed"]


def test_raising_track_reports_failed_and_propagates():
    dl, relay = _make_tracked()
    with patch.object(Download, "item", side_effect=RuntimeError("boom")), pytest.raises(RuntimeError):
        dl.item(media=_media())
    assert dl.ok_count == 0
    assert _statuses(relay) == ["running", "failed"]


def test_aborted_track_is_cancelled_not_counted():
    dl, relay = _make_tracked()
    dl.event_abort.set()
    with patch.object(Download, "item", return_value=(False, "/tmp/song.flac")):
        ok, _ = dl.item(media=_media())
    assert ok is False
    assert dl.ok_count == 0
    assert _statuses(relay) == ["running", "cancelled"]


def test_ok_count_accumulates_across_tracks():
    """items() fans item() out on a pool; a wholly-failed album tallies zero."""
    dl, _ = _make_tracked()
    outcomes = [(True, "/a.flac"), (False, "/b.flac"), (True, "/c.flac")]
    with patch.object(Download, "item", side_effect=outcomes):
        for _ in outcomes:
            dl.item(media=_media())
    assert dl.ok_count == 2  # only the two that wrote a file


# --- write_count vs ownership skips (the silent-DONE guard's real signal) ----
# The job worker raises incomplete when write_count == 0 and
# (fail_count > 0 or ok_count == 0): owned-track skips fill ok_count but must
# never mask a collection whose NEW tracks all failed silently.


def _incomplete_predicate(dl) -> bool:
    """The exact collection-level guard the job worker applies after items()."""
    return dl.write_count == 0 and (dl.fail_count > 0 or dl.ok_count == 0)


def test_ownership_skips_do_not_mask_silent_failures():
    # A partially owned album on an account whose new-track streams are all
    # rejected: the skips are handled work, but nothing was written, so the
    # job must be reported incomplete, not a green done.
    dl, _relay = _make_tracked()
    dl._emit_skip(_media())  # the owned track
    with patch.object(Download, "item", return_value=(False, "")):
        ok, _ = dl.item(media=_media())  # the new track, silently rejected
    assert ok is False
    assert dl.ok_count == 1  # the skip still counts as handled progress
    assert dl.write_count == 0  # but no file was produced this run
    assert dl.fail_count == 1
    assert _incomplete_predicate(dl) is True


def test_all_owned_collection_stays_a_clean_success():
    dl, _relay = _make_tracked()
    dl._emit_skip(_media())
    dl._emit_skip(_media())
    assert (dl.ok_count, dl.write_count, dl.skip_count, dl.fail_count) == (2, 0, 2, 0)
    assert _incomplete_predicate(dl) is False


def test_empty_collection_is_still_incomplete():
    dl, _relay = _make_tracked()
    assert _incomplete_predicate(dl) is True


def test_a_single_real_write_keeps_the_job_green():
    dl, _relay = _make_tracked()
    dl._emit_skip(_media())
    with patch.object(Download, "item", return_value=(False, "")):
        dl.item(media=_media())
    with patch.object(Download, "item", return_value=(True, "/tmp/song.flac")):
        dl.item(media=_media())
    assert dl.write_count == 1
    assert _incomplete_predicate(dl) is False
