"""A title made only of illegal characters still gets its own folder.

XXXTENTACION's album is called "?", which every filesystem rejects outright:
the whole title sanitizes to "", the empty path segment is dropped, and the
album has no folder of its own. The stand-in setting cures that ("?" becomes
"-"), but the layout guard that keeps an existing library from being
restructured read the OLD spelling's folder, which in this shape is the artist
folder one level up. An artist folder exists as soon as the first track lands,
so the guard fired on every following track and the album's tracks scattered
loose into the artist folder (issue #16).

An ancestor existing is no evidence of anything. Only a file already sitting
in the old place is, and only for itself: it stays where it is (nothing on
disk ever moves), while everything not downloaded yet gets the folder.
"""

from __future__ import annotations

import pathlib
import threading
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from tidalapi import Album, Track

from tidaler.download import Download
from tidaler.helper.path import format_path_media

_ARTIST = "XXXTENTACION"
_TEMPLATE = "{album_artist}/{album_title}/{album_track_num}. {track_title}"


def _track(album_title: str = "?", title: str = "Track") -> Track:
    t = Track.__new__(Track)
    t.id = 1
    t.name = title
    t.version = None
    t.full_name = title
    t.explicit = False
    t.track_num = 1
    t.volume_num = 1
    t.artists = [SimpleNamespace(name=_ARTIST)]
    t.artist = SimpleNamespace(name=_ARTIST)
    album = Album.__new__(Album)
    album.id = 1
    album.name = album_title
    album.artists = [SimpleNamespace(name=_ARTIST, roles=None)]
    album.artist = SimpleNamespace(name=_ARTIST)
    album.num_tracks = 1
    album.num_volumes = 1
    album.release_date = datetime(2018, 3, 16)
    t.album = album
    return t


def _make_download(base: pathlib.Path, replacement: str = "-") -> Download:
    dl = Download(
        tidal_obj=MagicMock(),
        skip_existing=True,
        path_base=str(base),
        fn_logger=MagicMock(),
        progress=MagicMock(),
    )
    dl.settings = MagicMock()
    dl.settings.data.filename_illegal_replacement = replacement
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()
    return dl


class TestTheStandInReachesTheFolder:
    def test_a_wholly_illegal_title_becomes_the_stand_in(self):
        assert format_path_media(_TEMPLATE, _track(), illegal_replacement="-") == f"{_ARTIST}/-/1. Track"

    def test_without_a_stand_in_the_segment_still_collapses(self):
        # The shipped default is plain removal, and an empty segment is
        # dropped so the path cannot turn absolute; unchanged here on purpose.
        assert format_path_media(_TEMPLATE, _track()) == f"{_ARTIST}/1. Track"


class TestTheTracksStayInTheAlbumFolder:
    def _paths(self, base: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
        standin = base / _ARTIST / "-" / "1. Track.flac"
        dropped = base / _ARTIST / "1. Track.flac"
        return standin, dropped

    def test_a_fresh_library_gets_the_stand_in_folder(self, tmp_path):
        dl = _make_download(tmp_path)
        standin, dropped = self._paths(tmp_path)

        assert dl._keep_existing_layout(standin, dropped, dropped) == standin

    def test_an_existing_artist_folder_does_not_swallow_the_album(self, tmp_path):
        # Issue #16: from the second track on, the artist folder existed and
        # the guard read it as "this library uses the old spelling".
        dl = _make_download(tmp_path)
        standin, dropped = self._paths(tmp_path)
        (tmp_path / _ARTIST).mkdir()

        assert dl._keep_existing_layout(standin, dropped, dropped) == standin

    def test_a_track_already_loose_in_the_artist_folder_stays_there(self, tmp_path):
        # Nothing on disk moves or duplicates: the one file downloaded under
        # the old layout keeps its place, and only its place.
        dl = _make_download(tmp_path)
        standin, dropped = self._paths(tmp_path)
        (tmp_path / _ARTIST).mkdir()
        dropped.write_bytes(b"x")

        assert dl._keep_existing_layout(standin, dropped, dropped) == dropped

    def test_a_sibling_folder_under_an_older_spelling_still_wins(self, tmp_path):
        # The guard's real job (issue #15) is untouched: a folder at the same
        # level, spelled the old way, keeps receiving downloads.
        dl = _make_download(tmp_path)
        tidy = tmp_path / "The Better Life Dead Love" / "Song.flac"
        legacy = tmp_path / "The Better Life  Dead Love" / "Song.flac"
        legacy.parent.mkdir()

        assert dl._keep_existing_layout(tidy, legacy).parent == legacy.parent


class TestTheCollectionFolderIsChosenTheSameWay:
    _STANDIN = f"{_ARTIST}/-/" + "{album_track_num}. {track_title}"
    _DROPPED = f"{_ARTIST}/" + "{album_track_num}. {track_title}"

    def test_an_existing_artist_folder_is_not_read_as_an_old_layout(self, tmp_path):
        dl = _make_download(tmp_path)
        (tmp_path / _ARTIST).mkdir()

        chosen = dl._keep_existing_collection_layout(self._STANDIN, self._DROPPED, self._DROPPED)

        assert chosen == self._STANDIN

    def test_a_sibling_folder_under_an_older_spelling_still_wins(self, tmp_path):
        dl = _make_download(tmp_path)
        tidy = "Three Doors Down/The Better Life Dead Love/{track_title}"
        legacy = "Three Doors Down/The Better Life  Dead Love/{track_title}"
        (tmp_path / "Three Doors Down" / "The Better Life  Dead Love").mkdir(parents=True)

        assert dl._keep_existing_collection_layout(tidy, legacy) == legacy

    def test_a_fresh_library_gets_the_preferred_spelling(self, tmp_path):
        dl = _make_download(tmp_path)

        assert dl._keep_existing_collection_layout(self._STANDIN, self._DROPPED) == self._STANDIN
