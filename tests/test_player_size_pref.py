"""The "Player bar" size (Settings > Appearance): full or mini.

The bottom bar's waveform is 200 px wide. Anyone who would rather give the
track's name and artists more of the bar can switch to the mini player,
whose waveform is 130 px. This pins the three things that make the choice
real: it ships as full by default, it is offered as a two-way choice in the
Appearance section, and picking the other notifies the QML, which re-reads
the pref on that signal and sizes the rail from root.playerSize.

The sibling of tests/test_art_hover_tilt_pref.py, pinned the same way.
"""

from __future__ import annotations

from pathlib import Path

from waves.waves_ui.backend import WavesBridge

_QML = Path(__file__).resolve().parents[1] / "waves" / "waves_ui" / "qml"


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


def test_player_size_defaults_to_full():
    assert _prefs_stub()._waves_prefs["player_size"] == "full"


def test_choice_is_offered_in_the_appearance_section():
    schema = WavesBridge.settingsSchema(_schema_stub())
    appearance = next(s for s in schema if s["id"] == "appearance")
    field = next((f for f in appearance["fields"] if f["key"] == "player_size"), None)
    assert field is not None, "the choice must be reachable from Settings > Appearance"
    assert field["type"] == "enum"
    assert field["value"] == "full"
    assert [o["value"] for o in field["options"]] == ["full", "mini"]
    assert [o["label"] for o in field["options"]] == ["Full", "Mini"]
    assert field["label"] and field["help"]


def test_picking_the_other_size_notifies_the_ui():
    stub = _prefs_stub()
    stub._save_waves_prefs = lambda: None
    stub._factory_reset = False
    fired = []

    class _Sig:
        def emit(self):
            fired.append(True)

    stub.playerSizeChanged = _Sig()
    _bind(stub, "setWavesPref")("player_size", "mini")
    assert stub._waves_prefs["player_size"] == "mini"
    assert fired, "Main.qml only re-reads the pref when playerSizeChanged fires"
    # An unknown value is refused and does not notify.
    fired.clear()
    _bind(stub, "setWavesPref")("player_size", "huge")
    assert stub._waves_prefs["player_size"] == "mini"
    assert not fired


def test_the_rail_sizes_itself_from_the_pref():
    src = (_QML / "Main.qml").read_text(encoding="utf-8")
    assert 'property string playerSize: waves.wavesPref("player_size") === "mini" ? "mini" : "full"' in src
    assert "function onPlayerSizeChanged()" in src
    rail = src[src.index("    component NowRail: Item {") : src.index("    component RailCanvas: Canvas {")]
    assert 'readonly property int rw: root.playerSize === "mini" ? 130 : 200' in rail


def _schema_stub():
    """A bridge stub with just enough state for settingsSchema(), on a
    defaults-only config, never the machine's own."""
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
    stub._user_ffmpeg_path = lambda: ""
    stub._ffmpeg_detected_path = lambda: ""
    return stub
