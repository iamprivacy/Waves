"""artist_import.resolve: the library import sorts names into buckets."""

from __future__ import annotations

import pytest

from waves import artist_import


def _search_from(catalog: dict):
    """A search callable serving canned candidates keyed by exact query."""

    def search(name: str) -> list[dict]:
        return catalog.get(name, [])

    return search


def test_exact_single_hit_is_matched():
    search = _search_from({"Nils Frahm": [{"id": "1", "name": "Nils Frahm", "art": "a"}]})
    out = artist_import.resolve(["Nils Frahm"], search=search)
    assert [r["artist"]["id"] for r in out["matched"]] == ["1"]
    assert out["fuzzy"] == out["unmatched"] == out["skipped"] == []


def test_case_and_whitespace_differences_still_match_exactly():
    search = _search_from({"  nils   FRAHM ": [{"id": "1", "name": "Nils Frahm"}]})
    out = artist_import.resolve(["  nils   FRAHM "], search=search)
    assert len(out["matched"]) == 1


def test_two_exact_namesakes_demote_to_fuzzy():
    cands = [{"id": "1", "name": "Bleachers"}, {"id": "2", "name": "Bleachers"}]
    out = artist_import.resolve(["Bleachers"], search=_search_from({"Bleachers": cands}))
    assert out["matched"] == []
    assert len(out["fuzzy"]) == 1
    assert {c["id"] for c in out["fuzzy"][0]["candidates"]} == {"1", "2"}


def test_close_name_scores_as_fuzzy_with_candidates_best_first():
    # A typo, not an accent: accents fold to an exact match (see below).
    cands = [{"id": "1", "name": "Radiohead"}, {"id": "2", "name": "Beyonce"}]
    out = artist_import.resolve(["Radiohed"], search=_search_from({"Radiohed": cands}))
    assert len(out["fuzzy"]) == 1
    row = out["fuzzy"][0]
    assert row["candidates"][0]["id"] == "1"
    assert row["candidates"][0]["score"] > 0.85


def test_nothing_close_is_unmatched():
    out = artist_import.resolve(["Aphex Twin"], search=_search_from({"Aphex Twin": [{"id": "9", "name": "ABBA"}]}))
    assert len(out["unmatched"]) == 1
    assert out["unmatched"][0]["candidates"] == []


def test_search_error_files_the_row_as_failed_and_run_continues():
    def search(name: str) -> list[dict]:
        if name == "Boom":
            raise RuntimeError("flaky")
        return [{"id": "1", "name": name}]

    out = artist_import.resolve(["Boom", "Caribou"], search=search)
    assert out["failed"][0]["name"] == "Boom" and out["unmatched"] == []
    assert len(out["matched"]) == 1 and out["offline"] is False


def test_a_streak_of_failed_searches_stops_the_run_as_offline():
    def search(name: str) -> list[dict]:
        if name.startswith("down"):
            raise OSError("no route to host")
        return [{"id": "1", "name": name}]

    names = ["Caribou"] + [f"down {i}" for i in range(artist_import._ERROR_STREAK + 3)]
    out = artist_import.resolve(names, search=search)
    assert out["offline"] is True and out["stopped"] is True
    assert len(out["failed"]) == artist_import._ERROR_STREAK and out["unmatched"] == []


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        ({"name": ""}, "empty"),
        ({"name": "Various Artists"}, "various-artists"),
        ({"name": "Caribou"}, "already-followed"),
        ({"name": "Dismissed Guy"}, "dismissed"),
    ],
)
def test_skip_reasons(row, reason):
    out = artist_import.resolve(
        [row],
        search=_search_from({}),
        followed_names=["caribou"],
        dismissed_norms=["dismissed guy"],
    )
    assert out["skipped"] == [{**row, "reason": reason}]


def test_duplicate_names_within_run_resolve_once():
    search = _search_from({"Caribou": [{"id": "1", "name": "Caribou"}]})
    out = artist_import.resolve(["Caribou", "caribou"], search=search)
    assert len(out["matched"]) == 1
    assert out["skipped"][0]["reason"] == "duplicate"


def test_matched_candidate_already_followed_by_id_skips():
    search = _search_from({"Caribou": [{"id": "42", "name": "Caribou"}]})
    out = artist_import.resolve(["Caribou"], search=search, followed_ids=["42"])
    assert out["matched"] == []
    assert out["skipped"][0]["reason"] == "already-followed"


def test_extra_row_keys_ride_into_result():
    search = _search_from({"Caribou": [{"id": "1", "name": "Caribou"}]})
    out = artist_import.resolve([{"name": "Caribou", "art": "plex://thumb"}], search=search)
    assert out["matched"][0]["art"] == "plex://thumb"


def test_progress_fires_per_row_including_skips():
    calls: list = []
    artist_import.resolve(
        ["", "Caribou"],
        search=_search_from({"Caribou": [{"id": "1", "name": "Caribou"}]}),
        progress=lambda done, total: calls.append((done, total)),
    )
    assert calls == [(1, 2), (2, 2)]


def test_threshold_is_clamped_to_floor():
    # A wild threshold (0.1) may not admit noise below the module floor.
    out = artist_import.resolve(
        ["Aphex Twin"],
        search=_search_from({"Aphex Twin": [{"id": "9", "name": "ABBA"}]}),
        threshold=0.1,
    )
    assert out["fuzzy"] == []


def test_stop_ends_the_run_with_what_was_resolved():
    asked: list = []

    def search(name: str) -> list[dict]:
        asked.append(name)
        return [{"id": name, "name": name}]

    out = artist_import.resolve(["A", "B", "C"], search=search, stop=lambda: len(asked) >= 2)
    assert asked == ["A", "B"] and [r["name"] for r in out["matched"]] == ["A", "B"]
    assert out["stopped"] is True


def test_a_full_run_is_not_stopped():
    out = artist_import.resolve(["A"], search=lambda n: [], stop=lambda: False)
    assert out["stopped"] is False


def test_dismissed_norms_are_compared_as_given():
    # Already normalized by the store: "The The" is stored as "the" and must
    # not be folded a second time on the way in.
    out = artist_import.resolve(["The The"], search=lambda n: [], dismissed_norms=["the"])
    assert out["skipped"][0]["reason"] == "dismissed"


@pytest.mark.parametrize(
    ("library", "tidal"),
    [("Bjork", "Björk"), ("Motorhead", "Motörhead"), ("Guns N’ Roses", "Guns N' Roses"), ("Sigur Ros", "Sigur Rós")],
)
def test_accents_and_quote_shapes_still_match_exactly(library, tidal):
    out = artist_import.resolve_one(library, [{"id": "1", "name": tidal}])
    assert out["bucket"] == "matched"


def test_an_artist_followed_under_its_accented_name_is_skipped():
    out = artist_import.resolve(["Bjork"], search=lambda n: [{"id": "1", "name": "Björk"}], followed_names=["Björk"])
    assert out["skipped"][0]["reason"] == "already-followed"


# --- weighing namesakes against the library ----------------------------------


def _catalog_from(releases: dict, calls: list | None = None):
    """A catalog callable serving release titles by (artist id, singles)."""

    def catalog(artist_id: str, singles: bool):
        if calls is not None:
            calls.append((artist_id, singles))
        hit = releases.get((artist_id, singles))
        if isinstance(hit, Exception):
            raise hit
        return hit

    return catalog


def test_weigh_scores_the_candidate_that_has_the_library_albums():
    lib = {"albums": ["Hollow", "Nightfall (Deluxe Edition)"], "tracks": ["Intro", "Hollow Song"]}
    cands = [
        {"id": "1", "name": "Afterglow", "releases": ["Something Else"]},
        {"id": "2", "name": "Afterglow", "releases": ["Hollow", "Nightfall"]},
        {"id": "3", "name": "Afterglow", "releases": None},
    ]
    out = artist_import.weigh("Afterglow", lib, cands)
    assert [c["id"] for c in out] == ["2", "1", "3"]
    assert out[0]["conf"] == 100 and out[0]["albums_n"] == 2
    assert out[0]["owned"] == ["Hollow", "Nightfall (Deluxe Edition)"]
    assert out[1]["conf"] == 20 and out[1]["owned"] == [] and out[1]["checked"] is True
    assert out[2]["checked"] is False and out[2]["conf"] == 20
    assert all("releases" not in c for c in out)


def test_a_self_titled_or_generic_album_is_weak_evidence():
    cands = [
        {"id": "1", "name": "Afterglow", "releases": ["Afterglow"]},
        {"id": "2", "name": "Afterglow", "releases": ["Greatest Hits"]},
    ]
    out = artist_import.weigh("Afterglow", {"albums": ["Afterglow"], "tracks": []}, cands)
    assert out[0]["id"] == "1" and out[0]["conf"] == 60 and out[0]["albums_n"] == 0 and out[0]["owned_n"] == 1
    assert artist_import.clear_winner(out) is None
    out = artist_import.weigh("Afterglow", {"albums": ["Greatest Hits"], "tracks": []}, cands)
    assert out[0]["id"] == "2" and out[0]["albums_n"] == 0
    assert artist_import.clear_winner(out) is None


def test_two_weak_hits_make_a_winner_one_does_not():
    lib = {"albums": ["Afterglow"], "tracks": ["Summer Song", "Intro"]}
    cands = [
        {"id": "1", "name": "Afterglow", "releases": ["Afterglow", "Summer Song"]},
        {"id": "2", "name": "Afterglow", "releases": ["Other"]},
    ]
    assert artist_import.clear_winner(artist_import.weigh("Afterglow", lib, cands))["id"] == "1"
    cands[0]["releases"] = ["Summer Song"]
    assert artist_import.clear_winner(artist_import.weigh("Afterglow", lib, cands)) is None


def test_no_winner_while_a_candidate_is_unread_or_two_have_hits():
    lib = {"albums": ["Hollow"], "tracks": []}
    both = [{"id": "1", "name": "A", "releases": ["Hollow"]}, {"id": "2", "name": "A", "releases": ["Hollow"]}]
    assert artist_import.clear_winner(artist_import.weigh("A", lib, both)) is None
    unread = [{"id": "1", "name": "A", "releases": ["Hollow"]}, {"id": "2", "name": "A", "releases": None}]
    assert artist_import.clear_winner(artist_import.weigh("A", lib, unread)) is None
    assert artist_import.clear_winner([]) is None


def test_a_big_catalog_tidal_lists_only_part_of_still_vouches():
    lib = {"albums": [f"Album {n}" for n in range(10)], "tracks": []}
    cands = [{"id": "1", "name": "A", "releases": ["Album 1", "Album 2"]}, {"id": "2", "name": "A", "releases": []}]
    out = artist_import.weigh("A", lib, cands)
    assert out[0]["conf"] == 80 and artist_import.clear_winner(out)["id"] == "1"


def test_resolve_settles_a_namesake_by_its_albums_first():
    cands = [{"id": "1", "name": "Afterglow", "art": "a1"}, {"id": "2", "name": "Afterglow", "art": "a2"}]
    calls: list = []
    out = artist_import.resolve(
        [{"name": "Afterglow", "key": "afterglow"}],
        search=_search_from({"Afterglow": cands}),
        library=lambda row: {"albums": ["Hollow"], "tracks": []} if row["key"] == "afterglow" else {},
        catalog=_catalog_from({("1", False): ["Other"], ("2", False): ["Hollow"]}, calls),
    )
    assert out["fuzzy"] == []
    (row,) = out["matched"]
    assert row["artist"] == {"id": "2", "name": "Afterglow", "art": "a2"}
    assert row["reason"] == "library" and row["owned"] == ["Hollow"] and row["conf"] == 100
    # The albums settled it: no EP and singles read was spent.
    assert calls == [("1", False), ("2", False)]


def test_more_namesakes_than_the_review_keeps_never_settle_by_the_library():
    # Four exact namesakes, three weighed: the one the library vouches for
    # may not be the real one, which could be the fourth.
    cands = [{"id": str(n), "name": "Phoenix", "art": ""} for n in range(1, 5)]
    out = artist_import.resolve(
        ["Phoenix"],
        search=_search_from({"Phoenix": cands}),
        library=lambda row: {"albums": ["Hollow"], "tracks": []},
        catalog=_catalog_from({("1", False): [], ("2", False): ["Hollow"], ("3", False): []}, []),
    )
    assert out["matched"] == []
    (row,) = out["fuzzy"]
    assert row["candidates"][0]["id"] == "2" and row["candidates"][0]["owned"] == ["Hollow"]


def test_resolve_reads_singles_only_when_the_albums_leave_it_open():
    cands = [{"id": "1", "name": "Afterglow"}, {"id": "2", "name": "Afterglow"}]
    calls: list = []
    titles = {("1", False): [], ("2", False): [], ("1", True): ["Summer Song"], ("2", True): ["Hollow"]}
    out = artist_import.resolve(
        ["Afterglow"],
        search=_search_from({"Afterglow": cands}),
        library=lambda row: {"albums": ["Hollow"], "tracks": []},
        catalog=_catalog_from(titles, calls),
    )
    assert calls == [("1", False), ("2", False), ("1", True), ("2", True)]
    assert [r["artist"]["id"] for r in out["matched"]] == ["2"]


def test_a_toss_up_stays_fuzzy_scored_best_first():
    cands = [{"id": "1", "name": "Afterglow"}, {"id": "2", "name": "Afterglow"}, {"id": "3", "name": "Afterglow"}]
    titles = {
        ("1", False): ["Hollow"],
        ("2", False): ["Hollow", "Nightfall"],
        ("3", False): RuntimeError("down"),
        ("1", True): [],
        ("2", True): [],
    }
    calls: list = []
    out = artist_import.resolve(
        ["Afterglow"],
        search=_search_from({"Afterglow": cands}),
        library=lambda row: {"albums": ["Hollow", "Nightfall"], "tracks": []},
        catalog=_catalog_from(titles, calls),
    )
    assert out["matched"] == []
    (row,) = out["fuzzy"]
    assert [c["id"] for c in row["candidates"]] == ["2", "1", "3"]
    assert row["candidates"][0]["conf"] == 100 and row["candidates"][1]["conf"] == 60
    assert row["candidates"][2]["checked"] is False
    # An unread candidate is not asked for its singles either.
    assert ("3", True) not in calls


def test_a_library_with_nothing_under_the_name_spends_no_reads():
    cands = [{"id": "1", "name": "Afterglow"}, {"id": "2", "name": "Afterglow"}]
    calls: list = []
    out = artist_import.resolve(
        ["Afterglow"],
        search=_search_from({"Afterglow": cands}),
        library=lambda row: {"albums": [], "tracks": []},
        catalog=_catalog_from({}, calls),
    )
    assert calls == [] and len(out["fuzzy"]) == 1 and "conf" not in out["fuzzy"][0]["candidates"][0]


def test_a_stop_mid_weigh_ends_the_reads():
    cands = [{"id": "1", "name": "Afterglow"}, {"id": "2", "name": "Afterglow"}]
    calls: list = []
    flag = {"stop": False}

    def catalog(artist_id: str, singles: bool):
        calls.append((artist_id, singles))
        flag["stop"] = True
        return ["Hollow"]

    out = artist_import.resolve(
        ["Afterglow", "Next"],
        search=_search_from({"Afterglow": cands, "Next": [{"id": "9", "name": "Next"}]}),
        library=lambda row: {"albums": ["Hollow"], "tracks": []},
        catalog=catalog,
        stop=lambda: flag["stop"],
    )
    assert calls == [("1", False)]
    assert out["stopped"] is True and out["matched"] == [] and len(out["fuzzy"]) == 1


def test_a_library_winner_already_followed_is_skipped():
    cands = [{"id": "1", "name": "A"}, {"id": "2", "name": "A"}]
    out = artist_import.resolve(
        ["A"],
        search=_search_from({"A": cands}),
        followed_ids=["2"],
        library=lambda row: {"albums": ["Hollow"], "tracks": []},
        catalog=_catalog_from({("1", False): [], ("2", False): ["Hollow"]}),
    )
    assert out["skipped"][0]["reason"] == "already-followed"


def test_title_key_meets_editions_and_spellings():
    assert artist_import.title_key("Hollow (Deluxe Edition)") == artist_import.title_key("Hollow")
    assert artist_import.title_key("Café Bleu") == artist_import.title_key("Cafe Bleu")
