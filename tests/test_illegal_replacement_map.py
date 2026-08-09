"""Per-character stand-ins: one rejected character, one replacement.

A single stand-in for every rejected character reads badly on the ones that
carry meaning: a colon is a subtitle, and "Rarities Edition- Live" is not what
the title said (issue #16). The map names a stand-in per character, so ":" can
become " · " while "?" becomes "-" and "/" is simply removed.

Pinned here: the map is laundered at the point of use exactly like the general
stand-in, it applies before the general one so unnamed characters still fall
back to it, an empty map reproduces the shipped behavior byte for byte, and
adding an override never restructures a library already named without one.
"""

from __future__ import annotations

import pathlib
import threading
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from tidalapi import Album, Track

from tidaler.download import Download
from tidaler.helper.path import (
    ILLEGAL_FILENAME_CHARS,
    format_path_media,
    safe_filename_replacement_map,
)
from tidaler.model.cfg import HelpSettings
from tidaler.model.cfg import Settings as CfgSettings
from tidaler.waves_ui import backend
from tidaler.waves_ui.backend import WavesBridge

_UI = pathlib.Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui"

_SUBTITLED = "The Better Life (Rarities Edition: Live At Red Rocks)"
_MIDDOT = "The Better Life (Rarities Edition · Live At Red Rocks)"


def _track(album_title: str = _SUBTITLED, title: str = "Song") -> Track:
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


def _make_download(base: pathlib.Path, replacement: str = "", mapping: dict | None = None) -> Download:
    dl = Download(
        tidal_obj=MagicMock(),
        skip_existing=True,
        path_base=str(base),
        fn_logger=MagicMock(),
        progress=MagicMock(),
    )
    dl.settings = MagicMock()
    dl.settings.data.filename_illegal_replacement = replacement
    dl.settings.data.filename_illegal_map = mapping if mapping is not None else {}
    dl.settings.data.filename_delimiter_artist = ", "
    dl.settings.data.filename_delimiter_album_artist = ", "
    dl.settings.data.use_primary_album_artist = False
    dl.settings.data.album_track_num_pad_min = 1
    dl.settings.data.symlink_to_track = False
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()
    return dl


class TestTheMapIsLaunderedAtUse:
    def test_every_rejected_character_can_be_named(self):
        given = dict.fromkeys(ILLEGAL_FILENAME_CHARS, "-")

        assert safe_filename_replacement_map(given) == given

    def test_a_character_a_name_can_hold_is_not_a_key(self):
        # Mapping "a" to "b" would silently rewrite every name; only the
        # characters that are actually removed can be given a stand-in.
        assert safe_filename_replacement_map({"a": "b", "é": "e", "": "-", "??": "-"}) == {}

    def test_a_stand_in_cannot_smuggle_a_rejected_character_back_in(self):
        assert safe_filename_replacement_map({":": "/"}) == {":": ""}
        assert safe_filename_replacement_map({":": " ·/"}) == {":": " ·"}

    def test_a_stand_in_is_capped_like_the_general_one(self):
        assert safe_filename_replacement_map({"?": "-----"}) == {"?": "---"}

    def test_anything_but_a_dict_of_strings_collapses_to_nothing(self):
        assert safe_filename_replacement_map(None) == {}
        assert safe_filename_replacement_map("?=-") == {}
        assert safe_filename_replacement_map(7) == {}
        assert safe_filename_replacement_map({"?": None}) == {"?": ""}
        assert safe_filename_replacement_map({7: "-"}) == {}

    def test_a_magicmock_settings_object_reads_as_no_overrides(self, tmp_path):
        dl = _make_download(tmp_path, mapping=MagicMock())

        assert dl._illegal_map() == {}


class TestEachCharacterGetsItsOwnStandIn:
    def test_a_colon_becomes_a_readable_middle_dot(self):
        out = format_path_media("{album_title}", _track(), illegal_map={":": " · "})

        assert out == _MIDDOT

    def test_the_spacing_around_it_is_still_tidied(self):
        # ": " already carries a space, and " · " brings its own; the run
        # collapses rather than leaving "Edition ·  Live".
        assert "  " not in format_path_media("{album_title}", _track(), illegal_map={":": " · "})

    def test_a_character_left_unnamed_falls_back_to_the_general_stand_in(self):
        out = format_path_media(
            "{album_title}",
            _track(album_title="AC/DC: Live?"),
            illegal_replacement="-",
            illegal_map={":": " · "},
        )

        assert out == "AC-DC · Live-"

    def test_a_named_character_wins_over_the_general_stand_in(self):
        out = format_path_media(
            "{album_title}",
            _track(album_title="AC/DC"),
            illegal_replacement="-",
            illegal_map={"/": ""},
        )

        assert out == "ACDC"

    def test_an_empty_map_is_exactly_the_shipped_behavior(self):
        plain = format_path_media("{album_title}", _track(), illegal_replacement="-")

        assert format_path_media("{album_title}", _track(), illegal_replacement="-", illegal_map={}) == plain
        assert format_path_media("{album_title}", _track(), illegal_replacement="-", illegal_map=None) == plain

    def test_the_template_s_own_separators_are_untouched(self):
        # The map applies to a token's VALUE, never to the assembled path, so
        # a stand-in for "/" cannot weld two folders into one.
        out = format_path_media(
            "{album_artist}/{album_title}",
            _track(album_title="AC/DC"),
            illegal_map={"/": "-"},
        )

        assert out == "Three Doors Down/AC-DC"


class TestAddingAnOverrideNeverRestructures:
    def test_folders_named_with_the_general_stand_in_keep_receiving_downloads(self, tmp_path):
        # A 0.1.17 library is spelled with the general stand-in and no map, a
        # spelling the older-spelling list has to carry once the map exists.
        dl = _make_download(tmp_path, replacement="-", mapping={":": " · "})
        (tmp_path / "The Better Life - Dead Love").mkdir()

        chosen = dl._keep_existing_layout(
            tmp_path / "The Better Life · Dead Love" / "Song.flac",
            tmp_path / "The Better Life - Dead Love" / "Song.flac",
            tmp_path / "The Better Life Dead Love" / "Song.flac",
        )

        assert chosen.parent.name == "The Better Life - Dead Love"

    def test_the_general_stand_in_spelling_is_offered_to_the_layout_guard(self, tmp_path):
        # The guard can only prefer a spelling it is handed, so the engine has
        # to build the map-free one and pass it along.
        dl = _make_download(tmp_path, replacement="-", mapping={":": " · "})
        dl.extension_guess = lambda *a, **k: ".flac"
        seen = []
        dl._keep_existing_layout = lambda tidied, *older: seen.extend([tidied, *older]) or tidied

        dl._prepare_file_paths_and_skip_logic(_track(), "{album_title}/{track_title}", None, 0, 0)

        assert [path.parent.name for path in seen] == [
            "The Better Life (Rarities Edition · Live At Red Rocks)",
            "The Better Life (Rarities Edition- Live At Red Rocks)",
            "The Better Life (Rarities Edition Live At Red Rocks)",
            "The Better Life (Rarities Edition Live At Red Rocks)",
        ]

    def test_a_fresh_library_gets_the_mapped_spelling(self, tmp_path):
        dl = _make_download(tmp_path, replacement="-", mapping={":": " · "})

        chosen = dl._keep_existing_layout(
            tmp_path / "The Better Life · Dead Love" / "Song.flac",
            tmp_path / "The Better Life - Dead Love" / "Song.flac",
        )

        assert chosen.parent.name == "The Better Life · Dead Love"


class _Stub:
    """Bare object the real bridge methods get bound onto."""


def _bind(stub, name):
    return getattr(WavesBridge, name).__get__(stub, type(stub))


def _schema_stub():
    class _Cfg:
        data = CfgSettings()
        help = HelpSettings()

    stub = _Stub()
    stub.settings = _Cfg()
    stub._help = HelpSettings()
    stub._help_for = _bind(stub, "_help_for")
    stub._default_waves_prefs = _bind(stub, "_default_waves_prefs")
    stub._waves_prefs = stub._default_waves_prefs()
    stub._waves_pref_bool = _bind(stub, "_waves_pref_bool")
    stub._ffmpeg_flag_prefs = {}
    stub.ffmpegState = lambda: {"status": "none", "source": "none", "path": ""}
    stub._user_ffmpeg_path = lambda: ""
    stub._ffmpeg_detected_path = lambda: ""
    return stub


def _map_field() -> dict:
    for section in WavesBridge.settingsSchema(_schema_stub()):
        for f in section["fields"]:
            if f["key"] == "filename_illegal_map":
                return f
    raise AssertionError("filename_illegal_map is missing from the settings schema")


class TestTheSettingsPageOffersOneBoxPerCharacter:
    def test_the_table_sits_in_file_organization_under_the_general_stand_in(self):
        section = next(s for s in WavesBridge.settingsSchema(_schema_stub()) if s["id"] == "files")
        keys = [f["key"] for f in section["fields"]]

        assert keys.index("filename_illegal_map") == keys.index("filename_illegal_replacement") + 1

    def test_the_page_is_handed_every_character_it_must_show(self):
        chars = [c["char"] for c in _map_field()["chars"]]

        assert chars == list(ILLEGAL_FILENAME_CHARS)
        assert all(c["name"] for c in _map_field()["chars"])

    def test_the_control_type_is_the_table_not_a_text_box(self):
        assert _map_field()["type"] == "char_map"
        assert _map_field()["value"] == {}

    def test_a_saved_value_is_laundered_on_the_way_in(self):
        stub = _schema_stub()
        stub.settings.data.filename_illegal_map = {":": " · ", "a": "b", "?": "/"}
        field = next(
            f for s in WavesBridge.settingsSchema(stub) for f in s["fields"] if f["key"] == "filename_illegal_map"
        )

        assert field["value"] == {":": " · ", "?": ""}

    def test_the_path_preview_shows_what_the_table_does(self, monkeypatch):
        # The preview is the proof offered before anything downloads, so it
        # runs the overrides through the same formatter a download uses (the
        # canned sample names hold no rejected character of their own, so the
        # formatter is asked what it was handed).
        stub = _schema_stub()
        stub.settings.data.filename_illegal_map = {":": " · ", "a": "b"}
        stub._template_sample = _bind(stub, "_template_sample")
        seen: dict = {}
        monkeypatch.setattr(backend, "format_path_media", lambda *a, **kw: seen.update(kw) or "Example Album")

        WavesBridge.previewPathTemplate(stub, "album", "{album_title}")

        assert seen["illegal_map"] == {":": " · "}


class TestThePageCannotSaveARejectedStandIn:
    """A rejected stand-in is refused on screen, and again on the way to disk."""

    def test_a_bad_row_turns_red_and_holds_save_changes(self):
        src = (_UI / "qml" / "SettingsPage.qml").read_text()

        # Same red-outline / held-save machinery as the general stand-in: the
        # table registers itself in sanitizeKeys, which hasInvalidEdits walks.
        assert "invalid: page.mapCharDirty(charRow.fieldData, charRow.ch)" in src
        assert "Component.onCompleted: page.sanitizeKeys[modelData.key] = modelData" in src

    def test_the_row_asks_the_engines_own_launderer(self):
        src = (_UI / "qml" / "SettingsPage.qml").read_text()

        assert "waves.sanitizeFilenameReplacement(v)" in src

    def test_clearing_a_row_is_not_the_same_as_emptying_it(self):
        # An empty stand-in IS a choice (remove the character outright), so
        # "follow the general stand-in again" needs its own action.
        src = (_UI / "qml" / "SettingsPage.qml").read_text()

        assert "function mapClear(" in src
        assert "onClicked: {\n" in src.replace("\r\n", "\n")
        assert "page.mapClear(charRow.fieldData, charRow.ch)" in src

    def test_the_save_path_stores_a_table_not_its_text(self):
        src = (_UI / "backend.py").read_text()

        assert "elif key in _MAP_FIELDS:" in src
        assert "laundered = safe_filename_replacement_map(dict(value or {}))" in src
        assert "setattr(data, key, laundered)" in src
