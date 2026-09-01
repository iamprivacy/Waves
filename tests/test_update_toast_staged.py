"""The update toast never names a version the restart will not land.

THE BUG WE ARE FENCING OFF
--------------------------
install() refuses to stage a second update over an already-armed helper, on
purpose: the two would race the same backup folder (tests/test_updater.py pins
that refusal). What it does in that case is hand back the STAGED result, and it
does so before it has looked at the release it was asked for.

So on Windows, with a swap staged yesterday and never restarted into: the toast
offered today's newer release, the user pressed INSTALL, the toast said that
newer version was installed, and the restart landed yesterday's. The Settings
card was honest throughout (it pins the staged version into its own copy); the
toast was not, because it renders its own idea of the version and had never
been told the install went nowhere near it.

HOW THIS STAYS FIXED
--------------------
An offer is refused outright while a restart is pending (the pill goes on
saying what the restart will give, and the newer release is offered again by
the check that follows the restart), and the installed label reads the staged
version whenever the app knows one.

Runs in a SUBPROCESS for the same reason as the other QML scenarios: building
the bridge installs process-global handlers that must not leak into the suite.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_EXIT_OK = 0
_EXIT_OFFERED_OVER_A_RESTART = 1  # the offer overwrote the restart pill
_EXIT_NAMED_THE_WRONG_VERSION = 2  # the label promised a version nobody staged
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"

# Staged yesterday, never restarted into. Spelled as the release TAG, which is
# what the updater stores: everything the UI prints puts its own "v" in front,
# so an unstripped tag renders "Waves vv0.1.26".
_STAGED_TAG = "v0.1.26"
_STAGED = "0.1.26"
_NEWER = "0.1.27"  # published since, and what the check finds today


def test_the_toast_does_not_offer_over_a_staged_restart():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-toaststaged-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-8:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not drive the update toast in this environment:\n{tail}")
    assert proc.returncode != _EXIT_OFFERED_OVER_A_RESTART, (
        "the toast offered an install over an update already staged: pressing it hands back the "
        f"staged result, so the restart lands the other version.\n{tail}"
    )
    assert (
        proc.returncode != _EXIT_NAMED_THE_WRONG_VERSION
    ), f"the toast said a version was installed that the restart will not land.\n{tail}"
    assert proc.returncode == _EXIT_OK, f"scenario exit={proc.returncode}:\n{tail}"


def _run_scenario() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from _qml_offline import patch_offline

        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    patch_offline()
    engine = QQmlApplicationEngine()
    bridge = WavesBridge(tidal=None)
    # A swap staged in an earlier session and re-armed at this launch. This is
    # exactly the shape resume_pending_apply leaves behind (and the shape the
    # updater's own tests use), so status() reports the restart as pending.
    bridge._updater._armed_result = {
        "ok": True,
        "version": _STAGED_TAG,
        "applied_to": "staged",
        "relaunch": True,
    }
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
        ctx = QQmlEngine.contextForObject(root)
        e = QQmlExpression(ctx, root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(e.error().toString())
        return r[0] if isinstance(r, tuple) else r

    def settle(ms: int = 150) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    settle()
    if not q("waves.appUpdateStatus().pending_restart"):
        print("the staged swap did not read as a pending restart", file=sys.stderr)
        return _EXIT_PRECONDITION

    # The check finds the newer release and asks the toast to offer it.
    q(f'updateToast.offer("{_NEWER}")')
    settle()
    phase = q("updateToast.phase")
    print(f"phase_after_offer={phase!r}", flush=True)
    if phase:
        return _EXIT_OFFERED_OVER_A_RESTART

    # And whatever the toast is showing when an install reports done, the
    # version it names is the one the restart will actually land.
    q(f'updateToast.version = "{_NEWER}"')
    landed = q("updateToast.landedVersion()")
    print(f"landed={landed!r} staged={_STAGED!r} tag={_STAGED_TAG!r}", flush=True)
    # The staged version, and spelled the way the label can print it: the label
    # supplies the "v" itself, so handing back the raw tag reads "vv0.1.26".
    if landed != _STAGED:
        return _EXIT_NAMED_THE_WRONG_VERSION

    # The label is what the user reads, so it has to be the one asking. Pinned
    # on the label's own expression, not merely on the function existing.
    label = '"Waves v" + updateToast.landedVersion() + " installed"'
    if label not in QML_MAIN.read_text(encoding="utf-8"):
        print("the installed label no longer asks what the restart will land", file=sys.stderr)
        return _EXIT_NAMED_THE_WRONG_VERSION

    return _EXIT_OK


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
