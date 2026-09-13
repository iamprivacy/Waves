"""Launch water probe: does the picture drop frames while the app loads?

Runs the REAL app from source (your real config, so also your real library
and session) with Qt's own render-loop timing log switched on, and reads
the gaps between rendered frames off that log. The stamps are written by
Qt's render thread in C++ with the process time, and the app's Python
message handler is replaced by a nothing, so no Python sits anywhere on
the measured path: a gap here is a gap the eye saw. (A Python frameSwapped
slot cannot tell a wait for the interpreter lock from time spent in Qt,
which is why this tool exists.)

Manual only, never CI. Usage:

    poetry run python tools/launch_probe.py [seconds] [gap_ms]

Prints every gap over gap_ms (default 45, four missed frames at 120 Hz,
three at 60) and a verdict line: the first frame is exempt (the window's
first paint), so are frames whose own render took the time (a texture
upload is not the interpreter), and the budget for the rest is zero.
Exit status 0 on a clean launch, 1 otherwise. Quits the app on its own.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_STAMP = re.compile(
    r"\s*([\d.]+)\s+\[window[^\]]*\]\[render thread[^\]]*\] syncAndRender: frame rendered in (\d+)ms, "
    r"sync=(\d+), render=(\d+), swap=(\d+)"
)
_CHILD = """
import ctypes, os, sys, threading
sys.path.insert(0, %(root)r)
from waves.waves_ui import diagnostics
diagnostics._install_qt_handler = lambda: None
def _quit():
    # Qt's default handler writes stderr through C stdio, fully buffered
    # when stderr is a file: flush it, or the tail of the timeline is lost.
    try:
        ctypes.CDLL(None).fflush(None)
    except Exception:
        pass
    sys.stderr.flush()
    os._exit(0)
threading.Timer(%(seconds)f, _quit).start()
from waves.waves_ui.app import waves_activate
waves_activate()
"""


def run_app(seconds: float, log: Path) -> None:
    env = dict(os.environ)
    env["QT_LOGGING_RULES"] = "qt.scenegraph.time.renderloop=true"
    env["QT_MESSAGE_PATTERN"] = "%{time process} %{message}"
    with open(log, "wb") as sink:
        subprocess.run(
            [sys.executable, "-c", _CHILD % {"root": str(ROOT), "seconds": seconds}],
            stdout=sink,
            stderr=subprocess.STDOUT,
            cwd=str(ROOT),
            env=env,
            check=False,
        )


def gaps(log: Path, gap_ms: float) -> tuple[int, list[tuple[float, float, int, int, bool]]]:
    """(frames rendered, [(at_s, gap_ms, sync_ms, render_ms, after_first_frame)
    for each gap]). Only the gap that follows the very first frame is the
    first frame's own cost; a slow gap later in the run is not."""
    n = 0
    prev = None
    found = []
    for line in log.read_text(errors="replace").splitlines():
        m = _STAMP.match(line)
        if not m:
            continue
        t = float(m.group(1)) * 1000.0
        n += 1
        if prev is not None and t - prev > gap_ms:
            found.append((prev / 1000.0, t - prev, int(m.group(3)), int(m.group(4)), n == 2))
        prev = t
    return n, found


def main(argv: list[str]) -> int:
    seconds = float(argv[0]) if argv else 12.0
    gap_ms = float(argv[1]) if len(argv) > 1 else 45.0
    log = Path(tempfile.mkdtemp(prefix="waves-launch-probe-")) / "render.log"
    run_app(seconds, log)
    n, found = gaps(log, gap_ms)
    print(f"frames rendered: {n} in {seconds:.0f}s (log: {log})")
    if n == 0:
        print("VERDICT: no frames seen (did the app start? is qt.scenegraph logging reachable?)")
        return 1
    blamed = []
    for at, gap, sync, render, first in found:
        exempt = first or render >= gap * 0.6
        tag = "first frame" if first else ("render thread" if exempt else "GUI thread starved")
        print(f"  +{at:7.3f}s  gap {gap:6.0f}ms  sync={sync:3d} render={render:3d}  {tag}")
        if not exempt:
            blamed.append(gap)
    if blamed:
        print(
            f"VERDICT: {len(blamed)} gap(s) over {gap_ms:.0f}ms the interpreter is answerable for, worst {max(blamed):.0f}ms"
        )
        return 1
    print(f"VERDICT: clean, no gap over {gap_ms:.0f}ms after the first frame")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
