"""Closing the window, end to end: the prompt, the background and the way back.

tests/test_close_gate.py reads the source; this boots the real Main.qml
offscreen and presses the real close: the prompt opens instead of the app
ending, RUN IN BACKGROUND hides the window while the bridge keeps running,
the window comes back when asked (the Dock, the tray, a second launch), a
real quit skips the offer, and the download warning still stands in front of
every way out, including a quit that arrives while the window is hidden,
and the prompt carries that warning itself so the two never follow each
other.

Every close but the last is one the flow vetoes; the last (EXIT ANYWAY on
the prompt) really closes the scenario's window, so it runs last.

Runs in a SUBPROCESS like the other Main.qml scenarios (shares the sibling's
``_boot``).
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
    booted = _boot()
    if not isinstance(booted, tuple):
        return booted
    root, q, settle, bridge = booted
    from PySide6.QtCore import QMetaObject, QObject

    problems: list[str] = []

    def check(ok, what: str) -> None:
        if not ok:
            problems.append(what)

    def press(name: str) -> None:
        btn = root.findChild(QObject, name)
        if btn is None:
            problems.append(f"no button named {name}")
            return
        QMetaObject.invokeMethod(btn, "clicked")
        settle(80)

    def close() -> None:
        root.close()
        settle(80)

    if not q("waves.backgroundAvailable"):
        # No Dock and no tray here: the close button exits, as it always did.
        print("no way back from the background on this desktop", file=sys.stderr)
        return _EXIT_PRECONDITION

    # 1. The close button asks; nothing is hidden or ended yet.
    check(q("root.closeAction") == "ask", "a fresh profile must ask")
    close()
    check(q("closeGate.open") is True, "the close button did not open the prompt")
    check(root.isVisible(), "the window went away before the prompt was answered")

    # 2. RUN IN BACKGROUND: the window hides, Waves keeps running.
    press("closeGateBackground")
    check(q("closeGate.open") is False, "the prompt stayed up after its answer")
    check(not root.isVisible(), "RUN IN BACKGROUND left the window on screen")
    check(q("root.inBackground") is True and bridge._in_background is True, "the background state was not entered")
    check(bridge._waves_prefs["close_action"] == "ask", "an unticked box must keep nothing")

    # 3. The way back (the Dock icon, the tray icon, a second launch).
    bridge.requestShowWindow()
    settle(80)
    check(root.isVisible(), "the window did not come back")
    check(q("root.inBackground") is False and bridge._in_background is False, "the background state was not left")

    # 3b. Audit 2026-11-14b: a close from the taskbar of a minimized window
    #     opened the prompt inside the minimized window, where nobody saw it,
    #     and a window minimized from maximized came back unmaximized.
    from PySide6.QtGui import QWindow

    def shown() -> QWindow.Visibility:
        return root.visibility()

    root.showMaximized()
    settle(80)
    if shown() != QWindow.Visibility.Maximized:
        print("this platform plugin cannot maximize", file=sys.stderr)
        return _EXIT_PRECONDITION
    root.showMinimized()
    settle(80)
    check(shown() == QWindow.Visibility.Minimized, "the scenario could not minimize the window")
    close()
    check(q("closeGate.open") is True, "a minimized window's close did not ask")
    check(shown() == QWindow.Visibility.Maximized, "the prompt opened in a window still minimized (or unmaximized)")
    press("closeGateBackground")
    check(not root.isVisible(), "RUN IN BACKGROUND left the window on screen")
    bridge.requestShowWindow()
    settle(80)
    check(shown() == QWindow.Visibility.Maximized, "the background lost the maximized state")
    root.showMinimized()
    settle(80)
    bridge.setWavesPref("close_action", "background")
    settle(50)
    close()
    check(not root.isVisible() and q("root.inBackground") is True, "a minimized window did not go to the background")
    bridge.requestShowWindow()
    settle(80)
    check(shown() == QWindow.Visibility.Maximized, "minimized, then the background, lost the maximized state")
    root.showMinimized()
    settle(80)
    bridge.requestShowWindow()  # a second launch while minimized
    settle(80)
    check(shown() == QWindow.Visibility.Maximized, "a minimized window came back unmaximized")
    bridge.setWavesPref("close_action", "ask")
    root.showNormal()
    settle(80)

    # 4. A real quit skips the offer; with downloads running it is warned.
    bridge._enqueue("Some Album", "album", media_id="al-1", artist="", tracks=10, collection=True)
    settle(120)
    check(int(q("root.activeQueueCount")) > 0, "the scenario's queue row is not counted as active")
    bridge.note_quit_requested()
    close()
    check(q("closeGate.open") is False, "a real quit was offered the background")
    check(q("exitGate.open") is True, "a real quit with downloads running was not warned")
    check(root.isVisible(), "the warned quit closed the window anyway")
    check(
        root.findChild(QObject, "exitGateExit").property("label") == "EXIT ANYWAY",
        "the warning's exit tile does not warn",
    )
    press("exitGateKeep")
    check(q("exitGate.open") is False, "KEEP DOWNLOADING did not close the warning")
    check(root.isVisible(), "KEEP DOWNLOADING closed the window")

    # 5. The setting "Run in background": no prompt, straight to the background.
    bridge.setWavesPref("close_action", "background")
    settle(50)
    check(q("root.closeAction") == "background", "the UI did not re-read the setting")
    close()
    check(q("closeGate.open") is False, "the background setting still asked")
    check(not root.isVisible() and q("root.inBackground") is True, "the background setting did not hide the window")

    # 6. A quit that arrives while hidden: the warning needs the window back.
    bridge.note_quit_requested()
    close()
    check(root.isVisible(), "the download warning opened with no window to read it in")
    check(q("exitGate.open") is True, "a quit from the background was not warned")
    check(q("root.inBackground") is False, "the window is up but still counted as hidden")
    q("exitGate.open = false")

    # 7. The setting "Exit": the close button leaves (here: is warned), no offer.
    bridge.setWavesPref("close_action", "exit")
    settle(50)
    close()
    check(q("closeGate.open") is False and q("exitGate.open") is True, "the exit setting still offered the background")
    q("exitGate.open = false")

    # 8. Back on ask with downloads running: the prompt itself carries the
    #    warning, so it is the ONLY prompt. Last, because this close leaves.
    bridge.setWavesPref("close_action", "ask")
    settle(50)
    close()
    check(q("closeGate.open") is True, "back on ask, the prompt did not open")
    check(q("closeGate.warnDl") is True, "the prompt did not say downloads are running")
    q("closeSkip.checked = true")
    press("closeGateExit")
    check(bridge._waves_prefs["close_action"] == "exit", "Don't ask again did not keep EXIT")
    check(q("exitGate.open") is False, "EXIT ANYWAY on the prompt was followed by a second warning")
    check(not root.isVisible(), "EXIT ANYWAY did not close the window")

    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print("CLOSE: OK")
    return _EXIT_OK


def test_closing_the_window_offers_the_background_and_a_quit_leaves():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    # Sandboxed: this scenario builds a REAL WavesBridge, and a bridge that
    # finds the packaged app's config dir adopts its settings, writes its log,
    # and starts a real scan of the user's music library.
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-close-gate-test-")
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
    assert proc.returncode == _EXIT_OK, f"closing the window no longer behaves:\n{tail}"


if __name__ == "__main__":
    sys.exit(_scenario())
