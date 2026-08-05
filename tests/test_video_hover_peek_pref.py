"""The "Videos preview on hover" escape hatch (Settings > Advanced).

Resting the pointer on a video thumbnail grows a sound-on live preview card
(the hover peek). Anyone who does not want video and audio starting from a
mere hover can turn it off: thumbnails then stay still and videos play only on
click. This pins the three things that make the toggle real: it ships on by
default, it is offered as a labelled bool in the Advanced section, and
flipping it notifies the QML, which re-reads the pref on that signal and gates
root.peekOpen (the single entry point every thumbnail's dwell timer funnels
into, so one check silences them all).

The sibling of tests/test_hover_motion_pref.py and
tests/test_art_hover_tilt_pref.py, and deliberately their twin: the escape
hatches should stay pinned the same way.
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


def test_video_hover_peek_defaults_on():
    assert _prefs_stub()._waves_prefs["video_hover_peek"] is True


def test_toggle_is_offered_in_the_advanced_section():
    schema = WavesBridge.settingsSchema(_schema_stub())
    advanced = next(s for s in schema if s["id"] == "advanced")
    field = next((f for f in advanced["fields"] if f["key"] == "video_hover_peek"), None)
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

    stub.videoHoverPeekChanged = _Sig()
    _bind(stub, "setWavesPref")("video_hover_peek", False)
    assert stub._waves_prefs["video_hover_peek"] is False
    assert fired, "Main.qml only re-reads the pref when videoHoverPeekChanged fires"


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
