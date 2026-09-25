"""Pins for the presence findings of the final audit of 2026-09-24 (report in
notes/, private): C19/C36 (clean vs explicit), C20/C21/C48/L25 (disc joins and
track totals), C23 (album artist from a lone file), L26 (key normaliser
version) and L04 (reveal, do not open).

Every test drives the real functions: the matcher on hand-built indexes, the
scanner on a temp tree with an injected tag reader, the bridge helpers bound
onto bare stubs. The whole design is biased against a FALSE "in library", so
several cases assert a verdict is deliberately withheld.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from waves import matching
from waves.library_index import (
    LibraryIndex,
    _advisory_word,
    _flat_set_declared,
    _marker_word,
)
from waves.matching import decide_presence, decide_track_presence, presence_key, track_key

# ---- helpers ------------------------------------------------------------------


def _album_index(*entries):
    """A presence index from (title, artist, year, tracks, folder, extra)."""
    idx: dict = {}
    for title, artist, year, tracks, fp, extra in entries:
        idx.setdefault(presence_key(title, artist), []).append(
            {"title": title, "year": year, "tracks": tracks, "id": fp, **extra}
        )
    return idx


def _track_index(*entries):
    idx: dict = {}
    for title, artist, facts in entries:
        idx.setdefault(track_key(title, artist), []).append({"id": "/lib/A/Alb", "codec": "flac", **facts})
    return idx


def _mk(base, rel, files):
    d = os.path.join(base, *rel.split("/"))
    os.makedirs(d, exist_ok=True)
    for name in files:
        open(os.path.join(d, name), "w").close()
    return d


def _by_name(filemap):
    """A fake tag reader keyed by the file's basename."""

    def read(path):
        return filemap.get(os.path.basename(path))

    return read


def _tags(album="Album", artist="A", **extra):
    return {"album": album, "artist": artist, "date": "2020", "title": "Song", **extra}


# ---- C19: the album verdict and the clean/explicit divide --------------------


def test_a_clean_copy_never_proves_the_explicit_release():
    idx = _album_index(("Album", "A", "2020", 10, "/m/A/Album", {"explicit": 0}))
    r = decide_presence("Album", "A", "2020", 10, idx, explicit=True)
    assert r["present"] is True, "the pill still lights: the user does hold the record"
    assert r["sure"] is False and r["partial"] is True, "but the bulk gate must not skip the other edition"


def test_an_explicit_copy_never_proves_the_clean_release():
    idx = _album_index(("Album", "A", "2020", 10, "/m/A/Album", {"explicit": 1}))
    r = decide_presence("Album", "A", "2020", 10, idx, explicit=False)
    assert r["present"] is True and r["sure"] is False and r["partial"] is True


@pytest.mark.parametrize("local,release", [(-1, True), (-1, False), (0, None), (1, None), (-1, None)])
def test_an_unknown_advisory_on_either_side_changes_nothing(local, release):
    idx = _album_index(("Album", "A", "2020", 10, "/m/A/Album", {"explicit": local}))
    r = decide_presence("Album", "A", "2020", 10, idx, explicit=release)
    assert r["sure"] is True and r["full"] is True and r["partial"] is False


def test_agreeing_advisory_facts_still_prove():
    idx = _album_index(("Album", "A", "2020", 10, "/m/A/Album", {"explicit": 1}))
    assert decide_presence("Album", "A", "2020", 10, idx, explicit=True)["partial"] is False
    idx = _album_index(("Album", "A", "2020", 10, "/m/A/Album", {"explicit": 0}))
    assert decide_presence("Album", "A", "2020", 10, idx, explicit=False)["partial"] is False


def test_a_row_from_before_the_fact_existed_reads_unknown():
    # No "explicit" key at all (an older index dict), and junk values.
    for extra in ({}, {"explicit": None}, {"explicit": "yes"}, {"explicit": 7}):
        idx = _album_index(("Album", "A", "2020", 10, "/m/A/Album", extra))
        assert decide_presence("Album", "A", "2020", 10, idx, explicit=True)["partial"] is False


def test_a_joined_set_is_explicit_when_any_disc_is():
    idx = _album_index(
        ("Album", "A", "2020", 10, "/m/A/Album/CD1", {"explicit": 0, "disc_no": 1, "disc_total": 2}),
        ("Album", "A", "2020", 10, "/m/A/Album/CD2", {"explicit": 1, "disc_no": 2, "disc_total": 2}),
    )
    assert decide_presence("Album", "A", "2020", 20, idx, explicit=False)["sure"] is False
    assert decide_presence("Album", "A", "2020", 20, idx, explicit=True)["partial"] is False


# ---- C36: the track claim and the divide ---------------------------------------


def test_a_clean_file_never_proves_the_explicit_track():
    idx = _track_index(("Song", "A", {"album": "Album", "album_year": "2020", "length": 200, "explicit": 0}))
    r = decide_track_presence("Song", "A", idx, "Album", "2020", 201, explicit=True)
    assert r["present"] is True and r["sure"] is False


def test_an_explicit_file_never_proves_the_clean_track():
    idx = _track_index(("Song", "A", {"album": "Album", "album_year": "2020", "length": 200, "explicit": 1}))
    assert decide_track_presence("Song", "A", idx, "Album", "2020", 201, explicit=False)["sure"] is False


@pytest.mark.parametrize("local,want", [(-1, True), (0, None), (1, True), (0, False)])
def test_the_track_claim_is_unchanged_unless_both_sides_know_and_clash(local, want):
    idx = _track_index(("Song", "A", {"album": "Album", "album_year": "2020", "length": 200, "explicit": local}))
    assert decide_track_presence("Song", "A", idx, "Album", "2020", 201, explicit=want)["sure"] is True


def test_the_agreeing_cut_is_chosen_over_the_other_edition():
    idx = _track_index(
        ("Song", "A", {"id": "/lib/A/Clean", "album": "Album", "album_year": "2020", "explicit": 0, "bits": 24}),
        ("Song", "A", {"id": "/lib/A/Explicit", "album": "Album", "album_year": "2020", "explicit": 1, "bits": 16}),
    )
    r = decide_track_presence("Song", "A", idx, "Album", "2020", explicit=True)
    assert r["sure"] is True and r["local_album_id"] == "/lib/A/Explicit"


# ---- C20: a split set tagged with the release-wide total -----------------------


def test_a_complete_split_set_tagged_with_the_release_total_is_full():
    # Waves writes TRACKTOTAL=album.num_tracks on every file of every disc, so
    # Album/CD1 and Album/CD2 each declare 20. Summing read "20 OF 40".
    idx = _album_index(
        ("Album", "A", "2015", 10, "/m/A/Album/CD1", {"declared": 20, "disc_no": 1, "disc_total": 2}),
        ("Album", "A", "2015", 10, "/m/A/Album/CD2", {"declared": 20, "disc_no": 2, "disc_total": 2}),
    )
    r = decide_presence("Album", "A", "2015", 20, idx)
    assert r["local_tracks"] == 20 and r["sure"] is True and r["full"] is True and r["partial"] is False


def test_per_disc_totals_still_add_up():
    idx = _album_index(
        ("Album", "A", "2015", 10, "/m/A/Album/CD1", {"declared": 10, "disc_no": 1, "disc_total": 2}),
        ("Album", "A", "2015", 12, "/m/A/Album/CD2", {"declared": 12, "disc_no": 2, "disc_total": 2}),
    )
    r = decide_presence("Album", "A", "2015", 22, idx)
    assert r["local_declared"] == 22 and r["full"] is True


def test_equal_per_disc_totals_exceeded_by_the_files_add_up():
    idx = _album_index(
        ("Album", "A", "2015", 10, "/m/A/Album/CD1", {"declared": 10, "disc_no": 1, "disc_total": 2}),
        ("Album", "A", "2015", 10, "/m/A/Album/CD2", {"declared": 10, "disc_no": 2, "disc_total": 2}),
    )
    r = decide_presence("Album", "A", "2015", 20, idx)
    assert r["local_declared"] == 20 and r["full"] is True


def test_a_short_split_set_is_still_short_under_either_reading():
    idx = _album_index(
        ("Album", "A", "2015", 8, "/m/A/Album/CD1", {"declared": 20, "disc_no": 1, "disc_total": 2}),
        ("Album", "A", "2015", 7, "/m/A/Album/CD2", {"declared": 20, "disc_no": 2, "disc_total": 2}),
    )
    r = decide_presence("Album", "A", "2015", 0, idx)
    assert r["local_declared"] == 20 and r["full"] is False and r["partial"] is True


def test_an_ambiguous_complete_looking_set_needs_tidals_count():
    # Two half-held discs whose per-disc claims add up to exactly the files
    # held are indistinguishable from a complete release-wide set, so the set
    # declares nothing and cannot complete itself without a count on screen.
    idx = _album_index(
        ("Album", "A", "2015", 5, "/m/A/Album/CD1", {"declared": 10, "disc_no": 1, "disc_total": 2}),
        ("Album", "A", "2015", 5, "/m/A/Album/CD2", {"declared": 10, "disc_no": 2, "disc_total": 2}),
    )
    r = decide_presence("Album", "A", "2015", 0, idx)
    assert r["local_declared"] == 0 and r["full"] is False
    assert decide_presence("Album", "A", "2015", 20, idx)["full"] is False


# ---- L25: name-marked siblings declaring different disc totals ----------------


def test_disc_siblings_from_two_releases_never_join():
    idx = _album_index(
        ("Album", "A", "2019", 10, "/m/A/Album (Disc 1)", {"disc_no": 1, "disc_total": 3}),
        ("Album", "A", "2019", 10, "/m/A/Album (Disc 2)", {"disc_no": 2, "disc_total": 2}),
    )
    r = decide_presence("Album", "A", "2019", 20, idx)
    assert r["local_tracks"] == 10 and r["full"] is False and r["partial"] is True


def test_disc_siblings_agreeing_on_the_total_still_join():
    idx = _album_index(
        ("Album", "A", "2019", 10, "/m/A/Album (Disc 1)", {"disc_no": 1, "disc_total": 2}),
        ("Album", "A", "2019", 10, "/m/A/Album (Disc 2)", {"disc_no": 2, "disc_total": 0}),
    )
    assert decide_presence("Album", "A", "2019", 20, idx)["local_tracks"] == 20


# ---- C48: a lone disc of a set with no count on screen ------------------------


def test_a_lone_disc_of_a_set_cannot_complete_itself_without_a_count():
    idx = _album_index(
        ("Album", "A", "2010", 10, "/m/A/Album (Disc 1)", {"declared": 10, "disc_no": 1, "disc_total": 3}),
    )
    for tracks in (0, -1, None):
        r = decide_presence("Album", "A", "2010", tracks, idx)
        assert r["sure"] is True and r["full"] is False and r["partial"] is True, tracks
    assert decide_presence("Album", "A", "2010", 30, idx)["partial"] is True


def test_a_flat_set_in_one_folder_still_completes_itself():
    # disc_no 0 with disc_total 2 is the whole set sitting flat, not one disc.
    idx = _album_index(("Album", "A", "2010", 20, "/m/A/Album", {"declared": 20, "disc_no": 0, "disc_total": 2}))
    assert decide_presence("Album", "A", "2010", 0, idx)["partial"] is False


def test_a_single_disc_release_still_completes_itself():
    idx = _album_index(("Album", "A", "2010", 10, "/m/A/Album", {"declared": 10, "disc_no": 1, "disc_total": 1}))
    assert decide_presence("Album", "A", "2010", 0, idx)["partial"] is False


# ---- C21: a flat set tagged per disc (the scanner) ------------------------------


def test_flat_set_declared_reads_both_conventions():
    picard = [(1, 10)] * 10 + [(2, 10)] * 10
    assert _flat_set_declared(picard, 20) == 20
    waves_complete = [(1, 20)] * 10 + [(2, 20)] * 10
    assert _flat_set_declared(waves_complete, 20) == 0  # ambiguous: TIDAL's count decides
    waves_short = [(1, 20)] * 8 + [(2, 20)] * 7
    assert _flat_set_declared(waves_short, 15) == 20
    unequal = [(1, 10)] * 10 + [(2, 12)] * 12
    assert _flat_set_declared(unequal, 22) == 22
    silent_disc = [(1, 10)] * 10 + [(2, 0)] * 10
    assert _flat_set_declared(silent_disc, 20) == 0
    split_disc = [(1, 10)] * 5 + [(1, 11)] * 5 + [(2, 10)] * 10
    assert _flat_set_declared(split_disc, 20) == 0


def test_a_flat_picard_set_declares_the_whole_release(tmp_path):
    lib = _mk(tmp_path, "lib", [])
    names = [f"{i:02d}.flac" for i in range(1, 21)]
    _mk(tmp_path, "lib/A/Album", names)
    filemap = {n: _tags(track_total=10, disc_no=1 if i < 10 else 2, disc_total=2) for i, n in enumerate(names)}
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_by_name(filemap))
    idx.refresh(lib)
    row = next(idx.iter_albums())
    assert row["tracks"] == 20 and row["disc_no"] == 0 and row["declared"] == 20
    r = decide_presence(
        "Album", "A", "2020", 20, {presence_key("Album", "A"): idx.presence_facts(presence_key("Album", "A"))}
    )
    assert r["sure"] is True and r["full"] is True and r["partial"] is False


def test_a_flat_waves_set_short_a_few_tracks_is_not_complete(tmp_path):
    lib = _mk(tmp_path, "lib", [])
    names = [f"{i:02d}.flac" for i in range(1, 16)]
    _mk(tmp_path, "lib/A/Album", names)
    filemap = {n: _tags(track_total=20, disc_no=1 if i < 8 else 2, disc_total=2) for i, n in enumerate(names)}
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_by_name(filemap))
    idx.refresh(lib)
    row = next(idx.iter_albums())
    assert row["declared"] == 20
    r = decide_presence("Album", "A", "2020", 0, {presence_key("Album", "A"): [row]})
    assert r["full"] is False


# ---- C23: the album artist from a lone file -----------------------------------


def _scan(tmp_path, filemap):
    lib = _mk(tmp_path, "lib", [])
    _mk(tmp_path, "lib/X/Greatest Hits", sorted(filemap))
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_by_name(filemap))
    idx.refresh(lib)
    return idx


def test_a_comp_without_an_album_artist_is_never_keyed_under_its_first_track(tmp_path):
    artists = ["Queen", "ABBA", "Blondie", "Cher", "Devo", "Eagles", "Foreigner", "Genesis", "Heart", "INXS"]
    filemap = {
        f"{i:02d}.flac": _tags("Greatest Hits", artist=a, albumartist="", track_artist=a) for i, a in enumerate(artists)
    }
    idx = _scan(tmp_path, filemap)
    row = next(idx.iter_albums())
    assert matching.is_various_artists(row["artist"])
    assert idx.presence_facts(presence_key("Greatest Hits", "Queen")) == []
    assert decide_presence("Greatest Hits", "Queen", "2020", 10, {})["present"] is False
    # The files themselves still answer as their own artists' tracks.
    assert idx.track_facts(track_key("Song", "ABBA"))


def test_agreeing_track_artists_still_fill_a_blank_album_artist(tmp_path):
    named = ["Queen"] * 8 + ["Queen feat. David Bowie", "Queen; Freddie Mercury"]
    filemap = {
        f"{i:02d}.flac": _tags("Greatest Hits", artist=a, albumartist="", track_artist=a) for i, a in enumerate(named)
    }
    idx = _scan(tmp_path, filemap)
    assert next(idx.iter_albums())["artist"] == "Queen"
    assert len(idx.presence_facts(presence_key("Greatest Hits", "Queen"))) == 1


def test_one_stray_credit_does_not_unkey_an_album(tmp_path):
    named = ["Queen"] * 9 + ["Brian May"]
    filemap = {
        f"{i:02d}.flac": _tags("Greatest Hits", artist=a, albumartist="", track_artist=a) for i, a in enumerate(named)
    }
    assert next(_scan(tmp_path, filemap).iter_albums())["artist"] == "Queen"


def test_a_set_album_artist_is_believed_over_the_track_credits(tmp_path):
    artists = ["Queen", "ABBA", "Blondie", "Cher"]
    filemap = {
        f"{i:02d}.flac": _tags("Greatest Hits", artist="Now", albumartist="Now", track_artist=a)
        for i, a in enumerate(artists)
    }
    assert next(_scan(tmp_path, filemap).iter_albums())["artist"] == "Now"


def test_a_compilation_flag_files_the_folder_as_various_artists(tmp_path):
    filemap = {f"{i:02d}.flac": _tags("Greatest Hits", artist="Queen", compilation=True) for i in range(10)}
    row = next(_scan(tmp_path, filemap).iter_albums())
    assert matching.is_various_artists(row["artist"])


def test_a_reader_that_never_reports_the_album_artist_keeps_the_old_fallback(tmp_path):
    filemap = {f"{i:02d}.flac": _tags("Greatest Hits", artist="Queen") for i in range(3)}
    assert next(_scan(tmp_path, filemap).iter_albums())["artist"] == "Queen"


# ---- The scanner's advisory fact ------------------------------------------------


def test_advisory_values_read_the_itunes_convention():
    assert [_advisory_word(v) for v in ("1", "4", "2", "0", " 1 ", "", "x", None)] == [1, 1, 0, 0, 1, -1, -1, -1]


def test_name_markers_only_ever_fall_back():
    assert _marker_word("[2020] Album (Explicit)") == 1
    assert _marker_word("Album [E]") == 1
    assert _marker_word("Album", "Album (Clean)") == 0
    assert _marker_word("Album", "Album") == -1


@pytest.mark.parametrize(
    "per_file,folder,expected",
    [
        ([1, 1, 1], "Album", 1),
        ([0, 0, 0], "Album", 0),
        ([0, 1, 0], "Album", 1),  # one explicit cut makes the explicit release
        ([-1, -1, -1], "Album (Explicit)", 1),  # the files never said: Waves' folder marker
        ([-1, -1, -1], "Album", -1),
        ([0, 0, -1], "Album (Explicit)", 0),  # the files outrank the name
    ],
)
def test_the_folder_records_its_files_advisory_fact(tmp_path, per_file, folder, expected):
    lib = _mk(tmp_path, "lib", [])
    names = [f"{i:02d}.flac" for i in range(len(per_file))]
    _mk(tmp_path, f"lib/A/{folder}", names)
    filemap = {n: _tags(explicit=e) for n, e in zip(names, per_file, strict=True)}
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_by_name(filemap))
    idx.refresh(lib)
    assert next(idx.iter_albums())["explicit"] == expected
    assert idx.presence_facts(presence_key("Album", "A"))[0]["explicit"] == expected
    facts = idx.track_facts(track_key("Song", "A"))
    assert sorted(f["explicit"] for f in facts) == sorted(per_file)
    assert sorted(t["explicit"] for t in idx.iter_tracks()) == sorted(per_file)


def test_a_mixed_folder_declares_no_advisory_fact(tmp_path):
    lib = _mk(tmp_path, "lib", [])
    names = [f"{i:02d}.flac" for i in range(12)]
    _mk(tmp_path, "lib/A/Album", names)
    filemap = {n: _tags(explicit=1) for n in names}
    filemap["11.flac"] = _tags("Other", explicit=0)
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_by_name(filemap))
    idx.refresh(lib)
    assert next(idx.iter_albums())["explicit"] == -1


def test_a_cache_from_before_the_advisory_fact_rereads_once(tmp_path):
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Album", ["01.flac", "02.flac"])
    reads: list = []

    def read(path):
        reads.append(path)
        return _tags(explicit=1) if os.path.dirname(path) == d else None

    db = str(tmp_path / "library.sqlite3")
    idx = LibraryIndex(db, read_tags=read)
    idx.refresh(lib)
    assert next(idx.iter_albums())["explicit"] == 1
    idx.close()
    conn = sqlite3.connect(db)  # rewind the row to its pre-migration state
    conn.execute("UPDATE albums SET explicit = NULL")
    conn.commit()
    conn.close()
    reads.clear()
    aged = LibraryIndex(db, read_tags=read)
    aged.refresh(lib)
    assert reads, "an unread row must not stay unread behind an unchanged mtime"
    assert next(aged.iter_albums())["explicit"] == 1
    reads.clear()
    aged.refresh(lib)
    assert not reads, "once read, the fact rests like every other column"


# ---- L26: the key normaliser version -----------------------------------------


def test_keys_derived_by_another_normaliser_are_rebuilt_once(tmp_path):
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Album", ["01.flac"])
    tags = {d: _tags()}
    db = str(tmp_path / "library.sqlite3")
    idx = LibraryIndex(db, read_tags=lambda p: tags.get(os.path.dirname(p)))
    idx.refresh(lib)
    assert idx.presence_facts(presence_key("Album", "A")) and idx.keys_missing() == 0
    idx.close()

    conn = sqlite3.connect(db)
    conn.execute("UPDATE meta SET value = 'stale' WHERE key = 'key_normaliser_version'")
    # A key the running normaliser would never derive, standing in for one
    # an older release stored.
    conn.execute("UPDATE albums SET pkey_title = 'old-title-key'")
    conn.commit()
    conn.close()

    reopened = LibraryIndex(db, read_tags=lambda p: tags.get(os.path.dirname(p)))
    assert reopened.keys_missing() > 0, "the stale keys are dropped at open"
    assert reopened.presence_facts(presence_key("Album", "A")) == []
    reopened.backfill_keys()
    assert reopened.presence_facts(presence_key("Album", "A")), "and rebuilt from the raw tags"
    assert reopened.keys_missing() == 0
    reopened.close()

    again = LibraryIndex(db, read_tags=lambda p: tags.get(os.path.dirname(p)))
    assert again.keys_missing() == 0, "the same version never drops the keys again"


def test_a_cache_that_never_stamped_a_version_is_rekeyed_once(tmp_path):
    lib = _mk(tmp_path, "lib", [])
    d = _mk(tmp_path, "lib/A/Album", ["01.flac"])
    tags = {d: _tags()}
    db = str(tmp_path / "library.sqlite3")
    idx = LibraryIndex(db, read_tags=lambda p: tags.get(os.path.dirname(p)))
    idx.refresh(lib)
    idx.close()
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM meta WHERE key = 'key_normaliser_version'")
    conn.commit()
    conn.close()
    reopened = LibraryIndex(db, read_tags=lambda p: tags.get(os.path.dirname(p)))
    assert reopened.keys_missing() > 0
    reopened.refresh(lib)  # the scan's own backfill pass
    assert reopened.keys_missing() == 0 and reopened.presence_facts(presence_key("Album", "A"))


# ---- The bridge gates -----------------------------------------------------------

bridge_library = pytest.importorskip("waves.waves_ui.bridge_library")
LibraryMixin = bridge_library.LibraryMixin


class _GateStub:
    _library_track_claim = LibraryMixin._library_track_claim
    _library_claims_track = LibraryMixin._library_claims_track
    _library_claims_album = LibraryMixin._library_claims_album

    def __init__(self, albums=None, tracks=None):
        self._library_index = albums
        self._library_track_index = tracks


def _clean_track_stub():
    return _GateStub(
        tracks=_track_index(("Song", "A", {"album": "Album", "album_year": "2020", "length": 200, "explicit": 0}))
    )


def test_the_track_gate_refuses_the_other_cut():
    s = _clean_track_stub()
    assert s._library_track_claim("A", "Song", "Album", "2020", 200, True) is None
    assert s._library_claims_track("A", "Song", "Album", "2020", 200, True) is False


def test_the_track_gate_is_unchanged_without_a_flag():
    s = _clean_track_stub()
    assert s._library_track_claim("A", "Song", "Album", "2020", 200) is not None
    assert s._library_track_claim("A", "Song", "Album", "2020", 200, None) is not None
    assert s._library_track_claim("A", "Song", "Album", "2020", 200, False) is not None
    assert s._library_track_claim("A", "Song", "Album", "2020", 200, "garbage") is not None


def _album(**kw):
    base = {"name": "Album", "artist": SimpleNamespace(name="A"), "year": 2020, "num_tracks": 10}
    base.update(kw)
    return SimpleNamespace(**base)


def test_the_album_gate_refuses_the_other_edition_only_when_tidal_said():
    s = _GateStub(albums=_album_index(("Album", "A", "2020", 10, "/m/A/Album", {"explicit": 0})))
    assert s._library_claims_album(_album(explicit=True)) is False
    assert s._library_claims_album(_album(explicit=False)) is True
    # tidalapi leaves the flag None when the payload carried none, and an
    # older Album object has no attribute at all: neither is a claim.
    assert s._library_claims_album(_album(explicit=None)) is True
    assert s._library_claims_album(_album()) is True


def test_a_caller_vouched_flag_wins_over_the_release_flag():
    s = _GateStub(albums=_album_index(("Album", "A", "2020", 10, "/m/A/Album", {"explicit": 0})))
    assert s._library_claims_album(_album(explicit=True), explicit=False) is True
    assert s._library_claims_album(_album(explicit=None), explicit=True) is False


def test_release_explicit_only_passes_a_real_bool():
    f = LibraryMixin._release_explicit
    assert f(_album(explicit=True)) is True and f(_album(explicit=False)) is False
    assert f(_album(explicit=None)) is None and f(_album(explicit=1)) is None and f(_album()) is None
    assert f(_album(explicit=True), explicit=False) is False and f(None) is None


def test_an_old_stub_without_a_flag_still_binds_the_album_gate():
    s = _GateStub(albums=_album_index(("Album", "A", "2020", 10, "/m/A/Album", {})))
    assert s._library_claims_album(_album(explicit=True)) is True, "an unknown local fact changes nothing"


# ---- L04: reveal, do not open ----------------------------------------------------


def _reveal(monkeypatch, platform, target):
    popen: list = []
    opened: list = []
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(bridge_library.subprocess, "Popen", lambda argv, **kw: popen.append((argv, kw)))
    monkeypatch.setattr(bridge_library.QtGui.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))
    LibraryMixin._reveal_in_file_manager(str(target))
    return popen, opened


def test_macos_reveals_the_item_in_its_parent_without_a_shell(tmp_path, monkeypatch):
    bundle = tmp_path / "Artist" / "Album.app"
    bundle.mkdir(parents=True)
    popen, opened = _reveal(monkeypatch, "darwin", bundle)
    assert popen == [(["/usr/bin/open", "-R", str(bundle)], {})], "a fixed argument list, nothing launched"
    assert opened == []


def test_macos_reveals_a_plain_album_folder_the_same_way(tmp_path, monkeypatch):
    folder = tmp_path / "Artist" / "[2020] Album"
    folder.mkdir(parents=True)
    popen, opened = _reveal(monkeypatch, "darwin", folder)
    assert popen[0][0] == ["/usr/bin/open", "-R", str(folder)] and opened == []


def test_a_failed_finder_reveal_falls_back_to_showing_the_parent(tmp_path, monkeypatch):
    bundle = tmp_path / "Artist" / "Album.pkg"
    bundle.mkdir(parents=True)
    opened: list = []
    monkeypatch.setattr(sys, "platform", "darwin")

    def boom(argv, **kw):
        raise OSError("no open")

    monkeypatch.setattr(bridge_library.subprocess, "Popen", boom)
    monkeypatch.setattr(bridge_library.QtGui.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))
    LibraryMixin._reveal_in_file_manager(str(bundle))
    assert opened == [str(bundle.parent)]


@pytest.mark.parametrize("suffix", [".app", ".pkg", ".bundle", ".framework", ".kext", ".plugin", ".prefPane", ".xpc"])
def test_elsewhere_a_package_suffixed_folder_shows_its_parent(tmp_path, monkeypatch, suffix):
    bundle = tmp_path / "Artist" / f"Album{suffix}"
    bundle.mkdir(parents=True)
    popen, opened = _reveal(monkeypatch, "linux", bundle)
    assert popen == [] and opened == [str(bundle.parent)]


def test_elsewhere_a_plain_folder_opens_as_before(tmp_path, monkeypatch):
    folder = tmp_path / "Artist" / "[2020] Album"
    folder.mkdir(parents=True)
    popen, opened = _reveal(monkeypatch, "win32", folder)
    assert popen == [] and opened == [str(folder)]


def test_the_resolved_reveal_slot_routes_through_the_safe_reveal(tmp_path, monkeypatch):
    seen: list = []
    monkeypatch.setattr(LibraryMixin, "_reveal_in_file_manager", staticmethod(lambda t: seen.append(t)))
    LibraryMixin._on_reveal_resolved(SimpleNamespace(), str(tmp_path))
    assert seen == [str(tmp_path)]
