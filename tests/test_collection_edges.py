"""Four things a collection download got wrong at its edges.

* A mix is the one collection whose items() hands back tracks and videos
  together, and nothing filtered it: full music videos landed in the mix folder
  with the "music videos" switch off everywhere.
* The m3u's order fix compared this run's paths to the folder listing with raw
  string equality, so on a filesystem that stores names in the other unicode
  normalization (HFS+ externals, several NAS shares) every accented title
  missed the folder set, the counts disagreed and the order silently stood
  down: the issue #22 symptom, on the libraries most likely to hold it.
* An empty playlist reached the outcome tallies as (0, 0, 0), which is also
  what a free account's every-stream-refused run looks like, so the row went
  red "Failed: no tracks were downloaded" over a collection that had nothing to
  do. The free-account verdict is settled behaviour and stays exactly as it is.
* And a symlinked entry on a Windows mapped drive raised ValueError out of the
  relative-path computation (the drive resolves to its UNC spelling, so the two
  paths have different anchors), which the OSError handler did not catch: the
  whole job failed at its last step with every track already landed, and the
  hidden temp survived every retry.
"""

from __future__ import annotations

import pathlib
import threading
from types import MethodType
from unittest.mock import MagicMock

import pytest
from tidalapi import Mix, Playlist
from tidalapi.media import Track, Video

from waves.download import Download
from waves.helper.tidal import items_results_all
from waves.waves_ui.backend import _collection_incomplete_reason


def _make_download(tmp_path: pathlib.Path) -> Download:
    dl = Download(
        tidal_obj=MagicMock(),
        skip_existing=True,
        path_base=str(tmp_path),
        fn_logger=MagicMock(),
        progress=MagicMock(),
    )
    dl.settings = MagicMock()
    dl.settings.data.filename_illegal_replacement = ""
    dl.settings.data.filename_illegal_map = None
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()

    return dl


# --------------------------------------------------------------------------- #
# F-15: a mix obeys the music-videos switch like every other collection.
# --------------------------------------------------------------------------- #
def _mix(items):
    mix = Mix.__new__(Mix)
    mix.items = lambda: list(items)

    return mix


def _track(tid):
    t = Track.__new__(Track)
    t.id = tid

    return t


def _video(vid):
    v = Video.__new__(Video)
    v.id = vid

    return v


def test_a_mix_leaves_its_videos_out_when_videos_are_off():
    got = items_results_all(_mix([_track(1), _video(2), _track(3)]), videos_include=False)

    assert [m.id for m in got] == [1, 3]
    assert not any(isinstance(m, Video) for m in got)


def test_a_mix_still_takes_its_videos_when_videos_are_on():
    got = items_results_all(_mix([_track(1), _video(2)]), videos_include=True)

    assert [m.id for m in got] == [1, 2]


def test_a_mix_of_nothing_but_videos_comes_back_empty():
    assert items_results_all(_mix([_video(1), _video(2)]), videos_include=False) == []


def test_a_playlist_is_untouched_by_the_mix_arm():
    """The playlist arm asks .tracks, which is where its own exclusion lives."""
    pl = Playlist.__new__(Playlist)
    pl.tracks = MethodType(lambda self, limit=None, offset=0: [_track(7)] if not offset else [], pl)
    pl.items = MethodType(lambda self, limit=None, offset=0: pytest.fail("videos-off must not call items()"), pl)

    assert [m.id for m in items_results_all(pl, videos_include=False)] == [7]


# --------------------------------------------------------------------------- #
# F-30: the folder's spelling and this run's are one file.
# --------------------------------------------------------------------------- #
NFC = "Caf\u00e9"  # as TIDAL sends it, and as this run wrote it
NFD = "Cafe\u0301"  # as a normalizing filesystem stores it


def test_the_order_survives_a_folder_that_renormalized_the_names(tmp_path):
    dl = _make_download(tmp_path)
    # The folder holds the NFD spellings, the run reports the NFC ones: one
    # file each, spelled two ways.
    (tmp_path / f"{NFD} Two.flac").write_bytes(b"b")
    (tmp_path / f"{NFD} One.flac").write_bytes(b"a")

    written = dl.playlist_populate(
        {tmp_path},
        "My List",
        is_album=False,
        sort_alphabetically=True,
        paths_ordered=[tmp_path / f"{NFC} Two.flac", tmp_path / f"{NFC} One.flac"],
    )

    lines = written[0].read_text(encoding="utf-8").splitlines()
    assert [pathlib.Path(name).stem.split()[-1] for name in lines] == ["Two", "One"], "the list order stood down"


def test_the_m3u_names_files_the_way_the_folder_spells_them(tmp_path):
    """Never this run's spelling: the player has to be able to open them."""
    dl = _make_download(tmp_path)
    (tmp_path / f"{NFD}.flac").write_bytes(b"a")

    written = dl.playlist_populate(
        {tmp_path},
        "My List",
        is_album=False,
        sort_alphabetically=True,
        paths_ordered=[tmp_path / f"{NFC}.flac"],
    )

    assert written[0].read_text(encoding="utf-8").splitlines() == [f"{NFD}.flac"]


def test_a_run_that_cannot_account_for_the_folder_still_stands_down(tmp_path):
    """The guard that keeps a partial run from replacing a complete m3u."""
    dl = _make_download(tmp_path)
    (tmp_path / "Alpha.flac").write_bytes(b"a")
    (tmp_path / "Zulu.flac").write_bytes(b"b")

    written = dl.playlist_populate(
        {tmp_path},
        "My List",
        is_album=False,
        sort_alphabetically=True,
        paths_ordered=[tmp_path / "Zulu.flac"],  # a re-download that skipped the other
    )

    assert written[0].read_text(encoding="utf-8").splitlines() == ["Alpha.flac", "Zulu.flac"]


# --------------------------------------------------------------------------- #
# F-31: nothing to do is not something gone wrong.
# --------------------------------------------------------------------------- #
def test_an_empty_collection_is_not_a_failure():
    assert _collection_incomplete_reason(0, 0, 0, 0, False, 0) is None


def test_every_stream_refused_is_still_a_failure():
    """The free-account verdict: same tallies, but the list had items in it."""
    assert _collection_incomplete_reason(0, 0, 0, 0, False, 20) == "no tracks were downloaded"
    # And with no count at all (a caller that cannot say), the old answer.
    assert _collection_incomplete_reason(0, 0, 0, 0, False, None) == "no tracks were downloaded"


def test_an_empty_count_never_covers_a_real_failure():
    assert _collection_incomplete_reason(0, 0, 1, 0, False, 0) == "1 of 1 tracks failed"
    assert _collection_incomplete_reason(0, 0, 0, 0, True, 0) == "this release is not available on TIDAL anymore"


def test_the_engine_reports_the_list_size_to_the_bridge():
    """The hook the bridge reads it through, and the base engine's no-op."""
    from waves.waves_ui.backend import _TrackedDownload

    td = _TrackedDownload.__new__(_TrackedDownload)
    assert Download._note_list_size(td, 5) is None  # base: nothing to report to

    td.list_item_count = None
    _TrackedDownload._note_list_size(td, 0)
    assert td.list_item_count == 0


# --------------------------------------------------------------------------- #
# F-32: a symlink whose target will not relate to its own folder.
# --------------------------------------------------------------------------- #
def test_a_symlink_target_on_another_anchor_falls_back_to_the_link_name(tmp_path, monkeypatch):
    """On Windows a mapped drive resolves to its UNC spelling, so relating the
    two raises ValueError. The link's own name plays just as well."""
    dl = _make_download(tmp_path)
    tracks = tmp_path / "tracks"
    tracks.mkdir()
    real = tracks / "Song.flac"
    real.write_bytes(b"a")
    folder = tmp_path / "list"
    folder.mkdir()
    link = folder / "Song.flac"
    link.symlink_to(real)

    real_resolve = pathlib.Path.resolve

    def resolve(self, strict=False):
        if self == link:
            raise ValueError("'\\\\\\\\server\\\\share' and 'Z:\\\\list' have different anchors")

        return real_resolve(self, strict=strict) if strict else real_resolve(self)

    monkeypatch.setattr(pathlib.Path, "resolve", resolve)

    written = dl.playlist_populate({folder}, "My List", is_album=False, sort_alphabetically=True, paths_ordered=None)

    assert written[0].read_text(encoding="utf-8").splitlines() == ["Song.flac"]
    leftovers = [p.name for p in folder.iterdir() if p.name.startswith(".")]
    assert leftovers == [], f"a hidden temp survived: {leftovers}"


def test_a_relatable_symlink_still_writes_the_relative_path(tmp_path):
    dl = _make_download(tmp_path)
    tracks = tmp_path / "tracks"
    tracks.mkdir()
    real = tracks / "Song.flac"
    real.write_bytes(b"a")
    folder = tmp_path / "list"
    folder.mkdir()
    (folder / "Song.flac").symlink_to(real)

    written = dl.playlist_populate({folder}, "My List", is_album=False, sort_alphabetically=True, paths_ordered=None)

    assert written[0].read_text(encoding="utf-8").splitlines() == [str(pathlib.Path("..") / "tracks" / "Song.flac")]
