"""Every My Tidal tab shows DOWNLOAD ALL and confirms the count (issue #43).

WHAT THIS FENCES OFF
--------------------
The Tracks tab's header carries a DOWNLOAD ALL button (``favTracksBtn``).
Four things must hold on the real Main.qml:

1. The button is on screen on the Tracks tab and on no other tab.
2. A tap arms ``favTracksPending``; when the backend answers with a count,
   the shared bulk confirm (``catDlGate``) opens titled for tracks.
3. Dismissing the confirm closes it and leaves nothing armed.
4. A count that arrives with nothing armed (a stale answer) opens nothing.
5. Albums, Artists, Playlists, Mixes and Videos carry their own button, on
   their own tab only, and
   each count opens the confirm titled for its kind.

The bridge is built offline (``tidal=None``, cached login parked) and the
count is injected by emitting ``favoriteTracksResolved`` from Python, so no
network is involved. Runs in a SUBPROCESS like the other Main.qml scenarios:
building the bridge installs process-global handlers that must not leak into
the suite.
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


def test_my_tidal_tracks_download_all_confirms_the_count():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-fav-dl-test-")
    env["HOME"] = env["XDG_CONFIG_HOME"]  # nothing in the scenario may reach the real home
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
    assert proc.returncode == _EXIT_OK, f"My Tidal DOWNLOAD ALL regressed. Scenario exit={proc.returncode}:\n{tail}"


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
        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    engine = QQmlApplicationEngine()
    bridge = WavesBridge(tidal=None)
    # The real tap asks the bridge for the count; count the asks instead of
    # letting the parked login swallow them silently.
    resolves = {"n": 0}
    bridge.resolveFavoriteTracks = lambda: resolves.__setitem__("n", resolves["n"] + 1)
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
    # Stand on My Tidal > Tracks without asking the (offline) backend for rows.
    q('libraryOpen = true; libraryCategory = "tracks"')
    settle(60)
    on_tracks = bool(q("favTracksBtn.visible")) and bool(q("favTracksBtn.parent.visible"))
    q('libraryCategory = "albums"')
    settle(60)
    off_albums = not bool(q("favTracksBtn.parent.visible"))
    q('libraryCategory = "tracks"')
    settle(60)

    # 2. The real tap arms the flag and asks the bridge for the count; then
    #    the count lands: the confirm opens, titled for tracks.
    q("favTracksBtn.onTap()")
    settle(30)
    tapped = bool(q("favTracksPending")) and resolves["n"] == 1
    bridge.favoriteTracksResolved.emit(5)
    settle(60)
    opened = bool(q("catDlGate.visible")) and q("catDlPrompt.kind") == "favTracks" and q("catDlPrompt.count") == 5
    disarmed = not bool(q("favTracksPending")) and tapped

    # 3. Dismiss closes it.
    q("catDlDismiss()")
    settle(30)
    closed = not bool(q("catDlGate.visible")) and q("catDlPrompt") is None

    # 4. A count with nothing armed opens nothing.
    bridge.favoriteTracksResolved.emit(9)
    settle(60)
    stale_inert = not bool(q("catDlGate.visible"))

    # 4b. A failed count (-1) while armed disarms and opens nothing.
    q("favTracksPending = true")
    bridge.favoriteTracksResolved.emit(-1)
    settle(60)
    failed_inert = not bool(q("catDlGate.visible")) and not bool(q("favTracksPending"))

    # 5. Albums and Artists carry their own DOWNLOAD ALL, each only on its
    #    own tab, and each count opens the confirm titled for its kind.
    twins = True
    for cat, btn, flag, signal, kind in (
        ("albums", "favAlbumsBtn", "favAlbumsPending", bridge.favoriteAlbumsResolved, "favAlbums"),
        ("artists", "favArtistsBtn", "favArtistsPending", bridge.favoriteArtistsResolved, "favArtists"),
        ("playlists", "favPlaylistsBtn", "favPlaylistsPending", bridge.favoritePlaylistsResolved, "favPlaylists"),
        ("mixes", "favMixesBtn", "favMixesPending", bridge.favoriteMixesResolved, "favMixes"),
        ("videos", "favVideosBtn", "favVideosPending", bridge.favoriteVideosResolved, "favVideos"),
    ):
        q(f'libraryCategory = "{cat}"')
        settle(60)
        shown = bool(q(f"{btn}.parent.visible")) and not bool(q("favTracksBtn.parent.visible"))
        q(f"{btn}.onTap()")
        settle(30)
        armed = bool(q(flag))
        signal.emit(4)
        settle(60)
        asked = bool(q("catDlGate.visible")) and q("catDlPrompt.kind") == kind and q("catDlPrompt.count") == 4
        q("catDlDismiss()")
        settle(30)
        q('libraryCategory = "tracks"')
        settle(60)
        hidden = not bool(q(f"{btn}.parent.visible"))
        print(f"{cat}: shown={shown} armed={armed} asked={asked} hidden={hidden}", flush=True)
        twins = twins and shown and armed and asked and hidden and not bool(q(flag))

    ok = on_tracks and off_albums and opened and disarmed and closed and stale_inert and failed_inert and twins
    print(
        f"on_tracks={on_tracks} off_albums={off_albums} opened={opened} disarmed={disarmed} "
        f"closed={closed} stale_inert={stale_inert} failed_inert={failed_inert} twins={twins}",
        flush=True,
    )
    return _EXIT_OK if ok else _EXIT_REGRESSED


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
