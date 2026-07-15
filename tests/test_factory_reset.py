"""Advanced-settings reset actions.

Two behaviors, tested against method-bound stubs (the window-geometry tests'
pattern) so no display or live bridge is needed:

* ``_factory_default_values`` produces a value for every schema key, shaped
  the way applySettings expects (enums by name, prefs from the waves.json
  defaults), and never touches housekeeping keys.
* ``factoryReset`` wipes the whole config directory except the
  installer-owned ``install_channel`` sentinel, latches the persistence
  freeze, and swaps the ownership store for a throwaway.
"""

from __future__ import annotations

import os

from tidaler.waves_ui import backend as backend_mod
from tidaler.waves_ui.backend import _FIRST_RUN_OVERRIDES, WavesBridge


class _Stub:
    """Bare object the real methods get bound onto."""


def _bind(stub, name):
    return getattr(WavesBridge, name).__get__(stub, type(stub))


# --------------------------------------------------------------------------- #
# _factory_default_values
# --------------------------------------------------------------------------- #
def _values_stub():
    stub = _Stub()
    stub._default_waves_prefs = _bind(stub, "_default_waves_prefs")
    # A canned schema: one tidaler enum field, one tidaler flag, one waves
    # pref, one composite carrying file_key + child_key, and one unknown key
    # that must be skipped rather than crash.
    stub.settingsSchema = lambda: [
        {
            "group": "G",
            "fields": [
                {"key": "quality_audio"},
                {"key": "video_download"},
                {"key": "explicit_mode"},
                {
                    "key": "metadata_cover_dimension",
                    "file_key": "metadata_cover_file_dimension",
                    "child_key": None,
                },
                {"key": "cover_album_file", "child_key": "cover_single_track_file"},
                {"key": "not_a_real_key"},
            ],
        }
    ]
    return stub


def test_factory_defaults_cover_schema_keys_in_apply_shape():
    values = _bind(_values_stub(), "_factory_default_values")()
    # Tidaler enum arrives by NAME (what applySettings indexes _ENUM_BY_FIELD with).
    assert isinstance(values["quality_audio"], str)
    # First-run override wins over the stock dataclass default.
    assert values["video_download"] is _FIRST_RUN_OVERRIDES["video_download"]
    # Waves pref comes from the waves.json defaults.
    assert values["explicit_mode"] == "explicit"
    # Composite sub-keys are resolved too.
    assert "metadata_cover_file_dimension" in values
    assert "cover_single_track_file" in values
    # Unknown keys are skipped, not invented.
    assert "not_a_real_key" not in values


def test_factory_defaults_leave_housekeeping_alone():
    values = _bind(_values_stub(), "_factory_default_values")()
    for key in ("win_x", "win_w", "win_max", "update_last_check", "search_sec_albums_expanded"):
        assert key not in values, f"housekeeping key {key} must not be reset"


# --------------------------------------------------------------------------- #
# factoryReset
#
# The safety property under test is structural: the wipe is an allowlist of
# exact Waves-written names with no recursive deletion, so it must be
# INCAPABLE of touching a user's file even when one sits inside (or is
# symlinked into) Waves' own folders.
# --------------------------------------------------------------------------- #
class _FakeOwnership:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakeQSettings:
    cleared = False

    def clear(self):
        _FakeQSettings.cleared = True

    def sync(self):
        pass


class _FakeQtCore:
    QSettings = _FakeQSettings


def _run_factory_reset(base, monkeypatch):
    monkeypatch.setattr(backend_mod, "path_config_base", lambda: str(base))
    monkeypatch.setattr(backend_mod.diagnostics, "detach_disk_log", lambda: None)
    _FakeQSettings.cleared = False
    monkeypatch.setattr(backend_mod, "QtCore", _FakeQtCore)
    stub = _Stub()
    stub._factory_reset = False
    stub._ownership = _FakeOwnership()
    original_store = stub._ownership
    _bind(stub, "factoryReset")()
    return stub, original_store


def test_factory_reset_wipes_waves_files_and_keeps_install_channel(tmp_path, monkeypatch):
    base = tmp_path / "cfg"
    base.mkdir()
    for name in (
        "settings.json",
        "settings.json.bak",
        "token.json",
        "waves.json",
        "waves.json.tmp",
        "page_cache.json",
        "browse_tile_art.json",
        "ownership.sqlite3",
        "ownership.sqlite3-wal",
        "crash.log",
        "crash.log.1",
        "waves_dev.log",
        "waves_dev.log.3",
        "app.log",
    ):
        (base / name).write_text("x")
    (base / "install_channel").write_text("homebrew")
    (base / "bin").mkdir()
    (base / "bin" / "ffmpeg").write_text("x")
    (base / "bin" / "ffmpeg.json").write_text("{}")
    (base / "updates").mkdir()
    (base / "updates" / "applied.json").write_text("{}")
    (base / "updates" / "staged").mkdir()

    stub, original_store = _run_factory_reset(base, monkeypatch)

    assert stub._factory_reset is True, "persistence freeze must latch"
    assert original_store.closed, "the on-disk ownership store is closed first"
    assert stub._ownership is not original_store, "queries after the wipe hit a throwaway store"
    assert sorted(os.listdir(base)) == ["install_channel"], "every Waves file (and empty subdir) is gone"
    assert (base / "install_channel").read_text() == "homebrew"
    assert _FakeQSettings.cleared, "the QML setup flags are cleared too"


def test_factory_reset_cannot_touch_foreign_files(tmp_path, monkeypatch):
    """A user's own files inside the config folder must survive untouched:
    unknown top-level names, unknown names inside Waves' subdirs (which then
    also keep the subdir alive), and whole foreign directories."""
    base = tmp_path / "cfg"
    base.mkdir()
    (base / "settings.json").write_text("x")
    (base / "vacation-notes.txt").write_text("precious")
    (base / "waves_dev.log.backup").write_text("precious")  # not the numeric rotation pattern
    (base / "tax-records").mkdir()
    (base / "tax-records" / "2025.pdf").write_text("precious")
    (base / "bin").mkdir()
    (base / "bin" / "ffmpeg").write_text("x")
    (base / "bin" / "my-own-tool").write_text("precious")

    _run_factory_reset(base, monkeypatch)

    assert not (base / "settings.json").exists()
    assert not (base / "bin" / "ffmpeg").exists()
    assert (base / "vacation-notes.txt").read_text() == "precious"
    assert (base / "waves_dev.log.backup").read_text() == "precious"
    assert (base / "tax-records" / "2025.pdf").read_text() == "precious"
    assert (base / "bin" / "my-own-tool").read_text() == "precious", "foreign file in bin survives"
    assert (base / "bin").is_dir(), "a non-empty bin is kept, never force-removed"


def test_factory_reset_never_deletes_through_a_symlinked_subdir(tmp_path, monkeypatch):
    """If something replaced Waves' bin/ with a symlink into a user directory,
    the wipe must not follow it: the target's contents stay, even one named
    exactly like Waves' own ffmpeg binary."""
    outside = tmp_path / "user-tools"
    outside.mkdir()
    (outside / "ffmpeg").write_text("the user's own build")
    base = tmp_path / "cfg"
    base.mkdir()
    (base / "settings.json").write_text("x")
    os.symlink(outside, base / "bin")

    _run_factory_reset(base, monkeypatch)

    assert (outside / "ffmpeg").read_text() == "the user's own build"
    assert (base / "bin").is_symlink(), "the foreign symlink itself is left alone"


def test_factory_reset_wipe_has_no_recursive_delete():
    """Guard the structural property itself: the wipe code must never grow a
    recursive delete. os.remove + os.rmdir are the only removal primitives
    allowed in factoryReset."""
    import inspect

    src = inspect.getsource(WavesBridge.factoryReset)
    assert "rmtree" not in src, "recursive deletion must never enter factoryReset"
    assert "walk(" not in src, "directory walking must never enter factoryReset"


def test_factory_reset_freeze_blocks_pref_saves(tmp_path):
    stub = _Stub()
    stub._factory_reset = True
    stub._waves_prefs_path = str(tmp_path / "waves.json")
    stub._waves_prefs = {"explicit_mode": "explicit"}
    _bind(stub, "_save_waves_prefs")()
    assert not os.path.exists(stub._waves_prefs_path), "no pref file may re-appear after the wipe"
