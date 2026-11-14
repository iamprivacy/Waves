"""A track whose stream links lapse mid-queue asks for fresh ones, once.

Issue #61: on a long queue a track could reach its segments after the signed
links in its stream manifest had died. The CDN answered 403, the fetch gave
up at once (a 4xx is final), and the track failed though a new manifest
would have worked. Now a pass that failed on 403 or 410, with nothing
stopped, asks for the stream once more and runs the whole track again. Never
twice, never for a 404 or a stop, never onto a different format, and always
before the stream slot is traded for a finishing seat.
"""

from __future__ import annotations

import pathlib
import threading
from unittest.mock import MagicMock

import pytest
import requests
from tidalapi import Track, Video

import waves.download as dl_mod
from waves.download import Download
from waves.model.downloader import DownloadSegmentResult


class _Resp:
    def __init__(self, status: int) -> None:
        self.status_code = status


def _seg(ok: bool, status: int | None = None) -> DownloadSegmentResult:
    error = requests.exceptions.HTTPError(response=_Resp(status)) if status else None
    return DownloadSegmentResult(
        result=ok, url="https://cdn.test/seg_1.m4s", path_segment=pathlib.Path("x"), id_segment=1, error=error
    )


def _track() -> Track:
    return Track.__new__(Track)


def _downloader(passes: list[tuple[bool, int | None]], fresh=("fresh-manifest", ".flac", True, "fresh-stream")):
    """A Download whose _download runs ``passes`` in order: (succeeded, the
    status a failed segment met). It sets the lapse note the way the real
    _download does, from segment results."""
    b = Download.__new__(Download)
    b.event_abort = threading.Event()
    b.fn_logger = MagicMock()
    b.calls = []
    queue = list(passes)

    def fake_download(media, path_file, stream_manifest=None, event_stop=None):
        b.calls.append(("download", stream_manifest))
        ok, status = queue.pop(0)
        results = [_seg(True), _seg(ok, None if ok else status)]
        if not ok and any(
            not r.result and dl_mod._http_status(r.error) in dl_mod._LINK_LAPSED_STATUSES for r in results
        ):
            dl_mod._PASS_NOTE.lapsed = True
        return ok, path_file

    def fake_stream_info(media):
        b.calls.append(("stream_info", None))
        return fresh

    b._download = fake_download
    b._get_stream_info = fake_stream_info
    return b


def _run(b, media=None, event_stop=None):
    return Download._download_with_link_refresh(
        b,
        media or _track(),
        pathlib.Path("/tmp-x/file"),
        pathlib.Path("/music/a.flac"),
        "first-manifest",
        True,
        "first-stream",
        event_stop,
    )


@pytest.mark.parametrize("status", [403, 410])
def test_a_lapsed_link_refetches_the_stream_and_runs_the_track_again(status):
    b = _downloader([(False, status), (True, None)])

    ok, _path, extract, stream = _run(b)

    assert ok is True
    assert b.calls == [("download", "first-manifest"), ("stream_info", None), ("download", "fresh-manifest")]
    assert (extract, stream) == (True, "fresh-stream"), "the finish follows the stream the file came from"
    assert "lapsed" in b.fn_logger.warning.call_args.args[0]


def test_a_second_lapse_is_final():
    b = _downloader([(False, 403), (False, 403), (True, None)])

    ok, *_ = _run(b)

    assert ok is False
    assert [c[0] for c in b.calls] == ["download", "stream_info", "download"]
    assert dl_mod._PASS_NOTE.lapsed is False, "no stale note for the next item on this thread"


@pytest.mark.parametrize("status", [404, 401, 500])
def test_other_failures_never_ask_for_a_new_stream(status):
    b = _downloader([(False, status)])

    ok, _path, extract, stream = _run(b)

    assert ok is False
    assert b.calls == [("download", "first-manifest")]
    assert (extract, stream) == (True, "first-stream")


def test_a_stopped_item_is_not_fetched_again():
    b = _downloader([(False, 403)])
    event_stop = threading.Event()
    event_stop.set()

    assert _run(b, event_stop=event_stop)[0] is False
    assert b.calls == [("download", "first-manifest")]


def test_quitting_the_app_counts_as_stopped():
    b = _downloader([(False, 403)])
    b.event_abort.set()

    assert _run(b)[0] is False
    assert len(b.calls) == 1


@pytest.mark.parametrize(
    "fresh",
    [
        ("fresh-manifest", ".m4a", True, "s"),  # another extension: the path would be wrong
        ("fresh-manifest", ".flac", False, "s"),  # another FLAC step
        (None, "", False, None),  # the stream ask itself failed
        (None, ".flac", True, None),  # a track with no manifest
    ],
)
def test_a_fresh_stream_that_does_not_match_fails_the_track(fresh):
    b = _downloader([(False, 403), (True, None)], fresh=fresh)

    ok, *_ = _run(b)

    assert ok is False
    assert [c[0] for c in b.calls] == ["download", "stream_info"]
    assert b.fn_logger.error.call_count == 1


def test_a_video_runs_again_without_a_manifest():
    """A video's playlist is fetched inside _download each pass, so its fresh
    links come with the rerun itself; the stream ask only has to succeed."""
    b = _downloader([(False, 403), (True, None)], fresh=(None, ".flac", True, None))

    ok, *_ = _run(b, media=Video.__new__(Video))

    assert ok is True


def test_a_stub_download_that_sets_no_note_is_taken_at_its_word():
    """Many tests stub _download; a stale note from an earlier item on the same
    thread must never send such a stub around twice."""
    dl_mod._PASS_NOTE.lapsed = True
    b = _downloader([])
    b._download = lambda media, path_file, stream_manifest=None, event_stop=None: (False, path_file)

    assert _run(b)[0] is False
    assert b.calls == []


def test_the_real_pass_notes_a_lapse_from_its_segments(monkeypatch):
    """The real _download marks the pass lapsed only when a failed segment met
    403 or 410, and not for a lapsed spurious tail the pass forgave."""
    monkeypatch.setattr(dl_mod, "name_builder_item", lambda media: "name")
    b = Download.__new__(Download)
    b.progress_gui = None
    b._get_media_urls = lambda media, manifest: ["u1", "u2"]
    b._setup_progress = lambda name, urls, to_stdout: (0, 2, None)
    b._note_progress_task = lambda media, task: None
    b._download_postprocess = lambda ok, path, results, media, manifest, tail: (ok, path)
    cases = [
        ((False, [_seg(True), _seg(False, 403)]), True),
        ((False, [_seg(True), _seg(False, 410)]), True),
        ((False, [_seg(True), _seg(False, 404)]), False),
        ((True, [_seg(True), _seg(False, 403)]), False),
    ]
    for (ok, results), lapsed in cases:
        dl_mod._PASS_NOTE.lapsed = False
        b._download_segments = lambda *a, ok=ok, results=results: (ok, results)
        Download._download(b, _track(), pathlib.Path("/tmp-x/f"), None, None)
        assert dl_mod._PASS_NOTE.lapsed is lapsed, (ok, results)


def test_the_finishing_seat_waits_for_the_last_pass(tmp_path):
    """The slot is traded for a finishing seat only once the bytes are done,
    so the second pass streams under the slot it already held."""
    b = _downloader([(False, 403), (False, 403)])
    ticket = MagicMock()
    ticket.try_finishing_seat.side_effect = lambda: b.calls.append(("seat", None))

    ok, _ = Download._perform_actual_download(
        b, _track(), tmp_path / "a.flac", "first-manifest", True, False, "first-stream", None, ticket=ticket
    )

    assert ok is False
    assert [c[0] for c in b.calls] == ["download", "stream_info", "download", "seat"]


def test_a_lapse_with_a_rerun_in_hand_is_not_reported_as_a_lost_track():
    """Pass 1 used to log "the track was not written" for a lapsed segment
    even when the rerun on fresh links then wrote it. Only the last pass
    (or a failure that is not a lapse) reports it."""
    b = Download.__new__(Download)
    b.event_abort = threading.Event()
    b.fn_logger = MagicMock()
    b._segment_pool = lambda: _Inline()
    b.progress = MagicMock()
    lapsed = _seg(False, 403)  # one url, so never a forgiven spurious tail
    b._download_segment_gauged = lambda url, *a: lapsed

    def run(refetch_left):
        b.fn_logger.reset_mock()
        dl_mod._PASS_NOTE.refetch_left = refetch_left
        try:
            ok, _ = Download._download_segments(b, ["u1"], pathlib.Path("/tmp-x"), None, 0, False, None, 0)
        finally:
            dl_mod._PASS_NOTE.refetch_left = False
        assert ok is False
        return b.fn_logger.error.call_count

    assert run(True) == 0
    assert run(False) == 1
    b._download_segment_gauged = lambda url, *a: _seg(False, 404)
    assert run(True) == 1, "a missing segment is not a lapse, the rerun will not come"


def test_the_first_pass_knows_a_rerun_is_in_hand():
    b = _downloader([(False, 403), (True, None)])
    seen = []
    inner = b._download

    def spy(**kw):
        seen.append(getattr(dl_mod._PASS_NOTE, "refetch_left", False))
        return inner(**kw)

    b._download = spy
    assert _run(b)[0] is True
    assert seen == [True, False]
    assert dl_mod._PASS_NOTE.refetch_left is False


class _Inline:
    """An executor that runs each job at once, so as_completed sees done futures."""

    def submit(self, fn, *args):
        from concurrent.futures import Future

        f = Future()
        f.set_result(fn(*args))
        return f
