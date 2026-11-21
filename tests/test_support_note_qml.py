"""The launch note, end to end: it comes up, closes, and stays closed.

tests/test_support_note.py reads the source; this boots the real Main.qml
offscreen as a signed-in, fully set up install and watches the note: it
waits while an earlier prompt is due, comes up once the queue is clear,
CLOSE takes it away, and nothing brings it back in the same session. Then,
re-armed: it steps aside for a dialog above it and a prompt under it, and a
real Escape closes it even while another Esc shortcut is live. A harness
that needs a clear frame can switch it off.

Runs in a SUBPROCESS like the other Main.qml scenarios (shares the sibling's
``_boot``). The QML Settings blocks are pointed at the sandbox too, so the
flags this scenario sets never leave the temporary directory.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from test_progress_matrix_stable_width import _EXIT_NO_QT, _EXIT_OK, _EXIT_PRECONDITION, _boot


def _scenario() -> int:
    try:
        from PySide6.QtCore import QMetaObject, QObject, QSettings
    except Exception as exc:  # pragma: no cover - environment guard
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT
    sandbox = os.environ.get("XDG_CONFIG_HOME", "")
    if not sandbox:
        print("no sandbox for the QML settings", file=sys.stderr)
        return _EXIT_PRECONDITION
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, sandbox)

    booted = _boot()
    if not isinstance(booted, tuple):
        return booted
    root, q, settle, bridge = booted

    problems: list[str] = []

    def check(ok, what: str) -> None:
        if not ok:
            problems.append(what)

    # Offscreen runs start with the note off, so other scenarios keep a
    # clear frame; this one is about the note.
    check(q("root.supportNoteEnabled") is False, "the note is not off by default offscreen")
    q("root.supportNoteEnabled = true")

    # Signed out, as every scenario boots: no note over the sign-in screen.
    settle(700)
    check(q("supportNoteGate.visible") is False, "the note showed to a signed-out launch")

    # A signed-in install with every first-run step done, but the FLAC
    # notice still owed: the note waits its turn. The flag flips without the
    # session side of a real sign-in, which needs an account.
    q("setupSettings.ffmpegSetupDone = true")
    q("legalSettings.termsAccepted = true")
    q("legalSettings.termsAcceptedVersion = root.termsVersion")
    q("setupSettings.updatePromptAnswered = true")
    q("setupSettings.flacNoticeSeen = false")
    bridge._logged_in = True
    bridge.loggedInChanged.emit()
    settle(900)
    if not q("flacNoticeGate.onTop"):
        print("the FLAC notice did not come up to queue behind", file=sys.stderr)
        return _EXIT_PRECONDITION
    check(q("supportNoteGate.shown") is False, "the note latched while the FLAC notice was up")
    check(q("supportNoteGate.visible") is False, "the note stacked on the FLAC notice")

    # The FLAC notice is answered: the queue is clear, but the note gives
    # the app five seconds first, then takes the frame.
    q("flacNoticeGate.answer(false)")
    settle(1500)
    check(q("supportNoteGate.shown") is False, "the note did not wait before coming up")
    check(q("supportNoteGate.visible") is False, "the note painted before its wait was over")
    settle(3900)
    check(q("supportNoteGate.onTop") is True, "the note did not come up once its wait was over")
    settle(1900)
    check(q("supportNoteGate.reveal") == 1, "the entrance did not finish")
    check(q("supportNoteGate.visible") is True, "the note is not painted")
    coffee = root.findChild(QObject, "supportNoteCoffee")
    check(coffee is not None and coffee.isVisible(), "the note has no coffee button")
    # For a look at the note without launching the app: name a PNG path.
    shot = os.environ.get("WAVES_SUPPORT_NOTE_SHOT", "")
    if shot:
        root.grabWindow().save(shot)

    # CLOSE waits five seconds from the note's arrival: an early press,
    # Escape-style close and a backdrop click all do nothing.
    btn = root.findChild(QObject, "supportNoteClose")
    if btn is None:
        problems.append("no CLOSE button")
    check(q("supportNoteGate.canClose") is False, "the note could be closed before its wait ran out")
    q('supportNoteGate.close("note closed")')
    settle(100)
    check(q("supportNoteGate.onTop") is True, "an early close took the note away")
    settle(3400)
    check(q("supportNoteGate.canClose") is True, "CLOSE never unlocked")

    # CLOSE: gone, and not back in this session.
    if btn is not None:
        QMetaObject.invokeMethod(btn, "clicked")
    settle(900)
    check(q("supportNoteGate.visible") is False, "CLOSE left the note on screen")
    check(q("supportNoteGate.dismissed") is True, "CLOSE did not mark the session")
    q("root.settingsOpen = true")
    settle(200)
    q("root.settingsOpen = false")
    settle(700)
    check(q("supportNoteGate.visible") is False, "the note came back in the same session")

    # Back for the keyboard and stacking checks, with short waits so the
    # scenario stays quick (the five seconds are checked above).
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QGuiApplication, QKeyEvent

    def escape() -> None:
        root.requestActivate()
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            QGuiApplication.sendEvent(root, QKeyEvent(kind, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
        settle(150)

    q("supportNoteGate.delayMs = 200")
    q("supportNoteGate.closeLockMs = 300")
    q("supportNoteGate.dismissed = false")
    settle(1200)
    check(q("supportNoteGate.onTop") is True, "the re-armed note did not come back")
    check(q("supportNoteGate.canClose") is True, "the short close lock did not run out")

    # A dialog above the note in the overlay: the note steps aside, and Esc
    # closes the dialog, not the note.
    q("followDialog.open = true")
    settle(200)
    check(q("supportNoteGate.onTop") is False, "the note stayed on top under the follow dialog")
    escape()
    check(q("followDialog.open") is False, "Esc did not close the follow dialog over the note")
    check(q("supportNoteGate.dismissed") is False, "Esc closed the note behind the follow dialog")
    settle(600)
    check(q("supportNoteGate.onTop") is True, "the note did not come back after the follow dialog")

    # A prompt under the note in the overlay: the note steps aside too.
    q("root.folderGateBlocking = true")
    settle(200)
    check(q("supportNoteGate.onTop") is False, "the note covered the download folder prompt")
    q("root.folderGateBlocking = false")
    settle(600)
    check(q("supportNoteGate.onTop") is True, "the note did not come back after the folder prompt")

    # The close and exit prompts sit above the note in the overlay: the note
    # steps aside so they are seen, and comes back after.
    for gate in ("closeGate", "exitGate"):
        q(f"{gate}.open = true")
        settle(200)
        check(q("supportNoteGate.onTop") is False, f"the note covered {gate}")
        q(f"{gate}.open = false")
        settle(600)
        check(q("supportNoteGate.onTop") is True, f"the note did not come back after {gate}")

    # The artist card asked for by a name behind the note: blocked, and
    # its stacking stays under the prompts outside the import review.
    q(
        "(function () { var t = Qt.createQmlObject('import QtQuick; Text { x: 60; y: 300;"
        ' text: "Behind"; textFormat: Text.PlainText }\', root.contentItem);'
        ' root.artistCardFor(t, "11", "Base Notion", ""); return true })()'
    )
    settle(700)
    check(q("root.ahcBlocked") is True, "the artist card is not blocked while the note is up")
    check(q("ahcCard.visible") is False, "the artist card opened over the note")
    check(q("ahcCard.z") < q("supportNoteGate.z"), "the artist card stacks above the prompts")

    # Another Esc shortcut live beneath the note (a video peek): Esc still
    # closes the note. Two live shortcuts on one key fire neither in Qt.
    q('root.peekNow = ({ "id": "support-note-probe" })')
    settle(150)
    escape()
    check(q("supportNoteGate.dismissed") is True, "Esc did not close the note while a peek was live")
    check(q("root.peekNow !== null") is True, "Esc closed the peek under the note")
    q("root.peekClose()")
    settle(700)
    check(q("supportNoteGate.visible") is False, "Esc left the note on screen")

    # The switch itself: with it off, a fresh session's note never latches.
    q("root.supportNoteEnabled = false")
    q("supportNoteGate.dismissed = false")
    settle(700)
    check(q("supportNoteGate.visible") is False, "supportNoteEnabled = false did not hold the note back")

    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else _EXIT_OK


def test_the_note_queues_shows_closes_and_stays_closed():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    # Sandboxed: this scenario builds a REAL WavesBridge, and its QML
    # settings are redirected into the same temporary directory.
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-support-note-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    lines = [ln for ln in (proc.stdout + proc.stderr).strip().splitlines() if "waves.qt" not in ln]
    tail = "\n".join(lines[-16:])
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"the launch note no longer behaves:\n{tail}"


if __name__ == "__main__":
    sys.exit(_scenario())
