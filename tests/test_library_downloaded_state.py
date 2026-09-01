"""An album the library scan says you fully own renders as DOWNLOADED, and
that claim can always be clicked through.

WHAT THIS FENCES OFF
--------------------
The Download button itself is the prevention: DownloadButton resolves the
album's library presence (its lib* properties) and a FULL local copy renders
the same inert DOWNLOADED state the ownership rollup uses, so an album you
already have cannot be re-downloaded by accident. A PARTIAL copy and an
unmatched album must keep a LIVE Download button (completing an album is not
a duplicate, and no match means nothing to prevent). Display is the only
consumer of the presence answer: the engine never sees it, and per-track
buttons stay independent, so a wrong tag match is always recoverable.

The claim is inferred from tags, so it can be wrong, and a wrong one must not
be terminal. So the DOWNLOADED-by-library-scan button stays CLICKABLE and
opens libraryClaimGate, which names the matched folder and offers DOWNLOAD
ANYWAY. Two halves of that are pinned here, and both matter:

* the claim is clickable and its DOWNLOAD ANYWAY really reaches
  waves.downloadAlbum with the right album id (detection must never prevent a
  download the user could otherwise make), and
* a DOWNLOADED that comes from the OWNERSHIP STORE is not a guess but a
  record of a download Waves made, so it stays inert and opens nothing.

Also pinned here: the verdict's two axes as the user reads them. Identity
("sure") picks the words and colour: a proven complete copy wears the green
fact wording with its media noun (ALBUM DOWNLOADED / ALBUM IN LIBRARY), an
unproven one hedges in gold MAYBE IN LIBRARY, and the badge's "?" follows the
same axis. Coverage ("full") picks the shape: a short copy stays a live cyan
PARTIALLY IN LIBRARY button. And the queued face stays frozen through
"running" (the state roll's exit), so the progress handoff never flashes the
idle label.

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


def test_owned_album_button_reads_downloaded_partial_stays_live():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-lib-dl-state-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-10:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, (
        "the library-owned DOWNLOADED button state regressed: a fully-owned album's button is "
        "live again (duplicates by accident), or a partial/unmatched album's button is wrongly "
        "inert (blocked download), or the library claim lost its click-through (a wrong tag "
        "match becomes a download the user cannot start), or an ownership-store DOWNLOADED "
        f"became clickable. Scenario exit={proc.returncode}:\n{tail}"
    )


_OWNED = json.dumps(
    {
        "id": "al-owned",
        "title": "Owned Album",
        "artist": "Artist A",
        "artist_id": "a1",
        "art": "",
        "year": "2020",
        "date": "2020-01-01",
        "tracks": 12,
        "quality": "LOSSLESS",
        "popularity": 50,
    }
)
_PARTIAL = json.dumps(
    {
        "id": "al-partial",
        "title": "Partial Album",
        "artist": "Artist B",
        "artist_id": "b1",
        "art": "",
        "year": "2021",
        "date": "2021-01-01",
        "tracks": 13,
        "quality": "LOSSLESS",
        "popularity": 50,
    }
)
# Exactly one track short: the sharpest partial there is. The backend keeps the
# button live for it (no slack in the completeness bar), and the PILL must say
# the same thing: an old "total - 1" in LibraryTag.partial dressed a 9-of-10
# copy in an unqualified IN LIBRARY beside that live DOWNLOAD button.
_ONE_SHORT = json.dumps(
    {
        "id": "al-oneshort",
        "title": "One Short Album",
        "artist": "Artist C",
        "artist_id": "c1",
        "art": "",
        "year": "2022",
        "date": "2022-01-01",
        "tracks": 10,
        "quality": "LOSSLESS",
        "popularity": 50,
    }
)


# A complete copy whose folder carries no year tag: coverage is proven (10 of
# 10) but identity is not, the exact split the sure/full axes exist for. The
# button must say MAYBE IN LIBRARY in gold, and the pill must keep its "?".
_UNDATED = json.dumps(
    {
        "id": "al-undated",
        "title": "Undated Album",
        "artist": "Artist E",
        "artist_id": "e1",
        "art": "",
        "year": "2020",
        "date": "2020-01-01",
        "tracks": 10,
        "quality": "LOSSLESS",
        "popularity": 50,
    }
)


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
        from waves.matching import presence_key
        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from PySide6.QtCore import Slot

    # DOWNLOAD ANYWAY has to actually reach the engine, so the slot is
    # overridden (a subclass, not an attribute: QML dispatches through the
    # meta-object) and every call recorded. Overriding also keeps the real
    # download path, which needs a live TIDAL session, out of the scenario.
    downloaded: list[str] = []

    class SpyBridge(WavesBridge):
        @Slot(str)
        def downloadAlbum(self, album_id: str) -> None:
            downloaded.append(str(album_id))

    engine = QQmlApplicationEngine()
    bridge = SpyBridge(tidal=None)
    # One full local copy, one partial (3 of 13 tracks). Seeded before the
    # rows build so the buttons resolve against it on creation.
    bridge._library_index = {
        presence_key("Owned Album", "Artist A"): [
            {
                # The raw title rides along: the completeness gate compares it
                # rather than the edition-stripped key it is filed under.
                "title": "Owned Album",
                "year": "2020",
                "tracks": 12,
                "id": "/lib/a/owned",
                "codec": "flac",
                "bitrate": 0,
                "bits": 16,
                "rate": 44100,
            }
        ],
        presence_key("Partial Album", "Artist B"): [
            {
                "title": "Partial Album",
                "year": "2021",
                "tracks": 3,
                "id": "/lib/b/partial",
                "codec": "mp3",
                "bitrate": 320,
                "bits": 0,
                "rate": 0,
            }
        ],
        presence_key("One Short Album", "Artist C"): [
            {
                "title": "One Short Album",
                "year": "2022",
                "tracks": 9,
                "id": "/lib/c/oneshort",
                "codec": "flac",
                "bitrate": 0,
                "bits": 16,
                "rate": 44100,
            }
        ],
        presence_key("Undated Album", "Artist E"): [
            {
                "title": "Undated Album",
                "year": "",
                "tracks": 10,
                "id": "/lib/e/undated",
                "codec": "flac",
                "bitrate": 0,
                "bits": 16,
                "rate": 44100,
            }
        ],
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

    def settle(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    settle(200)
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q(PARK_LOGIN_QML)
    q("root.openSearch()")
    q("albumsModel.clear()")
    q(f"albumsModel.append({_OWNED})")
    q(f"albumsModel.append({_PARTIAL})")
    q(f"albumsModel.append({_ONE_SHORT})")
    q(f"albumsModel.append({_UNDATED})")
    q("root.searchReveal = 1")
    q("root.searchBuilding = false")
    q("root.searchAlbumsExpanded = true")
    settle(700)

    # Walk the results tree for each AlbumBlock's row DownloadButton (the one
    # item under that block carrying its libTitle). _FIND leaves the button in
    # a JS variable so a caller can read any property off it, or call it.
    def _find(title: str) -> str:
        return (
            " function walk(it){"
            "  if (!it) return null;"
            f" if (it.libTitle === {title!r} && it.st !== undefined) return it;"
            "  for (var i = 0; i < it.children.length; i++) {"
            "   var hit = walk(it.children[i].item || it.children[i]);"
            "   if (hit) return hit;"
            "  }"
            "  return null;"
            " }"
            f" var b = walk(contentCol);"
        )

    def button(title: str, read: str) -> str:
        return str(q("(function(){" + _find(title) + f" return b ? ({read}) : 'missing';" + "})()"))

    def tap(title: str) -> None:
        """Fire the button's REAL tap area, not the function behind it: the
        thing that regresses is the wiring, and calling openLibraryClaim()
        directly would keep passing with the MouseArea branch deleted."""
        q(
            "(function(){" + _find(title) + " function ma(it){"
            "  if (!it) return null;"
            "  if (it.objectName === 'dbTapArea') return it;"
            "  for (var i = 0; i < it.children.length; i++) {"
            "   var hit = ma(it.children[i]);"
            "   if (hit) return hit;"
            "  }"
            "  return null;"
            " }"
            " var a = b ? ma(b) : null;"
            " if (a) a.clicked(null);"
            "})()"
        )

    def button_state(title: str) -> str:
        return button(title, "b.st === '' ? 'live' : b.st")

    states = {}
    for _ in range(40):  # async delegates: poll until all buttons exist
        states = {
            "owned": button_state("Owned Album"),
            "partial": button_state("Partial Album"),
            "oneshort": button_state("One Short Album"),
            "undated": button_state("Undated Album"),
        }
        if "missing" not in states.values():
            break
        settle(100)

    verdicts = {
        "owned_reads_downloaded": states.get("owned") == "done",
        "partial_stays_live": states.get("partial") == "live",
        "one_short_stays_live": states.get("oneshort") == "live",
        # A complete copy is done-shaped even when identity is unproven: the
        # hedge is carried by the words and colour, never by a live button
        # that would re-download an album the user probably has.
        "undated_reads_done": states.get("undated") == "done",
    }

    def face_text(title: str) -> str:
        """The words actually rendered on the button face (dbFaceText), so the
        label mapping is pinned at the pixel contract, not the properties
        behind it."""
        return str(
            q(
                "(function(){" + _find(title) + " function ft(it){"
                "  if (!it) return null;"
                "  if (it.objectName === 'dbFaceText') return it;"
                "  for (var i = 0; i < it.children.length; i++) {"
                "   var hit = ft(it.children[i].item || it.children[i]);"
                "   if (hit) return hit;"
                "  }"
                "  return null;"
                " }"
                " var t = b ? ft(b) : null;"
                " return t ? t.text : 'missing';"
                "})()"
            )
        )

    if "missing" not in states.values():
        # The two-axis split, as the user reads it. Identity proven (sure):
        # the green fact wording with its media noun. Identity unproven: gold
        # MAYBE, whatever the coverage. Coverage short: cyan PARTIALLY on a
        # still-live button.
        verdicts["sure_claim_wears_the_fact_words"] = (
            button("Owned Album", "'' + b.libGuess") == "false"
            and button("Owned Album", "'' + b.libSure") == "true"
            and face_text("Owned Album") == "ALBUM DOWNLOADED"
        )
        verdicts["unproven_claim_says_maybe"] = (
            button("Undated Album", "'' + b.libGuess") == "true"
            and button("Undated Album", "'' + b.libSure") == "false"
            and face_text("Undated Album") == "MAYBE IN LIBRARY"
        )
        verdicts["short_copy_says_partially"] = (
            button("Partial Album", "'' + b.libPartialClaim") == "true"
            and face_text("Partial Album") == "PARTIALLY IN LIBRARY"
        )
        # Both claims stay openable: MAYBE obviously, but the proven green one
        # too, because sure is still an inference from tags, not a record.
        verdicts["unproven_claim_is_clickable"] = button("Undated Album", "'' + b.libClaim") == "true"

    def pill_by_folder(folder: str, read: str) -> str:
        # A LibraryTag found by its matched folder; `proven` narrows the walk
        # to the pills (other items carry an albumId of their own).
        return str(
            q(
                "(function(){ function walk(it){ if (!it) return null;"
                f" if (it.albumId === {folder!r} && it.proven !== undefined) return it;"
                " for (var i = 0; i < it.children.length; i++) {"
                "  var hit = walk(it.children[i].item || it.children[i]);"
                "  if (hit) return hit; }"
                " return null; } var p = walk(contentCol);"
                f" return p ? ({read}) : 'missing'; }})()"
            )
        )

    # The badge's "?" is the identity axis alone: a proven match drops it, an
    # unproven one keeps it, and coverage never touches it.
    verdicts["proven_pill_drops_the_question_mark"] = pill_by_folder("/lib/a/owned", "'' + p.proven") == "true"
    verdicts["unproven_pill_keeps_it"] = pill_by_folder("/lib/e/undated", "'' + p.proven") == "false"

    def pill(read: str) -> str:
        # The 9-of-10 row's LibraryTag, found by its have/total pair.
        return str(
            q(
                "(function(){ function walk(it){ if (!it) return null;"
                " if (it.have === 9 && it.total === 10) return it;"
                " for (var i = 0; i < it.children.length; i++) {"
                "  var hit = walk(it.children[i].item || it.children[i]);"
                "  if (hit) return hit; }"
                " return null; } var p = walk(contentCol);"
                f" return p ? ({read}) : 'missing'; }})()"
            )
        )

    # The pill must agree with the live button: one track short is a partial
    # copy and says N OF M, never a bare IN LIBRARY.
    verdicts["one_short_pill_spells_it_out"] = pill("'' + p.partial") == "true"

    if "missing" not in states.values():
        # The claim is a guess, so it must be openable, and it must carry the
        # matched folder: that path is the evidence the user judges the match
        # by, and the target of SHOW IN FOLDER.
        verdicts["claim_is_clickable"] = button("Owned Album", "'' + b.libClaim") == "true"
        verdicts["claim_knows_its_folder"] = button("Owned Album", "b.libPath") == "/lib/a/owned"

        tap("Owned Album")
        settle(50)
        verdicts["claim_opens_the_gate"] = bool(q("libraryClaimGate.shown"))
        verdicts["gate_names_the_album"] = str(q("libraryClaimGate.albumTitle")) == "Owned Album"
        verdicts["gate_shows_the_folder"] = str(q("libraryClaimGate.folder")) == "/lib/a/owned"

        # The whole point: DOWNLOAD ANYWAY starts the download it was told the
        # user already had. Detection may never be the end of the road.
        q("libraryClaimGate.proceed()")
        settle(50)
        verdicts["download_anyway_downloads"] = downloaded == ["al-owned"]
        verdicts["proceeding_closes_the_gate"] = not q("libraryClaimGate.shown")

        # An ownership-store DOWNLOADED is a RECORD, not a guess: it stays
        # inert and opens nothing, so the click-through cannot creep into it.
        q("(function(){" + _find("Owned Album") + " b.owned = true; })()")
        settle(50)
        tap("Owned Album")
        settle(50)
        verdicts["ownership_done_stays_inert"] = (
            button("Owned Album", "'' + b.libClaim") == "false"
            and button_state("Owned Album") == "done"
            and not q("libraryClaimGate.shown")
            and downloaded == ["al-owned"]  # the tap started nothing new
        )

    # The queued face stays FROZEN through "running": the row is riding the
    # state roll's exit belt under the arriving progress matrix, and a fall
    # through to the idle label mid-exit is the flash this fences off (it
    # would also put the wrong words on screen).
    if "missing" not in states.values():
        bridge.downloadState.emit("al-partial", "queued")
        settle(80)
        queued_face = face_text("Partial Album")
        bridge.downloadState.emit("al-partial", "running")
        settle(500)  # past the 340ms roll: the frozen face must still hold
        verdicts["running_keeps_queued_face_frozen"] = (
            queued_face == "QUEUED ALBUM" and face_text("Partial Album") == "QUEUED ALBUM"
        )
        bridge.downloadState.emit("al-partial", "")

    print(f"states={states} downloaded={downloaded} verdicts={verdicts}", flush=True)
    return _EXIT_OK if all(verdicts.values()) else _EXIT_REGRESSED


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
