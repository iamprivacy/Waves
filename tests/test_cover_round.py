"""Covers wear rounded corners: the Images' textures drawn through
``shaders/cover_round.frag`` as a rounded face, with the plain Images kept
for loading, caching and the Software scene graph.

Qt clips an item's children to its box, never to its radius, so the Images
inside ``Art`` always showed square corners no matter the Rectangle's radius.
The face samples the Image's texture directly (no layer, no framebuffer per
cover) and cuts it to the cover's radius; on the Software backend (offscreen
runs, a machine with no GPU) a ShaderEffect draws nothing, so there the
Images paint as before.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
QML = REPO / "waves" / "waves_ui" / "qml"
MAIN = QML / "Main.qml"
SHADER = QML / "shaders" / "cover_round.frag"
BLEND = QML / "shaders" / "cover_blend.frag"


def _src() -> str:
    return MAIN.read_text(encoding="utf-8")


def _component(name: str) -> str:
    src = _src()
    start = src.index(f"\n    component {name}:")
    end = src.index("\n    component ", start + 1)
    return src[start:end]


def test_the_shader_is_compiled_and_current() -> None:
    for frag in (SHADER, BLEND):
        qsb = frag.with_suffix(".frag.qsb")
        assert frag.exists() and qsb.exists()
        assert qsb.stat().st_mtime >= frag.stat().st_mtime, f"recompile {qsb.name} (command in its header)"
    text = SHADER.read_text(encoding="utf-8")
    # The crop is PreserveAspectCrop's, the corner the cover's, premultiplied out.
    assert "max(size.x / tex.x, size.y / tex.y)" in text
    assert "sdCorner(p, 0.5 * size, radius, shapeN)" in text
    assert "1.0 - smoothstep(-aa, aa, d)" in text
    assert "fragColor = c * a * qt_Opacity;" in text


def test_the_face_draws_the_image_texture_rounded() -> None:
    face = _component("ArtFace")
    assert "ShaderEffect" in face
    assert "required property Image src" in face
    assert 'fragmentShader: "shaders/cover_round.frag.qsb"' in face
    # Never without a texture (a warning), never on the Software backend.
    assert "visible: root.roundFaces && src.status === Image.Ready && opacity > 0" in face
    assert "Qt.vector2d(Math.max(1, src.implicitWidth), Math.max(1, src.implicitHeight))" in face


def test_art_keeps_its_images_and_shows_two_faces() -> None:
    art = _component("Art")
    # The Images stay: loaded, cached, pinned, grabbed for the palette.
    for img in ("underImg", "artImg"):
        block = art.split(f"id: {img}", 1)[1].split("\n        }", 1)[0]
        assert "cache: true" in block
        assert "fillMode: Image.PreserveAspectCrop" in block
        assert "root.warmArt(" in block
    assert "artImg.grabToImage(" in art
    # Drawn at opacity 0 under the faces, as themselves on the Software backend.
    assert "opacity: root.roundFaces ? 0 : 1" in art
    assert "opacity: root.roundFaces ? 0 : artRoot.artT" in art
    # One fade dial, read by the cover's Image and its face alike.
    assert 'property real artT: artState === "ready" ? 1 : 0' in art
    assert (
        "ArtFace { src: underImg; radius: artRoot.faceRadius; shadeH: artRoot.shadeH; shadeA: artRoot.shadeA; opacity: artRoot.artT < 1 ? 1 : 0 }"
        in art
    )
    assert (
        "ArtFace { src: artImg; radius: artRoot.faceRadius; shadeH: artRoot.shadeH; shadeA: artRoot.shadeA; opacity: artRoot.artT }"
        in art
    )
    assert "readonly property real faceRadius: Math.min(radius, width / 2, height / 2)" in art
    # The faces come after both Images, the stand-in's first.
    after = art.split("id: artImg", 1)[1]
    assert after.index("ArtFace { src: underImg") < after.index("ArtFace { src: artImg")


def test_the_scene_graph_is_read_once_for_every_cover() -> None:
    src = _src()
    assert "readonly property bool software: GraphicsInfo.api === GraphicsInfo.Software" in src
    assert "readonly property bool roundFaces: !gfxProbe.software" in src
    assert src.count("GraphicsInfo.api") == 1, "one probe, not one attached object per cover"


def test_what_rides_on_a_cover_takes_its_corner() -> None:
    src = _src()
    # The Just in frame rides inside the cover, cut to its corner.
    art = _component("Art")
    assert 'objectName: "releaseFrame"' in art
    assert "radius: artRoot.faceRadius" in art
    assert "component ArtFrame" not in src
    # The video thumb's own frame, the scrims and the gloss sheet.
    assert "radius: 8   // the thumb's own corner" in src
    assert "radius: acArt.faceRadius" in src
    assert "root.cornerPath(0, 0, arbScrim.width, arbScrim.height, arb.radius)" in src
    assert src.count("radius: parent.faceRadius") == 2, "both ArtistBadges bars"
    assert "lit: thumbMa.containsMouse; radius: 6 }" in src
    gloss = _component("Art").split("// Gloss variant", 1)[1]
    assert "PathSvg { path: root.cornerPath(0, 0, artRoot.width, artRoot.height, glossSheet.parent.r) }" in gloss
    assert "PathArc" not in gloss


def test_the_edges_stay_smooth_and_the_corner_reads() -> None:
    src = _src()
    # A thin curved ring under the geometry renderer stair-steps; the layer,
    # drawn tilted and swelled, steps too unless sampled bilinearly.
    assert "layer.smooth: true" in _component("Art")
    # 12 px read as barely rounded on a 200 px cover: a share of the side.
    assert "property real artCornerK: 0.08" in src
    assert "function artCorner(side) { return Math.round(side * artCornerK) }" in src
    assert "radius: root.artCorner(ac.artSize)" in src
    assert "width: 180; height: 180; radius: root.artCorner(180)" in src


def test_the_rim_and_the_pill_keep_clear_of_the_corner() -> None:
    src = _src()
    # The rim stays the lab's width on a big cover instead of growing with it.
    assert "property real artPlayGlowMax: 3.5" in src
    assert "Math.min(root.artPlayGlowMax," in src
    # The hover pill runs parallel to the rounded corner.
    assert "pillRadius: Math.max(root.btnRad, acArt.faceRadius - 12)" in src
    assert "radius: rsw.pillRadius" in src


def test_the_hero_scrim_is_drawn_in_the_face() -> None:
    src = _src()
    # A scrim Rectangle over a rounded face laid a second edge on it and
    # showed as a gray slab; the face shades its own bottom instead.
    assert "visible: ac.hero && !root.roundFaces && opacity > 0" in src
    assert "shadeA: ac.hero && !acArt.controlsOn ? 0.9 : 0" in src
    assert src.count("shadeH: artRoot.shadeH; shadeA: artRoot.shadeA") == 2
    frag = SHADER.read_text(encoding="utf-8")
    assert "float shadeA;" in frag


def test_the_feed_ghost_wears_the_cards_face_and_frame() -> None:
    """A feed row's ghost twin (relRowGhost) is what the real row fades in
    over, so it draws its cover the way the card will: rounded through
    ArtFace at the card's corner, and framed when the release is fresh. A
    square, frameless twin showed as every Just in cover turning round."""
    src = _src()
    ghost = src.split('objectName: "relRowGhost"', 1)[1].split('objectName: "relGhostText"', 1)[0]
    assert "readonly property real corner: root.artCorner(root.relCardW)" in ghost
    assert "readonly property bool fresh: root.relIsFresh(modelData)" in ghost
    assert "ArtFace { src: ghostArt; radius: ghost.corner }" in ghost
    art = ghost.split("id: ghostArt", 1)[1].split("\n                                                        }", 1)[0]
    assert "opacity: root.roundFaces ? 0 : 1" in art
    frame = ghost.split('objectName: "relGhostFrame"', 1)[1]
    assert "visible: ghost.fresh && ghostArt.status === Image.Ready" in frame
    assert "radius: ghost.corner" in frame
    assert ghost.split('objectName: "relGhostFrame"', 1)[0].rstrip().endswith("CornerFrame {")


def _sd_corner(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index("float sdCorner(")
    return text[start : text.index("\n}\n", start) + 3]


def test_one_corner_profile_shapes_every_cover() -> None:
    """The corner is one profile (root.artCornerN, a superellipse at 3: no
    point where curve meets edge, and the cover stays square), read by the
    face, the playing rim and everything drawn over a cover. Change the one
    number and every cover follows."""
    src = _src()
    # Both shaders carry the same corner, word for word.
    assert _sd_corner(SHADER) == _sd_corner(BLEND)
    assert "sdRound" not in SHADER.read_text(encoding="utf-8") + BLEND.read_text(encoding="utf-8")
    assert "sdCorner(p, cover * 0.5, radius, shapeN)" in BLEND.read_text(encoding="utf-8")
    assert "property real artCornerN: 3" in src
    assert "readonly property real artCornerBez: (8 * Math.pow(2, -1 / Math.max(2, artCornerN)) - 4) / 3" in src
    assert "readonly property real artEdgeAA: 0.55 / (Screen.devicePixelRatio > 0 ? Screen.devicePixelRatio : 1)" in src
    face = _component("ArtFace")
    assert "readonly property real shapeN: root.artCornerN" in face
    assert "readonly property real aa: root.artEdgeAA" in face
    art = _component("Art")
    rim = art.split("id: glow", 1)[1].split("MultiEffect {", 1)[0]
    assert "readonly property real shapeN: root.artCornerN" in rim
    # The frames are the outline's, never a Rectangle's circular corner.
    assert src.count("CornerFrame {") == 3
    frame = _component("CornerFrame")
    assert "fillRule: ShapePath.OddEvenFill" in frame
    assert (
        "root.cornerPath(-cfr.outset, -cfr.outset, cfr.width + 2 * cfr.outset, cfr.height + 2 * cfr.outset, cfr.outerC)"
        in frame
    )
    assert "readonly property real innerC: cfr.outerC - (cfr.ring + cfr.outset) * root.artCornerInset" in frame
    # Half a pixel out only where the parent clips it back to the box.
    assert "outset: 0   // nothing clips the thumb" in src
    # Both scrims cut to it too.
    assert "PathSvg { path: root.cornerPath(0, 0, pbScrim.width, pbScrim.height, pb.radius) }" in src


def test_the_outline_cubic_sits_on_the_shaders_curve() -> None:
    """cornerPath() draws each quarter as one cubic with its control point
    at artCornerBez of the corner; within 0.1 px of the superellipse the
    shaders cut, on a 16 px corner, for the profile in use."""
    for n in (2.0, 3.0):
        k = (8 * 2 ** (-1 / n) - 4) / 3
        pts = [(0.0, 1.0), (k, 1.0), (1.0, k), (1.0, 0.0)]
        worst = 0.0
        for i in range(401):
            t = i / 400
            w = ((1 - t) ** 3, 3 * t * (1 - t) ** 2, 3 * t * t * (1 - t), t**3)
            x = sum(c * p[0] for c, p in zip(w, pts, strict=False))
            y = sum(c * p[1] for c, p in zip(w, pts, strict=False))
            worst = max(worst, abs((x**n + y**n) ** (1 / n) - 1))
        assert worst * 16 < 0.1, (n, worst * 16)
