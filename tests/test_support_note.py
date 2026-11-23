"""The note from the developer at every launch (supportNoteGate).

Waves is free and its developer lost their income, so each launch opens a
short note that says so and ends in the Buy Me a Coffee button.

WHAT THIS FENCES OFF
--------------------
1. It shows every launch: nothing persisted can retire it. Closing it holds
   for the running session only.
2. It is last in the launch queue: it waits for the launch water, every
   first-run prompt, the update prompt and the FLAC notice, reads their
   eligibility (not their lagging visible), and steps aside while any other
   prompt or dialog is up, under it in the overlay or above it.
3. It closes three ways (CLOSE, a click on the scrim, Escape), and the
   coffee button closes it too after opening the page. Every other Esc
   shortcut stands down while it is up (Qt fires neither of two live ones).
4. The button is the same one as in Settings (CoffeeButton.qml), and the
   link lives in that one file.
5. The wording: the job, rent and food, the hundreds of dollars, free and
   open source, and the ask for a recurring donation.
"""

from __future__ import annotations

import re
from pathlib import Path

QML = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml"
MAIN = (QML / "Main.qml").read_text(encoding="utf-8")
SETTINGS = (QML / "SettingsPage.qml").read_text(encoding="utf-8")
COFFEE = (QML / "CoffeeButton.qml").read_text(encoding="utf-8")


def _gate() -> str:
    m = re.search(r"id: supportNoteGate\b.*?\n    }\n", MAIN, re.S)
    assert m, "the launch note must exist in Main.qml"
    return m.group(0)


def test_note_shows_every_launch_and_stores_nothing():
    gate = _gate()
    # No persisted flag anywhere in the gate: the next launch shows it again.
    assert "setupSettings." not in gate.replace("setupSettings.ffmpegSetupDone", "")
    assert "legalSettings." not in gate
    assert "waves.setWavesPref" not in gate
    assert "supportNote" not in re.search(r"id: setupSettings;.*?\n    }\n", MAIN, re.S).group(0)
    # Closing holds for this run only.
    assert "property bool dismissed: false" in gate
    should = re.search(r"readonly property bool shouldShow:([^\n]*)", gate).group(1)
    for clause in ("root.supportNoteEnabled", "promptsClear", "!dismissed"):
        assert clause in should, f"the note's visibility lost its {clause!r} clause"
    # Not tied to a page: it opens wherever the launch lands.
    assert "browseOpen" not in gate
    # The one off switch: on for every real launch, off only offscreen,
    # where the scenarios run and no stored flag could clear their frame.
    assert 'property bool supportNoteEnabled: Qt.platform.pluginName !== "offscreen"' in MAIN


def test_note_is_last_in_the_launch_queue_and_never_stacks():
    gate = _gate()
    clear = re.search(r"promptsClear:(.*?)\n\s*readonly property bool shouldShow", gate, re.S).group(1)
    for clause in (
        "root.signedIn",
        "setupSettings.ffmpegSetupDone",
        "root.termsCurrentAccepted",
        "bootOverlay.done",
        "!ffmpegGate.wanted",
        "!updateOptInGate.shouldShow",
        "!flacNoticeGate.shouldShow",
        "!flacNoticeGate.shown",
    ):
        assert clause in clear, f"the note's queue test lost its {clause!r} clause"
    assert ".visible" not in clear, "eligibility never lags; a gate's visible does"
    on_top = re.search(r"readonly property bool onTop:(.*?)\n        function", gate, re.S).group(1)
    for clause in (
        "shown",
        "promptsClear",
        "!updateOptInGate.visible",
        "!flacNoticeGate.visible",
        # Under the note in the overlay: a download Waves starts on its own
        # can raise the folder prompts at launch, and the note's scrim would
        # cover them and eat their clicks.
        "!folderGate.visible",
        "!folderUnreachableGate.visible",
        "!ffmpegBlockGate.visible",
        "!libraryClaimGate.visible",
        # Above the note in the overlay.
        "!followDialog.open",
        "!importDialog.open",
        # Declared after the note, so above it: a window close or a quit
        # with downloads running would raise them hidden behind its card.
        "!closeGate.open",
        "!exitGate.open",
    ):
        assert clause in on_top, f"the note's on-screen test lost {clause!r}"
    # Painted only through its own entrance dial, which onTop drives both ways.
    assert "visible: reveal > 0" in gate
    assert "else supportNoteOut.start()" in gate
    assert "parent: Overlay.overlay" in gate
    # After the FLAC notice in the source too, so it stacks above nothing.
    assert MAIN.index("id: flacNoticeGate") < MAIN.index("id: supportNoteGate")


def test_note_closes_by_button_scrim_escape_and_the_coffee_button():
    gate = _gate()
    close = re.search(r"function close\(how\) \{(.*?)\n        \}", gate, re.S).group(1)
    assert close.lstrip().startswith("if (!onTop) return")
    assert "dismissed = true" in close
    assert 'objectName: "supportNoteClose"; label: "CLOSE"' in gate
    assert 'onClicked: supportNoteGate.close("note closed")' in gate
    # Every way out but the coffee button waits five seconds, and the wait
    # restarts each time the note comes up.
    assert 'if (!canClose && how !== "coffee opened") return' in close
    assert "property int closeLockMs: 5000" in gate
    assert "readonly property bool canClose: lockP >= 1" in gate
    assert "enabled: supportNoteGate.canClose" in gate
    assert "if (onTop) { supportNoteIn.start(); supportNoteLock.restart() }" in gate
    assert "property int delayMs: 5000" in gate
    assert 'onBackdropClicked: supportNoteGate.close("note closed")' in gate
    assert 'onOpened: supportNoteGate.close("coffee opened")' in gate
    assert (
        'Shortcut { sequence: "Esc"; enabled: supportNoteGate.onTop; onActivated: supportNoteGate.close("note closed") }'
        in MAIN
    )


def test_every_other_esc_shortcut_stands_down_while_the_note_is_up():
    # Qt fires NEITHER of two live shortcuts with one key (only
    # activatedAmbiguously), so a second live Esc made Esc dead on the note.
    shortcuts = re.findall(r'Shortcut \{\s*sequence: "Esc";?(.*?)onActivated', MAIN, re.S)
    assert len(shortcuts) >= 7, "the Esc shortcuts moved; re-read this guard"
    for body in shortcuts:
        if "supportNoteGate.close" in body or "enabled: supportNoteGate.onTop;" in body:
            continue
        assert "!root.noteUp" in body, f"an Esc shortcut stays live under the note: {body.strip()!r}"


def test_every_window_shortcut_stands_down_on_one_switch():
    # One root switch (noteUp) is the only thing outside the note that
    # names it, so removing the note is: delete the gate and its Esc, set
    # noteUp false. Every other Shortcut, whatever its key, carries it.
    assert "readonly property bool noteUp: supportNoteGate.onTop" in MAIN
    outside = MAIN.replace(_gate(), "").replace(
        'Shortcut { sequence: "Esc"; enabled: supportNoteGate.onTop; onActivated: supportNoteGate.close("note closed") }',
        "",
    )
    code = re.sub(r"//[^\n]*", "", outside.replace("readonly property bool noteUp: supportNoteGate.onTop", ""))
    assert "supportNoteGate" not in code, "only root.noteUp may name the note outside it"
    bodies = re.findall(r"Shortcut \{(.*?)onActivated", outside, re.S)
    assert len(bodies) >= 8, "the Shortcuts moved; re-read this guard"
    for body in bodies:
        assert "!root.noteUp" in body, f"a Shortcut stays live under the note: {body.strip()!r}"


def test_the_note_blocks_input_through_note_shield():
    # The backdrop holds every button, the wheel, hover and the keyboard;
    # the card swallows clicks so they neither close the note nor fall
    # through. Future notes reuse the same shield.
    gate = _gate()
    assert "NoteShield {\n            anchors.fill: parent; active: supportNoteGate.onTop" in gate
    assert 'onBackdropClicked: supportNoteGate.close("note closed")' in gate
    assert "NoteShield { anchors.fill: parent; holdsKeys: false }" in gate
    shield = (QML / "NoteShield.qml").read_text(encoding="utf-8")
    for clause in (
        "acceptedButtons: Qt.AllButtons; hoverEnabled: true",
        "onWheel: function(w) { w.accepted = true }",
        "Keys.onPressed: function(e) { e.accepted = true }",
        "if (m.button === Qt.LeftButton) shield.backdropClicked()",
    ):
        assert clause in shield, f"NoteShield lost {clause!r}"


def test_the_artist_card_stays_under_the_note_and_every_prompt():
    # z 1300 lifts the card over every z-1200 prompt in the overlay, and a
    # hover passes through their backdrops: only the import review may
    # have it on top, and the note blocks it outright.
    card = re.search(r"id: ahcCard\n(.*?)modal: false", MAIN, re.S).group(1)
    assert "z: root.ahcInReview ? 1300 : 0" in card
    blocked = re.search(r"readonly property bool ahcBlocked:(.*?)\n    //", MAIN, re.S).group(1)
    assert "root.noteUp" in blocked


def test_note_leaves_breadcrumbs_without_user_data():
    gate = _gate()
    assert 'waves.uiLog("support", "note shown", -1)' in gate
    assert 'waves.uiLog("support", how, -1)' in gate


def test_the_coffee_button_is_shared_and_the_link_lives_in_one_file():
    gate = _gate()
    assert "CoffeeButton {" in gate
    assert "steaming: supportNoteGate.onTop" in gate
    # Settings keeps its button, under the name its fit test looks up.
    block = re.search(r"CoffeeButton \{.*?\n        \}", SETTINGS, re.S).group(0)
    assert 'objectName: "settingsCoffee"' in block
    assert "steaming: page.active" in block
    url = "https://www.buymeacoffee.com/iamprivacy"
    assert COFFEE.count(url) == 1
    assert "Qt.openUrlExternally(coffee.url)" in COFFEE
    for name, src in (("Main.qml", MAIN), ("SettingsPage.qml", SETTINGS)):
        assert "buymeacoffee" not in src, f"{name} must reach the link through CoffeeButton"
    assert 'property string label: "Buy me a coffee"' in COFFEE
    assert 'label: "Support me and Waves"' in _gate()


def test_note_wording():
    gate = _gate()
    assert 'text: "I need your help"' in gate
    for phrase in (
        "I recently lost my job and my source of income.",
        "I don't know if I will be able to continue developing Waves, or pay my bills for rent and food.",
        "I really hate asking.",
        "software development is my passion",
        "hundreds of dollars",
        "free and open source",
        "become a job that pays the bills. This has been my way of giving back to the open source community that has given me so much over the years.",
        "A recurring one, if you can, helps the most.",
        "I am also open to employment offers. If you have a job opportunity, please reach out at",
        '"getwaves.dev/hire"',
        'Qt.openUrlExternally("https://" + snPara.link)',
    ):
        assert phrase in gate, f"the note lost {phrase!r}"
    assert "—" not in gate and "—" not in COFFEE, "no em dashes in UI copy"
    # Plain text throughout: no rich-text exemption to keep.
    assert "StyledText" not in gate and "RichText" not in gate
    # Job offers go through a page on the site, never an address in the app.
    assert "@" not in gate.split("model: [", 1)[1].split("]", 1)[0], "no email address in the note"
