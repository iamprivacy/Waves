"""The "My Tidal button" switch (Settings > Appearance).

Off hides the My Tidal tab in the top bar. Pins that it ships on, is a row
switch in Appearance, and that flipping it notifies the QML.
"""

from __future__ import annotations

from tests.test_video_hover_peek_pref import _bind, _prefs_stub, _schema_stub
from waves.waves_ui.backend import WavesBridge


def test_nav_my_tidal_default_on():
    assert _prefs_stub()._waves_prefs["nav_my_tidal"] is True


def test_switch_is_a_row_in_the_appearance_section():
    schema = WavesBridge.settingsSchema(_schema_stub())
    appearance = next(s for s in schema if s["id"] == "appearance")
    field = next((f for f in appearance["fields"] if f["key"] == "nav_my_tidal"), None)
    assert field is not None
    assert field["type"] == "bool"
    assert field["value"] is True
    assert field.get("rowToggle") is True


def test_flipping_the_pref_notifies_the_ui():
    stub = _prefs_stub()
    stub._save_waves_prefs = lambda: None
    stub._factory_reset = False
    fired = []

    class _Sig:
        def emit(self):
            fired.append(True)

    stub.navMyTidalChanged = _Sig()
    _bind(stub, "setWavesPref")("nav_my_tidal", False)
    assert stub._waves_prefs["nav_my_tidal"] is False
    assert fired, "Main.qml only re-reads the pref when navMyTidalChanged fires"
