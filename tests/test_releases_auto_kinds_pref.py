"""The "What downloads automatically" setting (Settings > Releases).

With "Download new releases automatically" on, the user picks which kinds
it takes: albums, EPs, singles, and featured releases (another artist's
release a followed one is a guest on). Pinned here: it ships with the
artist's own three kinds and featured off, it is offered only while the
switch is on, a saved pick is stored in one fixed order with anything
unknown dropped, and turning the featured download on or off (through
either pref) tells the Releases family exactly once.

A sibling of tests/test_queue_ledger_rows_pref.py, built the same way.
"""

from __future__ import annotations

from test_art_hover_tilt_pref import _bind, _prefs_stub, _schema_stub

from waves.waves_ui.backend import WavesBridge


def _setter_stub():
    stub = _prefs_stub()
    stub._save_waves_prefs = lambda: None
    stub._factory_reset = False
    stub._releases_forget_waiting = lambda: None
    switched: list = []
    stub._releases_guest_switched = switched.append
    stub._releases_auto_kinds = _bind(stub, "_releases_auto_kinds")
    stub._releases_guest_on = _bind(stub, "_releases_guest_on")
    return stub, switched


def test_ships_with_the_artists_own_kinds_and_featured_off():
    assert _prefs_stub()._waves_prefs["releases_auto_kinds"] == "album,ep,single"


def test_offered_under_the_switch_with_four_choices():
    schema = WavesBridge.settingsSchema(_schema_stub())
    releases = next(s for s in schema if s["id"] == "releases")
    field = next((f for f in releases["fields"] if f["key"] == "releases_auto_kinds"), None)
    assert field is not None, "the pick must be reachable from Settings > Releases"
    assert field["type"] == "kinds"
    assert field["value"] == "album,ep,single"
    assert [o["value"] for o in field["options"]] == ["album", "ep", "single", "featured"]
    assert [o["label"] for o in field["options"]] == ["Albums", "EPs", "Singles", "Guest tracks"]
    assert field["depends_on"] == "releases_auto_download" and field["depends_on_value"] is False
    assert field["label"] and field["help"]


def test_a_pick_is_stored_in_order_and_featured_switching_is_told_once():
    stub, switched = _setter_stub()
    set_pref = _bind(stub, "setWavesPref")
    set_pref("releases_auto_kinds", "featured,bogus,album")
    assert stub._waves_prefs["releases_auto_kinds"] == "album,featured"
    # The auto-download switch is still off: featured is not running yet.
    assert switched == []
    set_pref("releases_auto_download", True)
    assert switched == [True]
    set_pref("releases_auto_kinds", "album,ep")
    assert switched == [True, False]
    set_pref("releases_auto_kinds", "album")
    assert switched == [True, False]
    set_pref("releases_auto_kinds", "featured")
    set_pref("releases_auto_download", False)
    assert switched == [True, False, True, False]
