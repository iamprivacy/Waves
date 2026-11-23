"""The library anchor: which TIDAL profile is the library's artist."""

from __future__ import annotations

from waves import artist_anchor as aa

LIB = {"albums": ["Sleepless", "Death Dealers", "Silence the World"], "tracks": ["Grow Old"]}


def _v(titles, *, whole=True, lib=LIB, pid="1", tags=()):
    return aa.verdict("Adept", lib, titles, catalog_whole=whole, profile_id=pid, tag_ids=tags)


def test_a_profile_with_a_library_album_is_anchored():
    v = _v(["Sleepless", "Some Single"])
    assert v["state"] == aa.ANCHORED and v["reason"] == "titles" and v["albums_n"] == 1


def test_a_shared_song_title_anchors_too():
    assert _v(["Grow Old"])["state"] == aa.ANCHORED


def test_a_whole_catalog_without_the_library_albums_is_unanchored():
    v = _v(["Techno Track", "Another Remix"])
    assert v["state"] == aa.UNANCHORED and v["reason"] == "titles" and v["albums"] == 3


def test_an_empty_whole_catalog_is_unanchored():
    assert _v([])["state"] == aa.UNANCHORED


def test_a_partial_catalog_is_silent():
    v = _v(["Techno Track"], whole=False)
    assert v["state"] == aa.SILENT and v["reason"] == "partial"
    assert _v(None)["reason"] == "partial"


def test_no_library_under_the_name_is_silent():
    v = _v(["Techno Track"], lib={"albums": [], "tracks": []})
    assert v["state"] == aa.SILENT and v["reason"] == "no_library"


def test_one_distinctive_album_is_too_thin_to_judge_a_miss():
    # A self-titled album proves little, so this library has one album to go on.
    lib = {"albums": ["Adept", "Sleepless"], "tracks": []}
    v = _v(["Techno Track"], lib=lib)
    assert v["state"] == aa.SILENT and v["reason"] == "thin"


def test_file_tags_naming_the_profile_anchor_it():
    v = _v(["Techno Track"], tags={"1"})
    assert v["state"] == aa.ANCHORED and v["reason"] == "tag"


def test_file_tags_naming_one_other_profile_unanchor_it():
    v = _v(["Techno Track"], whole=False, tags={"2"})
    assert v["state"] == aa.UNANCHORED and v["reason"] == "tag" and v["tag_id"] == "2"


def test_file_tags_naming_two_profiles_say_nothing():
    assert _v(["Techno Track"], whole=False, tags={"2", "3"})["reason"] == "partial"


def test_titles_on_the_profile_beat_a_differing_tag():
    assert _v(["Sleepless"], tags={"2"})["state"] == aa.ANCHORED


def _p(pid, titles):
    return {"id": pid, "name": "Adept", "releases": titles}


def test_one_other_profile_with_two_albums_is_a_move_when_tidal_ranks_it_higher():
    ranks = {"new": 1, "cur": 15}
    out = aa.pick_move(
        "Adept", LIB, _p("cur", ["Techno"]), [_p("new", ["Sleepless", "Death Dealers"])], cut=False, ranks=ranks
    )
    assert out["grade"] == aa.MOVE and out["winner"]["id"] == "new"


def test_an_empty_followed_profile_moves_whatever_the_rank():
    ranks = {"new": 3, "cur": 1}
    out = aa.pick_move("Adept", LIB, _p("cur", []), [_p("new", ["Sleepless", "Death Dealers"])], cut=False, ranks=ranks)
    assert out["grade"] == aa.MOVE


def test_a_small_duplicate_below_a_living_catalog_is_only_a_suggestion():
    # One artist split over two profiles: the main one ranks first and has a
    # catalog of its own, the duplicate holds the library's two albums.
    lib = {"albums": ["The Fountain OST", "Requiem for a Dream / OST"], "tracks": []}
    cur = {"id": "main", "name": "Clint Mansell", "releases": ["Moon (Original Score)", "Black Swan"]}
    dup = {"id": "dup", "name": "Clint Mansell", "releases": ["The Fountain OST", "Requiem for a Dream / OST"]}
    out = aa.pick_move("Clint Mansell", lib, cur, [dup], cut=False, ranks={"main": 1, "dup": 15})
    assert out["grade"] == aa.SUGGEST and out["winner"]["id"] == "dup"


def test_one_stray_song_on_a_stranger_does_not_veto_many_albums():
    lib = {"albums": ["Soundtrack to the Struggle", "Dear Listener", "Uprising"], "tracks": ["Something Wonderful"]}
    win = {"id": "win", "name": "Lowkey", "releases": ["Soundtrack to the Struggle", "Dear Listener", "Uprising"]}
    stray = {"id": "stray", "name": "Lowkey", "releases": ["Something Wonderful", "Deepside"]}
    out = aa.pick_move("Lowkey", lib, _p("cur", ["Techno"]), [win, stray], cut=False, ranks={"win": 1, "cur": 6})
    assert out["grade"] == aa.MOVE and out["winner"]["id"] == "win"


def test_two_profiles_with_albums_are_no_dominance():
    lib = {"albums": ["A1", "A2", "A3", "B1"], "tracks": []}
    a = {"id": "a", "name": "X", "releases": ["A1", "A2", "A3"]}
    b = {"id": "b", "name": "X", "releases": ["B1"]}
    assert aa.pick_move("X", lib, {"id": "cur", "name": "X", "releases": []}, [a, b], cut=False) is None


def test_same_name_folds_punctuation_but_not_letters():
    assert aa.same_name("Static X", "Static-X")
    assert aa.same_name("AC/DC", "AC DC")
    assert not aa.same_name("Adept", "Adepts")
    assert not aa.same_name("", "")


def test_one_album_on_the_winner_is_only_a_suggestion():
    out = aa.pick_move("Adept", LIB, _p("cur", []), [_p("new", ["Sleepless"])], cut=False)
    assert out["grade"] == aa.SUGGEST


def test_a_cut_search_is_only_a_suggestion():
    out = aa.pick_move("Adept", LIB, _p("cur", []), [_p("new", ["Sleepless", "Death Dealers"])], cut=True)
    assert out["grade"] == aa.SUGGEST


def test_the_followed_profile_winning_means_nothing_to_do():
    assert aa.pick_move("Adept", LIB, _p("cur", ["Sleepless"]), [_p("new", ["Techno"])], cut=False) is None


def test_a_library_holding_both_namesakes_means_nothing_to_do():
    other = [_p("new", ["Death Dealers"])]
    assert aa.pick_move("Adept", LIB, _p("cur", ["Sleepless"]), other, cut=False) is None


def test_an_unread_followed_profile_never_loses_its_follow():
    out = aa.pick_move("Adept", LIB, _p("cur", None), [_p("new", ["Sleepless", "Death Dealers"])], cut=False)
    assert out is None


def test_an_unread_candidate_blocks_a_move():
    others = [_p("new", ["Sleepless", "Death Dealers"]), _p("odd", None)]
    assert aa.pick_move("Adept", LIB, _p("cur", []), others, cut=False) is None


def test_the_followed_profile_listed_among_the_others_is_counted_once():
    cur = _p("cur", [])
    out = aa.pick_move("Adept", LIB, cur, [cur, _p("new", ["Sleepless", "Death Dealers"])], cut=False)
    assert out is not None
    assert out["grade"] == aa.MOVE and len(out["weighed"]) == 2


def test_evidence_sig_changes_only_with_its_inputs():
    a = aa.evidence_sig("Adept", LIB, ["X", "Y"], catalog_whole=True)
    assert a == aa.evidence_sig("adept", LIB, ["Y", "X", "X"], catalog_whole=True)
    assert a != aa.evidence_sig("Adept", LIB, ["X"], catalog_whole=True)
    assert a != aa.evidence_sig("Adept", LIB, ["X", "Y"], catalog_whole=False)
    assert a != aa.evidence_sig("Adept", {"albums": ["Sleepless"]}, ["X", "Y"], catalog_whole=True)
    assert a != aa.evidence_sig("Adept", LIB, ["X", "Y"], catalog_whole=True, tag_ids={"9"})
    assert a != aa.evidence_sig("Adept", LIB, None, catalog_whole=True)
