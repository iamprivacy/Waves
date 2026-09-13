"""A release wears NEW for its first fortnight, on every surface that shows its date.

WHAT THIS FENCES OFF
--------------------
Three ways the mark goes wrong without anything on screen complaining.

A surface forgotten. Main.qml has no shared badge base: every pill is placed
by hand, per surface, and until this file nothing asserted that two surfaces
wear the same badges. A mark added to the art cards and forgotten on the
console cards, or added to search rows and forgotten on the album page, would
have passed the whole suite. The structural tests below name every wearer, so
losing one, or adding a sixth that bypasses the rule, fails here.

A verdict that is stored. This is the first value in the app that changes
while nothing happens. Album payloads persist to page_cache.json, restore with
no timestamp, and re-emit only when something else on the page changed, and
the app is expected to run for weeks. A NEW flag baked into a payload would
therefore stay wrong with nothing to correct it. The payload carries only the
DATE, which does not decay, and QML compares it against a cutoff a clock
moves. The scenario proves the marks clear when that cutoff moves, with no
payload sent.

A mark that keeps asking. Once you have the release (downloaded, or a full copy
on disk) the mark stays but stops breathing, row by row, so an album's own page
marks its tracks too: each one goes steady as it lands.

The scenario runs in a SUBPROCESS like the other Main.qml scenarios: building
the bridge installs process-global handlers that must not leak into the suite.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"

# Every surface that shows a release date, and so wears the mark. The two card
# styles share CardCaption, which is why it is one wearer and not two.
WEARERS = ["AlbumBlock", "CardCaption", "TrackRow", "album page header"]


# ----- structure ------------------------------------------------------------------

_NOISE_RE = re.compile(
    r"//[^\n]*"  # line comment
    r"|/\*.*?\*/"  # block comment
    r'|"(?:\\.|[^"\\])*"'  # double-quoted string
    r"|'(?:\\.|[^'\\])*'"  # single-quoted string
    r"|`(?:\\.|[^`\\])*`",  # template literal
    re.S,
)


def _clean() -> str:
    """Main.qml with comments and string literals blanked (lengths kept), so a
    brace or a name inside either cannot throw the matching off."""
    text = QML_MAIN.read_text(encoding="utf-8")
    return _NOISE_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _block_end(clean: str, brace: int) -> int:
    depth = 0
    for i in range(brace, len(clean)):
        if clean[i] == "{":
            depth += 1
        elif clean[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    raise AssertionError(f"unbalanced block at offset {brace}")


def _spans(clean: str, pattern: str) -> list[tuple[int, int]]:
    return [(m.start(), _block_end(clean, m.end() - 1)) for m in re.finditer(pattern, clean)]


def _wearers(clean: str) -> list[tuple[str, str]]:
    """(wearer, instance source) for every NewTag placed in Main.qml."""
    comps = {
        m.group(1): (m.start(), _block_end(clean, m.end() - 1))
        for m in re.finditer(r"\bcomponent\s+(\w+)\s*:\s*[\w.]+\s*\{", clean)
    }
    out = []
    for start, end in _spans(clean, r"(?<![\w.])NewTag\s*\{"):
        body = clean[start : end + 1]
        if "browseItemHeader.hd" in body:
            out.append(("album page header", body))
            continue
        inside = [(e - s, name) for name, (s, e) in comps.items() if s < start and end <= e]
        out.append((min(inside)[1] if inside else "?", body))
    return out


def test_there_are_marks_to_check() -> None:
    """A parser that matched nothing would make every structural test pass."""
    assert "component NewTag" in QML_MAIN.read_text(encoding="utf-8")
    assert len(_wearers(_clean())) >= len(WEARERS)


def test_every_surface_that_shows_a_release_date_wears_the_mark() -> None:
    got = sorted(name for name, _body in _wearers(_clean()))
    assert got == WEARERS, (
        f"the NEW mark is placed on {got}, expected exactly one on each of {WEARERS}. "
        "A surface that shows a release date without it, or a second copy of it, "
        "reads differently from its neighbours."
    )


def test_both_card_styles_caption_through_the_one_that_wears_it() -> None:
    clean = _clean()
    for card in ("ArtCard", "BrowseCard"):
        spans = _spans(clean, rf"\bcomponent\s+{card}\s*:\s*[\w.]+\s*\{{")
        assert len(spans) == 1, f"component {card} not found"
        s, e = spans[0]
        assert re.search(
            r"\bCardCaption\s*\{", clean[s:e]
        ), f"{card} no longer captions through CardCaption, so it no longer wears the NEW mark"


def test_no_surface_decides_newness_for_itself() -> None:
    """One rule: every wearer asks isNewRelease, and the cutoff it compares
    against is read nowhere else, so no surface can keep its own window."""
    clean = _clean()
    allowed = _spans(clean, r"\bfunction\s+(?:isNewRelease|refreshNewCutoff)\s*\([^)]*\)\s*\{")
    allowed += [(m.start(), m.end()) for m in re.finditer(r"\bproperty\s+string\s+new(?:Cutoff|Horizon)\b", clean)]
    assert len(allowed) == 4, allowed
    strays = [
        clean.count("\n", 0, m.start()) + 1
        for m in re.finditer(r"\bnew(?:Cutoff|Horizon)\b", clean)
        if not any(s <= m.start() <= e for s, e in allowed)
    ]
    assert not strays, f"the NEW cutoff is read outside isNewRelease at Main.qml lines {strays}"

    caption = _spans(clean, r"\bcomponent\s+CardCaption\s*:\s*[\w.]+\s*\{")[0]
    for name, body in _wearers(clean):
        rule = clean[caption[0] : caption[1]] if name == "CardCaption" else body
        assert "isNewRelease(" in rule, f"the {name} mark does not ask isNewRelease"


def test_the_dot_breathes_on_the_render_thread_and_only_when_seen() -> None:
    """Every marked row and card carries a pulsing dot, so the pulse must not
    tick on the GUI thread (this app's frames wait on it) and must stop when
    nothing can see it."""
    clean = _clean()
    spans = _spans(clean, r"\bcomponent\s+NewTag\s*:\s*[\w.]+\s*\{")
    assert len(spans) == 1
    body = clean[spans[0][0] : spans[0][1]]
    assert "OpacityAnimator" in body, "the NEW dot no longer breathes on the render thread"
    for gui in ("NumberAnimation", "PropertyAnimation", "ColorAnimation"):
        assert gui not in body, f"the NEW dot animates with {gui}, which ticks on the GUI thread"
    assert re.search(
        r"breathing\s*:\s*visible\s*&&\s*root\.onScreen\b", body
    ), "the NEW dot's breath is no longer gated on being visible and on screen"


def test_the_window_is_a_fortnight() -> None:
    assert re.search(r"readonly\s+property\s+int\s+newDays\s*:\s*14\b", _clean())


def test_the_payload_builders_never_read_the_clock() -> None:
    """The verdict is never stored: nothing that builds or dresses a payload
    may ask what day it is. A payload outlives the day it was built on."""
    from waves.waves_ui import backend

    for name in ("_album_dict", "_track_dict", "_browse_card", "_dress_card", "_build_browse_item"):
        src = inspect.getsource(getattr(backend.WavesBridge, name))
        for clock in ("date.today(", "datetime.now(", "time.time(", "utcnow("):
            assert clock not in src, f"{name} reads the clock ({clock}), so its answer goes stale in the cache"


def test_an_album_page_header_carries_the_date_its_rows_show(monkeypatch) -> None:
    from test_browse_item_prefetch import _bridge, _Cover, _fake_track, _page

    import waves.waves_ui.backend as backend

    for fn, stub in (("clock", lambda: 0.0), ("done", lambda *a, **k: None), ("event", lambda *a, **k: None)):
        monkeypatch.setattr(backend.devlog, fn, stub, raising=True)

    plain = _Cover("album", 51, [])
    plain.release_date = plain.tidal_release_date = datetime(2026, 9, 4)
    plain.version, plain.copyright = None, "2026 Label"
    plain._tracks = [_fake_track(1, plain)]
    b = _bridge(plain, "album")
    b.openBrowseItem("album", "51")
    assert _page(b)["header"]["date"] == "2026-09-04"

    # A reissue shows the day it was really listed, like its album row does,
    # and the mark must agree with the date beside it. The year stays TIDAL's.
    reissue = _Cover("album", 52, [])
    reissue.release_date, reissue.tidal_release_date = datetime(2016, 8, 19), datetime(2026, 9, 11)
    reissue.version, reissue.copyright = "Anniversary Edition", "Nuclear Blast"
    reissue._tracks = [_fake_track(2, reissue)]
    b = _bridge(reissue, "album")
    b.openBrowseItem("album", "52")
    header = _page(b)["header"]
    assert header["date"] == "2026-09-11"
    assert header["year"] == "2016"

    pl = _Cover("playlist", "p9", [_fake_track(3, plain)])
    b = _bridge(pl, "playlist")
    b.openBrowseItem("playlist", "p9")
    assert _page(b)["header"]["date"] == ""


# ----- the live UI ----------------------------------------------------------------


def test_the_mark_follows_the_date_on_every_surface_and_the_clock() -> None:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-new-release-mark-")
    env["XDG_CONFIG_HOME"] = sandbox
    env["HOME"] = sandbox
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-16:])
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"the NEW mark is wrong on screen:\n{tail}"


# Every visible NEW mark, named by what wears it. JSON, because a QML object
# comes back from QQmlExpression as an opaque QJSValue.
_TAGS = """
JSON.stringify((function() {
    var out = []
    function owner(it) {
        for (var p = it.parent; p; p = p.parent) {
            if (p.listedDate !== undefined && p.albumId !== undefined) return "album:" + p.title
            if (p.tId !== undefined && p.durationSec !== undefined) return "track:" + p.title
            if (p.fresh !== undefined && p.card !== undefined) return "card:" + (p.card.title || "")
            if (p.hd !== undefined && p.keyKind !== undefined) return "header:" + (p.hd ? p.hd.title : "")
        }
        return "?"
    }
    function walk(it) {
        if (!it) return
        if (it.objectName === "newTag" && it.visible && it.width > 0) out.push(owner(it))
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) walk(kids[i])
    }
    walk(root.contentItem)
    return out.sort()
})())
"""

# Whether each visible NEW dot is breathing.
_PULSE = """
JSON.stringify((function() {
    var out = []
    function walk(it) {
        if (!it) return
        if (it.objectName === "newTag" && it.visible && it.width > 0) out.push(!!it.pulsing)
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) walk(kids[i])
    }
    walk(root.contentItem)
    return out
})())
"""

# Each visible NEW mark as [owner, breathing], owners named as in _TAGS.
_PULSE_BY_OWNER = _TAGS.replace("out.push(owner(it))", "out.push([owner(it), !!it.pulsing])")

# How far (px) the album page header's NEW capitals sit from the title's.
_HEADER_CAP_GAP = """
(function() {
    var found = null
    function walk(it) {
        if (!it || found) return
        if (it.objectName === "newTag" && it.visible && it.width > 0) {
            for (var p = it.parent; p; p = p.parent)
                if (p.hd !== undefined && p.keyKind !== undefined) { found = it; return }
        }
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) walk(kids[i])
    }
    walk(root.contentItem)
    if (!found) return -1
    var title = null
    var sibs = found.parent.children
    for (var i = 0; i < sibs.length; i++)
        if (sibs[i].font !== undefined && sibs[i].font.pixelSize === 26) title = sibs[i]
    if (!title) return -2
    var fm = Qt.createQmlObject("import QtQuick; FontMetrics {}", root)
    fm.font = title.font
    var titleMid = title.mapToItem(null, 0, title.baselineOffset).y - fm.tightBoundingRect("H").height / 2
    var newMid = found.mapToItem(null, 0, found.capMiddle).y
    fm.destroy()
    return Math.abs(titleMid - newMid)
})()
"""

# Visible rows and captions, so a scenario that built nothing reads as a
# precondition rather than as "no marks, as wanted".
_ROWS = """
JSON.stringify((function() {
    var out = []
    function walk(it) {
        if (!it) return
        if (it.visible) {
            if (it.listedDate !== undefined && it.albumId !== undefined) out.push({ kind: "album", title: "" + it.title })
            else if (it.tId !== undefined && it.durationSec !== undefined) out.push({ kind: "track", title: "" + it.title })
            else if (it.fresh !== undefined && it.card !== undefined)
                out.push({ kind: "card", title: "" + (it.card.title || ""), h: it.implicitHeight })
        }
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) walk(kids[i])
    }
    walk(root.contentItem)
    return out
})())
"""


def _run_scenario() -> int:
    here = Path(__file__).resolve()
    sys.path.insert(0, str(here.parent.parent))
    sys.path.insert(0, str(here.parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
    except Exception as exc:  # pragma: no cover - environment guard
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from _qml_offline import PARK_LOGIN_QML, patch_offline

        patch_offline()
        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:  # pragma: no cover - environment guard
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
    root.setProperty("width", 1280)
    root.setProperty("height", 900)

    def q(expr: str):
        e = QQmlExpression(QQmlEngine.contextForObject(root), root, expr)
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

    def settle(ms: int = 200) -> None:
        pump(lambda: False, ms)

    def tags() -> list[str]:
        return json.loads(q(_TAGS))

    def rows() -> list[dict]:
        return json.loads(q(_ROWS))

    today = date.today()

    def ago(n: int) -> str:
        return (today - timedelta(days=n)).isoformat()

    failures: list[str] = []
    settle()

    # 1. Cards, in both styles. A reissue card shows its listed day, and is
    # marked by that day, not by TIDAL's original.
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q("browseBuilding = false")
    q("_browseAsyncBuild = false")

    def card(cid: str, kind: str, title: str, when: str, listed: str = "") -> dict:
        return {
            "id": cid,
            "kind": kind,
            "title": title,
            "artist": "Some Artist",
            "year": when[:4],
            "date": when,
            "listed": listed,
            "tracks": 10,
        }

    cards = [
        card("c1", "album", "Fresh Card Album", ago(2)),
        card("c2", "album", "Old Card Album", ago(60)),
        card("c3", "album", "Reissue Card", "2016-08-19", listed=ago(3)),
        card("c4", "track", "Fresh Card Track", ago(1)),
        card("c5", "track", "Old Card Track", ago(90)),
    ]
    root.setProperty("browseSections", [{"title": "SHELF", "rowKind": "cards", "data": "", "items": cards}])
    want = ["card:Fresh Card Album", "card:Fresh Card Track", "card:Reissue Card"]
    for style in ("art", "console"):
        q(f'browseStyle = "{style}"')
        settle(900)
        captions = {r["title"]: r["h"] for r in rows() if r["kind"] == "card"}
        if set(captions) != {c["title"] for c in cards}:
            print(f"{style}: the shelf did not build every card: {sorted(captions)}", file=sys.stderr)
            return _EXIT_PRECONDITION
        if tags() != want:
            failures.append(f"{style} cards: marked {tags()}, wanted {want}")
        if captions["Fresh Card Album"] != captions["Old Card Album"]:
            failures.append(
                f"{style} cards: the mark changed the caption line's height "
                f"({captions['Fresh Card Album']} vs {captions['Old Card Album']}), shifting the card under it"
            )

    # 2. Search rows, the fortnight's two edges, a pre-release, and the clock.
    def album(aid: str, title: str, when: str) -> dict:
        return {
            "id": aid, "title": title, "artist": "Some Artist", "artist_id": "ar0", "art": "",
            "year": when[:4], "date": when, "listed": "", "tracks": 1, "duration_sec": 200,
            "quality": "LOSSLESS", "popularity": 50, "explicit": False, "added": "",
        }  # fmt: skip

    def track(tid: str, title: str, when: str, album_id: str = "alx") -> dict:
        return {
            "id": tid, "kind": "track", "title": title, "artist": "Some Artist", "artist_id": "ar0",
            "album": "Some Album", "album_id": album_id, "num": 1, "vol": 1, "art": "",
            "year": when[:4], "date": when, "duration": "3:20", "duration_sec": 200,
            "quality": "LOSSLESS", "popularity": 50, "explicit": False, "added": "",
        }  # fmt: skip

    results = {
        "artists": [],
        "albums": [
            album("al1", "Edge In", ago(14)),
            album("al2", "Edge Out", ago(15)),
            album("al3", "Fresh Album", ago(2)),
            album("al4", "Pre Release", (today + timedelta(days=30)).isoformat()),
        ],
        "tracks": [track("t1", "Fresh Track", ago(5)), track("t2", "Old Track", ago(400))],
        "videos": [],
        "playlists": [],
        "mixes": [],
        "top": None,
    }
    q(PARK_LOGIN_QML)
    q("openSearch()")
    settle()
    q("_searchSeq = _navSeq")
    bridge.searchResults.emit(results)
    if not pump(lambda: not q("searchBuilding")):
        print("search never finished building", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle(500)
    shown = {f"{r['kind']}:{r['title']}" for r in rows()}
    needed = {
        "album:Edge In",
        "album:Edge Out",
        "album:Fresh Album",
        "album:Pre Release",
        "track:Fresh Track",
        "track:Old Track",
    }
    if not needed <= shown:
        print(f"search did not show every seeded row: missing {sorted(needed - shown)}", file=sys.stderr)
        return _EXIT_PRECONDITION
    want = ["album:Edge In", "album:Fresh Album", "track:Fresh Track"]
    if tags() != want:
        failures.append(f"search: marked {tags()}, wanted {want}")

    # The day rolls on with the page parked and nothing re-sent: the marks go.
    try:
        q(f'newCutoff = "{ago(1)}"')
        settle(100)
        if tags():
            failures.append(f"marks outlived their window with no republish: {tags()}")
        q("refreshNewCutoff()")
        settle(100)
    except RuntimeError as exc:
        print(f"the NEW cutoff clock is gone: {exc}", file=sys.stderr)
        return _EXIT_REGRESSED
    if tags() != want:
        failures.append(f"the clock did not restore today's marks: {tags()}, wanted {want}")

    # The dot breathes only while the window is on screen. The window is shown
    # from load (Main.qml declares it visible), so it is hidden first, then
    # shown, then hidden again, and finally shown so the pages below run in the
    # state they always have.
    def pulses() -> list[bool]:
        return json.loads(q(_PULSE))

    if not pulses():
        print("no NEW dots on screen to watch breathe", file=sys.stderr)
        return _EXIT_PRECONDITION
    root.hide()
    # Longer than one beat: a dot waits up to a whole breath before it starts,
    # so asserting sooner would pass whether or not the gate held.
    settle(int(q("newPulseMs")) + 400)
    if any(pulses()):
        failures.append(f"NEW dots breathe in a hidden window: {pulses()}")
    root.show()
    if not pump(lambda: all(pulses()), 5000):
        failures.append(f"NEW dots did not breathe once the window was shown: {pulses()}")
    root.hide()
    settle(150)
    if any(pulses()):
        failures.append(f"NEW dots kept breathing after the window hid: {pulses()}")
    root.show()
    settle(150)

    # 3. The album's own page: the header and every row are marked. Then a
    # playlist page, where a fresh track stands on its own and is marked.
    # Signing in revalidates the Browse landing. Served from here, never the
    # network, and shaped like the shelf already on screen.
    landing = {
        "sections": [{"title": "SHELF", "rowKind": "cards", "items": cards}],
        "genres": [],
        "moods": [],
        "decades": [],
        "error": False,
    }
    bridge._browse_root = lambda: dict(landing)
    bridge._logged_in = True
    bridge.loggedInChanged.emit()
    settle(100)

    def build(kind: str, media_id: str, *_args, **_kwargs) -> dict:
        fresh = ago(4)
        if kind == "album":
            items = [track(f"pt{n}", f"Page Track {n}", fresh, album_id=media_id) for n in range(3)]
        else:
            items = [track("lt0", "List Fresh", ago(6)), track("lt1", "List Old", ago(200))]
        return {
            "key": f"item:{kind}:{media_id}",
            "title": "Fresh Page",
            "header": {
                "kind": kind, "id": media_id, "title": "Fresh Page", "subtitle": "", "desc": "",
                "stats": "3 tracks", "artist_id": "", "artist": "Some Artist", "year": fresh[:4],
                "date": fresh if kind == "album" else "", "num_tracks": len(items),
                "duration_sec": 600, "quality": "LOSSLESS", "art": "",
            },  # fmt: skip
            "sections": [{"rowKind": "tracks", "title": "Tracks", "items": items}],
            "error": False,
        }

    bridge._build_browse_item = build

    def kind_of(key: str) -> str:
        return key.split(":")[1]

    for opener, key, want, row_titles in (
        (
            'root.openAlbumPage("alP", "", "Fresh Page", "")',
            "item:album:alP",
            ["header:Fresh Page", "track:Page Track 0", "track:Page Track 1", "track:Page Track 2"],
            {"Page Track 0"},
        ),
        ('root.openPlaylistPage("plP", "Fresh Page", "")', "item:playlist:plP", ["track:List Fresh"], {"List Fresh"}),
    ):
        q(opener)
        if not pump(lambda key=key: q("browsePageKey") == key and q("browsePageLoading") is False, 5000):
            print(f"{key} never opened (key={q('browsePageKey')!r})", file=sys.stderr)
            return _EXIT_PRECONDITION
        settle(700)
        if not row_titles <= {r["title"] for r in rows() if r["kind"] == "track"}:
            print(f"{key} built no track rows: {rows()}", file=sys.stderr)
            return _EXIT_PRECONDITION
        if tags() != want:
            failures.append(f"{key}: marked {tags()}, wanted {want}")
        if kind_of(key) != "album":
            continue

        # The header's capitals sit on the title's, not its box centre.
        gap = float(q(_HEADER_CAP_GAP))
        if gap < 0 or gap > 1.0:
            failures.append(f"the album header's NEW sits {gap}px off the title's capitals (negative: not found)")

        # One track lands: its mark stays, steady; its neighbours keep breathing.
        def breaths() -> dict:
            return dict(json.loads(q(_PULSE_BY_OWNER)))

        if not pump(lambda: all(breaths().values()), int(q("newPulseMs")) + 2000):
            failures.append(f"the album page's marks never all breathed: {breaths()}")
        bridge.downloadState.emit("pt1", "done")
        if not pump(lambda: breaths().get("track:Page Track 1") is False, 3000):
            failures.append(f"a downloaded track's NEW kept breathing: {breaths()}")
        got = breaths()
        if "track:Page Track 1" not in got:
            failures.append(f"a downloaded track lost its NEW mark: {got}")
        if not (got.get("track:Page Track 0") and got.get("track:Page Track 2")):
            failures.append(f"marks on tracks still to download stopped breathing: {got}")

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return _EXIT_REGRESSED
    print("ok")
    return _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
