"""The illegal-character stand-in setting is safe and strictly additive.

filename_illegal_replacement lets a user choose what is written where a
filesystem-rejected character is removed ("AC/DC" with "-" becomes "AC-DC").
Three properties are pinned here because each one is a way the setting could
have broken something:

- the value is laundered at the point of use, so nothing typed into the box
  (or hand-edited into the config file) can put an illegal character back
  into a name;
- the default "" reproduces the shipped behavior exactly;
- switching the setting never restructures a library: folders and files
  saved under EITHER older spelling (plain removal, or the pre-0.1.17
  doubled space) keep winning, and the stand-in only names what does not
  exist yet.
"""

from __future__ import annotations

import pathlib
import threading
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from tidalapi import Album, Track

from waves.download import Download
from waves.helper.path import format_path_media, safe_filename_replacement
from waves.waves_ui.backend import WavesBridge

_UI = pathlib.Path(__file__).resolve().parent.parent / "waves" / "waves_ui"

_SLASHED = "The Better Life / Dead Love"
_LEGACY_DIR = "The Better Life  Dead Love"  # doubled space, pre-0.1.17
_PLAIN_DIR = "The Better Life Dead Love"  # 0.1.17 removal spelling
_STANDIN_DIR = "The Better Life - Dead Love"  # with the "-" stand-in


def _track(album_title: str = _SLASHED, title: str = "Song") -> Track:
    t = Track.__new__(Track)
    t.id = 1
    t.name = title
    t.version = None
    t.full_name = title
    t.explicit = False
    t.track_num = 1
    t.volume_num = 1
    t.artists = [SimpleNamespace(name="Three Doors Down")]
    t.artist = SimpleNamespace(name="Three Doors Down")
    album = Album.__new__(Album)
    album.id = 1
    album.name = album_title
    album.artists = [SimpleNamespace(name="Three Doors Down", roles=None)]
    album.artist = SimpleNamespace(name="Three Doors Down")
    album.num_tracks = 1
    album.num_volumes = 1
    album.release_date = datetime(2000, 2, 8)
    t.album = album
    return t


def _make_download(base: pathlib.Path, replacement: str = "") -> Download:
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


class TestTheValueIsLaunderedAtUse:
    def test_ordinary_stand_ins_survive(self):
        for value in ("-", "_", "+", " - ", "~"):
            assert safe_filename_replacement(value) == value

    def test_illegal_characters_are_dropped_not_inserted(self):
        for value in ("/", "\\", ":", "*", "?", '"', "<", ">", "|"):
            assert safe_filename_replacement(value) == ""
        assert safe_filename_replacement("-/") == "-"

    def test_the_length_is_capped(self):
        assert safe_filename_replacement("-----") == "---"

    def test_a_non_string_or_empty_value_collapses_to_removal(self):
        assert safe_filename_replacement("") == ""
        assert safe_filename_replacement(None) == ""
        assert safe_filename_replacement(7) == ""

    def test_a_magicmock_settings_object_reads_as_no_replacement(self, tmp_path):
        # Live settings objects in tests are MagicMocks; the accessor must
        # degrade to the shipped behavior, never raise.
        dl = _make_download(tmp_path, replacement=MagicMock())

        assert dl._illegal_replacement() == ""


class TestTheStandInNamesNewDownloads:
    def test_a_slash_becomes_the_stand_in(self):
        out = format_path_media("{album_title}", _track(), illegal_replacement="-")

        assert out == _STANDIN_DIR

    def test_an_unspaced_name_no_longer_welds_together(self):
        out = format_path_media("{album_title}", _track(album_title="AC/DC"), illegal_replacement="-")

        assert out == "AC-DC"

    def test_a_spaced_stand_in_still_tidies_to_single_spaces(self):
        out = format_path_media("{album_title}", _track(), illegal_replacement=" - ")

        assert out == _STANDIN_DIR

    def test_the_default_is_exactly_the_shipped_removal(self):
        assert format_path_media("{album_title}", _track()) == _PLAIN_DIR
        assert format_path_media("{album_title}", _track(), illegal_replacement="") == _PLAIN_DIR


class TestSwitchingTheSettingNeverRestructures:
    def test_a_plain_0117_folder_keeps_receiving_downloads(self, tmp_path):
        dl = _make_download(tmp_path, replacement="-")
        (tmp_path / _PLAIN_DIR).mkdir()

        chosen = dl._keep_existing_layout(
            tmp_path / _STANDIN_DIR / "Song.flac",
            tmp_path / _PLAIN_DIR / "Song.flac",
            tmp_path / _LEGACY_DIR / "Song.flac",
        )

        assert chosen.parent.name == _PLAIN_DIR

    def test_a_pre_0117_folder_keeps_receiving_downloads(self, tmp_path):
        dl = _make_download(tmp_path, replacement="-")
        (tmp_path / _LEGACY_DIR).mkdir()

        chosen = dl._keep_existing_layout(
            tmp_path / _STANDIN_DIR / "Song.flac",
            tmp_path / _PLAIN_DIR / "Song.flac",
            tmp_path / _LEGACY_DIR / "Song.flac",
        )

        assert chosen.parent.name == _LEGACY_DIR

    def test_the_most_recent_spelling_wins_when_both_exist(self, tmp_path):
        dl = _make_download(tmp_path, replacement="-")
        (tmp_path / _PLAIN_DIR).mkdir()
        (tmp_path / _LEGACY_DIR).mkdir()

        chosen = dl._keep_existing_layout(
            tmp_path / _STANDIN_DIR / "Song.flac",
            tmp_path / _PLAIN_DIR / "Song.flac",
            tmp_path / _LEGACY_DIR / "Song.flac",
        )

        assert chosen.parent.name == _PLAIN_DIR

    def test_a_fresh_library_gets_the_stand_in_spelling(self, tmp_path):
        dl = _make_download(tmp_path, replacement="-")

        chosen = dl._keep_existing_layout(
            tmp_path / _STANDIN_DIR / "Song.flac",
            tmp_path / _PLAIN_DIR / "Song.flac",
            tmp_path / _LEGACY_DIR / "Song.flac",
        )

        assert chosen.parent.name == _STANDIN_DIR

    def test_an_existing_plain_file_is_reused_not_re_downloaded(self, tmp_path):
        dl = _make_download(tmp_path, replacement="-")
        (tmp_path / _PLAIN_DIR).mkdir()
        (tmp_path / _PLAIN_DIR / "A B.flac").write_bytes(b"x")

        chosen = dl._keep_existing_layout(
            tmp_path / _STANDIN_DIR / "A - B.flac",
            tmp_path / _PLAIN_DIR / "A B.flac",
            tmp_path / _LEGACY_DIR / "A  B.flac",
        )

        assert chosen == tmp_path / _PLAIN_DIR / "A B.flac"


class TestTheSettingsBoxRefusesIllegalCharacters:
    """Typing a rejected character warns in red, and a save cannot keep it."""

    def test_the_page_asks_the_engines_own_launderer(self):
        # The warning would be a lie if the page judged the value by a rule of
        # its own, so the slot is the very function the download path calls.
        assert WavesBridge.sanitizeFilenameReplacement(None, "-/") == "-"
        assert WavesBridge.sanitizeFilenameReplacement(None, "?") == ""
        assert WavesBridge.sanitizeFilenameReplacement(None, " - ") == " - "

    def test_the_field_is_marked_for_laundering(self):
        src = (_UI / "backend.py").read_text()

        assert '_SANITIZED_FIELDS = {"filename_illegal_replacement"}' in src
        assert 'extra["sanitize"] = True' in src

    def test_the_box_turns_red_while_the_value_would_not_survive(self):
        src = (_UI / "qml" / "SettingsPage.qml").read_text()

        # SText paints its outline red on `invalid` ...
        assert "border.color: invalid ? page.red :" in src
        # ... and the short-value box sets it from the laundering check.
        assert "invalid: page.sanitizeDirty(modelData)" in src
        assert "waves.sanitizeFilenameReplacement(String(val(f)))" in src

    def test_a_red_field_greys_out_save_instead_of_correcting_it(self):
        # Correcting the value on save flashed "changes saved" over a silent
        # rewrite. The button is held instead, leaving the bad character on
        # screen to be fixed.
        src = (_UI / "qml" / "SettingsPage.qml").read_text()

        assert "readonly property bool canSave: page.dirty && !page.hasInvalidEdits()" in src
        assert "opacity: canSave ? 1 : 0.4" in src
        assert "anchors.fill: parent; enabled: saveBtn.canSave" in src
        assert "cleanSanitized" not in src
