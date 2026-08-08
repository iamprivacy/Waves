"""Guard: the queue drawer's per-track Repeater must not rebuild on live ticks.

THE BUG WE ARE FENCING OFF
--------------------------
While an album downloads, the backend streams per-track updates twice a
second (``queueTrackState`` on lifecycle changes, ``queueTrackPct`` from the
500ms progress poll), and each one reassigns ``root.queueTracks`` wholesale
with fresh array instances. The drawer's expanded/peeked track list used to
bind that array directly as its Repeater model, so EVERY tick tore down and
rebuilt every row delegate. Each rebuild needs a layout-settle frame, which
read as the hover-peek sliver visibly vibrating under the pointer (and burned
CPU rebuilding rows whose text barely changed).

HOW THIS STAYS FIXED
--------------------
The Repeater's model is the row COUNT, not the array: delegates stay alive
across ticks and each row re-evaluates its own snapshot (``td``) in place, so
a tick only repaints the texts that changed. This guard pins that shape in
Main.qml: the one Repeater that models ``root.queueTracks`` rows must bind
``.length``, never the raw array.
"""

from __future__ import annotations

import re
from pathlib import Path

QML_MAIN = Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui" / "qml" / "Main.qml"


def test_queue_track_repeater_model_is_count_stable():
    src = QML_MAIN.read_text(encoding="utf-8")
    # Every Repeater/ListView model line that reads queueTracks rows.
    model_lines = [line.strip() for line in src.splitlines() if re.search(r"^\s*model:.*root\.queueTracks\[", line)]
    assert model_lines, (
        "no model binding on root.queueTracks found in Main.qml; if the queue "
        "track list moved, update this guard rather than deleting it"
    )
    for line in model_lines:
        assert ".length" in line, (
            "the queue track list binds root.queueTracks rows directly as its "
            "model again; every live tick (queueTrackState/Pct) reassigns that "
            "array, and an array model rebuilds every delegate per tick, which "
            "reads as the hover-peek sliver vibrating. Bind the COUNT and read "
            f"rows via index instead. Offending line: {line}"
        )
