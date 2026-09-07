"""The suite never reads or writes the config of the machine it runs on.

A test that builds a real ``WavesBridge`` resolves its settings, prefs,
ownership database and log from ``path_config_base()``, and ``__init__``
already writes there (``_migrate_video_flag`` stamps waves.json). Without a
sandbox that is the developer's own install: a suite run overwrote a real
library switch, folder and MusicBrainz choice with test defaults. conftest
points XDG_CONFIG_HOME at a throwaway directory at import; these pin that it
is still in force and that it is not somebody's real config folder.
"""

from __future__ import annotations

import os

from waves.helper.path import path_config_base, path_file_settings


def _real_config_homes() -> list[str]:
    """Where a person's own Waves config lives on this machine."""
    home = os.path.expanduser("~")
    return [
        os.path.join(home, "Library", "Application Support"),
        os.path.join(home, ".config"),
        os.environ.get("APPDATA", "") or os.path.join(home, "AppData", "Roaming"),
    ]


def test_the_config_home_is_a_throwaway_directory():
    """conftest set XDG_CONFIG_HOME, and it is still set for this test."""
    sandbox = os.environ.get("XDG_CONFIG_HOME", "")
    assert sandbox, "conftest must point XDG_CONFIG_HOME at a throwaway directory"
    assert "waves-test-config-" in sandbox, sandbox


def test_the_config_base_is_not_a_real_install():
    """Everything the bridge persists resolves inside the sandbox, so no test
    can reach a real settings.json, waves.json or ownership database."""
    base = os.path.realpath(path_config_base())
    assert base.startswith(os.path.realpath(os.environ["XDG_CONFIG_HOME"]) + os.sep), base
    for real in _real_config_homes():
        if not real:
            continue
        assert not base.startswith(os.path.realpath(real) + os.sep), base
    assert os.path.realpath(path_file_settings()).startswith(base + os.sep)
