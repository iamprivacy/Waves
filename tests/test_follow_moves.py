"""The follow store's same-name moves: a follow on the wrong profile moved to
the one the library vouches for, undone, accepted or only suggested, and the
anchor bookkeeping the Releases bridge reads.

The promises: a move never writes TIDAL (it only remembers the old id as
unfollowed here, so the mirror cannot bring it back); the moved-to follow's
first check announces nothing; a move undone or a suggestion dismissed is
never made again.
"""

from __future__ import annotations

import json

from waves import follows as f
from waves.follows import FollowStore


def _store(tmp_path):
    return FollowStore(str(tmp_path / "releases.sqlite3"))


def _rel(rid, title="Album", date="2026-10-01"):
    return {"id": rid, "title": title, "artist_id": "1", "art": "", "year": date[:4], "date": date, "rtype": "album"}


NEW = {"id": "34868882", "name": "Adept", "art": "art"}


def _mirrored(s, aid="3581093"):
    assert s.sync_tidal_favorites([{"id": aid, "name": "Adept", "art": ""}]) == (1, 0)
    s.record_check(aid, [_rel("t1", "Techno Track")])


def test_a_move_swaps_the_follow_and_the_mirror_cannot_bring_the_old_one_back(tmp_path):
    s = _store(tmp_path)
    _mirrored(s)
    assert s.move_follow("3581093", NEW, name_key="adept", evidence={"albums_n": 14})
    assert s.followed_ids() == {"34868882"}
    assert "3581093" in s.tidal_unfollowed_ids()
    # The old profile is still a TIDAL favorite: the next full walk adds nothing.
    assert s.sync_tidal_favorites([{"id": "3581093", "name": "Adept", "art": ""}]) == (0, 0)
    assert s.followed_ids() == {"34868882"}
    info = s.follow_info("34868882")
    assert info["origin"] == f.ORIGIN_LIBRARY and not info["touched"] and not info["tidal_fav"]
    # The old profile's releases are gone with it.
    assert s.anchor_inputs()[0]["titles"] == []


def test_the_moved_to_follows_first_check_announces_nothing(tmp_path):
    s = _store(tmp_path)
    _mirrored(s)
    s.move_follow("3581093", NEW)
    assert s.record_check("34868882", [_rel("s1", "Sleepless"), _rel("s2", "Death Dealers")]) == []
    fresh = s.record_check("34868882", [_rel("s1", "Sleepless"), _rel("s2", "Death Dealers"), _rel("s3", "New")])
    assert [r["id"] for r in fresh] == ["s3"]


def test_a_move_drops_the_old_profiles_waiting_downloads(tmp_path):
    s = _store(tmp_path)
    _mirrored(s)
    s.guest_wait("g1", "2099-01-01", "3581093")
    s.guest_wait("g2", "2099-01-01", "999")
    s.atmos_wait({"a1": "2099-01-01"}, "album", "3581093")
    s.atmos_wait({"a2": "2099-01-01"}, "album", "999")
    s.move_follow("3581093", NEW)
    assert set(json.loads(s.meta_get("guest_waiting"))) == {"g2"}
    assert set(json.loads(s.meta_get("atmos_waiting"))) == {"a2"}


def test_undo_restores_the_old_follow_and_the_move_is_never_repeated(tmp_path):
    s = _store(tmp_path)
    _mirrored(s)
    s.move_follow("3581093", NEW)
    assert s.undo_move("3581093")
    assert s.followed_ids() == {"3581093"}
    info = s.follow_info("3581093")
    assert info["origin"] == f.ORIGIN_TIDAL and info["tidal_fav"] and not info["seeded"]
    assert "3581093" not in s.tidal_unfollowed_ids()
    assert not s.move_follow("3581093", NEW)
    assert not s.suggest_move("3581093", NEW)
    assert s.moves() == [] and s.moves(settled=True)[0]["state"] == f.MOVE_UNDONE
    # The restored follow is never offered for another live check.
    s.anchor_note("3581093", f.ANCHOR_SUSPECT, "sig")
    assert s.anchor_next(2**40) is None


def test_undo_keeps_a_new_profile_that_was_followed_before(tmp_path):
    s = _store(tmp_path)
    _mirrored(s)
    s.follow("34868882", "Adept", "")
    s.move_follow("3581093", NEW)
    assert s.followed_ids() == {"34868882"}
    s.undo_move("3581093")
    assert s.followed_ids() == {"3581093", "34868882"}


def test_a_suggestion_waits_for_the_user(tmp_path):
    s = _store(tmp_path)
    _mirrored(s)
    assert s.suggest_move("3581093", NEW, name_key="adept", evidence={"albums_n": 2})
    assert not s.suggest_move("3581093", {"id": "5"})
    assert s.followed_ids() == {"3581093"}
    [mv] = s.moves()
    assert mv["state"] == f.MOVE_SUGGESTED and mv["new_id"] == "34868882" and mv["evidence"] == {"albums_n": 2}
    assert s.accept_move("3581093")
    assert s.followed_ids() == {"34868882"}
    assert s.moves() == [] and s.moves(settled=True)[0]["state"] == f.MOVE_MOVED


def test_a_dismissed_suggestion_stays_dismissed(tmp_path):
    s = _store(tmp_path)
    _mirrored(s)
    s.suggest_move("3581093", NEW)
    assert s.dismiss_move("3581093")
    assert s.followed_ids() == {"3581093"}
    assert not s.accept_move("3581093")
    assert not s.move_follow("3581093", NEW)
    assert s.moves() == []


def test_ok_on_a_made_move_clears_the_notice(tmp_path):
    s = _store(tmp_path)
    _mirrored(s)
    s.move_follow("3581093", NEW)
    assert [m["state"] for m in s.moves()] == [f.MOVE_MOVED]
    assert s.accept_move("3581093")
    assert s.moves() == []
    assert not s.dismiss_move("3581093")


def test_a_move_for_a_follow_that_is_gone_is_refused(tmp_path):
    s = _store(tmp_path)
    assert not s.move_follow("1", NEW)
    assert not s.suggest_move("1", NEW)
    assert not s.move_follow("1", {"id": "1"})


def test_anchor_inputs_and_the_next_check(tmp_path):
    s = _store(tmp_path)
    _mirrored(s)
    s.follow("7", "Air", "")
    [adept, air] = s.anchor_inputs()
    assert adept["titles"] == ["Techno Track"] and adept["origin"] == f.ORIGIN_TIDAL and not adept["touched"]
    assert air["titles"] == [] and air["state"] == ""
    assert s.anchor_next(100) is None
    s.anchor_note("3581093", f.ANCHOR_SUSPECT, "abc", at=50)
    assert s.anchor_next(10) is None
    assert s.anchor_next(100)["id"] == "3581093"
    s.anchor_note("3581093", f.ANCHOR_CHECKED, "abc")
    assert s.anchor_next(100) is None
    assert s.anchor_inputs()[0]["sig"] == "abc"


def test_unfollow_still_forgets_everything(tmp_path):
    s = _store(tmp_path)
    _mirrored(s)
    s.unfollow("3581093", remember_tidal=True)
    assert s.followed_ids() == set() and s.anchor_inputs() == []
    assert s.career_rows("3581093") == []


def test_undo_drops_the_new_profiles_waiting_downloads(tmp_path):
    s = _store(tmp_path)
    _mirrored(s)
    s.move_follow("3581093", NEW)
    s.guest_wait("g1", "2099-01-01", "34868882")
    s.undo_move("3581093")
    assert json.loads(s.meta_get("guest_waiting")) == {}


def test_anchor_of_reads_the_state_and_sig(tmp_path):
    s = _store(tmp_path)
    _mirrored(s)
    assert s.anchor_of("3581093") == ("", "")
    s.anchor_note("3581093", f.ANCHOR_SUSPECT, "abc")
    assert s.anchor_of("3581093") == (f.ANCHOR_SUSPECT, "abc")
    assert s.anchor_of("nope") is None


def test_undoing_one_of_two_moves_onto_one_profile_keeps_it_for_the_other(tmp_path):
    s = _store(tmp_path)
    assert s.sync_tidal_favorites(
        [{"id": "3581093", "name": "Adept", "art": ""}, {"id": "777", "name": "Adept", "art": ""}]
    ) == (2, 0)
    assert s.move_follow("3581093", NEW)
    assert s.move_follow("777", NEW)
    assert s.undo_move("3581093")
    # The other move still stands on the profile.
    assert s.followed_ids() == {"3581093", "34868882"}
    assert [m["old_id"] for m in s.moves()] == ["777"]
    # Undoing that one too takes the profile away: nothing followed it before.
    assert s.undo_move("777")
    assert s.followed_ids() == {"3581093", "777"}


def test_a_suggestion_for_a_follow_unfollowed_since_leaves_the_pane(tmp_path):
    s = _store(tmp_path)
    _mirrored(s)
    assert s.suggest_move("3581093", NEW, name_key="adept")
    assert [m["old_id"] for m in s.moves()] == ["3581093"]
    s.unfollow("3581093")
    assert s.moves() == []
    assert not s.accept_move("3581093")
    assert [m["old_id"] for m in s.moves(settled=True)] == ["3581093"]


def test_a_move_notice_leaves_once_its_target_is_unfollowed(tmp_path):
    s = _store(tmp_path)
    _mirrored(s)
    assert s.move_follow("3581093", NEW)
    assert [m["old_id"] for m in s.moves()] == ["3581093"]
    s.unfollow(NEW["id"])
    assert s.moves() == []
    assert [m["old_id"] for m in s.moves(settled=True)] == ["3581093"]
