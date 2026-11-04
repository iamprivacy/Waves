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


def _src() -> str:
    return MAIN.read_text(encoding="utf-8")


def _component(name: str) -> str:
    src = _src()
    start = src.index(f"\n    component {name}:")
    end = src.index("\n    component ", start + 1)
    return src[start:end]


def test_the_shader_is_compiled_and_current() -> None:
    qsb = SHADER.with_suffix(".frag.qsb")
    assert SHADER.exists() and qsb.exists()
    assert qsb.stat().st_mtime >= SHADER.stat().st_mtime, "recompile cover_round.frag.qsb (command in its header)"
    text = SHADER.read_text(encoding="utf-8")
    # The crop is PreserveAspectCrop's, the corner the cover's, premultiplied out.
    assert "max(size.x / tex.x, size.y / tex.y)" in text
    assert "sdRound(p, 0.5 * size, radius)" in text
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
    assert "radius: arb.radius" in src
    assert src.count("radius: parent.faceRadius") == 2, "both ArtistBadges bars"
    assert "lit: thumbMa.containsMouse; radius: 6 }" in src
    gloss = _component("Art").split("// Gloss variant", 1)[1]
    assert gloss.count("PathArc") == 4


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
