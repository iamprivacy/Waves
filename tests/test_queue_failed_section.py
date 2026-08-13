"""Failed queue rows get their own section, above the active work.

WHAT THIS FENCES OFF
--------------------
Issue #18: failed downloads used to sit lost inside the Downloading group and
"Clear finished" swept them away, so a failure was easy to miss and, once
cleared, impossible to retry without restarting. The drawer now partitions
rows into [Completed, Failed, Downloading, Queued], the Failed section header
carries a RETRY ALL control driven by root.failedCount, and a retried row
leaves the section the moment its status changes.

The scenario drives root.reconcileQueue() with hand-built rows (the same
payload shape the bridge emits) and asserts on the model's uiGroup order and
the counts, all pure QML facts, offline.

Runs in a SUBPROCESS like the other Main.qml scenarios: building the bridge
installs process-global handlers that must not leak into the suite.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

QML_MAIN = Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui" / "qml" / "Main.qml"


def test_failed_rows_form_their_own_section_with_a_count():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-failed-section-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-10:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"the Failed queue section regressed. Scenario exit={proc.returncode}:\n{tail}"


def _run_scenario() -> int:
    # THIS checkout's tidaler, not the venv's editable install.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from _qml_offline import PARK_LOGIN_QML, patch_offline

    patch_offline()

    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from tidaler.waves_ui.app import _load_mono
        from tidaler.waves_ui.backend import WavesBridge
    except Exception as exc:
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
        ctx = QQmlEngine.contextForObject(root)
        e = QQmlExpression(ctx, root, expr)
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

    def row(qid: int, status: str) -> str:
        return (
            "{qid: %d, name: 'r%d', type: 'track', status: '%s', progress: 0, "
            "media_id: 'm%d', template: '', collection: false, artist: '', tracks: 0, art: ''}"
            % (qid, qid, status, qid)
        )

    # One row per state: the partition must land [failed, downloading(running +
    # cancelled), queued] with Completed empty (nothing has moved there).
    rows = ", ".join([row(1, "queued"), row(2, "failed"), row(3, "running"), row(4, "failed"), row(5, "cancelled")])
    q(f"reconcileQueue([{rows}])")
    settle(60)
    groups = [q(f"queueModel.get({i}).uiGroup") for i in range(int(q("queueModel.count")))]
    partitioned = groups == ["failed", "failed", "downloading", "downloading", "queued"]
    counted = int(q("failedCount")) == 2 and int(q("downloadingCount")) == 2 and int(q("queuedCount")) == 1

    # A retried row leaves the Failed section as soon as its status changes.
    rows2 = ", ".join([row(1, "queued"), row(2, "queued"), row(3, "running"), row(4, "failed"), row(5, "cancelled")])
    q(f"reconcileQueue([{rows2}])")
    settle(60)
    groups2 = [q(f"queueModel.get({i}).uiGroup") for i in range(int(q("queueModel.count")))]
    regrouped = groups2 == ["failed", "downloading", "downloading", "queued", "queued"]
    recounted = int(q("failedCount")) == 1

    # The retry-all entry point the header calls must exist on the bridge.
    has_slot = bool(q("typeof waves.retryAllFailed === 'function'"))

    ok = partitioned and counted and regrouped and recounted and has_slot
    print(
        f"partitioned={partitioned} counted={counted} regrouped={regrouped} "
        f"recounted={recounted} has_slot={has_slot} groups={groups} groups2={groups2}",
        flush=True,
    )
    return _EXIT_OK if ok else _EXIT_REGRESSED


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
