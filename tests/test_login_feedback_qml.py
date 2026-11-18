"""The sign-in panel shows why a sign-in failed, and holds the button while one runs.

tests/test_login_feedback.py covers the bridge's wording; this boots the real
Main.qml offscreen with the sign-in panel up (issues #7, #56, #63: a failure
reported only in the status bar under the panel read as nothing happening):

- pasting the TIDAL login page link puts the "Page not found" directions on
  the panel, under the paste field,
- while a sign-in runs, COMPLETE SIGN-IN reads SIGNING IN… and is disabled,
- reopening the browser login clears the old error.

Runs in a SUBPROCESS like the other Main.qml scenarios (shares the sibling's
``_boot``). With WAVES_SHOT set, it also saves a picture of the panel there.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from test_progress_matrix_stable_width import _EXIT_NO_QT, _EXIT_OK, _EXIT_PRECONDITION, _boot

_AUTHORIZE = "https://login.tidal.com/authorize?response_type=code&client_id=x&code_challenge=y"


def _scenario() -> int:
    booted = _boot()
    if not isinstance(booted, tuple):
        return booted
    root, q, settle, bridge = booted
    from PySide6.QtCore import QMetaObject, QObject

    from waves.waves_ui import backend

    problems: list[str] = []

    def check(ok, what: str) -> None:
        if not ok:
            problems.append(what)

    # Undo the boot's park: this scenario is about the panel. The browser is
    # never opened; the panel is put in the state it has after it was.
    q("loginPanel.visible = true")
    q("loginPanel.urlOpened = true")
    settle(80)

    err = root.findChild(QObject, "loginErrorText")
    btn = root.findChild(QObject, "loginCompleteBtn")
    field = root.findChild(QObject, "redirectField")
    if err is None or btn is None or field is None:
        print("sign-in panel items not found", file=sys.stderr)
        return _EXIT_PRECONDITION

    check(err.property("visible") is False, "the error line showed before any failure")

    # 1. The login page link: refused, with directions, on the panel.
    # Straight to the bridge, as the decoder's submit does once a paste has
    # landed (setting the field's text here would start the decode animation).
    bridge.completeLogin(_AUTHORIZE)
    settle(80)
    check(err.property("visible") is True, "a refused paste left the panel silent")
    check(err.property("text") == backend._LOGIN_MSG_NO_CODE, f"wrong line: {err.property('text')!r}")
    shot = os.environ.get("WAVES_SHOT")
    if shot:
        root.grabWindow().save(shot)

    # 2. A sign-in in flight holds the button.
    bridge._set_login_busy(True)
    settle(80)
    check(btn.property("enabled") is False, "COMPLETE SIGN-IN stayed live during a sign-in")
    check(btn.property("label") == "SIGNING IN…", f"busy label: {btn.property('label')!r}")
    bridge._set_login_busy(False)
    settle(80)
    check(btn.property("enabled") is True, "COMPLETE SIGN-IN stayed disabled after the sign-in ended")
    check(btn.property("label") == "COMPLETE SIGN-IN", "the label did not come back")

    # 2b. A click while the paste is still decoding sends the real link,
    # not the scrambled text on screen (the guard would then drop the real one).
    sent: list[str] = []

    def _record(url):
        sent.append(url)
        raise RuntimeError("stop here: only the link sent matters")

    bridge.tidal.session.pkce_get_auth_token = _record
    real = "https://tidal.com/android/login/auth?code=RealCode123&state=na"
    field.setProperty("text", real)
    check(q("loginDecoder.decoding") is True, "a pasted link did not start decoding")
    # The frame the bug lived in: the code is half revealed and the tail is
    # still scramble. No event loop runs between these lines, so the decode
    # timer cannot move on before the click.
    field.setProperty("text", real[:-6] + "#%&@!?")
    QMetaObject.invokeMethod(btn, "clicked")
    settle(800)
    check(sent[:1] == [real], f"a mid-decode click sent {sent!r}")
    check(len(sent) == 1, f"the link was sent {len(sent)} times")

    # 3. Reopening the browser login clears the old error. Its worker would
    # ask tidalapi for a URL; the error is cleared before that, on this thread.
    bridge.threadpool.start = lambda *a, **k: None  # type: ignore[method-assign]
    bridge.beginLogin()
    settle(80)
    check(err.property("visible") is False, "the old error outlived REOPEN BROWSER LOGIN")

    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    return _EXIT_OK


def test_the_sign_in_panel_says_why_and_holds_the_button():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    # Sandboxed: this scenario builds a REAL WavesBridge (see the siblings).
    sandbox = tempfile.mkdtemp(prefix="waves-login-feedback-test-")
    env["XDG_CONFIG_HOME"] = sandbox
    env["HOME"] = sandbox
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
    assert proc.returncode == _EXIT_OK, f"the sign-in panel feedback no longer behaves:\n{tail}"


if __name__ == "__main__":
    sys.exit(_scenario())
