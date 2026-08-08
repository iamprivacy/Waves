"""The one-time update opt-in prompt (updateOptInGate).

Update checks default OFF and nothing in the app ever asked, so users on old
builds miss fixes for problems they are actively having. The prompt appears
once, at the end of the first-run chain (login > FFmpeg > terms) or on the
first launch of an existing install, and offers the once-a-day check.

WHAT THIS FENCES OFF
--------------------
1. Answering the prompt with "turn on" must actually enable the pref, persist
   it, refresh the Settings page underneath, and fire a check immediately, so
   a release that is already out surfaces this session instead of tomorrow.
2. Answering "not now" must change nothing: auto_update stays off and no
   check fires (the no-unsolicited-connections promise holds).
3. The QML gate must keep the shape that makes it one-time and non-naggy:
   gated on the whole first-run chain, on the pref being off, on the answered
   flag, and on the two-dismissal cap for click-aways.
"""

from __future__ import annotations

import re
from pathlib import Path

from tidaler.waves_ui.backend import WavesBridge

QML_MAIN = Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui" / "qml" / "Main.qml"


class _Stub:
    """Bare object the real methods get bound onto."""


def _bind(stub, name):
    return getattr(WavesBridge, name).__get__(stub, type(stub))


class _Sig:
    def __init__(self):
        self.fired = 0

    def emit(self):
        self.fired += 1


def _optin_stub():
    stub = _Stub()
    stub._default_waves_prefs = _bind(stub, "_default_waves_prefs")
    stub._waves_prefs = stub._default_waves_prefs()
    stub._waves_pref_bool = _bind(stub, "_waves_pref_bool")
    stub._saves = 0

    def _save():
        stub._saves += 1

    stub._save_waves_prefs = _save
    stub.settingsPersistedExternally = _Sig()
    stub._startup_checks = 0

    def _startup_check():
        stub._startup_checks += 1

    stub.startupUpdateCheck = _startup_check
    return stub


def test_accept_enables_persists_refreshes_and_checks():
    stub = _optin_stub()
    assert stub._waves_prefs["auto_update"] is False, "the prompt exists because this defaults off"
    _bind(stub, "resolveUpdateOptIn")(True)
    assert stub._waves_prefs["auto_update"] is True
    assert stub._saves == 1, "the choice must survive a restart"
    assert stub.settingsPersistedExternally.fired == 1, "the Settings page must not show a stale toggle"
    assert stub._startup_checks == 1, "an accepted prompt checks now, not tomorrow"


def test_accept_clears_a_stale_throttle_stamp():
    """A user who once enabled then disabled auto-update in Settings still
    carries update_last_check; the accept must clear it so the immediate check
    the button promises is not silently throttled away. Reset to 0, never
    popped: the key doubles as setWavesPref's whitelist entry, and a dormant
    updater's early return in startupUpdateCheck would leave it missing."""
    stub = _optin_stub()
    stub._waves_prefs["update_last_check"] = 4102444800
    _bind(stub, "resolveUpdateOptIn")(True)
    assert stub._waves_prefs["update_last_check"] == 0
    assert stub._startup_checks == 1


def test_decline_changes_nothing():
    stub = _optin_stub()
    _bind(stub, "resolveUpdateOptIn")(False)
    assert stub._waves_prefs["auto_update"] is False
    assert stub._saves == 0
    assert stub.settingsPersistedExternally.fired == 0
    assert stub._startup_checks == 0, "declining must not fire any outbound request"


def test_gate_keeps_its_one_time_non_naggy_shape():
    src = QML_MAIN.read_text(encoding="utf-8")
    m = re.search(r"id: updateOptInGate\b.*?shouldShow:(.*?)// Rather than pop", src, re.S)
    assert m, "the update opt-in gate must exist in Main.qml"
    cond = m.group(1)
    # The whole first-run chain comes first: never over login, FFmpeg or terms.
    for clause in (
        "waves.loggedIn",
        "setupSettings.ffmpegSetupDone",
        "legalSettings.termsAccepted",
        "bootOverlay.done",
        "!ffmpegGate.visible",
        "!setupSettings.updatePromptAnswered",
        "setupSettings.updatePromptDismissals < 2",
        "!sessionDismissed",
        "!autoUpdateOn",
    ):
        assert clause in cond, f"gate visibility lost its {clause!r} clause"
    # Both buttons answer for good through the same path.
    assert src.count("updateOptInGate.answer(") == 2
    # The persisted flags live in QSettings (the "setup" block), NOT in
    # waves.json: the prefs file is written wholesale and a new key there
    # would be pinned into every existing user's file by unrelated saves.
    assert "property bool updatePromptAnswered: false" in src
    assert "property int updatePromptDismissals: 0" in src
    # The fresh-setup wording flag persists too: a click-away re-asks next
    # launch, and a session-only flag would greet that same fresh install
    # with the veteran copy.
    assert "property bool updatePromptFresh: false" in src
    # Above the video overlay (z 999): the prompt must never paint under a
    # playing video.
    m = re.search(r"id: updateOptInGate\b.*?z: (\d+)", src, re.S)
    assert m and int(m.group(1)) > 999, "the opt-in gate must stack above the video overlay"


def test_accept_passes_the_real_startup_throttle():
    """End to end with the REAL startupUpdateCheck bound, not a counter stub:
    the accept's promise is an IMMEDIATE check, and the daily throttle is the
    thing that could silently eat it. Seed a stamp from minutes ago (which
    the throttle would normally swallow), accept, and require the real check
    to fire and re-stamp."""
    import time
    from types import SimpleNamespace

    stub = _optin_stub()
    stub.startupUpdateCheck = _bind(stub, "startupUpdateCheck")
    stub._updater = SimpleNamespace(is_configured=lambda: True)
    stub._real_checks = 0

    def _check():
        stub._real_checks += 1

    stub.checkAppUpdate = _check
    before = int(time.time())
    stub._waves_prefs["update_last_check"] = before - 300
    _bind(stub, "resolveUpdateOptIn")(True)
    assert stub._real_checks == 1, "the throttle swallowed the promised immediate check"
    assert int(stub._waves_prefs["update_last_check"]) >= before, "the check must re-stamp the throttle"
