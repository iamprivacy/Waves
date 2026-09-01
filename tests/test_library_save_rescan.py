"""SAVE CHANGES starts a library scan only when a library setting really moved.

The Settings page deliberately keeps its edit map populated after a save (so the
controls go on showing the values that just landed, instead of reverting to the
now-stale schema defaults), and it only clears on the next open. So every save
after the first one in a single visit resubmits the library keys unchanged.
applySettings used to test whether those keys were PRESENT, which turned every
later save (of any setting at all) into another sweep of the user's library: on a
cold network share that is a slow walk of the whole tree, and it restarted the
card's scan readout for nothing.

Tested with the method-bound stub pattern (no display, no live bridge), the same
shape test_quality_change_live.py uses.
"""

from __future__ import annotations

from threading import Lock
from types import SimpleNamespace

from waves.waves_ui.backend import WavesBridge


class _Stub:
    """Bare object the real applySettings and setWavesPref get bound onto."""

    _waves_pref_bool = WavesBridge._waves_pref_bool
    setWavesPref = WavesBridge.setWavesPref

    def _library_root(self) -> str:
        # The real resolution: the master switch first, then the chosen source.
        if not self._waves_pref_bool("library_enabled"):
            return ""
        if self._waves_prefs.get("library_source") == "download":
            return str(self.settings.data.download_base_path or "")
        return str(self._waves_prefs.get("library_folder") or "")

    def _rebuild_library_index(self) -> None:
        self.rescans.append(self._library_root())

    def _invalidate_library_index(self) -> None:
        self.drops.append(True)


def _signal():
    return SimpleNamespace(emit=lambda *a: None)


def _stub(*, enabled=False, source="separate", folder="", download_base="/dl"):
    s = _Stub()
    s.rescans = []
    s.drops = []
    s._waves_prefs = {
        "library_enabled": enabled,
        "library_source": source,
        "library_folder": folder,
    }
    s._save_waves_prefs = lambda: None  # these tests are about triggers, not persistence
    s.settings = SimpleNamespace(
        data=SimpleNamespace(
            download_base_path=download_base,
            quality_audio="LOW_320K",
            ffmpeg_source="system",
            skip_existing=False,
        ),
        save=lambda: None,
    )
    s._ffmpeg_flag_prefs = {}
    # applySettings does its ffmpeg restores and its write under this lock, the
    # same one _save_settings holds, so a worker save cannot slip its borrowed
    # path into the write. A stub that drives applySettings needs the real thing.
    s._settings_save_lock = Lock()
    # The real bridge hands the disk write to a background writer; the stub
    # runs it inline through its own settings.save seam.
    s._submit_settings_write = lambda: s.settings.save()
    s._restore_ffmpeg_flags = lambda: None
    s._restore_ffmpeg_path = lambda: None
    s._ffmpeg_source_label = lambda: "system"
    s.ownershipChanged = _signal()
    s.editionMergeChanged = _signal()
    s.ffmpegStatusChanged = _signal()
    s.confirmCategoryDlChanged = _signal()
    s.skipExistingChanged = _signal()
    s.librarySourceChanged = _signal()
    s._logged_in = False
    s._set_status = lambda text: None
    return s


def _apply(stub, values):
    WavesBridge.applySettings.__get__(stub, type(stub))(values)


def test_turning_the_library_on_scans_once():
    s = _stub()
    _apply(s, {"library_enabled": True, "library_source": "separate", "library_folder": "/lib"})
    assert s.rescans == ["/lib"]


def test_saving_the_same_library_values_again_does_not_rescan():
    # The edit map still carries the library keys on every later save in the
    # same visit; resubmitting them unchanged must not walk the library again.
    s = _stub()
    staged = {"library_enabled": True, "library_source": "separate", "library_folder": "/lib"}
    _apply(s, staged)
    assert s.rescans == ["/lib"]
    _apply(s, staged)
    assert s.rescans == ["/lib"], "a repeat save rescanned the library"


def test_unrelated_save_with_library_keys_riding_along_does_not_rescan():
    s = _stub(enabled=True, folder="/lib")
    _apply(
        s,
        {
            "skip_existing": True,
            "library_enabled": True,
            "library_source": "separate",
            "library_folder": "/lib",
        },
    )
    assert s.rescans == [], "an unrelated setting change rescanned the library"
    assert s.settings.data.skip_existing is True  # the real edit still landed


def test_moving_the_library_folder_rescans():
    s = _stub(enabled=True, folder="/lib")
    _apply(s, {"library_enabled": True, "library_source": "separate", "library_folder": "/other"})
    assert s.rescans == ["/other"]
    assert s.drops, "the old folder's badges were never dropped"


def test_turning_the_library_off_drops_badges_and_scans_nothing():
    s = _stub(enabled=True, folder="/lib")
    _apply(s, {"library_enabled": False, "library_source": "separate", "library_folder": "/lib"})
    assert s.drops, "the switch going off never dropped the badges"
    assert s.rescans == [], "a disabled library was scanned anyway"


def test_resubmitted_download_folder_does_not_rescan_in_download_mode():
    # Same trap on the other trigger: in download mode the scan follows the
    # download folder, but the folder arriving unchanged is not a move.
    s = _stub(enabled=True, source="download", folder="", download_base="/dl")
    _apply(s, {"download_base_path": "/dl", "library_enabled": True, "library_source": "download"})
    assert s.drops == [], "an unchanged download folder dropped the badges"
    assert s.rescans == []


def test_moved_download_folder_rescans_in_download_mode():
    s = _stub(enabled=True, source="download", folder="", download_base="/dl")
    _apply(s, {"download_base_path": "/moved", "library_enabled": True, "library_source": "download"})
    assert s.drops, "a moved download folder left stale badges up"
    assert s.rescans, "a moved download folder was never rescanned"
