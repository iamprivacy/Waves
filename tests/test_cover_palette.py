"""The playing glow's colors: a cover's distinct colors and their shares.

A playing cover glows in its own colors, blended through one soft glow
(shaders/cover_blend.frag), each weighted by how much of the cover it takes.
This pins the palette that feeds it (big vivid areas lead, one hue gives one
color, near-black gives nothing so the accent stays), the cache that keeps a
cover from being read twice, and the wiring from Art to the shader.
"""

from __future__ import annotations

import pathlib

from PySide6.QtGui import QColor, QGuiApplication, QImage

from waves.waves_ui import cover_palette
from waves.waves_ui.backend import WavesBridge

_QML = pathlib.Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml"

_app = QGuiApplication.instance() or QGuiApplication([])


def _cover(*bands: tuple[str, int]) -> QImage:
    """A 100 px square of horizontal bands, (color, rows) from the top."""
    img = QImage(100, 100, QImage.Format.Format_RGB32)
    y = 0
    for color, rows in bands:
        for row in range(y, y + rows):
            for x in range(100):
                img.setPixelColor(x, row, QColor(color))
        y += rows
    return img


def _hue(name: str) -> float:
    return QColor(name).hsvHueF()


def test_colors_come_most_used_first_with_their_shares() -> None:
    pal = cover_palette.palette(_cover(("#2050e0", 70), ("#e03020", 30)))
    assert len(pal["c"]) == 2
    assert abs(_hue(pal["c"][0]) - QColor("#2050e0").hsvHueF()) < 0.03
    assert abs(_hue(pal["c"][1]) - QColor("#e03020").hsvHueF()) < 0.03
    assert pal["w"][0] > pal["w"][1]
    assert abs(sum(pal["w"]) - 1) < 0.01


def test_colors_are_lifted_to_glow_brightness() -> None:
    pal = cover_palette.palette(_cover(("#401010", 100)))
    assert QColor(pal["c"][0]).valueF() >= 0.9


def test_shades_of_one_hue_give_one_color() -> None:
    pal = cover_palette.palette(_cover(("#20c040", 40), ("#30d050", 30), ("#18a838", 30)))
    assert len(pal["c"]) == 1


def test_a_black_cover_gives_no_colors() -> None:
    assert cover_palette.palette(_cover(("#050505", 100))) == {"c": [], "w": []}
    assert cover_palette.palette(QImage()) == {"c": [], "w": []}


def test_the_cache_drops_the_oldest() -> None:
    cache = cover_palette.PaletteCache(limit=2)
    cache.put("a", {"c": ["#111111"], "w": [1]})
    cache.put("b", {"c": ["#222222"], "w": [1]})
    assert cache.get("a") is not None  # a is now the newest
    cache.put("c", {"c": ["#333333"], "w": [1]})
    assert cache.get("b") is None
    assert cache.get("a") is not None
    assert cache.get("c") is not None


class _Stub:
    """Bare object the real slot gets bound onto."""


def test_the_bridge_reads_a_cover_once() -> None:
    stub = _Stub()
    stub._cover_palettes = cover_palette.PaletteCache()
    ask = WavesBridge.coverPalette.__get__(stub, _Stub)
    # Never seen and nothing to read: None, so QML knows to grab.
    assert ask("https://x/a.jpg", None) is None
    first = ask("https://x/a.jpg", _cover(("#e03020", 100)))
    assert first["c"]
    # Seen: answered from the cache, without an image.
    assert ask("https://x/a.jpg", None) == first


def test_art_draws_the_glow_from_its_own_colors() -> None:
    src = (_QML / "Main.qml").read_text(encoding="utf-8")
    art = src[src.index("    component Art: Rectangle {") : src.index("    component QualTag: Row {")]
    assert 'fragmentShader: "shaders/cover_blend.frag.qsb"' in art
    assert "waves.coverPalette(key, r.image)" in art
    # Grabbed only once raised (or asked for, as the bar's cover is), so a
    # page of idle covers reads nothing.
    assert "if ((!fxRaised && !palWanted) || fxPal !== null" in art
    # The compiled shader ships beside its source.
    assert (_QML / "shaders" / "cover_blend.frag.qsb").stat().st_size > 0
