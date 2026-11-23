"""The follow store's side of shared profiles (several same-name artists on one
TIDAL profile, see waves.release_ties): releases set apart stay on record but
leave the feed, its badge and the Following views, and a new release that
nothing ties to the library's artist is set apart on arrival."""

from __future__ import annotations

import sqlite3

from waves import follows as f
from waves.follows import FollowStore
from waves.release_ties import Ties

TIES = Ties("1", albums=frozenset({"home"}), copies=frozenset({"home label"}))


def _store(tmp_path):
    return FollowStore(str(tmp_path / "releases.sqlite3"))


def _rel(rid, title, copy="2020 Home Label", date="2026-10-01"):
    return {
        "id": rid,
        "title": title,
        "artist": "Band",
        "artist_id": "1",
        "artists": [{"id": "1", "name": "Band"}],
        "art": "",
        "date": date,
        "listed": "",
        "year": date[:4],
        "rtype": "album",
        "copyright": copy,
        "upc": "0123",
    }


def _seeded(tmp_path):
    s = _store(tmp_path)
    s.follow("1", "Band", "")
    s.record_check("1", [_rel("a", "Home"), _rel("b", "Stranger", copy="2024 Far Label")])
    return s


def test_copyright_and_upc_go_on_record_and_refresh(tmp_path):
    s = _seeded(tmp_path)
    assert s.ties_inputs() == []  # not anchored yet: not judged
    s.anchor_note("1", f.ANCHOR_ANCHORED, "x")
    [row] = s.ties_inputs()
    got = {r["id"]: r for r in row["rows"]}
    assert got["b"]["copyright"] == "2024 Far Label" and got["a"]["upc"] == "0123"
    assert got["a"]["artists"] == [{"id": "1", "name": "Band"}]
    s.record_check("1", [_rel("a", "Home", copy="2021 Home Label"), _rel("b", "Stranger", copy="2024 Far Label")])
    [row] = s.ties_inputs()
    assert {r["id"]: r for r in row["rows"]}["a"]["copyright"] == "2021 Home Label"


def test_set_apart_leaves_the_feed_and_the_refresh_keeps_it_apart(tmp_path):
    s = _seeded(tmp_path)
    assert {r["id"] for r in s.feed()} == {"a", "b"}
    assert s.set_apart("1", TIES, ["b"], "sig")
    assert s.is_shared("1") and s.apart_ids("1") == {"b"}
    assert {r["id"] for r in s.feed()} == {"a"}
    assert [r["id"] for _h, r in s._search_index(True)] == ["a"]
    assert not s.set_apart("1", TIES, ["b"], "sig")  # nothing changed sides
    s.record_check("1", [_rel("a", "Home"), _rel("b", "Stranger", copy="2024 Far Label")])
    assert s.apart_ids("1") == {"b"}
    # Judged clean later: everything comes back.
    assert s.set_apart("1", None, [], "clean")
    assert not s.is_shared("1") and s.apart_ids("1") == set()
    assert {r["id"] for r in s.feed()} == {"a", "b"}


def test_a_new_untied_release_on_a_shared_profile_is_set_apart_and_not_news(tmp_path):
    s = _seeded(tmp_path)
    s.set_apart("1", TIES, ["b"], "sig")
    fresh = s.record_check(
        "1",
        [
            _rel("a", "Home"),
            _rel("b", "Stranger", copy="2024 Far Label"),
            _rel("c", "Next Record", date="2026-11-01"),
            _rel("d", "Other Act", copy="2026 Far Label", date="2026-11-01"),
        ],
    )
    assert [r["id"] for r in fresh] == ["c"]
    assert s.apart_ids("1") == {"b", "d"}
    assert s.unseen_count() == 1
    assert not s.has_older("2026-11-01", "2026-10-15")


def test_the_badge_and_older_pages_skip_apart_rows(tmp_path):
    s = _store(tmp_path)
    s.follow("1", "Band", "")
    s.record_check("1", [_rel("a", "Home")])
    s.set_apart("1", TIES, [], "sig")
    s.record_check("1", [_rel("a", "Home"), _rel("z", "Other Act", copy="x", date="2026-09-01")])
    assert s.unseen_count() == 0
    assert not s.has_older("2026-10-01")


def test_a_deep_read_sets_apart_what_nothing_ties(tmp_path):
    s = _seeded(tmp_path)
    s.set_apart("1", TIES, ["b"], "sig")
    assert (
        s.record_deep(
            "1", [_rel("old1", "Home Demos", date="2010-01-01"), _rel("old2", "Other", copy="q", date="2009-01-01")]
        )
        == 2
    )
    assert s.apart_ids("1") == {"b", "old2"}


def test_the_following_view_leaves_apart_rows_out(tmp_path):
    s = _seeded(tmp_path)
    s.set_apart("1", TIES, ["b"], "sig")
    [row] = s.followed_artists()
    assert [r["id"] for r in row["releases"]] == ["a"]


def test_an_old_store_grows_the_new_columns(tmp_path):
    path = tmp_path / "releases.sqlite3"
    s = FollowStore(str(path))
    s.close()
    con = sqlite3.connect(path)
    con.execute("ALTER TABLE releases DROP COLUMN apart")
    con.execute("ALTER TABLE releases DROP COLUMN copyright")
    con.execute("ALTER TABLE followed DROP COLUMN shared_json")
    con.commit()
    con.close()
    s = FollowStore(str(path))
    s.follow("1", "Band", "")
    s.record_check("1", [_rel("a", "Home")])
    assert s.set_apart("1", TIES, ["a"], "sig")
    s.close()


def test_a_deep_read_fills_in_the_copyright_of_rows_stored_without_it(tmp_path):
    s = _store(tmp_path)
    s.follow("1", "Band", "")
    old = _rel("a", "Home", copy="")
    old["upc"] = ""
    s.record_check("1", [old])
    s.anchor_note("1", f.ANCHOR_ANCHORED, "x")
    assert s.ties_fill_due("1", 10**10, 86400)
    s.record_deep("1", [{**_rel("a-hires", "Home"), "sibling_ids": ["a", "a-hires"]}], complete=False)
    [row] = s.ties_inputs()
    assert row["rows"][0]["copyright"] == "2020 Home Label" and row["rows"][0]["upc"] == "0123"
    # Unfinished: tried again only after the retry gap.
    assert not s.ties_fill_due("1", int(__import__("time").time()) + 10, 86400)
    s.record_deep("1", [], complete=True)
    assert not s.ties_fill_due("1", 10**10, 86400)


def test_a_shared_follow_the_library_no_longer_anchors_is_judged_again(tmp_path):
    s = _seeded(tmp_path)
    s.anchor_note("1", f.ANCHOR_ANCHORED, "x")
    assert s.set_apart("1", TIES, ["b"], "sig")
    # The library stops holding the albums that anchored it.
    s.anchor_note("1", f.ANCHOR_SILENT, "y")
    [row] = s.ties_inputs()
    assert row["id"] == "1" and row["sig"] == "sig"
    # Judged with nothing pinned: every release comes back, and it is not
    # judged again until the library anchors it.
    assert s.set_apart("1", None, [], "unknown")
    assert s.apart_ids("1") == set() and {r["id"] for r in s.feed()} == {"a", "b"}
    assert s.ties_inputs() == []


def test_a_release_set_apart_while_waiting_lets_no_atmos_edition_down(tmp_path):
    s = _store(tmp_path)
    s.follow("1", "Band", "")
    home = _rel("a", "Home", date="2099-01-01")
    stranger = {**_rel("b", "Stranger", copy="2024 Far Label", date="2099-01-01"), "sibling_ids": ["bx"]}
    s.record_check("1", [home, stranger])
    s.set_dl_state("b", f.DL_WAITING)
    s.atmos_wait({"bx": "2099-01-01", "ax": "2099-01-01"}, "album", "1")
    assert s.set_apart("1", TIES, ["b"], "sig")
    # Its folded twin is taken off the list unreturned; another's still goes.
    assert [t for t, *_ in s.atmos_due("2099-01-01")] == ["ax"]
    assert s.atmos_due("2099-01-01") == []
    # A twin TIDAL lists days later follows no download of the apart release.
    joined: list = []
    late = {**stranger, "atmos_ids": ["by"], "sibling_ids": ["bx", "by"]}
    s.record_check("1", [home, late], joined=joined)
    assert [j["state"] for j in joined if j["id"] == "by"] == [0]


def test_a_release_set_apart_is_no_follows_own_for_a_featured_download(tmp_path):
    s = _seeded(tmp_path)
    assert s.release_kind("b") == "album"
    s.set_apart("1", TIES, ["b"], "sig")
    assert s.release_kind("b") == "" and s.release_kind("a") == "album"
