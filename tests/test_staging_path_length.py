"""The whole staging path is capped, not just its name (issue #17).

The sanitizer bounds a FINAL path against the platform cap (260 on Windows)
and the staging name against the 255-byte component cap, but nobody budgeted
the 42 characters of staging decoration against the whole-path cap. A final
path that fit Windows' limit by less than that put every staging attempt past
MAX_PATH, the OS answered "no such file or directory", every retry failed
identically, and exactly the longest-named tracks of an album were lost (3 of
13 on the reported album). Only the throwaway readable part of the staging
name may shrink; the final name is never touched.
"""

import os
import pathlib

import pytest

import tidaler.download as download_module
from tidaler.download import _STAGING_NAME_OVERHEAD, _staging_path

# The album from the report, respelled with POSIX separators so the length
# arithmetic is the same on the test machine as on the reporter's Windows box.
ALBUM_DIR = pathlib.Path(
    "/Users/Admin/Desktop/Tidal Waves Download/3 Doors Down/"
    "The Better Life (Rarities Edition · Live At Cynthia Woods Mitchell Pavilion)"
)
TRACK_NAME = (
    "06. Away From The Sun (Rarities Edition · Live At Cynthia Woods Mitchell Pavilion, Houston, TX, 2003).flac"
)

CJK = "曲"  # three bytes in UTF-8


def _path_bytes(path_file: pathlib.Path) -> int:
    return len(os.fsencode(str(path_file)))


class TestTheReportedAlbumStagesWithinTheWindowsCap:
    @pytest.fixture(autouse=True)
    def _windows_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(download_module, "_PATH_LENGTH_MAX", 259)

    def test_the_final_path_fits_while_the_undecorated_staging_name_did_not(self):
        destination = ALBUM_DIR / TRACK_NAME
        overflow = destination.with_name(f".{destination.name}.{'0' * 36}.tmp")

        assert _path_bytes(destination) <= 259, "the final path itself was never the problem"
        assert _path_bytes(overflow) > 259, "the undecorated staging spelling is what overflowed"

    def test_the_staging_path_fits_the_cap(self):
        staged = _staging_path(ALBUM_DIR / TRACK_NAME)

        assert _path_bytes(staged) <= 259

    def test_the_staging_name_is_still_hidden_unique_and_temporary(self):
        destination = ALBUM_DIR / TRACK_NAME
        staged = _staging_path(destination)

        assert staged.parent == destination.parent
        assert staged.name.startswith(".")
        assert staged.name.endswith(".tmp")
        assert _staging_path(destination) != _staging_path(destination)

    def test_a_parent_too_deep_for_any_readable_part_still_stages(self):
        # The readable part is long gone at this depth, so the uuid itself
        # gives ground: it used to stay at its full 36 characters, which put
        # every staging attempt past the cap the destination itself fit, the
        # exact issue-#17 failure this budget exists to prevent. The shrunken
        # uuid still rules out a concurrent-staging collision.
        destination = pathlib.Path("/" + "a" * 240) / "Song.flac"

        staged = _staging_path(destination)

        assert staged.name.startswith(".")
        assert staged.name.endswith(".tmp")
        assert len(staged.name) < _STAGING_NAME_OVERHEAD, "the uuid gave ground to the parent"
        assert _path_bytes(staged) <= 259, "the whole staging path fits the cap the destination fit"
        assert _staging_path(destination) != _staging_path(destination), "still unique"

    def test_a_short_path_keeps_the_whole_readable_name(self):
        staged = _staging_path(pathlib.Path("/music/Song.flac"))

        assert staged.name.startswith(".Song.flac.")
        assert staged.name.endswith(".tmp")


class TestThePosixCapHoldsToo:
    def test_a_final_path_near_the_posix_cap_stages_within_it(self):
        parent = pathlib.Path("/" + ("d" * 100 + "/") * 8 + "album")
        destination = parent / ("t" * 200 + ".flac")

        assert _path_bytes(destination) <= 1023, "the final path fits, as the sanitizer guarantees"
        assert _path_bytes(_staging_path(destination)) <= 1023

    def test_the_component_byte_cap_still_binds_at_a_shallow_parent(self):
        destination = pathlib.Path("/music") / (CJK * 250 + ".flac")

        assert len(os.fsencode(_staging_path(destination).name)) <= 255
