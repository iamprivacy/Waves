"""What the window's close button does (Settings > Appearance).

Closing the window used to be the only way out, and it always ended the app.
The close_action pref makes it a choice: "ask" (the prompt), "background"
(the window hides, Waves keeps running) or "exit". This pins what makes the
choice real: it ships as "ask", it is a three-way row in the Appearance
section, an unknown value is refused, a change notifies the QML, and the
prompt's "Don't ask again" both keeps the pick and refreshes an open
Settings page.

The sibling of tests/test_player_size_pref.py, pinned the same way.
"""

from __future__ import annotations

from tests.test_player_size_pref import _bind, _prefs_stub, _schema_stub
from waves.waves_ui.backend import WavesBridge


class _Sig:
    def __init__(self):
        self.fired = 0

    def emit(self):
        self.fired += 1


def _pref_stub():
    stub = _prefs_stub()
    stub._save_waves_prefs = lambda: None
    stub._factory_reset = False
    stub.closeActionChanged = _Sig()
    stub.settingsPersistedExternally = _Sig()
    stub.setWavesPref = _bind(stub, "setWavesPref")
    return stub


def test_close_action_defaults_to_ask():
    assert _prefs_stub()._waves_prefs["close_action"] == "ask"


def test_choice_is_offered_in_the_appearance_section():
    schema = WavesBridge.settingsSchema(_schema_stub())
    appearance = next(s for s in schema if s["id"] == "appearance")
    field = next((f for f in appearance["fields"] if f["key"] == "close_action"), None)
    assert field is not None, "the choice must be reachable from Settings > Appearance"
    # A choice row, never a switch tile: the Appearance tile grid is untouched.
    assert field["type"] == "enum"
    assert field["value"] == "ask"
    assert [o["value"] for o in field["options"]] == ["ask", "background", "exit"]
    assert [o["label"] for o in field["options"]] == ["Ask every time", "Run in background", "Exit"]
    assert field["label"] == "When the window is closed"
    assert field["help"]


def test_a_change_notifies_the_ui_and_an_unknown_value_is_refused():
    stub = _pref_stub()
    stub.setWavesPref("close_action", "background")
    assert stub._waves_prefs["close_action"] == "background"
    assert stub.closeActionChanged.fired == 1
    stub.setWavesPref("close_action", "minimize")
    assert stub._waves_prefs["close_action"] == "background"
    assert stub.closeActionChanged.fired == 1


def test_dont_ask_again_keeps_the_pick_and_refreshes_settings():
    stub = _pref_stub()
    remember = _bind(stub, "rememberCloseAction")
    remember("exit")
    assert stub._waves_prefs["close_action"] == "exit"
    assert stub.settingsPersistedExternally.fired == 1
    # The prompt only ever keeps one of its two buttons.
    remember("ask")
    remember("anything")
    assert stub._waves_prefs["close_action"] == "exit"
    assert stub.settingsPersistedExternally.fired == 1


def test_quit_request_is_read_once():
    stub = _prefs_stub()
    take = _bind(stub, "takeQuitRequest")
    assert take() is False
    _bind(stub, "note_quit_requested")()
    assert take() is True
    assert take() is False, "a vetoed quit must not color the next close"


def test_background_state_and_the_way_back(monkeypatch):
    from waves.waves_ui import backend

    stub = _prefs_stub()
    stub.showWindowRequested = _Sig()
    stub.backgroundAvailableChanged = _Sig()
    active = _bind(stub, "note_app_active")
    # The app becoming active means nothing while the window is up.
    active()
    assert stub.showWindowRequested.fired == 0
    _bind(stub, "enterBackground")()
    # Closed to the background, Waves is never "in front": banners show.
    assert _bind(stub, "_notify_waves_in_front")() is False
    active()  # the Dock icon on macOS
    assert stub.showWindowRequested.fired == 1
    _bind(stub, "leaveBackground")()
    active()
    assert stub.showWindowRequested.fired == 1

    # A way back exists on macOS (the Dock); elsewhere only with a tray.
    available = WavesBridge.backgroundAvailable.fget
    monkeypatch.setattr(backend.sys, "platform", "darwin")
    assert available(stub) is True
    monkeypatch.setattr(backend.sys, "platform", "linux")
    assert available(stub) is False
    _bind(stub, "setTrayAvailable")(True)
    assert available(stub) is True
    assert stub.backgroundAvailableChanged.fired == 1
    _bind(stub, "setTrayAvailable")(True)
    assert stub.backgroundAvailableChanged.fired == 1


def test_a_desktop_with_no_tray_is_told_closing_exits(monkeypatch):
    """Audit 2026-11-14b: on a Linux desktop with no tray the close button
    always exits, yet the setting promised downloads would keep going with
    the window closed."""
    from waves.waves_ui import backend

    def help_text(stub) -> str:
        schema = WavesBridge.settingsSchema(stub)
        appearance = next(s for s in schema if s["id"] == "appearance")
        return next(f for f in appearance["fields"] if f["key"] == "close_action")["help"]

    monkeypatch.setattr(backend.sys, "platform", "linux")
    stub = _schema_stub()
    assert "exits" in help_text(stub) and "keeps downloads" not in help_text(stub)
    stub._tray_available = True
    assert "keeps downloads" in help_text(stub)
    monkeypatch.setattr(backend.sys, "platform", "darwin")
    assert "keeps downloads" in help_text(_schema_stub())
