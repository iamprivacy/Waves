"""The FLAC signature card (Settings, FFmpeg & FLAC) and its confirmation gate.

Static checks over the QML, in the shape of test_settings_diagnostics_card:
the card may only ever read state and ask (scan, cancel, reveal); the two
writing calls, ``waves.flacCheckFix()`` and ``waves.flacCheckResume()``,
live in Main.qml inside the confirmation gate and nowhere else; and every
dynamic text is plain text.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

QML_DIR = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml"
SETTINGS = QML_DIR / "SettingsPage.qml"
MAIN = QML_DIR / "Main.qml"


def _code(path: Path) -> str:
    """The QML with ``//`` comments stripped."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(re.sub(r"//.*$", "", line) for line in lines)


def _block(source: str, anchor: str) -> str:
    """The braces-balanced block whose opening ``{`` is the last one before ``anchor``
    (the element whose ``id:`` line the anchor is)."""
    start = source.rindex("{", 0, source.index(anchor))
    depth = 0
    for offset, char in enumerate(source[start:], start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : offset + 1]
    raise AssertionError("unbalanced block")


def test_the_card_sits_with_ffmpeg_and_the_reset_row_ends_diagnostics():
    source = _code(SETTINGS)
    assert "id: flacCardComp" in source
    loaders = [m.start() for m in re.finditer(r"sourceComponent: (flacCardComp|resetRowComp)", source)]
    assert len(loaders) == 2
    flac_loader = source.rindex("Loader {", 0, source.index("sourceComponent: flacCardComp"))
    assert 'active: card.modelData.id === "processing"' in source[flac_loader : flac_loader + 400]
    reset_loader = source.rindex("Loader {", 0, source.index("sourceComponent: resetRowComp"))
    assert 'active: card.modelData.id === "diagnostics"' in source[reset_loader : reset_loader + 400]


def test_the_settings_page_never_calls_the_writing_slot():
    """Only Main.qml's gate may call flacCheckFix or flacCheckResume; the
    page raises a signal."""
    source = _code(SETTINGS)
    assert "flacCheckFix" not in source
    assert "flacCheckResume" not in source
    assert "signal flacFixRequested(int count, string where)" in source
    assert "signal flacResumeRequested(string where)" in source
    card = _block(source, "id: flacCardComp")
    assert "page.flacFixRequested(flacCard.fixable, page.flacWhere())" in card
    assert "page.flacResumeRequested(page.flacResumeWhere())" in card
    assert "waves.flacCheckScan(page.flacDeep, page.flacRecheck)" in card
    assert "waves.flacCheckCancel()" in card
    assert "waves.flacCheckRevealReports()" in card
    assert "setWavesPref" not in card, "the card holds no persisted setting"


def test_main_confirms_the_fix_with_the_count_before_calling_the_backend():
    source = _code(MAIN)
    assert source.count("waves.flacCheckFix()") == 1
    assert source.count("waves.flacCheckResume()") == 1
    gate = _block(source, "id: flacFixGate")
    assert "waves.flacCheckFix()" in gate
    assert "waves.flacCheckResume()" in gate
    assert "visible: root.confirmFlacFix" in gate
    assert "root.flacFixCount" in gate
    assert "text: root.flacFixWhere" in gate, "the gate names where it writes"
    # The confirm closes the gate and then asks, the cancel only closes it.
    confirm = gate[gate.index('label: root.flacFixResume ? "RESUME" : "WRITE SIGNATURES"') :]
    assert confirm.index("root.confirmFlacFix = false") < confirm.index("waves.flacCheckResume()")
    assert confirm.index("root.confirmFlacFix = false") < confirm.index("waves.flacCheckFix()")
    assert "if (root.flacFixResume) waves.flacCheckResume()" in confirm
    # A FIX request always opens the gate as a fix, a RESUME request as a resume.
    assert (
        "onFlacFixRequested: function(count, where) { root.flacFixResume = false; root.flacFixCount = count;"
        " root.flacFixWhere = where; root.confirmFlacFix = true }" in source
    )
    assert (
        "onFlacResumeRequested: function(where) { root.flacFixResume = true; root.flacFixWhere = where;"
        " root.confirmFlacFix = true }" in source
    )


def test_the_fix_dialog_says_what_is_written_and_what_is_not():
    gate = _block(_code(MAIN), "id: flacFixGate")
    for promise in ("34-byte", "audio is never changed", "never rewritten", "report"):
        assert promise in gate, promise


def test_every_dynamic_text_on_the_card_is_plain_text():
    card = _block(_code(SETTINGS), "id: flacCardComp")
    for match in re.finditer(r"Text \{(.*?)\n\s*\}", card, re.S):
        body = match.group(1)
        text = re.search(r'\btext:\s*("(?:[^"\\]|\\.)*"|[^;\n]+)', body)
        if text is None:
            continue
        if re.fullmatch(r'\s*"[^"]*"\s*', text.group(1)):
            continue
        assert "textFormat: Text.PlainText" in body, body.strip()[:120]


def test_the_folder_row_offers_the_waves_library_or_a_browsed_folder():
    card = _block(_code(SETTINGS), "id: flacCardComp")
    assert 'text: "WAVES LIBRARY"' in card
    assert 'text: "CUSTOM LOCATION"' in card
    assert 'waves.flacCheckSetSource("waves")' in card
    assert 'waves.flacCheckSetSource("folder")' in card
    dialog = _block(card, "id: flacFolderDlg")
    assert "onAccepted: waves.flacCheckSetFolder(page.urlToPath(selectedFolder))" in dialog


def test_the_chosen_folder_only_ever_comes_from_the_system_dialog():
    """No field on the card can type a path: a typed relative or half-typed
    path is never a scan target."""
    card = _block(_code(SETTINGS), "id: flacCardComp")
    for typed in ("SText", "TextField", "TextInput", "TextEdit"):
        assert typed not in card, typed
    assert card.count("waves.flacCheckSetFolder(") == 1


def test_the_card_offers_no_fix_for_folders_it_is_no_longer_showing():
    source = _code(SETTINGS)
    card = _block(source, "id: flacCardComp")
    assert "readonly property int fixable: running || page.flacStale() ? 0 : (st.fixable || 0)" in card
    assert 'page.flacStale()) return "Not checked yet."' in source


def test_the_card_re_reads_its_folders_whenever_the_waves_library_can_move():
    """The "Waves library" is the download folder and the library folder; both
    can change while Settings stays open (SAVE CHANGES, the library card's
    live switches, a settings reset), and the card's stale check compares
    against what it last read. Each of those paths refreshes it."""
    source = _code(SETTINGS)
    save = source[source.index('text: "SAVE CHANGES"') :]
    save = save[: save.index("flashTimer.restart()") + 60]
    assert "waves.applySettings(page.editMap)" in save
    assert "page.flacRefresh()" in save
    assert "refreshSchema(); syncLibraryMirrors(); flacRefresh() }" in source  # externalReset
    for handler in ("function onLibraryScanStatusChanged()", "function onLibrarySourceChanged()"):
        body = source[source.index(handler) :]
        body = body[: body.index("\n        }")]
        assert "page.flacRefresh()" in body, handler


def test_the_card_shows_progress_and_offers_a_resume():
    """While a run is on, a bar shows the share of files done and the time
    left sits beside it; a fix that stopped before it finished leaves RESUME
    LAST FIX, unless FIX already offers the same files."""
    source = _code(SETTINGS)
    card = _block(source, "id: flacCardComp")
    assert "readonly property real fraction: (st.total || 0) > 0 ? Math.min(1, (st.files || 0) / st.total) : 0" in card
    assert "width: parent.width * flacCard.fraction" in card
    assert "text: page.flacTimeLeft()" in card
    assert "visible: page.flacResumable()" in card
    assert 'text: "RESUME LAST FIX"' in card
    assert (
        '!st.running && (st.resume_source || "") !== "" && (page.flacStale() || !(st.fixable > 0))' in source
    ), "RESUME is not offered beside a FIX for the same files"


def test_a_stale_selection_paints_no_warning_and_reveals_no_old_report():
    """After the folder selection changes, the note reads "Not checked yet":
    it is not painted as a mismatch warning."""
    card = _block(_code(SETTINGS), "id: flacCardComp")
    assert '(!page.flacStale() && page.flacCount("mismatch") > 0)' in card


def test_view_reports_sits_beside_scan_and_shows_whenever_a_report_exists():
    """VIEW REPORTS needs no finished run: it shows while a scan runs and
    after a relaunch, wherever a report is on disk, and comes straight after
    SCAN/CANCEL so a FIX button appearing never moves it."""
    card = _block(_code(SETTINGS), "id: flacCardComp")
    btn = _block(card, 'objectName: "flacReportsBtn"')
    assert "visible: flacCard.st.reports === true" in btn
    assert 'text: "VIEW REPORTS"' in btn
    assert "running" not in btn
    assert card.index('text: flacCard.running ? "CANCEL" : "SCAN"') < card.index('objectName: "flacReportsBtn"')
    assert card.index('objectName: "flacReportsBtn"') < card.index("visible: flacCard.fixable > 0")


def test_folders_a_scan_could_not_open_are_named_on_the_card():
    """A partial scan (some folders unreadable) never reads as a clean one:
    the note counts the folders, and the dot and note turn gold."""
    source = _code(SETTINGS)
    missed = re.search(r"function flacMissed\(\) \{.*?\n    \}", source, re.S).group(0)
    assert "st.folders_unreadable || 0" in missed
    assert "page.flacStale()" in missed, "a stale count is never shown"
    note = re.search(r"function flacNote\(\) \{.*?\n    \}", source, re.S).group(0)
    assert "var missed = page.flacMissed()" in note
    assert '" folder could not be opened"' in note and '" folders could not be opened"' in note
    assert '" (see the report)"' in note
    card = _block(source, "id: flacCardComp")
    assert card.count("page.flacMissed() > 0") == 2, "the dot and the note both warn"
    # The note wraps, so the folder count at the end of a long line is never elided away.
    header = card[: card.index("text: page.flacNote()")]
    assert "wrapMode: Text.WordWrap" in header[header.rindex("Text {") :]


def test_a_scan_that_reused_earlier_verdicts_says_how_many():
    """Files unchanged since an earlier check are not read again; the note
    says so, so a quick second scan never passes for a full re-read."""
    source = _code(SETTINGS)
    note = re.search(r"function flacNote\(\) \{.*?\n    \}", source, re.S).group(0)
    scan_part = note[note.index("} else {") :]
    assert "st.cached || 0" in scan_part, "only a scan's note names them"
    assert '" unchanged since an earlier check"' in scan_part


def test_recheck_all_is_a_switch_that_cannot_flip_mid_run():
    """Recheck all is off by default and rides along with the scan call;
    like Deep check it cannot be flipped while a run is on."""
    source = _code(SETTINGS)
    assert "property bool flacRecheck: false" in source
    switch = _block(_block(source, "id: flacCardComp"), 'objectName: "flacRecheckRow"')
    assert "checked: page.flacRecheck" in switch
    assert "enabled: !flacCard.running" in switch
    assert "onClicked: page.flacRecheck = !page.flacRecheck" in switch


def test_the_button_row_fits_the_narrowest_window_without_stretching_a_button():
    """At the 880 px minimum window the deep-check label wraps instead of
    running off the card; at wide widths it sits right at its natural width.
    No button is ever stretched."""
    card = _block(_code(SETTINGS), "id: flacCardComp")
    start = card.rindex("RowLayout {", 0, card.index('text: flacCard.running ? "CANCEL" : "SCAN"'))
    row = _block(card[start:], "Layout.fillWidth: true; Layout.topMargin: 2")
    assert row.lstrip("{").lstrip().startswith("Layout.fillWidth: true")
    options = _block(row, 'objectName: "flacOptions"')
    assert "Layout.fillWidth: true" in options
    assert "Layout.maximumWidth: implicitWidth" in options
    for name, text in (("flacDeepRow", "flacDeepTxt"), ("flacRecheckRow", "flacRecheckTxt")):
        switch = _block(options, f'objectName: "{name}"')
        assert "Layout.fillWidth: true" in switch
        assert "Layout.maximumWidth: implicitWidth" in switch
        label = switch[switch.index(f"id: {text}") :]
        assert "Layout.fillWidth: true; wrapMode: Text.WordWrap" in label[: label.index("}")]
    # Buttons hug: no Rectangle in the row takes fillWidth.
    for match in re.finditer(r"Rectangle \{", row):
        button = _block(row[match.start() :], "\n")
        assert "Layout.fillWidth" not in button, button[:120]


def test_jumping_to_the_card_disarms_once_the_section_has_opened():
    """The card sits in the page's last screenful, so the jump's target can
    lie past the furthest reachable scroll. A settle timer takes the
    reachable spot and disarms once the open animation stops growing the
    page, and a scroll the user makes (the ScrollBar included, which emits no
    movementStarted) disarms it at once, so a later re-measure (a SCAN's
    progress row) never snaps the view back."""
    source = _code(SETTINGS)
    jump = re.search(r"function jumpToCard\(cardId\) \{.*?\n    \}", source, re.S).group(0)
    assert "page.flacJumpY = page.pendingY; flacJumpSettle.restart()" in jump
    timer = _block(source, "id: flacJumpSettle")
    assert "page.pendingY === page.flacJumpY" in timer, "only the jump's own target is disarmed"
    assert timer.index("page._restoreScroll()") < timer.index("page.pendingY = -1")
    assert "if (flacJumpSettle.running) flacJumpSettle.restart()" in source
    assert "ScrollBar.vertical: WavesScrollBar { onPressedChanged: if (pressed) page.pendingY = -1 }" in source
    restore = re.search(r"function _restoreScroll\(\) \{.*?\n    \}", source, re.S).group(0)
    assert restore.index("restoringScroll = true") < restore.index("settingsFlick.contentY =")
    assert restore.index("settingsFlick.contentY =") < restore.index("restoringScroll = false")
    assert "!page.restoringScroll" in source


def _path_functions():
    """pathUrl and urlToPath from the page, run in a bare JS engine."""
    qjs = pytest.importorskip("PySide6.QtQml")
    from PySide6.QtGui import QGuiApplication

    _ = QGuiApplication.instance() or QGuiApplication([])
    source = SETTINGS.read_text(encoding="utf-8")
    engine = qjs.QJSEngine()
    for name in ("urlToPath", "pathUrl"):
        body = re.search(rf"    function {name}\(\w+\) \{{.*?\n    \}}", source, re.S).group(0)
        result = engine.evaluate(body)
        assert not result.isError(), result.toString()
    return engine


def test_the_folder_dialog_url_keeps_every_character_of_the_path():
    """A folder named "AC#DC" or "What?" opens the dialog in that folder, not
    its parent: the URL is percent-encoded, and urlToPath reads it back."""
    engine = _path_functions()
    cases = {
        "/Music/AC#DC": "file:///Music/AC%23DC",
        "/Music/What?": "file:///Music/What%3F",
        "/Music/100% Pure": "file:///Music/100%25%20Pure",
        "C:/Music/AC#DC": "file:///C:/Music/AC%23DC",
        "C:\\Music\\Sub": "file:///C:/Music/Sub",
        "//nas/music/AC#DC": "file://nas/music/AC%23DC",
        "\\\\nas\\music": "file://nas/music",
        "": "",
    }
    for path, url in cases.items():
        got = engine.evaluate(f"pathUrl({path!r})").toString()
        assert got == url, (path, got)
        if url:
            back = engine.evaluate(f"urlToPath({url!r})").toString()
            assert back == path.replace("\\\\", "//").replace("\\", "/"), (url, back)
