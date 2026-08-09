"""CANCEL on the settings page discards edits without leaving the page.

CANCEL used to emit ``closed()``, which dropped the user back to Browse. That
reads as "get me out of here" rather than "undo what I typed": you lost your
place, and the page you were reading, to take back a single keystroke. It now
throws away the pending edits in place.

Two things are easy to lose in a later edit and are pinned here:

- it must NOT close the page (no ``closed()`` from the button);
- it must re-pull the schema, because a save earlier in the same visit leaves
  ``groups`` holding pre-save values (``applySettings`` deliberately keeps
  ``editMap`` so the controls show what was just saved), so dropping
  ``editMap`` alone would repaint the page with stale text;
- and it must hold the scroll position while those cards rebuild, which is the
  app-wide no-visible-scroll rule.
"""

from __future__ import annotations

import pathlib
import re

_QML = pathlib.Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui" / "qml" / "SettingsPage.qml"


def _source() -> str:
    return _QML.read_text()


def _cancel_button() -> str:
    """The CANCEL button's whole block, from its Rectangle to its onClicked."""
    src = _source()
    start = src.index("width: cancelTxt.width")
    end = src.index("onClicked", start)
    return src[start : src.index("\n", end)]


class TestCancelDiscardsInPlace:
    def test_the_button_calls_discard_not_close(self):
        block = _cancel_button()

        assert "page.discardEdits()" in block
        assert "page.closed()" not in block

    def test_nothing_on_the_page_closes_it_any_more(self):
        # The host still handles closed() for a programmatic close; the page
        # itself must not emit it, or CANCEL's old behavior creeps back in.
        body = _source()
        body = body[body.index("signal closed()") + len("signal closed()") :]

        assert not re.search(r"\bclosed\(\)", body)

    def test_discard_refreshes_the_schema_and_keeps_the_place(self):
        src = _source()
        fn = src[src.index("function discardEdits") :]
        fn = fn[: fn.index("\n    }")]

        # Arm the restore BEFORE the rebuild: the re-measure is what would
        # otherwise clamp the page to the top.
        assert fn.index("pendingY = settingsFlick.contentY") < fn.index("refreshSchema()")
        assert "editMap = ({})" in fn
        assert "dirty = false" in fn
        assert "_restoreScroll()" in fn

    def test_cancel_is_inert_with_nothing_to_discard(self):
        block = _cancel_button()

        assert "opacity: page.dirty ? 1 : 0.4" in block
        assert "enabled: page.dirty" in block
