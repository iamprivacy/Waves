"""Regression: an account switch must drop navForwardHistory too, not just navHistory.

THE BUG WE ARE FENCING OFF
--------------------------
``onLoggedInChanged`` clears ``navHistory`` on an account switch because history
snapshots hold page payloads (personalized rows) and artist ids from the
previous account. When ``navForwardHistory`` was introduced (mouse forward
side button navigation) it was left out of that reset: a snapshot pushed onto
it by ``navBack()`` while on Account A survived a switch to Account B. Pressing
the forward button afterward replayed Account A's page (or artist) as if it
were Account B's data, exactly the cross-account leak the existing reset was
written to prevent for the back stack.

HOW THIS STAYS FIXED
--------------------
``onLoggedInChanged`` clears ``navForwardHistory`` alongside ``navHistory``.

The same reset now also drops the armed Browse-category intent
(``catPendingDl`` / ``catPendingPv`` / ``catDlPrompt``), which leaked the same
way: a DOWNLOAD ALL click whose resolve the logout threw away stayed armed and
was consumed by the next resolve of that path on the NEW account. Tacked on at
the end is the confirm dialog's "Don't ask again" tick, which every exit from
the dialog must clear, not just Cancel.

HOW IT IS RUN
-------------
Boots the REAL ``Main.qml``, drills into a distinctively-titled Browse item,
presses Back (populating ``navForwardHistory`` with that item's snapshot),
fires the bridge's ``loggedInChanged`` signal (the account-switch trigger),
and asserts the forward stack is empty and that pressing Forward afterward
never resurfaces the old item. Runs in a SUBPROCESS for the same reason as
``test_browse_back_scroll.py``: constructing the bridge installs a
process-global Qt message handler / diagnostics logging that would otherwise
leak into unrelated tests in the same interpreter.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_EXIT_FIXED = 0  # Forward history was dropped on account switch: fix present.
_EXIT_REGRESSED = 1  # The old account's page leaked back in via Forward: bug is back.
_EXIT_NO_QT = 77  # PySide6 / a usable Qt platform is unavailable: skip.
_EXIT_PRECONDITION = 78  # environment could not set up the scenario.

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"

_LEAK_MARKER = "ACCOUNT-A-ONLY-PAGE"


def test_account_switch_clears_forward_history():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-fwdhistory-test-")

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
        pytest.skip(f"could not build the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_FIXED, (
        "An account switch left a stale entry in navForwardHistory, so the "
        f"mouse forward button can replay the previous account's page. Scenario exit={proc.returncode}:\n{tail}"
    )


def _leaked_item() -> dict:
    return {
        "key": "item:playlist:leak",
        "title": _LEAK_MARKER,
        "header": {"title": _LEAK_MARKER, "kind": "playlist"},
        "sections": [],
        "error": False,
    }


def _run_scenario() -> int:
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
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

    def pump(predicate, timeout_ms: int = 6000) -> bool:
        loop = QEventLoop()
        state = {"ok": False}

        def tick():
            try:
                if predicate():
                    state["ok"] = True
                    loop.quit()
            except Exception:
                loop.quit()

        poll = QTimer()
        poll.setInterval(25)
        poll.timeout.connect(tick)
        poll.start()
        QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec()
        poll.stop()
        return state["ok"]

    # 1. Land on Browse, then drill into a distinctively-titled item.
    root.setProperty("browseOpen", True)
    q('openBrowseItem("playlist", "leak")')
    bridge.browsePageLoaded.emit(_leaked_item())
    if not pump(lambda: q("browsePageKey") == "item:playlist:leak"):
        print("drill-in page never loaded", file=sys.stderr)
        return _EXIT_PRECONDITION

    # 2. Back to Browse: pushes the item's snapshot onto navForwardHistory.
    q("navBack()")
    if not pump(lambda: q("navForwardHistory.length") > 0):
        print("Back did not populate navForwardHistory", file=sys.stderr)
        return _EXIT_PRECONDITION
    top_title = q(
        "navForwardHistory.length > 0 && navForwardHistory[navForwardHistory.length - 1].page "
        "? navForwardHistory[navForwardHistory.length - 1].page.title : ''"
    )
    if top_title != _LEAK_MARKER:
        print(f"forward-history top was not the expected marker page: {top_title!r}", file=sys.stderr)
        return _EXIT_PRECONDITION

    # 3. Simulate an account switch: this is the real trigger onLoggedInChanged
    #    responds to, independent of the actual loggedIn value.
    bridge.loggedInChanged.emit()

    remaining = q("navForwardHistory.length")
    if remaining != 0:
        print(f"navForwardHistory still has {remaining} stale entr(y/ies) after account switch", file=sys.stderr)
        return _EXIT_REGRESSED

    # 4. Forward must now be inert, never resurrecting the old account's page.
    before_key = q("browsePageKey")
    q("navForward()")
    after_key = q("browsePageKey")
    if after_key == "item:playlist:leak" or after_key != before_key:
        print(f"navForward() resurrected the old page: {before_key!r} -> {after_key!r}", file=sys.stderr)
        return _EXIT_REGRESSED

    # 5. Same leak, different state: an armed DOWNLOAD ALL / PREVIEW intent.
    #    The tile records the category path it is waiting on and the backend
    #    answers it later; a logout drops that answer (generation bump), so the
    #    intent sits armed. Signing back in and opening the same category made
    #    the next resolve consume it: a confirm nobody asked for, or the whole
    #    category queued on the new account off a click made on the old one.
    q('catPendingDl = "pages/genre-rock"')
    q('catPendingPv = "pages/genre-jazz"')
    q('catDlPrompt = ({path: "pages/genre-rock", title: "Rock", count: 12})')
    bridge.loggedInChanged.emit()
    armed = [
        name
        for name, expr in (
            ("catPendingDl", 'catPendingDl !== ""'),
            ("catPendingPv", 'catPendingPv !== ""'),
            ("catDlPrompt", "catDlPrompt !== null"),
        )
        if bool(q(expr))
    ]
    if armed:
        print(f"category intent survived the account switch: {', '.join(armed)}", file=sys.stderr)
        return _EXIT_REGRESSED

    # 6. Clicking away from the confirm must forget "Don't ask again" too.
    #    Left ticked, the dialog re-opens pre-armed for an unrelated category
    #    and confirming that one persists the opt-out for good.
    q('catDlPrompt = ({path: "pages/genre-rock", title: "Rock", count: 12})')
    q("cdSkip.checked = true")
    q("catDlDismiss()")
    if bool(q("cdSkip.checked")) or q("catDlPrompt") is not None:
        print("dismissing the confirm left 'Don't ask again' ticked", file=sys.stderr)
        return _EXIT_REGRESSED

    print(
        f"navForwardHistory cleared on account switch, navForward() stayed inert (key={after_key!r});"
        " category intent and the confirm's tick cleared too",
        flush=True,
    )
    return _EXIT_FIXED


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
