"""A best-of-both merge leaves the album's .m3u8 behind, exactly as a plain
album download does.

WHAT THIS FENCES OFF
--------------------
Settings > Files > "Create .m3u8 playlist" promises a playlist file for every
downloaded album. The engine keeps that promise at the end of ``items()``: it
collects the landed paths in track order and hands them to
``playlist_populate``. The bridge's best-of-both merge stands in for
``items()`` over an explicit track list, fanned out through ``item()`` on its
own pool, and it stopped after the pool: every track landed, the queue row read
done, and no ``_<Album>.m3u8`` was ever written. The same album downloaded with
the merge preference off got its playlist. No retry ever produced the file,
because a retry only re-ran the same fan-out.

THE FIX
-------
The step ``items()`` ends with is now an engine method,
``Download._playlist_for_collection`` (the setting gate, the name, the
sort-by-number decision and the ``playlist_populate`` call), fed by
``Download._landed_paths`` (the submission-order path collection that
``_process_download_futures`` already did). ``items()`` calls both; the merge
fan-out calls both. One decision, so the two paths cannot drift on what the
file is called, what it lists or in which order.

HOW THIS STAYS FIXED
--------------------
A real ``Download`` (its network-touching ``__init__`` skipped) with the REAL
``playlist_populate``, a real temp album folder, and the REAL
``WavesBridge._download_merge_plan`` as an unbound method. ``item()`` is the
only stand-in on the merge path: it writes a real file where the engine would
and returns the engine's ``(ok, path)`` contract. Most assertions read the
folder; the few that need the arguments the playlist writer was handed record
the real ``playlist_populate`` and still let it write.
"""

from __future__ import annotations

import logging
import pathlib
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from tidalapi import Album

from tidaler.constants import PLAYLIST_EXTENSION, PLAYLIST_PREFIX
from tidaler.download import Download
from tidaler.helper.path import format_path_media
from tidaler.waves_ui import backend
from tidaler.waves_ui.backend import WavesBridge

_ALBUM_TITLE = "Album X (Deluxe)"
_M3U8 = f"{PLAYLIST_PREFIX}{_ALBUM_TITLE}{PLAYLIST_EXTENSION}"


def _album() -> Album:
    a = Album.__new__(Album)
    a.id = "alb-identity"
    a.name = _ALBUM_TITLE
    return a


class _Settings:
    def __init__(self, **kw):
        base = {
            "playlist_create": True,
            "downloads_concurrent_max": 3,
            "download_delay": False,
            "filename_illegal_replacement": "",
            "filename_illegal_map": None,
        }
        base.update(kw)
        self.data = SimpleNamespace(**base)


class _WritingDownload(Download):
    """The real engine minus its constructor. ``item()`` is the one stand-in:
    it lands a real file with the name the plan entry names, after an optional
    pause, and answers with the engine's own ``(ok, path)`` contract. Nothing
    playlist-related is overridden, so the file the tests read was written by
    the real ``playlist_populate``."""

    unavailable_count = 0

    def __init__(self, folder: pathlib.Path, settings: _Settings, *, pause: dict[int, float] | None = None):
        self.settings = settings
        self.fn_logger = logging.getLogger("test.merge.playlist")
        self.folder = folder
        self.pause = pause or {}
        self.outcomes: dict[int, tuple[bool, str]] = {}
        self._lock = threading.Lock()

    def item(self, *, media=None, list_position=0, event_stop=None, **_kw):
        # The engine's own first line: a set stop event means nothing is
        # fetched and nothing is written.
        if event_stop is not None and event_stop.is_set():
            return False, ""
        time.sleep(self.pause.get(list_position, 0.0))
        ok, name = self.outcomes.get(list_position, (True, media.name))
        if not name:
            return ok, ""
        path = self.folder / name
        path.write_bytes(b"\0")
        return ok, str(path)


def _plan(*names: str):
    """One plan entry per name; the entry's source track carries the file
    name ``item()`` will write, so the test can spell the folder in advance."""
    return [(SimpleNamespace(id=f"src-{i}", name=n), i, 1, f"id-{i}") for i, n in enumerate(names, 1)]


def _run_merge(dl: _WritingDownload, plan, *, template="{album_title}/{track_title}", abort=None) -> None:
    bridge = SimpleNamespace(settings=dl.settings)
    signals = SimpleNamespace(list_item=SimpleNamespace(emit=lambda v: None))
    # Identity re-tagging is not what is measured here; the source track
    # passes straight through so item() sees the name the plan gave it.
    with patch.object(backend, "_as_member_of", lambda src, *a: src):
        WavesBridge._download_merge_plan(bridge, dl, signals, abort or threading.Event(), _album(), template, plan)


def _lines(m3u8: pathlib.Path) -> list[str]:
    return m3u8.read_text(encoding="utf-8").splitlines()


class _RecordedPopulate:
    """Records what the real ``playlist_populate`` was handed, and still lets
    it write, so a test can read both the arguments and the file."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.paths_ordered: list = []

    def __enter__(self):
        real = Download.playlist_populate
        calls, ordered = self.calls, self.paths_ordered

        def _recording(this, *a, **k):
            calls.append(a)
            ordered.append(k.get("paths_ordered"))
            return real(this, *a, **k)

        self._patch = patch.object(_WritingDownload, "playlist_populate", _recording)
        self._patch.__enter__()
        return self

    def __exit__(self, *exc):
        self._patch.__exit__(*exc)
        return False


# --------------------------------------------------------------------------- #
# The fix: the merged album's folder holds the playlist file.
# --------------------------------------------------------------------------- #
def test_a_merged_album_gets_its_playlist_file(tmp_path):
    dl = _WritingDownload(tmp_path, _Settings())
    _run_merge(dl, _plan("A.flac", "B.flac", "C.flac"))
    assert (tmp_path / _M3U8).is_file(), sorted(p.name for p in tmp_path.iterdir())


def test_the_playlist_carries_the_identity_albums_title(tmp_path):
    """The merged album is re-opened under the identity edition; its playlist
    is named for that edition, exactly as a plain download of it would be."""
    dl = _WritingDownload(tmp_path, _Settings())
    _run_merge(dl, _plan("A.flac"))
    names = sorted(p.name for p in tmp_path.iterdir() if p.suffix == PLAYLIST_EXTENSION)
    assert names == [_M3U8]


def test_the_playlist_lists_the_tracks_in_plan_order_not_completion_order(tmp_path):
    """Track 1 lands LAST (it pauses; the pool is wide enough that 2 and 3
    finish first). The paths are read back from the futures in submission
    order, so the m3u still plays 1, 2, 3. A template with no track number
    keeps the folder from being sorted by name, so only the plan order can
    put these lines in this sequence."""
    dl = _WritingDownload(tmp_path, _Settings(downloads_concurrent_max=3), pause={1: 0.15})
    _run_merge(dl, _plan("C.flac", "A.flac", "B.flac"))
    assert _lines(tmp_path / _M3U8) == ["C.flac", "A.flac", "B.flac"]


def test_the_playlist_is_written_once_per_merge(tmp_path):
    dl = _WritingDownload(tmp_path, _Settings())
    with _RecordedPopulate() as rec:
        _run_merge(dl, _plan("A.flac", "B.flac"))
    assert len(rec.calls) == 1
    dirs, name, is_album, sort_by_number = rec.calls[0]
    assert dirs == {tmp_path}
    assert name == _ALBUM_TITLE
    assert is_album is True
    # The default template here carries no track number, so no name sort.
    assert sort_by_number is False


def test_a_numbered_template_sorts_the_playlist_by_number(tmp_path):
    """The other half of the sort decision, read from the RAW album template
    the merge passes (the merge never formats it at collection level): a
    template with {album_track_num} asks for the name sort, exactly as
    items() would decide from its collection-formatted copy."""
    dl = _WritingDownload(tmp_path, _Settings())
    with _RecordedPopulate() as rec:
        _run_merge(dl, _plan("01 A.flac", "02 B.flac"), template="{album_title}/{album_track_num} {track_title}")
    assert [c[3] for c in rec.calls] == [True]


# --------------------------------------------------------------------------- #
# The gate: the setting still decides.
# --------------------------------------------------------------------------- #
def test_no_playlist_when_the_setting_is_off(tmp_path):
    dl = _WritingDownload(tmp_path, _Settings(playlist_create=False))
    _run_merge(dl, _plan("A.flac", "B.flac"))
    assert not any(p.suffix == PLAYLIST_EXTENSION for p in tmp_path.iterdir())


# --------------------------------------------------------------------------- #
# The moment: after the pool, before the failure judgement, exactly where a
# plain album's playlist is written (items() writes it, then the bridge
# raises _raise_download_incomplete for the shortfall).
# --------------------------------------------------------------------------- #
def test_a_partly_failed_merge_still_leaves_the_playlist_for_what_landed(tmp_path):
    dl = _WritingDownload(tmp_path, _Settings())
    dl.outcomes[2] = (False, "")  # a hard failure: nothing written, no refusal recorded
    with pytest.raises(RuntimeError):
        _run_merge(dl, _plan("A.flac", "B.flac", "C.flac"))
    assert _lines(tmp_path / _M3U8) == ["A.flac", "C.flac"]


def test_a_member_that_returns_no_path_contributes_no_line(tmp_path):
    """A member owned in this folder is skipped with ``(True, "")`` (see
    _emit_skip): it lands nothing this run, so it hands the ordered list
    nothing and names no folder, and the folder listing (the truth about
    what belongs in the playlist) stands, exactly as it does for a plain
    album's skipped tracks."""
    dl = _WritingDownload(tmp_path, _Settings())
    dl.outcomes[2] = (True, "")
    with _RecordedPopulate() as rec:
        _run_merge(dl, _plan("A.flac", "B.flac", "C.flac"))
    assert rec.calls[0][0] == {tmp_path}, "an empty path must not name a folder (the process cwd)"
    assert rec.paths_ordered == [[tmp_path / "A.flac", tmp_path / "C.flac"]]
    assert _lines(tmp_path / _M3U8) == ["A.flac", "C.flac"]


def test_a_member_that_failed_names_no_folder_either(tmp_path):
    """The engine's failure shape is ``(False, <the path it aimed for>)``, from
    before anything was written there (a refusal, a stream that would not
    fetch, a stopped download). A first-time album that lost EVERY track
    therefore handed the playlist writer a folder that was never created,
    and the merge's own verdict ("N/N merged tracks failed") was replaced by
    the playlist's temp file failing to open. Landed means landed."""
    fresh = tmp_path / "never created"
    dl = _WritingDownload(fresh, _Settings())
    dl.item = lambda *, media=None, **kw: (False, str(fresh / media.name))  # aimed for, never written
    with pytest.raises(RuntimeError, match="merged tracks failed"):
        _run_merge(dl, _plan("01 A.flac", "02 B.flac"))
    assert not fresh.exists()


def test_a_track_still_landing_when_cancel_is_seen_is_listed(tmp_path):
    """The paths are read back once the pool has joined, so a track that was
    past its own stop check when the cancel arrived, and went on to land,
    is in the playlist. Two workers: 1 and 2 both pass their stop check at
    once, 1 sets the cancel on its way out while 2 is still pausing, and 3
    is refused by item()'s first line or cancelled before it starts. Track 1
    pauses a little itself so 2 has certainly started before the cancel."""
    abort = threading.Event()
    dl = _WritingDownload(tmp_path, _Settings(downloads_concurrent_max=2), pause={1: 0.15, 2: 0.4})
    real_item = dl.item

    def _item(*, media=None, list_position=0, **kw):
        result = real_item(media=media, list_position=list_position, **kw)
        if list_position == 1:
            abort.set()
        return result

    dl.item = _item
    _run_merge(dl, _plan("A.flac", "B.flac", "C.flac"), abort=abort)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["A.flac", "B.flac", _M3U8]
    assert _lines(tmp_path / _M3U8) == ["A.flac", "B.flac"]


def test_a_cancelled_merge_leaves_the_playlist_for_what_landed(tmp_path):
    """items() writes the playlist after the pool whatever stopped it, and a
    cancelled plain album keeps the m3u of what landed. The merge follows the
    engine here rather than choosing for itself. Track 1 sets the cancel on
    its way out; whether 2 and 3 are then cancelled by the loop or refused by
    item()'s own first line, neither lands, and the playlist says so."""
    abort = threading.Event()
    dl = _WritingDownload(tmp_path, _Settings(downloads_concurrent_max=1))
    real_item = dl.item

    def _item_then_cancel(*, media=None, list_position=0, **kw):
        result = real_item(media=media, list_position=list_position, **kw)
        if list_position == 1:
            abort.set()
        return result

    dl.item = _item_then_cancel
    _run_merge(dl, _plan("A.flac", "B.flac", "C.flac"), abort=abort)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["A.flac", _M3U8]
    assert _lines(tmp_path / _M3U8) == ["A.flac"]


# --------------------------------------------------------------------------- #
# The premises the extraction rests on.
# --------------------------------------------------------------------------- #
def test_items_ends_with_the_same_engine_method_the_merge_calls(tmp_path):
    """The engine's own path goes through the extracted step, so a plain
    album and a merged one write their playlist through ONE method. If this
    fails, the two have been allowed to drift again."""
    seen: list = []

    class _Engine(Download):
        def __init__(self):
            self.settings = _Settings()
            self.fn_logger = logging.getLogger("test.merge.playlist")
            self.progress = SimpleNamespace(add_task=lambda *a, **k: 0)
            self.progress_overall = None

        def _validate_and_prepare_media(self, media, media_id, media_type, video_download):
            return media

        def _setup_collection_download_context(self, media, file_template, video_download):
            return ("{album_track_num} - {track_title}", _ALBUM_TITLE, _ALBUM_TITLE, [1, 2], False)

        def _execute_collection_downloads(self, *a, **k):
            return [tmp_path / "01 - A.flac", tmp_path / "02 - B.flac"]

        def _playlist_for_collection(self, media, file_template, result_paths):
            seen.append((media, file_template, list(result_paths)))

    album = _album()
    Download.items(_Engine(), file_template="{album_title}/{album_track_num} - {track_title}", media=album)
    assert seen == [(album, "{album_track_num} - {track_title}", [tmp_path / "01 - A.flac", tmp_path / "02 - B.flac"])]


def test_the_sort_decision_reads_the_same_on_the_raw_and_the_collection_template():
    """items() decides sort-by-number from the collection-formatted template;
    the merge hands the extracted step the RAW album template. The item tokens
    the decision reads are only substituted per track, so an album-level
    format leaves them in place and both spellings answer alike."""
    raw = "{album_artist}/[{album_year}] {album_title}/{album_track_num}. {artist_name} - {track_title}"
    album = _album()
    album.artists = [SimpleNamespace(name="Someone", roles=None)]
    album.artist = SimpleNamespace(name="Someone")
    album.release_date = None
    formatted = format_path_media(raw, album)
    assert "{album_track_num}" in formatted
    assert ("album_track_num" in raw) == ("album_track_num" in formatted)


def test_landed_paths_keep_submission_order_and_skip_what_landed_nothing():
    """The engine's own collection rule, now callable on its own: submission
    order; cancelled and crashed futures skipped; an empty path skipped; a
    failed item's aimed-for path skipped (nothing was written there)."""
    from concurrent.futures import Future

    def _done(value):
        f = Future()
        f.set_result(value)
        return f

    def _crashed():
        f = Future()
        f.set_exception(RuntimeError("boom"))
        return f

    def _cancelled():
        f = Future()
        f.cancel()
        return f

    futs = [
        _done((True, "/x/03.flac")),
        _crashed(),
        _done((True, "")),
        _cancelled(),
        _done((False, "/x/02.flac")),
        _done((True, "/x/01.flac")),
    ]
    assert Download._landed_paths(futs) == [pathlib.Path("/x/03.flac"), pathlib.Path("/x/01.flac")]


# --------------------------------------------------------------------------- #
# What the merge REPORTS when nothing landed.
#
# The fan-out judged its shortfall from failures and refusals only. A member kept
# out by a setting answers ok=True, so it never reached failures, and its counter
# was never read: a merge that wrote no file at all reported done over an empty
# folder, and dropped its retry plan on the way out.
#
# These drive the real _download_merge_plan and read the real counters off the
# download; what is measured is the verdict the fan-out reaches given them.
# (Exclusions are gone: an Atmos-only member downloads now, so a refusal is the
# one way a member answers ok with no file besides an owned skip.)
# --------------------------------------------------------------------------- #
def _merge_outcome(tmp_path, plan_names, *, refused=0, outcomes=None):
    """Run a merge whose members produce no file, and report what it raised."""
    folder = tmp_path / "out"
    folder.mkdir()
    dl = _WritingDownload(folder, _Settings())
    dl.outcomes = dict(outcomes or {})
    dl.unavailable_count = refused
    try:
        _run_merge(dl, _plan(*plan_names))
    except Exception as exc:
        return type(exc).__name__, str(exc)
    return None, ""


def test_a_merge_refused_entirely_does_not_report_done(tmp_path):
    """Every member refused by TIDAL: item() answers ok=False with no file and
    the refusal counter absorbs them out of the failure count. Nothing was
    written, so the plain-album rule holds here too: never a finished album
    over an empty folder."""
    names = ["a.flac", "b.flac", "c.flac"]
    kind, msg = _merge_outcome(tmp_path, names, refused=3, outcomes={i: (False, "") for i in range(1, len(names) + 1)})
    assert kind is not None, "a merge that wrote nothing at all reported success"
    assert "not available on TIDAL" in msg, msg


def test_a_merge_that_only_had_some_members_refused_still_finishes(tmp_path):
    """The other side, and it must not change: one member refused, the rest
    written, so the album has something to show for itself and finishes with
    the refusal named in its status line rather than failing."""
    names = ["a.flac", "b.flac", "c.flac"]
    outcomes = {1: (False, "")}
    kind, msg = _merge_outcome(tmp_path, names, refused=1, outcomes=outcomes)
    assert kind is None, f"a merge that wrote two of three files failed instead of finishing: {kind} {msg}"


def test_a_merge_of_members_you_already_own_still_finishes(tmp_path):
    """An owned member answers ok=True with no path too, and it must NOT count
    toward "nothing landed": its file is on disk, which is a real success. This is
    the case the new sum would break if it were written as "no path means no
    file"."""
    names = ["a.flac", "b.flac"]
    kind, msg = _merge_outcome(tmp_path, names, outcomes={i: (True, "") for i in range(1, len(names) + 1)})
    assert kind is None, f"a merge whose every member was already on disk reported a failure: {kind} {msg}"
