"""A nasty-name battery over the four functions every library name funnels through.

Issues #15 and #16 both came out of one name shape nobody had tried, so this
module asserts INVARIANTS rather than specific spellings: whatever the four
funnel functions decide to call a thing, the result has to stay under the
download folder, be openable on a real filesystem, and never quietly become
another track's name.

The names are the ones that actually break things: titles that are nothing but
rejected characters (XXXTENTACION's "?"), Windows device names, trailing dots
and spaces, both unicode normalizations of one word, case twins, right-to-left
text, emoji, names at and over the byte cap, and pairs that collide only once a
stand-in has been written into them ("A?B" with "?" mapped to "-" against a real
"A-B").
"""

from __future__ import annotations

import os
import pathlib
import threading
import unicodedata
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from tidalapi import Album, Track

from waves.constants import FILENAME_LENGTH_MAX
from waves.download import Download
from waves.helper.path import (
    file_unique_suffix,
    format_path_media,
    name_comparison_key,
    path_file_sanitize,
    path_file_uniquify,
    safe_filename_replacement_map,
)

NASTY_NAMES = [
    "?",
    "*",
    '"',
    "<>",
    "|",
    "///",
    "...",
    "..",
    ".",
    " ",
    "",
    "CON",
    "NUL.txt",
    "aux",
    "trailing dot.",
    "trailing space ",
    " leading space",
    "a" * FILENAME_LENGTH_MAX,
    "a" * (FILENAME_LENGTH_MAX + 45),
    "曲" * 200,
    "🎧" * 120,
    unicodedata.normalize("NFC", "Café"),
    unicodedata.normalize("NFD", "Café"),
    "CAFÉ",
    "café",
    "‮evil‬",  # right-to-left override
    "AC/DC",
    "Rarities: Live",
    "A?B",
    "A-B",
    "\t\n\r",
    "..\\..\\windows",
    "../../etc/passwd",
]

STAND_INS = safe_filename_replacement_map({"?": "-", ":": " · ", "/": ""})


def _track(title: str, album_title: str = "Album", artist: str = "Artist") -> Track:
    t = Track.__new__(Track)
    t.id = 1
    t.name = title
    t.version = None
    t.full_name = title
    t.explicit = False
    t.track_num = 1
    t.volume_num = 1
    t.artists = [SimpleNamespace(name=artist)]
    t.artist = SimpleNamespace(name=artist)
    album = Album.__new__(Album)
    album.id = 1
    album.name = album_title
    album.artists = [SimpleNamespace(name=artist, roles=None)]
    album.artist = SimpleNamespace(name=artist)
    album.num_tracks = 1
    album.num_volumes = 1
    album.release_date = datetime(2000, 2, 8)
    t.album = album

    return t


def _make_download(base: pathlib.Path) -> Download:
    dl = Download(
        tidal_obj=MagicMock(),
        skip_existing=True,
        path_base=str(base),
        fn_logger=MagicMock(),
        progress=MagicMock(),
    )
    dl.settings = MagicMock()
    dl.settings.data.filename_illegal_replacement = "-"
    dl.settings.data.filename_illegal_map = dict(STAND_INS)
    dl.settings.data.filename_delimiter_artist = ", "
    dl.settings.data.filename_delimiter_album_artist = ", "
    dl.settings.data.use_primary_album_artist = False
    dl.settings.data.album_track_num_pad_min = 1
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()

    return dl


def _destination(base: pathlib.Path, title: str, album: str = "Album", artist: str = "Artist") -> pathlib.Path:
    """The path the engine would build for a track with these names."""
    relative = format_path_media(
        "{artist_name}/{album_title}/{track_title}",
        _track(title, album, artist),
        illegal_replacement="-",
        illegal_map=dict(STAND_INS),
    )

    return path_file_sanitize((base / (relative + ".flac")).absolute(), adapt=True)


def _segments_are_sane(path_file: pathlib.Path, base: pathlib.Path) -> None:
    relative = path_file.relative_to(base)

    for segment in relative.parts:
        assert segment, "an empty path segment cannot be created"
        assert len(os.fsencode(segment)) <= FILENAME_LENGTH_MAX, f"segment over the byte cap: {segment!r}"
        assert segment not in (".", ".."), "a traversal segment can never survive"
        assert "/" not in segment
        assert "\x00" not in segment


class TestEveryNastyNameProducesAUsablePath:
    @pytest.mark.parametrize("title", NASTY_NAMES)
    def test_the_path_stays_under_the_download_folder(self, tmp_path, title):
        destination = _destination(tmp_path, title)

        assert destination.is_absolute()
        assert destination.is_relative_to(tmp_path), f"{title!r} escaped the download folder"

    @pytest.mark.parametrize("title", NASTY_NAMES)
    def test_every_segment_is_creatable(self, tmp_path, title):
        destination = _destination(tmp_path, title)
        _segments_are_sane(destination, tmp_path)

    @pytest.mark.parametrize("title", NASTY_NAMES)
    def test_the_path_can_really_be_written(self, tmp_path, title):
        # The invariants above are about the string; this one asks the actual
        # filesystem, which is the only opinion that counts in the end.
        destination = _destination(tmp_path, title)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"audio")

        assert destination.is_file()

    @pytest.mark.parametrize("title", NASTY_NAMES)
    def test_the_audio_extension_survives(self, tmp_path, title):
        assert _destination(tmp_path, title).suffix == ".flac"


class TestNastyNamesAtEveryLevel:
    @pytest.mark.parametrize("name", NASTY_NAMES)
    def test_the_same_nasty_name_as_artist_album_and_track(self, tmp_path, name):
        # Issue #16 was an ALBUM name, not a track title, and the artist folder
        # is the level a lost segment falls back onto.
        destination = _destination(tmp_path, name, album=name, artist=name)

        assert destination.is_relative_to(tmp_path)
        _segments_are_sane(destination, tmp_path)

        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"audio")

        assert destination.is_file()

    @pytest.mark.parametrize("name", NASTY_NAMES)
    def test_a_long_name_at_every_level_still_lands(self, tmp_path, name):
        # Three long segments plus a long title is where the whole-path cap
        # bites, not any single segment's own limit.
        long_name = (name or "x") * 40
        destination = _destination(tmp_path, long_name, album=long_name, artist=long_name)

        assert destination.is_relative_to(tmp_path)
        _segments_are_sane(destination, tmp_path)

        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"audio")

        assert destination.is_file()


class TestDistinctTitlesNeverShareOneFile:
    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("A?B", "A-B"),  # the stand-in writes one title into the other
            ("Rarities: Live", "Rarities · Live"),
            ("Intro", "intro"),  # case twins, one file on APFS and NTFS
            (unicodedata.normalize("NFC", "Café"), unicodedata.normalize("NFD", "Café")),
            ("AC/DC", "ACDC"),
            ("Song", "Song."),
            ("Song", "Song "),
        ],
    )
    def test_a_colliding_pair_gets_two_files(self, tmp_path, left, right):
        dl = _make_download(tmp_path)
        first = _destination(tmp_path, left)
        second = _destination(tmp_path, right)

        # Whatever the two titles sanitize to, running them through the claim
        # mechanism in turn has to leave two openable files.
        with dl._names_reserved_lock:
            first_picked = path_file_uniquify(first, names_taken=dl._names_reserved)
            dl._names_reserved[str(first_picked)] = ("1", 1)
            second_picked = path_file_uniquify(second, names_taken=dl._names_reserved)
            dl._names_reserved[str(second_picked)] = ("2", 1)

        assert first_picked is not None
        assert second_picked is not None
        assert name_comparison_key(str(first_picked)) != name_comparison_key(str(second_picked))

        for path_file in (first_picked, second_picked):
            path_file.parent.mkdir(parents=True, exist_ok=True)
            path_file.write_bytes(b"audio")

        assert first_picked.is_file()
        assert second_picked.is_file()
        assert first_picked.read_bytes() == second_picked.read_bytes() == b"audio"


class TestTheUniquifierItself:
    @pytest.mark.parametrize("title", NASTY_NAMES)
    def test_a_uniquified_name_is_still_within_the_byte_cap(self, tmp_path, title):
        destination = _destination(tmp_path, title)
        picked = path_file_uniquify(destination, names_taken={str(destination)})

        assert picked is not None
        assert picked != destination
        assert len(os.fsencode(picked.name)) <= FILENAME_LENGTH_MAX
        assert picked.parent == destination.parent

    @pytest.mark.parametrize("title", NASTY_NAMES)
    def test_a_uniquified_name_can_be_written(self, tmp_path, title):
        destination = _destination(tmp_path, title)
        picked = path_file_uniquify(destination, names_taken={str(destination)})
        picked.parent.mkdir(parents=True, exist_ok=True)
        picked.write_bytes(b"audio")

        assert picked.is_file()

    def test_a_free_name_needs_no_suffix(self, tmp_path):
        assert file_unique_suffix(tmp_path / "Song.flac") == ""


class TestTheLayoutGuardOnNastyNames:
    @pytest.mark.parametrize("title", NASTY_NAMES)
    def test_an_older_spelling_never_moves_the_file_out_of_the_album(self, tmp_path, title):
        # _keep_existing_layout may prefer an older spelling, but only one at
        # the same depth: issue #16 was exactly an "older" candidate that had
        # lost a folder and pointed at the artist directory instead.
        dl = _make_download(tmp_path)
        preferred = _destination(tmp_path, title)
        older = path_file_sanitize(
            (
                tmp_path
                / (
                    format_path_media(
                        "{artist_name}/{album_title}/{track_title}",
                        _track(title),
                    )
                    + ".flac"
                )
            ).absolute(),
            adapt=True,
        )

        chosen = dl._keep_existing_layout(preferred, older)

        assert chosen.is_relative_to(tmp_path)
        assert len(chosen.parts) == len(preferred.parts), "a track never changes depth to match an old spelling"
        _segments_are_sane(chosen, tmp_path)
