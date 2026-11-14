"""The close prompt (closeGate) and the run in background flow in Main.qml.

The window's close button offers to keep Waves running in the background;
a real quit never does.

WHAT THIS FENCES OFF
--------------------
1. onClosing offers the background BEFORE the download warning, and skips
   the offer for a real quit, the prompt's EXIT, the warning's EXIT ANYWAY,
   the "exit" setting and a desktop with no way back to a hidden window.
2. The prompt's buttons do what they say: RUN IN BACKGROUND hides the window,
   EXIT re-closes once, only the buttons keep "Don't ask again", and a click
   away keeps nothing.
3. The window is hidden, not closed, and its frame is saved first (a hidden
   window is never persisted).
4. The tray host loads everywhere except macOS, which must never import
   Qt.labs.platform (the Mac bundle ships without it).
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_QML = _ROOT / "waves" / "waves_ui" / "qml"


def _src() -> str:
    return (_QML / "Main.qml").read_text(encoding="utf-8")


def _closing(src: str) -> str:
    m = re.search(r"onClosing: function \(close\) \{(.*?)\n    \}", src, re.S)
    assert m
    return m.group(1)


def _gate(src: str) -> str:
    m = re.search(r"id: closeGate\b.*?\n    \}\n", src, re.S)
    assert m, "the close prompt must exist in Main.qml"
    return m.group(0)


def test_the_offer_comes_first_and_a_real_quit_skips_it():
    body = _closing(_src())
    leaving = re.search(r"var leaving = (.*?)\n        closeGate\.exitPicked = false", body, re.S)
    assert leaving, "onClosing must decide whether this close is leaving for real"
    for clause in (
        "waves.takeQuitRequest()",
        "closeGate.exitPicked",
        "exitGate.confirmed",
        "!waves.backgroundAvailable",
        'root.closeAction === "exit"',
    ):
        assert clause in leaving.group(1), f"the offer lost its {clause!r} way out"
    offer = body.index("if (!leaving) {")
    assert offer < body.index("root.activeQueueCount > 0"), "the offer is decided before the download warning"
    branch = body[offer : body.index("closeGate.open = false", offer)]
    assert "close.accepted = false" in branch
    assert 'if (root.closeAction === "background") root.goBackground()' in branch
    assert "closeGate.open = true" in branch
    # A minimized window's close brings the window up before it asks.
    assert branch.index("if (root.visibility === Window.Minimized) root.comeBack()") < branch.index(
        "closeGate.open = true"
    )
    # A quit from the background (or minimized) needs a window for the
    # download warning.
    assert "if (root.inBackground || root.visibility === Window.Minimized) root.comeBack()" in body


def test_buttons_do_what_they_say():
    body = _gate(_src())
    assert 'label: "RUN IN BACKGROUND"' in body
    assert 'label: closeGate.warnDl ? "EXIT ANYWAY" : "EXIT"' in body
    assert body.count("root.goBackground()") == 1
    assert body.count("root.close()") == 1
    assert "closeGate.exitPicked = true" in body
    assert body.count("waves.rememberCloseAction(") == 2
    assert 'if (closeSkip.checked) waves.rememberCloseAction("background")' in body
    assert 'if (closeSkip.checked) waves.rememberCloseAction("exit")' in body
    assert "onClicked: closeGate.open = false" in body
    assert "onOpenChanged: if (open) closeSkip.checked = false" in body


def test_the_prompt_carries_the_download_warning_so_it_never_asks_twice():
    body = _gate(_src())
    # The same condition the exit warning vetoes on, read live.
    assert "readonly property bool warnDl: running > 0 && !setupSettings.exitWarnMuted" in body
    assert "readonly property int running: root.activeQueueCount" in body
    # EXIT after the card said so counts as the warning's answer.
    assert "if (closeGate.warnDl) exitGate.confirmed = true" in body
    assert body.index("if (closeGate.warnDl) exitGate.confirmed = true") < body.index("root.close()")


def test_gate_sits_above_the_drawer_and_the_video():
    body = _gate(_src())
    assert "parent: Overlay.overlay" in body
    z = re.search(r"\bz: (\d+)", body)
    assert z and int(z.group(1)) > 999


def test_the_window_hides_and_comes_back():
    src = _src()
    go = src[src.index("    function goBackground() {") : src.index("    function comeBack() {")]
    assert go.index("_winPersist()") < go.index("root.hide()")
    assert "root.visibility =" not in go, "assigning visibility next to visible makes Qt warn"
    assert "waves.enterBackground()" in go
    back = src[src.index("    function comeBack() {") :]
    back = back[: back.index("\n    }\n")]
    for line in (
        "waves.leaveBackground()",
        "root._showAs(root._bgVisibility)",
        "root._showAs(root._shownVisibility)",
        "root.raise()",
        "root.requestActivate()",
    ):
        assert line in back
    show_as = src[src.index("    function _showAs(v) {") :]
    show_as = show_as[: show_as.index("\n    }\n")]
    for line in ("root.showMaximized()", "root.showFullScreen()", "root.showNormal()"):
        assert line in show_as
    assert "function onShowWindowRequested() { root.comeBack() }" in src
    assert "function onCloseActionChanged()" in src


def test_tray_host_never_loads_on_macos():
    src = _src()
    loader = re.search(r"Loader \{\s+id: trayLoader(.*?)\}", src, re.S)
    assert loader
    assert 'active: Qt.platform.os !== "osx"' in loader.group(1)
    assert 'source: "TrayHost.qml"' in loader.group(1)
    assert "Qt.labs.platform" not in src, "only TrayHost.qml may import the tray module"
    tray = (_QML / "TrayHost.qml").read_text(encoding="utf-8")
    assert "import Qt.labs.platform as Platform" in tray
    for call in ("waves.setTrayAvailable(available)", "waves.requestShowWindow()", "waves.requestQuit()"):
        assert call in tray


def test_packaged_builds_keep_the_tray_module_off_macos_only():
    trim = (_ROOT / "tools" / "trim_qt_bundle.sh").read_text(encoding="utf-8")
    assert "KEEP_TRAY=0" in trim and "KEEP_TRAY=1" in trim
    keep = trim[trim.index('if [ "$KEEP_TRAY" = 1 ]; then') :]
    assert "*LabsPlatform*) continue ;;" in keep
    assert "libQt6Widgets.so*|Qt6Widgets.dll) continue ;;" in keep
    # The Python binding still goes everywhere: the app stays a QGuiApplication.
    assert "PYSIDE_BINDINGS=(QtWidgets QtOpenGL QtQuick)" in trim
