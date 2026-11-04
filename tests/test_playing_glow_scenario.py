"""A playing cover's rim, on the Browse shelves: whole, and eased per state.

The rim (Main.qml `Art`, `fxGlowT` and friends, drawn by
`shaders/cover_blend.frag`) reaches past the cover. The scenario proves,
on a landing with a hero shelf and a plain one:

- the rim's room: on the first and the last card of a shelf, and on a
  hero, the cover plus `artGlowPad` on every side lies inside the shelf's
  clip, and the delegate holding a raised cover orders above its neighbors;
- its states: buffering pulses the strength, playing holds it full,
  pausing settles it to the paused level, and a stop fades it out at once
  even while the cover is still held raised under the pointer.

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

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"


def test_playing_rim_is_whole_and_eased_on_the_shelves() -> None:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-playing-glow-test-")
    env["XDG_CONFIG_HOME"] = sandbox
    env["HOME"] = sandbox
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-20:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"the playing rim regressed:\n{tail}"


def _run_scenario() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QMetaObject, QPoint, QTimer, QUrl
        from PySide6.QtGui import QColor, QGuiApplication, QImage
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
        from PySide6.QtQuick import QQuickItem, QQuickWindow
        from PySide6.QtTest import QTest
    except Exception as exc:  # pragma: no cover - environment guard
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    app = QGuiApplication.instance() or QGuiApplication([])
    if sys.platform != "darwin":
        app.setApplicationName("Waves")
        app.setOrganizationName("Waves")
    try:
        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:  # pragma: no cover - environment guard
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from tests._qml_offline import PARK_LOGIN_QML, patch_offline

    # Local covers, so the art reaches Ready offline and the palette grab
    # has pixels to read.
    art_dir = Path(tempfile.mkdtemp(prefix="waves-playing-glow-art-"))

    def cover(name: str, rgb: tuple) -> str:
        img = QImage(64, 64, QImage.Format.Format_RGB32)
        img.fill(QColor(*rgb))
        path = art_dir / f"{name}.png"
        img.save(str(path))
        return QUrl.fromLocalFile(str(path)).toString()

    hero_art = cover("hero", (40, 120, 200))
    card_art = cover("card", (200, 80, 40))

    patch_offline()  # BEFORE the bridge: its __init__ fires the sign-in check
    bridge = WavesBridge(tidal=None)
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
    if not isinstance(root, QQuickWindow):
        print("root object is not a window", file=sys.stderr)
        return _EXIT_PRECONDITION

    def q(expr: str):
        e = QQmlExpression(QQmlEngine.contextForObject(root), root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(e.error().toString())
        return r[0] if isinstance(r, tuple) else r

    def pump(until, timeout_ms: int = 4000) -> bool:
        state = {"ok": False}
        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(25)

        def tick():
            try:
                if until():
                    state["ok"] = True
                    loop.quit()
            except Exception:
                loop.quit()

        poll.timeout.connect(tick)
        poll.start()
        QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec()
        poll.stop()
        return state["ok"]

    def settle(ms: int = 150) -> None:
        pump(lambda: False, ms)

    # WAVES_SCENARIO_GRABS=<dir> saves a window grab at the moments worth
    # eyeballing (a livetest aid, never part of the assertion).
    grab_dir = os.environ.get("WAVES_SCENARIO_GRABS", "")

    def grab(name: str) -> None:
        if grab_dir:
            settle(40)
            root.grabWindow().save(str(Path(grab_dir) / f"{name}.png"))

    # The landing: a hero shelf (280 px covers) over a plain one (200 px),
    # six albums each, every cover a local file.
    def albums(prefix: str, art: str) -> list:
        return [
            {"id": f"{prefix}{j}", "kind": "album", "title": f"Album {j}", "artist": "A", "art": art} for j in range(6)
        ]

    landing = {
        "sections": [
            {"title": "Hero shelf", "rowKind": "cards", "items": albums("h", hero_art)},
            {"title": "Plain shelf", "rowKind": "cards", "items": albums("a", card_art)},
        ],
        "genres": [],
        "moods": [],
        "decades": [],
        "error": False,
    }
    bridge._browse_root = lambda: dict(landing)

    root.resize(1280, 1000)
    root.show()
    settle(400)
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q(PARK_LOGIN_QML)
    bridge._logged_in = True
    bridge.loggedInChanged.emit()
    settle(100)
    q("termsGate.visible = false")
    q("ffmpegGate.visible = false")
    q('browseStyle = "art"')
    q("root.openBrowse()")
    if not pump(lambda: q("browsePageKey") == "" and q("browseSections.length") == 2 and not q("browseBuilding")):
        print(
            f"Browse landing never built (sections={q('browseSections.length')}, error={q('browseError')})",
            file=sys.stderr,
        )
        return _EXIT_PRECONDITION
    settle(400)

    def visual_items():
        # Delegates a ListView instantiates have no QObject parent, so
        # findChildren never sees them; the visual tree does.
        stack = [root.contentItem()]
        while stack:
            it = stack.pop()
            yield it
            stack.extend(it.childItems())

    def art_for(card_id: str) -> QQuickItem | None:
        # The cover is the Art carrying this card's preview identity.
        for it in visual_items():
            mo = it.metaObject()
            if mo.indexOfProperty("fxGlowT") >= 0 and it.property("fxId") == card_id:
                return it
        return None

    def shelf_of(it: QQuickItem) -> QQuickItem | None:
        p = it.parentItem()
        while p is not None and p.metaObject().indexOfProperty("contentX") < 0:
            p = p.parentItem()
        return p

    def delegate_of(it: QQuickItem, shelf: QQuickItem) -> QQuickItem:
        # The shelf's own child on the way up from the cover.
        p = it
        while p.parentItem() is not None and p.parentItem() is not shelf.property("contentItem"):
            p = p.parentItem()
        return p

    failures: list[str] = []

    def check(what: str, cond: bool, detail: str = "") -> None:
        if not cond:
            failures.append(f"{what}{(': ' + detail) if detail else ''}")

    pad = float(q("artGlowPad"))
    lift = float(q("artPlayLift"))
    blend_w = float(q("artPlayBlendW"))

    def reach(size: float) -> float:
        # As far as the rim can be seen: three falloffs (the quad goes on
        # past that, at nothing).
        return 3 * max(2.5, size * blend_w)

    def start(card_id: str) -> QQuickItem | None:
        q('previewError = ""')
        q('previewKind = "album"')
        q(f'previewId = "{card_id}"')
        q("previewLoading = true")
        q("previewPlaying = false")
        art = art_for(card_id)
        if art is None:
            return None
        if not pump(lambda: float(art.property("fxGlowT")) > 0.9, 2500):
            check(f"{card_id}: the rim never came up while buffering", False, f"fxGlowT={art.property('fxGlowT')}")
        return art

    def stop() -> None:
        q('previewKind = ""')
        q('previewId = ""')
        q("previewLoading = false")
        q("previewPlaying = false")
        settle(900)

    def clippers(it: QQuickItem) -> list[QQuickItem]:
        # Every ancestor that clips: the shelf's box, a page's scroll area,
        # anything up to the window. A rim cut by any one of them is cut.
        found = []
        p = it.parentItem()
        while p is not None:
            if bool(p.property("clip")):
                found.append(p)
            p = p.parentItem()
        return found

    def room(card_id: str, art: QQuickItem, shelf: QQuickItem) -> None:
        w, h = float(art.property("width")), float(art.property("height"))
        r = reach(min(w, h))
        # The box that clips the shelf: its parent, when the shelf does
        # not clip itself.
        box = shelf if bool(shelf.property("clip")) else shelf.parentItem()
        check(f"{card_id}: the shelf is clipped by a box around it", box is not None and bool(box.property("clip")))
        # Through the cover's own transforms (the raise is on), so this
        # is where the rim really falls in each clip, the window included.
        for clip in [*clippers(art), root.contentItem()]:
            tl = art.mapToItem(clip, -r, -r)
            br = art.mapToItem(clip, w + r, h + r)
            sw, sh = float(clip.property("width")), float(clip.property("height"))
            check(
                f"{card_id}: the rim lies inside {clip.metaObject().className()} ({clip.objectName() or 'no name'})",
                tl.x() >= -0.01 and tl.y() >= -0.01 and br.x() <= sw + 0.01 and br.y() <= sh + 0.01,
                f"rim {tl.x():.1f},{tl.y():.1f} to {br.x():.1f},{br.y():.1f} in {sw:.0f}x{sh:.0f}",
            )
        dl = delegate_of(art, shelf)
        check(f"{card_id}: the raised cover's delegate orders above its neighbors", float(dl.property("z")) >= 1)
        # And the card itself still sits where the shelf shows it: its
        # cover's top edge on the shelf's own top (less the raise's half
        # swell), with the box one pad out around it.
        top = art.mapToItem(shelf, 0, 0).y()
        check(
            f"{card_id}: the cover keeps its place in the shelf",
            abs(top + (lift - 1) * h / 2) < 1.5,
            f"top {top:.2f}",
        )
        inset = shelf.mapToItem(box, 0, 0)
        check(
            f"{card_id}: the box reaches one pad past the shelf",
            abs(inset.x() - pad) < 0.5 and abs(inset.y() - pad) < 0.5,
            f"inset {inset.x():.1f},{inset.y():.1f} for pad {pad:.0f}",
        )

    # 1. Room: the first and the last card of each shelf, the hero's too.
    for card_id in ("h0", "h5", "a0", "a5"):
        art = start(card_id)
        if art is None:
            print(f"no cover for {card_id} on the landing", file=sys.stderr)
            return _EXIT_PRECONDITION
        shelf = shelf_of(art)
        check(f"{card_id}: the cover sits in a shelf", shelf is not None)
        if shelf is not None and card_id.endswith("5"):
            # The last card is past the window's right edge until the shelf
            # is scrolled to its end.
            QMetaObject.invokeMethod(shelf, "positionViewAtEnd")
        settle(500)  # the raise and the shelf's own settle
        if shelf is not None:
            room(card_id, art, shelf)
        q("previewLoading = false")
        q("previewPlaying = true")
        settle(700)
        grab(f"playing_{card_id}")
        stop()

    # 1b. The Home tab's shelf: the same shelf shape inside the My Tidal
    # page, whose own scroll area used to clip at the side margins.
    q("homeSections = " + json.dumps([{"title": "Recent albums", "rowKind": "cards", "items": albums("m", card_art)}]))
    q('libraryCategory = "home"')
    q("root.openLibrary()")
    if not pump(lambda: q("libraryOpen") and art_for("m0") is not None):
        print("the Home shelf never built", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle(400)
    for card_id in ("m0", "m5"):
        art = start(card_id)
        if art is None:
            print(f"no cover for {card_id} on Home", file=sys.stderr)
            return _EXIT_PRECONDITION
        shelf = shelf_of(art)
        check(f"{card_id}: the cover sits in a shelf", shelf is not None)
        if shelf is not None and card_id.endswith("5"):
            QMetaObject.invokeMethod(shelf, "positionViewAtEnd")
        settle(500)
        if shelf is not None:
            room(card_id, art, shelf)
        grab(f"playing_{card_id}")
        stop()
    q("root.openBrowse()")
    if not pump(lambda: q("browsePageKey") == "" and art_for("a0") is not None):
        print("Browse never came back", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle(300)

    # 2. States, on the plain shelf's first card.
    art = start("a0")
    if art is None:
        return _EXIT_PRECONDITION
    settle(700)
    # Sampled across most of one pulse (1040 ms), not at two points, which
    # can land on either side of a crest and read alike.
    ks = []
    for _ in range(8):
        ks.append(float(art.property("fxGlowK")))
        settle(120)
    swing = max(ks) - min(ks)
    check("buffering: the strength pulses", swing > 0.12, "samples " + " ".join(f"{k:.2f}" for k in ks))
    lo, hi = float(q("artPlayBufferLo")), float(q("artPlayBufferHi"))
    check("buffering: the pulse stays inside its floor and ceiling", lo - 0.02 <= min(ks) and max(ks) <= hi + 0.02)
    check("buffering: the colors hold still", float(art.property("fxAmp")) < 0.01, f"fxAmp={art.property('fxAmp')}")

    q("previewLoading = false")
    q("previewPlaying = true")
    settle(900)
    check("playing: the strength is full", float(art.property("fxGlowK")) > 0.97, f"fxGlowK={art.property('fxGlowK')}")
    check("playing: the colors drift", float(art.property("fxAmp")) > 0.97, f"fxAmp={art.property('fxAmp')}")

    q("previewPlaying = false")
    settle(1000)
    level, breath = float(q("artPlayPauseK")), float(q("artPlayBreath"))
    kp = float(art.property("fxGlowK"))
    check(
        "paused: the strength settles to the paused level",
        level - breath / 2 - 0.03 <= kp <= level + breath / 2 + 0.03,
        f"fxGlowK={kp:.2f} for level {level} breath {breath}",
    )
    check("paused: the colors ease to a stop", float(art.property("fxAmp")) < 0.03, f"fxAmp={art.property('fxAmp')}")

    q("previewPlaying = true")
    settle(900)
    check(
        "resumed: the strength is full again",
        float(art.property("fxGlowK")) > 0.97,
        f"fxGlowK={art.property('fxGlowK')}",
    )

    # 3. Stop under the pointer: the raise is held, the rim is not.
    c = art.mapToScene(art.boundingRect().center())
    QTest.mouseMove(root, QPoint(int(c.x()) - 60, int(c.y()) - 60))
    settle(80)
    QTest.mouseMove(root, QPoint(int(c.x()), int(c.y())))
    settle(200)
    q('previewKind = ""')
    q('previewId = ""')
    q("previewPlaying = false")
    held = bool(art.property("fxHoldRaise"))
    t_ok = pump(lambda: float(art.property("fxGlowT")) < 0.01, 900)
    check("stopped: the rim fades out within its out time, pointer or not", t_ok, f"fxGlowT={art.property('fxGlowT')}")
    check("stopped: the raise is still held under the pointer", held and bool(art.property("fxRaised")))
    QTest.mouseMove(root, QPoint(2, 2))
    settle(400)

    for f in failures:
        print("REGRESSION:", f, file=sys.stderr)
    if failures:
        return _EXIT_REGRESSED
    print("playing rim scenario: ok")
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
    raise SystemExit(2)
