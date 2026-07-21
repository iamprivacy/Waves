"""Regression guard: the segment-download loop must terminate.

THE BUG
-------
``Download._download_segments`` used ``while not self.progress.tasks[p_task].finished:``
and re-submitted every segment URL each pass, exiting only when the rich progress
task's ``completed >= total``. Progress advances once per streamed chunk. A segment
that returns HTTP 200 with an empty (0-byte) body yields no chunks, so it advances
the bar zero times; the task never reaches ``total`` and the loop re-downloaded the
whole track forever (an infinite spin, multiplying bandwidth and CPU). The single-
file path carried the same risk when a HEAD content-length overstated the bytes
actually streamed.

THE FIX runs the segment pass exactly once (segment-level retries already live
inside ``_download_segment`` via requests ``Retry(total=5)``), derives success from
the per-segment results, and snaps the progress bar to complete on success so the
GUI still reads 100% despite the short estimate.
"""

from __future__ import annotations

import pathlib
import threading
from unittest.mock import MagicMock

from rich.progress import Progress

from tidaler.download import Download
from tidaler.model.downloader import DownloadSegmentResult


def _bridge(total: float) -> tuple[Download, int]:
    b = Download.__new__(Download)  # bypass __init__; set only what the method touches
    b.settings = MagicMock()
    b.settings.data.downloads_simultaneous_per_track_max = 4
    b.event_abort = threading.Event()
    b.fn_logger = MagicMock()
    b.progress = Progress()
    p_task = b.progress.add_task("test", total=total)
    return b, p_task


def _run(
    b: Download, urls: list[str], p_task: int, n_tail_spurious: int | None = None
) -> tuple[bool, list[DownloadSegmentResult]]:
    return Download._download_segments(b, urls, pathlib.Path("."), None, p_task, False, None, n_tail_spurious)


def _tail_fails(urls: list[str], calls: list[str]):
    """A fake _download_segment where only the final URL fails."""

    def fake_segment(url, path_base, block_size, task_id, to_stdout, event_stop):
        calls.append(url)
        assert len(calls) <= len(urls), "segment loop re-downloaded (did not terminate)"
        ok = url is not urls[-1]
        return DownloadSegmentResult(result=ok, url=url, path_segment=pathlib.Path(url), id_segment=0)

    return fake_segment


def test_empty_but_successful_segment_does_not_respin():
    """A 0-byte HTTP 200 segment advances the bar zero times; the loop must still
    run each URL exactly once instead of re-downloading forever."""
    urls = ["u1", "u2", "u3"]
    # A total the per-segment advances can never reach (the fakes advance 0), so
    # the old `while not finished` would have spun here.
    b, p_task = _bridge(total=len(urls) + 5)

    calls: list[str] = []

    def fake_segment(url, path_base, block_size, task_id, to_stdout, event_stop):
        calls.append(url)
        # If the loop re-submits, we see more calls than URLs. Fail loudly rather
        # than hang the suite.
        assert len(calls) <= len(urls), "segment loop re-downloaded (did not terminate)"
        # HTTP 200 with an empty body: success, but no progress advance.
        return DownloadSegmentResult(result=True, url=url, path_segment=pathlib.Path(url), id_segment=0)

    b._download_segment = fake_segment

    ok, results = _run(b, urls, p_task)

    assert ok is True
    assert sorted(calls) == sorted(urls)  # each URL attempted exactly once
    assert len(results) == len(urls)
    # Progress snapped to complete so the GUI reads 100% despite the short estimate.
    assert b.progress.tasks[p_task].percentage == 100.0


def test_real_failure_reports_false_and_leaves_progress_untouched():
    """A genuine failure (a single-URL track, so never a spurious tail) returns
    False and is NOT snapped to 100%, so the caller can mark it failed."""
    urls = ["only-one"]
    b, p_task = _bridge(total=4)

    def fake_segment(url, path_base, block_size, task_id, to_stdout, event_stop):
        return DownloadSegmentResult(result=False, url=url, path_segment=pathlib.Path(url), id_segment=0)

    b._download_segment = fake_segment

    ok, results = _run(b, urls, p_task)

    assert ok is False
    assert len(results) == 1
    assert b.progress.tasks[p_task].percentage == 0.0  # untouched on failure


def test_unproven_multi_segment_tail_is_tolerated():
    """With no manifest verdict (None: video m3u8, BTS, unparseable manifest),
    the legacy leniency applies: a failed final URL of a MULTI-segment track
    must not mark the whole track as failed, and the pass still runs once."""
    urls = ["seg1", "seg2", "seg3"]
    b, p_task = _bridge(total=len(urls))
    calls: list[str] = []
    b._download_segment = _tail_fails(urls, calls)

    ok, results = _run(b, urls, p_task, n_tail_spurious=None)

    assert ok is True  # presumed spurious tail tolerated
    assert sorted(calls) == sorted(urls)
    assert len(results) == len(urls)


def test_manifest_proven_spurious_tail_is_tolerated():
    """The manifest proved the final URL is over-generated padding
    (n_tail_spurious=1, the very-short-track shape): its failure is harmless."""
    urls = ["seg1", "seg2", "seg3"]
    b, p_task = _bridge(total=len(urls))
    calls: list[str] = []
    b._download_segment = _tail_fails(urls, calls)

    ok, _results = _run(b, urls, p_task, n_tail_spurious=1)

    assert ok is True


def test_manifest_proven_required_tail_failure_is_real():
    """THE 1e FIX: the manifest proved every URL is required audio
    (n_tail_spurious=0), so a failed final segment is a REAL failure. Before
    the fix this silently truncated the track and reported a clean success."""
    urls = ["seg1", "seg2", "seg3"]
    b, p_task = _bridge(total=len(urls))
    calls: list[str] = []
    b._download_segment = _tail_fails(urls, calls)

    ok, results = _run(b, urls, p_task, n_tail_spurious=0)

    assert ok is False  # truncation reported, not masked
    assert sorted(calls) == sorted(urls)
    assert len(results) == len(urls)
    assert b.progress.tasks[p_task].percentage == 0.0  # not snapped to 100% on failure


def _merge_fixture(tmp_path: pathlib.Path) -> tuple[Download, pathlib.Path, list[DownloadSegmentResult]]:
    """Two real segment files plus a missing tail segment."""
    b = Download.__new__(Download)
    results: list[DownloadSegmentResult] = []
    for i, data in enumerate([b"aaaa", b"bbbb"]):
        seg = tmp_path / f"seg_{i}"
        seg.write_bytes(data)
        results.append(DownloadSegmentResult(result=True, url=f"u{i}", path_segment=seg, id_segment=i))
    results.append(DownloadSegmentResult(result=False, url="u2", path_segment=tmp_path / "seg_2_missing", id_segment=2))
    return b, tmp_path / "merged", results


def test_merge_missing_tail_tolerated_when_spurious_or_unproven(tmp_path):
    """The merge mirrors the download-time rule: a missing tail segment file is
    tolerated when the manifest proved it spurious (or proved nothing)."""
    for n_tail_spurious in (1, None):
        case_dir = tmp_path / str(n_tail_spurious)
        case_dir.mkdir()
        b, path_file, results = _merge_fixture(case_dir)

        assert Download._segments_merge(b, path_file, results, n_tail_spurious) is True
        assert path_file.read_bytes() == b"aaaabbbb"


def test_merge_missing_required_tail_is_real_failure(tmp_path):
    """With the manifest proving every segment required (n_tail_spurious=0), a
    missing tail segment file at merge is a real failure."""
    b, path_file, results = _merge_fixture(tmp_path)

    assert Download._segments_merge(b, path_file, results, 0) is False
