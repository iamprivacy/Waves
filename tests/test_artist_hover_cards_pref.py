"""The "Artist cards on hover" switch (Settings > Appearance).

Resting the pointer on an artist's name opens a card with a summary, the top
tracks and a Follow button. Anyone who would rather keep names as plain links
can turn it off. This pins what makes the switch real: it ships on by default,
it is offered as a labeled row switch in the Appearance section (a row, never
a fifth tile, so the switch grid stays two across), and flipping it notifies
the QML, which re-reads the pref on that signal and feeds root.ahcBlocked
(the gate every name's hover passes through, and which closes an open card).

The twin of tests/test_video_hover_peek_pref.py.
"""

from __future__ import annotations

from tests.test_video_hover_peek_pref import _bind, _prefs_stub, _schema_stub
from waves.waves_ui.backend import WavesBridge


def test_artist_hover_cards_default_on():
    assert _prefs_stub()._waves_prefs["artist_hover_cards"] is True


def test_switch_is_a_row_in_the_appearance_section():
    schema = WavesBridge.settingsSchema(_schema_stub())
    appearance = next(s for s in schema if s["id"] == "appearance")
    field = next((f for f in appearance["fields"] if f["key"] == "artist_hover_cards"), None)
    assert field is not None, "the switch must be reachable from Settings > Appearance"
    assert field["type"] == "bool"
    assert field["value"] is True
    assert field.get("rowToggle") is True, "a fifth tile would leave one alone on a row"
    assert field["label"] and field["help"]


def test_flipping_the_pref_notifies_the_ui():
    stub = _prefs_stub()
    stub._save_waves_prefs = lambda: None
    stub._factory_reset = False
    fired = []

    class _Sig:
        def emit(self):
            fired.append(True)

    stub.artistHoverCardsChanged = _Sig()
    _bind(stub, "setWavesPref")("artist_hover_cards", False)
    assert stub._waves_prefs["artist_hover_cards"] is False
    assert fired, "Main.qml only re-reads the pref when artistHoverCardsChanged fires"
