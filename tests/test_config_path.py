"""Platform-native config location + one-shot legacy migration.

path_config_base() must answer with the folder users expect on each OS
(Application Support on macOS, %APPDATA% on Windows, XDG ~/.config elsewhere),
honor an explicit XDG_CONFIG_HOME everywhere, and move a legacy ~/.config
dotfolder over exactly once without ever losing settings: a failed move keeps
the legacy folder authoritative.

Every test isolates HOME/XDG_CONFIG_HOME/APPDATA and fakes sys.platform, so
nothing here reads or moves the developer's real config.
"""

from __future__ import annotations

import os

import pytest

import tidaler.helper.path as path_helper
from tidaler import __config_dirname__


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("HOMEDRIVE", raising=False)
    monkeypatch.delenv("HOMEPATH", raising=False)
    monkeypatch.setattr(path_helper, "CONFIG_MIGRATION", "")
    yield


def _legacy(tmp_path) -> str:
    return os.path.join(str(tmp_path), ".config", __config_dirname__)


def _native_mac(tmp_path) -> str:
    return os.path.join(str(tmp_path), "Library", "Application Support", __config_dirname__)


# --------------------------------------------------------------------------- #
# Location per platform
# --------------------------------------------------------------------------- #
def test_macos_uses_application_support(tmp_path, monkeypatch):
    monkeypatch.setattr(path_helper.sys, "platform", "darwin")
    assert path_helper.path_config_base() == _native_mac(tmp_path)


def test_windows_uses_appdata(tmp_path, monkeypatch):
    monkeypatch.setattr(path_helper.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert path_helper.path_config_base() == os.path.join(str(tmp_path / "Roaming"), __config_dirname__)


def test_windows_without_appdata_falls_back_to_legacy(tmp_path, monkeypatch):
    monkeypatch.setattr(path_helper.sys, "platform", "win32")
    assert path_helper.path_config_base() == _legacy(tmp_path)


def test_linux_stays_xdg(tmp_path, monkeypatch):
    monkeypatch.setattr(path_helper.sys, "platform", "linux")
    assert path_helper.path_config_base() == _legacy(tmp_path)


def test_xdg_config_home_override_wins_everywhere(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for platform in ("darwin", "win32", "linux"):
        monkeypatch.setattr(path_helper.sys, "platform", platform)
        assert path_helper.path_config_base() == os.path.join(str(tmp_path / "xdg"), __config_dirname__)


# --------------------------------------------------------------------------- #
# Migration
# --------------------------------------------------------------------------- #
def test_legacy_folder_migrates_whole(tmp_path, monkeypatch):
    monkeypatch.setattr(path_helper.sys, "platform", "darwin")
    legacy = _legacy(tmp_path)
    os.makedirs(legacy)
    with open(os.path.join(legacy, "settings.json"), "w") as f:
        f.write("{}")

    base = path_helper.path_config_base()

    assert base == _native_mac(tmp_path)
    assert os.path.isfile(os.path.join(base, "settings.json"))
    assert not os.path.exists(legacy)
    assert path_helper.CONFIG_MIGRATION == "moved"


def test_legacy_merges_into_installer_created_native(tmp_path, monkeypatch):
    # An installer (the Homebrew cask) may create the native folder first,
    # holding only its install-channel sentinel. The user's legacy settings
    # must still merge in, and the sentinel must survive.
    monkeypatch.setattr(path_helper.sys, "platform", "darwin")
    legacy, native = _legacy(tmp_path), _native_mac(tmp_path)
    os.makedirs(legacy)
    with open(os.path.join(legacy, "settings.json"), "w") as f:
        f.write('{"from": "legacy"}')
    os.makedirs(native)
    with open(os.path.join(native, "install_channel"), "w") as f:
        f.write("homebrew-cask\n")

    base = path_helper.path_config_base()

    assert base == native
    assert os.path.isfile(os.path.join(native, "settings.json"))
    assert os.path.isfile(os.path.join(native, "install_channel"))
    assert not os.path.exists(legacy)


def test_live_native_config_wins_and_legacy_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(path_helper.sys, "platform", "darwin")
    legacy, native = _legacy(tmp_path), _native_mac(tmp_path)
    for d in (legacy, native):
        os.makedirs(d)
        with open(os.path.join(d, "settings.json"), "w") as f:
            f.write("{}")

    assert path_helper.path_config_base() == native
    assert os.path.isdir(legacy), "a live native config must never trigger a merge"
    assert path_helper.CONFIG_MIGRATION == ""


def test_merge_never_overwrites_native_files(tmp_path, monkeypatch):
    monkeypatch.setattr(path_helper.sys, "platform", "darwin")
    legacy, native = _legacy(tmp_path), _native_mac(tmp_path)
    os.makedirs(legacy)
    with open(os.path.join(legacy, "token.json"), "w") as f:
        f.write("legacy-token")
    os.makedirs(native)
    with open(os.path.join(native, "token.json"), "w") as f:
        f.write("native-token")

    path_helper.path_config_base()

    with open(os.path.join(native, "token.json")) as f:
        assert f.read() == "native-token"
    assert os.path.isfile(os.path.join(legacy, "token.json")), "the colliding legacy file stays put"


def test_failed_migration_keeps_legacy_authoritative(tmp_path, monkeypatch):
    monkeypatch.setattr(path_helper.sys, "platform", "darwin")
    legacy = _legacy(tmp_path)
    os.makedirs(legacy)
    with open(os.path.join(legacy, "settings.json"), "w") as f:
        f.write("{}")

    def _boom(*a, **k):
        raise OSError("disk says no")

    monkeypatch.setattr(path_helper.shutil, "move", _boom)

    assert path_helper.path_config_base() == legacy
    assert path_helper.CONFIG_MIGRATION == "failed"
    # And it stays legacy on later calls without retrying the move.
    assert path_helper.path_config_base() == legacy


def test_fresh_install_needs_no_migration(tmp_path, monkeypatch):
    monkeypatch.setattr(path_helper.sys, "platform", "darwin")
    assert path_helper.path_config_base() == _native_mac(tmp_path)
    assert path_helper.CONFIG_MIGRATION == ""
