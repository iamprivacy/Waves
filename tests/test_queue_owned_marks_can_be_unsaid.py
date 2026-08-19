"""A queued row can take back "you already have this" once it stops being true.

WHAT THIS FENCES OFF
--------------------
Expanding a queued album runs a second pass that says which of its tracks you
already hold, and the drawer paints those rows IN LIBRARY. That answer used to
be one-way. The worker only handed the answer to the GUI thread when it found
something:

    if marks:
        self._queueOwnedFetched.emit(qid, marks)

_apply_owned_marks is the ONLY writer of the kept marks, so an answer of "none
of these, actually" never arrived and never replaced the previous one. Every
later merge re-stamped the old marks, and the store is only emptied when the
row leaves the queue.

What that looked like to a person: expand a queued album, three songs say IN
LIBRARY. Delete those files, or the drive holding them drops off, or switch the
library scan off. Collapse and re-expand, which is the one gesture that asks the
question again. The answer correctly comes back empty, the guard swallowed it,
and the drawer painted the three stale marks straight back. Nothing but quitting
the app cleared them, which is exactly the restart-to-refresh pattern this
project forbids.

The answer now reaches the GUI thread on every SUCCESSFUL lookup, an empty one
included. A lookup that RAISED still says nothing: it knows nothing either way,
so it leaves whatever is on screen alone. Both halves are pinned here.

Two neighbouring promises are pinned with them, because an "un-say" that keeps
every assertion above can still be built wrong in either direction: the empty
answer must un-say marks for the row it is about and no other queued row, and
the lookup must stay the SECOND pass, so a slow drive delays the marks and never
the track list itself.

HOW THIS IS DRIVEN
------------------
The carcass is a real WavesBridge (``__new__`` plus ``QObject.__init__``, so the
class's own Qt signals work and nothing else from ``__init__`` runs). The REAL
signals are used rather than stand-ins, for two reasons: an empty answer has to
survive the real QVariant hop to be worth anything, and the marks then land in
the real _apply_owned_marks and the real _merge_queue_tracks, so these tests can
assert on the rows the drawer would paint instead of on a dict. Recorders are
connected alongside. The carcass connects the signals the way ``__init__`` does,
so the last test pins that wiring on the real class.

Only _predict_skips is replaced: what the lookup answers is its own subject
(tests/test_queue_owned_prediction.py). What is pinned here is what
loadQueueTracks does with the answer it gets back.
"""

from __future__ import annotations

import ast
import inspect
import os
import textwrap
from types import SimpleNamespace

import pytest
from conftest import _InlinePool

_QID = 7
# A second queued album, so "un-say" can be held to the row it is about.
_OTHER_QID = 9


class _Album:
    """A queued album object, with the one method the fetch asks it for."""

    def __init__(self, track_ids):
        self._tracks = [SimpleNamespace(id=t, name=f"Song {t}", duration=200) for t in track_ids]

    def tracks(self):
        return list(self._tracks)


class _Prediction:
    """Stands in for _predict_skips: the answer is the input to this file's
    subject, not its subject. Set ``answer`` for a lookup that succeeded, set
    ``error`` for one that fell over (a mount going away mid-lookup)."""

    def __init__(self):
        self.answer: dict = {}
        self.error: BaseException | None = None
        self.calls = 0

    def __call__(self, qid, item, tracks):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return dict(self.answer)


def _bridge(track_ids=("t1", "t2"), other_track_ids=("u1",)):
    """A bridge with two queued albums that expand synchronously, plus recorders
    for what the GUI thread would have received."""
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore

    from tidaler.waves_ui import backend as be

    b = be.WavesBridge.__new__(be.WavesBridge)
    QtCore.QObject.__init__(b)
    b._queue_index = {
        _QID: {"qid": _QID, "media_id": "a1", "type": "album", "collection": True},
        _OTHER_QID: {"qid": _OTHER_QID, "media_id": "a2", "type": "album", "collection": True},
    }
    b._objs = {"album": {"a1": _Album(track_ids), "a2": _Album(other_track_ids)}}
    # The fetch runs on the calling thread, so an expansion is finished by the
    # time loadQueueTracks returns.
    b.threadpool = _InlinePool()
    b._job_tracks = {}
    b._job_owned = {}
    b._job_fetched = {}
    predict = _Prediction()
    b._predict_skips = predict

    owned: list[tuple[int, dict]] = []
    rows: list[tuple[int, list]] = []
    # One ordered log of both arrivals, so a test can say which of the two
    # passes reached the GUI thread first.
    events: list[str] = []

    def _note_owned(qid, marks):
        events.append("owned")
        owned.append((qid, marks))

    def _note_rows(qid, out):
        events.append("tracks")
        rows.append((qid, out))

    # Recorded before the real slot runs, so the log holds arrival order and not
    # the order the slots happen to finish in.
    b._queueOwnedFetched.connect(_note_owned)
    # The two connections WavesBridge.__init__ makes, in its order.
    b._queueTracksFetched.connect(b._merge_queue_tracks)
    b._queueOwnedFetched.connect(b._apply_owned_marks)
    b.queueTracksLoaded.connect(_note_rows)
    return b, SimpleNamespace(predict=predict, owned=owned, rows=rows, events=events)


def _shown(rec, track_id, qid=_QID):
    """The row the drawer would paint for one track, taken from the last list
    the GUI thread was handed for that queued album."""
    rows = next(out for q, out in reversed(rec.rows) if q == qid)
    return next(r for r in rows if r["id"] == track_id)


def _mark(kind="own", tier="LOSSLESS"):
    return {"kind": kind, "tier": tier}


def test_an_empty_answer_reaches_the_gui_and_clears_the_marks():
    """The files are gone, so the lookup finds nothing, and that "nothing" has
    to travel: it is the only thing that can un-say a mark."""
    b, rec = _bridge()
    # What an earlier expansion left behind, back when the copies existed.
    b._job_owned[_QID] = {"t1": _mark()}
    rec.predict.answer = {}

    b.loadQueueTracks(_QID)

    assert rec.owned, "an empty ownership answer never left the worker"
    # The whole list, not just the last entry: one expansion asks once and so
    # answers once.
    assert rec.owned == [(_QID, {})], "the GUI thread was handed something other than the empty answer"
    assert b._job_owned[_QID] == {}, "the kept marks still claim a copy the lookup no longer finds"


def test_re_expanding_takes_the_stale_mark_off_the_list_the_drawer_shows():
    """The whole round trip, in the user's own gesture: expand (one song says
    IN LIBRARY), lose the copy, collapse and expand again. The song must come
    back plain."""
    b, rec = _bridge()
    rec.predict.answer = {"t1": _mark()}

    b.loadQueueTracks(_QID)
    first = _shown(rec, "t1")
    assert first["status"] == "owned", "the first expansion never marked the song at all"
    assert first["owned"] == "own"

    # The copy goes away between the two expansions: deleted, or on a drive
    # that dropped, or the library scan switched off. The lookup now finds
    # nothing.
    rec.predict.answer = {}
    b.loadQueueTracks(_QID)

    assert rec.predict.calls == 2, "re-expanding did not ask the question again"
    again = _shown(rec, "t1")
    assert again["status"] == "pending", "the drawer painted the stale IN LIBRARY mark straight back"
    assert again["owned"] == "", "the row still carries the kind of copy it no longer has"
    assert again["quality"] == "", "the row still shows the tier of a copy that is gone"


def test_a_lookup_that_fell_over_leaves_the_marks_alone():
    """A failed lookup knows nothing either way, so it must not wipe marks that
    may still be perfectly true. This is the distinction the fix keeps."""
    b, rec = _bridge()
    b._job_owned[_QID] = {"t1": _mark()}
    rec.predict.answer = {"t1": _mark()}
    rec.predict.error = RuntimeError("the drive stopped answering mid-lookup")

    b.loadQueueTracks(_QID)

    assert rec.owned == [], "a failed lookup spoke anyway"
    assert b._job_owned[_QID] == {"t1": _mark()}, "a failed lookup threw away marks it knew nothing about"
    assert _shown(rec, "t1")["status"] == "owned", "the drawer dropped a mark on a lookup that simply failed"


def test_an_answer_that_did_find_copies_still_arrives():
    """The ordinary case, unchanged: what the lookup found is kept and painted."""
    b, rec = _bridge()
    rec.predict.answer = {"t2": _mark(kind="claim", tier="HI-RES")}

    b.loadQueueTracks(_QID)

    assert rec.owned[-1] == (_QID, {"t2": _mark(kind="claim", tier="HI-RES")})
    assert b._job_owned[_QID] == {"t2": _mark(kind="claim", tier="HI-RES")}
    marked = _shown(rec, "t2")
    assert (marked["status"], marked["owned"], marked["quality"]) == ("owned", "claim", "HI-RES")
    assert _shown(rec, "t1")["status"] == "pending", "a track the lookup never named was marked too"


def test_one_albums_marks_going_away_leaves_the_other_queued_albums_alone():
    """Un-saying is per row. Two albums are queued and expanded, the copies
    behind the first one vanish, and its next expansion says "none". The second
    album's songs are still sitting on disk, so its rows must go on saying IN
    LIBRARY."""
    b, rec = _bridge()

    rec.predict.answer = {"u1": _mark()}
    b.loadQueueTracks(_OTHER_QID)
    assert _shown(rec, "u1", _OTHER_QID)["status"] == "owned", "the second album was never marked to begin with"

    # Only the FIRST album's copies go away, so only its lookup comes back
    # empty. The second album is not asked about at all.
    rec.predict.answer = {}
    b.loadQueueTracks(_QID)

    kept = b._job_owned.get(_OTHER_QID)
    assert kept == {"u1": _mark()}, "un-saying one album's marks threw away another album's as well"
    # The merge the GUI thread runs whenever the second album's list is handed
    # over again, driven here directly because nothing else would repaint it.
    b._merge_queue_tracks(_OTHER_QID, b._job_fetched[_OTHER_QID])
    still = _shown(rec, "u1", _OTHER_QID)
    assert still["status"] == "owned", "the second album's song lost a mark that its own lookup never withdrew"


def test_the_songs_reach_the_drawer_before_the_ownership_answer_does():
    """The ownership lookup stats the disk, so it stays the second pass. Ask it
    first and a sleeping drive holds up the list itself: you expand an album and
    the drawer sits empty until the disk answers, instead of showing the songs
    at once and marking them a moment later."""
    b, rec = _bridge()
    rec.predict.answer = {"t1": _mark()}

    b.loadQueueTracks(_QID)

    assert rec.events, "the expansion delivered nothing at all"
    assert rec.events[0] == "tracks", "the drawer had to wait for the disk lookup before it could show any songs"
    assert "owned" in rec.events, "the ownership answer never followed the list"


def test_the_bridge_wires_the_owned_answer_into_the_marks_it_keeps():
    """The tests above connect the signal themselves, so this is where the real
    class's own wiring is held: without it the answer never reaches the GUI
    thread at all and no mark, fresh or stale, would ever be kept."""
    from tidaler.waves_ui import backend as be

    tree = ast.parse(textwrap.dedent(inspect.getsource(be.WavesBridge.__init__)))

    def _own_attr(node, name):
        # self.<name>, and nothing else that merely ends in the same word.
        return (
            isinstance(node, ast.Attribute)
            and node.attr == name
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        )

    wired = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "connect"
        and _own_attr(node.func.value, "_queueOwnedFetched")
        and len(node.args) == 1
        and _own_attr(node.args[0], "_apply_owned_marks")
    ]
    assert wired, "the bridge no longer routes an ownership answer into the marks it keeps"
