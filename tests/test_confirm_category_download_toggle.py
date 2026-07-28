"""The bulk-download confirm must be switchable back ON.

DOWNLOAD ALL on a Browse playlist category asks before queueing the whole set,
and the dialog's "Don't ask again" persists ``confirm_category_download =
False``. That flag was absent from ``settingsSchema``, which has three knock-on
effects, because the schema is the single list everything else iterates:

* no toggle anywhere in the settings page, so the only way to switch it back
  on was editing the config file by hand;
* ``_factory_default_values`` skips it, so even "Reset all settings" left the
  confirm muted;
* nothing tells the tile the value changed, so a toggle would not take effect
  until a relaunch.

One accidental tick therefore silenced a confirm about queueing an entire
category, permanently.
"""

from __future__ import annotations

from tidaler.model.cfg import HelpSettings
from tidaler.model.cfg import Settings as CfgSettings
from tidaler.waves_ui.backend import _FLAG_FIELDS, WavesBridge

_KEY = "confirm_category_download"


class _Stub:
    """Bare object the real methods get bound onto."""


def _bind(stub, name):
    return getattr(WavesBridge, name).__get__(stub, type(stub))


def _schema_stub():
    stub = _Stub()

    class _Cfg:
        data = CfgSettings()

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


def test_the_confirm_has_a_toggle_on_the_settings_page():
    field = _fields_by_key().get(_KEY)
    assert field is not None, "no way to switch the bulk-download confirm back on"
    assert field["type"] == "bool"
    assert field["value"] is True, "a fresh install confirms"
    assert field["label"] and field["help"], "a toggle with no label or help is not usable"


def test_it_is_persisted_as_a_flag():
    """Without this applySettings would str() the checkbox value, and every
    write would land as a truthy string."""
    assert _KEY in _FLAG_FIELDS


def test_reset_all_settings_restores_it():
    stub = _schema_stub()
    stub.settings.data.confirm_category_download = False
    stub.settingsSchema = _bind(stub, "settingsSchema")
    values = WavesBridge._factory_default_values(stub)
    assert values.get(_KEY) is True, "a full reset left the confirm muted"


def test_toggling_it_notifies_the_tile():
    """The tile reads confirmCategoryDl, a notifying property. Only the dialog
    used to change the flag (and emitted for itself); a settings write with no
    emit would not reach the tile until a relaunch."""
    import inspect

    src = inspect.getsource(WavesBridge.applySettings)
    assert _KEY in src and "confirmCategoryDlChanged.emit()" in src
