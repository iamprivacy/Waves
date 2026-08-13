"""Regression guard: QML colour literals must not be 9 characters long.

THE BUG
-------
Qt parses a 9-character hex colour as **#AARRGGBB**, not #RRGGBBAA. Ten modal
scrims in Main.qml were written as ``#06070ecc`` / ``#06070ed6`` / ``#06070ef4``,
meaning "dark navy #06070e at 80/84/96% alpha". Qt read them as **alpha 0x06
(2.4%) over the blue rgb(7,14,204)**, so every modal gate in the app (download
folder, category confirm, settings reset, factory reset, folder unreachable,
FFmpeg, terms) and the login panel failed to dim the interface behind them.

The same file's other overlays get it right (``#d90d0f12`` → 85% alpha), so
this was a byte-order slip in one family of literals, not a convention.

THE FIX rewrote the ten literals as ``#cc06070e`` / ``#d606070e`` /
``#f406070e``. This guard is mechanical: any 9-character hex literal is
ambiguous to a reader and is exactly how the slip happened, so the rule is that
colours are either 7 characters (opaque) or 9 with the alpha FIRST. We cannot
tell intent from the literal alone, so instead we pin the property that broke:
no literal may have an alpha byte under 10% unless it is deliberately listed.
"""

from __future__ import annotations

import re
from pathlib import Path

QML_DIR = Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui" / "qml"

_HEX9 = re.compile(r'"#([0-9a-fA-F]{8})"')

# Literals whose leading (alpha) byte really is meant to be zero: fully
# transparent gradient stops, colour-matched to the opaque stop beside them so
# the interpolation does not fade through grey. Each of these sits in a
# Gradient next to a correctly-ordered sibling (#bf060810, #04140a, #e006210f),
# which is what proves the intent. Add here only after checking in context.
_DELIBERATE_FAINT: set[str] = {
    "00060810",  # banner legibility scrim, top and bottom stops
    "0004140a",  # marquee edge fade
    "0006210f",  # highlight row fade-out
    "0006090c",  # video thumbnail play scrim, top stop (opaque sibling #cc06090c)
}


def _qml_files() -> list[Path]:
    files = sorted(QML_DIR.rglob("*.qml"))
    assert files, f"no QML found under {QML_DIR}"
    return files


def test_no_nearly_transparent_colour_literals():
    """A 9-char literal whose FIRST byte is tiny is the byte-order slip: the
    author wrote #RRGGBBAA and Qt read #AARRGGBB."""
    offenders: list[str] = []
    for path in _qml_files():
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in _HEX9.finditer(line):
                literal = match.group(1)
                if literal in _DELIBERATE_FAINT:
                    continue
                alpha = int(literal[:2], 16)
                if alpha < 26:  # under ~10%: almost certainly a swapped literal
                    offenders.append(
                        f"{path.name}:{line_no} #{literal} renders at alpha "
                        f"{alpha} ({alpha / 255 * 100:.1f}%). Qt reads 9-char hex as "
                        f"#AARRGGBB, so write #{literal[6:]}{literal[:6]} instead."
                    )
    assert not offenders, "nearly-transparent colour literals:\n" + "\n".join(offenders)


def test_the_modal_scrims_actually_dim():
    """The specific family that broke: the scrims must be substantially opaque
    over the dark base colour, so a modal visibly dims what is behind it."""
    main = (QML_DIR / "Main.qml").read_text(encoding="utf-8")

    scrims = re.findall(r'"#([0-9a-fA-F]{2})06070e"', main)
    assert len(scrims) == 13, f"expected the 13 modal scrims, found {len(scrims)}"
    for alpha_hex in scrims:
        alpha = int(alpha_hex, 16)
        assert alpha >= 178, f"scrim alpha {alpha} (<70%) no longer dims the interface behind it"
