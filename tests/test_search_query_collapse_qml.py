"""Issue #39: the search field sends the words it shows, and says when a
search found nothing.

A pasted title with a line break reads as one line in the single-line field,
but the break used to reach the bridge untouched. Pressing Enter now rewrites
the field to the collapsed query and sends that; the paste decoder collapses
the same way. A search that answers with nothing replaces the "begin" hint
with "No results for ..." so it no longer looks like nothing happened.

Runs in a SUBPROCESS like the other Main.qml scenarios.
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

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"


def test_search_query_collapses_and_an_empty_answer_says_so():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-query-collapse-test-")
    env["HOME"] = env["XDG_CONFIG_HOME"]
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
    assert proc.returncode == _EXIT_OK, f"search query hygiene regressed. Scenario exit={proc.returncode}:\n{tail}"


def _run_scenario() -> int:
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
        from PySide6.QtCore import Slot

        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    searched: list[str] = []

    class SpyBridge(WavesBridge):
        @Slot(str)
        def search(self, needle: str) -> None:
            searched.append(needle)

    engine = QQmlApplicationEngine()
    bridge = SpyBridge(tidal=None)
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
    q("browseOpen = false")
    verdicts = {}

    # Enter on a field holding an invisible line break sends one space.
    # Typed in steps under the decoder's 4-char paste threshold, so the field
    # keeps the raw text (a one-jump assignment reads as a paste and decodes).
    typed = "Record\nSoft  Power"
    q("searchField.forceActiveFocus(); searchField.clear()")
    for i in range(3, len(typed) + 3, 3):
        q(f"searchField.text = {typed[:i]!r}")
    settle(50)
    raw_kept = "\n" in str(q("searchField.text"))
    q("searchField.accepted()")
    settle(50)
    verdicts["raw_text_reached_the_field"] = raw_kept
    verdicts["enter_sends_collapsed"] = searched[-1:] == ["Record Soft Power"]
    verdicts["field_shows_what_was_sent"] = q("searchField.text") == "Record Soft Power"
    verdicts["query_remembered"] = q("root.lastSearchQuery") == "Record Soft Power"

    # The paste glyph's decode path collapses a tab the same way.
    q("searchField.forceActiveFocus(); searchField.clear(); searchDecoder.submitPending = true")
    q("searchField.text = 'tab\\tseparated words'")
    settle(1200)
    verdicts["decoder_sends_collapsed"] = searched[-1:] == ["tab separated words"]

    # An empty answer says so; an answer with rows does not.
    empty = {"artists": [], "albums": [], "tracks": [], "videos": [], "playlists": [], "mixes": [], "top": None}
    q("root._searchSeq = root._navSeq; root.lastSearchQuery = 'zzz'")
    bridge.searchResults.emit(empty)
    settle(200)
    verdicts["empty_answer_named"] = (
        not bool(q("root.hasResults")) and q("emptyHint.text") == "No results for \u201czzz\u201d"
    )
    q("root._searchSeq = root._navSeq; root.lastSearchQuery = 'band'")
    row = {"id": "a1", "name": "Band", "art": "", "roles": "", "popularity": -1}
    bridge.searchResults.emit({**empty, "artists": [row]})
    settle(300)
    verdicts["rows_clear_the_hint"] = q("root.searchNoResultsFor") == "" and not bool(q("emptyHint.visible"))

    failed = [k for k, v in verdicts.items() if not v]
    for k, v in verdicts.items():
        print(f"{k}: {v}")
    return _EXIT_REGRESSED if failed else _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
