"""The ARTISTS SHOW ALL / SHOW LESS label needs artists to label.

THE BUG WE ARE FENCING OFF
--------------------------
Every capped section on the search page hides its SHOW ALL toggle when the
section is empty, because the strip, the grid and the header all gate on
``sectionVisible(name, count)``. The ARTISTS toggle did not: it asked only
whether the view was the mixed All one and whether the row was expanded or
overflowing.

``searchArtistsExpanded`` is pref-backed (``search_sec_artists_expanded``), so
it comes back true at launch for anyone who has ever expanded the ARTISTS row.
With no search run there are no artists, the strip and the grid are correctly
gone, and the toggle was the only piece of the section left: a lone SHOW LESS
floating over an empty Search tab. Clicking it wrote the pref false, so it
vanished and did not come back, which is what made it look like a phantom.

HOW THIS STAYS FIXED
--------------------
The toggle carries the same count gate its section-mates get from
sectionVisible. The positive leg matters as much as the negative one: a guard
that hid the label unconditionally would also pass the empty case, so this
pins that one artist in the model brings the expanded row's SHOW LESS back.

Runs in a SUBPROCESS for the same reason as the other QML scenarios: building
the bridge installs process-global handlers that must not leak into the suite.
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
_EXIT_FLOATED = 1  # the label showed over a page with no artists on it
_EXIT_NEVER_SHOWS = 2  # the count gate swallowed the label that should show
_EXIT_PAGE_NOT_SHOWN = 3  # an invisible ancestor, so the read means nothing
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78


def test_the_artists_show_all_label_needs_artists():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-artists-showall-")
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
    if proc.returncode == _EXIT_PAGE_NOT_SHOWN:
        # Deliberately not a skip: the search page being off screen is this
        # scenario failing to set itself up, and a skip here would hide the
        # very bug the scenario exists to catch.
        pytest.fail(f"the search page never came on screen, so nothing was proved:\n{tail}")
    if proc.returncode == _EXIT_FLOATED:
        pytest.fail(f"SHOW LESS floated over a search page with no artists:\n{tail}")
    if proc.returncode == _EXIT_NEVER_SHOWS:
        pytest.fail(f"the label no longer shows when the row really is expanded:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"the ARTISTS toggle regressed:\n{tail}"


def _run_scenario() -> int:
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

    bridge = WavesBridge(tidal=None)
    # The state the bug needs, and the only state it needs: someone expanded
    # the ARTISTS row in an earlier session. Written BEFORE the QML loads,
    # because searchArtistsExpanded reads the pref once at creation.
    bridge.setWavesPref("search_sec_artists_expanded", True)

    engine = QQmlApplicationEngine()
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
    settle(120)
    # The app opens on Browse, and the search page is hidden wholesale while it
    # is. Reading the label from there would report HIDDEN whatever the binding
    # says, which is how the first draft of this test passed against the bug.
    q("root.openSearch()")
    settle(250)

    # Walks `data`, not `children`: the label is not a visual child of the item
    # that declares it, so a children-only walk reports MISSING and the whole
    # scenario skips itself green.
    #
    # `visible` on a QQuickItem is EFFECTIVE visibility, so it is only evidence
    # about this binding when every ancestor is visible. The walk reports the
    # first invisible ancestor and the caller refuses to judge without one.
    _SHOWN = """(function () {
            var hit = null;
            function walk(o, d) {
                if (!o || hit || d > 40) return;
                if (o.objectName === 'artistsShowAll') { hit = o; return }
                var kids = o.data !== undefined ? o.data : o.children;
                for (var i = 0; i < (kids ? kids.length : 0); ++i) walk(kids[i], d + 1);
            }
            walk(root, 0);
            if (!hit) return 'MISSING';
            var blocked = '';
            var o = hit.parent, n = 0;
            while (o && n < 25) {
                if (!o.visible) { blocked = o.objectName || ('' + o); break }
                o = o.parent; n++;
            }
            return (hit.visible ? 'SHOWN' : 'HIDDEN') + '|' + (blocked || 'ancestors-visible');
        })()"""

    if not bool(q("root.searchArtistsExpanded")):
        print("the pref did not reach the page; the scenario proves nothing", file=sys.stderr)
        return _EXIT_PRECONDITION
    if int(q("artistsModel.count")) != 0:
        print("a fresh page already held artists", file=sys.stderr)
        return _EXIT_PRECONDITION

    state = str(q(_SHOWN))
    if state == "MISSING":
        print("no item named artistsShowAll in the tree", file=sys.stderr)
        return _EXIT_PRECONDITION
    if not state.endswith("|ancestors-visible"):
        print(f"the search page is not on screen ({state}); the read proves nothing", file=sys.stderr)
        return _EXIT_PAGE_NOT_SHOWN
    if not state.startswith("HIDDEN"):
        print(f"with no search run at all the label read {state}", file=sys.stderr)
        return _EXIT_FLOATED

    # The positive leg: one artist, the row still expanded, and the toggle is
    # back. Without this an always-false binding would pass the check above.
    q("""artistsModel.append({ 'id': '1', 'name': 'A', 'art': '', 'popularity': 0 })""")
    settle(250)
    state = str(q(_SHOWN))
    if not state.startswith("SHOWN"):
        print(f"with an artist listed and the row expanded the label read {state}", file=sys.stderr)
        return _EXIT_NEVER_SHOWS
    return _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
