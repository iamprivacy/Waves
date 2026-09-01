"""The badge slots answer repeat asks from a memo, not by re-matching.

THE COST THIS FENCES OFF
------------------------
libraryAlbumPresence / libraryTrackPresence are synchronous GUI-thread slots.
Every libraryPresenceChanged republish makes ALL visible pills re-ask, and
scrolling re-asks per row, always against the same published index object,
so the matcher re-derived identical verdicts over and over inside frames.
Each slot now memoizes verdicts keyed by its arguments, valid only for the
index object they were computed against: the memo resets exactly when the
index is swapped (the same moment libraryPresenceChanged fires), so the
always-on freshness rule holds, and it is FIFO-bounded. The MusicBrainz
overlay stays OUTSIDE the album memo, so an arbitration answer landing
without an index swap still overlays on the next ask.
"""

from __future__ import annotations

import os

from test_library_bridge import _album, _make

import waves.matching as matching
from waves.waves_ui import bridge_library


def _bridge(tmp_path):
    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    d = _album(lib, "Lorna Shore/Pain Remains", ["1.flac", "2.flac", "3.flac"])
    s = _make(
        tmp_path, library_folder=lib, tagmap={d: {"album": "Pain Remains", "artist": "Lorna Shore", "date": "2022"}}
    )
    s._rebuild_library_index()
    assert s._library_index is not None
    return s


def test_repeat_album_asks_derive_once(tmp_path, monkeypatch):
    s = _bridge(tmp_path)
    calls = []
    real = matching.decide_presence
    monkeypatch.setattr(matching, "decide_presence", lambda *a, **k: calls.append(1) or real(*a, **k))
    first = s.libraryAlbumPresence("Lorna Shore", "Pain Remains", "2022", 3)
    again = s.libraryAlbumPresence("Lorna Shore", "Pain Remains", "2022", 3)
    assert first["present"] and again["present"]
    assert len(calls) == 1
    # A different ask is its own verdict, not a memo hit.
    s.libraryAlbumPresence("Boards of Canada", "Geogaddi", "2002", 10)
    assert len(calls) == 2


def test_a_republish_resets_the_memo(tmp_path, monkeypatch):
    s = _bridge(tmp_path)
    calls = []
    real = matching.decide_presence
    monkeypatch.setattr(matching, "decide_presence", lambda *a, **k: calls.append(1) or real(*a, **k))
    s.libraryAlbumPresence("Lorna Shore", "Pain Remains", "2022", 3)
    assert len(calls) == 1
    # A republish swaps the index OBJECT (equal content is irrelevant): the
    # memo must not answer for the previous scan's index.
    s._library_index = dict(s._library_index)
    s.libraryAlbumPresence("Lorna Shore", "Pain Remains", "2022", 3)
    assert len(calls) == 2


def test_the_memo_is_bounded_fifo(tmp_path, monkeypatch):
    s = _bridge(tmp_path)
    monkeypatch.setattr(bridge_library, "_PRESENCE_MEMO_MAX", 8)
    for i in range(12):
        s.libraryAlbumPresence(f"Artist {i}", f"Album {i}", "2020", 5)
    assert len(s._presence_memo) == 8
    # The first four asks were evicted, the last eight are held.
    keys = list(s._presence_memo)
    assert keys[0][0] == "Album 4"
    assert keys[-1][0] == "Album 11"


def test_track_asks_memoize_the_same_way(tmp_path, monkeypatch):
    s = _bridge(tmp_path)
    calls = []
    real = matching.decide_track_presence
    monkeypatch.setattr(matching, "decide_track_presence", lambda *a, **k: calls.append(1) or real(*a, **k))
    a = s.libraryTrackPresence("Lorna Shore", "Sun//Eater", "Pain Remains", "2022")
    b = s.libraryTrackPresence("Lorna Shore", "Sun//Eater", "Pain Remains", "2022")
    assert a == b
    assert len(calls) == 1
    s._library_track_index = dict(s._library_track_index)
    s.libraryTrackPresence("Lorna Shore", "Sun//Eater", "Pain Remains", "2022")
    assert len(calls) == 2
