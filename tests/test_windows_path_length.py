"""The platform's path cap is checked on the whole path, not just the folder.

path_file_sanitize validated the DIRECTORY for length and shortened it when it
did not fit, then joined the file name on without looking again. A folder that
was comfortably valid plus a long track name therefore came back over the cap,
and the download failed at the move after it had already finished.

The numbers differ per platform (260 on Windows, 1024 on macOS and Linux) but
the code is one path, so the repro is built at the running platform's own limit
rather than by pretending to be another one: forcing a platform also forces its
separators, which says nothing about the rule under test.
"""

import pathlib

import pytest
from pathvalidate import sanitize_filepath
from pathvalidate.error import ValidationError

from tidaler.helper.path import path_file_sanitize


def _is_valid(path_file: pathlib.Path) -> bool:
    try:
        sanitize_filepath(path_file, validate_after_sanitize=True, platform="auto")
    except ValidationError:
        return False

    return True


def _path_cap() -> int:
    """The running platform's cap, found by probing rather than hard-coded."""
    length = 64
    while length < 1 << 16:
        if not _is_valid(pathlib.Path("/m") / ("a" * length)):
            return length
        length *= 2

    raise AssertionError("no path length cap found")


PATH_CAP = _path_cap()
LONG_NAME = "a" * 200 + ".flac"


def _folder_just_inside_the_cap() -> pathlib.Path:
    """A deep folder that is valid on its own, but not once a long name joins it.

    Built from components of an ordinary size (a single 900-character directory
    would be truncated per component and would prove nothing about the join).
    """
    folder = pathlib.Path("/Music")

    while len(str(folder)) + 51 < PATH_CAP - len(LONG_NAME) + 100:
        folder = folder / ("x" * 50)

    return folder


# A folder well inside the cap, and a name well inside the 255 name cap, whose
# join is over it: the exact shape the old check waved through.
FOLDER = _folder_just_inside_the_cap()


class TestTheWholePathIsRevalidated:
    def test_a_valid_folder_plus_a_long_name_still_fits(self):
        candidate = FOLDER / LONG_NAME

        assert _is_valid(FOLDER), "the folder alone has to be valid, or the old check would have caught it"
        assert not _is_valid(candidate), "the case under test has to be over the cap to begin with"

        result = path_file_sanitize(candidate, adapt=True)

        assert _is_valid(result)

    def test_the_file_keeps_its_extension_and_its_folder(self):
        result = path_file_sanitize(FOLDER / LONG_NAME, adapt=True)

        assert result.suffix == ".flac"
        assert result.parent == FOLDER, "trimming the name is enough; the album folder is shared and stays put"

    def test_a_short_path_is_left_exactly_as_it_is(self):
        candidate = pathlib.Path("/Music/Artist/Album/01 Song.flac")

        assert path_file_sanitize(candidate, adapt=True) == candidate

    def test_an_over_long_folder_still_shortens(self):
        # The behavior that was already there: when the folder itself cannot
        # fit, it is shrunk deepest-first and the file stays under the base.
        candidate = pathlib.Path("/Music") / ("d" * (PATH_CAP + 200)) / "Song.flac"

        result = path_file_sanitize(candidate, adapt=True)

        assert _is_valid(result)
        assert result.parts[:2] == ("/", "Music")

    def test_without_adapt_the_over_long_path_still_raises(self):
        with pytest.raises(ValidationError):
            path_file_sanitize(FOLDER / LONG_NAME, adapt=False)
