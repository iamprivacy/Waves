"""Pins for the v0.1.31 audit, files and release groups (report in notes/, private)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from tests.test_release_star_history import (
    RELEASE_SH,
    _dry_run,
    _write_hook,
    sandbox,  # noqa: F401  (fixture, reached by name below)
)
from waves.config import BaseConfig, Settings
from waves.helper.decorator import SingletonMeta
from waves.helper.path import path_config_base
from waves.model.cfg import Settings as ModelSettings

_USER_FILE = {"download_base_path": "/music/mine", "format_album": "{album_title}/{track_title}"}


def _config_home(tmp_path: Path, monkeypatch) -> Path:
    """A config folder under tmp_path, found the way the app finds its own,
    and a fresh Settings singleton for this test only (restored afterwards)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    base = Path(path_config_base())
    assert tmp_path in base.parents, "never the real config folder"
    base.mkdir(parents=True)
    monkeypatch.setattr(BaseConfig, "path_base", str(base))
    monkeypatch.delitem(SingletonMeta._instances, Settings, raising=False)
    return base


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads a mode 000 file")
def test_an_unreadable_settings_file_survives_the_launch_and_later_saves(tmp_path, monkeypatch):
    """#25: read() left the file alone, but Settings() then ran the one-time
    migrations on the defaults and saved them, and a rename only needs the
    folder, so a settings.json the user could not read was replaced."""
    base = _config_home(tmp_path, monkeypatch)
    target = base / "settings.json"
    body = json.dumps(_USER_FILE).encode("utf-8")
    target.write_bytes(body)
    target.chmod(0)
    try:
        settings = Settings()
        assert settings.data.download_base_path == ModelSettings().download_base_path, "starts on defaults"
        # A later save from the app (a setting changed this session) holds off too.
        settings.data.download_base_path = "/elsewhere"
        settings.save()
    finally:
        target.chmod(0o600)
    assert target.read_bytes() == body, "the user's file is never written over"
    assert sorted(p.name for p in base.iterdir()) == ["settings.json"], "no stray temp or backup"


def test_a_broken_settings_file_that_cannot_be_set_aside_is_left_as_it_is(tmp_path, monkeypatch):
    """#26: when the move to .bak failed (a lock, a read-only folder), the
    defaults were still written over the only copy of the user's file."""
    import waves.config as config_module

    base = _config_home(tmp_path, monkeypatch)
    target = base / "settings.json"
    body = json.dumps({**_USER_FILE, "window_x": [1, 2]}).encode("utf-8")  # TypeError on load
    target.write_bytes(body)

    def _locked(*_a, **_k):
        raise PermissionError("held by another process")

    monkeypatch.setattr(config_module.shutil, "move", _locked)
    settings = Settings()
    assert settings.data.download_base_path == ModelSettings().download_base_path, "starts on defaults"
    settings.save()
    assert target.read_bytes() == body, "the only copy is never written over"
    assert sorted(p.name for p in base.iterdir()) == ["settings.json"]

    # Control: once the move works, the file is kept as .bak and the defaults
    # (with the migrations) are written as before.
    monkeypatch.undo()
    base = _config_home(tmp_path / "again", monkeypatch)
    target = base / "settings.json"
    target.write_bytes(body)
    Settings()
    assert (base / "settings.json.bak").read_bytes() == body
    assert json.loads(target.read_text(encoding="utf-8"))["replay_gain_default_migrated"] is True


@pytest.mark.skipif(not RELEASE_SH.exists(), reason="release.sh is not part of this checkout")
def test_a_marker_pattern_git_grep_cannot_compile_refuses_the_release(request):
    """#28: BSD grep accepts 'a**' but git grep exits 128 on it, and '|| true'
    read that as a clean tree, so the release went out unscanned."""
    private = request.getfixturevalue("sandbox")
    _write_hook(private, "a**")
    proc = _dry_run(private)
    assert proc.returncode != 0, f"an unscanned tree was released:\n{proc.stdout}"
    assert "content scan could not run" in proc.stderr, proc.stderr
    assert "dry-run tree: " not in proc.stdout


def test_the_readme_states_both_ffmpeg_check_cadences():
    """#29: the Privacy section promised at most one FFmpeg check a day, but
    the "Every launch" cadence asks on every launch."""
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    line = next(ln for ln in readme.splitlines() if "FFmpeg's automatic update check" in ln)
    assert "every launch" in line and "at most once a day" in line, line
