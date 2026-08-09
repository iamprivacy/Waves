"""Pins for the pre-push sweep of everything since v0.1.17 (2026-08-09).

An adversarially verified review of the b6c70ed..HEAD range confirmed a family
of defects in the new naming engine and its consumers. Each test here is one
confirmed failure, reproduced first against the unfixed code, now pinned
against its fix.
"""

from __future__ import annotations

import os
import pathlib
from types import SimpleNamespace
from unittest.mock import patch

import tidaler.download as download_module
from tidaler.download import Download, _staging_path
from tidaler.helper.folders import apply_folder_path
from tidaler.helper.path import (
    PATH_LENGTH_MAX,
    path_file_sanitize,
    path_file_uniquify,
    unique_variant_name,
)


def _path_bytes(p: pathlib.Path) -> int:
    return len(os.fsencode(str(p)))


def _make_download(tmp_path=None, skip_existing=True) -> Download:
    dl = Download.__new__(Download)
    dl.skip_existing = skip_existing
    dl.fn_logger = SimpleNamespace(debug=lambda *a: None, error=lambda *a: None)
    dl.settings = SimpleNamespace(data=SimpleNamespace(filename_illegal_replacement="", filename_illegal_map=None))
    dl._names_reserved = set()
    if tmp_path is not None:
        dl.path_base = str(tmp_path)
    return dl


def _track(tid):
    return SimpleNamespace(id=tid)


class TestTheM3uStagingNameIsBudgeted:
    """The m3u temp-and-swap hand-built its staging name (+42 chars) with no
    length budget, so a playlist name the sanitizer had fitted to the cap made
    the write raise after every track had already landed."""

    def test_a_name_at_the_cap_still_opens_its_staging_sibling(self, tmp_path):
        name = "L" * 230
        dl = _make_download(tmp_path)

        paths = Download.playlist_populate(dl, {tmp_path}, name, True, True)

        assert len(paths) == 1
        assert paths[0].is_file(), "the m3u landed where the pre-swap code landed it"
        assert not list(tmp_path.glob(".*.tmp")), "no staging leftover"


class TestTheM3uKeepsItsOldName:
    """The new name funnel respelled an existing library's m3u, leaving two
    files a library scanner both ingests, and nothing may ever delete one."""

    def test_an_existing_legacy_m3u_is_rewritten_not_duplicated(self, tmp_path):
        dl = _make_download(tmp_path)
        dl.settings.data.filename_illegal_replacement = "-"
        legacy = tmp_path / "_Best of 2010.m3u"
        legacy.write_text("old\n")

        paths = Download.playlist_populate(dl, {tmp_path}, "Best of: 2010", True, True)

        assert paths == [legacy], "the established name keeps receiving the playlist"
        assert not (tmp_path / "_Best of- 2010.m3u").exists(), "no second m3u appears"

    def test_a_fresh_library_gets_the_preferred_spelling(self, tmp_path):
        dl = _make_download(tmp_path)
        dl.settings.data.filename_illegal_replacement = "-"

        paths = Download.playlist_populate(dl, {tmp_path}, "Best of: 2010", True, True)

        assert paths == [tmp_path / "_Best of- 2010.m3u"]


class TestTheFolderPathKeepsItsOldSpelling:
    """{folder_path} is literal text before the engine's older-spelling
    fallbacks run, so a 0.1.17 folder respelled by the stand-ins or the
    spacing tidy re-downloaded every playlist inside it into a second tree."""

    TEMPLATE = "Playlists/{folder_path}{playlist_name}"

    def test_an_existing_library_folder_wins_over_the_respelling(self, tmp_path):
        (tmp_path / "Playlists" / "Chill  Mixes").mkdir(parents=True)

        out = apply_folder_path(self.TEMPLATE, "Chill  Mixes", base_path=str(tmp_path))

        assert out == "Playlists/Chill  Mixes/{playlist_name}", "the doubled space stays"

    def test_a_stand_in_never_respells_an_existing_folder(self, tmp_path):
        (tmp_path / "Playlists" / "Best of 2010").mkdir(parents=True)

        out = apply_folder_path(self.TEMPLATE, "Best of: 2010", "-", base_path=str(tmp_path))

        assert out == "Playlists/Best of 2010/{playlist_name}"

    def test_a_fresh_library_gets_the_preferred_spelling(self, tmp_path):
        out = apply_folder_path(self.TEMPLATE, "Best of: 2010", "-", base_path=str(tmp_path))

        assert out == "Playlists/Best of- 2010/{playlist_name}"

    def test_no_base_path_means_no_probe(self):
        # The settings preview passes no base: it shows the preferred spelling.
        out = apply_folder_path(self.TEMPLATE, "Chill  Mixes")

        assert out == "Playlists/Chill Mixes/{playlist_name}"

    def test_a_dropped_segment_is_no_evidence(self, tmp_path):
        # The legacy spelling of "?" loses the segment, so its "directory" is
        # the Playlists root, which exists trivially. Depth has to match.
        (tmp_path / "Playlists").mkdir(parents=True)

        out = apply_folder_path(self.TEMPLATE, "?", illegal_map={"?": "？"}, base_path=str(tmp_path))

        assert out == "Playlists/？/{playlist_name}"


class TestTheVariantScanSpellsNamesTheWayTheyAreWritten:
    """The numbered-copy scan concatenated stem + _NN raw, while the writer
    trims the stem to the 255-byte cap first: for any name at the cap the scan
    looked for a name that cannot exist, and every re-run of the album added
    another full copy."""

    def test_a_capped_stem_scan_finds_the_trimmed_copy(self, tmp_path):
        stem = "T" * 250
        base = tmp_path / (stem + ".flac")
        base.write_bytes(b"x")
        trimmed = tmp_path / unique_variant_name(base, "_01")
        assert len(trimmed.name.encode()) <= 255
        trimmed.write_bytes(b"x")
        ids = {base.name: "123", trimmed.name: "456"}

        dl = _make_download(tmp_path)
        with patch("tidaler.download.read_item_id", side_effect=lambda p: ids[pathlib.Path(p).name]):
            found = dl._existing_same_item_at(base, _track(456))

        assert found == trimmed, "the copy the writer produced is the copy the scan finds"


class TestAZeroByteVariantIsNotEvidence:
    """A 0-byte stem_NN leftover from a crash read as identity-unknown and
    permanently skipped a colliding track; the base name already refuses that
    trap through check_file_exists."""

    def test_an_empty_leftover_sibling_does_not_skip_the_track(self, tmp_path):
        (tmp_path / "Intro.flac").write_bytes(b"x")
        (tmp_path / "Intro_01.flac").write_bytes(b"")  # interrupted write

        dl = _make_download(tmp_path)
        with patch("tidaler.download.read_item_id", return_value="111"):
            found = dl._existing_same_item_at(tmp_path / "Intro.flac", _track(222))

        assert found is None, "track 222 still downloads; an empty file is not it"


class TestTheAnswerIsThePathNotABool:
    """When the item lives at a numbered variant, a bare yes made the caller
    skip and symlink against the BASE name: the fresh audio was deleted and
    the playlist entry pointed at the colliding stranger's file."""

    def test_the_symlink_move_targets_the_variant_that_is_this_track(self, tmp_path):
        # Library: Song.flac is id 999 (a stranger), Song_01.flac is id 111
        # (this track). The playlist copy of 111 must symlink to Song_01.
        track_dir = tmp_path / "Tracks"
        track_dir.mkdir()
        (track_dir / "Song.flac").write_bytes(b"id-999")
        (track_dir / "Song_01.flac").write_bytes(b"id-111")
        playlist_dir = tmp_path / "Playlists" / "Party"
        playlist_dir.mkdir(parents=True)
        src = playlist_dir / "Song.flac"
        src.write_bytes(b"id-111-fresh")

        ids = {"Song.flac": "999", "Song_01.flac": "111"}
        import threading
        from unittest.mock import MagicMock

        dl = Download(
            tidal_obj=MagicMock(),
            skip_existing=True,
            path_base=str(tmp_path),
            fn_logger=MagicMock(),
            progress=MagicMock(),
        )
        dl.settings = MagicMock()
        dl.event_abort = threading.Event()
        dl.event_run = threading.Event()
        dl.event_run.set()

        with (
            patch("tidaler.download.read_item_id", side_effect=lambda p: ids.get(pathlib.Path(p).name, "")),
            patch(
                "tidaler.download.format_path_media",
                return_value="Tracks/Song",
            ),
        ):
            result = dl.media_move_and_symlink(_track(111), src, ".flac")

        assert result == track_dir / "Song_01.flac", "the returned path IS this track"
        assert (track_dir / "Song.flac").read_bytes() == b"id-999", "the stranger is untouched"
        assert src.is_symlink(), "the playlist entry became a link"
        assert src.resolve() == (track_dir / "Song_01.flac").resolve(), "and points at this track"


class TestTheWholePathCapIsMeasuredHonestly:
    """pathvalidate strips the drive/UNC prefix before measuring and allows
    260, so Windows paths 3 to 15 characters past the real 259 limit sailed
    through and failed at the final move. The sanitizer now measures the whole
    spelling itself."""

    def test_a_path_just_over_the_cap_is_brought_under_it(self, tmp_path):
        parent = pathlib.Path("/" + "d" * 100) / ("e" * 100) / ("f" * 100) / ("g" * 100) / ("h" * 100) / ("i" * 100)
        name = "t" * 500 + ".flac"

        result = path_file_sanitize(parent / name, adapt=True)

        assert _path_bytes(result) <= PATH_LENGTH_MAX

    def test_the_trim_is_measured_not_halved(self):
        # A path a hair over the cap loses a hair of its title, not half. The
        # parent is deep enough that the PATH cap binds before the 255-byte
        # name cap does, so the trim under test is the whole-path one.
        parent = pathlib.Path("/" + "/".join(c * 100 for c in "defghijk"))
        room = PATH_LENGTH_MAX - _path_bytes(parent) - 1 - len(".flac")
        assert room < 250, "the path cap must be the binding one here"
        name = "t" * (room + 2) + ".flac"  # exactly 2 bytes over

        result = path_file_sanitize(parent / name, adapt=True)

        assert result.suffix == ".flac"
        assert len(result.stem) >= room - 4, "only the overage was surrendered"
        assert _path_bytes(result) <= PATH_LENGTH_MAX


class TestUniquifyRespectsTheWholePathCap:
    """Inserting _01 into a path fitted at the cap pushed it back over the
    limit nothing re-measured, failing the move after the download."""

    def test_a_collision_at_the_cap_stays_under_it(self, tmp_path):
        parent = pathlib.Path("/" + "d" * 100) / ("e" * 100) / ("f" * 100) / ("g" * 100) / ("h" * 100) / ("i" * 100)
        room = PATH_LENGTH_MAX - _path_bytes(parent) - 1 - len(".flac")
        fitted = parent / ("t" * room + ".flac")
        assert _path_bytes(fitted) == PATH_LENGTH_MAX

        unique = path_file_uniquify(fitted, names_taken={str(fitted)}, check_disk=False)

        assert unique is not None
        assert unique.name.endswith("_01.flac")
        assert _path_bytes(unique) <= PATH_LENGTH_MAX


class TestStagingSurvivesAParentNearTheCap:
    """A parent within 42 bytes of the cap overflowed every staging attempt
    even with the readable part already gone: the uuid gives ground now."""

    def test_deep_parent_staging_path_fits(self, monkeypatch):
        monkeypatch.setattr(download_module, "_PATH_LENGTH_MAX", 259)
        destination = pathlib.Path("/" + "a" * 240) / "Song.flac"
        assert _path_bytes(destination) <= 259

        staged = _staging_path(destination)

        assert _path_bytes(staged) <= 259
        assert staged.name.startswith(".") and staged.name.endswith(".tmp")
        assert _staging_path(destination) != _staging_path(destination), "uniqueness survives"
