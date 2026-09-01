"""255 is a byte limit on real filesystems, not a character limit.

Both places that trim a name to fit counted characters: the numbered-copy suffix
and the hidden staging name the cross-filesystem move writes through. A title in
CJK, Cyrillic or emoji costs two to four bytes per character, so a name that
measured well inside 255 characters was far past 255 bytes, and the move failed
with ENAMETOOLONG after the download had already finished. Every retry failed
identically, so the track was simply lost.

The name cap is only half of it: the WHOLE path has a platform cap too, and a
destination the sanitizer had just fitted to that cap failed the same way once
a numbered suffix was inserted into it. Both caps are pinned here.
"""

import os
import pathlib

from waves.constants import FILENAME_LENGTH_MAX
from waves.download import _staging_path
from waves.helper.path import (
    PATH_LENGTH_MAX,
    _path_length,
    _path_with_unique_suffix,
    file_unique_suffix,
    path_file_uniquify,
    truncate_to_byte_limit,
    unique_variant_name,
)

CJK = "曲"  # three bytes in UTF-8
EMOJI = "🎧"  # four bytes


def _name_bytes(path_file: pathlib.Path) -> int:
    return len(os.fsencode(path_file.name))


def _virtual_parent(length: int) -> pathlib.Path:
    """A folder path that measures exactly ``length`` the way the platform counts.

    Nothing here touches the disk: the budget is a pure computation, so the
    folder never has to exist. Read PATH_LENGTH_MAX from the module rather than
    hardcoding 1023, so the same test binds the whole-path term on Windows (259)
    and on POSIX alike.
    """
    parent = pathlib.Path("/" + "p" * (length - 1))
    assert _path_length(parent) == length
    return parent


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


class TestTheWholePathFitsThePlatformCapToo:
    """The name cap is not the only cap: the WHOLE path has one as well.

    Every test above measures the name with a six-byte parent, so the whole-path
    term of the budget is never the binding one there. Here the parent is built
    long enough that it is. The sanitizer parks a long destination exactly at
    PATH_LENGTH_MAX; inserting "_01" with nothing re-measuring the full path
    put it three over, the move out of staging failed ENAMETOOLONG on every
    retry, and the copy scan spelled its candidates differently from the writer.
    """

    def test_a_long_parent_makes_the_stem_give_way_to_the_path_cap(self):
        parent = _virtual_parent(PATH_LENGTH_MAX - 40)
        path_file = parent / ("s" * 80 + ".flac")
        # Precondition, so this can never pass by accident on a platform whose
        # cap makes the parent too short to bind: the untrimmed name is well
        # inside the 255-byte name cap, and only the whole path is over.
        untrimmed = parent / ("s" * 80 + "_01.flac")
        assert _name_bytes(untrimmed) < FILENAME_LENGTH_MAX
        assert _path_length(untrimmed) > PATH_LENGTH_MAX

        result = _path_with_unique_suffix(path_file, "_01")

        assert _path_length(result) <= PATH_LENGTH_MAX
        assert result.name.endswith("_01.flac"), "the suffix is never what gets trimmed"
        assert len(result.stem) < 80 + len("_01"), "the stem, not the suffix, paid for the overage"
        assert result.parent == parent
        # And it gave up no more than it had to: one more character would not fit.
        assert _path_length(parent / ("s" + result.name)) > PATH_LENGTH_MAX

    def test_a_path_already_at_the_cap_stays_within_it_after_the_suffix(self):
        # The sanitizer trims a destination to exactly PATH_LENGTH_MAX; the
        # numbered copy of that destination has to fit under the same cap.
        name = "s" * 15 + ".flac"
        parent = _virtual_parent(PATH_LENGTH_MAX - 1 - len(name))
        path_file = parent / name
        assert _path_length(path_file) == PATH_LENGTH_MAX
        # Raw insertion is what used to happen, and it lands three over the cap.
        assert _path_length(parent / ("s" * 15 + "_01.flac")) == PATH_LENGTH_MAX + len("_01")

        result = _path_with_unique_suffix(path_file, "_01")

        assert _path_length(result) <= PATH_LENGTH_MAX
        assert result.name.endswith("_01.flac")
        assert len(result.stem) < len("s" * 15 + "_01"), "the stem gave up the three bytes"

    def test_a_short_parent_still_binds_on_the_filename_term(self):
        # Regression anchor for the min(): with a short parent the 255-byte
        # name cap is what limits the stem, exactly as the tests above pin.
        path_file = pathlib.Path("/music") / ("s" * 250 + ".flac")

        result = _path_with_unique_suffix(path_file, "_01")

        assert _name_bytes(result) == FILENAME_LENGTH_MAX
        assert result.name.endswith("_01.flac")

    def test_a_multibyte_stem_near_the_path_cap_trims_on_whole_characters(self):
        parent = _virtual_parent(PATH_LENGTH_MAX - 40)
        path_file = parent / (CJK * 30 + ".flac")
        assert _path_length(parent / (CJK * 30 + "_01.flac")) > PATH_LENGTH_MAX

        result = _path_with_unique_suffix(path_file, "_01")

        assert _path_length(result) <= PATH_LENGTH_MAX
        assert result.name.endswith("_01.flac")
        kept = result.name.removesuffix("_01.flac")
        assert kept and set(kept) == {CJK}, "no character was split"
        assert len(kept) < 30, "the stem was trimmed on the byte budget"
        # One more whole character would not fit, so the budget is spent in
        # bytes and not rounded down to some safe character count.
        assert _path_length(parent / (CJK + result.name)) > PATH_LENGTH_MAX

    def test_the_writer_and_the_copy_scan_spell_the_numbered_name_alike(self):
        # unique_variant_name is what the copy scan in download.py probes the
        # folder listing with; path_file_uniquify is what the writer
        # lands on. Both route through the same budget, and both have to land
        # within the path cap, or the copy on disk is missed and re-downloaded.
        parent = _virtual_parent(PATH_LENGTH_MAX - 40)
        path_file = parent / ("s" * 80 + ".flac")

        # The base name is claimed by an in-flight sibling, so the first
        # numbered variant is the answer; nothing here exists on disk.
        assert file_unique_suffix(path_file, names_taken={str(path_file)}) == "_01"
        written = path_file_uniquify(path_file, names_taken={str(path_file)})

        assert written is not None
        assert written.name == unique_variant_name(path_file, "_01")
        assert written.parent == parent
        assert _path_length(written) <= PATH_LENGTH_MAX
        # The name both agree on is NOT the raw concatenation the scan used to
        # probe with: that spelling is over the cap and matches nothing on disk.
        assert written.name != "s" * 80 + "_01.flac"
