"""The item page hero paints the clicked card's cover the frame the page is
keyed, and a hovered card has its page warmed before the click.

Source-level pins on Main.qml for the plumbing a headless load cannot see
fail: the art hint travelling beside the title hint, the Art stand-in layer
gating the "art: GET" box, the skeleton header, and the hover prefetch wiring.
"""

from __future__ import annotations

import re
from pathlib import Path

MAIN_PATH = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"
MAIN_QML = MAIN_PATH.read_text()


def _body(start: str, end: str = "\n    }") -> str:
    assert start in MAIN_QML, start
    return MAIN_QML.split(start, 1)[1].split(end, 1)[0]


def _component(name: str) -> str:
    return _body(f"    component {name}:", "\n    component ")


# ----- the art hint travels with the title hint -------------------------------


def test_every_title_hint_assignment_has_its_art_twin():
    lines = MAIN_QML.splitlines()
    title_sites = [i for i, ln in enumerate(lines) if re.search(r"\bbrowseTitleHint = ", ln)]
    assert title_sites, "browseTitleHint assignments moved"
    for i in title_sites:
        window = "\n".join(lines[i : i + 3])
        assert re.search(r"\bbrowseArtHint = ", window), f"no browseArtHint beside line {i + 1}: {lines[i].strip()}"


def test_open_item_carries_the_opener_art_and_the_snapshot_keeps_it():
    body = _body("    function openBrowseItem(kind, id, highlight, title, art) {")
    assert 'browseArtHint = art || ""' in body
    snap = _body("    function navSnapshot() {")
    assert 'art: browsePageKey !== "" && !browsePage ? browseArtHint : ""' in snap
    restore = _body("    function _navRestore(")
    assert 'browseArtHint = !s.page ? (s.art || "") : ""' in restore


def test_page_openers_forward_the_art_they_have():
    assert "function openAlbumPage(albumId, highlight, title, art)" in MAIN_QML
    assert "function openPlaylistPage(playlistId, title, art)" in MAIN_QML
    card = _body("    function openBrowseCard(card) {")
    assert 'openBrowseItem(kind, card.id, "", card.title || "", card.art || "")' in card
    assert 'openBrowseItem("album", card.album_id, card.id, card.album || "", card.art || "")' in card
    # Rows that name a page pass their cover along too.
    assert MAIN_QML.count('root.openAlbumPage(albumId, "", title, art)') == 2
    assert MAIN_QML.count("root.openPlaylistPage(plId, title, art)") == 2
    assert "root.openPlaylistPage(plRow.model.id, plRow.model.title, plRow.model.art)" in MAIN_QML


# ----- the Art stand-in layer -------------------------------------------------


def test_art_stand_in_layer_sits_beneath_and_silences_the_placeholder():
    art = _component("Art")
    assert 'property string underUrl: ""' in art
    assert 'readonly property bool underReady: underUrl !== "" && underImg.status === Image.Ready' in art
    # Declared before the cover so it paints beneath it.
    assert art.index("id: underImg") < art.index("id: artImg")
    under = art.split("id: underImg", 1)[1].split("id: artImg", 1)[0]
    assert 'source: artRoot.everShown ? artRoot.underUrl : ""' in under
    assert "sourceSize.width: artRoot.decodeW" in under and "sourceSize.height: artRoot.decodeW" in under
    assert "visible: status === Image.Ready && artImg.opacity < 1" in under
    # The "art: GET" box never covers a visible stand-in.
    term = art.split("id: artTerm", 1)[1]
    gate = term.split("opacity:", 1)[1].split("\n", 2)
    assert "!artRoot.underReady" in gate[0] + gate[1]
    # Nor does the no-art glyph flash while the stand-in decodes.
    assert 'visible: artRoot.artState === "none" && artRoot.underUrl === ""' in art


# ----- the skeleton header -----------------------------------------------------


def test_item_header_paints_as_a_skeleton_before_the_payload():
    hdr = MAIN_QML.split("id: browseItemHeader", 1)[1].split("id: browseDrillHint", 1)[0]
    assert 'readonly property bool skeleton: hd === null && keyKind !== ""' in hdr
    assert 'root.browseHighlightId === ""' in hdr, "highlight opens hide the column; a skeleton there would blink"
    assert "visible: hd !== null || skeleton" in hdr
    assert "underUrl: root.browseArtHint" in hdr
    assert 'text: browseItemHeader.hd ? (browseItemHeader.hd.title || "") : root.browseTitleHint' in hdr
    # Both backdrop layers decode square so a pre-warmed cover is an exact hit.
    assert hdr.count("sourceSize.height: 480") == 2
    assert "source: root.browseArtHint" in hdr
    # No download control before the payload says what the page is.
    assert "visible: browseItemHeader.hd !== null" in hdr


def test_wire_hint_sits_under_the_skeleton():
    hint = MAIN_QML.split("id: browseDrillHint", 1)[1].split("\n                    }", 1)[0]
    assert "topPad: browseItemHeader.visible ? 40 : 96" in hint


def test_every_loading_surface_shares_the_one_wire_hint():
    # The hint's look lives in WireHint.qml alone; the three loading surfaces
    # (landing, drilled page, fresh search) instantiate it rather than each
    # carrying its own copy, so an edit there changes every loading state.
    wire = (MAIN_PATH.parent / "WireHint.qml").read_text(encoding="utf-8")
    assert 'property string phrase: "Reading the wire…"' in wire
    # (comments in Main.qml still quote the phrase when they explain the
    # loading states; what must not come back is a Text rendering it.)
    assert 'text: "Reading the wire…"' not in MAIN_QML
    for hint_id in ("browseLandingHint", "browseDrillHint", "searchBuildHint"):
        site = MAIN_QML.split(f"id: {hint_id}", 1)[0].rsplit("\n", 2)[1]
        assert site.strip() == "WireHint {", (hint_id, site)


def test_the_hint_ships_one_treatment_and_not_a_dial():
    # Eleven were built to be compared in the lab (tags waves-wire-hint-*);
    # the chosen one is the whole file now. A `variant` switch back in this
    # component means the other ten came back with it, and every loading
    # surface in the app is then carrying code no release can reach.
    wire = (MAIN_PATH.parent / "WireHint.qml").read_text(encoding="utf-8")
    assert "property int variant" not in wire
    assert "Loader" not in wire
    # The swell: a row of cells with a bright head and a long wake behind it.
    assert "readonly property int cells: 30" in wire
    assert "readonly property real head" in wire
    assert "opacity: 0.14 + 0.86 * lit" in wire


def test_the_finished_page_never_waits_for_the_hint_to_fade():
    # The hint fades out over the arriving content, but its LAYOUT box must
    # collapse the instant loading ends: an animated height would hold the
    # finished page down for the length of the fade, and the rows would be
    # watched sliding up into place on every load.
    wire = (MAIN_PATH.parent / "WireHint.qml").read_text(encoding="utf-8")
    assert "height: active ? implicitHeight : 0" in wire
    assert "Behavior on height" not in wire
    # The visual, and only the visual, rides the cross-fade. Through states and
    # transitions, not a Behavior: a Behavior whose duration binding also reads
    # the flag that drives it captures the OLD value and swaps the two timings
    # (measured on HoverSwell in Main.qml), so every appearance ran at the exit
    # speed and every exit at the arrival one.
    assert "opacity: hint.shown" in wire
    assert 'states: State { name: "up"; when: hint.active' in wire
    assert "Behavior on shown" not in wire


# ----- the hover prefetch wiring ----------------------------------------------


def test_hover_prefetch_is_one_shared_dwell_that_warms_the_hero_and_asks_the_backend():
    body = _body("    function hoverPrefetch(card, dwell) {")
    assert "if (!root.signedIn) return" in body
    assert 'root.browsePageKey === "item:" + k' in body, "hovering the page you are on must not refetch it"
    assert "hoverPrefetchTimer.interval = dwell > 0 ? dwell : 200" in body, "a caller may ask for a longer rest"
    timer = MAIN_QML.split("id: hoverPrefetchTimer", 1)[1].split("\n    }", 1)[0]
    assert "interval: 200" in timer
    assert 'root.warmArt("" + c.art, 360, 360)' in timer, "the hero decodes at 360 (180px Art)"
    assert "waves.prefetchBrowseItem(" in timer
    keyfn = _body("    function _cardPrefetchKey(card) {")
    assert '"album:" + card.album_id' in keyfn, "a track card prefetches its album page"
    assert 'kind === "playlist" || kind === "mix" || kind === "album"' in keyfn
    assert '"artist"' not in keyfn, "artist pages are a different, heavier path: not prefetched"


def test_cards_and_rows_arm_the_prefetch_on_hover():
    bc = _component("BrowseCard")
    assert "root.hoverPrefetch(bc.card)" in bc and "root.hoverPrefetchCancel(bc.card)" in bc
    # Its own HoverHandler, not the Art's fxHover (off when the tilt is off):
    # the handler opens within two lines of the arm call.
    before = bc.split("root.hoverPrefetch(bc.card)", 1)[0].splitlines()[-3:]
    assert any("HoverHandler {" in ln for ln in before), before
    # The art card arms from the WHOLE card, so crossing from the artwork down
    # to the title does not cancel a dwell that never left the card. Its
    # artwork-only handler keeps the hover strip and must not prefetch.
    ac = _component("ArtCard")
    assert "root.hoverPrefetch(ac.card)" in ac and "root.hoverPrefetchCancel(ac.card)" in ac
    wrap = MAIN_QML.split("id: acWrapHover", 1)[1].split("}", 1)[0]
    assert "hoverPrefetch" not in wrap, "prefetch belongs to the card-wide handler, not the artwork's"
    pl = _component("LibPlaylistRow")
    assert "root.hoverPrefetch(plRow.prefetchCard)" in pl and "enabled: !plRow.isFolder" in pl
    assert '({ kind: "album", id: ab.albumId, art: ab.art })' in MAIN_QML
    assert '({ kind: "playlist", id: pb.plId, art: pb.art })' in MAIN_QML


def test_track_rows_prefetch_their_album_only_after_a_longer_rest():
    trow = _component("TrackRow")
    assert (
        'readonly property var prefetchCard: ({ kind: "album", id: trow.albumId, art: "" })' in trow
    ), "a row's cover is the small size, warming it at the hero's would pin a pixmap nobody asks for"
    assert "root.hoverPrefetch(trow.prefetchCard, 450)" in trow, "a row is where a pointer parks: longer dwell"
    assert "root.hoverPrefetchCancel(trow.prefetchCard)" in trow
    # A HoverHandler, not the row's MouseArea: the thumb, title and buttons
    # stacked on the row each take hover, so containsMouse would restart the
    # dwell at every internal edge.
    before = trow.split("root.hoverPrefetch(trow.prefetchCard, 450)", 1)[0].splitlines()[-4:]
    assert any("HoverHandler {" in ln for ln in before), before
    assert 'enabled: trow.kind !== "video" && trow.albumId !== ""' in trow


def test_prefetched_covers_warm_at_the_sizes_the_page_asks_for():
    handler = _body("        function onBrowsePagePrefetched(p) {", "\n        }")
    assert 'root.warmArt("" + p.art, 360, 360)' in handler
    assert 'root.warmArt("" + p.art, 480, 480)' in handler
    assert 'root.warmArt("" + arts[i], root.discDecode, root.discDecode)' in handler
    assert "i < 16" in handler, "a screenful of rows, not half of one"
    # One decode size for the disc, in one place: the pool keys on the exact
    # size, so two literals drifting apart would silently warm nothing.
    assert "readonly property int discDecode: 68" in MAIN_QML
    assert "68" not in handler, "the warm loop must ask root.discDecode, never repeat the literal"


# ----- the round track disc says which state it is in -------------------------


def test_the_disc_has_the_same_four_load_states_as_a_cover_box():
    pa = _component("PreviewArt")
    assert 'readonly property string artState: pa.url === "" ? "none"' in pa
    assert (
        '(paImg.status === Image.Error && paImg.retries >= 3) ? "failed"' in pa
    ), "a disc mid-retry must read as loading, or the mark strobes red/green on the way to failed"
    assert "property bool artWaited: false" in pa and "onUrlChanged: artWaited = false" in pa
    assert 'running: pa.artState === "loading" && pa.everShown' in pa, "the grace window starts when the disc shows"
    assert "interval: 250" in pa


def test_the_disc_shows_the_house_marks_on_a_still_plate():
    pa = _component("PreviewArt")
    # The marks live outside coverWrap, which spins with the buffering vinyl.
    plate = pa.index("id: paPlate")
    wrap = pa.index("id: coverWrap")
    assert plate < wrap, "the plate must be declared before (and so paint under) the cover"
    assert "rotation: paVinyl.rot" in pa[wrap:], "coverWrap still spins"
    assert "rotation:" not in pa[plate:wrap], "the loading mark must never spin"
    # The mask stays inside coverWrap: it is the circle.
    assert "id: paMask" in pa[wrap:], "paMask is the mask's sourceItem and belongs with the Image"
    term = pa[pa.index("id: paTerm") :]
    assert 'color: "#04140a"' in term and 'pa.artState === "failed" ? root.red : root.accentDim' in term
    assert 'text: ">"' in term and 'text: "x"' in term
    assert 'opacity: ((pa.artState === "loading" && pa.artWaited) || pa.artState === "failed") ? 1 : 0' in term
    assert 'text: "≈"' in pa and 'visible: pa.artState === "none"' in pa, "a track with no cover says so"


def test_the_disc_blinks_off_the_shared_clock_and_fades_on_the_wrap():
    assert "readonly property real termBlink: (marchTick % 20) < 10 ? 1 : 0" in MAIN_QML
    pa = _component("PreviewArt")
    assert (
        "opacity: paTerm.visible ? root.termBlink : 1" in pa
    ), "the visible test goes first, so a settled disc never reads the 20Hz tick"
    # (The vinyl spin is a SequentialAnimation too, but only one disc buffers
    # at a time; a blink would run on every loading disc on the page.)
    mark = pa[pa.index("id: paTerm") : pa.index("id: coverWrap")]
    assert "SequentialAnimation" not in mark, "one blink animation per disc is the per-vsync repaint trap"
    # The fade rides coverWrap, never the layered Image (whose texture would
    # re-render every frame, times every disc on the page).
    wrap = pa[pa.index("id: coverWrap") :]
    assert 'opacity: pa.artState === "ready" ? 1 : 0' in wrap
    assert "Behavior on opacity { enabled: pa.artWaited; NumberAnimation { duration: 220" in wrap
    img = pa[pa.index("id: paImg") :]
    assert "visible: status === Image.Ready" in img
    assert "opacity:" not in img.split("layer.enabled", 1)[0], "no opacity animation on the layered Image"
    assert "sourceSize.width: root.discDecode" in img
