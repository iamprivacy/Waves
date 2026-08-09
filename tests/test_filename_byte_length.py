"""255 is a byte limit on real filesystems, not a character limit.

Both places that trim a name to fit counted characters: the numbered-copy suffix
and the hidden staging name the cross-filesystem move writes through. A title in
CJK, Cyrillic or emoji costs two to four bytes per character, so a name that
measured well inside 255 characters was far past 255 bytes, and the move failed
with ENAMETOOLONG after the download had already finished. Every retry failed
identically, so the track was simply lost.
"""

import os
import pathlib

from tidaler.constants import FILENAME_LENGTH_MAX
from tidaler.download import _staging_path
from tidaler.helper.path import _path_with_unique_suffix, truncate_to_byte_limit

CJK = "曲"  # three bytes in UTF-8
EMOJI = "🎧"  # four bytes


def _name_bytes(path_file: pathlib.Path) -> int:
    return len(os.fsencode(path_file.name))


class TestTheNumberedSuffixFitsInBytes:
    def test_a_long_multibyte_stem_is_trimmed_to_the_byte_cap(self):
        path_file = pathlib.Path("/music") / (CJK * 250 + ".flac")

        result = _path_with_unique_suffix(path_file, "_01")

        assert _name_bytes(result) <= FILENAME_LENGTH_MAX
        assert result.name.endswith("_01.flac"), "the suffix is never what gets trimmed"
        assert result.parent == path_file.parent

    def test_an_emoji_stem_is_trimmed_too(self):
        path_file = pathlib.Path("/music") / (EMOJI * 200 + ".flac")

        result = _path_with_unique_suffix(path_file, "_99")

        assert _name_bytes(result) <= FILENAME_LENGTH_MAX
        assert result.name.endswith("_99.flac")

    def test_a_plain_ascii_name_is_untouched(self):
        path_file = pathlib.Path("/music/Song.flac")

        assert _path_with_unique_suffix(path_file, "_01").name == "Song_01.flac"

    def test_a_long_ascii_stem_keeps_the_old_answer(self):
        path_file = pathlib.Path("/music") / ("a" * 300 + ".flac")

        result = _path_with_unique_suffix(path_file, "_01")

        assert len(result.name) <= FILENAME_LENGTH_MAX
        assert _name_bytes(result) <= FILENAME_LENGTH_MAX


class TestTheStagingNameFitsInBytes:
    def test_a_long_multibyte_destination_stages_within_the_cap(self):
        destination = pathlib.Path("/music") / (CJK * 250 + ".flac")

        assert _name_bytes(_staging_path(destination)) <= FILENAME_LENGTH_MAX

    def test_a_long_ascii_destination_stages_within_the_cap(self):
        destination = pathlib.Path("/music") / ("a" * 250 + ".flac")

        assert _name_bytes(_staging_path(destination)) <= FILENAME_LENGTH_MAX

    def test_the_staging_name_is_hidden_and_marked_temporary(self):
        staged = _staging_path(pathlib.Path("/music/Song.flac"))

        assert staged.name.startswith(".Song.flac.")
        assert staged.name.endswith(".tmp")
        assert staged.parent == pathlib.Path("/music")

    def test_two_stagings_of_one_name_never_collide(self):
        destination = pathlib.Path("/music/Song.flac")

        assert _staging_path(destination) != _staging_path(destination)


class TestTruncateToByteLimit:
    def test_it_cuts_on_character_boundaries(self):
        result = truncate_to_byte_limit(CJK * 10, 10)

        assert len(os.fsencode(result)) <= 10
        assert result == CJK * 3

    def test_a_short_value_comes_back_whole(self):
        assert truncate_to_byte_limit("Song", 255) == "Song"

    def test_a_limit_of_nothing_leaves_nothing(self):
        assert truncate_to_byte_limit("Song", 0) == ""
