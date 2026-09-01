"""Importing ``waves`` must not pay for packaging metadata it may never use.

``waves/__init__.py`` used to compute ``__name_display__``, ``__version__``
and ``__config_dirname__`` at import time: two ``importlib.metadata.version``
probes plus a ``pyproject.toml`` parse before the first window could appear,
on every launch, even though most launches read none of them (the UI carries
its own version literal in ``waves.waves_ui``). They are now computed on
first attribute access (PEP 562) with the ``is_dev_env()`` verdict cached, so
a full touch of all three costs at most ONE metadata probe, and a launch that
touches none costs zero.

The probe counting runs in a subprocess: the test process itself imported
``waves`` long ago, so only a fresh interpreter can observe import time.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_PROBE = """
import importlib.metadata

calls = []
_orig_version = importlib.metadata.version

def _counted(name, *a, **k):
    calls.append(name)
    return _orig_version(name, *a, **k)

importlib.metadata.version = _counted

import waves

print("PROBES_AT_IMPORT", len(calls))

dirname = waves.__config_dirname__
version = waves.__version__
display = waves.__name_display__

print("PROBES_AFTER_ALL_THREE", len(calls))
print("DIRNAME_OK", dirname in ("Waves", "Waves-dev"))
print("VERSION_NONEMPTY", bool(version))
print("DISPLAY_NONEMPTY", bool(display))

# Second reads come from the cached globals, not the hook: still no new probe.
_ = waves.__config_dirname__, waves.__version__, waves.__name_display__
print("PROBES_AFTER_REREAD", len(calls))
"""


def _run_probe() -> dict[str, str]:
    # Fixed argv: this interpreter runs the literal probe above, no user input.
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    out: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition(" ")
        out[key] = value
    return out


def test_import_is_free_and_all_three_cost_at_most_one_probe():
    report = _run_probe()
    assert report["PROBES_AT_IMPORT"] == "0", "importing waves ran a metadata probe"
    assert int(report["PROBES_AFTER_ALL_THREE"]) <= 1, "is_dev_env() verdict is not cached"
    assert report["PROBES_AFTER_REREAD"] == report["PROBES_AFTER_ALL_THREE"]
    assert report["DIRNAME_OK"] == "True"
    assert report["VERSION_NONEMPTY"] == "True"
    assert report["DISPLAY_NONEMPTY"] == "True"


def test_unknown_attribute_still_raises():
    import waves

    with pytest.raises(AttributeError):
        _ = waves.no_such_attribute_anywhere
