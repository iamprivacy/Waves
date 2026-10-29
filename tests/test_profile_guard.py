"""Test data never reaches a real profile, and none that did reaches Releases.

A scenario test run as a script (``python tests/test_x.py --run-scenario``)
skips pytest and conftest, so nothing gives it a scratch config folder: on
2026-10-14 one wrote its fixture artists into a real profile, where they sat
in the Releases tab unchecked. Two nets:

1. path_config_base refuses a test file run directly with no XDG_CONFIG_HOME.
2. The follow store drops ids TIDAL never issues (the app does it on open).
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

from waves.follows import FollowStore

ROOT = Path(__file__).resolve().parent.parent


def _run_as_test_script(tmp_path: Path, xdg: str | None) -> subprocess.CompletedProcess:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    script = tests_dir / "test_direct_run.py"
    script.write_text(textwrap.dedent("""
            from waves.helper.path import path_config_base
            print("BASE", path_config_base())
            """))
    env = dict(os.environ)
    # A throwaway home either way: a broken guard must still miss the real one.
    env["HOME"] = str(tmp_path / "home")
    env["PYTHONPATH"] = str(ROOT)
    env.pop("XDG_CONFIG_HOME", None)
    if xdg is not None:
        env["XDG_CONFIG_HOME"] = xdg
    return subprocess.run(
        [sys.executable, str(script)], env=env, capture_output=True, text=True, timeout=120, check=False
    )


def test_a_test_file_run_directly_cannot_reach_the_profile(tmp_path):
    proc = _run_as_test_script(tmp_path, None)
    assert proc.returncode != 0
    assert "would use the real Waves profile" in proc.stderr
    assert "BASE" not in proc.stdout


def test_a_test_file_with_its_own_config_folder_runs(tmp_path):
    scratch = tmp_path / "xdg"
    proc = _run_as_test_script(tmp_path, str(scratch))
    assert proc.returncode == 0, proc.stderr
    assert f"BASE {scratch}" in proc.stdout


def test_the_store_drops_ids_tidal_never_issues(tmp_path):
    store = FollowStore(str(tmp_path / "releases.sqlite3"))
    store.follow("4050205", "Real", "")
    store.follow("ar9", "Nine", "")
    store.follow_many([{"id": "im1", "name": "Fixture"}])
    store.record_check(
        "ar9",
        [{"id": "r1", "title": "Fake", "date": "2026-10-01", "type": "album", "artist": "Nine"}],
    )
    assert store.drop_foreign_ids() == 2
    assert store.followed_ids() == {"4050205"}
    assert store.drop_foreign_ids() == 0


def test_the_app_drops_them_when_it_opens_the_store():
    src = (ROOT / "waves" / "waves_ui" / "backend.py").read_text()
    opened = src.index("self._releases = FollowStore(_rel_file)")
    assert "drop_foreign_ids()" in src[opened : opened + 800]
