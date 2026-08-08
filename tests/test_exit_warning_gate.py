"""The exit-while-downloading warning (exitGate).

Closing the window while queue items are queued or running silently killed
them: the app exits, worker threads die mid-file, and unfinished tracks have
to be downloaded again. The gate vetoes that close once, asks, and lets the
user mute it for good.

WHAT THIS FENCES OFF
--------------------
1. onClosing must veto (close.accepted = false) only when downloads are
   active, the warning is not muted, and the exit was not already confirmed,
   so EXIT ANYWAY's re-close actually leaves and an idle queue exits
   instantly.
2. Both buttons must do exactly what they say: KEEP DOWNLOADING only closes
   the gate, EXIT ANYWAY confirms then calls root.close(), and only those two
   persist the "don't warn me again" checkbox.
3. The mute flag lives in QSettings (the "setup" block), NOT waves.json.
4. The gate re-arms per attempt: opening resets confirmed and the checkbox,
   so a cancelled exit warns again next time.
"""

from __future__ import annotations

import re
from pathlib import Path

QML_MAIN = Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui" / "qml" / "Main.qml"


def _src() -> str:
    return QML_MAIN.read_text(encoding="utf-8")


def test_onclosing_vetoes_only_when_it_should():
    src = _src()
    m = re.search(r"onClosing: function \(close\) \{(.*?)\n    \}", src, re.S)
    assert m, "onClosing must be the function form that receives the close event"
    body = m.group(1)
    assert "_winPersist()" in body, "the geometry flush must survive the rewrite"
    assert "close.accepted = false" in body
    cond = re.search(r"if \((.*?)\) \{", body, re.S)
    assert cond, "the veto must be conditional"
    for clause in (
        "root.activeQueueCount > 0",
        "!setupSettings.exitWarnMuted",
        "!exitGate.confirmed",
    ):
        assert clause in cond.group(1), f"veto lost its {clause!r} clause"


def test_buttons_do_what_they_say():
    src = _src()
    gate = re.search(r"id: exitGate\b.*?\n    \}\n", src, re.S)
    assert gate, "the exit gate must exist in Main.qml"
    body = gate.group(0)
    # EXIT ANYWAY: confirm, then a real re-close. Exactly one close() call.
    assert body.count("root.close()") == 1
    assert "exitGate.confirmed = true" in body
    # Both buttons, and ONLY the buttons, persist the checkbox.
    assert body.count("setupSettings.exitWarnMuted = true") == 2
    # A click away closes the gate without persisting anything.
    assert "onClicked: exitGate.open = false" in body
    # Re-arm per attempt: opening resets the confirm latch and the checkbox.
    assert "onOpenChanged: if (open) { confirmed = false; exitSkip.checked = false }" in body


def test_mute_flag_lives_in_qsettings_not_waves_json():
    src = _src()
    # In the "setup" Settings block: waves.json is written wholesale, a new
    # key there would be pinned into every existing user's file.
    setup = re.search(r'id: setupSettings; category: "setup"(.*?)\n    \}', src, re.S)
    assert setup, "the setup Settings block must exist"
    assert "property bool exitWarnMuted: false" in setup.group(1)


def test_count_copy_tracks_the_live_queue_badge():
    src = _src()
    gate = re.search(r"id: exitGate\b.*?\n    \}\n", src, re.S)
    body = gate.group(0)
    # The message counts the same number as the header badge (queued+running
    # from the bridge), singular and plural.
    assert "root.activeQueueCount === 1" in body
    assert "A download is still running." in body
    assert 'root.activeQueueCount + " downloads are still running."' in body


def test_gate_stacks_above_the_video_overlay():
    """The close veto opens this gate; with a video full-screen (z 999) a
    default-z gate paints underneath and the window looks un-closable."""
    src = _src()
    m = re.search(r"id: exitGate\b.*?z: (\d+)", src, re.S)
    assert m and int(m.group(1)) > 999, "the exit gate must stack above the video overlay"


def test_copy_survives_the_last_download_finishing():
    """activeQueueCount can hit 0 while the gate is up (the user chose to
    keep it open rather than auto-close); the body must never read
    '0 downloads are still running.'"""
    src = _src()
    gate = re.search(r"id: exitGate\b.*?\n    \}\n", src, re.S)
    assert gate
    body = gate.group(0)
    assert "All downloads finished." in body
    assert "It is safe to exit now." in body
    assert "root.activeQueueCount === 0" in body
