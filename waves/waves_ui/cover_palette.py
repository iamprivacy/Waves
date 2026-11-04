"""A cover's distinct colors and how much of it each one takes.

Feeds the playing glow (shaders/cover_blend.frag), which melts a cover's
colors into one soft glow, each weighted by its share of the cover. The
cover arrives as a small grab of the Art already on screen, so nothing is
fetched or decoded twice, and one pass over 40 x 40 pixels takes about a
millisecond, cheap enough for the GUI thread once per cover.
"""

from __future__ import annotations

import colorsys
from collections import OrderedDict

from PySide6.QtGui import QColor, QImage

_SIDE: int = 40
_HUE_BUCKETS: int = 16
_MAX_COLORS: int = 5
_MIN_SHARE: float = 0.02
# Near-black is skipped (it is the cover's shadow, not its color); below
# this saturation a pixel counts as gray rather than as a weak hue.
_DARK: float = 0.12
_GRAY: float = 0.18
# Every color is lifted to glow brightness: the glow is light, and a dark
# cover's colors would otherwise glow as a muddy smudge.
_GLOW_VALUE: float = 0.92


def palette(image: QImage) -> dict[str, list]:
    """``{"c": ["#rrggbb", ...] most used first, "w": [share, ...]}``.

    Hue buckets weighted by saturation and value, so a cover's big vivid
    areas lead; neighboring hues fold into one color, so a cover that is
    all one hue gives one color, not four shades of it. A cover with no
    usable color at all (black, or a failed grab) gives an empty palette,
    and the caller keeps its own fallback.
    """
    if image is None or image.isNull():
        return {"c": [], "w": []}
    img = image.scaled(_SIDE, _SIDE).convertToFormat(QImage.Format.Format_RGB32)
    raw = bytes(img.constBits())
    bins: dict[int, list[float]] = {}
    # Format_RGB32 is 0xffRRGGBB in native order: B, G, R, A on every
    # platform Qt ships for.
    for i in range(0, len(raw), 4):
        r, g, b = raw[i + 2] / 255, raw[i + 1] / 255, raw[i] / 255
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        if v < _DARK:
            continue
        key = -1 if s < _GRAY else int(h * _HUE_BUCKETS) % _HUE_BUCKETS
        weight = (0.15 + s) * v
        acc = bins.setdefault(key, [0.0, 0.0, 0.0, 0.0])
        acc[0] += r * weight
        acc[1] += g * weight
        acc[2] += b * weight
        acc[3] += weight

    picked: list[tuple[int, list[float]]] = []
    for key, acc in sorted(bins.items(), key=lambda kv: -kv[1][3]):
        near = next(
            (p for p in picked if key >= 0 and p[0] >= 0 and min(abs(key - p[0]), _HUE_BUCKETS - abs(key - p[0])) <= 1),
            None,
        )
        if near:
            for j in range(4):
                near[1][j] += acc[j]
        elif len(picked) < _MAX_COLORS:
            picked.append((key, list(acc)))

    total = sum(p[1][3] for p in picked) or 1.0
    colors: list[str] = []
    shares: list[float] = []
    for _, acc in sorted(picked, key=lambda p: -p[1][3]):
        if acc[3] / total < _MIN_SHARE:
            continue
        h, s, v = colorsys.rgb_to_hsv(acc[0] / acc[3], acc[1] / acc[3], acc[2] / acc[3])
        s = min(1.0, s * 1.15) if s >= _GRAY else s
        colors.append(QColor.fromHsvF(h, s, max(v, _GLOW_VALUE)).name())
        shares.append(round(acc[3] / total, 3))
    return {"c": colors, "w": shares}


class PaletteCache:
    """Palettes by cover URL, the oldest dropped past ``limit``."""

    def __init__(self, limit: int = 400) -> None:
        self._limit: int = limit
        self._items: OrderedDict[str, dict[str, list]] = OrderedDict()

    def get(self, key: str) -> dict[str, list] | None:
        found = self._items.get(key)
        if found is not None:
            self._items.move_to_end(key)
        return found

    def put(self, key: str, value: dict[str, list]) -> None:
        self._items[key] = value
        self._items.move_to_end(key)
        while len(self._items) > self._limit:
            self._items.popitem(last=False)
