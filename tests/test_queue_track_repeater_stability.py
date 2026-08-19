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
The Repeater's model is a row COUNT, not the array: delegates stay alive
across ticks and each row re-evaluates its own snapshot (``td``) in place, so
a tick only repaints the texts that changed.

The count now arrives through an int property on the row rather than inline on
the model line, because it is also where the ledger's ceiling is applied (see
test_queue_ledger_cap.py). That is the same shape, so this guard checks it
structurally rather than by matching one expression: whatever the ledger's
model names must be declared an int, and anything deriving a count from
``root.queueTracks`` must take ``.length`` rather than carrying the array.
"""

from __future__ import annotations

import re
from pathlib import Path

QML_MAIN = Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui" / "qml" / "Main.qml"

# The ledger row delegate is the only thing that carries this name, so it is
# the anchor for finding the ledger's own Repeater without pinning a line
# number or the name of the property the model happens to bind today.
LEDGER_ROW_MARKER = 'objectName: "qTrackTitle"'


def _ledger_model_line(src: str) -> str:
    """The `model:` binding of the Repeater whose delegate is a ledger row."""
    head = src[: src.index(LEDGER_ROW_MARKER)]
    models = [line.strip() for line in head.splitlines() if re.match(r"^\s*model:", line)]
    assert models, (
        "no model binding found above the ledger row delegate in Main.qml; if "
        "the queue track list moved, update this guard rather than deleting it"
    )
    return models[-1]


def test_the_ledger_model_is_a_count_not_the_array():
    src = QML_MAIN.read_text(encoding="utf-8")
    line = _ledger_model_line(src)

    # Bound inline: it must count. Bound through a property: that property must
    # be declared an int, which a queueTracks array cannot satisfy.
    if "root.queueTracks[" in line:
        assert ".length" in line, (
            "the queue track list binds root.queueTracks rows directly as its "
            "model again; every live tick (queueTrackState/Pct) reassigns that "
            "array, and an array model rebuilds every delegate per tick, which "
            f"reads as the hover-peek sliver vibrating. Offending line: {line}"
        )
        return

    m = re.match(r"^model:\s*(?:\w+\.)?(\w+)\s*$", line)
    assert m, (
        "the ledger's model is neither a counted queueTracks binding nor a "
        f"plain property reference, so this guard can no longer read it: {line}"
    )
    name = m.group(1)
    assert re.search(rf"^\s*readonly property int {name}\b", src, re.M), (
        f"the ledger's model binds {name!r}, which is not declared "
        "`readonly property int`. A model that is not a count is an array "
        "model, and an array model rebuilds every delegate on every live tick."
    )


def test_every_count_taken_from_queue_tracks_uses_length():
    """Whatever feeds that int must count rows, not carry them."""
    src = QML_MAIN.read_text(encoding="utf-8")
    counts = [
        line.strip()
        for line in src.splitlines()
        if re.match(r"^\s*(?:model:|readonly property int \w+:)", line) and "root.queueTracks[" in line
    ]
    assert counts, (
        "nothing derives a count from root.queueTracks in Main.qml any more; "
        "if the queue track list moved, update this guard rather than deleting it"
    )
    for line in counts:
        assert ".length" in line, (
            "a queue-track count binds the rows array itself rather than its "
            f"length, so the delegates rebuild whenever a tick replaces it: {line}"
        )
