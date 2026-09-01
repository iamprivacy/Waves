"""An album named "." gets a folder, and only a name that is nothing but dots
is touched.

Kesha has an album whose title is a single period. Nothing in Waves removed it:
it survived every sanitizer intact and then evaporated in the join that builds
the destination, because "." is what every platform calls "this folder". The
album had no folder at all and its tracks landed loose in the artist folder,
mixed in with everything else that ever landed there (issue #29).

``_no_traversal`` (helper.path) was written for exactly "." and "..", but it
runs over ``Path.parent.parts`` and pathlib has already swallowed the "." by
the time it looks. It still catches "..", which pathlib keeps. So the naming of
a dots-only segment has to happen on the string, before any Path is built,
which is where ``_drop_empty_segments`` sits.

The reporter's rule, pinned below: ONLY a segment that is entirely "." (or
"..") is renamed. ". (Deluxe)", "Album." and "(...) ." keep whatever the
ordinary sanitizer makes of them.
"""

from __future__ import annotations

import pathlib
from datetime import datetime
from types import SimpleNamespace

from tidalapi import Album, Track

from waves.constants import DOT_SEGMENT_STANDIN
from waves.helper.path import format_path_media, path_file_sanitize

_ARTIST = "Kesha"
_TEMPLATE = "{album_artist}/{album_title}/{album_track_num}. {track_title}"


def _track(album_title: str, title: str = "Song", artist: str = _ARTIST) -> Track:
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
    album.release_date = datetime(2023, 1, 1)
    t.album = album
    return t


class TestTheAlbumGetsAFolder:
    def test_a_title_of_one_dot_is_named_not_dropped(self):
        assert format_path_media(_TEMPLATE, _track(".")) == f"{_ARTIST}/{DOT_SEGMENT_STANDIN}/1. Song"

    def test_the_folder_survives_the_join_that_used_to_eat_it(self):
        # The whole defect: pathlib resolves a "." component away while parsing,
        # so the album folder was gone before anything could object to it.
        relative = format_path_media(_TEMPLATE, _track("."))
        joined = pathlib.Path("/base") / (relative + ".flac")

        assert joined.parent == pathlib.Path("/base") / _ARTIST / DOT_SEGMENT_STANDIN
        assert joined.parent != pathlib.Path("/base") / _ARTIST

    def test_the_old_spelling_really_did_lose_the_folder(self):
        # Pins the mechanism rather than trusting the description of it: an
        # unnamed "." collapses, which is what the fix above prevents.
        assert (pathlib.Path("/base") / f"{_ARTIST}/./1. Song.flac").parent == pathlib.Path("/base") / _ARTIST

    def test_every_level_a_template_can_name_is_covered(self):
        # The artist segment has the same hole: with it gone the album folder
        # would sit straight in the download root.
        relative = format_path_media(_TEMPLATE, _track("Rainbow", artist="."))

        assert relative.split("/")[0] == DOT_SEGMENT_STANDIN


class TestOnlyAWholeSegmentIsTouched:
    def test_a_dot_that_opens_a_real_title_is_left_alone(self):
        assert format_path_media(_TEMPLATE, _track(". (Deluxe)")) == f"{_ARTIST}/. (Deluxe)/1. Song"

    def test_a_title_that_merely_ends_in_a_dot_is_not_renamed(self):
        # pathvalidate trims the trailing dot (Windows would too); the point is
        # that it does NOT become the stand-in.
        assert format_path_media(_TEMPLATE, _track("Album.")) == f"{_ARTIST}/Album/1. Song"

    def test_a_title_ending_in_a_spaced_dot_is_not_renamed(self):
        assert DOT_SEGMENT_STANDIN not in format_path_media(_TEMPLATE, _track("(...) ."))


class TestTraversalStaysShut:
    def test_a_title_of_two_dots_cannot_walk_up(self):
        relative = format_path_media(_TEMPLATE, _track(".."))

        assert relative == f"{_ARTIST}/{DOT_SEGMENT_STANDIN}/1. Song"
        assert ".." not in pathlib.Path(relative).parts

    def test_the_second_net_still_stands(self):
        # _no_traversal keeps catching a ".." that reaches path_file_sanitize by
        # any other route; the fix above does not replace it.
        sanitized = path_file_sanitize(pathlib.Path("/base") / _ARTIST / ".." / "1. Song.flac")

        assert ".." not in sanitized.parts

    def test_the_stand_in_is_a_legal_folder_name(self):
        sanitized = path_file_sanitize(pathlib.Path("/base") / _ARTIST / DOT_SEGMENT_STANDIN / "1. Song.flac")

        assert sanitized.parent.name == DOT_SEGMENT_STANDIN
