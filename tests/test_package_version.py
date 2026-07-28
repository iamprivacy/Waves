"""The packaging metadata version must match the version the app reports.

``tidaler.waves_ui.__version__`` is the single source of truth: the in-app
updater compares it against the latest release tag, and CI refuses to build
unless the tag matches it. ``pyproject.toml`` carries its own copy for anyone
installing from source or reading the published tree, and nothing enforced the
two agreeing, so it silently fell two releases behind. This guard makes the
drift a test failure instead of something noticed at release time.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from tidaler.waves_ui import __version__

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_pyproject_version_matches_app_version():
    packaged = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    assert packaged == __version__, (
        f"pyproject.toml says {packaged!r} but tidaler.waves_ui.__version__ is "
        f"{__version__!r}; bump both in the release commit"
    )


def test_app_version_is_a_plain_release_number():
    """X.Y.Z only: the tag CI builds is this string with a 'v' in front."""
    assert re.fullmatch(
        r"\d+\.\d+\.\d+", __version__
    ), f"__version__ {__version__!r} is not a plain X.Y.Z release number"
