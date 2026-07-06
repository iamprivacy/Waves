"""Regression tests for the download/metadata audit fixes.

Covers:
  * Finding 2  - a fully failed single-URL (BTS) track is marked corrupt, not "success".
  * Finding 2  - _segments_merge failure on the sole segment is a real failure.
  * Finding 3  - Metadata.save() raises a clear, catchable error for unreadable files.
  * Finding 5  - the .m3u8 playlist writer emits single '\n' terminators (no os.linesep).
  * Finding 6  - pause wait is abort/stop aware and does not block forever.
"""

import pathlib
import threading
from unittest.mock import MagicMock, patch

import pytest

from tidaler.download import Download
from tidaler.metadata import Metadata, MetadataUnreadable
from tidaler.model.downloader import DownloadSegmentResult


class _FakeTask:
    """Progress task that reports `finished` False on the first check, True after.

    `_download_segments` wraps its executor block in `while not task.finished`; the
    real segment downloader advances progress until finished. In these tests the
    segment downloader is mocked, so we flip `finished` to True after the first read
    to let the outer loop run exactly one pass and then exit.
    """

    def __init__(self):
        self._reads = 0
        self.percentage = 0.0

    @property
    def finished(self) -> bool:
        self._reads += 1
        return self._reads > 1


class _FakeProgress:
    def __init__(self):
        self._task = _FakeTask()
        self.tasks = {0: self._task}

    def add_task(self, *args, **kwargs):
        return 0

    def advance(self, _task):
        pass


def _make_download(skip_existing: bool = False) -> Download:
    dl = Download(
        tidal_obj=MagicMock(),
        skip_existing=skip_existing,
        path_base="./tmp",
        fn_logger=MagicMock(),
        progress=MagicMock(),
    )
    dl.settings = MagicMock()
    dl.settings.data.downloads_simultaneous_per_track_max = 1
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()
    return dl


class TestSingleUrlFailureIsCorrupt:
    """Finding 2: the last-URL leniency must not exempt a single-URL BTS track."""

    def test_single_url_hard_failure_marks_corrupt(self):
        dl = _make_download()
        dl.progress = _FakeProgress()

        url = "https://tidal/bts-only-segment"
        failed = DownloadSegmentResult(result=False, url=url, path_segment=pathlib.Path("seg"), id_segment=0)

        with patch.object(dl, "_download_segment", return_value=failed):
            result_segments, results = dl._download_segments(
                [url], pathlib.Path("./tmp"), None, 0, progress_to_stdout=False
            )

        # The only (and thus last) URL failed: this is a genuine failure, not a spurious tail.
        assert result_segments is False
        assert len(results) == 1

    def test_multi_url_spurious_tail_is_lenient(self):
        dl = _make_download()
        dl.progress = _FakeProgress()

        urls = ["https://tidal/seg_0", "https://tidal/seg_1"]

        def fake_segment(url, *_args, **_kwargs):
            # The trailing segment is the spurious 500 tail; the rest succeed.
            ok = url is not urls[-1]
            return DownloadSegmentResult(result=ok, url=url, path_segment=pathlib.Path(url), id_segment=0)

        with patch.object(dl, "_download_segment", side_effect=fake_segment):
            result_segments, _ = dl._download_segments(urls, pathlib.Path("./tmp"), None, 0, progress_to_stdout=False)

        # Only the multi-segment spurious tail is exempt -> not corrupt.
        assert result_segments is True

    def test_multi_url_non_tail_failure_marks_corrupt(self):
        dl = _make_download()
        dl.progress = _FakeProgress()

        urls = ["https://tidal/seg_0", "https://tidal/seg_1"]

        def fake_segment(url, *_args, **_kwargs):
            # A NON-tail segment fails -> real corruption.
            ok = url is not urls[0]
            return DownloadSegmentResult(result=ok, url=url, path_segment=pathlib.Path(url), id_segment=0)

        with patch.object(dl, "_download_segment", side_effect=fake_segment):
            result_segments, _ = dl._download_segments(urls, pathlib.Path("./tmp"), None, 0, progress_to_stdout=False)

        assert result_segments is False


class TestSegmentsMerge:
    """Finding 2 mirror: _segments_merge must treat a sole-segment failure as real."""

    def test_single_segment_merge_failure_is_real(self, tmp_path):
        dl = _make_download()

        missing = DownloadSegmentResult(result=True, url="u", path_segment=tmp_path / "does-not-exist", id_segment=0)

        # Reading the (only) segment fails -> must NOT be exempted as a spurious tail.
        assert dl._segments_merge(tmp_path / "out.m4a", [missing]) is False

    def test_multi_segment_tail_failure_is_lenient(self, tmp_path):
        dl = _make_download()

        good = tmp_path / "seg_0"
        good.write_bytes(b"data")
        seg_good = DownloadSegmentResult(result=True, url="u0", path_segment=good, id_segment=0)
        seg_tail = DownloadSegmentResult(result=True, url="u1", path_segment=tmp_path / "seg_1_missing", id_segment=1)

        # The trailing segment is missing but it's a multi-segment track -> lenient.
        assert dl._segments_merge(tmp_path / "out.m4a", [seg_good, seg_tail]) is True


class TestMetadataUnreadableGuard:
    """Finding 3: Metadata.save() must raise a clear error, not AttributeError."""

    def test_save_on_unreadable_file_raises_metadata_unreadable(self, tmp_path):
        bogus = tmp_path / "truncated.m4a"
        bogus.write_bytes(b"not a real audio container")

        with patch("tidaler.metadata.mutagen.File", return_value=None):
            m = Metadata(path_file=bogus, target_upc={"MP4": "x"})

            with pytest.raises(MetadataUnreadable):
                m.save()


class TestPlaylistLineEndings:
    """Finding 5: playlist entries must not use os.linesep (Windows double-translation)."""

    def test_playlist_written_with_plain_newline(self, tmp_path, monkeypatch):
        dl = _make_download()
        dl.settings.data.playlist_create = True

        track = tmp_path / "01 - Song.m4a"
        track.write_bytes(b"x")

        # Force os.linesep to CRLF to prove the writer does not concatenate it directly.
        monkeypatch.setattr("tidaler.download.os.linesep", "\r\n")

        with patch("tidaler.download.AudioExtensionsValid", [".m4a"]):
            created = dl.playlist_populate({tmp_path}, name_list="MyList", is_album=True, sort_alphabetically=True)

        assert len(created) == 1
        # Read raw bytes without newline translation.
        raw = created[0].read_bytes()
        assert b"\r\r\n" not in raw
        assert raw.rstrip(b"\r\n") == b"01 - Song.m4a"


class TestWaitWhilePaused:
    """Finding 6: pause wait must honor abort and per-item stop, never block forever."""

    def test_returns_true_when_abort_set_while_paused(self):
        dl = _make_download()
        dl.event_run.clear()  # paused

        stop = threading.Event()

        def abort_soon():
            dl.event_abort.set()

        timer = threading.Timer(0.05, abort_soon)
        timer.start()
        try:
            should_abort = dl._wait_while_paused(stop)
        finally:
            timer.cancel()

        assert should_abort is True

    def test_returns_true_when_stop_set_while_paused(self):
        dl = _make_download()
        dl.event_run.clear()  # paused

        stop = threading.Event()
        timer = threading.Timer(0.05, stop.set)
        timer.start()
        try:
            should_abort = dl._wait_while_paused(stop)
        finally:
            timer.cancel()

        assert should_abort is True

    def test_returns_false_on_resume(self):
        dl = _make_download()
        dl.event_run.clear()  # paused

        stop = threading.Event()
        timer = threading.Timer(0.05, dl.event_run.set)  # resume
        timer.start()
        try:
            should_abort = dl._wait_while_paused(stop)
        finally:
            timer.cancel()

        assert should_abort is False

    def test_returns_immediately_when_not_paused(self):
        dl = _make_download()  # event_run already set
        assert dl._wait_while_paused(None) is False
