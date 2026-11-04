"""The audio output pick (Settings > Playback, and the bar's output chip).

Issue #57: speakers on Miracast, Bluetooth or AirPlay never got Waves' sound,
because every AudioOutput took whatever Qt called the default when it was
made. The pick is one pref holding the device id as hex ("" follows the
system default) plus the device's name alongside it, so a pick that is
unplugged still reads by name. This pins the pref and the bridge side: the
defaults, the Playback section, the rows the page and the menu list, the
fallback to the system default, and the signal Main.qml re-resolves on.

Devices are faked: a unit test has no business reading this machine's
outputs. tests/test_audio_output_qml.py drives the real ones.
"""

from __future__ import annotations

import logging

import pytest
from PySide6 import QtMultimedia

from waves.waves_ui.backend import WavesBridge


class _Stub:
    """Bare object the real methods get bound onto."""


def _bind(stub, name):
    return getattr(WavesBridge, name).__get__(stub, type(stub))


class _Dev:
    def __init__(self, raw: bytes, name: str, default: bool = False):
        self.raw, self.name, self.default = raw, name, default

    def id(self):
        return self.raw

    def description(self):
        return self.name

    def isDefault(self):
        return self.default


_SPEAKERS = _Dev(b"spk", "MacBook Speakers", default=True)
_TV = _Dev(b"tv", "Living room TV (Miracast)")


class _Devices:
    def __init__(self):
        # The TV twice: some backends list one endpoint under two roles.
        self.outputs = [_SPEAKERS, _TV, _Dev(b"tv", "Living room TV (Miracast)")]

    def audioOutputs(self):
        return list(self.outputs)

    @staticmethod
    def defaultAudioOutput():
        return _SPEAKERS


class _Sig:
    def __init__(self):
        self.fired = 0

    def emit(self):
        self.fired += 1


@pytest.fixture
def stub(monkeypatch):
    monkeypatch.setattr(QtMultimedia, "QMediaDevices", _Devices)
    s = _Stub()
    s._default_waves_prefs = _bind(s, "_default_waves_prefs")
    s._waves_prefs = s._default_waves_prefs()
    s._waves_pref_bool = _bind(s, "_waves_pref_bool")
    s._save_waves_prefs = lambda: None
    s._factory_reset = False
    s._media_devices = _Devices()
    s.audioOutputChanged = _Sig()
    for name in (
        "_audio_devices",
        "_audio_real_outputs",
        "_audio_output_name_for",
        "audioOutputs",
        "audioOutputDevice",
        "audioOutputMissing",
        "audioOutputLive",
        "setWavesPref",
    ):
        setattr(s, name, _bind(s, name))
    return s


def test_the_pick_defaults_to_the_system_default(stub):
    assert stub._waves_prefs["audio_output"] == ""
    assert stub._waves_prefs["audio_output_name"] == ""
    assert stub.audioOutputDevice() is _SPEAKERS
    assert stub.audioOutputMissing() is False


def test_rows_lead_with_system_default_and_list_each_device_once(stub):
    rows = stub.audioOutputs()
    assert rows[0]["value"] == "" and rows[0]["label"] == "System default"
    # System default names the device it resolves to.
    assert rows[0]["sub"] == "MacBook Speakers"
    assert [r["value"] for r in rows[1:]] == [b"spk".hex(), b"tv".hex()]
    assert [r["default"] for r in rows[1:]] == [True, False]
    assert not any(r["missing"] for r in rows)


def test_a_pick_stores_the_id_and_name_and_tells_the_ui(stub, caplog):
    with caplog.at_level(logging.INFO, logger="waves.audio"):
        stub.setWavesPref("audio_output", b"tv".hex())
    assert stub._waves_prefs["audio_output"] == b"tv".hex()
    assert stub._waves_prefs["audio_output_name"] == "Living room TV (Miracast)"
    assert stub.audioOutputChanged.fired == 1, "Main.qml only re-resolves the device on audioOutputChanged"
    assert stub.audioOutputDevice() is _TV
    assert stub.audioOutputLive() == "Living room TV (Miracast)"
    # Neither the name (a system string that can carry a person's name) nor
    # the id ever logs, only which kind of pick it was.
    assert "a picked device" in caplog.text
    assert "Living room TV" not in caplog.text
    assert b"tv".hex() not in caplog.text
    # The same pick again changes nothing and stays quiet.
    stub.setWavesPref("audio_output", b"tv".hex())
    assert stub.audioOutputChanged.fired == 1


def test_an_unplugged_pick_falls_back_and_keeps_its_name(stub):
    stub.setWavesPref("audio_output", b"tv".hex())
    stub._media_devices.outputs = [_SPEAKERS]
    assert stub.audioOutputMissing() is True
    assert stub.audioOutputDevice() is _SPEAKERS, "a gone pick must play through the system default"
    rows = stub.audioOutputs()
    # The gone pick sits right under System default, by its saved name.
    assert rows[1] == {
        "value": b"tv".hex(),
        "label": "Living room TV (Miracast)",
        "sub": "",
        "default": False,
        "missing": True,
    }
    # Re-saving the gone pick (a page save, a restart) keeps the name.
    stub.setWavesPref("audio_output", b"tv".hex())
    assert stub._waves_prefs["audio_output_name"] == "Living room TV (Miracast)"


def test_picking_system_default_clears_the_name(stub):
    stub.setWavesPref("audio_output", b"tv".hex())
    stub.setWavesPref("audio_output", "")
    assert stub._waves_prefs["audio_output"] == ""
    assert stub._waves_prefs["audio_output_name"] == ""
    assert stub.audioOutputChanged.fired == 2


def test_the_pick_has_its_own_playback_section_after_appearance():
    from tests.test_video_hover_peek_pref import _schema_stub

    schema = WavesBridge.settingsSchema(_schema_stub())
    ids = [s["id"] for s in schema]
    assert ids.index("playback") == ids.index("appearance") + 1
    playback = schema[ids.index("playback")]
    assert playback["group"] == "Playback" and playback["desc"]
    assert [f["key"] for f in playback["fields"]] == ["audio_output"]
    field = playback["fields"][0]
    assert field["type"] == "device"
    assert field["value"] == ""
    assert field["label"] == "Audio output"
    assert 0 < len(field["help"]) <= 115


def test_a_machine_with_no_output_names_the_chip(stub, monkeypatch):
    """A headless box lists no output and Qt's default device has no name;
    the bar's chip would be an empty pill."""
    monkeypatch.setattr(_Devices, "defaultAudioOutput", staticmethod(lambda: _Dev(b"", "")))
    assert stub.audioOutputLive() == "No output"


def test_the_saved_id_is_registered_as_a_secret(stub, monkeypatch):
    """A CoreAudio or PulseAudio id can carry the device's MAC address and a
    WASAPI endpoint id is unique to the machine: the hex and the decoded
    form both go to the redactor, on a pick and on load."""
    from waves.waves_ui import backend

    noted = []
    monkeypatch.setattr(backend.diagnostics, "register_secret", lambda v, ph="": noted.append((v, ph)))
    stub.setWavesPref("audio_output", b"tv".hex())
    assert (b"tv".hex(), "‹audio-output›") in noted
    assert ("tv", "‹audio-output›") in noted
    noted.clear()
    stub.setWavesPref("audio_output", "")
    assert noted == [], "the system default has no id to hide"


def test_a_wrong_shaped_pref_on_disk_goes_back_to_stock(tmp_path, monkeypatch):
    """A list where the output id belongs raised (a list "in" a set of ids)
    on every open of the output menu; a number where a word belongs became
    the literal "5". Each goes back to its default and the rest survive."""
    import json

    from waves.waves_ui import backend

    noted = []
    monkeypatch.setattr(backend.diagnostics, "register_secret", lambda v, ph="": noted.append(v))
    s = _Stub()
    s._default_waves_prefs = _bind(s, "_default_waves_prefs")
    s._waves_prefs_path = str(tmp_path / "waves.json")
    (tmp_path / "waves.json").write_text(
        json.dumps({"audio_output": ["x"], "explicit_mode": 5, "audio_output_name": "TV", "collapse_editions": False}),
        encoding="utf-8",
    )
    prefs = _bind(s, "_load_waves_prefs")()
    assert prefs["audio_output"] == ""
    assert prefs["explicit_mode"] == "explicit"
    assert prefs["audio_output_name"] == "TV"
    assert prefs["collapse_editions"] is False, "the rest of the file survives"
    assert noted == [], "an empty id is nothing to hide"
    (tmp_path / "waves.json").write_text(json.dumps({"audio_output": b"tv".hex()}), encoding="utf-8")
    prefs = _bind(s, "_load_waves_prefs")()
    assert prefs["audio_output"] == b"tv".hex()
    assert b"tv".hex() in noted, "a saved id is registered the moment it is read"
