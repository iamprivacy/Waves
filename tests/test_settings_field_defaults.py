"""Per-field "Restore default" data (Settings, string fields).

Each customizable path template carries the value a fresh install ships with,
so the settings page can offer a one-click restore for that field alone
instead of making the user reset every setting. Fields whose default is empty
(the download folder, a custom FFmpeg path) must NOT offer it: restoring "" is
not a useful action, and for the download folder it would clear a required
value.
"""

from __future__ import annotations

from tidaler.model.cfg import HelpSettings
from tidaler.model.cfg import Settings as CfgSettings
from tidaler.waves_ui.backend import WavesBridge, _shipped_default

# Every string field the page lets you customize, and whether restoring a
# shipped default makes sense for it.
_WITH_DEFAULT = [
    "format_track",
    "format_album",
    "format_playlist",
    "format_video",
    "format_mix",
    "filename_delimiter_artist",
    "filename_delimiter_album_artist",
]
_WITHOUT_DEFAULT = ["download_base_path", "path_binary_ffmpeg"]


class _Stub:
    """Bare object the real methods get bound onto."""


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


def _fields_by_key():
    out = {}
    for section in WavesBridge.settingsSchema(_schema_stub()):
        for f in section["fields"]:
            out[f["key"]] = f
    return out


def test_templates_carry_the_shipped_default():
    fields = _fields_by_key()
    for key in _WITH_DEFAULT:
        assert key in fields, f"{key} vanished from the settings page"
        assert fields[key].get("default_value"), f"{key} offers no default to restore"


def test_the_default_is_what_a_fresh_install_gets():
    fresh = CfgSettings()
    for key, field in _fields_by_key().items():
        if "default_value" not in field:
            continue
        # Read off the dataclass, so it can never drift from a new install.
        assert field["default_value"] == getattr(fresh, key)


def test_fields_with_no_useful_default_do_not_offer_one():
    fields = _fields_by_key()
    for key in _WITHOUT_DEFAULT:
        assert "default_value" not in fields[key], f"{key} must not offer a default"
        assert _shipped_default(key) is None


def test_unknown_key_has_no_default():
    assert _shipped_default("not_a_setting") is None
