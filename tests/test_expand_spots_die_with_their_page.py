"""A remembered scroll spot never outlives the page it was made on.

WHAT THIS FENCES OFF
--------------------
Expanding an album row remembers the contentY the view left, so collapsing it
can glide back there (see test_album_collapse_returns_the_view). That spot only
means something on the page it was measured on. Every wholesale replacement of
the expanded set (a new search, an artist page loading, a Back restore) has to
drop the spots with it: left behind, a spot outlives its page, and a later
collapse of a row with the same id glides the view to a contentY that belonged
to a page the user has already left.

The rule is structural rather than remembered: `resetExpandedAlbums` is the
only writer that may replace the map wholesale, and it clears the spots in the
same breath. The one other writer is AlbumBlock.toggle, which earns its spots.
"""

from __future__ import annotations

import re
from pathlib import Path

QML = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"

# The two writers the rule allows: the reset helper's own line, and the toggle
# that adds or removes a single row.
_ALLOWED = {"expandedAlbums = map || ({})", "root.expandedAlbums = e"}


def _assignments(src: str) -> list[str]:
    return [m.group(0).strip() for m in re.finditer(r"(?:root\.)?expandedAlbums\s*=\s*[^\n]+", src)]


def test_every_wholesale_write_of_the_expanded_set_goes_through_the_reset():
    src = QML.read_text(encoding="utf-8")
    stray = [a for a in _assignments(src) if a not in _ALLOWED]
    assert not stray, (
        "an expanded-album set is replaced without dropping the remembered scroll spots; "
        "call root.resetExpandedAlbums(map) instead: " + "; ".join(stray)
    )


def test_the_reset_drops_the_spots_it_was_written_for():
    src = QML.read_text(encoding="utf-8")
    body = src.split("function resetExpandedAlbums(", 1)
    assert len(body) == 2, "resetExpandedAlbums is gone; the wholesale-write rule has no home"
    head = body[1][:400]
    assert "expandedAlbums = map || ({})" in head, "the reset no longer replaces the expanded set"
    assert "expandReturnY = ({})" in head, "the reset no longer drops the remembered scroll spots"


def test_the_spots_map_is_only_cleared_by_that_reset():
    """A second clear site would mean the pair can come apart again."""
    src = QML.read_text(encoding="utf-8")
    clears = [m.group(0).strip() for m in re.finditer(r"(?:root\.)?expandReturnY\s*=\s*[^\n]+", src)]
    assert clears == ["expandReturnY = ({})"], f"the remembered spots are reset somewhere else too: {clears}"
