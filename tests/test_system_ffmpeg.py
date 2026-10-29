"""An FFmpeg the user installed is found even when the app's PATH is bare."""

from __future__ import annotations

import stat
import sys

import pytest

from waves.helper import system_ffmpeg

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX executable bits")


def _make(folder, executable=True):
    exe = folder / "ffmpeg"
    exe.write_text("#!/bin/sh\n")
    if executable:
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return str(exe)


def test_path_hit_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(system_ffmpeg.shutil, "which", lambda name: "/on/path/ffmpeg")
    monkeypatch.setattr(system_ffmpeg, "_known_dirs", lambda: (str(tmp_path),))
    _make(tmp_path)
    assert system_ffmpeg.find_system_ffmpeg() == "/on/path/ffmpeg"


def test_package_manager_folder_found_when_path_is_bare(tmp_path, monkeypatch):
    monkeypatch.setattr(system_ffmpeg.shutil, "which", lambda name: None)
    missing = tmp_path / "missing"
    present = tmp_path / "brew"
    present.mkdir()
    exe = _make(present)
    monkeypatch.setattr(system_ffmpeg, "_known_dirs", lambda: (str(missing), str(present)))
    assert system_ffmpeg.find_system_ffmpeg() == exe


def test_non_executable_file_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(system_ffmpeg.shutil, "which", lambda name: None)
    _make(tmp_path, executable=False)
    monkeypatch.setattr(system_ffmpeg, "_known_dirs", lambda: (str(tmp_path),))
    assert system_ffmpeg.find_system_ffmpeg() == ""


def test_a_broken_install_is_passed_over_for_one_that_works(tmp_path, monkeypatch):
    broken = tmp_path / "local"
    brew = tmp_path / "brew"
    broken.mkdir()
    brew.mkdir()
    bad, good = _make(broken), _make(brew)
    monkeypatch.setattr(system_ffmpeg.shutil, "which", lambda name: None)
    monkeypatch.setattr(system_ffmpeg, "_known_dirs", lambda: (str(broken), str(brew)))
    assert system_ffmpeg.find_system_ffmpeg(works=lambda p: p == good) == good
    assert system_ffmpeg.find_system_ffmpeg(works=lambda p: False) == ""
    assert system_ffmpeg.find_system_ffmpeg() == bad


def test_status_never_reports_a_broken_system_ffmpeg_installed(tmp_path, monkeypatch):
    from waves.waves_ui import ffmpeg_manager as fm

    _make(tmp_path)
    monkeypatch.setattr(system_ffmpeg.shutil, "which", lambda name: None)
    monkeypatch.setattr(system_ffmpeg, "_known_dirs", lambda: (str(tmp_path),))
    monkeypatch.setattr(fm, "_probe_version", lambda path: "")
    assert fm._which_ffmpeg("macos") == ""


def _broken_then_working(tmp_path, monkeypatch):
    from waves.waves_ui import ffmpeg_manager as fm

    broken = tmp_path / "local"
    brew = tmp_path / "brew"
    broken.mkdir()
    brew.mkdir()
    good = _make(brew)
    _make(broken)
    monkeypatch.setattr(system_ffmpeg.shutil, "which", lambda name: None)
    monkeypatch.setattr(system_ffmpeg, "_known_dirs", lambda: (str(broken), str(brew)))
    monkeypatch.setattr(fm, "_safe_target", lambda: ("macos", "arm64"))
    monkeypatch.setattr(fm, "_probe_version", lambda path: "n8.1" if path == good else "")
    return good


def test_a_download_runs_the_ffmpeg_settings_shows(tmp_path, monkeypatch):
    """A broken first candidate is passed over by the download engine too, not
    only by the status card, so Settings never names one binary while every
    lossless track fails on another."""
    from unittest.mock import MagicMock

    from waves.config import Settings
    from waves.download import Download
    from waves.waves_ui import ffmpeg_manager as fm

    good = _broken_then_working(tmp_path, monkeypatch)
    assert fm.FfmpegManager(tmp_path / "app").status()["path"] == good
    monkeypatch.setattr(Settings().data, "path_binary_ffmpeg", None)
    dl = Download(
        tidal_obj=MagicMock(), skip_existing=False, path_base=str(tmp_path), fn_logger=MagicMock(), progress=MagicMock()
    )
    assert dl.settings.data.path_binary_ffmpeg == good


def test_preview_flac_check_and_source_label_pass_over_a_broken_ffmpeg(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from waves.waves_ui import ffmpeg_manager as fm
    from waves.waves_ui.backend import WavesBridge
    from waves.waves_ui.bridge_flac import FlacCheckMixin

    good = _broken_then_working(tmp_path, monkeypatch)
    stub = SimpleNamespace(
        settings=SimpleNamespace(data=SimpleNamespace(path_binary_ffmpeg=None)),
        _resolve_ffmpeg=lambda: None,
        _user_ffmpeg_path=lambda: "",
        _ffmpeg=SimpleNamespace(is_installed=lambda: False),
    )
    assert WavesBridge._preview_ffmpeg_bin(stub) == good
    assert FlacCheckMixin._flac_check_ffmpeg(stub) == good
    assert WavesBridge._ffmpeg_source_label(stub) == "system"
    monkeypatch.setattr(fm, "_probe_version", lambda path: "")
    assert WavesBridge._preview_ffmpeg_bin(stub) is None
    assert FlacCheckMixin._flac_check_ffmpeg(stub) == ""
    assert WavesBridge._ffmpeg_source_label(stub) == "none"


def test_an_ffmpeg_too_slow_for_the_probe_is_kept_and_not_probed_again(tmp_path, monkeypatch):
    """A cold first run (snap, a virus scan) that outlasts the probe is not a
    broken install: downloads keep it, and the GUI thread (status()) never
    forks it again."""
    import subprocess

    from waves.waves_ui import ffmpeg_manager as fm

    exe = _make(tmp_path)
    monkeypatch.setattr(system_ffmpeg.shutil, "which", lambda name: None)
    monkeypatch.setattr(system_ffmpeg, "_known_dirs", lambda: (str(tmp_path),))
    monkeypatch.setattr(fm, "_safe_target", lambda: ("linux", "x86_64"))
    monkeypatch.setattr(fm, "_probe_cache", {})
    monkeypatch.setattr(fm, "_probe_slow", {})
    runs = []

    def slow(argv, **kw):
        runs.append(argv)
        raise subprocess.TimeoutExpired(argv, kw.get("timeout"))

    monkeypatch.setattr(fm.subprocess, "run", slow)
    assert fm.working_system_ffmpeg() == exe
    assert fm.working_system_ffmpeg() == exe
    assert len(runs) == 1


def test_a_slow_ffmpeg_does_not_shadow_a_working_one(tmp_path, monkeypatch):
    """The first candidate hangs the probe (it may be hung for good): a copy
    further down the list that answers is the one jobs and status get."""
    import subprocess
    from types import SimpleNamespace

    from waves.waves_ui import ffmpeg_manager as fm

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    hung = _make(tmp_path / "a")
    good = _make(tmp_path / "b")
    monkeypatch.setattr(system_ffmpeg.shutil, "which", lambda name: None)
    monkeypatch.setattr(system_ffmpeg, "_known_dirs", lambda: (str(tmp_path / "a"), str(tmp_path / "b")))
    monkeypatch.setattr(fm, "_safe_target", lambda: ("linux", "x86_64"))
    monkeypatch.setattr(fm, "_probe_cache", {})
    monkeypatch.setattr(fm, "_probe_slow", {})

    def run(argv, **kw):
        if argv[0] == hung:
            raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
        return SimpleNamespace(returncode=0, stdout="ffmpeg version 7.1 built", stderr="")

    monkeypatch.setattr(fm.subprocess, "run", run)
    assert fm.working_system_ffmpeg() == good
    assert fm._probe_version(good) == "7.1"


def test_a_slow_ffmpeg_is_probed_again_for_a_job_once_its_pause_is_over(tmp_path, monkeypatch):
    """A worker-thread caller (a download, the FLAC check) gives the slow
    copy a fresh probe after its pause: a cold start that warmed up answers,
    and status() shows its real version from then on."""
    import subprocess
    from types import SimpleNamespace

    from waves.waves_ui import ffmpeg_manager as fm

    exe = _make(tmp_path)
    monkeypatch.setattr(system_ffmpeg.shutil, "which", lambda name: None)
    monkeypatch.setattr(system_ffmpeg, "_known_dirs", lambda: (str(tmp_path),))
    monkeypatch.setattr(fm, "_safe_target", lambda: ("linux", "x86_64"))
    monkeypatch.setattr(fm, "_probe_cache", {})
    monkeypatch.setattr(fm, "_probe_slow", {})
    monkeypatch.setattr(fm, "_PROBE_SLOW_RETRY_S", -1.0)  # the pause is already over
    runs = []
    warmed = []

    def run(argv, **kw):
        runs.append(argv)
        if not warmed:
            raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
        return SimpleNamespace(returncode=0, stdout="ffmpeg version 7.1 built", stderr="")

    monkeypatch.setattr(fm.subprocess, "run", run)
    assert fm.working_system_ffmpeg(retry_slow=True) == exe  # still cold: kept as the fallback
    assert len(runs) == 2  # the first probe, and the retry its expired pause allowed
    assert fm.working_system_ffmpeg() == exe and len(runs) == 2  # status() never re-forks
    warmed.append(True)
    assert fm.working_system_ffmpeg(retry_slow=True) == exe
    assert fm._probe_version(exe) == "7.1" and not fm._probe_slow


def test_macos_looks_in_homebrew(monkeypatch):
    monkeypatch.setattr(system_ffmpeg.sys, "platform", "darwin")
    assert "/opt/homebrew/bin" in system_ffmpeg._known_dirs()
