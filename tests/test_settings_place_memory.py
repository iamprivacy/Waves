"""Settings remembers its shape: sections collapsed by default, state kept.

The page used to hardcode Downloads open and forget everything else between
launches. Now every section starts collapsed on a first visit, the user's
opens/closes persist in the settings_open_sections pref (a JSON object of
id -> bool), and deep links (update notice, folder gate, lyrics link) open
their target section themselves. The QML half (exact scroll restore through
the save-armed schema rebuild included) is exercised by
scratchpad/settings_place_probe.py against the live Main.qml.
"""

from __future__ import annotations

import json

from waves.waves_ui.backend import WavesBridge


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


def test_open_sections_pref_defaults_to_untouched():
    assert _prefs_stub()._waves_prefs["settings_open_sections"] == ""


def test_no_section_ships_forced_open():
    schema = WavesBridge.settingsSchema(_schema_stub())
    forced = [s["id"] for s in schema if s.get("open") is True]
    assert forced == [], "every section must start collapsed on a first visit"


def test_open_sections_survive_a_round_trip_as_json():
    stub = _prefs_stub()
    stub._save_waves_prefs = lambda: None
    stub._factory_reset = False
    recorded = json.dumps({"advanced": True, "downloads": False})
    _bind(stub, "setWavesPref")("settings_open_sections", recorded)
    # setWavesPref str-coerces non-bool prefs; the JSON must come back intact
    # for the page to parse on the next launch.
    stored = stub._waves_prefs["settings_open_sections"]
    assert json.loads(stored) == {"advanced": True, "downloads": False}


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
