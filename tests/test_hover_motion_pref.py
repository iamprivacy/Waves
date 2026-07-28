"""The "Hover controls slide in" escape hatch (Settings > Advanced).

Hover controls rise in with a bounce by default; anyone who finds the motion
distracting can turn it off and get the plain fade back. This pins the three
things that make the toggle real: it ships on by default, it is offered as a
labelled bool in the Advanced section, and flipping it notifies the QML (which
re-reads the pref on that signal, see Main.qml's hoverMotion).
"""

from __future__ import annotations

from tidaler.waves_ui.backend import WavesBridge


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


def test_hover_control_motion_defaults_on():
    assert _prefs_stub()._waves_prefs["hover_control_motion"] is True


def test_toggle_is_offered_in_the_advanced_section():
    schema = WavesBridge.settingsSchema(_schema_stub())
    advanced = next(s for s in schema if s["id"] == "advanced")
    field = next((f for f in advanced["fields"] if f["key"] == "hover_control_motion"), None)
    assert field is not None, "the toggle must be reachable from Settings > Advanced"
    assert field["type"] == "bool"
    assert field["value"] is True
    # A power-user knob still has to explain itself.
    assert field["label"] and field["help"]


def test_flipping_the_pref_notifies_the_ui():
    stub = _prefs_stub()
    stub._save_waves_prefs = lambda: None
    stub._factory_reset = False
    fired = []

    class _Sig:
        def emit(self):
            fired.append(True)

    stub.hoverMotionChanged = _Sig()
    _bind(stub, "setWavesPref")("hover_control_motion", False)
    assert stub._waves_prefs["hover_control_motion"] is False
    assert fired, "Main.qml only re-reads the pref when hoverMotionChanged fires"


def _schema_stub():
    """A bridge stub with just enough state for settingsSchema().

    Built on a fresh defaults-only config, never the machine's own, so the
    test can't depend on (or print) the user's real settings.
    """
    from tidaler.model.cfg import HelpSettings
    from tidaler.model.cfg import Settings as CfgSettings

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
