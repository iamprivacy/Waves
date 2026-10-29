"""Tests for waves/follows.py: the follow list, the release baseline and the
feed window behind the Releases pane.

Pure standard library (sqlite), no Qt. The promises: following seeds silently
so the first sweep announces nothing; a later sweep announces only genuinely
new releases, exactly once each; the TIDAL mirror never drops a follow the
user made by hand; the feed shows one row per release, newest day first.
"""

from __future__ import annotations

import pytest

from waves.follows import DL_QUEUED, DL_WAITING, FollowStore


def _store(tmp_path):
    return FollowStore(str(tmp_path / "releases.sqlite3"))


def _rel(rid, date="2026-10-01", title="Album", rtype="album", **extra):
    return {
        "id": rid,
        "title": title,
        "artist": "Artist",
        "artist_id": "1",
        "art": "u",
        "year": date[:4],
        "date": date,
        "listed": "",
        "rtype": rtype,
        **extra,
    }


def _seed(s, aid="1", releases=None):
    """Follow + absorb a first catalog, the way the first sweep step does."""
    s.follow(aid, "Artist", "img")
    assert s.record_check(aid, releases if releases is not None else [_rel("a")]) == []


# --- follow / unfollow -----------------------------------------------------


def test_follow_marks_followed_and_is_unseeded(tmp_path):
    s = _store(tmp_path)
    assert s.is_followed("1") is False
    assert s.follow("1", "Artist", "img") is True
    assert s.is_followed("1") is True
    info = s.follow_info("1")
    assert info["origin"] == "manual" and info["touched"] and not info["seeded"]
    assert s.follow("1", "Artist", "img") is False  # already there
    s.close()


def test_first_catalog_is_absorbed_silently(tmp_path):
    s = _store(tmp_path)
    s.follow("1", "Artist", "img")
    back = [_rel("a"), _rel("b"), _rel("c")]
    assert s.record_check("1", back) == []
    assert s.unseen_count() == 0
    # The rows still reach the feed, acknowledged.
    assert sorted(r["id"] for r in s.feed()) == ["a", "b", "c"]
    assert all(not r["fresh"] for r in s.feed())
    # A genuinely new release after that baseline is announced normally.
    fresh = s.record_check("1", [*back, _rel("new", date="2026-10-12")])
    assert [r["id"] for r in fresh] == ["new"]
    assert s.unseen_count() == 1
    assert [r["id"] for r in s.feed() if r["fresh"]] == ["new"]
    s.close()


def test_empty_and_partial_fetches_never_seed(tmp_path):
    s = _store(tmp_path)
    s.follow("1", "Artist", "")
    assert s.record_check("1", []) == []
    assert not s.follow_info("1")["seeded"]
    assert s.record_check("1", [_rel("a")], complete=False) == []
    assert not s.follow_info("1")["seeded"]
    full = [_rel("a"), _rel("e1"), _rel("e2")]
    assert s.record_check("1", full) == []  # the first COMPLETE fetch seeds
    assert s.follow_info("1")["seeded"]
    fresh = s.record_check("1", [*full, _rel("new")])
    assert [r["id"] for r in fresh] == ["new"]
    s.close()


def test_two_empty_complete_fetches_seed_an_empty_baseline(tmp_path):
    # An artist followed before their first release: the debut is announced.
    s = _store(tmp_path)
    s.follow("1", "Artist", "")
    assert s.record_check("1", []) == []
    assert s.record_check("1", [], complete=False) == []  # a partial empty never counts
    assert not s.follow_info("1")["seeded"]
    assert s.record_check("1", []) == []
    assert s.follow_info("1")["seeded"]
    fresh = s.record_check("1", [_rel("debut")])
    assert [r["id"] for r in fresh] == ["debut"]
    assert s.unseen_count() == 1
    s.close()


def test_new_release_is_detected_once(tmp_path):
    s = _store(tmp_path)
    _seed(s)
    assert [r["id"] for r in s.record_check("1", [_rel("a"), _rel("b")])] == ["b"]
    assert s.record_check("1", [_rel("a"), _rel("b")]) == []
    s.acknowledge_all()
    assert s.record_check("1", [_rel("a"), _rel("b")]) == []
    s.close()


def test_record_on_unfollowed_artist_is_noop(tmp_path):
    s = _store(tmp_path)
    assert s.record_check("9", [_rel("a")]) == []
    assert s.feed() == []
    s.close()


def test_unfollow_forgets_everything(tmp_path):
    s = _store(tmp_path)
    _seed(s)
    s.record_check("1", [_rel("a"), _rel("b")])
    assert s.unseen_count() == 1
    s.unfollow("1")
    assert s.is_followed("1") is False
    assert s.feed() == [] and s.unseen_count() == 0
    # Following again starts clean: the first catalog seeds silently again.
    s.follow("1", "Artist", "img")
    assert s.record_check("1", [_rel("a"), _rel("b")]) == []
    s.close()


def test_followed_artists_ordered_by_name(tmp_path):
    s = _store(tmp_path)
    s.follow("1", "Zed", "")
    s.follow("2", "alice", "")
    s.follow("3", "Bob", "")
    assert [a["name"] for a in s.followed_artists()] == ["alice", "Bob", "Zed"]
    assert s.followed_count() == 3 and s.followed_ids() == {"1", "2", "3"}
    s.close()


def test_followed_artists_carry_their_latest_release(tmp_path):
    s = _store(tmp_path)
    _seed(
        s,
        releases=[
            _rel("old", date="2026-01-05", title="Early"),
            _rel("re", date="2010-01-01", listed="2026-09-09", title="Reissue", rtype="ep"),
        ],
    )
    s.follow("2", "Quiet", "")
    rows = {r["artist_id"]: r for r in s.followed_artists()}
    assert (rows["1"]["last_release"], rows["1"]["last_title"], rows["1"]["last_rtype"]) == (
        "2026-09-09",
        "Reissue",
        "ep",
    )
    assert (rows["2"]["last_release"], rows["2"]["last_title"], rows["2"]["last_rtype"]) == ("", "", "")
    s.close()


def test_followed_artists_carry_the_releases_the_views_draw(tmp_path):
    s = _store(tmp_path)
    old = [_rel(f"o{i}", date=f"20{10 + i:02d}-03-01", title=f"Old {i}") for i in range(12)]
    recent = [
        _rel("new", date="2026-09-01", title="New", rtype="single", tracks=3),
        _rel("ahead", date="2026-11-20", title="Ahead", rtype="ep"),
        # A Dolby Atmos twin of New: folded, never a second mark.
        _rel("twin", date="2026-09-01", title="New", rtype="single", tracks=3, quality="ATMOS"),
    ]
    _seed(s, releases=old + recent)
    s.follow("2", "Quiet", "")
    rows = {r["artist_id"]: r for r in s.followed_artists("2026-10-14")}
    rels = rows["1"]["releases"]
    assert [r["id"] for r in rels[:3]] == ["ahead", "new", "o11"]
    assert rels[1] == {"id": "new", "title": "New", "art": "u", "day": "2026-09-01", "rtype": "single", "tracks": 3}
    # Every one whatever its age (here fourteen), newest first, no twin.
    assert len(rels) == 14 and "twin" not in {r["id"] for r in rels}
    assert (rows["1"]["total"], rows["1"]["since"]) == (14, "2010")
    assert (rows["2"]["releases"], rows["2"]["total"], rows["2"]["since"]) == ([], 0, "")
    s.close()


def test_followed_artists_carry_the_whole_career_on_first_release_days(tmp_path, monkeypatch):
    import waves.follows as follows

    monkeypatch.setattr(follows, "FOLLOW_SHELF", 2)
    monkeypatch.setattr(follows, "FOLLOW_CAREER_CAP", 4)
    s = _store(tmp_path)
    rels = [_rel(f"r{m}", date=f"2026-{m:02d}-01", title=f"R{m}") for m in range(1, 4)]
    _seed(
        s,
        releases=[
            *rels,
            _rel("ancient", date="2001-01-01", title="Ancient"),
            # Listed this year, first out in 1999: the career puts it in 1999.
            _rel("reissue", date="1999-05-05", listed="2026-09-20", title="Reissue (Remaster)"),
            _rel("undated", date="", title="Undated"),
        ],
    )
    (row,) = s.followed_artists("2026-10-14")
    # The shelf goes by the listing day, so the reissue leads it.
    assert [r["id"] for r in row["releases"]] == ["reissue", "r3"]
    assert row["releases"][0]["day"] == "2026-09-20"
    # The career goes by the first day out, newest first, within the cap;
    # nothing undated is on it.
    assert [r["id"] for r in row["career"]] == ["r3", "r2", "r1", "ancient"]
    monkeypatch.setattr(follows, "FOLLOW_CAREER_CAP", 400)
    (row,) = s.followed_artists("2026-10-14")
    assert [(r["id"], r["day"]) for r in row["career"][-2:]] == [("ancient", "2001-01-01"), ("reissue", "1999-05-05")]
    # An undated release is not counted either: it is on no shelf or line.
    assert row["total"] == 5
    s.close()


def test_refollow_keeps_origin_and_never_downgrades_touched(tmp_path):
    s = _store(tmp_path)
    s.follow("1", "Old Name", "", origin="library", touched=True)
    s.follow("1", "New Name", "img", origin="manual", touched=False)
    info = s.follow_info("1")
    assert info["name"] == "New Name" and info["art"] == "img"
    assert info["origin"] == "library" and info["touched"]
    s.close()


def test_follow_many_is_one_bulk_add_that_skips_existing(tmp_path):
    s = _store(tmp_path)
    s.follow("1", "Kept", "")
    added = s.follow_many([{"id": "1", "name": "X"}, {"id": "2", "name": "B"}, {"id": "", "name": "no id"}])
    assert added == 1
    assert s.follow_info("1")["name"] == "Kept"
    assert s.follow_info("2")["origin"] == "library" and s.follow_info("2")["touched"]
    s.close()


def test_release_without_id_or_date_is_handled(tmp_path):
    s = _store(tmp_path)
    _seed(s)
    fresh = s.record_check("1", [_rel("a"), {"title": "no id"}, _rel("undated", date="")])
    # Undated releases join the baseline (announced once) but never the feed.
    assert [r["id"] for r in fresh] == ["undated"]
    assert sorted(r["id"] for r in s.feed()) == ["a"]
    assert s.record_check("1", [_rel("a"), _rel("undated", date="")]) == []
    s.close()


# --- edition groups ----------------------------------------------------------


def test_settings_change_does_not_resurface_a_seen_edition_group(tmp_path):
    s = _store(tmp_path)
    _seed(s, releases=[_rel("hi-res-id")])
    same_album = {**_rel("lossless-id"), "sibling_ids": ["hi-res-id", "lossless-id"]}
    assert s.record_check("1", [same_album]) == []
    back = {**_rel("hi-res-id"), "sibling_ids": ["hi-res-id", "lossless-id"]}
    assert s.record_check("1", [back]) == []
    s.close()


def test_fresh_release_absorbs_all_of_its_editions(tmp_path):
    s = _store(tmp_path)
    _seed(s)
    drop = {**_rel("new-explicit", title="New LP"), "sibling_ids": ["new-explicit", "new-clean"]}
    assert [r["id"] for r in s.record_check("1", [_rel("a"), drop])] == ["new-explicit"]
    other = {**_rel("new-clean", title="New LP"), "sibling_ids": ["new-explicit", "new-clean"]}
    assert s.record_check("1", [_rel("a"), other]) == []
    s.close()


def test_empty_seed_absorb_covers_sibling_editions(tmp_path):
    s = _store(tmp_path)
    s.follow("1", "Artist", "")
    first = {**_rel("survivor"), "sibling_ids": ["survivor", "other-edition"]}
    assert s.record_check("1", [first]) == []
    swapped = {**_rel("other-edition"), "sibling_ids": ["survivor", "other-edition"]}
    assert s.record_check("1", [swapped]) == []
    s.close()


# --- the feed window ---------------------------------------------------------


def test_feed_is_newest_day_first_and_listed_wins(tmp_path):
    s = _store(tmp_path)
    _seed(
        s,
        releases=[
            _rel("old", date="2026-01-05"),
            _rel("mid", date="2026-06-01"),
            _rel("re", date="2010-01-01", listed="2026-09-09"),
        ],
    )
    assert [r["id"] for r in s.feed()] == ["re", "mid", "old"]
    assert s.feed()[0]["listed"] == "2026-09-09"
    s.close()


def test_feed_shows_an_atmos_twin_once_and_the_badge_agrees(tmp_path):
    s = _store(tmp_path)
    _seed(s)
    stereo = _rel("s", title="I Know Too Much", tracks=10, quality="HI-RES")
    twin = _rel("a", title="I Know Too Much", tracks=10, quality="ATMOS")
    s.record_check("1", [_rel("a"), stereo, twin])
    assert [r["id"] for r in s.feed()] == ["s"]
    assert s.feed()[0]["fresh"] is True and s.unseen_count() == 1
    s.acknowledge_all()
    assert s.unseen_count() == 0
    s.close()


def test_the_badge_count_agrees_with_the_feed_without_building_it(tmp_path, monkeypatch):
    s = _store(tmp_path)
    _seed(s)
    rows = [
        _rel("a"),
        _rel("s", title="I Know Too Much", tracks=10, quality="HI-RES"),
        _rel("t", title="I Know Too Much (Dolby Atmos)", tracks=10, quality="ATMOS"),
        _rel("lone", title="Only In Atmos", tracks=8, quality="ATMOS"),
        _rel("old", date="2026-01-05", title="Older", quality="LOSSLESS"),
    ]
    s.record_check("1", rows)
    expected = sum(1 for r in s.feed() if r["fresh"])
    assert expected == 3
    # The badge is asked on the GUI thread: it must not dress the whole feed.
    monkeypatch.setattr(s, "feed", lambda *a, **k: pytest.fail("unseen_count built the feed"))
    assert s.unseen_count() == 3
    assert s.unseen_count("2026-06-01") == 2
    monkeypatch.undo()
    # Only the twin still unseen: it keeps its stereo row fresh, counted once.
    s._conn.execute("UPDATE releases SET acknowledged = 1 WHERE release_id != 't'")
    s._conn.commit()
    assert [r["id"] for r in s.feed() if r["fresh"]] == ["s"]
    assert s.unseen_count() == 1
    s.close()


def test_an_atmos_row_stored_before_its_stereo_twin_is_not_withdrawn(tmp_path):
    s = _store(tmp_path)
    _seed(s)
    twin = _rel("t", title="LP", tracks=10, quality="ATMOS")
    assert [r["id"] for r in s.record_check("1", [_rel("a"), twin])] == ["t"]
    s.set_dl_state("t", DL_QUEUED)
    # The stereo edition appears and the sweep folds the twin into it.
    stereo = {**_rel("s", title="LP", tracks=10, quality="HI-RES"), "sibling_ids": ["s", "t"], "atmos_ids": ["t"]}
    s.record_check("1", [_rel("a"), stereo])
    states = {r[0]: r[1] for r in s._conn.execute("SELECT release_id, dl_state FROM releases")}
    assert states.get("t") == DL_QUEUED
    assert sorted(r["id"] for r in s.feed()) == ["a", "s"]
    s.close()


def test_collab_shows_once_under_either_artist(tmp_path):
    s = _store(tmp_path)
    s.follow("1", "A", "")
    s.follow("2", "B", "")
    assert s.record_check("1", [_rel("x")]) == []
    assert s.record_check("2", [_rel("x")]) == []
    assert [r["id"] for r in s.feed()] == ["x"]
    # Fresh for one follower is fresh in the feed.
    _ = s.record_check("1", [_rel("x"), _rel("y")])
    assert s.record_check("2", [_rel("x")]) == []
    assert [r["id"] for r in s.feed() if r["fresh"]] == ["y"]
    assert s.unseen_count() == 1
    # Unfollowing one keeps the collab for the other.
    s.unfollow("1")
    assert [r["id"] for r in s.feed()] == ["x"]
    s.close()


def test_a_collab_new_to_two_followed_artists_is_announced_once(tmp_path):
    from waves.follows import DL_QUEUED

    s = _store(tmp_path)
    _seed(s, "1")
    _seed(s, "2")
    assert [r["id"] for r in s.record_check("1", [_rel("a"), _rel("col")])] == ["col"]
    s.set_dl_state("col", DL_QUEUED)
    assert s.acknowledge_all() == ["col"]
    # The second artist's step lists the same album: not news again, and its
    # row takes the first one's state (seen, queued).
    assert s.record_check("2", [_rel("a"), _rel("col")]) == []
    assert s.unseen_count() == 0
    rows = s._conn.execute(
        "SELECT acknowledged, dl_state, detected_at FROM releases WHERE release_id = 'col'"
    ).fetchall()
    assert len(rows) == 2 and all(r[0] == 1 and r[1] == DL_QUEUED and r[2] > 0 for r in rows)
    # An edition of it (a sibling id) counts as the same release.
    _seed(s, "3")
    assert s.record_check("3", [_rel("a"), _rel("col-dlx", sibling_ids=["col-dlx", "col"])]) == []
    s.close()


def test_a_collab_absorbed_into_one_baseline_is_still_news_for_another(tmp_path):
    s = _store(tmp_path)
    _seed(s, "2")
    # Artist 1 is followed after the album came out: absorbed, never announced.
    s.follow("1", "A", "")
    assert s.record_check("1", [_rel("col")]) == []
    assert [r["id"] for r in s.record_check("2", [_rel("a"), _rel("col")])] == ["col"]
    s.close()


def test_a_new_release_older_than_the_window_is_absorbed_silently(tmp_path):
    s = _store(tmp_path)
    _seed(s, releases=[_rel("a", date="2026-09-01")])
    got = s.record_check(
        "1",
        [_rel("a", date="2026-09-01"), _rel("old", date="2008-03-01"), _rel("new", date="2026-10-01")],
        keep_from="2025-10-01",
    )
    assert [r["id"] for r in got] == ["new"]
    assert sorted(r["id"] for r in s.feed()) == ["a", "new"]
    # It joined the baseline, so it is never news later either.
    assert s.record_check("1", [_rel("old", date="2008-03-01")], keep_from="2000-01-01") == []
    s.close()


def test_withdrawn_release_inside_the_fetched_span_is_pruned(tmp_path):
    s = _store(tmp_path)
    _seed(s, releases=[_rel("a", date="2026-09-01"), _rel("b", date="2026-08-01"), _rel("old", date="2025-01-01")])
    # A complete fetch covering August onwards no longer lists "b": gone.
    # "old" sits before the span the fetch covered: a windowed fetch would
    # not have listed it anyway, so it stays.
    s.record_check("1", [_rel("a", date="2026-09-01"), _rel("c", date="2026-07-20")])
    assert sorted(r["id"] for r in s.feed()) == ["a", "c", "old"]
    # A partial fetch prunes nothing.
    s.record_check("1", [_rel("a", date="2026-09-01")], complete=False)
    assert sorted(r["id"] for r in s.feed()) == ["a", "c", "old"]
    s.close()


def test_the_window_is_read_through_and_the_floor_is_pruned(tmp_path):
    s = _store(tmp_path)
    rows = [
        _rel("a", date="2026-09-01"),
        _rel("old", date="2025-01-01"),
        _rel("re", date="2001-01-01", listed="2026-05-05"),
        _rel("ancient", date="1990-01-01"),
    ]
    _seed(s, releases=rows)
    s.record_check("1", rows, keep_from="2026-01-01", prune_from="2016-10-01")
    # The window hides "old" without deleting it; the floor deletes "ancient".
    assert sorted(r["id"] for r in s.feed("2026-01-01")) == ["a", "re"]
    assert sorted(r["id"] for r in s.feed("2026-08-01")) == ["a"]
    assert sorted(r["id"] for r in s.feed("2024-10-01")) == ["a", "old", "re"]
    assert "ancient" not in {r["id"] for r in s.feed()}
    s.close()


def test_an_unseen_release_outside_the_window_does_not_badge(tmp_path):
    s = _store(tmp_path)
    _seed(s)
    s.record_check("1", [_rel("a"), _rel("new", date="2026-03-01")])
    assert s.unseen_count() == 1
    assert s.unseen_count("2026-06-01") == 0
    s.close()


def test_each_section_prunes_withdrawn_rows_inside_its_own_span(tmp_path):
    s = _store(tmp_path)
    eps = [_rel(f"ep{i}", date=f"2026-0{i}-01", rtype="ep") for i in range(1, 7)]
    albums = [_rel("lp", date="2010-01-01")]
    _seed(s, releases=albums + eps)
    # The EP list is capped: this fetch's newest EPs reach back only to
    # April, so March and earlier were not listed, but they still exist.
    s.record_check("1", albums + eps[3:])
    assert sorted(r["id"] for r in s.feed()) == ["ep1", "ep2", "ep3", "ep4", "ep5", "ep6", "lp"]
    # An EP inside the EP span that is no longer listed is withdrawn.
    s.record_check("1", albums + eps[4:] + [eps[2]])
    assert "ep4" not in {r["id"] for r in s.feed()}
    s.close()


def test_existing_row_keeps_its_acknowledged_and_dl_state(tmp_path):
    s = _store(tmp_path)
    _seed(s)
    s.record_check("1", [_rel("a"), _rel("b", title="v1")])
    s.set_dl_state("b", DL_WAITING)
    s.record_check("1", [_rel("a"), _rel("b", title="v2")])
    row = next(r for r in s.feed() if r["id"] == "b")
    assert row["title"] == "v2" and row["fresh"] and row["dl_state"] == DL_WAITING
    s.close()


def test_acknowledge_all_returns_ids_and_clears_badge(tmp_path):
    s = _store(tmp_path)
    _seed(s)
    s.record_check("1", [_rel("a"), _rel("b"), _rel("c")])
    assert s.unseen_count() == 2
    assert s.acknowledge_all() == ["b", "c"]
    assert s.unseen_count() == 0 and s.acknowledge_all() == []
    assert all(not r["fresh"] for r in s.feed())
    s.close()


def test_pending_auto_downloads_wait_for_the_day(tmp_path):
    s = _store(tmp_path)
    _seed(s)
    s.record_check("1", [_rel("a"), _rel("soon", date="2026-12-25"), _rel("now", date="2026-10-01")])
    s.set_dl_state("soon", DL_WAITING)
    s.set_dl_state("now", DL_WAITING)
    assert [r["id"] for r in s.pending_auto_downloads("2026-10-13")] == ["now"]
    assert [r["id"] for r in s.pending_auto_downloads("2026-12-25")] == ["now", "soon"]
    s.set_dl_state("now", DL_QUEUED)
    assert [r["id"] for r in s.pending_auto_downloads("2026-10-13")] == []
    s.close()


# --- the sweep order ---------------------------------------------------------


def test_next_due_picks_never_checked_then_stalest(tmp_path):
    s = _store(tmp_path)
    s.follow("1", "A", "")
    s.follow("2", "B", "")
    assert s.never_checked_count() == 2
    assert s.next_due(10**10)["artist_id"] == "1"
    s.record_check("1", [_rel("a")])
    assert s.next_due(10**10)["artist_id"] == "2"
    s.record_check("2", [_rel("b")])
    assert s.never_checked_count() == 0
    # Both fresh: nothing is due before a cutoff in the past.
    assert s.next_due(0) is None
    s.close()


def test_record_failure_moves_the_artist_back_and_counts(tmp_path):
    s = _store(tmp_path)
    s.follow("1", "A", "")
    s.follow("2", "B", "")
    s.record_failure("1")
    assert s.next_due(10**10)["artist_id"] == "2"
    assert s.follow_info("1")["last_checked"] > 0
    # A later good check resets the count.
    s.record_check("1", [_rel("a")])
    s.close()


# --- the TIDAL mirror --------------------------------------------------------


def test_mirror_adds_marks_and_removes_untouched_rows(tmp_path):
    s = _store(tmp_path)
    s.follow("m", "Manual", "")
    added, removed = s.sync_tidal_favorites([{"id": "t1", "name": "T1", "art": "a"}, {"id": "m", "name": "Manual"}])
    assert (added, removed) == (1, 0)
    assert s.follow_info("t1")["origin"] == "tidal" and not s.follow_info("t1")["touched"]
    assert s.follow_info("t1")["tidal_fav"] and s.follow_info("m")["tidal_fav"]
    # Gone from TIDAL: the untouched mirror row drops, the manual one stays.
    added, removed = s.sync_tidal_favorites([])
    assert (added, removed) == (0, 1)
    assert s.is_followed("t1") is False
    assert s.is_followed("m") and not s.follow_info("m")["tidal_fav"]
    s.close()


def test_mirror_keeps_a_touched_row_and_a_partial_walk_never_removes(tmp_path):
    s = _store(tmp_path)
    s.sync_tidal_favorites([{"id": "t1", "name": "T1"}, {"id": "t2", "name": "T2"}])
    s.touch("t1")
    s.sync_tidal_favorites([{"id": "t2", "name": "T2"}], partial=True)
    assert s.is_followed("t1") and s.is_followed("t2")
    s.sync_tidal_favorites([])
    assert s.is_followed("t1") and not s.is_followed("t2")
    s.close()


def test_unfollow_remembered_suppresses_the_mirror_until_tidal_drops_it(tmp_path):
    s = _store(tmp_path)
    s.sync_tidal_favorites([{"id": "t1", "name": "T1"}])
    s.unfollow("t1", remember_tidal=True)
    assert s.sync_tidal_favorites([{"id": "t1", "name": "T1"}]) == (0, 0)
    assert s.is_followed("t1") is False
    # Dropped on TIDAL too: the memory clears, a later re-favorite mirrors.
    s.sync_tidal_favorites([])
    assert s.sync_tidal_favorites([{"id": "t1", "name": "T1"}]) == (1, 0)
    # A hand follow clears the memory as well.
    s.unfollow("t1", remember_tidal=True)
    s.follow("t1", "T1", "")
    assert s.follow_info("t1")["origin"] == "manual"
    s.sync_tidal_favorites([{"id": "t1", "name": "T1"}])
    assert s.follow_info("t1")["tidal_fav"]
    s.close()


# --- tray, meta, reopen ------------------------------------------------------


def test_tray_round_trips_and_dismiss_is_remembered(tmp_path):
    s = _store(tmp_path)
    s.tray_record([{"norm": "foo", "name": "Foo", "reason": "fuzzy", "candidates": [{"id": "1", "name": "Fooo"}]}])
    s.tray_record([{"norm": "bar", "name": "Bar", "reason": "unmatched"}])
    assert [r["name"] for r in s.tray_rows()] == ["Bar", "Foo"]
    assert s.tray_rows()[1]["candidates"][0]["id"] == "1"
    assert s.tray_count() == 2
    s.tray_remove("foo")
    s.tray_dismiss("bar")
    assert s.tray_rows() == [] and s.tray_dismissed_norms() == {"bar"}
    s.close()


def test_meta_and_persistence_across_reopen(tmp_path):
    s = _store(tmp_path)
    _seed(s)
    s.record_check("1", [_rel("a"), _rel("b")])
    s.meta_set("last_round_end", "123")
    s.close()
    s = _store(tmp_path)
    assert s.is_followed("1") and s.unseen_count() == 1
    assert s.meta_get("last_round_end") == "123" and s.meta_get("missing", "d") == "d"
    assert s.record_check("1", [_rel("a"), _rel("b")]) == []
    s.close()


def test_search_folds_accents_needs_every_word_and_reaches_past_any_window(tmp_path):
    s = _store(tmp_path)
    _seed(s)
    s.record_check(
        "1",
        [
            _rel("a"),
            _rel("bj", "2026-09-01", title="Homogenic", artist="Björk"),
            _rel("old", "1997-09-22", title="Homogenic Live", artist="Björk"),
            _rel("co", "2026-08-01", title="Duet", artists=[{"id": "9", "name": "Beyoncé"}]),
            _rel("s", "2026-07-01", title="Too Much", tracks=10, quality="HI-RES"),
            _rel("t", "2026-07-01", title="Too Much", tracks=10, quality="ATMOS"),
        ],
    )
    rows, more = s.search("BJORK  homogenic")
    assert [r["id"] for r in rows] == ["bj", "old"] and more is False
    assert [r["id"] for r in s.search("beyonce")[0]] == ["co"]
    assert [r["id"] for r in s.search("homogenic live")[0]] == ["old"]
    # A folded Atmos twin is one result, and a blank needle finds nothing.
    assert [r["id"] for r in s.search("too much")[0]] == ["s"]
    assert s.search("   ") == ([], False)
    # Newest first, cut at the limit, saying more matched.
    rows, more = s.search("homogenic", limit=1)
    assert [r["id"] for r in rows] == ["bj"] and more is True
    s.close()


def test_a_section_listed_whole_prunes_its_oldest_day_too(tmp_path):
    # A fetch cut at its limit may leave rows sharing its oldest day just
    # past the cut, so that day is kept; a section listed whole has nothing
    # past it, so a row gone from that day is withdrawn.
    s = _store(tmp_path)
    head = [_rel("a", "2026-03-01", title="A"), _rel("b", "2026-01-01", title="B")]
    _seed(s, releases=[*head, _rel("tie", "2026-01-01", title="Tie")])
    s.record_check("1", head, full={True: True, False: True})
    assert "tie" in {r["id"] for r in s.feed()}
    s.record_check("1", head, full={True: False, False: False})
    assert "tie" not in {r["id"] for r in s.feed()}
    s.close()


def test_search_counts_its_limit_under_the_feed_filter(tmp_path):
    s = _store(tmp_path)
    s.follow("A", "Artist", "")
    rows = [_rel(f"s{i}", f"2026-0{1 + i % 9}-01", title=f"Love Single {i}", rtype="single") for i in range(6)]
    rows.append(_rel("lp", "2019-01-01", title="Love Album"))
    rows.append(_rel("soon", "2099-01-01", title="Love Later"))
    s.record_check("A", rows)
    # Unfiltered, the newest singles fill a small limit and the album is cut.
    hits, more = s.search("love", 3)
    assert "lp" not in {r["id"] for r in hits} and more is True
    # Under Albums the limit counts albums only, so the old album is found.
    hits, more = s.search("love", 3, kind="album", today="2026-10-15")
    assert [r["id"] for r in hits] == ["lp"] and more is False
    assert [r["id"] for r in s.search("love", 9, kind="upcoming", today="2026-10-15")[0]] == ["soon"]
    assert "soon" not in {r["id"] for r in s.search("love", 99, kind="all", today="2026-10-15")[0]}
    s.close()


def test_search_folds_the_store_once_while_it_is_unchanged(tmp_path, monkeypatch):
    s = _store(tmp_path)
    s.follow("A", "Artist", "")
    s.record_check("A", [_rel("a", title="Björk Debut")])
    built = []
    real = FollowStore.feed

    def counting(self, *a, **k):
        built.append(1)
        return real(self, *a, **k)

    monkeypatch.setattr(FollowStore, "feed", counting)
    for needle in ("b", "bj", "bjork", "bjork debut"):
        assert [r["id"] for r in s.search(needle)[0]] == ["a"]
    assert len(built) == 1
    # A sweep step that finds the same releases only restamps them (and the
    # follow's check time): nothing the feed shows changed, so it is kept.
    with s._lock:
        s._conn.execute("UPDATE releases SET updated_at = updated_at + 600")
        s._conn.commit()
    s.record_check("A", [_rel("a", title="Björk Debut")])
    s.meta_set("fresh_hint_day", "2026-10-02")
    assert [r["id"] for r in s.search("bjork")[0]] == ["a"] and len(built) == 1
    # A write is new matter: the next search reads the store again.
    s.record_check("A", [_rel("b", "2026-10-02", title="Björk Two"), _rel("a", title="Björk Debut")])
    assert {r["id"] for r in s.search("bjork")[0]} == {"a", "b"} and len(built) == 2
    # So is a change to a row's state the feed shows.
    s.set_dl_state("a", 2)
    assert {r["dl_state"] for r in s.search("bjork debut")[0]} == {2} and len(built) == 3
    # The rows handed out are copies: dressing one never touches the index.
    s.search("bjork")[0][0]["lib"] = "dressed"
    assert all("lib" not in r for r in s.search("bjork")[0])
    s.search_forget()
    s.search("bjork")
    assert len(built) == 4
    s.close()


def test_has_older_looks_only_inside_the_window(tmp_path):
    s = _store(tmp_path)
    s.follow("A", "Artist", "")
    s.record_check("A", [{"id": "a", "date": "2026-10-01"}, {"id": "b", "date": "2026-05-01"}])
    assert s.has_older("2026-07-01", "2026-01-01")
    assert not s.has_older("2026-07-01", "2026-06-01")
    assert not s.has_older("2026-05-01", "2026-01-01")


def test_record_deep_puts_older_releases_on_record_silently(tmp_path):
    s = _store(tmp_path)
    _seed(s)
    assert [r["id"] for r in s.record_check("1", [_rel("a"), _rel("n", "2026-10-02")])] == ["n"]
    assert s.unseen_count() == 1 and s.deep_read_at("1") == 0
    # The back catalog arrives: on record, acknowledged, never news; a row
    # the sweep holds is left alone, and so is another edition of it; a
    # release without a day stays out.
    older = [
        _rel("old", "1982-11-30"),
        _rel("n", "2026-10-02", title="Renamed"),
        _rel("n-deluxe", "2026-10-02", sibling_ids=["n-deluxe", "n"]),
        _rel("nodate", ""),
    ]
    assert s.record_deep("1", older, complete=False) == 1
    rows = {r["id"]: r for r in s.feed()}
    assert set(rows) == {"a", "n", "old"} and rows["n"]["title"] == "Album"
    assert s.unseen_count() == 1 and s.deep_read_at("1") == 0
    # Read to its end, the follow is stamped; the sweep listing the old
    # release later announces nothing, and reading it again adds nothing.
    assert s.record_deep("1", older, complete=True) == 0 and s.deep_read_at("1") > 0
    assert s.record_check("1", [_rel("a"), _rel("n", "2026-10-02"), _rel("old", "1982-11-30")]) == []
    assert s.unseen_count() == 1
    # Nothing for an artist no longer followed.
    assert s.record_deep("9", older) == 0 and s.deep_read_at("9") == 0
    s.close()
    assert FollowStore(str(tmp_path / "releases.sqlite3")).deep_read_at("1") > 0


def test_a_deep_read_reissue_survives_the_next_check(tmp_path):
    # TIDAL orders a section by release date, so a remaster released long
    # ago sits past the head however new its listing: the head's complete
    # fetch says nothing about it, and the next check must not drop it as
    # withdrawn. A row sharing the head's oldest day may lie just past the
    # cut, so it stays too; one inside the span the head did not list goes.
    s = _store(tmp_path)
    head = [_rel(f"h{i}", f"2026-{i + 1:02d}-01", title=f"H{i}") for i in range(6)]
    _seed(s, releases=head)
    deep = [
        {**_rel("remaster", "1985-03-01", title="Old One"), "listed": "2023-05-05"},
        _rel("tie", "2026-01-01", title="Same Day"),
        _rel("gone", "2026-03-15", title="Withdrawn"),
    ]
    assert s.record_deep("1", deep, complete=True) == 3
    s.record_check("1", head)
    assert {r["id"] for r in s.feed()} == {*(r["id"] for r in head), "remaster", "tie"}
    s.close()


# --- the MusicBrainz check -----------------------------------------------------


def _mbg(rgid, title, date="2000-01-01", rtype="album", release_id=""):
    return {"rgid": rgid, "title": title, "date": date, "rtype": rtype, "release_id": release_id}


def test_a_store_from_before_the_musicbrainz_check_grows_its_columns(tmp_path):
    import sqlite3

    path = str(tmp_path / "releases.sqlite3")
    s = FollowStore(path)
    s.follow("1", "Artist", "img")
    s.close()
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE followed DROP COLUMN mbid")
    conn.execute("ALTER TABLE followed DROP COLUMN mb_checked")
    conn.execute("DROP TABLE mb_releases")
    conn.commit()
    conn.close()
    s = FollowStore(path)
    info = s.follow_info("1")
    assert info["mbid"] == "" and info["mb_checked"] == 0
    assert s.mb_next_due(1)["artist_id"] == "1"
    assert s.record_mb("1", "m1", [_mbg("g1", "Lost")]) is True
    assert s.follow_info("1")["mbid"] == "m1" and s.mb_rows("1") == [_mbg("g1", "Lost")]
    s.close()


def test_record_mb_marks_what_tidal_lacks_against_the_career_as_it_stands(tmp_path):
    s = _store(tmp_path)
    _seed(s, releases=[_rel("a", "2026-10-01", title="Album"), _rel("b", "2001-05-05", title="Old One (Deluxe)")])
    assert s.career_rows("1") == [
        {"id": "a", "title": "Album", "art": "u", "day": "2026-10-01", "rtype": "album", "tracks": 0},
        {"id": "b", "title": "Old One (Deluxe)", "art": "u", "day": "2001-05-05", "rtype": "album", "tracks": 0},
    ]
    assert s.mb_next_due(1)["artist_id"] == "1"
    groups = [
        _mbg("g1", "Album", "2026-09-30", release_id="a"),
        _mbg("g2", "Lost", "1999-03"),
        _mbg("g3", "Old One", "2001", release_id="b"),
        _mbg("g4", "Undated", ""),
        _mbg("g5", "Gone", "2010-01-01T00:00:00", "single"),
        {"rgid": "", "title": "no id"},
    ]
    assert s.record_mb("1", "m1", groups) is True
    info = s.follow_info("1")
    assert info["mbid"] == "m1" and info["mb_checked"] > 0
    assert s.mb_next_due(info["mb_checked"]) is None and s.mb_next_due(info["mb_checked"] + 1)["artist_id"] == "1"
    assert [r["rgid"] for r in s.mb_rows("1")] == ["g1", "g5", "g3", "g2", "g4"]
    assert s.mb_rows("1")[1]["date"] == "2010-01-01"
    (row,) = s.followed_artists("2026-10-14")
    # Newest first; a bare month lands on its first day; nothing undated.
    assert row["missing"] == [
        {
            "id": "mb:g5",
            "rgid": "g5",
            "title": "Gone",
            "day": "2010-01-01",
            "shown": "2010-01-01",
            "rtype": "single",
            "mb": True,
        },
        {
            "id": "mb:g2",
            "rgid": "g2",
            "title": "Lost",
            "day": "1999-03-01",
            "shown": "1999-03",
            "rtype": "album",
            "mb": True,
        },
    ]
    assert [r["id"] for r in row["career"]] == ["a", "b"]
    # The same answer again changes nothing; one that fills a gap does. A
    # stored match counts while its row is on record ("a"), not once it
    # went ("x").
    assert s.record_mb("1", "m1", groups) is False
    assert s.record_mb("1", "m1", [*groups[:1], _mbg("g2", "Lost", "1999-03", release_id="x")]) is True
    assert [m["rgid"] for m in s.followed_artists()[0]["missing"]] == ["g2"]
    assert s.record_mb("1", "m1", [*groups[:1], _mbg("g2", "Lost", "1999-03", release_id="a")]) is True
    assert s.followed_artists()[0]["missing"] == []
    # The marks follow the career as it stands: a group recorded unmatched
    # whose title TIDAL now lists (a deep read filled it) is not missing,
    # and one recorded matched whose row went is.
    assert s.record_mb(
        "1", "m1", [_mbg("g6", "Album (Remastered)", "2025-12-01"), _mbg("g7", "Nope", "1990-01-01", release_id="zz")]
    )
    assert [m["rgid"] for m in s.followed_artists()[0]["missing"]] == ["g7"]
    # Touched: due again only once the stamp ages out.
    s.mb_touch("1", 5)
    assert s.follow_info("1")["mb_checked"] == 5 and s.mb_next_due(6)["artist_id"] == "1"
    # Nothing for an artist no longer followed, and unfollowing forgets.
    assert s.record_mb("9", "m9", groups) is False and s.mb_rows("9") == []
    s.unfollow("1")
    assert s.mb_rows("1") == []
    s.close()


def test_no_sure_artist_is_recorded_as_such_and_marks_nothing(tmp_path):
    s = _store(tmp_path)
    _seed(s)
    assert s.record_mb("1", "", []) is False
    info = s.follow_info("1")
    assert info["mbid"] == "" and info["mb_checked"] > 0
    assert s.followed_artists()[0]["missing"] == []
    # A full walk of the favorites that drops the follow drops its rows too.
    s.sync_tidal_favorites([{"id": "2", "name": "Mirrored"}])
    s.record_mb("2", "m2", [_mbg("g1", "Lost")])
    assert s.mb_rows("2") != []
    s.sync_tidal_favorites([], partial=False)
    assert s.mb_rows("2") == [] and not s.is_followed("2")
    s.close()


# --- music videos -------------------------------------------------------------


def _vid(vid, date="2026-10-01", title="Clip"):
    return {"id": vid, "title": title, "artist": "Artist", "artist_id": "1", "date": date, "duration_sec": 200}


def test_a_first_video_answer_is_taken_in_silently_then_new_ones_are_news(tmp_path):
    s = _store(tmp_path)
    _seed(s)
    assert s.record_videos("1", [_vid("10")]) == []
    fresh = s.record_videos("1", [_vid("11", "2026-10-02"), _vid("10")])
    assert [(r["id"], r["kind"], r["rtype"]) for r in fresh] == [("11", "video", "video")]
    feed = {(r["kind"], r["id"]): r for r in s.feed()}
    assert feed[("video", "11")]["fresh"] and not feed[("video", "10")]["fresh"]
    assert feed[("video", "11")]["duration_sec"] == 200
    # Announced once.
    assert s.record_videos("1", [_vid("11", "2026-10-02"), _vid("10")]) == []


def test_two_empty_video_answers_seed_an_empty_baseline(tmp_path):
    s = _store(tmp_path)
    _seed(s)
    assert s.record_videos("1", []) == [] and s.record_videos("1", []) == []
    assert [r["id"] for r in s.record_videos("1", [_vid("10")])] == ["10"]


def test_a_video_id_never_meets_the_album_with_the_same_number(tmp_path):
    s = _store(tmp_path)
    _seed(s, releases=[_rel("10")])
    s.record_videos("1", [_vid("10")])
    rows = s.feed()
    assert sorted((r["kind"], r["id"]) for r in rows) == [("album", "10"), ("video", "10")]
    # A fresh video with that number is still news; the album is not.
    fresh = s.record_videos("1", [_vid("10"), _vid("20")])
    assert [r["id"] for r in fresh] == ["20"]


def test_a_music_step_never_prunes_videos_and_a_whole_video_answer_does(tmp_path):
    s = _store(tmp_path)
    _seed(s, releases=[_rel("a"), _rel("e", rtype="ep")])
    s.record_videos("1", [_vid("10"), _vid("11")])
    s.record_check("1", [_rel("a"), _rel("e", rtype="ep")])
    assert {r["id"] for r in s.feed() if r["kind"] == "video"} == {"10", "11"}
    # A head cut at the limit says nothing about what it left out.
    s.record_videos("1", [_vid("10")], whole=False)
    assert {r["id"] for r in s.feed() if r["kind"] == "video"} == {"10", "11"}
    s.record_videos("1", [_vid("10")], whole=True)
    assert {r["id"] for r in s.feed() if r["kind"] == "video"} == {"10"}


def test_hiding_videos_leaves_them_out_of_every_read(tmp_path):
    s = _store(tmp_path)
    _seed(s, releases=[_rel("a", "2026-01-01")])
    s.record_videos("1", [_vid("10", "2026-02-01")])
    s.record_videos("1", [_vid("11", "2026-10-01"), _vid("10", "2026-02-01")])
    assert [r["kind"] for r in s.feed(videos=False)] == ["album"]
    assert s.unseen_count() == 1 and s.unseen_count(videos=False) == 0
    assert s.has_older("2026-03-01") and not s.has_older("2026-01-01")
    assert s.has_older("2026-02-15", videos=False) and s.search("Clip", videos=False) == ([], False)
    row = s.followed_artists("2026-10-14", videos=False)[0]
    assert row["last_rtype"] == "album" and all(e.get("kind") != "video" for e in row["releases"] + row["career"])


def test_hiding_featured_releases_leaves_them_out_of_every_read(tmp_path):
    s = _store(tmp_path)
    _seed(s, releases=[_rel("a", "2026-01-01")])
    # TIDAL lists another artist's release under the follow: it leads, the follow is a guest.
    guest = _rel("g", "2026-10-01", title="Lullabies", artist="Sparrow", artist_id="9")
    assert [r["id"] for r in s.record_check("1", [guest, _rel("a", "2026-01-01")])] == ["g"]
    assert [r["id"] for r in s.feed()] == ["g", "a"] and [r["id"] for r in s.feed(guests=False)] == ["a"]
    assert s.unseen_count() == 1 and s.unseen_count(guests=False) == 0
    assert s.has_older("2026-11-01", "2026-02-01") and not s.has_older("2026-11-01", "2026-02-01", guests=False)
    assert s.search("Lullabies")[0] and s.search("Lullabies", guests=False) == ([], False)
    row = s.followed_artists("2026-10-14", guests=False)[0]
    assert row["last_title"] == "Album" and row["total"] == 1
    assert [e["id"] for e in row["releases"] + row["career"]] == ["a"] * 2
    assert s.followed_artists("2026-10-14")[0]["total"] == 2
    # A hidden release is no gap on the timeline: the marks match the whole career.
    s.record_mb("1", "m1", [_mbg("x", "Lullabies", "2026-10-01")])
    assert s.followed_artists("2026-10-14", guests=False)[0]["missing"] == []
    # Its lead followed too, it is that follow's own release and shows.
    s.follow("9", "Sparrow", "")
    assert [r["id"] for r in s.feed(guests=False)] == ["g", "a"]
    assert s.search("Lullabies", guests=False)[0] and s.unseen_count(guests=False) == 1


def test_videos_stand_on_the_artists_page_but_not_in_its_counts(tmp_path):
    s = _store(tmp_path)
    _seed(s, releases=[_rel("a", "2020-05-01")])
    s.record_videos("1", [_vid("10", "2026-09-01")])
    row = s.followed_artists("2026-10-14")[0]
    assert row["last_rtype"] == "video" and row["last_release"] == "2026-09-01"
    assert row["releases"][0] == {
        "id": "10",
        "title": "Clip",
        "art": "",
        "day": "2026-09-01",
        "rtype": "video",
        "tracks": 0,
        "kind": "video",
    }
    assert [e["id"] for e in row["career"]] == ["10", "a"]
    assert row["total"] == 1 and row["since"] == "2020"
    # The MusicBrainz match sees the music alone.
    assert [e["id"] for e in s.career_rows("1")] == ["a"]


def test_video_pace_is_a_day_with_videos_and_a_week_without(tmp_path):
    s = _store(tmp_path)
    _seed(s)
    s.follow("2", "Quiet", "")
    s.record_check("2", [_rel("q")])
    now = 1_800_000_000
    assert s.video_due("1", now) and s.video_due("2", now)
    s.record_videos("1", [_vid("10")])
    s.record_videos("2", [])
    t = int(__import__("time").time())
    assert not s.video_due("1", t + 3600) and s.video_due("1", t + 86400)
    assert not s.video_due("2", t + 86400) and s.video_due("2", t + 7 * 86400)
    # A Check now asked after the last read makes both due at once.
    assert s.video_due("1", t + 60, rescan_at=t + 30) and s.video_due("2", t + 60, rescan_at=t + 30)
    assert not s.video_due("gone", now)
    # Videos only from years ago: the weekly pace, like none at all.
    s.follow("3", "Retired", "")
    s.record_check("3", [_rel("r")])
    s.record_videos("3", [_vid("30", "2015-05-05")])
    assert not s.video_due("3", t + 86400) and s.video_due("3", t + 7 * 86400)


def test_check_now_makes_every_follow_due(tmp_path):
    s = _store(tmp_path)
    _seed(s)
    t = int(__import__("time").time())
    assert s.rescan_at() == 0 and s.due_count(1) == 0
    s.request_rescan(t)
    assert s.rescan_at() == t and s.due_count(t + 10) == 1
    assert s.next_due(t + 10)["artist_id"] == "1"
    # A stamp ahead of the clock (it was set back since) reads as none.
    s.request_rescan(t + 3600)
    assert s.rescan_at() == 0


def test_a_waiting_video_comes_back_from_the_store_bare(tmp_path):
    s = _store(tmp_path)
    _seed(s)
    s.record_videos("1", [])
    s.record_videos("1", [])
    s.record_videos("1", [_vid("10", "2026-10-20")])
    s.set_dl_state("10", DL_WAITING, video=True)
    assert s.pending_auto_downloads("2026-10-20") == [
        {"id": "10", "kind": "video", "rtype": "video", "title": "Clip", "artist": "Artist"}
    ]
    # The video's id keeps its mark, so the pane never lights an album
    # that happens to share the number.
    assert s.acknowledge_all() == ["v:10"]


def test_a_new_video_never_lights_an_album_with_its_number(tmp_path):
    s = _store(tmp_path)
    _seed(s, releases=[_rel("10", "2026-09-01")])
    s.record_videos("1", [])
    s.record_videos("1", [])
    s.record_videos("1", [_vid("10", "2026-10-02")])
    assert s.acknowledge_all() == ["v:10"]


# --- featured releases (auto-download only) ----------------------------------


def test_a_first_featured_answer_is_taken_in_silently_then_new_ones_are_news(tmp_path):
    s = _store(tmp_path)
    _seed(s)
    assert s.record_guests("1", [_rel("g1", "2026-09-01")]) == []
    fresh = s.record_guests("1", [_rel("g2", "2026-10-01"), _rel("g1", "2026-09-01")])
    assert [(r["id"], r["guest_of"]) for r in fresh] == [("g2", "1")]
    # News once: the next read finds nothing new.
    assert s.record_guests("1", [_rel("g2", "2026-10-01"), _rel("g1", "2026-09-01")]) == []
    # Never in the feed, never counted on the badge.
    assert all(r["id"] not in ("g1", "g2") for r in s.feed())


def test_a_featured_release_never_meets_the_follows_own_baseline(tmp_path):
    s = _store(tmp_path)
    _seed(s, releases=[_rel("a")])
    s.record_guests("1", [_rel("x")])
    # An own release with the number of an already seen featured one is news.
    assert [r["id"] for r in s.record_check("1", [_rel("a"), _rel("x")])] == ["x"]
    assert [r["id"] for r in s.record_guests("1", [_rel("x"), _rel("a")])] == ["a"]


def test_featured_back_catalog_and_editions_are_absorbed(tmp_path):
    s = _store(tmp_path)
    _seed(s)
    s.record_guests("1", [_rel("g1")])
    old = _rel("old", "2020-01-01")
    deluxe = _rel("g1d", sibling_ids=["g1", "g1d"])
    assert s.record_guests("1", [old, deluxe, _rel("g1")], keep_from="2025-11-01") == []


def test_two_empty_featured_answers_seed_an_empty_baseline(tmp_path):
    s = _store(tmp_path)
    _seed(s)
    assert s.record_guests("1", []) == []
    assert s.record_guests("1", []) == []
    assert [r["id"] for r in s.record_guests("1", [_rel("debut")])] == ["debut"]


def test_switching_featured_back_on_takes_the_next_answer_in_silently(tmp_path):
    s = _store(tmp_path)
    _seed(s)
    s.record_guests("1", [_rel("g1")])
    s.reset_guest_seeding()
    assert s.record_guests("1", [_rel("g2"), _rel("g1")]) == []
    assert [r["id"] for r in s.record_guests("1", [_rel("g3"), _rel("g2")])] == ["g3"]


def test_featured_reads_are_paced_a_day_apart_and_check_now_asks_again(tmp_path):
    s = _store(tmp_path)
    _seed(s)
    now = int(__import__("time").time())
    assert s.guest_due("1", now)
    s.record_guests("1", [])
    assert not s.guest_due("1", now + 3600)
    assert s.guest_due("1", now + 23 * 3600 + 5)
    assert s.guest_due("1", now + 60, rescan_at=now + 30)
    assert not s.guest_due("nobody", now)
    s.unfollow("1")
    assert s.record_guests("1", [_rel("g")]) == []


def test_waiting_featured_releases_leave_on_their_day_or_with_the_switch(tmp_path):
    s = _store(tmp_path)
    s.guest_wait("g1", "2026-10-20", "1")
    s.guest_wait("g2", "2026-11-01", "2")
    assert s.guests_due("2026-10-19") == []
    assert s.guests_due("2026-10-20") == [("g1", "1")]
    assert s.guests_due("2026-10-20") == []
    assert s.clear_waiting() == 1
    assert s.guests_due("2099-01-01") == []


def test_waiting_lists_lose_nothing_to_writers_at_once(tmp_path):
    # A pool worker's retry and the GUI thread's drain write the same list.
    import threading

    s = _store(tmp_path)
    go = threading.Barrier(8)

    def write(n):
        go.wait()
        for k in range(20):
            s.guest_wait(f"g{n}-{k}", "2026-10-20", str(n))
            s.atmos_wait({f"t{n}-{k}": "2026-10-20"}, "album")

    workers = [threading.Thread(target=write, args=(n,)) for n in range(8)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    assert len(s.guests_due("2026-10-20")) == 160
    assert len(s.atmos_due("2026-10-20")) == 160
    s.close()


def test_release_kind_names_a_follows_own_release(tmp_path):
    s = _store(tmp_path)
    _seed(s, releases=[_rel("a")])
    s.record_check("1", [_rel("a"), _rel("s", rtype="single")])
    assert s.release_kind("s") == "single"
    assert s.release_kind("nope") == ""
