"""The queue's section headers pulse the number that GREW.

WHAT THIS FENCES OFF
--------------------
A finished row's promotion moves it out of Downloading and into Completed in
one tick: one count falls, the other rises. The pulse was routed through a
root-wide ``compBump`` signal that every section header listened to, each
deciding whether the bump was its own with a ``section === "completed"`` test.
The view POOLS those headers, so the instance answering the signal was not
reliably the completed one, and what the drawer actually animated was
DOWNLOADING counting DOWN: a bounce celebrating a number shrinking, on the one
header where shrinking is not the news, while the count that had just gone up
sat still.

Each header now watches its own count and pulses only on a rise. Proved on the
real Main.qml in a subprocess, by driving a real promotion through the real
queue model and reading which header's animation ran.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78


def _scenario(flag: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-queue-section-pulse-")
    env["XDG_CONFIG_HOME"] = sandbox
    env["HOME"] = sandbox
    return subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), flag],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _check(proc: subprocess.CompletedProcess, complaint: str) -> None:
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-12:])
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"{complaint}:\n{tail}"


def test_the_pulse_follows_the_count_that_rose() -> None:
    _check(_scenario("--run-scenario"), "the wrong section header is pulsing")


def test_the_first_row_into_an_empty_section_pulses_too() -> None:
    """An audit read the arming as unable to serve the header a promotion
    CREATES, on the grounds that the tick fires while no such header is
    listening yet. It reaches it anyway, and the two properties this design
    already has are why: the tick arms every header that exists at that
    moment, the view hands one of those POOLED headers to the section that
    just appeared, and the timer fires afterwards and asks what section it is
    holding by then. Pinned here because it is not obvious from either half,
    and because an arming added "to be safe" on header creation is the
    tempting wrong answer (it also needs a staleness window, and that window
    is what makes a recycled header replay an old pulse)."""
    _check(_scenario("--run-first-row"), "a section gaining its first row did not pulse")


# The queue's section headers, and the pulse animation inside one of them.
# Returned one at a time as plain scalars / objects: a JS map comes back from
# QQmlExpression as an opaque QJSValue.
_SECTIONS = """
(function(){
    var out = []
    var walk = function(o){
        if (!o) return
        if (o.objectName === "queueSectionHeader") out.push("" + o.section)
        var c = o.children || []
        for (var i = 0; i < c.length; i++) walk(c[i])
    }
    walk(queueList)
    return out.join(",")
})()
"""


def _pulse_of(section: str) -> str:
    return """
(function(){
    var found = null
    var walk = function(o){
        if (!o || found) return
        if (o.objectName === "queueSectionHeader" && ("" + o.section) === "%s") {
            // `data`, not `children`: an animation is not an Item, so it
            // sits in the header's resources rather than its child items.
            var k = o.data || []
            for (var j = 0; j < k.length; j++) if (k[j].objectName === "queueSectionPulse") found = k[j]
        }
        var c = o.children || []
        for (var i = 0; i < c.length && !found; i++) walk(c[i])
    }
    walk(queueList)
    return found
})()
""" % section


def _run_scenario(first_row: bool = False) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
    except Exception as exc:  # pragma: no cover - environment guard
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from _qml_offline import PARK_LOGIN_QML, patch_offline

    patch_offline()
    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:  # pragma: no cover - environment guard
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    engine = QQmlApplicationEngine()
    bridge = WavesBridge(tidal=None)
    engine.rootContext().setContextProperty("waves", bridge)
    engine.rootContext().setContextProperty("monoFont", _load_mono())
    engine.rootContext().setContextProperty("uiFontFamily", app.font().family())
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
    roots = engine.rootObjects()
    if not roots:
        print("Main.qml failed to load", file=sys.stderr)
        return _EXIT_PRECONDITION
    root = roots[0]

    def q(expr: str):
        e = QQmlExpression(QQmlEngine.contextForObject(root), root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(e.error().toString())
        return r[0] if isinstance(r, tuple) else r

    def settle(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    settle(120)
    q(PARK_LOGIN_QML)
    q("queueDrawer.open()")
    settle(120)
    if not bool(q("queueDrawer.visible")):
        print("the queue drawer would not open", file=sys.stderr)
        return _EXIT_PRECONDITION

    # Two downloads in flight, and one already promoted, so BOTH headers the
    # promotion touches exist before the one under test moves.
    for i in range(3):
        bridge._enqueue(f"Album {i}", "album", media_id=f"al-{i}", collection=True, tracks=9)
    bridge.queueChanged.emit(list(bridge._queue))
    settle(160)
    qids = [int(it["qid"]) for it in bridge._queue]
    # Downloading, which is the section the promotion takes the row OUT of and
    # the one that was bouncing as its count fell.
    for it in bridge._queue:
        it["status"] = "running"
    bridge.queueChanged.emit(list(bridge._queue))
    settle(160)
    downloading = int(
        q(
            "(function(){ var n = 0; for (var i = 0; i < queueModel.count; i++)"
            " if (queueModel.get(i).uiGroup === 'downloading') n++; return n })()"
        )
    )
    if downloading != len(qids):
        print(f"the rows did not land in Downloading: {downloading} of {len(qids)}", file=sys.stderr)
        return _EXIT_PRECONDITION
    if not first_row:
        # One row promoted first, so the Completed header already exists and
        # the measured promotion below is a plain count change on two live
        # headers. The first_row run deliberately skips this: there COMPLETED
        # does not exist yet, and the promotion under test is what creates it.
        q(f"root.promoteCompleted({qids[0]})")
        settle(400)

    # The tick under test: one row leaves Downloading, one joins Completed.
    first = qids[0] if first_row else qids[1]
    q(f"root.promoteCompleted({first})")
    # Past the headers' arming frame and inside the 330ms pulse. Polled, not
    # sampled once: a single fixed settle can land before the arming frame on
    # a loaded machine and past the whole pulse on a very slow one, and both
    # read as "COMPLETED did not pulse".
    running_by_section: dict[str, bool] = {}
    sections: list[str] = []
    for _ in range(20):
        settle(16)
        sections = [x for x in str(q(_SECTIONS)).split(",") if x]
        if "completed" not in sections:
            continue
        for section in sections:
            pulse = q(_pulse_of(section))
            if pulse is None:
                # NOT a precondition: a header with no pulse animation is the
                # regression this test exists to catch. Routing it to a skip
                # meant deleting the animation turned the guard green.
                print(f"the {section!r} header has no pulse animation", file=sys.stderr)
                return _EXIT_REGRESSED
            if bool(pulse.property("running")):
                running_by_section[section] = True
        if running_by_section:
            break
    if "completed" not in sections or "downloading" not in sections:
        print(f"the two headers the promotion touches were not both built: {sections}", file=sys.stderr)
        return _EXIT_PRECONDITION
    bad: list[str] = []
    for section in sections:
        running = running_by_section.get(section, False)
        # Completed just gained the promoted row; every other section either
        # lost one or did not move, and a loss must never be celebrated.
        if section == "completed" and not running:
            bad.append("COMPLETED gained a row and did not pulse")
        if section != "completed" and running:
            bad.append(f"{section.upper()} pulsed without its count rising")

    if bad:
        for b in bad:
            print(b, file=sys.stderr)
        return _EXIT_REGRESSED
    return _EXIT_OK


if __name__ == "__main__" and ("--run-scenario" in sys.argv or "--run-first-row" in sys.argv):
    sys.exit(_run_scenario(first_row="--run-first-row" in sys.argv))
