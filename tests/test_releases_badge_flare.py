"""The Releases tab's badge flares in, counts, and counts back down before it goes.

WHAT THIS FENCES OFF
--------------------
The badge used to appear and vanish in a single frame. Now it flares in
white hot and cools to green, steps through every number on the way up, and
when the count clears it counts back down and then shrinks away. All of it
keys off the count, so a badge that went back to switching on its own
``visible`` (or straight to the new number) would jump again.

HOW THIS STAYS FIXED
--------------------
Boot the real Main.qml offscreen and drive ``releasesUnseen``:

* 0 to 3: the badge shows at once, white hot, steps through 1, 2 and 3, and
  settles green at full size reading 3;
* 3 to 0: it is still on screen counting down, passes back through 2 and 1,
  and only then shrinks away.

Runs in a SUBPROCESS like the other Main.qml scenarios (shares the sibling's
``_boot``).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from test_progress_matrix_stable_width import _EXIT_OK, _EXIT_REGRESSED, _boot

_FIND = """
 function walk(it, name){
  if (!it) return null;
  if (it.objectName === name) return it;
  for (var i = 0; i < it.children.length; i++) {
   var hit = walk(it.children[i].item || it.children[i], name);
   if (hit) return hit;
  }
  return null;
 }
 function bd(){ return walk(walk(root.contentItem, 'releasesTab'), 'navTabBadge') }
"""


def test_the_releases_badge_flares_in_and_counts_both_ways():
    import pytest

    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-releases-badge-test-")
    env["HOME"] = env["XDG_CONFIG_HOME"]
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-12:])
    if proc.returncode == 77:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == 78:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"the releases badge regressed:\n{tail}"


def _scenario() -> int:
    booted = _boot()
    if not isinstance(booted, tuple):
        return booted
    _root, q, settle, _bridge = booted

    def js(body: str):
        return q("(function(){" + _FIND + body + "})()")

    if not js("return bd() !== null"):
        print("no navTabBadge on the Releases tab", file=sys.stderr)
        return 78

    def sample() -> tuple[bool, int, float, float, str]:
        got = js(
            "var b = bd(); return [b.visible ? 1 : 0, b.disp, b.heat, b.sc, walk(b, 'navTabBadgeText').text].join('|')"
        )
        vis, disp, heat, sc, text = str(got).split("|")
        return vis == "1", int(disp), float(heat), float(sc), text

    failures = []
    js("root.releasesUnseen = 0; return 1")
    settle(400)
    if sample()[0]:
        failures.append("with nothing unseen the badge is on screen")

    js("root.releasesUnseen = 3; return 1")
    settle(20)
    vis, _d, heat, _sc, _t = sample()
    if not vis:
        failures.append("an arrival did not show the badge at once")
    if heat < 0.5:
        failures.append(f"an arrival came in at heat {heat:.2f}, wanted it white hot")
    seen = set()
    for _ in range(40):
        settle(20)
        seen.add(sample()[1])
    if not {1, 2, 3} <= seen:
        failures.append(f"counting up showed {sorted(seen)}, wanted every number through 3")
    vis, disp, heat, sc, text = sample()
    if not vis or disp != 3 or text != "3" or heat > 0.02 or abs(sc - 1) > 0.01:
        failures.append(f"settled at visible={vis} disp={disp} text={text!r} heat={heat:.2f} scale={sc:.2f}")

    js("root.releasesUnseen = 0; return 1")
    settle(20)
    if not sample()[0]:
        failures.append("clearing the count dropped the badge at once, wanted it to count down first")
    down, gone_at = [], None
    for i in range(50):
        settle(20)
        vis, disp, *_ = sample()
        down.append(disp)
        if not vis and gone_at is None:
            gone_at = i
    if not {2, 1} <= set(down):
        failures.append(f"counting down showed {sorted(set(down), reverse=True)}, wanted it back through 2 and 1")
    if gone_at is None:
        failures.append("the badge never left after the count cleared")
    elif gone_at < 4:
        failures.append(f"the badge left after {gone_at * 20}ms, before it could count down")

    for f in failures:
        print(f, file=sys.stderr)
    return _EXIT_REGRESSED if failures else _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_scenario())
