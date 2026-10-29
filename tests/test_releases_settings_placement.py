"""Where the Releases settings live, and that their words fit their tiles.

Following the library's artists in Releases only helps if people find it, and
they are in the Library card while they set up the scan it feeds on, so the
switch is a tile there (on by default) instead of a fourth Releases switch.
The Releases section keeps the switches that work with no library at all.

The tiles hold a fixed number of lines and elide the rest, so a help that
grows past it reads as "...". The caps below keep each help inside a
third-width tile at the narrowest window, the tightest shape the grid uses.
"""

from __future__ import annotations

import pathlib
import re

from waves.waves_ui.backend import WavesBridge

_QML = pathlib.Path(__file__).resolve().parents[1] / "waves" / "waves_ui" / "qml" / "SettingsPage.qml"

# A third-width tile at the minimum window holds about 36 characters a line
# over four lines; the label gets one line at a larger size.
_HELP_MAX = 140
_LABEL_MAX = 42


class _Stub:
    """Bare object the real methods get bound onto."""


def _bind(stub, name):
    return getattr(WavesBridge, name).__get__(stub, type(stub))


def _schema_stub():
    """Just enough state for settingsSchema(), on a defaults-only config."""
    from waves.model.cfg import HelpSettings
    from waves.model.cfg import Settings as CfgSettings

    class _Cfg:
        data = CfgSettings()
        help = HelpSettings()

    stub = _Stub()
    stub._default_waves_prefs = _bind(stub, "_default_waves_prefs")
    stub._waves_prefs = stub._default_waves_prefs()
    stub._waves_pref_bool = _bind(stub, "_waves_pref_bool")
    stub.settings = _Cfg()
    stub._help = HelpSettings()
    stub._help_for = _bind(stub, "_help_for")
    stub._ffmpeg_flag_prefs = {}
    stub.ffmpegState = lambda: {"status": "none", "source": "none", "path": ""}
    stub._user_ffmpeg_path = lambda: ""
    stub._ffmpeg_detected_path = lambda: ""
    return stub


def _section(schema, sid):
    return next(s for s in schema if s["id"] == sid)


def test_auto_follow_is_on_by_default():
    assert _schema_stub()._waves_prefs["releases_auto_import"] is True


def test_auto_follow_rides_in_the_library_card_not_releases():
    schema = WavesBridge.settingsSchema(_schema_stub())
    releases = _section(schema, "releases")
    assert "releases_auto_import" not in [f["key"] for f in releases["fields"]]
    library = next(f for f in _section(schema, "library")["fields"] if f["key"] == "library")
    # Naming it on the composite is what lets RESET ALL SETTINGS find it.
    assert library["follow_key"] == "releases_auto_import"


def test_reset_all_restores_auto_follow_on():
    stub = _schema_stub()
    stub.settingsSchema = lambda: WavesBridge.settingsSchema(stub)
    values = _bind(stub, "_factory_default_values")()
    assert values["releases_auto_import"] is True


def test_releases_and_notifications_words_fit_their_tiles(monkeypatch):
    # Both sections' fields, the banner ones included (the Notifications
    # section only appears where desktop-notifier is installed).
    monkeypatch.setattr("waves.waves_ui.backend.notifications_supported", lambda: True)
    schema = WavesBridge.settingsSchema(_schema_stub())
    fields = _section(schema, "releases")["fields"] + _section(schema, "notifications")["fields"]
    assert any(f["key"] == "releases_notify" for f in fields)
    for f in fields:
        assert len(f["label"]) <= _LABEL_MAX, f["key"]
        assert len(f["help"]) <= _HELP_MAX, f"{f['key']}: {len(f['help'])} chars"
        # The switch already shows its state.
        assert "Off by default" not in f["help"], f["key"]


def _library_tiles():
    src = _QML.read_text(encoding="utf-8")
    flow = src[src.index("id: libFlagFlow") :]
    flow = flow[: flow.index("// String / path")]
    return re.findall(r'keyName: "([^"]+)".*?label: "([^"]+)"\s*help: "([^"]+)"', flow, re.S)


def test_library_card_carries_the_auto_follow_tile():
    tiles = _library_tiles()
    assert [k for k, _, _ in tiles] == [
        "library_bulk_skip",
        "library_mb_arbiter",
        "library_mb_lookup",
        "releases_auto_import",
    ]
    for key, label, help_ in tiles:
        assert len(label) <= _LABEL_MAX, key
        assert len(help_) <= _HELP_MAX, key


def test_library_tiles_share_one_width():
    # Never one option stretched across the whole row: the card's four tiles
    # sit two across in an even grid, all the same width.
    src = _QML.read_text(encoding="utf-8")
    flow = src[src.index("id: libFlagFlow") :]
    flow = flow[: flow.index("// The follow floor")]
    widths = re.findall(r"^\s*width: (.+)$", flow, re.M)[1:]
    assert widths == ["(libFlagFlow.width - 10) / 2"] * 4, widths


def test_auto_follow_does_not_hold_rescan():
    # Following is not scan configuration: staging it must not make the card
    # "dirty", which would gray Rescan until SAVE CHANGES.
    src = _QML.read_text(encoding="utf-8")
    dirty = next(line for line in src.splitlines() if "readonly property bool libDirty:" in line)
    assert "followE" not in dirty
    assert 'page.editMap["releases_auto_import"]' in src


def test_saving_re_reads_the_library_card_mirrors():
    # The auto-follow tile falls back to its mirror once CANCEL clears the
    # edits: a save that left the mirror at the page-open value showed the old
    # value (and took two clicks to change).
    src = _QML.read_text(encoding="utf-8")
    save = next(line for line in src.splitlines() if "waves.applySettings(page.editMap)" in line)
    assert "page.syncLibraryMirrors()" in save
    sync = src[src.index("function syncLibraryMirrors()") :]
    assert 'libraryFollowLive = waves.wavesPref("releases_auto_import") === true' in sync[: sync.index("\n    }")]
    assert 'libraryMbLookupLive = waves.wavesPref("library_mb_lookup") === true' in sync[: sync.index("\n    }")]


def test_the_follow_floor_is_five_tracks_and_rides_in_the_library_card():
    stub = _schema_stub()
    assert stub._waves_prefs["releases_import_min_tracks"] == "5"
    schema = WavesBridge.settingsSchema(stub)
    library = next(f for f in _section(schema, "library")["fields"] if f["key"] == "library")
    assert library["min_tracks_key"] == "releases_import_min_tracks"
    assert all(f["key"] != "releases_import_min_tracks" for s in schema for f in s["fields"])


def test_the_follow_floor_row_sits_under_the_tiles_and_does_not_hold_rescan():
    src = _QML.read_text(encoding="utf-8")
    row = src[src.index("id: libFloorRow") :]
    row = row[: row.index("// String / path")]
    assert 'page.setv("releases_import_min_tracks", v)' in row
    assert "minimum: 1; maximum: 999" in row
    # The label says the count and the word, not only the stepper's suffix.
    assert 'text: "Follow artists with at least " + libraryCol.minTracksE' in row and 'text: "tracks"' in row
    # Earlier follows under a raised floor: counted, offered for unfollowing.
    assert "waves.unfollowUnderFloor()" in row and "waves.followsUnderFloor()" in src
    assert "function onFollowFloorChanged()" in src
    dirty = next(line for line in src.splitlines() if "readonly property bool libDirty:" in line)
    assert "minTracksE" not in dirty
    # Mirrored like the other library prefs: read on open, after a save and
    # after a reset, and again when the floor moves.
    assert src.count('Number(waves.wavesPref("releases_import_min_tracks")) || 5') == 4


def test_saving_the_floor_stores_a_count_and_looks_at_the_library_again():
    stub = _Stub()
    stub._waves_prefs = {"releases_import_min_tracks": "5"}
    stub._save_waves_prefs = lambda: None
    looks: list = []
    stub._releases_import_floor_changed = lambda: looks.append(1)
    save = _bind(stub, "setWavesPref")
    for given, stored in ((7, "7"), (7.0, "7"), ("9", "9"), ("0", "1"), ("5000", "999")):
        save("releases_import_min_tracks", given)
        assert stub._waves_prefs["releases_import_min_tracks"] == stored, given
    # Four changes: 7.0 after 7 is the same count, and looks again for nothing.
    assert len(looks) == 4
    save("releases_import_min_tracks", "junk")
    assert stub._waves_prefs["releases_import_min_tracks"] == "999" and len(looks) == 4
