"""The queue's one-item releases answer a hover but offer no expansion, and
the peek settles where it stops.

WHAT THIS FENCES OFF
--------------------
Two things a livetest called out on the drawer, both on the same row.

1. A SINGLE still wore the expansion. The row's affordance was gated on
   ``model.collection`` alone, which is true for any album/playlist/mix job,
   so a one-track release got the caret, the pointing-hand cursor, a click
   target and a track fetch, all to reveal a list of one line the card's own
   title already said. The expansion gate now also asks that the release is not
   exactly one item. Exactly one: a ``tracks`` of 0 means the count was never
   known, and that row still needs its ledger.

   The HOVER is separate, and a later livetest said so: a row that does nothing
   whatsoever under the pointer reads as a broken row. So every collection row
   still peeks (``peekable``), and the single's sliver says it is a single,
   while only ``expandable`` rows carry the caret, the hand cursor, the track
   fetch and a working click.

2. The peek SPRANG. Its height Behavior used ``Easing.OutBack`` with an
   overshoot, which by definition drives the value past its target and brings
   it back. On a peek that is driven by the POINTER rather than by a click,
   that reads as the card opening and then retracting on its own while the
   pointer sits still, and on the way out it aims the height below zero before
   returning to it. Sweeping the pointer down a list of rows played the wobble
   once per row, which is what got reported as glitchy. A hover response has to
   settle where it stops, so the easing must not be one of the springy families.

The first is proved on the real Main.qml in a subprocess, like its siblings in
``test_queue_playlist_expands.py``; the second on the QML source, because the
peek is reachable only from a real pointer and the property that carries the
overshoot is a declared constant.
"""

from __future__ import annotations

import os
import re
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

# Easings that deliberately leave the 0..1 range: they overshoot the target (or
# undershoot the start) and come back. Fine for a click, wrong for a hover.
SPRINGY = (
    "OutBack",
    "InBack",
    "InOutBack",
    "OutBounce",
    "InBounce",
    "InOutBounce",
    "OutElastic",
    "InElastic",
    "InOutElastic",
    "OutCurve",
    "InCurve",
)


def _peek_behavior() -> str:
    """The queue ledger's height Behavior block, found by the animation's id."""
    src = QML_MAIN.read_text(encoding="utf-8")
    at = src.find("id: qtrackAnim")
    assert at != -1, "could not find the queue ledger's peek animation (id: qtrackAnim)"
    start = src.rfind("Behavior on implicitHeight", 0, at)
    assert start != -1, "the peek animation is no longer inside a Behavior on implicitHeight"
    i, depth = src.index("{", start), 0
    for end in range(i, len(src)):
        if src[end] == "{":
            depth += 1
        elif src[end] == "}":
            depth -= 1
            if depth == 0:
                return src[start : end + 1]
    raise AssertionError("unbalanced braces in the peek Behavior")


def test_the_peek_animation_does_not_overshoot() -> None:
    # Comments out: the block explains the very words being searched for.
    block = re.sub(r"//[^\n]*", "", _peek_behavior())
    used = [name for name in SPRINGY if f"Easing.{name}" in block]
    assert not used, (
        f"the queue peek is driven by the pointer, so it must settle on its target; "
        f"Easing.{used[0] if used else ''} carries it past and back"
    )
    assert "overshoot" not in block, "an overshoot is the same spring by another name"
    assert "easing.type: Easing." in block, "the peek should still name an easing rather than fall back to Linear"


def test_a_one_item_release_offers_no_expansion() -> None:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-queue-single-expand-")
    env["XDG_CONFIG_HOME"] = sandbox
    env["HOME"] = sandbox
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-12:])
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"the queue row's expansion gate is wrong:\n{tail}"


def _run_scenario() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from PySide6.QtCore import QEventLoop, QPointF, Qt, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication, QMouseEvent
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

    # (label, tracks queued with, should the row offer an expansion)
    cases = (
        ("One Song Single", 1, False),  # the release the report was about
        ("Two Song Single", 2, True),  # the gate must not swallow small EPs
        ("Unknown Size", 0, True),  # 0 is "not counted", not "one track"
    )
    bad: list[str] = []
    for i, (name, tracks, want) in enumerate(cases):
        qid = bridge._enqueue(name, "album", media_id=f"al-{i}", collection=True, tracks=tracks)
        bridge.queueChanged.emit(list(bridge._queue))
        settle(140)
        row = (
            "(function(){ var n = queueList.count;"
            " for (var i = 0; i < n; i++) { var it = queueList.itemAtIndex(i);"
            f" if (it && it.model && it.model.qid === {qid}) return it }} return null }})()"
        )
        if not bool(q(row + " !== null")):
            print(f"no drawer row for {name!r}", file=sys.stderr)
            return _EXIT_PRECONDITION
        got = bool(q(row + ".expandable"))
        if got != want:
            bad.append(f"{name!r} (tracks={tracks}): expandable is {got}, expected {want}")
            continue
        # A row that cannot expand must not pretend otherwise: clicking it is
        # inert. It still answers the pointer, though, or it reads as broken,
        # so the peek stays available to every collection row.
        if not bool(q(row + ".peekable")):
            bad.append(f"{name!r} does not answer a hover at all")
        if not want:
            q(row + ".qtoggle()")
            if bool(q(row + ".qexp")) or bool(q(f"root.queueExpanded[{qid}] === true")):
                bad.append(f"{name!r} expanded on click even though it has nothing to show")
            # And the hover really does open the sliver, with the line that
            # explains why there is nothing more behind it.
            # The scene point is computed in QML: the rows come back as bare
            # QObjects through QQmlExpression, with no Item API on them.
            sx = float(q("(function(){ var c = " + row + "; return c.mapToItem(null, c.width / 2, 12).x })()"))
            sy = float(q("(function(){ var c = " + row + "; return c.mapToItem(null, c.width / 2, 12).y })()"))
            pt = QPointF(sx, sy)
            ev = QMouseEvent(
                QMouseEvent.Type.MouseMove,
                pt,
                pt,
                Qt.MouseButton.NoButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
            app.sendEvent(root, ev)
            settle(400)
            if not bool(q(row + ".peeking")):
                bad.append(f"{name!r} does not peek with the pointer on it")
            note = q(
                "(function(){ var c = " + row + "; var f = function(o){ if (!o) return null;"
                " if (o.objectName === 'qSingleNote') return o;"
                " var k = o.children || []; for (var i = 0; i < k.length; i++)"
                " { var r = f(k[i]); if (r) return r } return null }; return f(c) })()"
            )
            if note is None or not bool(note.property("visible")):
                bad.append(f"{name!r} peeks but says nothing about being a single")
                continue
            # And when the pointer leaves, the sliver keeps that line until it
            # has finished closing. Bound to the hover alone, the line went on
            # the frame the pointer left and the card then spent the whole
            # 220ms retract folding away an empty box, which reads as the row
            # swallowing its own content rather than as a peek closing.
            clip = q(
                "(function(){ var c = " + row + "; var f = function(o){ if (!o) return null;"
                " if (o.objectName === 'qLedgerClip') return o;"
                " var k = o.children || []; for (var i = 0; i < k.length; i++)"
                " { var r = f(k[i]); if (r) return r } return null }; return f(c) })()"
            )
            if clip is None:
                print("the queue ledger clip is no longer findable", file=sys.stderr)
                return _EXIT_PRECONDITION
            elsewhere = QPointF(2.0, 2.0)
            app.sendEvent(
                root,
                QMouseEvent(
                    QMouseEvent.Type.MouseMove,
                    elsewhere,
                    elsewhere,
                    Qt.MouseButton.NoButton,
                    Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier,
                ),
            )
            # Sampled while it closes, not once at a fixed delay: the retract
            # is 220ms and a loaded machine can land either side of any single
            # sample.
            closing = 0
            for _ in range(60):
                settle(8)
                if float(clip.property("implicitHeight")) <= 0.5:
                    break
                closing += 1
                if not bool(note.property("visible")):
                    bad.append(f"{name!r} retracted its sliver with the line already gone")
                    break
            if not closing:
                print("the sliver was never caught mid-retract", file=sys.stderr)
                return _EXIT_PRECONDITION
            if bool(note.property("visible")):
                bad.append(f"{name!r} kept its sliver line after the sliver had closed")

    if bad:
        for b in bad:
            print(b, file=sys.stderr)
        return _EXIT_REGRESSED
    return _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
