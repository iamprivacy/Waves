"""The queue ledger's FINISHING word: finalize-progress plumbing.

The stream bytes landing is not the work landing: extraction, tagging (with
its lyrics and cover fetches) and the move to the destination all still run
while the track's pct sits at 100, which used to read as a DOWNLOADING word
stuck fully lit. The drawer now hands that word off to FINISHING, filled by
``fpct``: a second progress axis fed from the engine's finalize-step
boundaries (``Download._note_stage``, overridden by ``_TrackedDownload``).

These tests pin the plumbing end to end of the Python side:
  * the tracked download emits the fraction under the row's key (the identity
    id when the track is a merge-plan member, exactly like item() does),
  * the bridge's lifecycle handler stores it on the row,
  * a plain "running" event (a track starting, or RE-starting on retry)
    resets it, so a retry never opens on the previous attempt's full word.
"""

from __future__ import annotations

import pathlib
from threading import Event, Lock, local
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tidalapi.media import Track

from tidaler.download import Download
from tidaler.waves_ui import backend


def _tracked(relay):
    """A real _TrackedDownload without Download.__init__ (needs a session)."""
    td = backend._TrackedDownload.__new__(backend._TrackedDownload)
    td._track_signals = relay
    return td


def _media(mid="123", identity=None):
    m = MagicMock()
    m.id = mid
    m.waves_identity_id = identity
    return m


class _Signal:
    def __init__(self):
        self.calls = []

    def emit(self, *a):
        self.calls.append(a)


class _LifecycleStub:
    """Just enough bridge for _track_lifecycle: a track registry and a queue
    that answers None (an event for a cleared row is a real shape, and the
    rollup then no-ops)."""

    def __init__(self):
        self._job_tracks = {}
        self._job_signals = {}
        self._queue_index = {}
        self._outcome_lock = Lock()
        self.queueTrackState = _Signal()
        self._emit_queue = lambda: None
        for name in ("_track_lifecycle", "_queue_item"):
            setattr(self, name, getattr(backend.WavesBridge, name).__get__(self, _LifecycleStub))


def test_note_stage_emits_running_with_fraction():
    relay = MagicMock()
    _tracked(relay)._note_stage(_media(), 40)
    ev = relay.track_event.emit.call_args.args[0]
    assert ev == {"id": "123", "status": "running", "fpct": 40.0}


def test_note_stage_reports_under_the_identity_id():
    relay = MagicMock()
    _tracked(relay)._note_stage(_media("456", identity="123"), 90)
    assert relay.track_event.emit.call_args.args[0]["id"] == "123"


def test_note_stage_without_relay_or_media_is_silent():
    _tracked(None)._note_stage(_media(), 40)  # headless run: no signals at all
    relay = MagicMock()
    _tracked(relay)._note_stage(None, 40)
    _tracked(relay)._note_stage(_media(mid=None), 40)
    relay.track_event.emit.assert_not_called()


def test_stage_events_fill_the_row_and_a_restart_drains_it():
    b = _LifecycleStub()
    b._track_lifecycle(1, {"id": "9", "title": "t", "status": "running"})
    row = b._job_tracks[1]["9"]
    assert row["fpct"] == 0.0

    b._track_lifecycle(1, {"id": "9", "status": "running", "fpct": 40.0})
    assert row["fpct"] == 40.0
    assert row["title"] == "t"  # a stage event only carries progress

    b._track_lifecycle(1, {"id": "9", "status": "running", "fpct": 90.0})
    assert row["fpct"] == 90.0

    # The retry: a plain running event must not inherit the old word's fill.
    b._track_lifecycle(1, {"id": "9", "status": "running"})
    assert row["fpct"] == 0.0

    # Every hop was streamed to QML.
    assert [c[1]["fpct"] for c in b.queueTrackState.calls] == [0.0, 40.0, 90.0, 0.0]


def _finalize_fracs(tmp_path, *, extract=False, suffix=".flac", is_bts=True):
    """Run _perform_actual_download with every step patched out and return the
    fpct fractions it reported, in order."""
    relay = MagicMock()
    td = backend._TrackedDownload.__new__(backend._TrackedDownload)
    td._track_signals = relay
    td._tls = local()
    td._skip_existing_base = False
    td.fn_logger = MagicMock()
    td._names_reserved = set()
    td._names_reserved_lock = Lock()
    td.settings = SimpleNamespace(
        data=SimpleNamespace(
            video_convert_mp4=False,
            extract_flac=extract,
            downsample_enabled=False,
            path_binary_ffmpeg="ffmpeg" if extract else "",
        )
    )
    media = Track.__new__(Track)
    media.id = "123"
    media.waves_identity_id = None
    dst = tmp_path / f"song{suffix}"
    stream = SimpleNamespace(is_bts=is_bts)
    cls = backend._TrackedDownload
    with (
        patch.object(cls, "_download", return_value=(True, pathlib.Path(tmp_path / "raw"))),
        patch.object(cls, "_extract_flac", side_effect=lambda p: p),
        patch.object(cls, "_downsample_audio", side_effect=lambda p: p),
        patch.object(cls, "_faststart_remux", side_effect=lambda p, s: p),
        patch.object(cls, "_claim_destination", return_value=(dst, "reserved")),
        patch.object(cls, "_handle_metadata_and_extras", return_value=None),
        patch.object(cls, "_move_file", return_value=True),
        patch.object(cls, "_record_name_written"),
        patch("tidaler.download.name_builder_item", return_value="x"),
    ):
        ok, _ = td._perform_actual_download(media, dst, None, extract, False, stream)
    assert ok is True
    return [c.args[0]["fpct"] for c in relay.track_event.emit.call_args_list]


def test_finishing_opens_empty_when_the_ffmpeg_steps_are_skipped(tmp_path):
    """The reported livetest bug: fixed milestones opened the word two-thirds
    full because the skipped ffmpeg steps' shares were awarded anyway. With
    nothing but tagging and the move to run, every fraction before tagging
    must be zero, and the two real steps carry the whole word."""
    fracs = _finalize_fracs(tmp_path)
    assert fracs[0] == 0.0
    tag, move = fracs[-2], fracs[-1]
    assert move == 100.0
    assert 60 <= tag <= 75  # tagging's share of tag+move, not a near-full word
    assert all(f == 0.0 for f in fracs[:-2])  # skipped steps award nothing


def test_a_step_that_runs_earns_its_share(tmp_path):
    """With FLAC extraction on, its boundary reports a real fraction between
    the empty start and tagging's, and the sequence never moves backwards."""
    fracs = _finalize_fracs(tmp_path, extract=True)
    assert fracs == sorted(fracs)
    assert any(0 < f < fracs[-2] for f in fracs[:-2])


def test_note_delivered_flips_the_word_early_and_carries_no_record():
    """The early done must ride the row's key (the identity id) and carry
    neither path nor quality: the definitive done event that follows is the
    one allowed to record ownership from reality."""
    relay = MagicMock()
    _tracked(relay)._note_delivered(_media("456", identity="123"))
    assert relay.track_event.emit.call_args.args[0] == {"id": "123", "status": "done"}
    _tracked(None)._note_delivered(_media())  # headless: silent, no crash


def _run_item(tmp_path, *, success):
    """Drive Download.item with every step patched, returning the order the
    download, the delivery announcement and post-processing happened in."""
    dl = Download.__new__(Download)
    dl.event_abort = Event()
    dl.settings = SimpleNamespace(data=SimpleNamespace(download_dolby_atmos=True))
    media = Track.__new__(Track)
    media.audio_modes = None
    calls: list[str] = []
    dst = tmp_path / "s.flac"

    def _dl(*a, **k):
        calls.append("download")
        return success, dst

    with (
        patch.object(Download, "_validate_and_prepare_media", return_value=media),
        patch.object(Download, "_prepare_file_paths_and_skip_logic", return_value=(dst, "", False, False)),
        patch.object(Download, "_adjust_quality_settings", return_value=(None, None)),
        patch.object(Download, "_download_and_process_media", side_effect=_dl),
        patch.object(Download, "_note_delivered", side_effect=lambda m: calls.append("delivered")),
        patch.object(Download, "_perform_post_processing", side_effect=lambda *a, **k: calls.append("post")),
    ):
        ok, _ = dl.item("template", media=media)
    assert ok is success
    return calls


def test_delivery_is_announced_before_the_post_processing_delay(tmp_path):
    """The politeness delay lives inside post-processing; announcing delivery
    after it left the FINISHING word sitting full through a deliberate sleep."""
    assert _run_item(tmp_path, success=True) == ["download", "delivered", "post"]


def test_a_failed_download_is_never_announced_delivered(tmp_path):
    assert _run_item(tmp_path, success=False) == ["download", "post"]
