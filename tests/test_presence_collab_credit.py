"""A collaboration on disk wears its library badge.

THE BUG
-------
Every album card names a collaboration by its main artists joined with ", "
("Scarlett O'Hara, Patient Sixty-Seven"), and every badge asked the library
with that whole credit. The files carry each album artist as its own tag
value (Waves' own downloads do), the scan reads the first, so the copy is
filed under "Scarlett O'Hara" alone. The matcher never splits a credit at a
comma (one band can have one in its name), so the two never met: the cover
badge stayed hidden over a copy that was right there, while the download
button, reading Waves' own download record, said IN LIBRARY.

THE FIX
-------
libraryAlbumPresence asks with the whole credit first and, only when that
finds nothing, with the credit's primary artist. A band with a comma in its
name still matches as itself, and a credit tagged whole on disk still finds
its own folder first.
"""

from __future__ import annotations

import os

from test_library_bridge import _album, _make

import waves.matching as matching


def _bridge(tmp_path, tagmap_for):
    lib = str(tmp_path / "lib")
    os.makedirs(lib, exist_ok=True)
    tagmap = {}
    for rel, files, tags in tagmap_for:
        tagmap[_album(lib, rel, files)] = tags
    s = _make(tmp_path, library_folder=lib, tagmap=tagmap)
    s._rebuild_library_index()
    assert s._library_index is not None
    return s


def test_credit_primary():
    assert matching.credit_primary("Scarlett O'Hara, Patient Sixty-Seven") == "Scarlett O'Hara"
    assert matching.credit_primary("A, B, C") == "A"
    assert matching.credit_primary("Lorna Shore") == ""
    assert matching.credit_primary("") == ""
    assert matching.credit_primary(", B") == ""


def test_a_collaboration_filed_under_its_primary_is_found(tmp_path):
    s = _bridge(
        tmp_path,
        [
            (
                "Scarlett O'Hara/[2026] Let The Record Show",
                ["1.flac"],
                {"album": "Let The Record Show", "artist": "Scarlett O'Hara", "date": "2026-11-13"},
            )
        ],
    )
    v = s.libraryAlbumPresence("Scarlett O'Hara, Patient Sixty-Seven", "Let The Record Show", "2026", 1)
    assert v["present"] and v["full"] and v["sure"]
    # The primary asked directly answers the same, and a stranger does not.
    assert s.libraryAlbumPresence("Scarlett O'Hara", "Let The Record Show", "2026", 1)["present"]
    assert not s.libraryAlbumPresence("Someone Else, Patient Sixty-Seven", "Let The Record Show", "2026", 1)["present"]


def test_a_credit_tagged_whole_still_finds_itself(tmp_path):
    s = _bridge(
        tmp_path,
        [
            (
                "Earth, Wind and Fire/That's the Way of the World",
                ["1.flac", "2.flac"],
                {"album": "That's the Way of the World", "artist": "Earth, Wind and Fire", "date": "1975"},
            )
        ],
    )
    v = s.libraryAlbumPresence("Earth, Wind and Fire", "That's the Way of the World", "1975", 2)
    assert v["present"] and v["full"]


def test_the_whole_credit_wins_over_the_primary(tmp_path, monkeypatch):
    # Both copies on disk: the whole credit answers, and the primary is never
    # asked (a band's own folder is not overruled by its first word's).
    s = _bridge(
        tmp_path,
        [
            (
                "Earth, Wind and Fire/Open Our Eyes",
                ["1.flac", "2.flac"],
                {"album": "Open Our Eyes", "artist": "Earth, Wind and Fire", "date": "1974"},
            ),
            (
                "Earth/Open Our Eyes",
                ["1.flac", "2.flac"],
                {"album": "Open Our Eyes", "artist": "Earth", "date": "1974"},
            ),
        ],
    )
    asked = []
    real = matching.decide_presence
    monkeypatch.setattr(matching, "decide_presence", lambda t, a, *r, **k: asked.append(a) or real(t, a, *r, **k))
    v = s.libraryAlbumPresence("Earth, Wind and Fire", "Open Our Eyes", "1974", 2)
    assert v["present"]
    assert "Earth, Wind and Fire" in v["local_album_id"]
    assert asked == ["Earth, Wind and Fire"]


def test_the_fallback_is_memoized_with_the_verdict(tmp_path, monkeypatch):
    s = _bridge(
        tmp_path,
        [("A/Song Book", ["1.flac"], {"album": "Song Book", "artist": "A", "date": "2020"})],
    )
    calls = []
    real = matching.decide_presence
    monkeypatch.setattr(matching, "decide_presence", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert s.libraryAlbumPresence("A, B", "Song Book", "2020", 1)["present"]
    assert len(calls) == 2  # whole credit, then primary
    assert s.libraryAlbumPresence("A, B", "Song Book", "2020", 1)["present"]
    assert len(calls) == 2  # the repeat is a memo hit


def test_a_collaboration_track_filed_under_its_primary_is_found(tmp_path):
    s = _bridge(
        tmp_path,
        [
            (
                "A/[2020] Song Book",
                ["1.flac"],
                {"album": "Song Book", "artist": "A", "title": "Song", "date": "2020"},
            )
        ],
    )
    v = s.libraryTrackPresence("A, B", "Song", "Song Book", "2020", 0)
    assert v["present"] and v["sure"]
    assert not s.libraryTrackPresence("C, B", "Song", "Song Book", "2020", 0)["present"]


def test_the_download_gate_finds_a_collaboration_tagged_whole(tmp_path):
    # Files tagged with one joined value ("A, B"): the badge finds them by
    # the whole credit, so the gate must too, or auto-download refetches.
    from types import SimpleNamespace as NS

    s = _bridge(
        tmp_path,
        [("A, B/Song Book", ["1.flac"], {"album": "Song Book", "artist": "A, B", "date": "2020"})],
    )
    album = NS(
        name="Song Book",
        artist=NS(name="A", roles=None),
        artists=[NS(name="A", roles=None), NS(name="B", roles=None)],
        year="2020",
        num_tracks=1,
        duration=0,
        explicit=False,
    )
    assert s.libraryAlbumPresence("A, B", "Song Book", "2020", 1)["present"]
    assert s._library_claims_album(album)
