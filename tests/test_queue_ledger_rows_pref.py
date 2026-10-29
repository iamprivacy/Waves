"""The "Tracks shown in an open queue row" setting (Settings > Appearance).

An open queue row lists its tracks in a box that stops growing at a number of
rows and scrolls inside after that (issue #54). That number is a setting.
Pinned here: it ships at 12, it is offered as a whole number with its range in
the Appearance section, a saved value is held inside the range (a stray string
or a hand-edited waves.json can never size the box off a cliff), a stored
value is held there on load too, and a change notifies the QML, which re-reads
the pref on that signal.

A sibling of tests/test_art_hover_tilt_pref.py, built the same way.
"""

from __future__ import annotations

import json

from test_art_hover_tilt_pref import _bind, _prefs_stub, _schema_stub

from waves.waves_ui import backend
from waves.waves_ui.backend import WavesBridge


def _setter_stub():
    stub = _prefs_stub()
    stub._save_waves_prefs = lambda: None
    stub._factory_reset = False
    fired = []

    class _Sig:
        def emit(self):
            fired.append(True)

    stub.queueLedgerRowsChanged = _Sig()
    return stub, fired


def test_ships_at_twelve_rows():
    assert _prefs_stub()._waves_prefs["queue_ledger_rows"] == 12


def test_offered_in_appearance_as_a_bounded_number():
    schema = WavesBridge.settingsSchema(_schema_stub())
    appearance = next(s for s in schema if s["id"] == "appearance")
    field = next((f for f in appearance["fields"] if f["key"] == "queue_ledger_rows"), None)
    assert field is not None, "the setting must be reachable from Settings > Appearance"
    assert field["type"] == "int"
    assert field["value"] == 12
    assert (field["minimum"], field["maximum"]) == backend._WAVES_INT_PREFS["queue_ledger_rows"]
    assert field["label"] and field["help"]


def test_a_change_is_saved_as_a_number_and_notifies_the_ui():
    stub, fired = _setter_stub()
    _bind(stub, "setWavesPref")("queue_ledger_rows", 20)
    assert stub._waves_prefs["queue_ledger_rows"] == 20
    assert fired, "Main.qml only re-reads the pref when queueLedgerRowsChanged fires"
    # The page hands numbers over as floats or strings too.
    _bind(stub, "setWavesPref")("queue_ledger_rows", "15")
    assert stub._waves_prefs["queue_ledger_rows"] == 15


def test_a_saved_value_is_held_inside_the_range():
    lo, hi = backend._WAVES_INT_PREFS["queue_ledger_rows"]
    stub, _ = _setter_stub()
    set_pref = _bind(stub, "setWavesPref")
    set_pref("queue_ledger_rows", 0)
    assert stub._waves_prefs["queue_ledger_rows"] == lo
    set_pref("queue_ledger_rows", 10_000)
    assert stub._waves_prefs["queue_ledger_rows"] == hi
    set_pref("queue_ledger_rows", 9)
    set_pref("queue_ledger_rows", "lots")
    assert stub._waves_prefs["queue_ledger_rows"] == 9, "garbage keeps the value it had"


def test_an_unchanged_value_does_not_notify():
    stub, fired = _setter_stub()
    _bind(stub, "setWavesPref")("queue_ledger_rows", 12)
    assert not fired


def test_a_stored_value_is_held_inside_the_range_on_load(tmp_path):
    lo, hi = backend._WAVES_INT_PREFS["queue_ledger_rows"]
    stub = _prefs_stub()
    stub._load_waves_prefs = _bind(stub, "_load_waves_prefs")
    stub._waves_prefs_path = str(tmp_path / "waves.json")
    for stored, want in ((999, hi), (-3, lo), ("abc", 12), (None, 12), ("17", 17)):
        (tmp_path / "waves.json").write_text(json.dumps({"queue_ledger_rows": stored}), encoding="utf-8")
        assert stub._load_waves_prefs()["queue_ledger_rows"] == want, stored
