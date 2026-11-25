"""'Primary album artist for folders' names folders, not files (issue #68).

The setting used to reach into the file name too: a track by two artists was
saved under the first one only, and every track of a compilation was saved as
"Various Artists - Song". Folders keep answering to the setting; the file name
(everything after the last separator of the template) lists the track's own
artists, or only the first of them with 'Primary artist only in file names'.

Files a release up to 0.2.9 wrote keep their old name: it is still recognized,
but only when the file sitting there is that very track.
"""

from __future__ import annotations

import pathlib
import threading
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from tidalapi import Album, Playlist, Track

import waves.download as download_module
from waves.download import Download
from waves.helper.path import format_path_media
from waves.model.cfg import Settings

_DEFAULT = Settings.__dataclass_fields__["format_track"].default
_FLAT = "{artist_name} - {track_title}"
_PLAYLIST = "Playlists/{playlist_name}/{list_pos}. {artist_name} - {track_title}"


def _track(track_artists: list[str], album_artists: list[str], title: str = "All We Have", tid: int = 7) -> Track:
    t = Track.__new__(Track)
    t.id = tid
    t.name = title
    t.version = None
    t.full_name = title
    t.explicit = False
    t.track_num = 5
    t.volume_num = 1
    t.media_metadata_tags = []
    t.artists = [SimpleNamespace(name=name) for name in track_artists]
    t.artist = t.artists[0]
    album = Album.__new__(Album)
    album.id = 1
    album.name = "times"
    album.explicit = False
    album.artists = [SimpleNamespace(name=name, roles=None) for name in album_artists]
    album.artist = album.artists[0]
    album.num_tracks = 10
    album.num_volumes = 1
    album.release_date = datetime(2021, 2, 19)
    t.album = album
    return t


_COLLAB = (["SG Lewis", "Lastlings"], ["SG Lewis"])
_COMPILATION = (["Real Artist", "Guest"], ["Various Artists"])


def _parts(template: str, media: Track, **kw) -> tuple[str, str]:
    """(folders, file name) the template gives ``media``."""
    relative = format_path_media(template, media, **{"use_primary_album_artist": True, **kw})
    folders, _, name = relative.rpartition("/")
    return folders, name


class TestTheSettingStopsAtTheFileName:
    def test_folder_is_the_album_artist_and_the_file_lists_the_track_artists(self):
        folders, name = _parts(_DEFAULT, _track(*_COLLAB))
        assert folders == "SG Lewis/[2021] times"
        assert name == "05. SG Lewis, Lastlings - All We Have"

    def test_a_template_with_no_folders_lists_the_track_artists(self):
        assert _parts(_FLAT, _track(*_COLLAB)) == ("", "SG Lewis, Lastlings - All We Have")

    def test_the_separator_setting_still_joins_them(self):
        assert _parts(_FLAT, _track(*_COLLAB), delimiter_artist=" & ")[1] == "SG Lewis & Lastlings - All We Have"

    def test_a_compilation_track_is_not_filed_as_various_artists(self):
        folders, name = _parts(_DEFAULT, _track(*_COMPILATION))
        assert folders.startswith("Various Artists/")
        assert name == "05. Real Artist, Guest - All We Have"

    def test_a_playlist_file_names_the_track_artists(self):
        playlist = Playlist.__new__(Playlist)
        playlist.name = "Mine"
        kw = {"use_primary_album_artist": True}
        baked = format_path_media(_PLAYLIST, playlist, **kw)
        assert format_path_media(baked, _track(*_COMPILATION), 1, 3, 9, **kw) == (
            "Playlists/Mine/3. Real Artist, Guest - All We Have"
        )

    def test_setting_off_is_what_it_always_was(self):
        folders, name = _parts(_DEFAULT, _track(*_COLLAB), use_primary_album_artist=False)
        assert folders == "SG Lewis, Lastlings/[2021] times"
        assert name == "05. SG Lewis, Lastlings - All We Have"


class TestPrimaryArtistOnlyInFileNames:
    def test_the_file_keeps_only_the_first_track_artist(self):
        folders, name = _parts(_DEFAULT, _track(*_COLLAB), primary_artist_only_in_filename=True)
        assert (folders, name) == ("SG Lewis/[2021] times", "05. SG Lewis - All We Have")

    def test_it_is_the_tracks_artist_never_the_albums(self):
        _, name = _parts(_DEFAULT, _track(*_COMPILATION), primary_artist_only_in_filename=True)
        assert name == "05. Real Artist - All We Have"

    def test_folders_do_not_follow_it(self):
        folders, name = _parts(
            _DEFAULT, _track(*_COLLAB), use_primary_album_artist=False, primary_artist_only_in_filename=True
        )
        assert folders == "SG Lewis, Lastlings/[2021] times"
        assert name == "05. SG Lewis - All We Have"


def test_the_legacy_switch_reproduces_the_old_file_name():
    kw = {"legacy_filename_artist": True, "primary_artist_only_in_filename": True}
    assert _parts(_DEFAULT, _track(*_COMPILATION), **kw)[1] == "05. Various Artists - All We Have"


def _make_download(base: pathlib.Path) -> Download:
    dl = Download(
        tidal_obj=MagicMock(),
        skip_existing=True,
        path_base=str(base),
        fn_logger=MagicMock(),
        progress=MagicMock(),
    )
    dl.settings = MagicMock()
    dl.settings.data.format_track = _FLAT
    dl.settings.data.filename_illegal_replacement = ""
    dl.settings.data.filename_illegal_map = {}
    dl.settings.data.filename_delimiter_artist = ", "
    dl.settings.data.filename_delimiter_album_artist = ", "
    dl.settings.data.use_primary_album_artist = True
    dl.settings.data.filename_primary_artist_only = False
    dl.settings.data.album_track_num_pad_min = 1
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()
    return dl


_OLD = "Various Artists - All We Have"
_NEW = "Real Artist, Guest - All We Have"


class TestALibraryFromBeforeTheFixIsNotFetchedAgain:
    @pytest.fixture
    def ids(self, monkeypatch):
        """Item ids by file name, standing in for the tag each file carries."""
        table: dict[str, str] = {}
        monkeypatch.setattr(download_module, "read_item_id", lambda path: table.get(pathlib.Path(path).name, ""))
        return table

    @pytest.fixture
    def ext(self, tmp_path) -> str:
        """The extension the engine guesses for this track (the stub settings decide it)."""
        return _make_download(tmp_path)._destination_path(_track(*_COMPILATION), _FLAT, None, 0, 0)[1]

    def _file(self, base: pathlib.Path, stem: str, ext: str) -> pathlib.Path:
        path = base / (stem + ext)
        path.write_bytes(b"x" * 64)
        return path

    def _destination(self, base: pathlib.Path) -> pathlib.Path:
        return _make_download(base)._destination_path(_track(*_COMPILATION), _FLAT, None, 0, 0)[0]

    def test_this_tracks_old_file_is_the_destination(self, tmp_path, ids, ext):
        old = self._file(tmp_path, _OLD, ext)
        ids[old.name] = "7"
        assert self._destination(tmp_path) == old

    def test_an_untagged_old_file_is_still_taken_for_it(self, tmp_path, ids, ext):
        old = self._file(tmp_path, _OLD, ext)
        assert self._destination(tmp_path) == old

    def test_another_songs_file_under_the_old_name_is_left_alone(self, tmp_path, ids, ext):
        ids[self._file(tmp_path, _OLD, ext).name] = "999"
        assert self._destination(tmp_path).name == _NEW + ext

    def test_a_file_at_the_new_name_wins(self, tmp_path, ids, ext):
        ids[self._file(tmp_path, _OLD, ext).name] = "7"
        new = self._file(tmp_path, _NEW, ext)
        assert self._destination(tmp_path) == new

    def test_a_fresh_library_gets_the_new_name(self, tmp_path, ids, ext):
        assert self._destination(tmp_path).name == _NEW + ext

    def test_the_track_folder_copy_is_found_under_its_old_name_too(self, tmp_path, ids):
        old = self._file(tmp_path, _OLD, ".flac")
        ids[old.name] = "7"
        assert _make_download(tmp_path)._track_dir_path(_track(*_COMPILATION), ".flac") == old
