"""The "Logo equalizer" look (Settings > Appearance).

While a preview plays, the logo in the top bar moves with the music, in one
of three looks: its water bending over the spectrum, bars of dots, or a
scrolling history, or Off. This pins what makes the choice real: it ships as the
water, it is offered as a four-way choice in the Appearance section, picking
another notifies the QML, and the logo asks for its levels only from the
clock that already pauses off screen, with the tap made on first play.

The sibling of tests/test_player_size_pref.py, pinned the same way.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_player_size_pref import _bind, _prefs_stub, _schema_stub
from waves.waves_ui.backend import WavesBridge

_QML = Path(__file__).resolve().parents[1] / "waves" / "waves_ui" / "qml"


def test_logo_eq_defaults_to_the_water():
    assert _prefs_stub()._waves_prefs["logo_eq"] == "water"


def test_choice_is_offered_in_the_appearance_section():
    schema = WavesBridge.settingsSchema(_schema_stub())
    appearance = next(s for s in schema if s["id"] == "appearance")
    field = next((f for f in appearance["fields"] if f["key"] == "logo_eq"), None)
    assert field is not None, "the choice must be reachable from Settings > Appearance"
    assert field["type"] == "enum"
    assert field["value"] == "water"
    assert [o["value"] for o in field["options"]] == ["water", "dots", "scroll", "off"]
    assert [o["label"] for o in field["options"]] == ["Rolling water", "Dot bars", "Scrolling dots", "Off"]
    assert field["label"] and field["help"]


def test_picking_another_look_notifies_the_ui():
    stub = _prefs_stub()
    stub._save_waves_prefs = lambda: None
    stub._factory_reset = False
    fired = []

    class _Sig:
        def emit(self):
            fired.append(True)

    stub.logoEqChanged = _Sig()
    _bind(stub, "setWavesPref")("logo_eq", "scroll")
    assert stub._waves_prefs["logo_eq"] == "scroll"
    assert fired, "Main.qml only re-reads the pref when logoEqChanged fires"
    # An unknown value is refused and does not notify.
    fired.clear()
    _bind(stub, "setWavesPref")("logo_eq", "lasers")
    assert stub._waves_prefs["logo_eq"] == "scroll"
    assert not fired


def test_with_no_tap_the_bridge_has_nothing_to_show():
    stub = _prefs_stub()
    assert _bind(stub, "logoEqLevels")() == []
    assert _bind(stub, "logoEqAttach")(object()) is False
    assert _bind(stub, "logoEqLevels")() == []


def test_the_logo_reads_the_pref_and_steps_from_its_own_clock():
    src = (_QML / "Main.qml").read_text(encoding="utf-8")
    assert 'property string logoEq: logoEqLook(waves.wavesPref("logo_eq"))' in src
    assert "function onLogoEqChanged()" in src
    assert "WaveMark { id: headerMark; eqPlayer: previewPlayer }" in src
    mark = src[src.index("    component WaveMark: Rectangle {") : src.index("    component PasteGlyph: Rectangle {")]
    # stepped by the 20 Hz clock that stops while the window is off screen
    assert "wm.eqStep()" in mark
    # nothing is asked of the bridge unless a preview plays (or the look is still fading out)
    assert "if (eqPlayer === null || (!root.previewPlaying && !eqOn)) return" in mark
    # both looks are built only while they show, never at launch
    assert 'active: wm.eqAmt > 0 && wm.eqLook === "water"' in mark
    assert 'active: wm.eqAmt > 0 && (wm.eqLook === "dots" || wm.eqLook === "scroll")' in mark
    # Off: the logo stays as it is and no audio tap is ever made
    assert 'if (eqLook === "off") { eqOn = false; return }' in mark
    assert mark.index('eqLook === "off"') < mark.index("waves.logoEqAttach(")
