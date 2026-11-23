"""The Releases bridge's library anchor: a follow on the wrong one of several
same-name TIDAL profiles is found from the library and moved (or the move
suggested when the outcome is less sure), all through the store, never through a TIDAL favorites write.

The bound bridge methods run over the test_releases_backend stub, with a fake
library cache and a fake TIDAL search and catalog.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from conftest import _Signal
from test_releases_backend import _bind, _stub

from waves import follows as f
from waves.waves_ui import bridge_releases as rel
from waves.waves_ui.backend import WavesBridge

OLD, NEW = "3581093", "34868882"
ALBUMS = ["Sleepless", "Death Dealers", "Silence the World", "Another Year"]


class _Cache:
    """A library scan cache: what the anchor reads of it."""

    def __init__(self, titles=None, tags=None):
        self.titles = titles if titles is not None else {"adept": {"albums": ALBUMS, "tracks": []}}
        self.tags = tags or {}

    def distinct_artists(self):
        return [{"key": k, "name": k.title()} for k in self.titles]

    def artist_titles(self, key):
        return self.titles.get(key, {"albums": [], "tracks": []})

    def artist_tidal_ids(self):
        return dict(self.tags)


class TooManyRequests(Exception):  # the name tidalapi's 429 carries
    pass


def _artist(titles, *, fail=False):
    def page(limit=None, offset=0):
        if fail:
            raise TooManyRequests()
        return [SimpleNamespace(name=t) for t in titles[offset : offset + limit]]

    return SimpleNamespace(get_albums=page, get_ep_singles=lambda limit=None, offset=0: [])


@pytest.fixture
def s(tmp_path, monkeypatch):
    monkeypatch.setattr(rel, "_REL_IMPORT_SEARCH_GAP_S", 0.0)
    st = _stub(tmp_path, prefs={"follow_mirror_tidal": True})
    st._library_index = SimpleNamespace(cache=_Cache())
    st._library_stamp = 1
    st._rel_anchor_map = {}
    st._rel_anchor_fold = {}
    st._rel_anchor_stamp = None
    st._rel_anchor_at = 0.0
    st._rel_anchor_cache = None
    st.artistMovesChanged = _Signal()
    st.artistAnchorsChanged = _Signal()
    st._fav_artist_dict = lambda a: {"id": a.id, "name": a.name, "art": "art-" + str(a.id)}
    st.searches = []
    st.on_search = None
    st.found = [SimpleNamespace(id=NEW, name="Adept"), SimpleNamespace(id=OLD, name="Adept")]

    def search(query, models=None, limit=None, offset=0):
        st.searches.append((query, offset))
        if st.on_search:
            st.on_search()
        return {"artists": st.found[offset : offset + limit]}

    st.tidal = SimpleNamespace(session=SimpleNamespace(search=search))
    st._objs["artist"][OLD] = _artist(["Techno Track", "Another Remix"])
    st._objs["artist"][NEW] = _artist(ALBUMS)
    # The followed profile, mirrored from the TIDAL favorites and checked once.
    st._releases.sync_tidal_favorites([{"id": OLD, "name": "Adept", "art": ""}])
    st._releases.record_check(OLD, [{"id": "t1", "title": "Techno Track", "date": "2026-01-01", "rtype": "album"}])
    return st


def _run(st):
    """One anchor turn, its outcome handed to the GUI thread's handler."""
    before = len(st._relStepDone.emits)
    assert _bind(st, "_releases_anchor_turn")()
    assert len(st._relStepDone.emits) == before + 1
    _bind(st, "_on_rel_step_done")(st._relStepDone.emits[-1])


def test_a_mirrored_favorite_on_the_wrong_profile_moves_without_a_tidal_write(s):
    _run(s)  # classify
    assert s._releases.anchor_of(OLD)[0] == f.ANCHOR_SUSPECT
    _run(s)  # the live check
    assert s._releases.followed_ids() == {NEW}
    assert OLD in s._releases.tidal_unfollowed_ids()
    assert (OLD, False) in s.followStateChanged.emits and (NEW, True) in s.followStateChanged.emits
    assert s.artistMovesChanged.emits
    # Mirroring follows to TIDAL is on, yet no favorites write was queued.
    assert s._rel_tidal_out == {} and not s._rel_tidal_inflight
    [mv] = _bind(s, "artistMoves")()
    assert mv["state"] == "moved" and mv["oldId"] == OLD and mv["newId"] == NEW and mv["albums"] == 4
    assert s._releases.follow_info(NEW)["art"] == "art-" + NEW


def test_a_follow_the_user_made_moves_too_and_undo_restores_it(s):
    s._releases.touch(OLD)
    _run(s)
    _run(s)
    assert s._releases.followed_ids() == {NEW}
    assert s._rel_tidal_out == {} and not s._rel_tidal_inflight
    [mv] = _bind(s, "artistMoves")()
    assert mv["state"] == "moved"
    assert _bind(s, "undoArtistMove")(OLD)
    assert s._releases.followed_ids() == {OLD}
    assert s._releases.follow_info(OLD)["touched"]


def _offer_only(s, monkeypatch):
    """Two same-name profiles past the candidate cap: a cut list is only
    ever suggested."""
    monkeypatch.setattr(rel, "_REL_ANCHOR_CANDIDATES", 1)
    s.found = [SimpleNamespace(id=NEW, name="Adept"), SimpleNamespace(id="5", name="Adept"), *s.found[1:]]
    s._objs["artist"]["5"] = _artist(["Polka"])


def test_a_suggestion_can_be_taken(s, monkeypatch):
    _offer_only(s, monkeypatch)
    _run(s)
    _run(s)
    assert s._releases.followed_ids() == {OLD}
    assert _bind(s, "acceptArtistMove")(OLD)
    assert s._releases.followed_ids() == {NEW}
    assert _bind(s, "artistMoves")() == []


def test_keep_on_a_suggestion_is_never_asked_again(s, monkeypatch):
    _offer_only(s, monkeypatch)
    _run(s)
    _run(s)
    assert _bind(s, "dismissArtistMove")(OLD)
    assert s._releases.followed_ids() == {OLD} and _bind(s, "artistMoves")() == []
    s._rel_anchor_stamp = None
    _run(s)  # judged again: the follow is never checked again
    assert _bind(s, "_releases_anchor_due")() is None


def test_undo_puts_the_follow_back(s):
    _run(s)
    _run(s)
    assert _bind(s, "undoArtistMove")(OLD)
    assert s._releases.followed_ids() == {OLD}
    assert (OLD, True) in s.followStateChanged.emits and (NEW, False) in s.followStateChanged.emits
    assert not _bind(s, "undoArtistMove")(OLD)


def test_undo_tells_the_ui_the_profile_stays_followed_while_another_move_stands_on_it(s):
    s._releases.sync_tidal_favorites(
        [{"id": OLD, "name": "Adept", "art": ""}, {"id": "777", "name": "Adept", "art": ""}]
    )
    new = {"id": NEW, "name": "Adept", "art": ""}
    assert s._releases.move_follow(OLD, new) and s._releases.move_follow("777", new)
    assert _bind(s, "undoArtistMove")(OLD)
    assert NEW in s._releases.followed_ids()
    assert (NEW, True) in s.followStateChanged.emits and (NEW, False) not in s.followStateChanged.emits


def test_an_empty_answer_for_a_followed_profile_with_rows_on_record_decides_nothing(s):
    s._objs["artist"][OLD] = _artist([])
    _run(s)
    _run(s)
    assert s._releases.followed_ids() == {OLD} and _bind(s, "artistMoves")() == []
    state, _sig = s._releases.anchor_of(OLD)
    assert state == f.ANCHOR_SUSPECT
    # Tried again a day later, not on the next beat.
    assert s._releases.anchor_next(10**12) is not None
    assert _bind(s, "_releases_anchor_due")() is None


def test_a_rate_limit_backs_off_and_leaves_the_follow_suspect(s):
    s._objs["artist"][NEW] = _artist(ALBUMS, fail=True)
    _run(s)
    _run(s)
    assert s._releases.followed_ids() == {OLD}
    assert s._rel_backoff_level == 1
    assert _bind(s, "_releases_anchor_due")() == "check"


def test_a_follow_judged_again_during_its_check_is_left_alone(s):
    _run(s)
    # A step lands a new release while the check runs: judged again.
    s.on_search = lambda: s._releases.anchor_note(OLD, f.ANCHOR_SUSPECT, "other evidence")
    _run(s)
    assert s._releases.followed_ids() == {OLD} and _bind(s, "artistMoves")() == []


def test_too_many_namesakes_make_the_move_only_a_suggestion(s, monkeypatch):
    monkeypatch.setattr(rel, "_REL_ANCHOR_CANDIDATES", 1)
    s.found = [SimpleNamespace(id=NEW, name="Adept"), SimpleNamespace(id="5", name="Adept"), *s.found[1:]]
    s._objs["artist"]["5"] = _artist(["Polka"])
    _run(s)
    _run(s)
    assert s._releases.followed_ids() == {OLD}
    assert [m["state"] for m in _bind(s, "artistMoves")()] == ["suggested"]


def test_the_search_reads_on_while_a_full_page_still_holds_the_name(s, monkeypatch):
    monkeypatch.setattr(rel, "_REL_ANCHOR_PAGE", 2)
    s.found = [
        SimpleNamespace(id="8", name="Adepts"),
        SimpleNamespace(id=OLD, name="Adept"),
        SimpleNamespace(id="9", name="Other"),
        SimpleNamespace(id=NEW, name="Adept"),
        SimpleNamespace(id="10", name="Else"),
        SimpleNamespace(id="11", name="Else"),
    ]
    _run(s)
    _run(s)
    assert [o for _q, o in s.searches] == [0, 2, 4]
    # TIDAL ranks the followed profile above the library's: only offered.
    assert [m["state"] for m in _bind(s, "artistMoves")()] == ["suggested"]


def test_nothing_runs_before_every_follow_is_checked_or_while_a_scan_runs(s):
    s._releases.follow("77", "Air", "")
    assert _bind(s, "_releases_anchor_due")() is None
    s._releases.record_check("77", [])
    s._library_scanning = object()
    assert _bind(s, "_releases_anchor_due")() is None
    s._library_scanning = None
    assert _bind(s, "_releases_anchor_due")() == "classify"
    s._library_index = None
    assert _bind(s, "_releases_anchor_due")() is None


def test_the_anchor_map_pins_one_followed_profile_per_name(s):
    s._library_index = SimpleNamespace(
        cache=_Cache(
            titles={
                "air": {"albums": ["Moon Safari", "Talkie Walkie"], "tracks": []},
                "static-x": {"albums": ["Wisconsin Death Trip", "Machine"], "tracks": []},
            },
            tags={"lowkey": "31803754"},
        )
    )
    s._releases.follow("77", "Air", "")
    s._releases.record_check("77", [{"id": "m1", "title": "Moon Safari", "date": "2026-01-01", "rtype": "album"}])
    s._releases.follow("14059", "Static X", "")
    s._releases.record_check("14059", [{"id": "w1", "title": "Machine", "date": "2026-01-01", "rtype": "album"}])
    _run(s)
    assert _bind(s, "_releases_anchor_for")("Air") == "77"
    # The follow's spelling and the library's meet once punctuation is folded.
    assert _bind(s, "_releases_anchor_for")("Static-X") == "14059"
    assert _bind(s, "_releases_anchor_for")("Static X") == "14059"
    # Nobody here follows the name: the id in the downloaded files stands.
    assert _bind(s, "_releases_anchor_for")("Lowkey") == "31803754"
    assert _bind(s, "_releases_anchor_for")("Adept") == ""
    assert _bind(s, "artistNamesake")("999", "Air") == {"other": "77"}
    assert _bind(s, "artistNamesake")("77", "Air") == {}
    assert _bind(s, "artistNamesake")("1", "Adept") == {}
    # The library off: the saved map no longer speaks for it.
    s._library_index = None
    assert _bind(s, "artistNamesake")("999", "Air") == {}


def test_two_followed_profiles_the_library_vouches_for_pin_nothing(s):
    s._library_index = SimpleNamespace(
        cache=_Cache(titles={"air": {"albums": ["Moon Safari", "Talkie"], "tracks": []}})
    )
    for aid, title in (("77", "Moon Safari"), ("78", "Talkie")):
        s._releases.follow(aid, "Air", "")
        s._releases.record_check(aid, [{"id": aid + "r", "title": title, "date": "2026-01-01", "rtype": "album"}])
    _run(s)
    assert _bind(s, "_releases_anchor_for")("Air") == ""


def test_library_presence_carries_the_anchor_without_touching_the_shared_rollup():
    hit = {"present": True, "albums": 3, "tracks": 30}
    idx = object()
    st = SimpleNamespace(
        _library_index=idx,
        _library_artist_index_src=idx,
        _library_artist_index={"adept": hit},
        _releases_anchor_for=lambda name: NEW if name == "Adept" else "",
        _library_probe_miss=lambda name: None,
    )
    out = WavesBridge.artistLibraryPresence(st, "Adept")
    assert out["anchor"] == NEW and out["albums"] == 3
    assert "anchor" not in hit
    st._releases_anchor_for = lambda name: ""
    assert WavesBridge.artistLibraryPresence(st, "Adept") is hit


def test_a_library_change_during_the_judging_still_lands_and_asks_again(s):
    calls = {"n": 0}
    real = s._releases_library_still if hasattr(s, "_releases_library_still") else None
    assert real is not None

    def still(cache):
        calls["n"] += 1
        return calls["n"] < 2

    s._releases_library_still = still
    s._releases.follow("77", "Air", "")
    s._releases.record_check("77", [])
    _run(s)
    # The step flag cleared (the outcome landed), nothing was published and
    # the judging is due again over the new library.
    assert not s._rel_step_running
    assert s.artistAnchorsChanged.emits == []
    assert s._rel_anchor_stamp is None


def test_an_unread_namesake_means_try_again_not_nothing_to_do(s):
    s.found = [*s.found, SimpleNamespace(id="5", name="Adept")]

    def broken(limit=None, offset=0):
        raise RuntimeError("a page failed")

    s._objs["artist"]["5"] = SimpleNamespace(get_albums=broken, get_ep_singles=broken)
    _run(s)
    _run(s)
    assert s._releases.followed_ids() == {OLD} and _bind(s, "artistMoves")() == []
    assert s._releases.anchor_of(OLD)[0] == f.ANCHOR_SUSPECT
    assert _bind(s, "_releases_anchor_due")() is None  # a day later


def test_a_namesake_too_long_to_read_whole_settles_the_check_until_the_evidence_changes(s):
    s.found = [*s.found, SimpleNamespace(id="5", name="Adept")]
    reads = []

    def endless(limit=None, offset=0):
        reads.append(offset)
        return [SimpleNamespace(name=f"Track {offset + i}") for i in range(limit)]

    s._objs["artist"]["5"] = SimpleNamespace(get_albums=endless, get_ep_singles=endless)
    _run(s)
    _run(s)
    assert s._releases.followed_ids() == {OLD} and _bind(s, "artistMoves")() == []
    assert s._releases.anchor_of(OLD)[0] == f.ANCHOR_CHECKED
    assert len(reads) == rel._REL_ANCHOR_CATALOG_PAGES
    # Judged again on the same evidence: still checked, no second live check.
    s._rel_anchor_stamp = None
    _run(s)
    assert s._releases.anchor_of(OLD)[0] == f.ANCHOR_CHECKED
    assert _bind(s, "_releases_anchor_due")() is None


def test_the_badges_hear_about_the_map_only_when_it_changed(s):
    _run(s)
    n = len(s.artistAnchorsChanged.emits)
    s._rel_anchor_stamp = None
    _run(s)
    assert len(s.artistAnchorsChanged.emits) == n


def test_a_library_publish_asks_for_the_judging_again_only_after_the_gap(s, monkeypatch):
    _run(s)
    s._library_stamp = 2
    assert _bind(s, "_releases_anchor_due")() == "check"
    s._rel_anchor_at -= rel._REL_ANCHOR_CLASSIFY_GAP_S
    assert _bind(s, "_releases_anchor_due")() == "classify"


def _rel_row(rid, title, copy, date="2026-01-01"):
    return {
        "id": rid,
        "title": title,
        "date": date,
        "rtype": "album",
        "artist_id": "500",
        "artists": [{"id": "500", "name": "Band"}],
        "copyright": copy,
    }


def test_the_judging_pass_sets_apart_other_artists_on_a_shared_profile(s):
    s._library_index = SimpleNamespace(
        cache=_Cache(titles={"band": {"albums": ["First Record", "Second Record"], "tracks": []}})
    )
    s._releases.follow("500", "Band", "")
    strangers = [_rel_row(f"x{i}", f"Stranger {i}", f"2024 Far Label {i}") for i in range(5)]
    s._releases.record_check(
        "500",
        [
            _rel_row("p1", "First Record", "(C) 2002 Home Label"),
            _rel_row("p2", "Second Record", "(C) 2005 Home Label"),
            _rel_row("t1", "A Single", "(P) 2019 Home Label"),
            *strangers,
        ],
    )
    n = len(s.releasesChanged.emits)
    _run(s)
    assert s._releases.is_shared("500")
    assert s._releases.apart_ids("500") == {r["id"] for r in strangers}
    assert len(s.releasesChanged.emits) == n + 1
    assert {r["id"] for r in s._releases.feed()} >= {"p1", "p2", "t1"}
    assert not {r["id"] for r in s._releases.feed()} & s._releases.apart_ids("500")
    # The same evidence again changes nothing and says nothing.
    s._rel_anchor_stamp = None
    _run(s)
    assert len(s.releasesChanged.emits) == n + 1
    # A new release: tied by its label it is news, an unrelated one is not.
    fresh = s._releases.record_check(
        "500",
        [
            _rel_row("p1", "First Record", "(C) 2002 Home Label"),
            _rel_row("p2", "Second Record", "(C) 2005 Home Label"),
            _rel_row("t1", "A Single", "(P) 2019 Home Label"),
            *strangers,
            _rel_row("n1", "New Record", "2026 Home Label", date="2026-11-01"),
            _rel_row("n2", "Someone Else", "2026 Elsewhere", date="2026-11-01"),
        ],
    )
    assert [r["id"] for r in fresh] == ["n1"]
    assert "n2" in s._releases.apart_ids("500")


def test_a_clean_follow_has_nothing_set_apart(s):
    s._library_index = SimpleNamespace(
        cache=_Cache(titles={"band": {"albums": ["First Record", "Second Record"], "tracks": []}})
    )
    s._releases.follow("500", "Band", "")
    s._releases.record_check(
        "500",
        [
            _rel_row("p1", "First Record", "2002 Home Label"),
            _rel_row("p2", "Second Record", "2005 Home Label"),
            _rel_row("o1", "Odd One", "2024 Elsewhere"),
        ],
    )
    _run(s)
    assert not s._releases.is_shared("500") and s._releases.apart_ids("500") == set()


def test_the_anchor_map_is_kept_for_the_next_launch(s):
    s._library_index = SimpleNamespace(cache=_Cache(titles={"air": {"albums": ["Moon Safari", "Talkie Walkie"]}}))
    s._releases.follow("77", "Air", "")
    s._releases.record_check("77", [{"id": "m1", "title": "Moon Safari", "date": "2026-01-01", "rtype": "album"}])
    _run(s)
    assert s._rel_anchor_map.get("air") == "77"
    # The next launch starts from it, before its own first judging lands.
    assert _bind(s, "_releases_anchor_saved")() == (s._rel_anchor_map, s._rel_anchor_fold)
    s._releases.meta_set(rel._REL_ANCHOR_SAVED_KEY, "not json")
    assert _bind(s, "_releases_anchor_saved")() == ({}, {})
