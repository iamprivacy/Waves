"""The v0.2.0 launch fireworks play on every launch of that version.

So the launch stays near its usual length: the opening frame holds only
0.1 s past the plain 1.9 s, the version drains under the last shell's
sparks instead of waiting them out, and the zoom fades whatever is left.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = (ROOT / "waves" / "waves_ui" / "qml" / "Main.qml").read_text(encoding="utf-8")


def test_every_launch_of_the_version_plays_the_show():
    block = MAIN[MAIN.index("id: bootFx") : MAIN.index("id: bootFx") + 400]
    assert re.search(r'celebrate: waves\.appVersion === "0\.2\.0"\n', block)
    assert "launch_fx_seen" not in MAIN


def test_the_hold_stays_near_the_plain_one():
    m = re.search(r"PauseAnimation \{ duration: bootFx\.celebrate \? (\d+) : (\d+) \}", MAIN)
    assert m and int(m.group(2)) == 1900
    assert int(m.group(1)) - int(m.group(2)) <= 100


def test_the_sparks_fade_with_the_zoom_not_before_the_drain():
    assert "onStarted: bootFx.release()" in MAIN[MAIN.index("id: bootZoom") : MAIN.index("id: bootZoom") + 80]
    handover = MAIN[MAIN.index("id: bootHandover") : MAIN.index("id: bootZoom")]
    assert "release()" not in handover
