"""Every ``waves.<name>`` the QML calls really is reachable from QML.

A Python method on the bridge is only visible to QML when it carries a
``@Slot`` decorator: without one it never reaches ``staticMetaObject``, the
context property has no such member, and the call throws a TypeError at the
moment the user triggers it. Nothing catches that at import time, and a unit
test that binds the unbound function onto a plain stub (which is how the
bridge tests are written, see tests/conftest.py) passes either way, so a whole
feature can ship inert with a green suite. That happened: prefetchArtist
shipped without its decorator and every artist-card hover threw.

This is the gate for it. It reads the QML, collects every ``waves.NAME``
reference, and asserts each one is a real member of the bridge's meta object.
"""

from __future__ import annotations

import re
from pathlib import Path

from waves.waves_ui.backend import WavesBridge

_QML_DIR = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml"
_REFERENCE = re.compile(r"\bwaves\.([A-Za-z_]\w*)")


def _code_only(source: str) -> str:
    """``source`` with its comments and string bodies blanked out.

    Both matter. A prose comment in Main.qml mentions ``waves.json`` (the
    settings file, not a bridge member), which a naive regex reads as a missing
    slot; and a URL inside a string carries a ``//`` that a line-based comment
    strip would mistake for the start of a comment, swallowing whatever real
    code followed it on that line. Walking the characters once handles both.
    """
    out: list[str] = []
    i, n = 0, len(source)
    while i < n:
        ch = source[i]
        if ch in ("'", '"', "`"):
            quote = ch
            i += 1
            while i < n and source[i] != quote:
                i += 2 if source[i] == "\\" else 1
            i += 1
            out.append('""')
            continue
        if ch == "/" and i + 1 < n:
            if source[i + 1] == "/":
                while i < n and source[i] != "\n":
                    i += 1
                continue
            if source[i + 1] == "*":
                end = source.find("*/", i + 2)
                i = n if end < 0 else end + 2
                out.append(" ")
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _bridge_members() -> set[str]:
    """Every name QML can resolve on the bridge: invokable methods and signals
    from the meta object, plus its properties.

    Methods and properties are read differently on purpose. A method name comes
    back as a QByteArray, which decodes; a property name comes back as ``str``
    already, and calling ``bytes()`` on it raises.
    """
    mo = WavesBridge.staticMetaObject
    names = {bytes(mo.method(i).name()).decode() for i in range(mo.methodCount())}
    names |= {str(mo.property(i).name()) for i in range(mo.propertyCount())}
    return names


def test_every_qml_call_into_the_bridge_resolves():
    members = _bridge_members()
    missing: dict[str, set[str]] = {}
    for qml in sorted(_QML_DIR.rglob("*.qml")):
        used = set(_REFERENCE.findall(_code_only(qml.read_text(encoding="utf-8"))))
        absent = used - members
        if absent:
            missing[qml.name] = absent
    assert not missing, "QML calls bridge members that QML cannot see (missing @Slot or @Property): %r" % missing


def test_the_hover_prefetch_slots_are_the_ones_that_regressed():
    """The three hover-warm entry points, pinned by name.

    The general gate above only fires while the QML still calls them. These
    stay red even if a refactor drops the call site, because the decorator
    going missing is the defect, not the call.
    """
    members = _bridge_members()
    for name in ("prefetchArtist", "prefetchAlbumTracks", "prefetchBrowseItem"):
        assert name in members, f"{name} is not reachable from QML"
