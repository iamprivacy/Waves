"""A track TIDAL refuses to stream is skipped, not failed (issue #25).

THE BUG
-------
The engine refused any track whose ``allow_streaming`` flag was false, logged
"This item is not available for listening anymore on TIDAL. Skipping", and
returned ``(False, "")`` exactly like a download that broke. Two things were
wrong with that. The flag is a FALSE NEGATIVE for our client: TIDAL serves
editions like "ALICIA (With Commentary)" with ``allowStreaming=false`` on every
track, yet the account can still play most of them, so gating on the flag
refused tracks that were downloadable. And the GUI tallied the refusal into
``fail_count``, so a whole album came back as ``RuntimeError: 15 of 15 tracks
failed``: a red album, a RETRY button, and no hint of who was refusing.

THE FIX has two halves:

1. Availability is decided where it is authoritative, at stream-fetch time. The
   ``allow_streaming`` pre-gate on tracks is gone; the engine attempts the
   stream and only the tracks TIDAL actually withholds (a 404 / "no stream", or
   a 401/403 whose body blames the asset, e.g. subStatus 4005) are refused. An
   auth 401 is NOT a refusal.
2. A refusal is a third outcome. ``_note_unavailable`` marks the calling thread,
   the tracked ``item()`` reads that mark and reports ``unavailable`` instead of
   ``failed``, and the count rides beside the tallies rather than inside them:
   it never fails the album around it, and never props up an album that produced
   nothing either.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest
import requests
from tidalapi.album import Album
from tidalapi.exceptions import StreamNotAvailable
from tidalapi.media import Track

from tidaler import download as download_mod
from tidaler.download import Download, _tidal_refuses_asset
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


def _track(allow_streaming: bool = False) -> MagicMock:
    m = MagicMock(spec=Track)
    m.id = "156010615"
    m.track_num = 1
    m.volume_num = 1
    m.duration = 100
    m.allow_streaming = allow_streaming
    return m


def _http_error(status: int, body: dict | None = None) -> requests.HTTPError:
    """A requests.HTTPError carrying a TIDAL-shaped JSON body, exactly as the
    playback endpoint raises for a track it will not serve."""
    response = MagicMock(spec=requests.Response)
    response.status_code = status
    response.json.return_value = body if body is not None else {}
    return requests.HTTPError(f"{status} error", response=response)


def _statuses(relay: MagicMock) -> list[str]:
    return [call.args[0]["status"] for call in relay.track_event.emit.call_args_list]


@pytest.fixture(autouse=True)
def _stub_name_builders():
    with (
        patch.object(backend, "name_builder_item", return_value="Alicia Keys - Time Machine"),
        patch.object(backend, "name_builder_title", return_value="Time Machine"),
        patch.object(download_mod, "name_builder_item", return_value="Alicia Keys - Time Machine"),
        patch.object(download_mod, "name_builder_title", return_value="ALICIA (With Commentary)"),
    ):
        yield


# --- the refusal classifier reads TIDAL's own words --------------------------


def test_asset_refusal_is_recognised():
    # The real body observed for tracks greyed out in the official apps.
    err = _http_error(401, {"status": 401, "subStatus": 4005, "userMessage": "Asset is not ready for playback"})
    assert _tidal_refuses_asset(err) == "Asset is not ready for playback"


def test_a_403_asset_refusal_counts_too():
    err = _http_error(403, {"subStatus": 4006, "userMessage": "Asset is not available in your location"})
    assert _tidal_refuses_asset(err) == "Asset is not available in your location"


def test_an_expired_token_is_not_a_refusal():
    # A 401 that blames the session, not the asset, must never read as "gone":
    # retrying after a real login can still fetch the track.
    err = _http_error(
        401, {"status": 401, "subStatus": 11003, "userMessage": "The token has expired. (Expired on ...)"}
    )
    assert _tidal_refuses_asset(err) is None


def test_an_auth_substatus_is_not_a_refusal():
    err = _http_error(401, {"status": 401, "subStatus": 11002, "userMessage": "User does not have a valid session"})
    assert _tidal_refuses_asset(err) is None


def test_a_server_error_is_not_a_refusal():
    assert _tidal_refuses_asset(_http_error(500, {"userMessage": "Internal server error"})) is None


def test_a_bodyless_401_is_still_a_refusal():
    # A 401 with no readable body is treated as a refusal (the stream path has
    # already survived tidalapi's one expired-token refresh), with a generic
    # message so the log line still says something.
    err = _http_error(401, None)
    assert _tidal_refuses_asset(err) == "HTTP 401"


# --- the engine raises the mark at stream time -------------------------------


def test_the_flag_no_longer_gates_a_track():
    # allow_streaming=false must NOT refuse the track up front anymore: those
    # tracks are downloadable. keep_album skips the re-fetch that needs a live
    # session, so the very track object passes straight through, unmarked.
    dl, _relay = _make_tracked()
    media = _track(allow_streaming=False)
    assert dl._validate_and_prepare_media(media, None, None, keep_album=True) is media
    assert dl._take_unavailable() is False


def test_stream_refusal_marks_the_track():
    dl, _relay = _make_tracked()
    media = _track(allow_streaming=False)
    media.get_stream.side_effect = _http_error(
        401, {"subStatus": 4005, "userMessage": "Asset is not ready for playback"}
    )
    manifest, _ext, _flac, _stream = dl._get_stream_info(media)
    assert manifest is None
    assert dl._take_unavailable() is True


def test_stream_not_available_marks_the_track():
    # tidalapi raises StreamNotAvailable when the asset is a 404 on the wire.
    dl, _relay = _make_tracked()
    media = _track(allow_streaming=True)
    media.get_stream.side_effect = StreamNotAvailable("Stream not available for this track")
    manifest, _ext, _flac, _stream = dl._get_stream_info(media)
    assert manifest is None
    assert dl._take_unavailable() is True


def test_an_auth_error_at_stream_time_is_a_failure_not_a_refusal():
    dl, _relay = _make_tracked()
    media = _track(allow_streaming=True)
    media.get_stream.side_effect = _http_error(
        401, {"subStatus": 11003, "userMessage": "The token has expired. (Expired on ...)"}
    )
    manifest, _ext, _flac, _stream = dl._get_stream_info(media)
    assert manifest is None
    assert dl._take_unavailable() is False  # a real failure keeps the retry


def test_a_refused_collection_is_recorded_on_the_job():
    # An album whose OWN allowStreaming is false has nothing to enumerate, so it
    # is still refused up front. The engine returns before the track loop, so no
    # item() ever runs and the mark belongs to the job instead.
    dl, _relay = _make_tracked()
    album = MagicMock(spec=Album)
    album.allow_streaming = False
    assert dl._validate_and_prepare_media(album, None, None) is None
    assert dl.list_unavailable is True
    assert dl._take_unavailable() is False  # not a track's mark  # not a track's mark


# --- the GUI turns the mark into an outcome ----------------------------------


def _refusing_item(dl):
    """Stand in for the engine: mark the thread, then report no file, which is
    exactly what item() does for a track TIDAL will not stream."""

    def _item(*_args, media=None, **_kwargs):
        dl._note_unavailable(media)
        return False, ""

    return _item


def test_refused_track_reports_unavailable_not_failed():
    dl, relay = _make_tracked()
    with patch.object(Download, "item", side_effect=_refusing_item(dl)):
        ok, _ = dl.item(media=_track())
    assert ok is False
    assert _statuses(relay) == ["running", "unavailable"]
    assert dl.unavailable_count == 1
    assert dl.fail_count == 0  # THE FIX: not a failure of ours
    assert dl.ok_count == 0  # and not a success either


def test_a_refused_track_does_not_fail_the_album_around_it():
    dl, _relay = _make_tracked()
    with patch.object(Download, "item", return_value=(True, "/tmp/a.flac")):
        dl.item(media=_track())
    with patch.object(Download, "item", side_effect=_refusing_item(dl)):
        dl.item(media=_track())
    assert (dl.write_count, dl.ok_count, dl.fail_count, dl.unavailable_count) == (1, 1, 0, 1)
    assert (
        backend._collection_incomplete_reason(
            dl.write_count, dl.ok_count, dl.fail_count, dl.unavailable_count, dl.list_unavailable
        )
        is None
    )


def test_an_album_of_nothing_but_refusals_still_says_so():
    dl, _relay = _make_tracked()
    with patch.object(Download, "item", side_effect=_refusing_item(dl)):
        for _ in range(3):
            dl.item(media=_track())
    assert (dl.write_count, dl.ok_count, dl.fail_count, dl.unavailable_count) == (0, 0, 0, 3)
    assert (
        backend._collection_incomplete_reason(
            dl.write_count, dl.ok_count, dl.fail_count, dl.unavailable_count, dl.list_unavailable
        )
        == "not available on TIDAL anymore (3 tracks)"
    )


def test_a_real_failure_is_still_a_failure():
    dl, relay = _make_tracked()
    with patch.object(Download, "item", return_value=(False, "")):
        dl.item(media=_track())
    assert _statuses(relay) == ["running", "failed"]
    assert (dl.fail_count, dl.unavailable_count) == (1, 0)


def test_the_mark_never_leaks_onto_the_next_track():
    """items() fans item() out on a pool and threads are reused, so a refusal
    left behind would silently excuse the next track's real failure."""
    dl, relay = _make_tracked()
    with patch.object(Download, "item", side_effect=_refusing_item(dl)):
        dl.item(media=_track())
    with patch.object(Download, "item", return_value=(False, "")):
        dl.item(media=_track())
    assert _statuses(relay) == ["running", "unavailable", "running", "failed"]
    assert (dl.fail_count, dl.unavailable_count) == (1, 1)


def test_a_raising_track_clears_the_mark_too():
    dl, relay = _make_tracked()
    with (
        patch.object(Download, "item", side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError),
    ):
        dl.item(media=_track())
    with patch.object(Download, "item", return_value=(False, "")):
        dl.item(media=_track())
    assert _statuses(relay) == ["running", "failed", "running", "failed"]
    assert dl.unavailable_count == 0
