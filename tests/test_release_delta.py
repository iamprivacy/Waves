"""The shared release-delta core: edition groups, seeding sets, freshness."""

from waves.release_delta import all_group_ids, collapse_atmos_twins, edition_group_ids, split_fresh


def test_edition_group_ids_own_plus_siblings():
    assert edition_group_ids({"id": 1, "sibling_ids": [2, "3", None, ""]}) == {"1", "2", "3"}
    assert edition_group_ids({"id": None}) == set()
    assert edition_group_ids({}) == set()


def test_all_group_ids_flattens_every_group():
    releases = [{"id": "a", "sibling_ids": ["b"]}, {"id": "c"}]
    assert all_group_ids(releases) == {"a", "b", "c"}


def test_split_fresh_basic_delta():
    fresh, absorbed = split_fresh([{"id": "old"}, {"id": "new"}], {"old"})
    assert [r["id"] for r in fresh] == ["new"]
    assert absorbed == {"old", "new"}


def test_split_fresh_seen_edition_absorbs_whole_group():
    # The baseline knows edition "b"; the caller's dedup now keeps "a" of the
    # same album. Nothing is new, and both ids join the baseline.
    fresh, absorbed = split_fresh([{"id": "a", "sibling_ids": ["b"]}], {"b"})
    assert fresh == []
    assert absorbed == {"a", "b"}


def test_split_fresh_two_editions_in_one_batch_count_once():
    releases = [
        {"id": "a", "sibling_ids": ["b"]},
        {"id": "b", "sibling_ids": ["a"]},
    ]
    fresh, absorbed = split_fresh(releases, set())
    assert [r["id"] for r in fresh] == ["a"]
    assert absorbed == {"a", "b"}


def test_split_fresh_skips_idless_and_keeps_order():
    releases = [{"title": "no id"}, {"id": "x"}, {"id": "y"}]
    fresh, absorbed = split_fresh(releases, set())
    assert [r["id"] for r in fresh] == ["x", "y"]
    assert absorbed == {"x", "y"}


def test_split_fresh_does_not_mutate_the_callers_baseline():
    seen = {"old"}
    split_fresh([{"id": "new"}], seen)
    assert seen == {"old"}


def _row(rid, quality="HI-RES", title="I Know Too Much", day="2026-09-06", tracks=10, **extra):
    return {
        "id": rid,
        "title": title,
        "artist": "Ellie",
        "date": day,
        "listed": "",
        "tracks": tracks,
        "quality": quality,
        **extra,
    }


def test_collapse_atmos_twins_folds_the_twin_into_the_stereo_row():
    stereo, twin = _row("s", sibling_ids=["s2"]), _row("a", "ATMOS", sibling_ids=["a2"], fresh=True)
    out = collapse_atmos_twins([twin, stereo, _row("other", title="Other")])
    assert [r["id"] for r in out] == ["s", "other"]
    assert out[0]["sibling_ids"] == ["s2", "a", "a2"] and out[0]["fresh"] is True


def test_collapse_atmos_twins_matches_a_dolby_atmos_tail_and_keeps_lone_atmos_rows():
    out = collapse_atmos_twins(
        [
            _row("s"),
            _row("a", "ATMOS", title="I Know Too Much (Dolby Atmos)"),
            _row("lone", "ATMOS", title="Spatial Only"),
            _row("b", "ATMOS", tracks=12),
            _row("c", "ATMOS", day="2026-09-07"),
        ]
    )
    assert [r["id"] for r in out] == ["s", "lone", "b", "c"]
    assert out[0]["sibling_ids"] == ["a"]


def test_collapse_atmos_twins_reads_the_sweep_row_names_too():
    rows = [
        {"id": "s", "title": "LP", "artist_name": "A", "date": "2026-01-01", "tracks": 3, "quality": "LOSSLESS"},
        {"id": "a", "title": "LP", "artist_name": "A", "date": "2026-01-01", "tracks": 3, "quality": "ATMOS"},
    ]
    assert [r["id"] for r in collapse_atmos_twins(rows)] == ["s"]
