"""Regression guard: the collection-download loop must terminate.

THE BUG
-------
``Download._execute_collection_downloads`` used ``while not progress.finished:``
and re-submitted every item in the collection on each pass. ``progress`` is the
shared rich ``Progress`` bar, and ``rich.Progress.finished`` is
``all(task.finished)`` over EVERY task on it, including the one task
``_setup_progress`` adds per track. A track task is snapped complete only on
success, so a single failed track left the whole item list being re-submitted
forever: the album never finished, the queue row stayed "running", and with
skip_existing off every sibling track was re-downloaded and rewritten on every
pass, without bound. Only Cancel escaped.

The spin is specific to the single-URL (BTS) branch, which is the default
quality: multi-segment tracks advance their task even on failure, so they reach
completed >= total regardless.

THE FIX gates the loop on THIS collection's own progress task, which
``_process_download_futures`` advances once per completed item, so the pass runs
exactly once. This is the collection-level twin of the segment-level spin already
fenced by ``test_download_segment_loop.py``.
"""

from __future__ import annotations

import pathlib
import threading
from unittest.mock import MagicMock

from rich.progress import Progress

from tidaler.download import Download


def _bridge(n_items: int) -> tuple[Download, Progress, int]:
    b = Download.__new__(Download)  # bypass __init__; set only what the method touches
    b.settings = MagicMock()
    b.settings.data.downloads_concurrent_max = 2
    b.event_abort = threading.Event()
    b.fn_logger = MagicMock()
    b.progress_gui = None
    progress = Progress()
    progress_task = progress.add_task("list", total=n_items)
    return b, progress, progress_task


def _run(b: Download, items: list, progress: Progress, progress_task: int) -> list[pathlib.Path]:
    return Download._execute_collection_downloads(
        b,
        items,
        "{artist_name}/{album_title}/{track_title}",
        None,
        None,
        False,
        True,
        len(items),
        progress,
        progress_task,
        True,  # progress_stdout, so the GUI emitter is never touched
    )


def test_a_failed_single_url_track_does_not_respin_the_collection():
    """A track whose own progress task never completes (the failed single-URL
    shape) must not cause every other item to be downloaded again."""
    items = ["t1", "t2", "t3", "t4"]
    b, progress, progress_task = _bridge(len(items))

    # The failed track's per-track task: added to the same bar, never completed.
    # This is what made `progress.finished` permanently False.
    progress.add_task("failed track", total=100, completed=0)

    calls: list[str] = []

    def fake_item(media, **kwargs):
        calls.append(media)
        # If the loop re-submits, we see more calls than items. Fail loudly
        # rather than hang the suite.
        assert len(calls) <= len(items), "collection loop re-submitted (did not terminate)"
        return True, pathlib.Path("/base/artist/album/track.flac")

    b.item = fake_item

    result_dirs = _run(b, items, progress, progress_task)

    assert sorted(calls) == sorted(items)  # each item attempted exactly once
    assert len(result_dirs) == len(items)
    assert not progress.finished  # the stuck track task is still unfinished...
    assert progress.tasks[progress_task].finished  # ...but the collection is done


def test_a_fully_successful_collection_still_runs_once():
    """Control: with no stuck task the behaviour is unchanged."""
    items = ["t1", "t2", "t3"]
    b, progress, progress_task = _bridge(len(items))

    calls: list[str] = []

    def fake_item(media, **kwargs):
        calls.append(media)
        assert len(calls) <= len(items), "collection loop re-submitted (did not terminate)"
        return True, pathlib.Path("/base/artist/album/track.flac")

    b.item = fake_item

    _run(b, items, progress, progress_task)

    assert sorted(calls) == sorted(items)
    assert progress.finished


def test_an_empty_collection_completes_its_task():
    """The empty-list early return is untouched by the fix."""
    b, progress, progress_task = _bridge(0)
    b.item = MagicMock(side_effect=AssertionError("must not download anything"))

    assert _run(b, [], progress, progress_task) == []
    assert progress.tasks[progress_task].finished


def test_abort_still_escapes_the_loop():
    """Aborting mid-pass returns instead of continuing, as before."""
    items = ["t1", "t2"]
    b, progress, progress_task = _bridge(len(items))
    progress.add_task("failed track", total=100, completed=0)

    def fake_item(media, **kwargs):
        b.event_abort.set()
        return False, None

    b.item = fake_item

    assert _run(b, items, progress, progress_task) == []
