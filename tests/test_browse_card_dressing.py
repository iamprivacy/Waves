"""A browse card carries its library verdict, and only while that verdict holds.

WHAT THIS FENCES OFF
--------------------
A browse page is built on a worker thread and each of its album cards is
"dressed" there with the answers the card would otherwise ask the bridge for
at creation time: whether the album is already in the local library, and
whether it has been downloaded before. That exists because a bridge call
inside a delegate's incubation slice waits for the interpreter behind every
busy worker, which at launch was most of what the boot water dropped frames
on. Two things about doing the work early:

1. The verdict is baked BEFORE the card exists. A card that is built after a
   library publish that the payload was dressed before holds an answer from
   the wrong index, and the signal that makes a live card re-ask has already
   fired, so it keeps that answer until the next publish, which for a settled
   library never comes: the card prints DOWNLOAD over an album on disk. Each
   card carries the publish count its verdict came from and compares it.

2. The dressing is one library lookup and one ownership lookup per card, so a
   page of fifty is a real span of time after the caller's own staleness
   check. A sign-out and sign-in inside it painted the previous account's
   personalised rows over the new account's page.
"""

from __future__ import annotations

from waves.waves_ui.backend import WavesBridge

_METHODS = ("_dress_card", "_dress_cards", "_emit_dressed")


class _Stub:
    _CARD_DRESS_KINDS = WavesBridge._CARD_DRESS_KINDS


for _m in _METHODS:
    setattr(_Stub, _m, getattr(WavesBridge, _m))


class _Emits:
    def __init__(self):
        self.sent = []

    def emit(self, payload):
        self.sent.append(payload)


def _stub(*, stamp=0, presence=None, on_presence=None):
    s = _Stub()
    s._library_stamp = stamp
    s._browse_gen = 7
    s.libraryAlbumPresence = lambda *a, **k: (on_presence() if on_presence else None) or (
        presence or {"present": False}
    )
    s.collectionOwnership = lambda _id: False
    return s


def _page(n=1):
    return {
        "key": "p",
        "sections": [
            {
                "rowKind": "cards",
                "items": [{"kind": "album", "id": f"al-{i}", "title": f"Album {i}", "artist": "A"} for i in range(n)],
            }
        ],
    }


def _cards(payload):
    return payload["sections"][0]["items"]


def test_a_dressed_card_says_which_published_index_answered_it():
    s = _stub(stamp=3, presence={"present": True, "full": True})
    card = _cards(s._dress_cards(_page()))[0]
    assert card["lib"] == {"present": True, "full": True}
    assert card["libStamp"] == 3, "the card cannot tell whether its answer is still current"


def test_the_card_the_payload_came_from_is_left_undressed():
    """The cache holds the page, and a verdict persisted with it would be a
    stale one on the next launch."""
    page = _page()
    s = _stub(stamp=1, presence={"present": True})
    s._dress_cards(page)
    assert "lib" not in _cards(page)[0]
    assert "libStamp" not in _cards(page)[0]


def test_a_card_that_is_not_an_album_wears_no_album_verdict():
    s = _stub(stamp=1, presence={"present": True})
    page = _page()
    _cards(page)[0]["kind"] = "playlist"
    card = _cards(s._dress_cards(page))[0]
    assert "lib" not in card and "libStamp" not in card
    assert card["own"] is False


def test_a_page_dressed_for_one_account_is_never_emitted_to_another():
    """The emit happens after the dressing, so the account is re-read last.
    Every caller checks before the dressing; nothing checked after it."""
    signal = _Emits()
    s = _stub(stamp=1)

    def relogin():
        s._browse_gen = 8  # the sign-in flip, one card into the dressing

    s.libraryAlbumPresence = lambda *a, **k: (relogin(), {"present": False})[1]

    s._emit_dressed(signal, _page(n=3), 7)

    assert signal.sent == [], "the previous account's page was painted over the new one's"


def test_a_page_still_current_is_emitted_dressed():
    signal = _Emits()
    s = _stub(stamp=4, presence={"present": True})

    s._emit_dressed(signal, _page(n=2), 7)

    assert len(signal.sent) == 1
    for card in _cards(signal.sent[0]):
        assert card["libStamp"] == 4


def test_the_cards_in_the_qml_compare_the_stamp_before_trusting_the_answer():
    """The Python half above is only half the fix: the baked verdict is read
    back in Main.qml, and reading it without comparing the stamp is exactly
    the state this exists to end. Checked on the source because the miss
    needs a publish to land inside a delegate's incubation slice, which is
    not a thing a scenario can arrange.

    Both card styles carry it: the art card on a shelf and the console
    card in the list style."""
    import re
    from pathlib import Path

    qml = (Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml").read_text()
    uses = re.findall(r'\(!live && \("lib" in c\)[^)]*\)', qml)
    assert len(uses) == 2, f"expected both card styles to read the baked verdict, found {len(uses)}"
    for use in uses:
        assert "c.libStamp === root.libStamp" in use, f"a card trusts a verdict it cannot date: {use}"
    assert "root.libStamp = waves.libraryStamp()" in qml, "nothing refreshes the window's stamp on a publish"
