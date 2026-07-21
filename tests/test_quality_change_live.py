"""Issue #9: a saved audio-quality change must reach the tidal session live.

Streams are requested at the SESSION's audio quality (the Waves UI never
passes a per-download quality), and historically that was only written at
startup, so a settings change kept downloading at the old quality until the
app was restarted. applySettings must re-apply settings to the tidal session
whenever quality_audio changes.

Tested with the method-bound stub pattern (no display, no live bridge).
"""

from __future__ import annotations

from types import SimpleNamespace

from tidaler.waves_ui.backend import WavesBridge


class _Stub:
    """Bare object the real applySettings gets bound onto."""


def _signal():
    return SimpleNamespace(emit=lambda *a: None)


def _apply_stub():
    stub = _Stub()
    stub._waves_prefs = {}
    stub.settings = SimpleNamespace(
        data=SimpleNamespace(quality_audio="LOW_320K", ffmpeg_source="system", downloads_concurrent_max=3),
        save=lambda: None,
    )
    stub._ffmpeg_flag_prefs = {}
    stub._restore_ffmpeg_flags = lambda: None
    stub._restore_ffmpeg_path = lambda: None
    stub._ffmpeg_source_label = lambda: "system"
    stub._waves_pref_bool = lambda key: False
    stub.ownershipChanged = _signal()
    stub.editionMergeChanged = _signal()
    stub.ffmpegStatusChanged = _signal()
    stub.dl_pool = SimpleNamespace(setMaxThreadCount=lambda n: None)
    stub._logged_in = False
    stub._set_status = lambda text: None
    calls = []
    stub.tidal = SimpleNamespace(settings_apply=lambda: calls.append(True) or True)
    stub._settings_apply_calls = calls
    return stub


def _apply(stub, values):
    WavesBridge.applySettings.__get__(stub, type(stub))(values)


def test_quality_audio_change_reapplies_session_settings():
    stub = _apply_stub()
    _apply(stub, {"quality_audio": "hi_res_lossless"})
    assert stub._settings_apply_calls, "quality change never reached the tidal session"
    assert stub.settings.data.quality_audio.name == "hi_res_lossless"


def test_unrelated_save_leaves_session_untouched():
    stub = _apply_stub()
    _apply(stub, {"skip_existing": True})
    assert not stub._settings_apply_calls
