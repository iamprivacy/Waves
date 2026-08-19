"""Recommended stand-ins: offered on the settings page, never imposed.

The per-character table (issue #16) shipped empty, so every rejected character
was simply removed: "Mercury: Act 1" lost its colon, and XXXTENTACION's album
"?" sanitized away to nothing at all and lost its folder entirely.
``DEFAULT_ILLEGAL_MAP`` is what the table should hold.

It cannot just become the dataclass default, though. An existing library's
folders are already spelled the old way, and quietly changing how future
downloads spell them would split albums across two conventions with no way back.
So the recommended table is:

* the factory value for a **brand-new install** (nothing on disk to contradict),
* an **offer** on the File organization card for everyone else, answerable three
  ways (take it, decline it, or fill the boxes in yourself), asked exactly once,
* and permanently reachable afterwards through the card's Recommended link.

Pinned here: the constant's own safety, the spellings it produces, who gets
asked, and that every one of the three answers ends the asking.
"""

from __future__ import annotations

import pathlib
from datetime import datetime
from threading import Lock
from types import SimpleNamespace

from tidalapi import Album, Track

from tidaler.constants import DEFAULT_ILLEGAL_MAP
from tidaler.helper.path import (
    ILLEGAL_FILENAME_CHARS,
    format_path_media,
    safe_filename_replacement,
    safe_filename_replacement_map,
)
from tidaler.model.cfg import HelpSettings
from tidaler.model.cfg import Settings as CfgSettings
from tidaler.waves_ui.backend import _FIRST_RUN_OVERRIDES, WavesBridge

_UI = pathlib.Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui"
_SETTINGS_QML = (_UI / "qml" / "SettingsPage.qml").read_text(encoding="utf-8")


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
    # applySettings does its ffmpeg restores and its write under this lock, the
    # same one _save_settings holds, so a worker save cannot slip its borrowed
    # path into the write. A stub that drives applySettings needs the real thing.
    stub._settings_save_lock = Lock()
    stub.ffmpegState = lambda: {"status": "none", "source": "none", "path": ""}
    stub._user_ffmpeg_path = lambda: ""
    stub._ffmpeg_detected_path = lambda: ""
    return stub


def _map_field(stub=None) -> dict:
    stub = stub or _schema_stub()
    for section in WavesBridge.settingsSchema(stub):
        for f in section["fields"]:
            if f["key"] == "filename_illegal_map":
                return f
    raise AssertionError("filename_illegal_map is missing from the settings schema")


class TestTheRecommendedTableIsSafeToWrite:
    def test_it_only_names_characters_a_file_name_cannot_hold(self):
        # A stand-in for a legal character would rewrite titles that were never
        # in danger, so the launderer drops one and this catches it earlier.
        assert set(DEFAULT_ILLEGAL_MAP) <= set(ILLEGAL_FILENAME_CHARS)

    def test_every_stand_in_survives_the_launderer_untouched(self):
        # Anything the launderer would trim is a stand-in the engine would not
        # actually write, which would make the settings page tell a lie.
        for char, standin in DEFAULT_ILLEGAL_MAP.items():
            assert safe_filename_replacement(standin) == standin, char

    def test_the_laundered_table_is_the_table(self):
        assert safe_filename_replacement_map(dict(DEFAULT_ILLEGAL_MAP)) == DEFAULT_ILLEGAL_MAP

    def test_the_rare_four_are_left_to_the_general_stand_in(self):
        # * < > | are vanishingly rare in release titles and read fine simply
        # removed. Naming them would be churn for nothing.
        assert not ({"*", "<", ">", "|"} & set(DEFAULT_ILLEGAL_MAP))


def _track(album_title: str) -> Track:
    t = Track.__new__(Track)
    t.id = 1
    t.name = "Song"
    t.version = None
    t.full_name = "Song"
    t.explicit = False
    t.track_num = 1
    t.volume_num = 1
    t.artists = [SimpleNamespace(name="A")]
    t.artist = SimpleNamespace(name="A")
    album = Album.__new__(Album)
    album.id = 1
    album.name = album_title
    album.artists = [SimpleNamespace(name="A", roles=None)]
    album.artist = SimpleNamespace(name="A")
    album.num_tracks = 1
    album.num_volumes = 1
    album.release_date = datetime(2000, 2, 8)
    t.album = album
    return t


class TestWhatTheRecommendedTableSpells:
    def _album(self, title: str, standin: str = "") -> str:
        return format_path_media(
            "{album_title}",
            _track(title),
            illegal_replacement=standin,
            illegal_map=dict(DEFAULT_ILLEGAL_MAP),
        )

    def test_a_subtitle_keeps_its_separator(self):
        assert self._album("Mercury: Act 1") == "Mercury · Act 1"

    def test_a_slash_inside_a_name_stays_readable(self):
        assert self._album("AC/DC Live") == "AC-DC Live"

    def test_quotes_around_a_title_survive_as_apostrophes(self):
        assert self._album('"Heroes"') == "'Heroes'"

    def test_a_trailing_question_mark_is_still_a_question_mark(self):
        assert self._album("What's Going On?") == "What's Going On？"

    def test_an_album_named_only_punctuation_still_has_a_name(self):
        # The issue #16 complaint: "?" sanitized to "", the segment was dropped,
        # and the album never got a folder of its own.
        assert self._album("?") == "？"

    def test_the_unnamed_characters_still_follow_the_general_stand_in(self):
        assert self._album("Star * Struck", standin="+") == "Star + Struck"


class TestWhoGetsTheDefaultsOutright:
    def test_the_dataclass_default_stays_empty(self):
        # Nothing is ever applied to an existing install by the mere act of
        # upgrading. This is the load-bearing half of that promise.
        assert CfgSettings().filename_illegal_map == {}

    def test_a_brand_new_install_starts_with_them(self):
        assert _FIRST_RUN_OVERRIDES["filename_illegal_map"] == DEFAULT_ILLEGAL_MAP

    def test_the_first_run_copy_cannot_rewrite_the_constant(self):
        # setattr of the shared dict would make the user's first edit change
        # what "recommended" means for every later reset.
        stub = _Stub()
        stub.settings = SimpleNamespace(data=CfgSettings(), save=lambda: None)
        _bind(stub, "_apply_first_run_defaults")()
        stub.settings.data.filename_illegal_map["?"] = "!!"

        assert DEFAULT_ILLEGAL_MAP["?"] == "？"


def _offer_stub(*, fresh: bool, configured: bool, stamped: bool = False):
    stub = _Stub()
    stub.settings = SimpleNamespace(data=CfgSettings())
    if configured:
        stub.settings.data.filename_illegal_map = {":": " - "}
    stub._fresh_install = fresh
    stub._default_waves_prefs = _bind(stub, "_default_waves_prefs")
    stub._waves_prefs = stub._default_waves_prefs()
    stub._waves_prefs["illegal_map_offer_done"] = stamped
    stub._saves = []
    stub._save_waves_prefs = lambda: stub._saves.append(dict(stub._waves_prefs))
    return stub


class TestWhoGetsAsked:
    def test_an_upgrade_with_no_stand_ins_is_asked(self):
        stub = _offer_stub(fresh=False, configured=False)
        _bind(stub, "_migrate_illegal_map_offer")()

        assert stub._waves_prefs["illegal_map_offer_done"] is False
        assert stub._saves == [], "nothing to persist until the user answers"
        assert _map_field()["offer"] is True

    def test_a_brand_new_install_is_not_asked(self):
        # It already has the recommended table; asking would be asking about a
        # choice it has not made yet.
        stub = _offer_stub(fresh=True, configured=False)
        _bind(stub, "_migrate_illegal_map_offer")()

        assert stub._waves_prefs["illegal_map_offer_done"] is True
        assert len(stub._saves) == 1

    def test_someone_with_stand_ins_of_their_own_is_not_asked(self):
        stub = _offer_stub(fresh=False, configured=True)
        _bind(stub, "_migrate_illegal_map_offer")()

        assert stub._waves_prefs["illegal_map_offer_done"] is True

    def test_the_answer_is_remembered(self):
        stub = _offer_stub(fresh=False, configured=False, stamped=True)
        _bind(stub, "_migrate_illegal_map_offer")()

        assert stub._saves == [], "a settled question is not re-saved every launch"

    def test_a_stamped_install_no_longer_sees_the_strip(self):
        stub = _schema_stub()
        stub._waves_prefs["illegal_map_offer_done"] = True

        assert _map_field(stub)["offer"] is False

    def test_declining_settles_it(self):
        stub = _offer_stub(fresh=False, configured=False)
        _bind(stub, "resolveIllegalMapOffer")()

        assert stub._waves_prefs["illegal_map_offer_done"] is True
        assert len(stub._saves) == 1

        _bind(stub, "resolveIllegalMapOffer")()
        assert len(stub._saves) == 1, "declining twice must not rewrite waves.json"


class TestSavingStandInsAnswersTheOfferToo:
    def _apply_stub(self):
        stub = _Stub()
        stub.settings = SimpleNamespace(data=CfgSettings(), save=lambda: None)
        stub._default_waves_prefs = _bind(stub, "_default_waves_prefs")
        stub._waves_prefs = stub._default_waves_prefs()
        stub._waves_prefs["illegal_map_offer_done"] = False
        stub._saves = []
        stub._save_waves_prefs = lambda: stub._saves.append(dict(stub._waves_prefs))
        stub._ffmpeg_flag_prefs = {}
        # applySettings does its ffmpeg restores and its write under this lock, the
        # same one _save_settings holds, so a worker save cannot slip its borrowed
        # path into the write. A stub that drives applySettings needs the real thing.
        stub._settings_save_lock = Lock()
        stub._restore_ffmpeg_flags = lambda: None
        stub._restore_ffmpeg_path = lambda: None
        stub._ffmpeg_source_label = lambda: "system"
        stub._waves_pref_bool = lambda key: False
        stub._set_status = lambda text: None
        stub._logged_in = False
        stub._init_download = lambda: None
        for signal in ("ownershipChanged", "editionMergeChanged", "ffmpegStatusChanged"):
            setattr(stub, signal, SimpleNamespace(emit=lambda *a: None))
        stub.dl_pool = SimpleNamespace(setMaxThreadCount=lambda n: None)
        stub.tidal = SimpleNamespace(settings_apply=lambda: True)
        return stub

    def test_filling_the_table_in_by_hand_ends_the_asking(self):
        # Whether the values came from the offer's own button or from typing
        # into the boxes, the user has answered.
        stub = self._apply_stub()
        WavesBridge.applySettings.__get__(stub, type(stub))({"filename_illegal_map": {"?": "-"}})

        assert stub.settings.data.filename_illegal_map == {"?": "-"}
        assert stub._waves_prefs["illegal_map_offer_done"] is True

    def test_clearing_the_table_back_to_empty_does_not_answer_it(self):
        stub = self._apply_stub()
        WavesBridge.applySettings.__get__(stub, type(stub))({"filename_illegal_map": {}})

        assert stub._waves_prefs["illegal_map_offer_done"] is False


class TestTheSettingsPageShowsTheOffer:
    def test_the_card_is_handed_the_recommended_table(self):
        assert _map_field()["default_value"] == DEFAULT_ILLEGAL_MAP

    def test_the_schema_hands_over_a_copy(self):
        field = _map_field()
        field["default_value"]["?"] = "tampered"

        assert DEFAULT_ILLEGAL_MAP["?"] == "？"

    def test_the_strip_offers_all_three_answers(self):
        assert "USE THESE" in _SETTINGS_QML
        assert "KEEP REMOVING THEM" in _SETTINGS_QML
        assert "SET MY OWN" in _SETTINGS_QML

    def test_only_declining_writes_the_stamp(self):
        # Taking the table stages it for SAVE CHANGES instead, so a CANCEL
        # leaves the question genuinely unanswered and it gets asked again.
        assert _SETTINGS_QML.count("waves.resolveIllegalMapOffer()") == 1

    def test_taking_the_table_stages_it_rather_than_saving_it(self):
        assert "page.mapStage(mapCard.modelData, mapCard.modelData.default_value)" in _SETTINGS_QML

    def test_the_strip_goes_once_the_table_holds_anything(self):
        # Otherwise it would still be offering stand-ins over the top of the
        # ones just typed in.
        assert "Object.keys(page.mapVal(mapCard.modelData)).length === 0" in _SETTINGS_QML

    def test_the_recommended_link_is_the_permanent_way_back(self):
        assert "function mapIsDefault(f)" in _SETTINGS_QML
        assert '"Use recommended" : "Recommended"' in _SETTINGS_QML

    def test_the_offer_shows_what_each_character_would_become(self):
        strip = _SETTINGS_QML[_SETTINGS_QML.index("id: offerStrip") :]
        chip = strip[: strip.index("Mercury: Act 1")]
        chip = chip[chip.index("model: page.mapDefaultChars(mapCard.modelData)") :]

        assert "text: mapCard.modelData.default_value[parent.parent.modelData]" in chip
        # Nothing in the strip is rendered as markup, same rule as the rest.
        assert chip.count("Text {") == chip.count("textFormat: Text.PlainText")
