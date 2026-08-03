"""A formatted media path can never escape the download folder.

THE BUG
-------
``format_path_media`` substitutes each token blind. A token whose value
sanitizes to ``""`` therefore leaves an **empty path component**, and both
default templates open with ``{artist_name}``:

    "{artist_name}/[{album_year}] {album_title}/..."  ->  "/[2024] Album/..."

``Path(path_base) / file_name_relative`` DISCARDS ``path_base`` when the
right-hand operand is absolute. On Windows ``PureWindowsPath`` keeps the drive,
so the track landed at ``C:\\[2024] Album\\...``, outside the download folder,
and the queue reported done. On macOS/Linux the write failed at the volume root
with ``OSError errno 30`` and no explanation.

Artist names that empty out under pathvalidate's UNIVERSAL platform are real:
``?``, ``??``, ``*``, ``<>``, ``|``, ``"``, and names of only dots (trailing
dots are stripped). ``!!!`` and ``M|A|R|R|S`` survive, so this is about the
degenerate cases, not punctuation generally.

``_no_traversal`` fences ``..`` escaping the base and does not address this
shape; there was no ``lstrip`` and no containment check anywhere.

THE FIX drops empty components from the formatted relative path, which keeps it
relative (and therefore inside the base) and also tidies the doubled separator
an emptied mid-template token leaves behind.
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace

import pytest
from tidalapi import Track

from tidaler.helper.path import format_path_media
from tidaler.model.cfg import Settings

# Names that pathvalidate reduces to nothing.
EMPTYING_NAMES = ["?", "??", "*", "<>", "|", '"', "..."]

BASE = pathlib.Path("/base/download")


def _track(artist_name: str) -> Track:
    """A real Track: the formatter's token lookups are isinstance-gated, so a
    stand-in would resolve nothing and the test would pass on its own."""
    track = Track.__new__(Track)
    track.artists = [SimpleNamespace(name=artist_name)]
    track.artist = SimpleNamespace(name=artist_name)
    track.name = "Title"
    track.track_num = 6
    track.volume_num = 1
    track.explicit = False
    track.isrc = "GB1234567890"
    track.album = SimpleNamespace(
        name="Example Album",
        artist=SimpleNamespace(name=artist_name),
        artists=[SimpleNamespace(name=artist_name)],
        num_tracks=12,
        num_volumes=1,
        release_date=None,
        explicit=False,
    )
    return track


def _destination(relative: str) -> pathlib.Path:
    """Mirror download.py's join of the base and the formatted relative path."""
    return (BASE / (relative + ".flac")).absolute()


@pytest.mark.parametrize("artist_name", EMPTYING_NAMES)
def test_an_empty_leading_token_cannot_escape_the_download_base(artist_name):
    """The headline case: the template's first token empties out."""
    relative = format_path_media(Settings().format_track, _track(artist_name), 2, 0, 0)

    assert not relative.startswith("/"), f"relative path is absolute: {relative!r}"
    assert not relative.startswith("\\"), f"relative path is absolute: {relative!r}"

    destination = _destination(relative)
    assert BASE in destination.parents, f"{destination} escaped the download base"


@pytest.mark.parametrize("artist_name", EMPTYING_NAMES)
def test_no_empty_components_survive_anywhere_in_the_path(artist_name):
    """An emptied token mid-template must not leave a doubled separator either
    (which is how an empty directory level would appear)."""
    relative = format_path_media(Settings().format_track, _track(artist_name), 2, 0, 0)

    assert "//" not in relative, relative
    assert all(part for part in relative.split("/")), relative


def test_an_ordinary_name_is_untouched():
    """Control: normal formatting must be byte-identical to before the fix."""
    relative = format_path_media(Settings().format_track, _track("Aphex Twin"), 2, 0, 0)

    assert relative.startswith("Aphex Twin/")
    assert "Example Album" in relative
    assert BASE in _destination(relative).parents


def test_punctuation_heavy_names_that_do_survive_are_kept():
    """Names that sanitize to something real keep it: the fix must not eat
    them along with the degenerate cases."""
    for artist_name in ("!!!", "M|A|R|R|S"):
        relative = format_path_media(Settings().format_track, _track(artist_name), 2, 0, 0)
        assert relative.split("/")[0], f"{artist_name!r} emptied out unexpectedly"
        assert BASE in _destination(relative).parents
