"""The console card's bottom control line holds both its controls, in every state.

WHAT THIS FENCES OFF
--------------------
The console browse style's card is 156px wide, so after its 8px margins the
control line at its foot is 140px. Two things live on it and neither one can
push the other: the preview control is anchored to the left edge, the download
box to the right. When their words together outgrow 140px they do not reflow,
they simply draw through each other, and nothing in the layout complains.

That is what happened. The download box reserves its widest state so its width
never jitters, and its widest state was the queued one: the QueueStack glyph
plus "QUEUED ALBUM" came to 101px. The preview control is 63px. 101 + 63 is
164, so the box began 25px inside the preview control and stayed there, on
every previewable card, whether or not anything was queued. It read worst
while actually queued, when both halves were drawing text on top of each other.

The fix is the one the art card's strip already makes (see acStrip's padFull /
padMin / availW and tests/test_browse_strip_fits_the_card.py): the words give
way rather than overrun. The queued label drops the media noun the full button
carries, because the card is already the noun, and the preview control stands
its word down when what is left of the line will not hold it, keeping the
glyph, which always fits.

The fence is the LINE, not those numbers: whatever either half says, in
whatever font the platform gives it, the preview control must end before the
download box begins and the box must end inside the line. That is the boundary
that actually collides, and it holds the fix without restating its arithmetic,
so a longer word, a new state or a wider font is caught here rather than on a
user's screen.

Runs in a SUBPROCESS like the other Main.qml scenarios: building the bridge
installs process-global handlers that must not leak into the suite.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"

CARD_ID = "al-fit"


def test_the_console_card_controls_never_overlap():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-card-line-fits-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-20:])
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, (
        "the console browse card's preview control and download box collided on their shared "
        f"control line. Scenario exit={proc.returncode}:\n{tail}"
    )


# Walks the object tree collecting whatever `pick` returns non-null for.
_COLLECT = """
function collect(item, pick, out) {
    if (!item) return out
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) {
        var k = kids[i].item || kids[i]
        if (!k) continue
        var got = pick(k)
        if (got !== null && got !== undefined) out.push(got)
        collect(k, pick, out)
    }
    return out
}
"""

# The download box is reached the way tests/test_library_presence_surfaces.py
# reaches it, by the word it prints; the preview control is named. Both are
# measured in the coordinates of the line they share, which is their parent.
_LINE_PROBE = (
    "JSON.stringify((function() {" + _COLLECT + " return collect(browseLanding, function(k) {"
    "   if (k.libWord === undefined || k.card === undefined) return null;"
    "   var words = collect(k, function(b) {"
    "     return b.objectName === 'bcDlWord' ? b : null; }, []);"
    "   if (words.length === 0) return null;"
    "   var box = words[0].parent;"
    "   var line = box.parent;"
    "   var pvs = collect(k, function(b) {"
    "     return b.objectName === 'bcPreview' ? b : null; }, []);"
    "   var pv = pvs.length > 0 ? pvs[0] : null;"
    "   return { id: '' + (k.card.id || ''), lineW: line.width,"
    "            word: '' + words[0].text,"
    "            pvShown: pv ? !!pv.visible : false,"
    "            pvX: pv ? pv.x : -1, pvW: pv ? pv.width : -1,"
    "            boxX: box.x, boxW: box.width }"
    " }, []) })())"
)


def _run_scenario() -> int:  # (a linear boot -> drive -> measure scenario)
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
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    engine = QQmlApplicationEngine()
    bridge = WavesBridge(tidal=None)
    engine.rootContext().setContextProperty("waves", bridge)
    engine.rootContext().setContextProperty("monoFont", "JetBrains Mono")
    engine.rootContext().setContextProperty("uiFontFamily", app.font().family())
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
    roots = engine.rootObjects()
    if not roots:
        print("Main.qml failed to load", file=sys.stderr)
        return _EXIT_PRECONDITION
    root = roots[0]
    root.setProperty("width", 1400)
    root.setProperty("height", 900)
    root.setProperty("visible", True)

    def q(expr: str):
        e = QQmlExpression(QQmlEngine.contextForObject(root), root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(e.error().toString())
        return r[0] if isinstance(r, tuple) else r

    def settle(ms: int = 200) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    settle(300)
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q("browseBuilding = false")
    q("_browseAsyncBuild = false")
    q("libraryOn = true")
    q("root.openBrowse()")
    q("root.browseStyle = 'console'")
    card = {
        "id": CARD_ID,
        "kind": "album",
        "title": "Shadows Inside",
        "artist": "Miss May I",
        "year": "2017",
        "tracks": 11,
        "art": "",
    }
    root.setProperty("browseSections", [{"title": "ALBUMS", "rowKind": "cards", "data": "", "items": [card]}])
    settle(900)

    try:
        first = {r["id"]: r for r in json.loads(q(_LINE_PROBE))}
    except Exception as exc:
        print(f"control-line probe failed: {exc}", file=sys.stderr)
        return _EXIT_PRECONDITION
    if CARD_ID not in first:
        print(f"the console browse style built no card: {first}", file=sys.stderr)
        return _EXIT_PRECONDITION
    # An album card is previewable by construction, so a missing preview half
    # is the collision going unwatched, not a scenario that could not be set
    # up: fail on it rather than skipping, or renaming the control would
    # silence this whole guard.
    if not first[CARD_ID]["pvShown"]:
        print(
            f"the card built no preview control to measure against: {first[CARD_ID]!r}",
            file=sys.stderr,
        )
        return _EXIT_REGRESSED

    bad: list[str] = []

    def measure(label: str) -> None:
        try:
            seen = {r["id"]: r for r in json.loads(q(_LINE_PROBE))}
        except Exception as exc:
            bad.append(f"{label}: the control line could not be measured ({exc})")
            return
        m = seen.get(CARD_ID)
        if m is None:
            bad.append(f"{label}: the card vanished from the shelf")
            return
        # Anchored to opposite edges of the same line, so the only thing that
        # keeps them apart is their own width.
        if m["pvShown"] and m["pvX"] + m["pvW"] > m["boxX"]:
            bad.append(
                f"{label}: the download box runs {m['pvX'] + m['pvW'] - m['boxX']:.1f}px into the "
                f"preview control ({m!r})"
            )
        if m["boxX"] < 0 or m["boxX"] + m["boxW"] > m["lineW"] + 0.5:
            bad.append(f"{label}: the download box does not fit the line ({m!r})")
        if m["pvShown"] and m["pvX"] < 0:
            bad.append(f"{label}: the preview control starts outside the line ({m!r})")

    # Every state that draws on this line, at rest and while a preview is live:
    # the box reserves its widest state at all times, so a state that does not
    # fit poisons every other one too.
    for state in ("", "queued", "preparing", "running", "done", "failed"):
        q(f"root.dlHolder('{CARD_ID}').st = '{state}'")
        q(f"root.dlHolder('{CARD_ID}').pct = {50 if state == 'running' else -1}")
        q("root.dlHoldersGen += 1")
        settle(160)
        measure(f"idle preview, download {state or 'not started'!r}")

        # Playing: the preview swaps PREVIEW for a mono elapsed counter and
        # grows a "· STOP", the widest this half ever gets.
        q("root.previewKind = 'album'")
        q(f"root.previewId = '{CARD_ID}'")
        q("root.previewPlaying = true")
        q("root.previewLoading = false")
        q("root.previewDuration = 214000")
        q("root.previewPosition = 128000")
        settle(160)
        measure(f"playing preview, download {state or 'not started'!r}")

        # Buffering: the glyph goes, the word becomes "[buffering]".
        q("root.previewPlaying = false")
        q("root.previewLoading = true")
        settle(160)
        measure(f"buffering preview, download {state or 'not started'!r}")

        q("root.previewLoading = false")
        q("root.previewKind = ''")
        q("root.previewId = ''")
        settle(120)

    # The give-way itself, proven rather than assumed. With the queued label
    # down to one word the 156px card has room to spare, so nothing makes the
    # preview control stand its word down in this font: squeeze the card until
    # the line cannot hold both and the fence has to do its job. A future
    # narrower card, a longer word or a wider font all arrive here.
    q(f"root.dlHolder('{CARD_ID}').st = 'queued'")
    q("root.dlHoldersGen += 1")
    settle(160)
    roomy = json.loads(q(_LINE_PROBE))
    roomy_pv = next((r["pvW"] for r in roomy if r["id"] == CARD_ID), -1)
    squeeze = (
        "(function() {" + _COLLECT + " var cards = collect(browseLanding, function(k) {"
        f"   return (k.libWord !== undefined && k.card !== undefined && ('' + (k.card.id || '')) === '{CARD_ID}')"
        "     ? k : null; }, []);"
        " if (cards.length === 0) return false;"
        " cards[0].width = 128; return true })()"
    )
    if not q(squeeze):
        print("could not reach the card to squeeze it", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle(200)
    measure("queued download on a squeezed card")
    tight = json.loads(q(_LINE_PROBE))
    tight_pv = next((r["pvW"] for r in tight if r["id"] == CARD_ID), -1)
    if tight_pv < 0 or roomy_pv < 0:
        print(f"the squeezed card could not be measured ({roomy_pv} -> {tight_pv})", file=sys.stderr)
        return _EXIT_PRECONDITION
    if tight_pv >= roomy_pv:
        bad.append(
            f"squeezed card: the preview control never gave anything up ({roomy_pv} -> {tight_pv}), "
            "so the line only fits while the words happen to be short"
        )

    if bad:
        print("\n".join(bad), file=sys.stderr)
        return _EXIT_REGRESSED
    return _EXIT_OK


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
