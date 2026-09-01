"""Exporting a diagnostic report must not rewrite the user's privacy prefs.

THE BUG
-------
The two diagnostics toggles apply live via ``setWavesPref`` but never set
``needsRefresh``, and ``refreshSchema()`` is the only thing that re-reads
``waves.settingsSchema()``. ``onActiveChanged`` clears ``editMap`` on every
reopen but rebuilds ``groups`` only when ``needsRefresh`` is set, which only the
Save button does. So after one close and reopen the card rendered the stale
baked-in values, and the EXPORT REPORT handler pushed **those** booleans back
through ``setWavesPref`` before calling ``exportDiagnostics()``, which reads the
pref it had just clobbered.

The card's own copy invites exactly that sequence: "Turn on, reproduce the
problem, then export." The user turned on verbose diagnostics and content
redaction, reproduced their bug, came back, clicked Export, and silently got
both switched off, the freeze watchdog and perf sampler stopped, and a bundle
containing the searches and titles they had asked to hide.

THE FIX has two halves: the export handler no longer re-pushes the prefs (the
toggles already apply live, so the backend holds the truth), and each toggle
marks ``needsRefresh`` so a reopen shows the real value instead of the stale
one.
"""

from __future__ import annotations

import re
from pathlib import Path

QML = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "SettingsPage.qml"

_DIAG_PREFS = ("verbose_diagnostics", "diagnostics_redact_content")


def _source() -> str:
    """The QML with ``//`` comments stripped: a comment explaining why a call
    was removed must not read as the call still being there."""
    lines = QML.read_text(encoding="utf-8").splitlines()
    return "\n".join(re.sub(r"//.*$", "", line) for line in lines)


def _handler_body(source: str, anchor: str) -> str:
    """The braces-balanced ``onClicked: { ... }`` block containing ``anchor``."""
    index = source.index(anchor)
    start = source.rindex("onClicked: {", 0, index)
    depth = 0
    for offset, char in enumerate(source[start:], start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : offset + 1]
    raise AssertionError("unbalanced onClicked block")


def test_export_does_not_rewrite_diagnostics_prefs_from_a_stale_schema():
    """The export handler must call exportDiagnostics and nothing that writes."""
    body = _handler_body(_source(), "waves.exportDiagnostics()")

    assert "setWavesPref" not in body, (
        "the export handler writes the diagnostics prefs before exporting; after a "
        "close-and-reopen it writes the STALE schema value and silently turns the "
        "user's choices off"
    )
    for key in _DIAG_PREFS:
        assert key not in body, f"the export handler still touches {key}"


def test_live_applied_diagnostics_toggles_mark_the_schema_stale():
    """A pref applied without a Save leaves `groups` stale, so the toggle must
    request a rebuild or the card keeps rendering the pre-change value."""
    source = _source()

    for key in _DIAG_PREFS:
        # The toggle handler is the one that calls setWavesPref for this key.
        anchor = f'waves.setWavesPref("{key}"'
        assert anchor in source, f"{key} is no longer applied live; update this guard"
        body = _handler_body(source, anchor)

        assert "page.setv(" in body, f"the {key} toggle no longer updates the edit map"
        assert re.search(r"needsRefresh\s*=\s*true", body), (
            f"the {key} toggle applies live but does not set needsRefresh, so reopening "
            "Settings renders the stale baked-in value"
        )


def test_the_two_prefs_are_only_written_from_their_own_toggles():
    """Belt and braces: exactly one write site per pref, so no other handler
    can push a stale value back at the backend."""
    source = _source()
    for key in _DIAG_PREFS:
        writes = len(re.findall(rf'setWavesPref\(\s*"{re.escape(key)}"', source))
        assert writes == 1, f"expected exactly one setWavesPref site for {key}, found {writes}"
