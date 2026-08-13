"""An offscreen scenario must never run against the real config directory.

WHAT THIS FENCES OFF
--------------------
A scenario that builds a real ``WavesBridge`` inherits whatever config
directory the environment points at, and on a developer's machine that is
the packaged app's own: ``~/Library/Application Support/Waves`` (or the
platform equivalent). The bridge then adopts the user's settings, writes
its startup lines and tracebacks into the user's ``waves_dev.log``, and,
if a library root is configured, starts a REAL scan of the user's music
library from its constructor.

Three scenarios were missing the sandbox, and it showed: a verbose log
kept for a livetest was interleaved with test sessions, each one starting
a library scan that bailed seconds later, and a test's own "live TIDAL API
disabled in this test" traceback was written into it as an app ERROR.

HOW THIS STAYS FIXED
--------------------
Every test module that constructs a bridge behind a QML engine must hand
its subprocess an ``XDG_CONFIG_HOME`` of its own. ``path_config_base()``
honours that variable first on every platform, so a scenario given one
can only ever touch a temporary directory.
"""

from __future__ import annotations

from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent


def test_every_offscreen_bridge_scenario_sandboxes_its_config_dir():
    unsandboxed = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        src = path.read_text()
        builds_bridge = "WavesBridge(" in src and "QQmlApplicationEngine" in src
        if builds_bridge and "XDG_CONFIG_HOME" not in src:
            unsandboxed.append(path.name)

    assert not unsandboxed, (
        "these scenarios build a real WavesBridge without an XDG_CONFIG_HOME of "
        "their own, so they run against the packaged app's config dir: they adopt "
        "the user's settings, write into the user's log, and start a real scan of "
        "the user's library. Give each one "
        'env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-<name>-test-"): ' + ", ".join(unsandboxed)
    )
