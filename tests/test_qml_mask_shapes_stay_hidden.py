"""Regression guard: an item used only as a mask SHAPE is never drawn.

THE BUG
-------
Three places round off a layered effect by masking it with a plain white
rounded rectangle: the track download button's LED fill (``diGridMask``), the
preview ring (``paMask``) and the LED bar (``ledMask``). Each is handed to a
``MultiEffect`` through ``ShaderEffectSource { sourceItem: <mask> }``, which
reads the item whether or not the scene draws it, so the mask itself must stay
``visible: false``.

``diGridMask`` lost that line in 11ec500 (a boot-shield fix that flipped a
neighbouring ``visible: false`` to ``visible: enabled`` as collateral). Since
``enabled`` reads back the *effective* enabled state, which is true for every
button a user can press, the white rectangle was painted edge to edge inside
every enabled track download button: a white tile with a green arrow on it,
instead of the dark themed chip beside it. It shipped that way in the re-cut
v0.1.26 and was reported from a livetest.

The rule is mechanical, which is what makes it worth pinning: a mask shape is
geometry, not a thing on screen. If one ever does need to be drawn, it is not a
mask any more; give it its own item.
"""

from __future__ import annotations

import re
from pathlib import Path

QML_DIR = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml"

_SOURCE_ITEM = re.compile(r"ShaderEffectSource\s*\{[^}]*?sourceItem:\s*([A-Za-z_][A-Za-z0-9_]*)")


def _qml_files() -> list[Path]:
    return sorted(QML_DIR.glob("*.qml"))


def test_every_mask_source_item_is_hidden():
    checked = 0
    for path in _qml_files():
        text = path.read_text(encoding="utf-8")
        for mask_id in _SOURCE_ITEM.findall(text):
            decl = re.search(r"\bid:\s*%s\b" % re.escape(mask_id), text)
            assert decl, f"{path.name}: mask {mask_id} is used but never declared"
            # The declaration's own block: up to the next `id:` or the end.
            after = text[decl.end() : decl.end() + 600]
            block = re.split(r"\n\s*(?:id:|component )", after)[0]
            assert "visible: false" in block, (
                f"{path.name}: {mask_id} is a mask shape and must be `visible: false`; "
                "drawn, it paints its white ground over whatever it masks"
            )
            checked += 1
    assert checked >= 3, f"expected the three known mask shapes, found {checked}"
