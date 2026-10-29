"""Settings remembers its place: the section on show, kept across launches.

The page shows one section at a time, picked from the rail. The one you
leave it on is stored in the settings_section pref (a section id, "" until
you pick one), and the next visit, or the next launch, opens there. Deep
links (update notice, folder gate, FLAC notice) pick their target section
themselves, which counts as a pick. A stored id that no longer names a
section (renamed or merged by an update) falls back to the first one.
"""

from __future__ import annotations

import pathlib

from waves.waves_ui.backend import WavesBridge

_QML_SRC = pathlib.Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "SettingsPage.qml"


class _Stub:
    """Bare object the real methods get bound onto."""


def _bind(stub, name):
    return getattr(WavesBridge, name).__get__(stub, type(stub))


def _prefs_stub():
    stub = _Stub()
    stub._default_waves_prefs = _bind(stub, "_default_waves_prefs")
    stub._waves_prefs = stub._default_waves_prefs()
    stub._waves_pref_bool = _bind(stub, "_waves_pref_bool")
    return stub


def test_section_pref_defaults_to_untouched():
    prefs = _prefs_stub()._waves_prefs
    assert prefs["settings_section"] == ""
    # The accordion's per-section record is gone with the accordion.
    assert "settings_open_sections" not in prefs


def test_no_section_ships_forced_open():
    schema = WavesBridge.settingsSchema(_schema_stub())
    forced = [s["id"] for s in schema if s.get("open") is True]
    assert forced == [], "no section carries the old accordion's open flag"


def test_the_section_survives_a_round_trip():
    stub = _prefs_stub()
    stub._save_waves_prefs = lambda: None
    stub._factory_reset = False
    _bind(stub, "setWavesPref")("settings_section", "pacing")
    assert stub._waves_prefs["settings_section"] == "pacing"


def _schema_stub():
    """A bridge stub with just enough state for settingsSchema().

    Built on a fresh defaults-only config, never the machine's own, so the
    test can't depend on (or print) the user's real settings.
    """
    from waves.model.cfg import HelpSettings
    from waves.model.cfg import Settings as CfgSettings

    class _Cfg:
        data = CfgSettings()
        help = HelpSettings()

    stub = _prefs_stub()
    stub.settings = _Cfg()
    stub._help = HelpSettings()
    stub._help_for = _bind(stub, "_help_for")
    stub._ffmpeg_flag_prefs = {}
    stub.ffmpegState = lambda: {"status": "none", "source": "none", "path": ""}
    # No ffmpeg probing from a unit test: the page prefills the detected
    # binary, which is machine state this test has no business reading.
    stub._user_ffmpeg_path = lambda: ""
    stub._ffmpeg_detected_path = lambda: ""
    return stub


def test_the_page_restores_and_records_the_section():
    src = _QML_SRC.read_text()
    # Read on load, and only an id the rail lists is taken.
    load = src[src.index("function loadSection()") :]
    load = load[: load.index("\n    }\n")]
    assert 'waves.wavesPref("settings_section")' in load
    assert "if (railHas(v)) curSec = v" in load
    # After the schema, so a section it leaves out is never restored.
    assert "Component.onCompleted: { refreshSchema(); loadSection();" in src
    # Written on every real change of section, deep links included.
    show = src[src.index("function showSection(id)") :]
    show = show[: show.index("\n    }\n")]
    assert 'waves.setWavesPref("settings_section", id)' in show
    # A deep link goes through showSection, never around it.
    jump = src[src.index("function jumpToCard(cardId)") :]
    jump = jump[: jump.index("\n    }\n")]
    assert "page.showSection(it.modelData.id)" in jump
    assert "curSec =" not in jump
