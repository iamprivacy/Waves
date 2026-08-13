"""The paste glyph searches what it pastes; a bare Ctrl+V still only fills.

WHAT THIS FENCES OFF
--------------------
Issue #18 asked for the search bar's paste button to also run the search.
The wiring is a one-shot arm on the decode pipeline: the glyph's click sets
``searchDecoder.submitPending`` right before ``paste()``, the decode that
paste starts latches it (``submitArmed``), and ``onDecoded`` submits. Three
behaviors must hold:

1. A glyph-armed paste auto-searches once the decrypt animation settles.
2. A plain paste (Ctrl+V, no glyph) fills the field but does NOT search,
   unless it is a TIDAL link (that auto-search predates the glyph arm).
3. The arm is one-shot and disarmed by any non-decode text change, so a
   stale arm (empty clipboard at click time) cannot fire on a later paste.

The scenario never touches the OS clipboard: a paste, to the decoder, is a
multi-char text jump typing can't produce, so the test assigns the field's
text directly, exactly the signal ``noteTextChanged`` keys on. The observable
is ``root._searchSeq``: every submit path stamps it with ``root._navSeq``
before calling ``waves.search``, so "a search fired" is a pure QML fact and
the bridge stays offline.

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


def test_paste_glyph_auto_searches_and_plain_paste_does_not():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-paste-search-test-")
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
    assert (
        proc.returncode == _EXIT_OK
    ), f"paste auto-search behavior regressed. Scenario exit={proc.returncode}:\n{tail}"


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
    # The submit guard blocks auto-search on the Browse/Library/Settings
    # surfaces (a pasted link must not yank those pages); sit on the Search
    # tab, where the box lives, like a real paste would.
    q("browseOpen = false")
    # The decode runs ~0.6s (24 ticks * 26ms); give it slack.
    decode_ms = 1200

    def paste(text: str) -> None:
        # A paste, to the decoder, is a >=4-char jump; assign directly so the
        # OS clipboard is never involved.
        q(f"searchField.text = {text!r}")

    # 1. Glyph-armed paste: the glyph click's sequence minus the real paste().
    q("_searchSeq = -99")
    q("searchField.forceActiveFocus(); searchField.clear(); searchDecoder.submitPending = true")
    paste("monolink amniotic")
    settle(decode_ms)
    armed_searched = bool(q("_searchSeq === _navSeq")) and q("searchField.text") == "monolink amniotic"

    # 2. Plain paste (no glyph): fills, does not search.
    q("_searchSeq = -99")
    q("searchField.clear()")
    paste("another band name")
    settle(decode_ms)
    plain_inert = bool(q("_searchSeq === -99")) and q("searchField.text") == "another band name"

    # 2b. A pasted TIDAL link still auto-searches without the glyph.
    q("_searchSeq = -99")
    q("searchField.clear()")
    paste("https://tidal.com/browse/album/12345")
    settle(decode_ms)
    url_searched = bool(q("_searchSeq === _navSeq"))

    # 3. One-shot: a stale arm is cleared by a non-decode text change (typing),
    #    so the next plain paste stays inert.
    q("_searchSeq = -99")
    q("searchField.clear(); searchDecoder.submitPending = true")
    q("searchField.text = searchField.text + 'a'")  # 1-char jump: no decode, disarms
    disarmed = not bool(q("searchDecoder.submitPending"))
    q("searchField.clear()")
    paste("yet another term")
    settle(decode_ms)
    stale_inert = bool(q("_searchSeq === -99"))

    # 4. The REAL glyph handler with an empty clipboard: paste() inserts
    #    nothing, so no decode ever latches the arm, and the handler itself
    #    must drop it, or the NEXT bare Ctrl+V (a paste the user meant to
    #    edit) inherits the glyph's search. The offscreen platform's clipboard
    #    is in-process, cleared here; the OS clipboard is never involved.
    from PySide6.QtGui import QGuiApplication

    QGuiApplication.clipboard().clear()
    q("_searchSeq = -99")
    q("searchField.clear()")
    q("pasteGlyph.clicked()")
    glyph_disarmed = not bool(q("searchDecoder.submitPending"))
    paste("paste meant for editing")
    settle(decode_ms)
    empty_glyph_inert = bool(q("_searchSeq === -99"))

    ok = armed_searched and plain_inert and url_searched and disarmed and stale_inert
    ok = ok and glyph_disarmed and empty_glyph_inert
    print(
        f"armed_searched={armed_searched} plain_inert={plain_inert} url_searched={url_searched} "
        f"disarmed={disarmed} stale_inert={stale_inert} "
        f"glyph_disarmed={glyph_disarmed} empty_glyph_inert={empty_glyph_inert}",
        flush=True,
    )
    return _EXIT_OK if ok else _EXIT_REGRESSED


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
